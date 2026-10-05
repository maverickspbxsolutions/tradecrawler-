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
        return {"tradeable": False, "regime": "chop", "adx": round(adx),
                "reason": f"Unclear regime (ADX {adx:.0f}, VR {vr:.2f}) — no edge, standing aside."}

    reasons, score = [], 0
    if regime == "momentum":
        direction = "Up" if (e20 > e50 and price > e20) else "Down" if (e20 < e50 and price < e20) else None
        if not direction:
            return {"tradeable": False, "regime": regime, "adx": round(adx),
                    "reason": "Momentum regime but price between the moving averages — waiting for a clean side."}
        up = direction == "Up"
        # trend structure (25)
        score += 25; reasons.append(f"Momentum regime: ADX {adx:.0f}, price {'above' if up else 'below'} EMA20/50.")
        # slope (10)
        slope = (e20 - e20_prev)
        if (up and slope > 0) or (not up and slope < 0):
            score += 10; reasons.append("EMA20 slope confirms the trend direction.")
        # momentum trigger MACD (15)
        h = float(hist.iloc[-1])
        if (up and h > 0) or (not up and h < 0):
            score += 15; reasons.append("MACD momentum agrees.")
        # RSI not stretched against us (10)
        if (up and r < 72) or (not up and r > 28):
            score += 10
        # DI agreement (10)
        if (up and float(pdi.iloc[-1]) > float(ndi.iloc[-1])) or (not up and float(ndi.iloc[-1]) > float(pdi.iloc[-1])):
            score += 10; reasons.append("Directional index (+DI/-DI) confirms.")
    else:  # reversion — fade the stretched extreme back toward the mean
        if r <= RSI_FADE_LO:
            direction = "Up"
        elif r >= RSI_FADE_HI:
            direction = "Down"
        else:
            return {"tradeable": False, "regime": regime, "adx": round(adx),
                    "reason": f"Mean-reversion regime but RSI {r:.0f} isn't stretched enough — no fade setup."}
        up = direction == "Up"
        score += 25; reasons.append(f"Mean-reversion regime (ADX {adx:.0f}, VR {vr:.2f}); fading RSI {r:.0f}.")
        div = _rsi_divergence(close, rsi)
        if (up and div == "bull") or (not up and div == "bear"):
            score += 20; reasons.append(f"{'Bullish' if up else 'Bearish'} RSI divergence confirms exhaustion.")
        # price at Bollinger extreme
        if len(ma20) and sd20.iloc[-1] == sd20.iloc[-1]:
            band = 2 * float(sd20.iloc[-1])
            if (up and price <= float(ma20.iloc[-1]) - band * 0.9) or (not up and price >= float(ma20.iloc[-1]) + band * 0.9):
                score += 10; reasons.append("Price is at the outer Bollinger band — stretched.")

    # shared confluence factors
    if htf_dir and ((direction == "Up" and htf_dir != "Down") or (direction == "Down" and htf_dir != "Up")):
        score += 10; reasons.append(f"Higher timeframe ({htf_dir}) is aligned.")
    elif htf_dir and ((direction == "Up" and htf_dir == "Down") or (direction == "Down" and htf_dir == "Up")):
        score -= 15; reasons.append(f"Higher timeframe ({htf_dir}) disagrees — penalised.")
    if vol_ratio >= 1.3:
        score += 10; reasons.append(f"Volume expanding ({vol_ratio:.1f}x average).")
    if squeeze_release:
        score += 10; reasons.append("Volatility squeeze releasing — expansion underway.")

    score = int(max(0, min(100, score)))
    tradeable = score >= MIN_SCORE
    return {
        "tradeable": tradeable, "regime": regime, "direction": direction,
        "side": "BUY" if direction == "Up" else "SELL",
        "score": score, "confidence": int(min(92, 50 + score * 0.45)),
        "adx": round(adx), "vr": round(vr, 2), "rsi": round(r, 1),
        "atr": float(atr.iloc[-1]) if atr.iloc[-1] == atr.iloc[-1] else price * 0.004,
        "vol_ratio": round(vol_ratio, 2), "squeeze_release": squeeze_release,
        "reasons": reasons,
        "reason": (f"{regime.title()} {('BUY' if direction=='Up' else 'SELL')} · confluence {score}/100"
                   if tradeable else f"{regime.title()} setup only scored {score}/100 (need {MIN_SCORE}). Waiting."),
    }
