"""Strategy models and signal evaluation. Composable rule-based strategies.
Long-only state machine: enter on entry signal when flat, exit on exit signal when long."""
from typing import List, Literal, Optional
from datetime import datetime, timezone
import uuid
import pandas as pd
from pydantic import BaseModel, Field

import indicators as ind


class Rule(BaseModel):
    indicator: Literal["rsi", "ma_cross", "macd", "volume"]
    params: dict = Field(default_factory=dict)


class Strategy(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    symbol: str = "BTCUSDT"
    timeframe: str = "1h"
    entry_rules: List[Rule] = Field(default_factory=list)
    exit_rules: List[Rule] = Field(default_factory=list)
    logic: Literal["AND", "OR"] = "AND"
    enabled: bool = True          # strategy is active (scanned by engine)
    live: bool = False            # alerts fire (requires gate_passed)
    gate_passed: bool = False     # beat benchmark + positive expectancy in backtest
    auto_evolve: bool = True      # adaptive engine may auto-apply improved params
    fees_pct: float = 0.1         # per side, percent
    slippage_pct: float = 0.05    # per fill, percent
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class StrategyCreate(BaseModel):
    name: str
    symbol: str = "BTCUSDT"
    timeframe: str = "1h"
    entry_rules: List[Rule] = Field(default_factory=list)
    exit_rules: List[Rule] = Field(default_factory=list)
    logic: Literal["AND", "OR"] = "AND"
    fees_pct: float = 0.1
    slippage_pct: float = 0.05


class StrategyUpdate(BaseModel):
    name: Optional[str] = None
    symbol: Optional[str] = None
    timeframe: Optional[str] = None
    entry_rules: Optional[List[Rule]] = None
    exit_rules: Optional[List[Rule]] = None
    logic: Optional[Literal["AND", "OR"]] = None
    enabled: Optional[bool] = None
    live: Optional[bool] = None
    auto_evolve: Optional[bool] = None
    fees_pct: Optional[float] = None
    slippage_pct: Optional[float] = None


def candles_to_df(candles: list) -> pd.DataFrame:
    df = pd.DataFrame(candles)
    for col in ("o", "h", "l", "c", "v"):
        df[col] = df[col].astype(float)
    return df


def _rule_series(df: pd.DataFrame, rule: dict) -> pd.Series:
    kind = rule["indicator"]
    p = rule.get("params", {}) or {}
    c = df["c"]

    if kind == "rsi":
        period = int(p.get("period", 14))
        value = float(p.get("value", 30))
        op = p.get("op", "below")
        r = ind.rsi(c, period)
        return (r < value) if op == "below" else (r > value)

    if kind == "ma_cross":
        fast_n = int(p.get("fast", 9))
        slow_n = int(p.get("slow", 21))
        ma_type = p.get("ma_type", "ema")
        direction = p.get("direction", "bull")
        fast = ind.ema(c, fast_n) if ma_type == "ema" else ind.sma(c, fast_n)
        slow = ind.ema(c, slow_n) if ma_type == "ema" else ind.sma(c, slow_n)
        if direction == "bull":
            return (fast > slow) & (fast.shift(1) <= slow.shift(1))
        return (fast < slow) & (fast.shift(1) >= slow.shift(1))

    if kind == "macd":
        fast_n = int(p.get("fast", 12))
        slow_n = int(p.get("slow", 26))
        sig_n = int(p.get("signal", 9))
        direction = p.get("direction", "bull")
        macd_line, signal_line, _ = ind.macd(c, fast_n, slow_n, sig_n)
        if direction == "bull":
            return (macd_line > signal_line) & (macd_line.shift(1) <= signal_line.shift(1))
        return (macd_line < signal_line) & (macd_line.shift(1) >= signal_line.shift(1))

    if kind == "volume":
        period = int(p.get("period", 20))
        mult = float(p.get("multiplier", 1.5))
        avg = df["v"].rolling(period, min_periods=period).mean()
        return df["v"] > (avg * mult)

    return pd.Series([False] * len(df), index=df.index)


def _combine(df: pd.DataFrame, rules: list, logic: str) -> pd.Series:
    if not rules:
        return pd.Series([False] * len(df), index=df.index)
    series_list = [_rule_series(df, r).fillna(False).astype(bool) for r in rules]
    combined = series_list[0]
    for s in series_list[1:]:
        combined = (combined & s) if logic == "AND" else (combined | s)
    return combined.fillna(False).astype(bool)


def evaluate(df: pd.DataFrame, strategy: dict):
    """Return (entry_signal, exit_signal) boolean lists aligned to df rows."""
    entry = _combine(df, strategy.get("entry_rules", []), strategy.get("logic", "AND"))
    exit_ = _combine(df, strategy.get("exit_rules", []), strategy.get("logic", "AND"))
    return entry.tolist(), exit_.tolist()


def rule_label(rule: dict) -> str:
    kind = rule["indicator"]
    p = rule.get("params", {}) or {}
    if kind == "rsi":
        return f"RSI({p.get('period',14)}) {p.get('op','below')} {p.get('value',30)}"
    if kind == "ma_cross":
        return f"{p.get('ma_type','ema').upper()} {p.get('fast',9)}/{p.get('slow',21)} {p.get('direction','bull')} cross"
    if kind == "macd":
        return f"MACD({p.get('fast',12)},{p.get('slow',26)},{p.get('signal',9)}) {p.get('direction','bull')} cross"
    if kind == "volume":
        return f"Volume > {p.get('multiplier',1.5)}x avg({p.get('period',20)})"
    return kind
