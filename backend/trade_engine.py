"""Demo-only trade execution engine for Deriv MT5 via MetaApi.

Flow: a signal becomes a PROPOSAL -> the user reviews it in the Approval Queue ->
on explicit approval the engine runs server-side risk checks and places the order
on the connected Deriv **demo** account. Nothing is ever placed automatically.

Hard safety rails (see risk_config.py):
  - demo environment lock (refuses to trade a non-demo account)
  - symbol allowlist (Gold / BTC only for now)
  - max lot ceiling (10) with dynamic, market-aware sizing
  - $50 daily realized-loss kill switch
  - idempotent placement (one order per approved proposal)
"""
import uuid
import logging
from datetime import datetime, timezone, timedelta

import pandas as pd
import numpy as np

from db import db
import mt5_trader
import risk_config as rc
from outlook import compute_outlook
import market_data as md
from alerts import get_settings

logger = logging.getLogger("trade_engine")

PROPOSAL_TTL_MIN = 15  # a proposal's reference price goes stale after this


# ----------------------------- helpers -----------------------------
def _now():
    return datetime.now(timezone.utc)


def _day_start():
    n = _now()
    return n.replace(hour=0, minute=0, second=0, microsecond=0)


def _digits(price: float) -> int:
    return 4 if price < 10 else 2


def _atr(candles: list, period: int = 14) -> float:
    if not candles or len(candles) < 2:
        return 0.0
    df = pd.DataFrame(candles)
    prev_c = df["c"].shift(1)
    tr = pd.concat([
        df["h"] - df["l"],
        (df["h"] - prev_c).abs(),
        (df["l"] - prev_c).abs(),
    ], axis=1).max(axis=1)
    return float(tr.tail(period).mean())


_TF_MIN = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}


def _tf_minutes(tf: str) -> int:
    return _TF_MIN.get(tf, 60)


def _humanize_mins(m: float) -> str:
    m = int(round(m))
    if m < 60:
        return f"~{max(5, m)} min"
    h, mm = m // 60, m % 60
    return f"~{h}h" + (f" {mm}m" if mm else "")


def _is_demo(info: dict) -> bool:
    t = str(info.get("type", "")).upper()
    # MetaApi reports demo accounts as ACCOUNT_TRADE_MODE_DEMO
    return "DEMO" in t


# ----------------------------- daily P&L / kill switch -----------------------------
async def daily_pnl() -> dict:
    """Realized P&L for the current UTC day + floating P&L on open positions."""
    start = _day_start()
    realized = 0.0
    floating = 0.0
    try:
        deals = await mt5_trader.deals_range(start, _now()) or []
        if isinstance(deals, dict):
            deals = deals.get("deals", []) or []
        closing = {"DEAL_ENTRY_OUT", "DEAL_ENTRY_OUT_BY", "DEAL_ENTRY_INOUT"}
        for d in deals:
            if str(d.get("entryType", "")) in closing:
                realized += float(d.get("profit", 0) or 0) + float(d.get("commission", 0) or 0) + float(d.get("swap", 0) or 0)
        for p in (await mt5_trader.positions() or []):
            floating += float(p.get("unrealizedProfit", p.get("profit", 0)) or 0)
    except Exception as e:  # noqa
        logger.warning(f"daily_pnl failed: {e}")
    limit = rc.DAILY_LOSS_LIMIT_USD
    over_limit = realized <= -limit
    kill = over_limit and rc.ENFORCE_KILL_SWITCH
    return {
        "realized": round(realized, 2),
        "floating": round(floating, 2),
        "limit": limit,
        "loss_used": round(max(0.0, -realized), 2),
        "remaining": round(max(0.0, limit + realized), 2),
        "over_limit": over_limit,
        "enforced": rc.ENFORCE_KILL_SWITCH,
        "kill_switch_active": kill,
        "day_start": start.isoformat(),
    }


# ----------------------------- risk gate -----------------------------
async def _active_bot_positions() -> int:
    """How many of the bot's own trades are currently open on the account."""
    try:
        live_ids = {str(p.get("id")) for p in (await mt5_trader.positions() or [])}
    except Exception:  # noqa
        live_ids = set()
    open_trades = await db.trades.find({"status": "open"}, {"_id": 0, "position_id": 1}).to_list(100)
    # reconcile: trades no longer live are closed
    active = 0
    for t in open_trades:
        if t.get("position_id") in live_ids:
            active += 1
        else:
            await db.trades.update_one({"position_id": t.get("position_id")},
                                       {"$set": {"status": "closed"}})
    return active


async def _risk_gate(symbol: str, lot: float) -> str | None:
    """Returns an error string if the trade must be blocked, else None."""
    if rc.TRADE_ENV != "demo":
        return "Trading is locked to demo mode."
    if symbol not in await allowed_symbols():
        return f"{symbol} isn't one of your active instruments. Turn on a strategy for it first."
    if lot > rc.MAX_LOT:
        return f"Lot {lot} exceeds the max lot ceiling of {rc.MAX_LOT}."
    if lot < rc.MIN_LOT:
        return f"Lot {lot} is below the minimum of {rc.MIN_LOT}."
    active = await _active_bot_positions()
    if active >= rc.MAX_OPEN_POSITIONS:
        return f"Already running {active} trades — the max is {rc.MAX_OPEN_POSITIONS} at once. Close one first."
    try:
        info = await mt5_trader.account_info()
    except Exception as e:  # noqa
        return f"Could not reach the trading account: {e}"
    if not _is_demo(info):
        return "Connected account is NOT a demo account — placement blocked for safety."
    if not info.get("tradeAllowed", True):
        return "Trading is not currently allowed on this account."
    pnl = await daily_pnl()
    if pnl["kill_switch_active"]:
        return f"Daily loss kill switch active (lost ${pnl['loss_used']} of ${pnl['limit']} limit). No new trades today."
    return None


# ----------------------------- active instrument universe -----------------------------
async def _scan_targets() -> list:
    """Every instrument the bot should hunt for setups on: the Gold/BTC defaults
    plus the symbol of every strategy the user has turned on (enabled). All scanned
    on the intraday day-trading timeframe (30min-5h holds)."""
    tf = rc.TRADE_TIMEFRAME
    targets = [{"signal": "XAUUSD", "timeframe": tf, "label": "Gold"},
               {"signal": "BTCUSD", "timeframe": tf, "label": "Bitcoin"}]
    seen = {mt5_trader.mt5_symbol(t["signal"]) for t in targets}
    strats = await db.strategies.find({"enabled": True}, {"_id": 0}).to_list(500)
    for s in strats:
        m = mt5_trader.mt5_symbol(s.get("symbol"))
        if not m or m in seen:
            continue
        seen.add(m)
        targets.append({"signal": s["symbol"], "timeframe": tf,
                        "label": s.get("name") or m})
    return targets


async def allowed_symbols() -> set:
    """MT5 symbols the engine is currently allowed to trade (demo phase)."""
    syms = {mt5_trader.mt5_symbol(s) or s for s in rc.ALLOWED_SYMBOLS}
    for t in await _scan_targets():
        m = mt5_trader.mt5_symbol(t["signal"])
        if m:
            syms.add(m)
    return syms


async def active_instruments() -> list:
    """For the UI: the scan buttons the user can trigger manually."""
    out = []
    for t in await _scan_targets():
        out.append({"value": t["signal"], "label": t["label"],
                    "mt5_symbol": mt5_trader.mt5_symbol(t["signal"])})
    return out


def _predict(candles: list, side: str, entry: float, sl: float, tp: float,
             score: int, horizon: int = None) -> dict:
    """Forward Monte-Carlo: simulate many price paths (drift tilted by the live
    outlook) and measure how often the trade reaches TP before SL. Returns win
    probability and expected value in R (risk) multiples."""
    c = np.array([x["c"] for x in candles], dtype=float)
    logret = np.diff(np.log(c[c > 0]))
    tail = logret[-120:] if len(logret) >= 120 else logret
    sigma = float(np.std(tail)) or 1e-4
    mu_hist = float(np.mean(tail))
    # directional lean from the outlook score (-100..100)
    tilt = sigma * (score / 100.0) * 0.6
    mu = mu_hist * 0.3 + tilt

    n = rc.SIM_PATHS
    horizon = horizon or rc.SIM_HORIZON_BARS
    sl_dist = abs(entry - sl) or entry * 0.002
    tp_dist = abs(tp - entry) or entry * 0.003

    prices = np.full(n, entry, dtype=float)
    active = np.ones(n, dtype=bool)
    outcome = np.zeros(n, dtype=float)  # in price terms, + = profit
    resolved_step = np.full(n, horizon, dtype=float)  # bars until TP/SL (horizon if never)
    rng = np.random.default_rng()
    for i in range(1, horizon + 1):
        shocks = rng.normal(mu, sigma, n)
        prices = np.where(active, prices * np.exp(shocks), prices)
        if side == "BUY":
            tp_hit = active & (prices >= tp)
            sl_hit = active & (prices <= sl)
        else:
            tp_hit = active & (prices <= tp)
            sl_hit = active & (prices >= sl)
        hit = tp_hit | sl_hit
        outcome[tp_hit] = tp_dist
        outcome[sl_hit] = -sl_dist
        resolved_step[hit] = i
        active &= ~hit
        if not active.any():
            break
    # settle anything still open at its mark-to-market
    if active.any():
        mtm = (prices[active] - entry) if side == "BUY" else (entry - prices[active])
        outcome[active] = mtm

    win_prob = float(np.mean(outcome > 0))
    exp_value_price = float(np.mean(outcome))
    expected_r = exp_value_price / sl_dist
    est_hold_bars = float(np.median(resolved_step))
    return {
        "win_prob": round(win_prob, 3),
        "expected_r": round(expected_r, 3),
        "horizon_bars": horizon,
        "est_hold_bars": est_hold_bars,
        "paths": n,
    }


def _value_per_unit(spec: dict) -> float:
    """$ gained/lost per 1.0 price move per 1 lot, for a USD-quoted symbol."""
    tick_size = spec.get("tickSize") or spec.get("tick_size")
    tick_value = spec.get("tickValue") or spec.get("tick_value")
    if tick_size and tick_value:
        return float(tick_value) / float(tick_size)
    # Deriv CFDs expose no tickValue; for a USD profit currency the value per
    # price unit per lot equals the contract size.
    return float(spec.get("contractSize") or 1)


def _risk_lot(spec: dict, sl_dist: float, risk_usd: float):
    """Size the lot so a stop-loss hit loses about `risk_usd`. Clamped to the
    symbol's volume rules and MAX_LOT."""
    vmin = float(spec.get("minVolume") or spec.get("volumeMin") or rc.MIN_LOT)
    vmax = float(spec.get("maxVolume") or spec.get("volumeMax") or rc.MAX_LOT)
    step = float(spec.get("volumeStep") or spec.get("lotStep") or 0.01)
    vpu = _value_per_unit(spec)

    raw = risk_usd / (sl_dist * vpu) if (vpu and sl_dist > 0) else vmin
    lot = np.floor(raw / step) * step if step else raw
    lot = max(max(vmin, rc.MIN_LOT), min(lot, vmax, rc.MAX_LOT))
    lot = round(lot, 2)
    est_risk = round(lot * sl_dist * vpu, 2) if vpu else None
    return lot, est_risk, vpu


def _min_stop_dist(spec: dict) -> float:
    pt = float(spec.get("point") or spec.get("tickSize") or 0)
    stops = float(spec.get("stopsLevel") or 0)
    return stops * pt


# ----------------------------- proposals -----------------------------
async def _trade_settings() -> dict:
    """Effective trade config: user Settings overrides over risk_config defaults."""
    s = await get_settings()
    tf = s.get("trade_timeframe") or rc.TRADE_TIMEFRAME
    if tf not in _TF_MIN:
        tf = rc.TRADE_TIMEFRAME
    max_hold_h = float(s.get("max_hold_hours") or (rc.SIM_HORIZON_BARS * _tf_minutes(rc.TRADE_TIMEFRAME) / 60))
    horizon = max(2, int(round(max_hold_h * 60 / _tf_minutes(tf))))
    return {
        "timeframe": tf,
        "max_hold_hours": max_hold_h,
        "horizon": horizon,
        "auto_approve": bool(s.get("auto_approve")),
        "auto_approve_min_winprob": float(s.get("auto_approve_min_winprob") or 0.85),
        "trailing_enabled": s.get("trailing_enabled", True) is not False,
    }


async def _analyze(signal_symbol: str, timeframe: str, horizon: int) -> dict:
    """Read-only market read + forward simulation for one instrument. Returns the
    full decision (tradeable or not, with reasons) WITHOUT placing or persisting."""
    mt5_sym = mt5_trader.mt5_symbol(signal_symbol) or signal_symbol
    candles = await md.get_candles(signal_symbol, timeframe, 300)
    if not candles or len(candles) < 60:
        return {"ok": False, "mt5_symbol": mt5_sym, "reason": "Not enough market data yet."}
    ol = compute_outlook(candles, signal_symbol, timeframe)
    direction = ol.get("direction")
    conf = float(ol.get("confidence", 50))
    score = int(ol.get("score", 0))
    if direction not in ("Up", "Down"):
        return {"ok": False, "mt5_symbol": mt5_sym, "direction": "Neutral", "confidence": int(conf),
                "reason": "Outlook is Neutral — no clear buy/sell edge."}
    if conf < rc.MIN_CONFIDENCE:
        return {"ok": False, "mt5_symbol": mt5_sym, "direction": direction, "confidence": int(conf),
                "reason": f"Confidence {int(conf)}% (need {rc.MIN_CONFIDENCE}%+). Holding off."}

    side = "BUY" if direction == "Up" else "SELL"
    live = await md.get_live(signal_symbol, timeframe)
    price = float(live.get("price") or candles[-1]["c"])
    atr = _atr(candles) or price * 0.004
    d = _digits(price)
    sl_dist = round(1.5 * atr, d)
    tp_dist = round(1.5 * sl_dist, d)
    if side == "BUY":
        sl, tp = round(price - sl_dist, d), round(price + tp_dist, d)
    else:
        sl, tp = round(price + sl_dist, d), round(price - tp_dist, d)

    pred = _predict(candles, side, price, sl, tp, score, horizon)
    tf_min = _tf_minutes(timeframe)
    hold_min = pred["est_hold_bars"] * tf_min
    hold_label = _humanize_mins(hold_min)
    window_label = _humanize_mins(pred["horizon_bars"] * tf_min)
    base = {"mt5_symbol": mt5_sym, "signal_symbol": signal_symbol, "timeframe": timeframe,
            "direction": direction, "side": side, "confidence": int(conf),
            "win_prob": pred["win_prob"], "expected_r": pred["expected_r"],
            "horizon_bars": pred["horizon_bars"], "est_hold_min": int(round(hold_min)),
            "hold_label": hold_label, "window_label": window_label,
            "price": round(price, d), "stop_loss": sl, "take_profit": tp, "sl_dist": sl_dist,
            "vol": float((ol.get("metrics") or {}).get("volatility_pct") or 0),
            "reasons": ol.get("reasons", [])[:3]}
    if pred["win_prob"] < rc.MIN_WIN_PROB or pred["expected_r"] < rc.MIN_EXPECTED_R:
        base["ok"] = False
        base["reason"] = (f"Sim {int(pred['win_prob']*100)}% win odds, {pred['expected_r']:+.2f}R "
                          f"within {window_label} — not worth it. Waiting.")
        return base
    base["ok"] = True
    base["reason"] = f"Tradeable: {int(pred['win_prob']*100)}% win odds, {pred['expected_r']:+.2f}R, hold {hold_label}."
    return base


async def scan_status() -> list:
    """Why-no-trade readout: current confidence + sim odds + decision per instrument."""
    cfg = await _trade_settings()
    out = []
    for t in (await _scan_targets())[:10]:
        try:
            a = await _analyze(t["signal"], cfg["timeframe"], cfg["horizon"])
        except Exception as e:  # noqa
            a = {"ok": False, "mt5_symbol": mt5_trader.mt5_symbol(t["signal"]), "reason": f"Unavailable: {e}"}
        out.append({
            "label": t["label"], "value": t["signal"], "mt5_symbol": a.get("mt5_symbol"),
            "tradeable": a.get("ok", False), "side": a.get("side"),
            "direction": a.get("direction"), "confidence": a.get("confidence"),
            "win_prob": a.get("win_prob"), "expected_r": a.get("expected_r"),
            "hold_label": a.get("hold_label"), "reason": a.get("reason"),
        })
    return out


async def propose(signal_symbol: str, timeframe: str = None) -> dict:
    """Build a pending trade proposal only when the outlook is confident AND a
    forward simulation shows a favourable, risk-sized setup."""
    cfg = await _trade_settings()
    timeframe = timeframe or cfg["timeframe"]
    mt5_sym = mt5_trader.mt5_symbol(signal_symbol) or signal_symbol
    if mt5_sym not in await allowed_symbols():
        return {"ok": False, "error": f"{mt5_sym} isn't one of your active instruments. Turn on a strategy for it first."}

    a = await _analyze(signal_symbol, timeframe, cfg["horizon"])
    if not a.get("ok"):
        return {"ok": False, "error": a.get("reason", "No tradeable setup right now.")}

    # symbol trading rules (volume steps, min stop distance, contract size)
    try:
        spec = await mt5_trader.symbol_spec(mt5_sym)
    except Exception as e:  # noqa
        return {"ok": False, "error": f"Could not read {mt5_sym} trading rules: {e}"}

    side, price, d = a["side"], a["price"], _digits(a["price"])
    sl_dist = max(a["sl_dist"], _min_stop_dist(spec) * 1.2)  # respect broker min stop
    sl_dist = round(sl_dist, d)
    tp_dist = round(1.5 * sl_dist, d)
    if side == "BUY":
        sl, tp = round(price - sl_dist, d), round(price + tp_dist, d)
    else:
        sl, tp = round(price + sl_dist, d), round(price - tp_dist, d)

    lot, est_risk, vpu = _risk_lot(spec, sl_dist, rc.RISK_PER_TRADE_USD)
    reasons = list(a.get("reasons", []))
    reasons.append(
        f"Simulation: {int(a['win_prob']*100)}% win odds, {a['expected_r']:+.2f}R expected; "
        f"typically plays out in {a['hold_label']} (day-trade, within {a['window_label']}). "
        f"Sized to risk ≈ ${est_risk if est_risk is not None else rc.RISK_PER_TRADE_USD}.")

    prop = {
        "id": str(uuid.uuid4()),
        "created_at": _now().isoformat(),
        "expires_at": (_now() + timedelta(minutes=PROPOSAL_TTL_MIN)).isoformat(),
        "signal_symbol": signal_symbol,
        "mt5_symbol": mt5_sym,
        "timeframe": timeframe,
        "side": side,
        "lot": lot,
        "entry_ref": round(price, d),
        "stop_loss": sl,
        "take_profit": tp,
        "confidence": a["confidence"],
        "volatility_pct": round(a["vol"], 2),
        "win_prob": a["win_prob"],
        "expected_r": a["expected_r"],
        "horizon_bars": a["horizon_bars"],
        "est_hold_min": a["est_hold_min"],
        "hold_label": a["hold_label"],
        "est_risk_usd": est_risk,
        "reasons": reasons,
        "status": "pending",
        "decided_at": None,
        "order_result": None,
        "position_id": None,
        "client_id": None,
        "error": None,
    }
    await db.trade_proposals.insert_one({**prop})
    return {"ok": True, "proposal": prop}


async def auto_scan() -> dict:
    """Background hunt: scan every active instrument and auto-create a proposal
    when a high-conviction setup appears. Deduped so there's at most one pending
    proposal (and no duplicate of an already-open trade) per symbol."""
    if not mt5_trader.is_configured():
        return {"created": 0, "reason": "mt5 not configured"}
    try:
        info = await mt5_trader.account_info()
    except Exception:  # noqa
        return {"created": 0, "reason": "account unreachable"}
    if not _is_demo(info):  # safety: never auto-hunt on a live account
        return {"created": 0, "reason": "not demo"}

    now = _now()
    pend = await db.trade_proposals.find(
        {"status": "pending"}, {"_id": 0, "mt5_symbol": 1, "expires_at": 1}).to_list(200)
    busy = {p["mt5_symbol"] for p in pend if datetime.fromisoformat(p["expires_at"]) > now}
    for t in await db.trades.find({"status": "open"}, {"_id": 0, "symbol": 1}).to_list(100):
        busy.add(t["symbol"])

    created = 0
    seen = set()
    cfg = await _trade_settings()
    for tgt in (await _scan_targets())[:10]:
        m = mt5_trader.mt5_symbol(tgt["signal"])
        if not m or m in seen or m in busy:
            continue
        seen.add(m)
        try:
            res = await propose(tgt["signal"], cfg["timeframe"])
        except Exception as e:  # noqa
            logger.warning(f"auto_scan propose {tgt['signal']} failed: {e}")
            continue
        if res.get("ok"):
            created += 1
            p = res["proposal"]
            logger.info(f"auto_scan: queued {p['side']} {m}")
            # Auto-approve mode: place the strongest setups automatically (demo only).
            if cfg["auto_approve"] and (p.get("win_prob") or 0) >= cfg["auto_approve_min_winprob"]:
                try:
                    ap = await approve(p["id"])
                    logger.info(f"auto_scan: AUTO-APPROVED {p['side']} {m} -> {ap.get('ok')}")
                except Exception as e:  # noqa
                    logger.warning(f"auto_scan auto-approve failed: {e}")
            try:
                import alerts
                await alerts.email_proposal(p)
            except Exception as e:  # noqa
                logger.warning(f"auto_scan email failed: {e}")
    return {"created": created}


async def trail_stops() -> dict:
    """Lock in profit on winning trades: once a trade is +1R, trail the stop-loss
    to stay 1R behind price (never loosening it). Demo only."""
    cfg = await _trade_settings()
    if not cfg["trailing_enabled"]:
        return {"trailed": 0}
    try:
        live = {str(p.get("id")): p for p in (await mt5_trader.positions() or [])}
    except Exception:  # noqa
        return {"trailed": 0}
    trailed = 0
    for t in await db.trades.find({"status": "open"}, {"_id": 0}).to_list(100):
        pid = t.get("position_id")
        pos = live.get(pid)
        if not pos:
            continue
        entry = float(t.get("entry_ref") or pos.get("openPrice") or 0)
        orig_sl = float(t.get("stop_loss") or 0)
        risk = abs(entry - orig_sl)
        cur = float(pos.get("currentPrice") or 0)
        if risk <= 0 or cur <= 0:
            continue
        side = t.get("side")
        cur_sl = pos.get("stopLoss")
        d = _digits(cur)
        if side == "BUY" and (cur - entry) >= risk:
            new_sl = round(cur - risk, d)
            if cur_sl is None or new_sl > float(cur_sl) + 1e-9:
                try:
                    await mt5_trader.modify_position(pid, new_sl, pos.get("takeProfit"))
                    await db.trades.update_one({"position_id": pid}, {"$set": {"trail_sl": new_sl}})
                    trailed += 1
                except Exception as e:  # noqa
                    logger.warning(f"trail modify failed {pid}: {e}")
        elif side == "SELL" and (entry - cur) >= risk:
            new_sl = round(cur + risk, d)
            if cur_sl is None or new_sl < float(cur_sl) - 1e-9:
                try:
                    await mt5_trader.modify_position(pid, new_sl, pos.get("takeProfit"))
                    await db.trades.update_one({"position_id": pid}, {"$set": {"trail_sl": new_sl}})
                    trailed += 1
                except Exception as e:  # noqa
                    logger.warning(f"trail modify failed {pid}: {e}")
    return {"trailed": trailed}


async def list_proposals(limit: int = 50) -> list:
    items = await db.trade_proposals.find({}, {"_id": 0}).sort("created_at", -1).to_list(limit)
    # auto-expire stale pending proposals
    now = _now()
    for p in items:
        if p["status"] == "pending" and datetime.fromisoformat(p["expires_at"]) < now:
            p["status"] = "expired"
            await db.trade_proposals.update_one({"id": p["id"]}, {"$set": {"status": "expired"}})
    return items


async def approve(proposal_id: str) -> dict:
    p = await db.trade_proposals.find_one({"id": proposal_id}, {"_id": 0})
    if not p:
        return {"ok": False, "error": "Proposal not found."}
    if p["status"] != "pending":
        return {"ok": False, "error": f"Proposal is already {p['status']}."}
    if datetime.fromisoformat(p["expires_at"]) < _now():
        await db.trade_proposals.update_one({"id": proposal_id}, {"$set": {"status": "expired"}})
        return {"ok": False, "error": "Proposal expired — please generate a fresh one."}

    block = await _risk_gate(p["mt5_symbol"], p["lot"])
    if block:
        await db.trade_proposals.update_one(
            {"id": proposal_id}, {"$set": {"status": "rejected", "error": block, "decided_at": _now().isoformat()}})
        return {"ok": False, "error": block}

    client_id = f"TC_{p['mt5_symbol'][:6]}_{proposal_id.replace('-', '')[:8]}"
    try:
        result = await mt5_trader.market_order(
            p["mt5_symbol"], p["side"], p["lot"],
            stop_loss=p["stop_loss"], take_profit=p["take_profit"],
            comment="TC", client_id=client_id)
    except Exception as e:  # noqa
        detail = getattr(e, "details", None) or getattr(e, "arguments", None)
        msg = f"{e}" + (f" | {detail}" if detail else "")
        await db.trade_proposals.update_one(
            {"id": proposal_id}, {"$set": {"status": "failed", "error": msg, "decided_at": _now().isoformat()}})
        return {"ok": False, "error": f"Order failed: {msg}"}

    position_id = str(result.get("positionId") or result.get("orderId") or "")
    await db.trade_proposals.update_one(
        {"id": proposal_id},
        {"$set": {"status": "placed", "order_result": _safe(result),
                  "position_id": position_id, "client_id": client_id,
                  "decided_at": _now().isoformat()}})
    await db.trades.insert_one({
        "id": str(uuid.uuid4()),
        "proposal_id": proposal_id,
        "placed_at": _now().isoformat(),
        "symbol": p["mt5_symbol"],
        "signal_symbol": p["signal_symbol"],
        "side": p["side"],
        "lot": p["lot"],
        "entry_ref": p["entry_ref"],
        "stop_loss": p["stop_loss"],
        "take_profit": p["take_profit"],
        "position_id": position_id,
        "client_id": client_id,
        "status": "open",
        "result_code": str(result.get("stringCode") or result.get("numericCode") or "ok"),
    })
    p2 = await db.trade_proposals.find_one({"id": proposal_id}, {"_id": 0})
    return {"ok": True, "proposal": p2}


async def reject(proposal_id: str) -> dict:
    p = await db.trade_proposals.find_one({"id": proposal_id}, {"_id": 0})
    if not p:
        return {"ok": False, "error": "Proposal not found."}
    if p["status"] != "pending":
        return {"ok": False, "error": f"Proposal is already {p['status']}."}
    await db.trade_proposals.update_one(
        {"id": proposal_id}, {"$set": {"status": "rejected", "decided_at": _now().isoformat()}})
    p["status"] = "rejected"
    return {"ok": True, "proposal": p}


# ----------------------------- live account / log -----------------------------
def _safe(v):
    if isinstance(v, dict):
        return {k: _safe(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_safe(x) for x in v]
    if hasattr(v, "model_dump"):
        return _safe(v.model_dump())
    if isinstance(v, (datetime,)):
        return v.isoformat()
    return v


async def account_summary() -> dict:
    try:
        info = await mt5_trader.account_info()
    except Exception as e:  # noqa
        return {"available": False, "reason": str(e)}
    return {
        "available": True,
        "balance": info.get("balance"),
        "equity": info.get("equity"),
        "currency": info.get("currency"),
        "trade_allowed": info.get("tradeAllowed"),
        "is_demo": _is_demo(info),
        "server": info.get("server"),
        "leverage": info.get("leverage"),
    }


async def open_positions() -> list:
    try:
        rows = await mt5_trader.positions() or []
    except Exception as e:  # noqa
        logger.warning(f"open_positions failed: {e}")
        return []
    out = []
    for p in rows:
        out.append({
            "id": str(p.get("id")),
            "symbol": p.get("symbol"),
            "type": p.get("type"),
            "volume": p.get("volume"),
            "open_price": p.get("openPrice"),
            "current_price": p.get("currentPrice"),
            "stop_loss": p.get("stopLoss"),
            "take_profit": p.get("takeProfit"),
            "profit": round(float(p.get("unrealizedProfit", p.get("profit", 0)) or 0), 2),
        })
    return out


async def trade_log(limit: int = 50) -> list:
    return await db.trades.find({}, {"_id": 0}).sort("placed_at", -1).to_list(limit)


async def close(position_id: str) -> dict:
    try:
        result = await mt5_trader.close_position(position_id)
    except Exception as e:  # noqa
        return {"ok": False, "error": str(e)}
    await db.trades.update_one({"position_id": position_id}, {"$set": {"status": "closed", "closed_at": _now().isoformat()}})
    return {"ok": True, "result": _safe(result)}
