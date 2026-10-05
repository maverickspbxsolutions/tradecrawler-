"""Adaptive trade-learning layer.

Every closed demo trade is logged as an R-multiple (profit ÷ planned risk). From
the rolling history we compute the only edge metric that matters —
EXPECTANCY in R = win_rate·avg_win_R − loss_rate·avg_loss_R — per setup
(symbol + side), and use it to:
  1) CALIBRATE the forward-simulation win odds toward what actually happens
     (the raw Monte-Carlo is over-optimistic), via sample-size shrinkage; and
  2) BLOCK setups whose realized expectancy is negative over a meaningful
     sample, so the bot stops repeating losing trades.

This is how "it learns so future trades are better": nothing is guaranteed, but
setups that lose money get sized down / skipped and the odds shown become honest.
"""
import logging
from datetime import datetime, timezone

from db import db

logger = logging.getLogger("learning")

ROLLING = 60          # most-recent closed trades used for the edge read
MIN_SAMPLE = 8        # need this many before realized stats influence decisions
BLOCK_SAMPLE = 12     # need this many before we outright block a losing setup
PRIOR_STRENGTH = 10.0  # how many "virtual" trades the model's prior is worth


def _now():
    return datetime.now(timezone.utc)


def _conf_bucket(conf) -> str:
    try:
        c = int(conf)
    except (TypeError, ValueError):
        return "na"
    return f"{(c // 10) * 10}s"


async def record_outcome(trade: dict, realized_profit: float, planned_risk: float):
    """Persist a closed trade as an R-multiple outcome."""
    try:
        risk = float(planned_risk or 0)
        profit = float(realized_profit or 0)
    except (TypeError, ValueError):
        return
    r_mult = (profit / risk) if risk > 0 else (1.0 if profit > 0 else -1.0 if profit < 0 else 0.0)
    doc = {
        "position_id": trade.get("position_id"),
        "symbol": trade.get("symbol"),
        "side": trade.get("side"),
        "rr": trade.get("rr"),
        "confidence": trade.get("confidence"),
        "conf_bucket": _conf_bucket(trade.get("confidence")),
        "regime": trade.get("regime"),
        "profit": round(profit, 2),
        "planned_risk": round(risk, 2),
        "r_multiple": round(r_mult, 3),
        "win": profit > 0,
        "closed_at": _now().isoformat(),
    }
    await db.trade_outcomes.update_one(
        {"position_id": doc["position_id"]}, {"$set": doc}, upsert=True)
    logger.info(f"learned outcome {doc['side']} {doc['symbol']}: {doc['r_multiple']:+.2f}R (${doc['profit']})")


async def _recent(symbol: str = None, side: str = None, limit: int = ROLLING):
    q = {}
    if symbol:
        q["symbol"] = symbol
    if side:
        q["side"] = side
    return await db.trade_outcomes.find(q, {"_id": 0}).sort("closed_at", -1).to_list(limit)


def _summarize(rows: list) -> dict:
    n = len(rows)
    if n == 0:
        return {"n": 0, "win_rate": None, "avg_win_r": None, "avg_loss_r": None,
                "expectancy_r": None, "total_r": 0.0}
    wins = [r["r_multiple"] for r in rows if r.get("win")]
    losses = [r["r_multiple"] for r in rows if not r.get("win")]
    win_rate = len(wins) / n
    avg_win = (sum(wins) / len(wins)) if wins else 0.0
    avg_loss = (sum(losses) / len(losses)) if losses else 0.0  # negative
    expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss
    return {"n": n, "win_rate": round(win_rate, 3), "avg_win_r": round(avg_win, 3),
            "avg_loss_r": round(avg_loss, 3), "expectancy_r": round(expectancy, 3),
            "total_r": round(sum(r["r_multiple"] for r in rows), 2)}


async def stats(symbol: str = None, side: str = None) -> dict:
    return _summarize(await _recent(symbol, side))


async def overall() -> dict:
    return _summarize(await _recent())


async def calibrate_winprob(predicted: float, symbol: str, side: str, hist: dict = None) -> float:
    """Blend the simulation's win odds with (1) a HISTORICAL backtest prior for
    this asset+side (from deep-history profiling) and (2) what the setup has
    ACTUALLY achieved live, weighting realized data by sample size. Pulls
    untested high odds toward a realistic band so a 97% never slips through."""
    pred = max(0.02, min(0.97, float(predicted)))
    base = pred
    had_prior = False
    if hist and hist.get("n") and hist["n"] >= 20 and hist.get("win") is not None:
        hn = min(30, hist["n"])
        hw = hn / (hn + 15.0)
        base = (1 - hw) * base + hw * float(hist["win"])
        had_prior = True
    s = await stats(symbol, side)
    n = s["n"] or 0
    if n >= MIN_SAMPLE and s["win_rate"] is not None:
        w = n / (n + PRIOR_STRENGTH)
        base = (1 - w) * base + w * s["win_rate"]
    elif n < MIN_SAMPLE and not had_prior:
        base = min(base, 0.78)  # honesty cap only when we have no evidence at all
    return round(max(0.02, min(0.95, base)), 3)


async def edge_block(symbol: str, side: str):
    """Return a reason string to SKIP this setup when its realized expectancy is
    clearly negative over a meaningful sample, else None."""
    s = await stats(symbol, side)
    if s["n"] >= BLOCK_SAMPLE and s["expectancy_r"] is not None and s["expectancy_r"] <= -0.15:
        return (f"Learned edge: last {s['n']} {side} {symbol} trades ran "
                f"{s['expectancy_r']:+.2f}R expectancy ({int((s['win_rate'] or 0)*100)}% win). "
                f"Skipping this setup until it recovers.")
    return None


async def edge_report() -> dict:
    """Compact learned-edge summary for the UI."""
    ov = await overall()
    by = {}
    rows = await _recent(limit=ROLLING)
    for r in rows:
        key = f"{r.get('side')} {r.get('symbol')}"
        by.setdefault(key, []).append(r)
    setups = []
    for key, rs in by.items():
        d = _summarize(rs)
        d["setup"] = key
        setups.append(d)
    setups.sort(key=lambda x: (x["expectancy_r"] if x["expectancy_r"] is not None else 0), reverse=True)
    return {"overall": ov, "setups": setups[:8]}
