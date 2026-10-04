"""Main API routes (strategies, backtests, signals, paper-trading, market, settings)."""
import asyncio
import uuid
from datetime import datetime, timezone, timedelta
import numpy as np

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

from db import db
from auth import get_current_user
import market_data as md
from strategy_engine import Strategy, StrategyCreate, StrategyUpdate
from backtester import run_backtest
from paper_engine import run_strategy_step, backfill_signals, PAPER_CAPITAL, _humanize_duration
from alerts import get_settings, send_email, EMAIL_FROM_NAME
from scheduler import POLL_SECONDS, EVOLVE_SECONDS
from evolution import evolve_strategy, detect_regime
from outlook import compute_outlook
import risk_config

api = APIRouter(prefix="/api", dependencies=[Depends(get_current_user)])


@api.get("/risk/config")
async def get_risk_config():
    """Demo-only trade risk caps (kill switch, max lot, allowlist)."""
    return risk_config.as_dict()


async def _auto_evaluate(sid: str):
    """Background: backtest a strategy and auto-enable alerts if its edge is proven."""
    s = await db.strategies.find_one({"id": sid}, {"_id": 0})
    if not s:
        return
    try:
        candles = await md.get_candles(s["symbol"], s["timeframe"], 500)
        result = run_backtest(candles, s)
        if "error" in result:
            return
        result["strategy_id"] = sid
        result["ran_at"] = datetime.now(timezone.utc).isoformat()
        await db.backtests.replace_one({"strategy_id": sid}, result, upsert=True)
        await db.strategies.update_one(
            {"id": sid}, {"$set": {"gate_passed": result["gate_passed"], "live": result["gate_passed"]}})
    except Exception:  # noqa
        pass


# ---------------- Market ----------------
@api.get("/market/symbols")
async def symbols():
    # Prefer the REAL Deriv MT5 universe (same instruments the user trades)
    mt5_opts = await md.build_mt5_options()
    if mt5_opts:
        def rank_mt5(o):
            n = (o["symbol"] + " " + o["market"]).lower()
            if o["symbol"] in ("XAUUSD",) or "gold" in n:
                return 0
            if o["symbol"] in ("BTCUSD",) or n.startswith("btc"):
                return 1
            if "volatility" in n:
                return 2
            if o["market"] == "Synthetic & Volatility":
                return 3
            if o["market"] == "Metals":
                return 4
            if o["market"] == "Crypto":
                return 5
            if o["market"] == "Stock Indices":
                return 6
            return 8
        ordered = sorted(mt5_opts, key=rank_mt5)
        return {"options": ordered, "symbols": [o["symbol"] for o in ordered],
                "default": "BTCUSD", "timeframes": list(md.TIMEFRAMES.keys()),
                "deriv_available": True, "source": "deriv-mt5"}

    crypto = await md.get_all_symbols()
    deriv = await md.get_deriv_symbols()
    options = [{"symbol": s, "label": s.replace("USDT", "") + " / USDT",
                "market": "Crypto (Binance)", "source": "binance"} for s in crypto]

    def rank(d):
        lbl = (d["label"] + " " + d["symbol"]).lower()
        m = d["market"].lower()
        if "gold" in lbl or d["symbol"] == "frxXAUUSD":
            return 0
        if d["symbol"].startswith("cry") or "bitcoin" in lbl or "btc" in lbl:
            return 1
        if "volatility" in m or "volatility" in lbl or d["symbol"].startswith(("R_", "1HZ")):
            return 2
        return 5

    for d in sorted(deriv, key=rank):
        options.append({"symbol": d["symbol"], "label": d["label"],
                        "market": f"Deriv · {d['market']}", "source": "deriv"})

    return {"options": options, "symbols": [o["symbol"] for o in options],
            "default": "BTCUSDT", "timeframes": list(md.TIMEFRAMES.keys()),
            "deriv_available": len(deriv) > 0, "source": "fallback"}


@api.get("/market/candles")
async def candles(symbol: str = "BTCUSDT", timeframe: str = "1h", limit: int = 300):
    limit = max(50, min(limit, 1000))
    try:
        data = await md.get_candles(symbol, timeframe, limit)
    except md.DerivUnavailable as e:
        return {"symbol": symbol, "timeframe": timeframe, "candles": [],
                "available": False, "reason": str(e)}
    except Exception as e:  # noqa
        raise HTTPException(502, f"Could not load {symbol} data: {e}")
    return {"symbol": symbol, "timeframe": timeframe, "candles": data, "available": True}


@api.get("/market/live")
async def market_live(symbol: str = "BTCUSDT", timeframe: str = "1h"):
    try:
        return await md.get_live(symbol, timeframe)
    except Exception as e:  # noqa
        return {"symbol": symbol, "timeframe": timeframe, "price": None,
                "candle": None, "source": None, "available": False, "reason": str(e)}


@api.get("/market/outlook")
async def market_outlook(symbol: str = "BTCUSDT", timeframe: str = "1h"):
    try:
        candles = await md.get_candles(symbol, timeframe, 300)
    except md.DerivUnavailable as e:
        return {"symbol": symbol, "timeframe": timeframe, "direction": "Neutral",
                "confidence": 0, "score": 0, "metrics": {}, "reasons": [],
                "available": False, "reason": str(e),
                "disclaimer": "Market data unavailable for this instrument right now."}
    except Exception as e:  # noqa
        raise HTTPException(502, f"Could not load {symbol} data: {e}")
    result = compute_outlook(candles, symbol, timeframe)
    result["available"] = True
    return result


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
    ids = [s["id"] for s in strats]
    bts = await db.backtests.find(
        {"strategy_id": {"$in": ids}}, {"_id": 0, "equity_curve": 0, "trades": 0}).to_list(1000)
    sts = await db.paper_states.find({"strategy_id": {"$in": ids}}, {"_id": 0}).to_list(1000)
    bt_map = {b["strategy_id"]: b for b in bts}
    st_map = {p["strategy_id"]: p for p in sts}
    for s in strats:
        s["backtest"] = bt_map.get(s["id"])
        s["paper"] = st_map.get(s["id"])
    return strats


@api.post("/strategies")
async def create_strategy(body: StrategyCreate):
    strat = Strategy(**body.model_dump())
    await db.strategies.insert_one(strat.model_dump())
    asyncio.create_task(backfill_signals(strat.model_dump()))
    asyncio.create_task(_auto_evaluate(strat.id))
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
    # autonomous: auto-enable alerts the moment the edge is proven; disable if it drops
    await db.strategies.update_one(
        {"id": sid}, {"$set": {"gate_passed": result["gate_passed"], "live": result["gate_passed"]}})
    return result


@api.get("/strategies/{sid}/backtest")
async def get_backtest(sid: str):
    bt = await db.backtests.find_one({"strategy_id": sid}, {"_id": 0})
    if not bt:
        raise HTTPException(404, "No backtest yet")
    return bt


@api.get("/strategies/{sid}/simulate")
async def simulate(sid: str, trades: int = 20, paths: int = 400, capital: float = 20.0):
    """Streak-aware Monte-Carlo tuned to current market conditions.
    - Block bootstrap of the strategy's historical trade returns (preserves win/loss streaks).
    - Scales magnitude to today's volatility and tilts drift by the live Market Outlook.
    - Reports typical/good/rough endings, profit odds, worst drawdown, risk of losing half,
      and a realistic time horizon. Rough preview only — not a prediction."""
    s = await db.strategies.find_one({"id": sid}, {"_id": 0})
    if not s:
        raise HTTPException(404, "Strategy not found")
    bt = await db.backtests.find_one({"strategy_id": sid}, {"_id": 0})
    trades = max(5, min(trades, 60))
    paths = max(50, min(paths, 1000))

    returns = []
    if bt and bt.get("trades"):
        returns = [t["return_pct"] for t in bt["trades"] if isinstance(t.get("return_pct"), (int, float))]
    if len(returns) < 3 and bt:
        wr = (bt.get("win_rate") or 0) / 100.0
        aw = bt.get("avg_win_pct") or 2.5
        al = bt.get("avg_loss_pct") or -1.5
        nwin = max(1, int(round(wr * 20)))
        returns = [aw] * nwin + [al] * max(1, 20 - nwin)
    if not returns:
        raise HTTPException(400, "Run a test first so there's data to simulate")

    seq = np.array(returns, dtype=float) / 100.0  # ordered historical trade returns (fractions)
    n = len(seq)

    # ---- tune to current market conditions (volatility + live outlook) ----
    vol_ratio = 1.0
    tilt = 0.0
    tuning = {"applied": False, "direction": None, "confidence": None,
              "vol_ratio": 1.0, "regime": None, "reason": None}
    try:
        candles = await md.get_candles(s["symbol"], s["timeframe"], 300)
        closes = np.array([c["c"] for c in candles], dtype=float)
        if len(closes) > 30:
            r = np.diff(closes) / closes[:-1]
            vol_recent = float(np.std(r[-20:]))
            vol_ref = float(np.std(r[-120:])) if len(r) >= 120 else float(np.std(r))
            if vol_ref > 0:
                vol_ratio = float(np.clip(vol_recent / vol_ref, 0.6, 1.6))
        ol = compute_outlook(candles, s["symbol"], s["timeframe"])
        regime = detect_regime(candles)
        sign = 1 if ol["direction"] == "Up" else -1 if ol["direction"] == "Down" else 0
        tilt = sign * (ol.get("confidence", 0) / 100.0) * 0.004  # up to ±0.4% drift/trade
        tuning = {"applied": True, "direction": ol["direction"],
                  "confidence": ol.get("confidence", 0), "vol_ratio": round(vol_ratio, 2),
                  "regime": regime, "reason": None}
    except Exception as e:  # noqa
        tuning["reason"] = "Live market data unavailable — showing untuned history."

    # ---- streak-aware block bootstrap ----
    rng = np.random.default_rng()
    block = int(min(max(2, n // 4), 5))
    draws = np.empty((paths, trades), dtype=float)
    for p in range(paths):
        row = []
        while len(row) < trades:
            start = int(rng.integers(0, n))
            row.extend(seq[start:start + block].tolist())
        draws[p] = row[:trades]
    draws = draws * vol_ratio + tilt  # apply current-market tuning

    equity = np.concatenate(
        [np.full((paths, 1), capital), np.cumprod(1.0 + draws, axis=1) * capital], axis=1)
    ending = equity[:, -1]
    order = np.argsort(ending)

    runmax = np.maximum.accumulate(equity, axis=1)
    path_maxdd = (equity / runmax - 1.0).min(axis=1)  # most-negative dip per path

    avg_hold_ms = (bt or {}).get("avg_hold_ms")
    horizon = _humanize_duration(avg_hold_ms * trades) if avg_hold_ms else None

    stats = {
        "capital": capital,
        "trades": trades,
        "paths": paths,
        "median": round(float(np.median(ending)), 2),
        "p10": round(float(np.percentile(ending, 10)), 2),
        "p90": round(float(np.percentile(ending, 90)), 2),
        "best": round(float(ending.max()), 2),
        "worst": round(float(ending.min()), 2),
        "prob_profit": round(float((ending > capital).mean() * 100), 1),
        "prob_lose_half": round(float((ending < capital * 0.5).mean() * 100), 1),
        "median_max_drawdown_pct": round(float(np.median(path_maxdd)) * 100, 1),
        "avg_trade_pct": round(float(seq.mean() * 100), 3),
        "sample_size": len(returns),
        "expected_duration": horizon,
    }
    picks = [order[int(q * (paths - 1))] for q in (0.05, 0.2, 0.35, 0.5, 0.65, 0.8, 0.95)]
    sample_paths = [{"id": f"p{i}", "points": [round(float(x), 4) for x in equity[idx]]}
                    for i, idx in enumerate(picks)]
    median_path = [round(float(x), 4) for x in equity[order[paths // 2]]]
    return {"stats": stats, "sample_paths": sample_paths, "median_path": median_path,
            "tuning": tuning}


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
        "trade_capital": 20.0,
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
    alert_emails: Optional[list] = None
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
    recipients = [e.strip() for e in (s.get("alert_emails") or []) if e and e.strip()]
    if not recipients:
        raise HTTPException(400, "Add at least one email first")
    html = (
        f'<table role="presentation" width="100%" style="background:#0A0A0C;padding:24px">'
        f'<tr><td style="font-family:Arial,sans-serif;color:#EDEDED">'
        f'<h1 style="color:#00E5FF">TradeCrawler is watching 🕷️</h1>'
        f'<p>Your email alerts are set up correctly. When TradeCrawler spots a good '
        f'~$20 trade, it will email you right here, 24/7 — with exactly what to do.</p>'
        f'<div style="background:#FFE600;color:#000;padding:10px;font-size:11px;'
        f'font-weight:bold;text-align:center;margin-top:12px">'
        f'NOT FINANCIAL ADVICE. SUGGESTIONS ONLY &mdash; NO ORDERS ARE PLACED.</div>'
        f'</td></tr></table>')
    try:
        email_id = await send_email(to=recipients, subject="TradeCrawler test alert", html=html)
    except Exception as e:  # noqa
        raise HTTPException(502, f"Email failed: {e}")
    return {"sent": True, "email_id": email_id, "recipients": recipients}


@api.get("/alerts")
async def recent_alerts(limit: int = 30):
    items = await db.alerts.find({}, {"_id": 0}).sort("sent_at", -1).to_list(min(limit, 100))
    return items
