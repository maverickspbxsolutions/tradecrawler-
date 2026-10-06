"""Regime-aware, confluence-scored trade edge.

Research-grounded (ADX + variance-ratio regime detection, multi-factor confluence
scoring, trade-with-trend in momentum / fade extremes in mean-reversion, volume
and squeeze confirmation, skip chop). Produces a single decision the trade engine
turns into a proposal — direction depends on the REGIME, and a 0-100 confluence
score must clear a threshold. Not advice; no guarantees."""
import numpy as np
import pandas as pd

import indicators as ind

MIN_SCORE = 60          # composite confluence score required to trade
ADX_TREND = 25.0        # strong trend (momentum regime)
ADX_CHOP = 18.0         # (legacy) kept for reference
VR_PERSIST = 1.05       # variance ratio > this => trending/persistent
VR_REVERT = 0.92        # variance ratio < this => mean-reverting
RSI_FADE_LO = 33.0      # fade long when RSI <= this in a reversion regime
RSI_FADE_HI = 67.0      # fade short when RSI >= this in a reversion regime


def _variance_ratio(logret: np.ndarray, k: int = 5) -> float:
    """VR>1 persistent (momentum), VR<1 anti-persistent (mean-reverting)."""
    if len(logret) < k * 6:
        return 1.0
    var1 = np.var(logret, ddof=1)
    if var1 <= 0:
        return 1.0
    agg = np.add.reduceat(logret, np.arange(0, len(logret) - len(logret) % k, k))
    vark = np.var(agg, ddof=1) / k
    return float(vark / var1)


def _rsi_divergence(close: pd.Series, rsi: pd.Series, lookback: int = 20):
    """Return 'bull', 'bear', or None from simple price/RSI divergence."""
    if len(close) < lookback + 2:
        return None
    c = close.iloc[-lookback:]
    r = rsi.iloc[-lookback:]
    half = lookback // 2
    p1, p2 = c.iloc[:half].min(), c.iloc[half:].min()
    r1, r2 = r.iloc[:half].min(), r.iloc[half:].min()
    if p2 < p1 and r2 > r1:
        return "bull"              # price lower low, RSI higher low
    hp1, hp2 = c.iloc[:half].max(), c.iloc[half:].max()
    hr1, hr2 = r.iloc[:half].max(), r.iloc[half:].max()
    if hp2 > hp1 and hr2 < hr1:
        return "bear"              # price higher high, RSI lower high
    return None


def assess(candles: list, htf_dir: str | None = None) -> dict:
    """Score the current setup and pick a regime-appropriate direction."""
    if not candles or len(candles) < 80:
        return {"tradeable": False, "reason": "Not enough history for an edge read."}
    df = pd.DataFrame(candles)
    close, high, low = df["c"].astype(float), df["h"].astype(float), df["l"].astype(float)
    vol = df["v"].astype(float) if "v" in df else pd.Series(np.ones(len(df)))

    ema20, ema50 = ind.ema(close, 20), ind.ema(close, 50)
    rsi = ind.rsi(close, 14)
    _, _, hist = ind.macd(close)
    atr = ind.atr(high, low, close, 14)
    adx_s, pdi, ndi = ind.adx(high, low, close, 14)

    price = float(close.iloc[-1])
    e20, e50 = float(ema20.iloc[-1]), float(ema50.iloc[-1])
    e20_prev = float(ema20.iloc[-6])
    adx = float(adx_s.iloc[-1]) if adx_s.iloc[-1] == adx_s.iloc[-1] else 0.0
    r = float(rsi.iloc[-1])
    logret = np.diff(np.log(close.values[close.values > 0]))
    vr = _variance_ratio(logret[-120:] if len(logret) >= 120 else logret)

    # Bollinger width / squeeze (std of last 20 vs its own recent percentile)
    ma20 = close.rolling(20).mean()
    sd20 = close.rolling(20).std()
    bbw = (sd20 * 4 / ma20).dropna()
    squeeze_release = False
    if len(bbw) > 30:
        w = float(bbw.iloc[-1])
        floor = float(bbw.iloc[-30:].quantile(0.3))
        prev = float(bbw.iloc[-4])
        squeeze_release = prev <= floor and w > prev  # was compressed, now expanding

    vol_ratio = 1.0
    if len(vol) > 20 and float(vol.iloc[-20:].mean()) > 0:
        vol_ratio = float(vol.iloc[-3:].mean()) / float(vol.iloc[-20:].mean())

    # ---- regime ----
    # momentum = strong trend + persistent (or a very strong trend even if VR is
    # neutral); reversion = anti-persistent and not strongly trending (no
    # dead-zone in the ADX 18-25 band); otherwise chop.
    if (adx >= ADX_TREND and vr >= VR_PERSIST) or (adx >= 35 and vr >= 0.98):
        regime = "momentum"
    elif vr <= VR_REVERT and adx < ADX_TREND:
        regime = "reversion"
    else:
        return {"tradeable": False, "regime": "chop", "regime_label": "Choppy", "adx": round(adx),
                "reason": "The market's choppy right now with no clear direction — waiting for a cleaner setup."}

    reasons, score = [], 0
    if regime == "momentum":
        regime_label = "Trending"
        direction = "Up" if (e20 > e50 and price > e20) else "Down" if (e20 < e50 and price < e20) else None
        if not direction:
            return {"tradeable": False, "regime": regime, "regime_label": regime_label, "adx": round(adx),
                    "reason": "The market's trending but price is stuck in the middle — waiting for it to pick a side."}
        up = direction == "Up"
        word = "rising" if up else "falling"
        score += 25; reasons.append(f"The market is {word} steadily (a clear trend).")
        slope = (e20 - e20_prev)
        if (up and slope > 0) or (not up and slope < 0):
            score += 10; reasons.append(f"The short-term average is still {word} — trend is healthy.")
        h = float(hist.iloc[-1])
        if (up and h > 0) or (not up and h < 0):
            score += 15; reasons.append("Momentum is pushing the same way.")
        if (up and r < 72) or (not up and r > 28):
            score += 10
        if (up and float(pdi.iloc[-1]) > float(ndi.iloc[-1])) or (not up and float(ndi.iloc[-1]) > float(pdi.iloc[-1])):
            score += 10; reasons.append("Buyers are in control." if up else "Sellers are in control.")
    else:  # range-bound — buy dips / sell spikes back toward the average
        regime_label = "Range-bound"
        mean = float(ma20.iloc[-1]) if ma20.iloc[-1] == ma20.iloc[-1] else price
        if price < mean and r < 50:
            direction = "Up"
        elif price > mean and r > 50:
            direction = "Down"
        else:
            return {"tradeable": False, "regime": regime, "regime_label": regime_label, "adx": round(adx),
                    "reason": "The market's bouncing in a range, but price is sitting near its average — no good bounce to trade yet."}
        up = direction == "Up"
        score += 20
        reasons.append(f"The market is range-bound and price has {'dropped below' if up else 'risen above'} "
                       f"its recent average — betting it bounces back {'up' if up else 'down'}.")
        stretch = abs(50 - r)
        if stretch >= 18:
            score += 28; reasons.append("Price has moved a long way from the average (very stretched).")
        elif stretch >= 11:
            score += 20; reasons.append("Price has moved well away from the average.")
        elif stretch >= 6:
            score += 12; reasons.append("Price has drifted away from the average.")
        div = _rsi_divergence(close, rsi)
        if (up and div == "bull") or (not up and div == "bear"):
            score += 15; reasons.append("Momentum is already starting to turn back — a bounce looks likely.")
        if len(ma20) and sd20.iloc[-1] == sd20.iloc[-1]:
            band = 2 * float(sd20.iloc[-1])
            if (up and price <= mean - band * 0.8) or (not up and price >= mean + band * 0.8):
                score += 12; reasons.append("Price is right at the edge of its normal range.")
            elif (up and price <= mean - band * 0.5) or (not up and price >= mean + band * 0.5):
                score += 6

    # extra confirmations
    if htf_dir and ((direction == "Up" and htf_dir != "Down") or (direction == "Down" and htf_dir != "Up")):
        score += 10; reasons.append("The bigger-picture trend agrees with this trade.")
    elif htf_dir and ((direction == "Up" and htf_dir == "Down") or (direction == "Down" and htf_dir == "Up")):
        score -= 15; reasons.append("The bigger-picture trend disagrees — counted against this trade.")
    if vol_ratio >= 1.3:
        score += 10; reasons.append("Trading activity is picking up (more people trading).")
    if squeeze_release:
        score += 10; reasons.append("The market just woke up after a quiet stretch — a bigger move may be starting.")

    score = int(max(0, min(100, score)))
    tradeable = score >= MIN_SCORE
    buy = direction == "Up"
    return {
        "tradeable": tradeable, "regime": regime, "regime_label": regime_label, "direction": direction,
        "side": "BUY" if buy else "SELL",
        "score": score, "confidence": int(min(92, 50 + score * 0.45)),
        "adx": round(adx), "vr": round(vr, 2), "rsi": round(r, 1),
        "atr": float(atr.iloc[-1]) if atr.iloc[-1] == atr.iloc[-1] else price * 0.004,
        "vol_ratio": round(vol_ratio, 2), "squeeze_release": squeeze_release,
        "reasons": reasons,
        "reason": (f"{'Buy' if buy else 'Sell'} setup on a {regime_label.lower()} market · setup strength {score}/100"
                   if tradeable
                   else f"{'Buy' if buy else 'Sell'} idea on a {regime_label.lower()} market, but it's only "
                        f"{score}/100 strong (needs {MIN_SCORE}) — waiting for a better one."),
    }
