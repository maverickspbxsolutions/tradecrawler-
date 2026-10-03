"""Paper-trading (forward simulation). Evaluates closed candles, maintains a
long-only position per strategy, logs simulated trades, feeds the signal feed,
and triggers alerts. Signals are idempotent (same signal never fires twice)."""
import uuid
import logging
from datetime import datetime, timezone

from db import db
import market_data as md
from strategy_engine import candles_to_df, evaluate, rule_label
from alerts import maybe_send_alert

logger = logging.getLogger(__name__)

PAPER_CAPITAL = 10000.0
TRADE_CAPITAL = 20.0  # assume the trader can act on ~$20 the moment the email lands


def _humanize_duration(ms) -> str | None:
    if not ms or ms <= 0:
        return None
    mins = ms / 60000.0
    if mins < 90:
        return f"about {round(mins)} minutes"
    hours = mins / 60.0
    if hours < 48:
        return f"about {round(hours)} hours"
    return f"about {round(hours / 24.0)} days"


async def _trade_plan(strategy: dict, side: str, price: float) -> dict:
    """Plain-English, dollar-concrete plan assuming ~$20 ready to trade."""
    cap = TRADE_CAPITAL
    if side != "BUY":
        return {"action": "SELL", "capital": cap, "entry": round(price, 6)}
    bt = await db.backtests.find_one(
        {"strategy_id": strategy["id"]},
        {"_id": 0, "avg_win_pct": 1, "avg_loss_pct": 1, "avg_hold_ms": 1})
    aw = (bt or {}).get("avg_win_pct") or 2.5
    al = (bt or {}).get("avg_loss_pct") or -1.5
    if aw <= 0:
        aw = 2.5
    if al >= 0:
        al = -1.5
    units = cap / price if price else 0
    return {
        "action": "BUY",
        "capital": cap,
        "units": round(units, 8),
        "entry": round(price, 6),
        "target_price": round(price * (1 + aw / 100), 6),
        "stop_price": round(price * (1 + al / 100), 6),
        "target_pct": round(aw, 2),
        "stop_pct": round(al, 2),
        "est_profit": round(cap * aw / 100, 2),
        "est_loss": round(cap * al / 100, 2),
        "hold_label": _humanize_duration((bt or {}).get("avg_hold_ms")),
    }


async def _get_state(strategy_id: str) -> dict:
    state = await db.paper_states.find_one({"strategy_id": strategy_id}, {"_id": 0})
    if not state:
        state = {
            "strategy_id": strategy_id,
            "position": "flat",
            "entry_price": 0.0,
            "entry_time": None,
            "units": 0.0,
            "cash": PAPER_CAPITAL,
            "realized_pnl": 0.0,
            "num_trades": 0,
            "wins": 0,
        }
        await db.paper_states.insert_one(dict(state))
    return state


def _reason(strategy: dict, side: str) -> str:
    rules = strategy.get("entry_rules" if side == "BUY" else "exit_rules", [])
    labels = [rule_label(r) for r in rules]
    joiner = f" {strategy.get('logic', 'AND')} "
    return joiner.join(labels) if labels else "manual"


async def run_strategy_step(strategy: dict, source: str = "poll") -> dict | None:
    symbol = strategy["symbol"]
    timeframe = strategy["timeframe"]
    try:
        candles = await md.get_candles(symbol, timeframe, limit=400)
    except Exception as e:  # noqa
        logger.warning(f"Step skipped (data): {strategy['name']} {e}")
        return None
    if len(candles) < 50:
        return None

    df = candles_to_df(candles)
    entry, exit_ = evaluate(df, strategy)
    last = len(df) - 1
    candle_t = int(df["t"].iloc[last])
    price = float(df["c"].iloc[last])
    fees = float(strategy.get("fees_pct", 0.1)) / 100.0
    slip = float(strategy.get("slippage_pct", 0.05)) / 100.0

    state = await _get_state(strategy["id"])

    side = None
    if state["position"] == "flat" and entry[last]:
        side = "BUY"
    elif state["position"] == "long" and exit_[last]:
        side = "SELL"

    # always refresh mark-to-market equity + staleness
    equity = state["cash"] + state["units"] * price
    await db.paper_states.update_one(
        {"strategy_id": strategy["id"]},
        {"$set": {"last_price": price, "equity": round(equity, 2),
                  "last_checked": datetime.now(timezone.utc).isoformat(),
                  "last_candle": candle_t}})

    if side is None:
        return None

    sig_key = f"{strategy['id']}:{candle_t}:{side}"
    if await db.signals.find_one({"sig_key": sig_key}):
        return None  # idempotent

    candle_iso = datetime.fromtimestamp(candle_t / 1000, tz=timezone.utc).isoformat()
    signal = {
        "id": str(uuid.uuid4()),
        "sig_key": sig_key,
        "strategy_id": strategy["id"],
        "strategy_name": strategy["name"],
        "symbol": symbol,
        "timeframe": timeframe,
        "side": side,
        "price": round(price, 6),
        "candle_time": candle_iso,
        "reason": _reason(strategy, side),
        "source": source,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    signal["plan"] = await _trade_plan(strategy, side, price)

    # update paper position
    if side == "BUY":
        buy_price = price * (1 + slip)
        units = (state["cash"] * (1 - fees)) / buy_price
        await db.paper_states.update_one(
            {"strategy_id": strategy["id"]},
            {"$set": {"position": "long", "entry_price": round(buy_price, 6),
                      "entry_time": candle_iso, "units": units, "cash": 0.0}})
    else:  # SELL
        sell_price = price * (1 - slip)
        entry_value = state["units"] * state["entry_price"]
        proceeds = state["units"] * sell_price * (1 - fees)
        pnl = proceeds - entry_value
        ret_pct = (proceeds / entry_value - 1) * 100 if entry_value else 0.0
        await db.paper_trades.insert_one({
            "id": str(uuid.uuid4()),
            "strategy_id": strategy["id"],
            "strategy_name": strategy["name"],
            "symbol": symbol,
            "entry_price": round(state["entry_price"], 6),
            "exit_price": round(sell_price, 6),
            "entry_time": state["entry_time"],
            "exit_time": candle_iso,
            "return_pct": round(ret_pct, 4),
            "pnl": round(pnl, 2),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        await db.paper_states.update_one(
            {"strategy_id": strategy["id"]},
            {"$set": {"position": "flat", "entry_price": 0.0, "entry_time": None,
                      "units": 0.0, "cash": round(proceeds, 2)},
             "$inc": {"realized_pnl": round(pnl, 2), "num_trades": 1,
                      "wins": 1 if ret_pct > 0 else 0}})
        signal["pnl"] = round(pnl, 2)
        signal["return_pct"] = round(ret_pct, 4)

    await db.signals.insert_one(dict(signal))

    alert_res = await maybe_send_alert(signal, strategy)
    await db.signals.update_one({"id": signal["id"]},
                                {"$set": {"alert": alert_res}})
    signal["alert"] = alert_res
    signal.pop("_id", None)
    return signal


async def backfill_signals(strategy: dict, lookback: int = 40) -> int:
    """Populate the feed with recent historical entry/exit events (no alerts)."""
    try:
        candles = await md.get_candles(strategy["symbol"], strategy["timeframe"], limit=400)
    except Exception:
        return 0
    if len(candles) < 60:
        return 0
    df = candles_to_df(candles)
    entry, exit_ = evaluate(df, strategy)
    n = len(df)
    start = max(0, n - lookback)
    position = "flat"
    count = 0
    for i in range(1, n):
        side = None
        if position == "flat" and entry[i]:
            side = "BUY"
            position = "long"
        elif position == "long" and exit_[i]:
            side = "SELL"
            position = "flat"
        if side is None or i < start:
            continue
        candle_t = int(df["t"].iloc[i])
        sig_key = f"{strategy['id']}:{candle_t}:{side}"
        if await db.signals.find_one({"sig_key": sig_key}):
            continue
        candle_iso = datetime.fromtimestamp(candle_t / 1000, tz=timezone.utc).isoformat()
        await db.signals.insert_one({
            "id": str(uuid.uuid4()),
            "sig_key": sig_key,
            "strategy_id": strategy["id"],
            "strategy_name": strategy["name"],
            "symbol": strategy["symbol"],
            "timeframe": strategy["timeframe"],
            "side": side,
            "price": round(float(df["c"].iloc[i]), 6),
            "candle_time": candle_iso,
            "reason": _reason(strategy, side),
            "source": "historical",
            "created_at": candle_iso,
            "alert": {"emailed": False, "reason": "historical backfill"},
        })
        count += 1
    return count
