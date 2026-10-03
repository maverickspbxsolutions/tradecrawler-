"""Main API routes (strategies, backtests, signals, paper-trading, market, settings)."""
import asyncio
import uuid
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

from db import db
from auth import get_current_user
import market_data as md
from strategy_engine import Strategy, StrategyCreate, StrategyUpdate
from backtester import run_backtest
from paper_engine import run_strategy_step, backfill_signals, PAPER_CAPITAL
from alerts import get_settings, send_email, EMAIL_FROM_NAME
from scheduler import POLL_SECONDS, EVOLVE_SECONDS
from evolution import evolve_strategy, detect_regime

api = APIRouter(prefix="/api", dependencies=[Depends(get_current_user)])


# ---------------- Market ----------------
@api.get("/market/symbols")
async def symbols():
    return {"symbols": md.POPULAR_SYMBOLS, "timeframes": list(md.TIMEFRAMES.keys())}


@api.get("/market/candles")
async def candles(symbol: str = "BTCUSDT", timeframe: str = "1h", limit: int = 300):
    limit = max(50, min(limit, 1000))
    data = await md.get_candles(symbol, timeframe, limit)
    return {"symbol": symbol.upper(), "timeframe": timeframe, "candles": data}


@api.get("/market/health")
async def market_health():
    engine = await db.engine_status.find_one({"id": "engine"}, {"_id": 0})
    last_ok = md.health.get("last_ok")
    stale = (datetime.now(timezone.utc).timestamp() - last_ok) > 180 if last_ok else True
    return {
        "source": md.health.get("source"),
        "error": md.health.get("error"),
        "stale": stale,
        "engine": engine,
        "poll_seconds": POLL_SECONDS,
    }


# ---------------- Strategies ----------------
@api.get("/strategies")
async def list_strategies():
    strats = await db.strategies.find({}, {"_id": 0}).sort("created_at", -1).to_list(500)
    for s in strats:
        bt = await db.backtests.find_one({"strategy_id": s["id"]}, {"_id": 0, "equity_curve": 0, "trades": 0})
        st = await db.paper_states.find_one({"strategy_id": s["id"]}, {"_id": 0})
        s["backtest"] = bt
        s["paper"] = st
    return strats


@api.post("/strategies")
async def create_strategy(body: StrategyCreate):
    strat = Strategy(**body.model_dump())
    await db.strategies.insert_one(strat.model_dump())
    asyncio.create_task(backfill_signals(strat.model_dump()))
    return strat.model_dump()


@api.get("/strategies/{sid}")
async def get_strategy(sid: str):
    s = await db.strategies.find_one({"id": sid}, {"_id": 0})
    if not s:
        raise HTTPException(404, "Strategy not found")
    return s


@api.put("/strategies/{sid}")
async def update_strategy(sid: str, body: StrategyUpdate):
    s = await db.strategies.find_one({"id": sid}, {"_id": 0})
    if not s:
        raise HTTPException(404, "Strategy not found")
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    # live can only be enabled if gate passed
    if updates.get("live") and not s.get("gate_passed"):
        raise HTTPException(400, "Strategy must pass backtest gate before going live")
    if updates:
        await db.strategies.update_one({"id": sid}, {"$set": updates})
    return await db.strategies.find_one({"id": sid}, {"_id": 0})


@api.delete("/strategies/{sid}")
async def delete_strategy(sid: str):
    await db.strategies.delete_one({"id": sid})
    await db.backtests.delete_many({"strategy_id": sid})
    await db.paper_states.delete_many({"strategy_id": sid})
    await db.paper_trades.delete_many({"strategy_id": sid})
    await db.signals.delete_many({"strategy_id": sid})
    return {"deleted": True}


class BacktestInput(BaseModel):
    limit: int = 500


@api.post("/strategies/{sid}/backtest")
async def backtest_strategy(sid: str, body: BacktestInput):
    s = await db.strategies.find_one({"id": sid}, {"_id": 0})
    if not s:
        raise HTTPException(404, "Strategy not found")
    limit = max(100, min(body.limit, 1000))
    try:
        data = await md.get_candles(s["symbol"], s["timeframe"], limit)
    except Exception as e:  # noqa
        raise HTTPException(502, f"Market data error: {e}")
    result = run_backtest(data, s)
    if "error" in result:
        raise HTTPException(400, result["error"])
    result["strategy_id"] = sid
    result["ran_at"] = datetime.now(timezone.utc).isoformat()
    await db.backtests.replace_one({"strategy_id": sid}, result, upsert=True)
    # gate: update strategy; if gate fails, force live off
    new_live = s.get("live", False) and result["gate_passed"]
    await db.strategies.update_one(
        {"id": sid}, {"$set": {"gate_passed": result["gate_passed"], "live": new_live}})
    return result


@api.get("/strategies/{sid}/backtest")
async def get_backtest(sid: str):
    bt = await db.backtests.find_one({"strategy_id": sid}, {"_id": 0})
    if not bt:
        raise HTTPException(404, "No backtest yet")
    return bt


@api.post("/strategies/{sid}/scan")
async def scan_strategy(sid: str):
    s = await db.strategies.find_one({"id": sid}, {"_id": 0})
    if not s:
        raise HTTPException(404, "Strategy not found")
    sig = await run_strategy_step(s, source="scan")
    return {"signal": sig}


@api.post("/strategies/{sid}/backfill")
async def backfill(sid: str):
    s = await db.strategies.find_one({"id": sid}, {"_id": 0})
    if not s:
        raise HTTPException(404, "Strategy not found")
    count = await backfill_signals(s)
    return {"backfilled": count}


@api.post("/scan-all")
async def scan_all():
    strats = await db.strategies.find({"enabled": True}, {"_id": 0}).to_list(500)
    signals = []
    for s in strats:
        sig = await run_strategy_step(s, source="scan")
        if sig:
            signals.append(sig)
    return {"scanned": len(strats), "signals": signals}


# ---------------- Evolution (adaptive engine) ----------------
@api.post("/strategies/{sid}/evolve")
async def evolve(sid: str):
    s = await db.strategies.find_one({"id": sid}, {"_id": 0})
    if not s:
        raise HTTPException(404, "Strategy not found")
    result = await evolve_strategy(s)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@api.get("/strategies/{sid}/evolution")
async def evolution(sid: str):
    latest = await db.evolutions.find_one({"strategy_id": sid}, {"_id": 0}, sort=[("ran_at", -1)])
    history = await db.evolutions.find(
        {"strategy_id": sid},
        {"_id": 0, "ran_at": 1, "applied": 1, "improved": 1, "improvement": 1,
         "current_oos_fitness": 1, "candidate_oos_fitness": 1, "regime": 1},
    ).sort("ran_at", -1).to_list(15)
    return {"latest": latest, "history": history}


@api.post("/evolve-all")
async def evolve_all():
    strats = await db.strategies.find({"enabled": True}, {"_id": 0}).to_list(500)
    applied = 0
    for s in strats:
        r = await evolve_strategy(s)
        if r.get("applied"):
            applied += 1
    return {"evolved": len(strats), "applied": applied}


@api.get("/market/regime")
async def market_regime(symbol: str = "BTCUSDT", timeframe: str = "1h"):
    candles = await md.get_candles(symbol, timeframe, 300)
    return detect_regime(candles)


# ---------------- Signals feed ----------------
@api.get("/signals")
async def signals(limit: int = 50, strategy_id: Optional[str] = None):
    q = {}
    if strategy_id:
        q["strategy_id"] = strategy_id
    limit = max(1, min(limit, 200))
    items = await db.signals.find(q, {"_id": 0}).sort("created_at", -1).to_list(limit)
    return items


# ---------------- Paper trading ----------------
@api.get("/paper/{sid}")
async def paper(sid: str):
    state = await db.paper_states.find_one({"strategy_id": sid}, {"_id": 0})
    trades = await db.paper_trades.find({"strategy_id": sid}, {"_id": 0}).sort("created_at", 1).to_list(500)
    # cumulative realized equity curve
    equity = PAPER_CAPITAL
    curve = [{"t": None, "equity": round(equity, 2)}]
    for t in trades:
        equity += t["pnl"]
        curve.append({"t": t["exit_time"], "equity": round(equity, 2)})
    return {"state": state, "trades": trades[::-1], "equity_curve": curve, "initial": PAPER_CAPITAL}


# ---------------- Overview ----------------
@api.get("/overview")
async def overview():
    strats = await db.strategies.find({}, {"_id": 0}).to_list(500)
    live = [s for s in strats if s.get("live")]
    enabled = [s for s in strats if s.get("enabled")]
    day_cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    signals_today = await db.signals.count_documents({"created_at": {"$gte": day_cutoff}})
    states = await db.paper_states.find({}, {"_id": 0}).to_list(500)
    total_pnl = sum(st.get("realized_pnl", 0) for st in states)
    total_trades = sum(st.get("num_trades", 0) for st in states)
    total_wins = sum(st.get("wins", 0) for st in states)
    alerts_today = await db.alerts.count_documents({"sent_at": {"$gte": day_cutoff}})
    engine = await db.engine_status.find_one({"id": "engine"}, {"_id": 0})
    return {
        "num_strategies": len(strats),
        "num_enabled": len(enabled),
        "num_live": len(live),
        "signals_today": signals_today,
        "alerts_today": alerts_today,
        "total_paper_pnl": round(total_pnl, 2),
        "total_paper_trades": total_trades,
        "win_rate": round(total_wins / total_trades * 100, 1) if total_trades else 0.0,
        "engine": engine,
        "data_source": md.health.get("source"),
        "poll_seconds": POLL_SECONDS,
    }


# ---------------- Settings & alerts ----------------
class SettingsInput(BaseModel):
    alert_email: Optional[str] = None
    email_enabled: Optional[bool] = None
    sms_enabled: Optional[bool] = None
    max_alerts_per_hour: Optional[int] = None
    max_emails_per_day: Optional[int] = None
    min_minutes_between_same_strategy: Optional[int] = None


@api.get("/settings")
async def read_settings():
    return await get_settings()


@api.put("/settings")
async def update_settings(body: SettingsInput):
    await get_settings()
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if updates:
        await db.settings.update_one({"id": "global"}, {"$set": updates})
    return await get_settings()


@api.post("/alerts/test")
async def test_alert():
    s = await get_settings()
    if not s.get("alert_email"):
        raise HTTPException(400, "Set an alert email first")
    html = (
        f'<table role="presentation" width="100%" style="background:#0A0A0C;padding:24px">'
        f'<tr><td style="font-family:Arial,sans-serif;color:#EDEDED">'
        f'<h1 style="color:#00E5FF">Test alert from {EMAIL_FROM_NAME}</h1>'
        f'<p>Your email alerts are configured correctly.</p>'
        f'<div style="background:#FFE600;color:#000;padding:10px;font-size:11px;'
        f'font-weight:bold;text-align:center;margin-top:12px">'
        f'SIGNALS ARE NOT FINANCIAL ADVICE. PAPER-TRADING ONLY.</div>'
        f'</td></tr></table>')
    try:
        email_id = await send_email(to=s["alert_email"], subject=f"{EMAIL_FROM_NAME} test alert", html=html)
    except Exception as e:  # noqa
        raise HTTPException(502, f"Email failed: {e}")
    return {"sent": True, "email_id": email_id}


@api.get("/alerts")
async def recent_alerts(limit: int = 30):
    items = await db.alerts.find({}, {"_id": 0}).sort("sent_at", -1).to_list(min(limit, 100))
    return items
