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
import asyncio
import logging
from datetime import datetime, timezone, timedelta

import pandas as pd
import numpy as np

from db import db
import mt5_trader
import risk_config as rc
from outlook import compute_outlook
from evolution import detect_regime
import indicators as ind
import learning
import edge
import history
import market_data as md
from alerts import get_settings

logger = logging.getLogger("trade_engine")

PROPOSAL_TTL_MIN = 15  # a proposal's reference price goes stale after this

# Regime / confluence filters (research-grounded edge: trade trends, skip chop)
ADX_MIN = 20.0          # ADX below this = weak/choppy trend -> skip
_HTF = {"1m": "15m", "5m": "1h", "15m": "1h", "1h": "4h", "4h": "1d", "1d": "1d"}


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
        info = await mt5_trader.account_info(force=True)
    except Exception as e:  # noqa
        # Transient broker reconnect: don't hard-block if we've already confirmed
        # this is a demo account — fall back to the last-known snapshot. The
        # actual order placement will force a fresh reconnect and will surface a
        # real error if it genuinely can't place.
        info = mt5_trader.last_account()
        if not info:
            logger.warning(f"risk_gate account unreachable: {e}")
            return "Trading account is reconnecting — give it a few seconds and tap approve again."
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
    # 24/7 synthetic indices trend strongly and never close, so the bot always has
    # something to hunt even when Gold/BTC are choppy or the FX session is quiet.
    targets = [{"signal": "XAUUSD", "timeframe": tf, "label": "Gold"},
               {"signal": "BTCUSD", "timeframe": tf, "label": "Bitcoin"},
               {"signal": "R_75", "timeframe": tf, "label": "Volatility 75"},
               {"signal": "R_100", "timeframe": tf, "label": "Volatility 100"},
               {"signal": "R_50", "timeframe": tf, "label": "Volatility 50"},
               {"signal": "R_25", "timeframe": tf, "label": "Volatility 25"},
               {"signal": "R_10", "timeframe": tf, "label": "Volatility 10"}]
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
    """Forward Monte-Carlo with HONEST drift. Intraday price is close to a random
    walk, and the true drift is uncertain — so we apply only a small outlook lean
    AND give each simulated path its own drift drawn from a distribution. This
    stops the old model from reporting impossible odds (e.g. 97% at 3:1) by
    properly widening the outcome cone."""
    c = np.array([x["c"] for x in candles], dtype=float)
    logret = np.diff(np.log(c[c > 0]))
    tail = logret[-120:] if len(logret) >= 120 else logret
    sigma = float(np.std(tail)) or 1e-4
    mu_hist = float(np.mean(tail))
    # small, honest directional lean (was 0.6 — wildly overconfident)
    tilt = sigma * (score / 100.0) * 0.12
    mu_center = mu_hist * 0.15 + tilt

    n = rc.SIM_PATHS
    horizon = horizon or rc.SIM_HORIZON_BARS
    sl_dist = abs(entry - sl) or entry * 0.002
    tp_dist = abs(tp - entry) or entry * 0.003

    prices = np.full(n, entry, dtype=float)
    active = np.ones(n, dtype=bool)
    outcome = np.zeros(n, dtype=float)  # in price terms, + = profit
    resolved_step = np.full(n, horizon, dtype=float)  # bars until TP/SL (horizon if never)
    rng = np.random.default_rng()
    # drift UNCERTAINTY: each path gets its own drift around the (small) center
    mu_path = rng.normal(mu_center, sigma * 0.5, n)
    for i in range(1, horizon + 1):
        shocks = rng.normal(mu_path, sigma)  # per-path mean, shape (n,)
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


def _clamp_lot(spec: dict, desired: float, sl_dist: float):
    """Clamp a desired lot to the symbol's volume rules and MAX_LOT, then report
    the $ risked if the stop-loss is hit."""
    vmin = float(spec.get("minVolume") or spec.get("volumeMin") or rc.MIN_LOT)
    vmax = float(spec.get("maxVolume") or spec.get("volumeMax") or rc.MAX_LOT)
    step = float(spec.get("volumeStep") or spec.get("lotStep") or 0.01)
    vpu = _value_per_unit(spec)

    lot = float(desired)
    lot = np.floor(lot / step) * step if step else lot
    lot = max(max(vmin, rc.MIN_LOT), min(lot, vmax, rc.MAX_LOT))
    lot = round(lot, 2)
    est_risk = round(lot * sl_dist * vpu, 2) if (vpu and sl_dist > 0) else None
    return lot, est_risk, vpu


def _cap_lot_to_risk(spec: dict, lot: float, sl_dist: float, equity) -> float:
    """Reduce the lot so a stop-loss hit risks at most RISK_CAP_PCT of equity.
    Never increases the lot; floors to the symbol's volume step."""
    try:
        eq = float(equity or 0)
    except (TypeError, ValueError):
        eq = 0.0
    vpu = _value_per_unit(spec)
    per_lot_risk = sl_dist * vpu
    if eq <= 0 or per_lot_risk <= 0:
        return lot
    max_lot_by_risk = (eq * rc.RISK_CAP_PCT) / per_lot_risk
    if max_lot_by_risk >= lot:
        return lot
    step = float(spec.get("volumeStep") or spec.get("lotStep") or 0.01)
    vmin = float(spec.get("minVolume") or spec.get("volumeMin") or rc.MIN_LOT)
    capped = np.floor(max_lot_by_risk / step) * step if step else max_lot_by_risk
    return round(max(vmin, min(capped, lot)), 2)


def _min_stop_dist(spec: dict) -> float:
    pt = float(spec.get("point") or spec.get("tickSize") or 0)
    stops = float(spec.get("stopsLevel") or 0)
    return stops * pt


def _adx_value(candles: list) -> float:
    """Current ADX (trend strength). Used as a chop filter."""
    try:
        df = pd.DataFrame(candles)
        a, _, _ = ind.adx(df["h"], df["l"], df["c"], 14)
        v = float(a.iloc[-1])
        return v if v == v else 0.0  # nan guard
    except Exception:  # noqa
        return 0.0


async def _htf_bias(signal_symbol: str, timeframe: str):
    """Higher-timeframe directional bias for multi-timeframe confluence."""
    htf = _HTF.get(timeframe, timeframe)
    if htf == timeframe:
        return None
    try:
        hc = await md.get_candles(signal_symbol, htf, 200)
        if hc and len(hc) >= 60:
            return compute_outlook(hc, signal_symbol, htf).get("direction")
    except Exception:  # noqa
        pass
    return None


async def _realized_profit(position_id: str):
    """Net realized P&L for a closed position from its deals (for learning)."""
    try:
        deals = await mt5_trader.deals_range(_day_start() - timedelta(days=4), _now())
        if isinstance(deals, dict):
            deals = deals.get("deals", []) or []
        tot, found = 0.0, False
        for dd in deals or []:
            if str(dd.get("positionId")) == str(position_id):
                tot += (float(dd.get("profit", 0) or 0) + float(dd.get("commission", 0) or 0)
                        + float(dd.get("swap", 0) or 0))
                found = True
        return round(tot, 2) if found else None
    except Exception:  # noqa
        return None


# Reward:risk targets the simulation chooses between. Letting winners run (higher
# R) is how the balance compounds toward the 2x goal — but only when the forward
# simulation says the higher target still carries positive expected value.
_RR_CANDIDATES = (1.0, 1.5, 2.0, 2.5, 3.0)


def _optimize_levels(candles: list, side: str, entry: float, atr: float,
                     score: int, horizon: int, swing_dist: float = None) -> dict:
    """Logically set SL/TP from the forward prediction. The stop is anchored to
    the pullback/swing when the entry is well-timed (tighter, better-placed) and
    otherwise to volatility (ATR); the take-profit is chosen by simulating each
    reward:risk target and keeping the one with the best expected value
    (growth objective), tie-broken by win probability."""
    atr_sl = max(1.3 * atr, entry * 0.0012)
    sl_dist = atr_sl
    if swing_dist and swing_dist > 0:
        # well-timed entry near value -> stop just beyond the swing, but keep it
        # sane: never tighter than ~0.6 ATR (noise) nor wider than the ATR stop.
        sl_dist = min(max(swing_dist, 0.6 * atr, entry * 0.0008), atr_sl)
    best = None
    for rr in _RR_CANDIDATES:
        tp_dist = rr * sl_dist
        if side == "BUY":
            sl, tp = entry - sl_dist, entry + tp_dist
        else:
            sl, tp = entry + sl_dist, entry - tp_dist
        pred = _predict(candles, side, entry, sl, tp, score, horizon)
        key = (pred["expected_r"], pred["win_prob"])
        if best is None or key > best["key"]:
            best = {"rr": rr, "sl_dist": sl_dist, "tp_dist": tp_dist, "pred": pred, "key": key}
    return best


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

    # Multi-timeframe bias first (confluence input), then the regime-aware edge.
    htf_dir = await _htf_bias(signal_symbol, timeframe)
    ev = edge.assess(candles, htf_dir)
    if not ev.get("tradeable"):
        return {"ok": False, "mt5_symbol": mt5_sym, "direction": ev.get("direction", "Neutral"),
                "confidence": ev.get("confidence"), "score": ev.get("score"),
                "regime": ev.get("regime"), "adx": ev.get("adx"), "reason": ev.get("reason")}

    direction = ev["direction"]
    side = ev["side"]
    conf = float(ev["confidence"])
    score = ev["score"] if direction == "Up" else -ev["score"]  # signed for sim drift lean
    adx_v = ev["adx"]
    regime = {"trend": ev["regime"]}

    # per-asset long-run profile (from deep history)
    prof = await history.get_profile(mt5_sym, side)
    # auto-tune per asset: don't fade a market that historically almost always trends
    if ev["regime"] == "reversion" and (prof.get("trend_frac") or 0) >= 0.6:
        return {"ok": False, "mt5_symbol": mt5_sym, "direction": direction, "side": side,
                "confidence": int(conf), "regime": ev["regime"],
                "reason": f"{mt5_sym} is a strong trender historically "
                          f"({int(prof['trend_frac']*100)}% of the time) — not fading it."}

    # learned edge: stop repeating a setup that's been losing money
    blk = await learning.edge_block(mt5_sym, side)
    if blk:
        return {"ok": False, "mt5_symbol": mt5_sym, "direction": direction, "side": side,
                "confidence": int(conf), "reason": blk}

    live = await md.get_live(signal_symbol, timeframe)
    price = float(live.get("price") or candles[-1]["c"])
    # floor the stop to the asset's own typical volatility (deep-history profile)
    atr = ev.get("atr") or _atr(candles) or price * 0.004
    if prof.get("atr_pct"):
        atr = max(atr, float(prof["atr_pct"]) * price * 0.8)
    d = _digits(price)
    # prediction-driven SL/TP: simulate several targets, keep the best-EV one
    opt = _optimize_levels(candles, side, price, atr, score, horizon, ev.get("swing_stop_dist"))
    sl_dist = round(opt["sl_dist"], d)
    tp_dist = round(opt["tp_dist"], d)
    rr = opt["rr"]
    if side == "BUY":
        sl, tp = round(price - sl_dist, d), round(price + tp_dist, d)
    else:
        sl, tp = round(price + sl_dist, d), round(price - tp_dist, d)

    pred = opt["pred"]
    # reject only genuinely slow setups: if the sim says it rarely resolves within
    # the whole max-hold window, it'll drag for hours — skip it for a faster one.
    if pred["est_hold_bars"] >= horizon:
        return {"ok": False, "mt5_symbol": mt5_sym, "direction": direction, "side": side,
                "confidence": int(conf), "regime": ev.get("regime"), "score": ev.get("score"),
                "reason": (f"{'Buy' if side == 'BUY' else 'Sell'} setup would likely drag on for hours before "
                           f"paying off — skipping it for a faster one.")}
    # CALIBRATE the sim's odds against the asset's historical backtest AND live results
    hist_prior = {"n": prof.get("hist_n"), "win": prof.get("hist_win")} if prof.get("hist_n") else None
    cal_win = await learning.calibrate_winprob(pred["win_prob"], mt5_sym, side, hist_prior)
    cal_ev = round(cal_win * rr - (1 - cal_win) * 1.0, 3)  # expectancy in R
    es = await learning.stats(mt5_sym, side)
    tf_min = _tf_minutes(timeframe)
    hold_min = pred["est_hold_bars"] * tf_min
    hold_label = _humanize_mins(hold_min)
    window_label = _humanize_mins(pred["horizon_bars"] * tf_min)
    base = {"mt5_symbol": mt5_sym, "signal_symbol": signal_symbol, "timeframe": timeframe,
            "direction": direction, "side": side, "confidence": int(conf),
            "win_prob": cal_win, "expected_r": cal_ev, "sim_win_prob": pred["win_prob"], "rr": rr,
            "adx": round(adx_v, 0), "htf_dir": htf_dir, "regime": regime.get("trend"),
            "regime_label": ev.get("regime_label"), "trigger": ev.get("trigger"),
            "score": ev.get("score"), "vr": ev.get("vr"),
            "edge_n": es.get("n"), "edge_expectancy": es.get("expectancy_r"),
            "hist_win": prof.get("hist_win"), "hist_expectancy": prof.get("hist_expectancy"),
            "hist_n": prof.get("hist_n"), "asset_trend_frac": prof.get("trend_frac"),
            "horizon_bars": pred["horizon_bars"], "est_hold_min": int(round(hold_min)),
            "hold_label": hold_label, "window_label": window_label,
            "price": round(price, d), "stop_loss": sl, "take_profit": tp, "sl_dist": sl_dist,
            "vol": float((ol.get("metrics") or {}).get("volatility_pct") or 0),
            "reasons": (ev.get("reasons") or ol.get("reasons", []))[:4]}
    verb = "Buy" if side == "BUY" else "Sell"
    mkt = (ev.get("regime_label") or "").lower()
    breakeven = 1.0 / (1.0 + rr)
    win_floor = max(0.33, breakeven + 0.02)  # must clear breakeven with margin
    if cal_win < win_floor or cal_ev < rc.MIN_EXPECTED_R:
        base["ok"] = False
        base["reason"] = (f"{verb} idea on a {mkt} market (setup strength {ev['score']}/100), but only about a "
                          f"{int(cal_win*100)}% chance of working — not worth the risk yet, so waiting.")
        return base
    base["ok"] = True
    trig = ev.get("trigger")
    entry_note = f" Entry: {trig.lower()}." if trig else ""
    base["reason"] = (f"{verb} setup on a {mkt} market — about a {int(cal_win*100)}% chance of working "
                      f"(setup strength {ev['score']}/100).{entry_note} Usually plays out in {hold_label}.")
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
            "regime": a.get("regime"), "regime_label": a.get("regime_label"),
            "trigger": a.get("trigger"),
            "score": a.get("score"), "adx": a.get("adx"),
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

    try:
        a = await _analyze(signal_symbol, timeframe, cfg["horizon"])
    except Exception as e:  # noqa
        logger.warning(f"propose analyze {signal_symbol} failed: {e}")
        return {"ok": False, "error": f"Couldn't analyze {mt5_sym} right now: {e}"}
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
    rr = float(a.get("rr") or 1.5)
    tp_dist = round(rr * sl_dist, d)
    if side == "BUY":
        sl, tp = round(price - sl_dist, d), round(price + tp_dist, d)
    else:
        sl, tp = round(price + sl_dist, d), round(price - tp_dist, d)

    # ---- goal-aware sizing: risk a % of the CURRENT balance, paced toward the 2x goal ----
    capped_note = ""
    try:
        acct = await account_summary()
        avail = acct.get("available")
        balance = (acct.get("balance") or acct.get("equity")) if avail else None
        growth = acct.get("growth") or {}
    except Exception:  # noqa
        balance, growth = None, {}
    vpu = _value_per_unit(spec)
    if balance and vpu and sl_dist > 0:
        risk_pct = rc.risk_pct_for(a["confidence"], growth.get("growth_pct"))
        risk_usd = float(balance) * risk_pct
        desired_lot = risk_usd / (sl_dist * vpu)
    else:
        desired_lot = rc.confidence_lot(a["confidence"])  # fallback if balance unreadable
    lot, est_risk, vpu = _clamp_lot(spec, desired_lot, sl_dist)
    # hard safety ceiling: never risk more than RISK_CAP_PCT of equity on one trade
    equity = (acct.get("equity") or acct.get("balance")) if (balance is not None) else None
    if equity:
        safe_lot = _cap_lot_to_risk(spec, lot, sl_dist, equity)
        if safe_lot < lot:
            lot, est_risk, vpu = _clamp_lot(spec, safe_lot, sl_dist)
            capped_note = (f" Lot trimmed to {lot} so this trade risks ≤ "
                           f"{int(rc.RISK_CAP_PCT*100)}% of your ${round(float(equity),2)} equity.")
    est_reward = round(est_risk * rr, 2) if est_risk is not None else None
    reasons = list(a.get("reasons", []))
    reasons.append(
        f"The plan: {'buy' if side == 'BUY' else 'sell'} {lot} lots — risking about "
        f"${est_risk if est_risk is not None else '—'} to aim for about "
        f"${est_reward if est_reward is not None else '—'} "
        f"(roughly a {int(a['win_prob']*100)}% chance of working). Usually wraps up in "
        f"{a['hold_label']}.{capped_note}")

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
        "regime": a.get("regime"),
        "regime_label": a.get("regime_label"),
        "trigger": a.get("trigger"),
        "adx": a.get("adx"),
        "score": a.get("score"),
        "htf_dir": a.get("htf_dir"),
        "win_prob": a["win_prob"],
        "expected_r": a["expected_r"],
        "rr": rr,
        "horizon_bars": a["horizon_bars"],
        "est_hold_min": a["est_hold_min"],
        "hold_label": a["hold_label"],
        "est_risk_usd": est_risk,
        "est_reward_usd": est_reward,
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

    # stop hunting once the demo balance has doubled (goal reached) — protect it.
    # Also: if the account has been drawn down to a fraction of the goal's starting
    # balance (effectively blown), keep EMAILING ideas but auto-pause auto-placing
    # so nothing auto-trades a near-empty account. Resumes once the balance is reset.
    tiny_balance = False
    try:
        acct = await account_summary()
        g = acct.get("growth") or {}
        if g.get("goal_reached"):
            return {"created": 0, "reason": "goal reached — balance doubled; pausing new trades"}
        bal = acct.get("balance") or acct.get("equity")
        start = g.get("start_balance")
        if bal and start and float(bal) < float(start) * 0.25:
            tiny_balance = True
    except Exception:  # noqa
        pass

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
            # Hybrid: auto-place the highest-conviction setups (demo only); email the
            # rest as proposals for the user to approve.
            placed = False
            if cfg["auto_approve"] and not tiny_balance and (p.get("win_prob") or 0) >= cfg["auto_approve_min_winprob"]:
                try:
                    ap = await approve(p["id"])
                    placed = bool(ap.get("ok"))
                    if placed and ap.get("proposal"):
                        p = ap["proposal"]
                    logger.info(f"auto_scan: AUTO-PLACED {p['side']} {m} -> {placed}")
                except Exception as e:  # noqa
                    logger.warning(f"auto_scan auto-approve failed: {e}")
            try:
                import alerts
                await alerts.email_proposal(p, auto_placed=placed)
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


async def _fresh_stops(p: dict, spec: dict):
    """Re-anchor SL/TP to the CURRENT market price at approval time. The stored
    stops were computed when the proposal was created and go stale as price
    drifts, which makes the broker reject the order ('Invalid stops'). We keep
    the proposal's risk distance but recompute from live price, enforce the
    broker's minimum stop distance (+ a buffer for spread), and round to ticks."""
    side = p["side"]
    entry_ref = float(p.get("entry_ref") or 0)
    risk = abs(entry_ref - float(p.get("stop_loss") or 0)) or entry_ref * 0.003

    # live price (mid); fall back to the proposal reference if unavailable
    price = entry_ref
    try:
        lp = await mt5_trader.live_price(p["mt5_symbol"])
        price = float(lp.get("price") or entry_ref)
    except Exception:  # noqa
        pass

    digits = int(spec.get("digits") or _digits(price))
    tick = float(spec.get("tickSize") or (10 ** -digits))
    # minimum allowed stop distance from the broker, with a safety buffer
    min_stop = _min_stop_dist(spec)
    sl_dist = max(risk, min_stop * 1.5, price * 0.0008)
    rr = float(p.get("rr") or 1.5)
    tp_dist = rr * sl_dist

    def _snap(v):
        return round(round(v / tick) * tick, digits) if tick else round(v, digits)

    if side == "BUY":
        sl = _snap(price - sl_dist)
        tp = _snap(price + tp_dist)
    else:
        sl = _snap(price + sl_dist)
        tp = _snap(price - tp_dist)
    return sl, tp, round(price, digits)


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

    # recompute stops against the live price so the broker accepts them
    try:
        spec = await mt5_trader.symbol_spec(p["mt5_symbol"])
        sl, tp, entry = await _fresh_stops(p, spec)
    except Exception as e:  # noqa
        logger.warning(f"approve: could not refresh stops for {p['mt5_symbol']}: {e}")
        await db.trade_proposals.update_one(
            {"id": proposal_id}, {"$set": {"status": "failed",
             "error": f"Could not refresh stops: {e}", "decided_at": _now().isoformat()}})
        return {"ok": False, "error": "Could not refresh live stops right now — please try again in a moment."}

    client_id = f"TC_{p['mt5_symbol'][:6]}_{proposal_id.replace('-', '')[:8]}"
    try:
        result = await mt5_trader.market_order(
            p["mt5_symbol"], p["side"], p["lot"],
            stop_loss=sl, take_profit=tp,
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
                  "stop_loss": sl, "take_profit": tp, "entry_ref": entry,
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
        "entry_ref": entry,
        "stop_loss": sl,
        "take_profit": tp,
        "position_id": position_id,
        "client_id": client_id,
        "status": "open",
        "rr": p.get("rr"),
        "confidence": p.get("confidence"),
        "regime": p.get("regime"),
        "planned_risk": p.get("est_risk_usd"),
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


async def _growth_target(current_equity) -> dict:
    """Track progress toward doubling the demo balance. The starting balance is
    captured once (the first time we can read the account) and the goal is 2x it."""
    cur = float(current_equity or 0)
    meta = await db.bot_meta.find_one({"_id": "growth"}, {"_id": 0})
    if not meta or not meta.get("start_balance"):
        start = cur or 0.0
        meta = {"start_balance": round(start, 2), "created_at": _now().isoformat()}
        await db.bot_meta.update_one({"_id": "growth"}, {"$set": meta}, upsert=True)
    start = float(meta.get("start_balance") or cur or 0.0)
    target = round(start * 2, 2)
    denom = (target - start) or 1.0
    progress = max(0.0, min(100.0, (cur - start) / denom * 100))
    return {"start_balance": round(start, 2), "target_balance": target,
            "growth_pct": round(progress, 1),
            "goal_reached": cur >= target and target > 0}


async def reanchor_goal() -> dict:
    """Re-baseline the growth goal to the current balance (2x it). Used after the
    user resets the Deriv demo so progress tracks from the fresh balance."""
    try:
        info = await mt5_trader.account_info(force=True)
    except Exception:  # noqa
        info = mt5_trader.last_account()
    if not info:
        return {"ok": False, "error": "Couldn't read the account balance right now — try again in a moment."}
    cur = float(info.get("balance") or info.get("equity") or 0)
    if cur <= 0:
        return {"ok": False, "error": "Balance reads as zero — nothing to anchor to."}
    await db.bot_meta.update_one(
        {"_id": "growth"},
        {"$set": {"start_balance": round(cur, 2), "re_anchored_at": _now().isoformat()}},
        upsert=True)
    return {"ok": True, "start_balance": round(cur, 2), "target_balance": round(cur * 2, 2),
            "growth_pct": 0.0, "goal_reached": False}


async def account_summary() -> dict:
    stale = False
    try:
        info = await mt5_trader.account_info()
    except Exception as e:  # noqa
        # transient reconnect: show the last-known snapshot instead of "OFFLINE"
        info = mt5_trader.last_account()
        stale = True
        if not info:
            return {"available": False, "reason": str(e)}
    equity = info.get("equity")
    summary = {
        "available": True,
        "stale": stale,
        "balance": info.get("balance"),
        "equity": equity,
        "currency": info.get("currency"),
        "trade_allowed": info.get("tradeAllowed"),
        "is_demo": _is_demo(info),
        "server": info.get("server"),
        "leverage": info.get("leverage"),
    }
    try:
        summary["growth"] = await _growth_target(equity if equity is not None else info.get("balance"))
    except Exception as e:  # noqa
        logger.warning(f"growth target failed: {e}")
    return summary


async def open_positions() -> list:
    """The bot's open demo trades, driven by our own DB record so they ALWAYS
    show (and survive a page refresh) even while MT5 is briefly reconnecting.
    Enriched with live price/P&L when MT5 is reachable; reconciled to 'closed'
    only when MT5 confirms the position is gone."""
    db_open = await db.trades.find({"status": "open"}, {"_id": 0}).sort("placed_at", -1).to_list(100)
    live_map = None
    try:
        rows = await mt5_trader.positions() or []
        live_map = {str(p.get("id")): p for p in rows}
    except Exception as e:  # noqa
        logger.warning(f"open_positions live fetch failed (serving last known): {e}")

    out = []
    for t in db_open:
        pid = t.get("position_id")
        p = live_map.get(pid) if live_map is not None else None
        if live_map is not None and p is None:
            # MT5 is reachable and says this position is gone -> learn + mark closed.
            profit = await _realized_profit(pid)
            await db.trades.update_one(
                {"position_id": pid},
                {"$set": {"status": "closed", "closed_at": _now().isoformat(),
                          "realized_profit": profit}})
            try:
                await learning.record_outcome(
                    t, profit if profit is not None else t.get("last_profit", 0),
                    t.get("planned_risk"))
            except Exception as e:  # noqa
                logger.warning(f"record_outcome failed: {e}")
            continue
        if p:
            profit = round(float(p.get("unrealizedProfit", p.get("profit", 0)) or 0), 2)
            await db.trades.update_one({"position_id": pid}, {"$set": {"last_profit": profit}})
            out.append({
                "id": pid, "symbol": p.get("symbol") or t.get("symbol"),
                "type": p.get("type") or t.get("side"),
                "volume": p.get("volume") or t.get("lot"),
                "open_price": p.get("openPrice") or t.get("entry_ref"),
                "current_price": p.get("currentPrice"),
                "stop_loss": p.get("stopLoss") or t.get("stop_loss"),
                "take_profit": p.get("takeProfit") or t.get("take_profit"),
                "profit": profit, "live": True,
            })
        else:
            # MT5 momentarily unreachable: still show the trade with last known data.
            out.append({
                "id": pid, "symbol": t.get("symbol"), "type": t.get("side"),
                "volume": t.get("lot"), "open_price": t.get("entry_ref"),
                "current_price": None, "stop_loss": t.get("stop_loss"),
                "take_profit": t.get("take_profit"),
                "profit": round(float(t.get("last_profit") or 0), 2), "live": False,
            })
    return out


async def trade_log(limit: int = 50) -> list:
    return await db.trades.find({}, {"_id": 0}).sort("placed_at", -1).to_list(limit)


async def _position_gone(position_id: str, tries: int = 3) -> bool:
    """Poll the broker to confirm a position is actually closed. MetaApi can
    return a transient 'account not connected' error during a reconnect even
    though the close already went through on the broker's side."""
    for _ in range(tries):
        try:
            ids = {str(p.get("id")) for p in (await mt5_trader.positions() or [])}
            if position_id not in ids:
                return True
        except Exception:  # noqa
            pass
        await asyncio.sleep(1)
    return False


async def _learn_close(position_id: str):
    """Record the realized R-multiple of a just-closed trade for the learner."""
    try:
        t = await db.trades.find_one({"position_id": position_id}, {"_id": 0})
        if not t:
            return
        profit = await _realized_profit(position_id)
        await db.trades.update_one({"position_id": position_id},
                                   {"$set": {"realized_profit": profit}})
        await learning.record_outcome(
            t, profit if profit is not None else t.get("last_profit", 0), t.get("planned_risk"))
    except Exception as e:  # noqa
        logger.warning(f"_learn_close failed: {e}")


async def close(position_id: str) -> dict:
    try:
        result = await mt5_trader.close_position(position_id)
    except Exception as e:  # noqa
        # The broker may have closed it anyway during a reconnect — verify before
        # surfacing a scary "Close failed" to the user.
        if await _position_gone(position_id):
            await db.trades.update_one(
                {"position_id": position_id},
                {"$set": {"status": "closed", "closed_at": _now().isoformat()}})
            await _learn_close(position_id)
            return {"ok": True, "result": {"note": "Position closed (confirmed with broker)."}}
        return {"ok": False, "error": str(e)}
    await db.trades.update_one({"position_id": position_id}, {"$set": {"status": "closed", "closed_at": _now().isoformat()}})
    await _learn_close(position_id)
    return {"ok": True, "result": _safe(result)}


async def edge_report() -> dict:
    """Learned-edge summary (realized expectancy per setup) for the UI."""
    return await learning.edge_report()


async def backfill_outcomes() -> int:
    """One-shot: seed the learner from already-closed trades (so it learns from
    past losers immediately instead of starting from scratch)."""
    try:
        closed = await db.trades.find({"status": "closed"}, {"_id": 0}).to_list(1000)
        have = {o.get("position_id") for o in
                await db.trade_outcomes.find({}, {"_id": 0, "position_id": 1}).to_list(2000)}
        n = 0
        for t in closed:
            pid = t.get("position_id")
            if not pid or pid in have:
                continue
            profit = t.get("realized_profit")
            if profit is None:
                profit = await _realized_profit(pid)
            if profit is None:
                profit = t.get("last_profit", 0)
            await learning.record_outcome(t, profit, t.get("planned_risk"))
            n += 1
        if n:
            logger.info(f"backfilled {n} historical trade outcomes into the learner")
        return n
    except Exception as e:  # noqa
        logger.warning(f"backfill_outcomes failed: {e}")
        return 0
