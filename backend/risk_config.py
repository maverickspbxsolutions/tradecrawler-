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

# Forward Monte-Carlo prediction settings.
SIM_PATHS = 600
SIM_HORIZON_BARS = 24


def suggest_lot(signal_confidence=None, volatility_pct=None) -> float:
    """Legacy confidence/vol-based sizing (kept for reference). The engine now
    uses risk-based sizing from the stop-loss distance instead."""
    conf = 0.5 if signal_confidence is None else max(0.0, min(1.0, float(signal_confidence)))
    vol = 0.0 if volatility_pct is None else max(0.0, float(volatility_pct))
    vol_factor = 1.0 / (1.0 + vol / 2.0)
    lot = MAX_LOT * conf * vol_factor
    return round(max(MIN_LOT, min(MAX_LOT, lot)), 2)


def as_dict() -> dict:
    return {
        "trade_env": TRADE_ENV,
        "daily_loss_limit_usd": DAILY_LOSS_LIMIT_USD,
        "enforce_kill_switch": ENFORCE_KILL_SWITCH,
        "max_lot": MAX_LOT,
        "min_lot": MIN_LOT,
        "max_open_positions": MAX_OPEN_POSITIONS,
        "allowed_symbols": list(ALLOWED_SYMBOLS),
        "risk_per_trade_usd": RISK_PER_TRADE_USD,
        "min_confidence": MIN_CONFIDENCE,
        "min_win_prob": MIN_WIN_PROB,
        "dynamic_lot_sizing": True,
    }
