"""Demo-only trade risk configuration.

Single source of truth for the Deriv MT5 (demo) trade-execution engine's hard
caps and the conviction/sizing thresholds. No orders are ever placed automatically
— every trade needs explicit user approval.
"""

# Trading environment lock. Must stay "demo" until a separate explicit go-live.
TRADE_ENV = "demo"

# Kill switch: stop opening new trades once this much is lost in a day (USD).
DAILY_LOSS_LIMIT_USD = 50.0

# The daily-loss kill switch is a LIVE-money guard. On demo it is tracking-only
# (we still show the daily P&L) and never blocks placement.
ENFORCE_KILL_SWITCH = TRADE_ENV != "demo"

# Hard ceiling on lot size the bot may ever use on a single trade.
MAX_LOT = 10.0

# Floor so dynamic sizing never rounds to zero.
MIN_LOT = 0.01

# Realistic, repeatable position sizes the user can copy on ANY account — sized by
# confidence, NOT by account balance. The caller clamps to the symbol's own volume
# rules and the hard MAX_LOT ceiling.
BASE_LOT = 2.0

# Standard lot ladder (balance-INDEPENDENT): 0.10 lots at the minimum tradeable
# confidence, scaling linearly up to 0.50 lots at full confidence. Same numbers
# whether the bot places the trade on demo or the user copies it on their own account.
LADDER_LOT_MIN = 0.10
LADDER_LOT_MAX = 0.50

# Max number of the bot's own trades open at the same time (across any symbols).
MAX_OPEN_POSITIONS = 3

# Symbols the bot is allowed to trade (demo phase).
ALLOWED_SYMBOLS = ("XAUUSD", "BTCUSD")

# ---- conviction + sizing thresholds (anti "willy-nilly") ----
# Each trade is sized so its stop-loss risks at most this much, keeping all
# concurrent trades comfortably under the daily limit.
RISK_PER_TRADE_USD = round(DAILY_LOSS_LIMIT_USD / (MAX_OPEN_POSITIONS + 1), 2)  # 12.5

# Only propose when the outlook is this confident AND the forward simulation is favourable.
MIN_CONFIDENCE = 58
MIN_WIN_PROB = 0.45          # breakeven for a 1.5 reward:risk is ~0.40
MIN_EXPECTED_R = 0.05        # expected value must beat 0.05R

# Growth guard: however big the sized lot is, never risk more than this fraction
# of equity on a single trade.
RISK_CAP_PCT = 0.20

# ---- goal-aware money management (risk a % of the CURRENT balance per trade) ----
# Size grows as the balance grows and shrinks on drawdown. Confidence scales the
# risk within [MIN..MAX]; risk is PACED DOWN as the balance nears the 2x goal to
# protect gains. NOT martingale — losses never increase the next trade's risk.
RISK_PCT_MIN = 0.01          # 1% of balance at the minimum tradeable confidence
RISK_PCT_MAX = 0.05          # 5% of balance at full confidence


def risk_pct_for(confidence, growth_pct=None) -> float:
    """Fraction of current balance to risk on one trade, from confidence, paced
    down as progress toward the 2x goal rises."""
    try:
        conf = float(confidence)
    except (TypeError, ValueError):
        conf = MIN_CONFIDENCE
    frac = (conf - MIN_CONFIDENCE) / max(1.0, 100.0 - MIN_CONFIDENCE)
    frac = max(0.0, min(1.0, frac))
    pct = RISK_PCT_MIN + frac * (RISK_PCT_MAX - RISK_PCT_MIN)
    g = max(0.0, min(100.0, float(growth_pct or 0)))   # protect gains near the goal
    if g >= 80:
        pct *= 0.4
    elif g >= 60:
        pct *= 0.6
    elif g >= 40:
        pct *= 0.8
    return round(max(RISK_PCT_MIN * 0.3, min(pct, RISK_CAP_PCT)), 4)

# Forward Monte-Carlo prediction settings. Tuned for SHORT intraday day-trading:
# 15-minute bars with a 12-bar (3 hour) HARD max horizon. Typical holds target
# ~30min-2h30m; a trade only earns the full 3h when it's an exceptional setup.
TRADE_TIMEFRAME = "15m"
SIM_PATHS = 600
SIM_HORIZON_BARS = 12            # 12 x 15m = 3h hard ceiling

# ---- hold-time shaping (keep trades short) ----
MAX_HOLD_HOURS = 3.0             # absolute longest hold the bot will ever target
SOFT_HOLD_HOURS = 2.5            # beyond this, only EXCEPTIONAL setups are allowed
LONG_HOLD_MIN_SCORE = 80         # a 2h30m-3h trade must be this strong (0-100)
LONG_HOLD_MIN_EV = 0.30          # ...and carry at least this expectancy (in R)


def suggest_lot(signal_confidence=None, volatility_pct=None) -> float:
    """Legacy confidence/vol-based sizing (kept for reference). The engine now
    uses confidence_lot() for sizing instead."""
    conf = 0.5 if signal_confidence is None else max(0.0, min(1.0, float(signal_confidence)))
    vol = 0.0 if volatility_pct is None else max(0.0, float(volatility_pct))
    vol_factor = 1.0 / (1.0 + vol / 2.0)
    lot = MAX_LOT * conf * vol_factor
    return round(max(MIN_LOT, min(MAX_LOT, lot)), 2)


def confidence_lot(confidence) -> float:
    """Realistic lot sizing: BASE_LOT (2.0) at the minimum tradeable confidence,
    scaling linearly up to MAX_LOT (10.0) at 100% confidence. The caller then
    clamps the result to the symbol's own min/max/step volume rules."""
    try:
        conf = float(confidence)
    except (TypeError, ValueError):
        conf = MIN_CONFIDENCE
    span = max(1.0, 100.0 - MIN_CONFIDENCE)
    frac = (conf - MIN_CONFIDENCE) / span
    frac = max(0.0, min(1.0, frac))
    lot = BASE_LOT + frac * (MAX_LOT - BASE_LOT)
    return round(max(BASE_LOT, min(MAX_LOT, lot)), 2)


def ladder_lot(confidence) -> float:
    """Standard, balance-INDEPENDENT lot ladder: LADDER_LOT_MIN (0.10) at the
    minimum tradeable confidence, scaling linearly to LADDER_LOT_MAX (0.50) at
    full confidence. Realistic sizes the user can copy on any account; the caller
    clamps to the symbol's volume step/min/max."""
    try:
        conf = float(confidence)
    except (TypeError, ValueError):
        conf = MIN_CONFIDENCE
    span = max(1.0, 100.0 - MIN_CONFIDENCE)
    frac = max(0.0, min(1.0, (conf - MIN_CONFIDENCE) / span))
    lot = LADDER_LOT_MIN + frac * (LADDER_LOT_MAX - LADDER_LOT_MIN)
    return round(max(LADDER_LOT_MIN, min(lot, LADDER_LOT_MAX)), 2)


def as_dict() -> dict:
    return {
        "trade_env": TRADE_ENV,
        "daily_loss_limit_usd": DAILY_LOSS_LIMIT_USD,
        "enforce_kill_switch": ENFORCE_KILL_SWITCH,
        "max_lot": MAX_LOT,
        "min_lot": MIN_LOT,
        "base_lot": BASE_LOT,
        "max_open_positions": MAX_OPEN_POSITIONS,
        "allowed_symbols": list(ALLOWED_SYMBOLS),
        "risk_per_trade_usd": RISK_PER_TRADE_USD,
        "min_confidence": MIN_CONFIDENCE,
        "min_win_prob": MIN_WIN_PROB,
        "risk_cap_pct": RISK_CAP_PCT,
        "dynamic_lot_sizing": True,
        "confidence_scaled_lots": True,
    }
