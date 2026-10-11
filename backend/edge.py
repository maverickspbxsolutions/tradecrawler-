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
ADX_TREND = 22.0        # trend strong enough for the momentum regime
VR_PERSIST = 1.0        # variance ratio >= this => trending/persistent
VR_REVERT = 0.95        # variance ratio <= this => mean-reverting
RSI_FADE_LO = 33.0      # fade long when RSI <= this in a reversion regime
RSI_FADE_HI = 67.0      # fade short when RSI >= this in a reversion regime

# ---- entry timing (don't chase; get in near value) ----
EXT_MAX_ATR = 2.0       # price this many ATRs past the average = overextended -> wait
VALUE_ZONE_ATR = 1.2    # within this many ATRs of the average = fine to trade with the trend
PULLBACK_ZONE_ATR = 0.8 # a recent bar within this many ATRs of the average = a real pullback
SWING_LOOKBACK = 5      # bars used to find the pullback/swing for a tight stop
SWING_BUF_ATR = 0.25    # place the stop this far beyond the swing


def _swing_stop_dist(high: pd.Series, low: pd.Series, price: float, up: bool, atr_v: float) -> float:
    """Distance from entry to just beyond the recent swing (tight, well-placed stop)."""
    lb = SWING_LOOKBACK
    if up:
        swing = float(low.iloc[-lb:].min())
        dist = (price - swing) + SWING_BUF_ATR * atr_v
    else:
        swing = float(high.iloc[-lb:].max())
        dist = (swing - price) + SWING_BUF_ATR * atr_v
    return max(dist, 0.0)


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


def _retest(high: pd.Series, low: pd.Series, close: pd.Series, atr_v: float):
    """Detect a breakout of a recent range followed by a successful RETEST of the
    broken level (old resistance -> new support, or old support -> new resistance).
    A held retest is a higher-probability entry. Returns (direction, level) or None."""
    if len(close) < 55 or atr_v <= 0:
        return None
    box_high = float(high.iloc[-50:-12].max())    # the range that existed BEFORE the breakout
    box_low = float(low.iloc[-50:-12].min())
    price = float(close.iloc[-1])
    prev = float(close.iloc[-2])
    tol = 0.75 * atr_v
    recent_high = float(high.iloc[-12:].max())     # the breakout push
    recent_low = float(low.iloc[-12:].min())
    pullback_low = float(low.iloc[-4:].min())      # the retest touch
    pullback_high = float(high.iloc[-4:].max())
    # Bullish: broke above the range, dipped back to retest the old ceiling, holding above it, turning up
    if (recent_high > box_high + 0.15 * atr_v and pullback_low <= box_high + tol
            and price >= box_high - 0.1 * atr_v and price > prev):
        return ("Up", box_high)
    # Bearish: broke below the range, popped back to retest the old floor, holding below it, turning down
    if (recent_low < box_low - 0.15 * atr_v and pullback_high >= box_low - tol
            and price <= box_low + 0.1 * atr_v and price < prev):
        return ("Down", box_low)
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
    atr_v = float(atr.iloc[-1]) if atr.iloc[-1] == atr.iloc[-1] else price * 0.004
    trigger, swing_dist = None, None
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

    # ---- consolidation & breakout-retest reads ----
    retest = _retest(high, low, close, atr_v)
    rng20 = float(high.iloc[-20:].max()) - float(low.iloc[-20:].min())
    tight_range = atr_v > 0 and rng20 <= 3.2 * atr_v
    bbw_squeeze = len(bbw) > 60 and float(bbw.iloc[-1]) <= float(bbw.iloc[-60:].quantile(0.30))
    consolidating = adx < 22.0 and (tight_range or bbw_squeeze)

    # ---- regime ----
    # A confirmed breakout-RETEST is the highest-quality read and takes priority.
    # A tight CONSOLIDATION means "wait for the breakout" — don't force a trade.
    # Otherwise: momentum (persistent trend) or reversion (anti-persistent range).
    if retest is not None:
        regime = "breakout"
    elif (adx >= ADX_TREND and vr >= VR_PERSIST) or (adx >= 30 and vr >= 0.97):
        regime = "momentum"
    elif consolidating:
        return {"tradeable": False, "regime": "consolidation", "regime_label": "Consolidating",
                "adx": round(adx),
                "reason": "Price is coiling in a tight range — buyers and sellers are balanced. "
                          "Waiting for a breakout and a retest of the level to confirm before entering."}
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
        # ---- entry timing: don't chase; enter on a pullback-resume or fresh breakout ----
        ext_series = (close - ema20) / atr.replace(0, np.nan)
        ext_dir = ext_series if up else -ext_series            # + = extended in the profit direction
        ext_now = float(ext_dir.iloc[-1]) if ext_dir.iloc[-1] == ext_dir.iloc[-1] else 0.0
        ext_min = float(ext_dir.iloc[-4:].min())
        c1, c2 = float(close.iloc[-1]), float(close.iloc[-2])
        resuming = (c1 > c2) if up else (c1 < c2)
        if ext_now > EXT_MAX_ATR:
            return {"tradeable": False, "regime": regime, "regime_label": regime_label, "adx": round(adx),
                    "direction": direction, "side": "BUY" if up else "SELL", "score": score,
                    "reason": "The trend has run too far from its average — waiting for a pullback to get in at a better price."}
        pullback_resume = ext_min <= PULLBACK_ZONE_ATR and resuming
        breakout = squeeze_release and resuming
        in_value = ext_now <= VALUE_ZONE_ATR          # not extended -> fine to join the trend
        if pullback_resume:
            trigger = "Pulled back to value, turning back " + ("up" if up else "down")
            score += 14
            reasons.append("Price dipped back toward its average and is turning back "
                           + ("up" if up else "down") + " — a good spot to get in.")
        elif breakout:
            trigger = "Fresh breakout after a quiet stretch"
            score += 10
            reasons.append("Price just broke out after a calm stretch — getting in early on the move.")
        elif in_value:
            trigger = "Trading with the trend, near fair value"
            score += 6
            reasons.append("Price is still near its average, so we're joining the trend without chasing.")
        else:
            return {"tradeable": False, "regime": regime, "regime_label": regime_label, "adx": round(adx),
                    "direction": direction, "side": "BUY" if up else "SELL", "score": score,
                    "reason": "The trend's a bit extended here — waiting for a small pullback to get in at a better price."}
        swing_dist = _swing_stop_dist(high, low, price, up, atr_v)
    elif regime == "breakout":
        regime_label = "Breakout retest"
        rdir, level = retest
        direction = rdir
        up = direction == "Up"
        score += 40
        reasons.append(
            f"Price broke {'above a resistance' if up else 'below a support'} level, came back to "
            f"retest it, and the level is holding — that confirms the breakout and marks a "
            f"higher-probability entry.")
        h = float(hist.iloc[-1])
        if (up and h > 0) or (not up and h < 0):
            score += 10
            reasons.append("Momentum is pushing the same way as the breakout.")
        if (up and e20 >= e50) or (not up and e20 <= e50):
            score += 8
            reasons.append("The broader trend is aligned with the breakout.")
        if (up and r < 72) or (not up and r > 28):
            score += 6
        trigger = f"Breakout retest holding near {round(level, 6)}"
        # tight stop just beyond the retested level — if it fails, the breakout is void
        buf = SWING_BUF_ATR * atr_v
        swing_dist = (price - (level - buf)) if up else ((level + buf) - price)
        swing_dist = max(swing_dist, 0.4 * atr_v)
    else:  # reversion — range-bound — buy dips / sell spikes back toward the average
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
        # ---- entry timing: wait for the bounce to actually start (don't catch a falling knife) ----
        o1 = float(df["o"].astype(float).iloc[-1]) if "o" in df else float(close.iloc[-2])
        c1, c2 = float(close.iloc[-1]), float(close.iloc[-2])
        turning = ((c1 > c2) or (c1 > o1)) if up else ((c1 < c2) or (c1 < o1))
        if not turning:
            return {"tradeable": False, "regime": regime, "regime_label": regime_label, "adx": round(adx),
                    "direction": direction, "side": "BUY" if up else "SELL", "score": score,
                    "reason": "Price is stretched, but the bounce hasn't started yet — waiting for it to actually turn."}
        trigger = "Bounce is starting"
        score += 10; reasons.append("The bounce is starting — getting in just as it turns.")
        swing_dist = _swing_stop_dist(high, low, price, up, atr_v)

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
        "trigger": trigger,
        "swing_stop_dist": round(swing_dist, 8) if swing_dist else None,
        "reasons": reasons,
        "reason": (f"{'Buy' if buy else 'Sell'} setup on a {regime_label.lower()} market · setup strength {score}/100"
                   if tradeable
                   else f"{'Buy' if buy else 'Sell'} idea on a {regime_label.lower()} market, but it's only "
                        f"{score}/100 strong (needs {MIN_SCORE}) — waiting for a better one."),
    }
