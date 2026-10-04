"""Demo-only trade risk configuration.

These are the agreed hard caps for the upcoming Deriv MT5 (demo) trade-execution
engine. They are recorded here as the single source of truth so the execution /
approval flow (built later) reads from one place. No orders are placed yet.
"""

# Trading environment lock. Must stay "demo" until a separate explicit go-live.
TRADE_ENV = "demo"

# Kill switch: stop opening new trades once this much is lost in a day (USD).
DAILY_LOSS_LIMIT_USD = 50.0

# Hard ceiling on lot size the bot may ever use on a single trade.
MAX_LOT = 10.0

# Floor so dynamic sizing never rounds to zero.
MIN_LOT = 0.01

# Symbols the bot is allowed to trade (demo phase).
ALLOWED_SYMBOLS = ("XAUUSD", "BTCUSD")


def suggest_lot(signal_confidence: float | None = None,
                volatility_pct: float | None = None) -> float:
    """Pick a lot size between MIN_LOT and MAX_LOT based on market conditions.

    Higher conviction (confidence) scales size up; higher volatility scales it
    down so riskier conditions get smaller exposure. Result is clamped to the
    demo hard caps.
    """
    conf = 0.5 if signal_confidence is None else max(0.0, min(1.0, float(signal_confidence)))

    # Volatility dampener: 0% vol -> factor 1.0, high vol -> smaller factor.
    vol = 0.0 if volatility_pct is None else max(0.0, float(volatility_pct))
    vol_factor = 1.0 / (1.0 + vol / 2.0)  # 2% vol -> 0.5x, 4% -> 0.33x

    lot = MAX_LOT * conf * vol_factor
    lot = max(MIN_LOT, min(MAX_LOT, lot))
    return round(lot, 2)


def as_dict() -> dict:
    return {
        "trade_env": TRADE_ENV,
        "daily_loss_limit_usd": DAILY_LOSS_LIMIT_USD,
        "max_lot": MAX_LOT,
        "min_lot": MIN_LOT,
        "allowed_symbols": list(ALLOWED_SYMBOLS),
        "dynamic_lot_sizing": True,
    }
