"""Deep Deriv/MT5 history + per-asset profiling.

- get_history(symbol, range): serves a range-appropriate candle set (auto
  timeframe), fetched as deep as Deriv provides and cached in MongoDB so it's
  instant after the first load. Powers the chart's 1D/1W/1M/1Y/MAX selector.
- get_profile(symbol): a long-run profile built from deep history — the asset's
  own volatility, trend base-rate and a BACKTEST of the live regime/confluence
  rules over its history (historical win-rate & expectancy per side). This is
  fed back into the live engine so sizing/thresholds adapt per asset and the
  odds start calibrated to what has actually worked on that asset.
"""
import time
import asyncio
import logging

import numpy as np
import pandas as pd

from db import db
import mt5_trader
import indicators as ind
import edge as edge_mod

logger = logging.getLogger("history")

# range -> (timeframe, bars to show)
RANGE_TF = {"1D": "5m", "1W": "1h", "1M": "1h", "1Y": "1d", "MAX": "1d"}
RANGE_BARS = {"1D": 300, "1W": 200, "1M": 760, "1Y": 420, "MAX": 4000}
_HIST_TTL = {"1m": 300, "5m": 600, "15m": 900, "1h": 3600, "4h": 7200, "1d": 21600}
_PROFILE_TTL = 86400  # rebuild an asset profile at most once a day


async def get_history(symbol: str, rng: str) -> dict:
    rng = (rng or "MAX").upper()
    tf = RANGE_TF.get(rng, "1d")
    bars = RANGE_BARS.get(rng, 420)
    msym = mt5_trader.mt5_symbol(symbol) or symbol
    key = f"{msym}:{tf}"
    ttl = _HIST_TTL.get(tf, 3600)

    doc = await db.history_cache.find_one({"_id": key}, {"_id": 0})
    candles = None
    if doc and time.time() - doc.get("ts", 0) < ttl and len(doc.get("candles", [])) >= min(bars, 150):
        candles = doc["candles"]
    elif mt5_trader.is_ready():
        want = max(bars, RANGE_BARS["MAX"] if rng == "MAX" else bars)
        try:
            candles = await mt5_trader.candles_deep(msym, tf, want)
            if candles:
                await db.history_cache.update_one(
                    {"_id": key},
                    {"$set": {"symbol": msym, "tf": tf, "candles": candles, "ts": time.time()}},
                    upsert=True)
        except Exception as e:  # noqa
            logger.warning(f"deep history {msym} {tf} failed: {e}")
            candles = doc["candles"] if doc else None
            mt5_trader.ensure_bg()
    else:
        candles = doc["candles"] if doc else None
        mt5_trader.ensure_bg()

    if not candles:
        # last resort: shallow (but still MT5) recent window on this timeframe
        try:
            import market_data as md
            shallow = await md.get_candles(symbol, tf, bars)
            if shallow:
                return {"symbol": symbol, "range": rng, "timeframe": tf, "available": True,
                        "candles": shallow, "oldest": shallow[0]["t"], "count": len(shallow),
                        "total_available": len(shallow), "has_more": False,
                        "partial": True, "source": "deriv-mt5"}
        except Exception:  # noqa
            pass
        return {"symbol": symbol, "range": rng, "timeframe": tf, "available": False,
                "candles": [], "reason": "Deep history is loading from Deriv — try again in a moment."}
    sliced = candles[-bars:]
    return {"symbol": symbol, "range": rng, "timeframe": tf, "available": True,
            "candles": sliced, "oldest": sliced[0]["t"], "count": len(sliced),
            "total_available": len(candles), "has_more": len(candles) > len(sliced),
            "source": "deriv-mt5"}


# ---------------- per-asset profile + backtest prior ----------------
def _slice(doc: dict, side: str | None) -> dict:
    if not doc:
        return {}
    out = {k: doc.get(k) for k in ("vol_pct", "atr_pct", "trend_frac", "vr_longrun",
                                   "n_backtest", "overall_win", "overall_expectancy",
                                   "bars", "timeframe", "updated_at")}
    bt = doc.get("by_side", {})
    if side and side in bt:
        out["hist_win"] = bt[side].get("win")
        out["hist_expectancy"] = bt[side].get("expectancy")
        out["hist_n"] = bt[side].get("n")
    return out


async def get_profile(symbol: str, side: str | None = None) -> dict:
    msym = mt5_trader.mt5_symbol(symbol) or symbol
    doc = await db.asset_profiles.find_one({"_id": msym}, {"_id": 0})
    if doc and time.time() - doc.get("ts", 0) < _PROFILE_TTL:
        return _slice(doc, side)
    asyncio.create_task(build_profile(symbol))  # refresh in background
    return _slice(doc, side)  # stale (or {}) meanwhile


def _backtest(candles: list, horizon: int = 20, rr: float = 2.0, step: int = 4) -> dict:
    """Walk the live regime/confluence rules across history and tally R-multiples."""
    if len(candles) < 200:
        return {"n": 0, "by_side": {}}
    closes = [c["c"] for c in candles]
    highs = [c["h"] for c in candles]
    lows = [c["l"] for c in candles]
    atr_series = ind.atr(pd.Series(highs), pd.Series(lows), pd.Series(closes), 14).values
    by = {"BUY": [], "SELL": []}
    start = max(100, 60)
    end = len(candles) - horizon - 1
    for i in range(start, end, step):
        ev = edge_mod.assess(candles[: i + 1])
        if not ev.get("tradeable"):
            continue
        side = ev["side"]
        entry = closes[i]
        a = atr_series[i] if atr_series[i] == atr_series[i] else entry * 0.004
        sl_dist = max(1.3 * a, entry * 0.0012)
        if side == "BUY":
            sl, tp = entry - sl_dist, entry + rr * sl_dist
        else:
            sl, tp = entry + sl_dist, entry - rr * sl_dist
        r = None
        for j in range(i + 1, i + 1 + horizon):
            hi, lo = highs[j], lows[j]
            if side == "BUY":
                if lo <= sl:
                    r = -1.0; break
                if hi >= tp:
                    r = rr; break
            else:
                if hi >= sl:
                    r = -1.0; break
                if lo <= tp:
                    r = rr; break
        if r is None:  # settle at mark-to-market in R
            exitp = closes[i + horizon]
            r = ((exitp - entry) if side == "BUY" else (entry - exitp)) / sl_dist
        by[side].append(r)

    def summ(rs):
        if not rs:
            return {"n": 0, "win": None, "expectancy": None}
        wins = [x for x in rs if x > 0]
        return {"n": len(rs), "win": round(len(wins) / len(rs), 3),
                "expectancy": round(float(np.mean(rs)), 3)}

    out = {"BUY": summ(by["BUY"]), "SELL": summ(by["SELL"])}
    allr = by["BUY"] + by["SELL"]
    out["_all"] = summ(allr)
    return {"n": len(allr), "by_side": out}


_building: set = set()


async def build_profile(symbol: str) -> dict:
    msym = mt5_trader.mt5_symbol(symbol) or symbol
    if msym in _building or not mt5_trader.is_ready():
        return {}
    _building.add(msym)
    try:
        hourly = await mt5_trader.candles_deep(msym, "1h", 2500)
        daily = await mt5_trader.candles_deep(msym, "1d", 1500)
        if not hourly or len(hourly) < 200:
            return {}
        closes = pd.Series([c["c"] for c in hourly], dtype=float)
        highs = pd.Series([c["h"] for c in hourly], dtype=float)
        lows = pd.Series([c["l"] for c in hourly], dtype=float)
        price = float(closes.iloc[-1])
        atr_pct = float(ind.atr(highs, lows, closes, 14).iloc[-1] / price) if price else 0.0
        adx_s, _, _ = ind.adx(highs, lows, closes, 14)
        trend_frac = float((adx_s.dropna() >= edge_mod.ADX_TREND).mean()) if len(adx_s.dropna()) else 0.0
        if daily and len(daily) > 10:
            dret = np.diff(np.log([c["c"] for c in daily if c["c"] > 0]))
            vol_pct = float(np.std(dret) * 100)
        else:
            lret = np.diff(np.log(closes.values[closes.values > 0]))
            vol_pct = float(np.std(lret) * np.sqrt(24) * 100)
        lret = np.diff(np.log(closes.values[closes.values > 0]))
        vr = edge_mod._variance_ratio(lret[-300:] if len(lret) >= 300 else lret)

        bt = _backtest(hourly)
        doc = {
            "symbol": msym, "timeframe": "1h", "bars": len(hourly), "ts": time.time(),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "vol_pct": round(vol_pct, 3), "atr_pct": round(atr_pct, 5),
            "trend_frac": round(trend_frac, 3), "vr_longrun": round(vr, 3),
            "n_backtest": bt["n"],
            "overall_win": bt["by_side"].get("_all", {}).get("win"),
            "overall_expectancy": bt["by_side"].get("_all", {}).get("expectancy"),
            "by_side": {k: bt["by_side"][k] for k in ("BUY", "SELL") if k in bt["by_side"]},
        }
        await db.asset_profiles.update_one({"_id": msym}, {"$set": doc}, upsert=True)
        logger.info(f"profiled {msym}: {bt['n']} backtest trades, "
                    f"win {doc['overall_win']}, exp {doc['overall_expectancy']}R, "
                    f"trend_frac {doc['trend_frac']}")
        return _slice(doc, None)
    except Exception as e:  # noqa
        logger.warning(f"build_profile {msym} failed: {e}")
        return {}
    finally:
        _building.discard(msym)
