"""Market Outlook — a probabilistic directional read of CURRENT market conditions.
Combines trend structure, trend slope, RSI momentum, MACD momentum and recent drift
into a single bias (Up / Down / Neutral) + confidence. Not a guarantee, not advice."""
import pandas as pd
import indicators as ind

DISCLAIMER = ("Probabilistic read of current conditions — not a guaranteed prediction "
              "and not financial advice. No orders are placed.")


def _r(x, d=2):
    try:
        return round(float(x), d)
    except Exception:  # noqa
        return None


def compute_outlook(candles: list, symbol: str = "", timeframe: str = "") -> dict:
    if not candles or len(candles) < 60:
        return {"symbol": symbol, "timeframe": timeframe, "direction": "Neutral",
                "confidence": 0, "score": 0, "metrics": {}, "disclaimer": DISCLAIMER,
                "reasons": ["Not enough closed candles yet to read this market."]}

    df = pd.DataFrame(candles)
    c = df["c"].astype(float)
    ema20 = ind.ema(c, 20)
    ema50 = ind.ema(c, 50)
    rsi_s = ind.rsi(c, 14)
    _, _, hist = ind.macd(c)

    price = float(c.iloc[-1])
    e20 = float(ema20.iloc[-1])
    e50 = float(ema50.iloc[-1])
    e20_prev = float(ema20.iloc[-6])
    r = float(rsi_s.iloc[-1])
    h = float(hist.iloc[-1])
    h_prev = float(hist.iloc[-3])
    look = min(10, len(c) - 1)
    ret = (price / float(c.iloc[-1 - look]) - 1) * 100.0
    rets = c.pct_change().dropna()
    vol = float(rets.tail(20).std() * 100) if len(rets) >= 20 else float(rets.std() * 100)
    ref_vol = float(rets.tail(100).std() * 100) if len(rets) >= 100 else vol

    comps = []
    reasons = []

    # 1) Trend structure (EMA20 vs EMA50 vs price) — weight 0.30
    if e20 > e50 and price > e20:
        t = 1.0
        reasons.append(f"Uptrend: price sits above both moving averages (EMA20 {_r(e20)} > EMA50 {_r(e50)}).")
    elif e20 < e50 and price < e20:
        t = -1.0
        reasons.append(f"Downtrend: price sits below both moving averages (EMA20 {_r(e20)} < EMA50 {_r(e50)}).")
    elif e20 > e50:
        t = 0.4
        reasons.append("Mildly bullish: short average above long average, but price is hesitating.")
    else:
        t = -0.4
        reasons.append("Mildly bearish: short average below long average.")
    comps.append(0.30 * t)

    # 2) Trend slope — weight 0.15
    slope = ((e20 - e20_prev) / e20_prev * 100) if e20_prev else 0.0
    comps.append(0.15 * max(-1.0, min(1.0, slope / 0.5)))
    reasons.append(
        f"Trend is {'rising' if slope > 0.02 else 'falling' if slope < -0.02 else 'flat'} "
        f"({_r(slope, 2)}% over the last 5 bars).")

    # 3) RSI momentum — weight 0.20
    comps.append(0.20 * max(-1.0, min(1.0, (r - 50) / 25.0)))
    if r >= 70:
        reasons.append(f"RSI {_r(r, 1)} is overbought — strong but stretched; pullback risk rises.")
    elif r <= 30:
        reasons.append(f"RSI {_r(r, 1)} is oversold — weak but stretched; bounce risk rises.")
    else:
        reasons.append(f"RSI {_r(r, 1)} shows {'bullish' if r > 50 else 'bearish'} momentum.")

    # 4) MACD momentum — weight 0.20
    if h > 0 and h > h_prev:
        m = 1.0
        reasons.append("MACD momentum is positive and expanding — buyers gaining control.")
    elif h > 0:
        m = 0.6
        reasons.append("MACD momentum is positive — buyers in control.")
    elif h < 0 and h < h_prev:
        m = -1.0
        reasons.append("MACD momentum is negative and expanding — sellers gaining control.")
    else:
        m = -0.6
        reasons.append("MACD momentum is negative — sellers in control.")
    comps.append(0.20 * m)

    # 5) Recent drift — weight 0.15
    comps.append(0.15 * max(-1.0, min(1.0, ret / 2.0)))

    score = sum(comps)  # ~ -1 .. 1
    score100 = int(max(-100, min(100, round(score * 100))))

    signs = [1 if x > 0.02 else -1 if x < -0.02 else 0 for x in comps]
    active = [x for x in signs if x != 0]
    pos, neg = signs.count(1), signs.count(-1)
    agreement = (max(pos, neg) / len(active)) if active else 0.0

    direction = "Up" if score100 >= 12 else "Down" if score100 <= -12 else "Neutral"
    confidence = int(min(92, round(abs(score100) * 0.7 + agreement * 30)))
    if direction == "Neutral":
        confidence = min(confidence, 45)

    vol_label = "High" if vol > ref_vol * 1.4 else "Low" if vol < ref_vol * 0.6 else "Normal"
    reasons.append(
        f"Volatility is {vol_label.lower()} right now — "
        f"{'wider swings, trade smaller' if vol_label == 'High' else 'calm, quieter moves' if vol_label == 'Low' else 'typical conditions'}.")

    metrics = {
        "price": _r(price, 4 if price < 10 else 2),
        "rsi": _r(r, 1),
        "ema20": _r(e20, 4 if e20 < 10 else 2),
        "ema50": _r(e50, 4 if e50 < 10 else 2),
        "macd_hist": _r(h, 5 if abs(h) < 1 else 2),
        "trend_slope_pct": _r(slope, 2),
        "recent_change_pct": _r(ret, 2),
        "volatility_pct": _r(vol, 2),
        "volatility_label": vol_label,
    }
    return {"symbol": symbol, "timeframe": timeframe, "direction": direction,
            "confidence": confidence, "score": score100, "metrics": metrics,
            "reasons": reasons, "disclaimer": DISCLAIMER}
