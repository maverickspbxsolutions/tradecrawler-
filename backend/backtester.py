"""Historical backtester. No lookahead (signal at candle i close, fill at i+1 open).
Models fees + slippage. Reports expectancy, risk-reward, max drawdown, vs buy-and-hold."""
from strategy_engine import candles_to_df, evaluate


def run_backtest(candles: list, strategy: dict, initial_capital: float = 10000.0) -> dict:
    df = candles_to_df(candles)
    n = len(df)
    fees = float(strategy.get("fees_pct", 0.1)) / 100.0
    slip = float(strategy.get("slippage_pct", 0.05)) / 100.0

    if n < 50:
        return {"error": "Not enough candles to backtest", "candles": n}

    entry, exit_ = evaluate(df, strategy)
    closes = df["c"].tolist()
    opens = df["o"].tolist()
    times = df["t"].tolist()

    cash = initial_capital
    units = 0.0
    position = "flat"
    entry_price = 0.0
    entry_value = 0.0
    entry_time = None
    trades = []
    equity_curve = []

    # buy-and-hold benchmark (one entry fee)
    bench_units = (initial_capital * (1 - fees)) / closes[0]

    peak = initial_capital
    max_dd = 0.0

    for i in range(n):
        # execute decision made at candle i-1 (fill at candle i open) -> no lookahead
        if i > 0:
            fill = opens[i]
            if position == "flat" and entry[i - 1]:
                buy_price = fill * (1 + slip)
                units = (cash * (1 - fees)) / buy_price
                entry_value = cash
                cash = 0.0
                position = "long"
                entry_price = buy_price
                entry_time = times[i]
            elif position == "long" and exit_[i - 1]:
                sell_price = fill * (1 - slip)
                proceeds = units * sell_price * (1 - fees)
                ret_pct = (proceeds / entry_value - 1) * 100 if entry_value else 0.0
                trades.append({
                    "entry_time": entry_time,
                    "exit_time": times[i],
                    "entry_price": round(entry_price, 6),
                    "exit_price": round(sell_price, 6),
                    "return_pct": round(ret_pct, 4),
                    "pnl": round(proceeds - entry_value, 2),
                })
                cash = proceeds
                units = 0.0
                position = "flat"

        equity = cash + units * closes[i]
        bench_equity = bench_units * closes[i]
        peak = max(peak, equity)
        dd = (equity / peak - 1) * 100 if peak else 0.0
        max_dd = min(max_dd, dd)
        equity_curve.append({
            "t": times[i],
            "equity": round(equity, 2),
            "benchmark": round(bench_equity, 2),
        })

    # close any open position at last close
    if position == "long":
        sell_price = closes[-1] * (1 - slip)
        proceeds = units * sell_price * (1 - fees)
        ret_pct = (proceeds / entry_value - 1) * 100 if entry_value else 0.0
        trades.append({
            "entry_time": entry_time,
            "exit_time": times[-1],
            "entry_price": round(entry_price, 6),
            "exit_price": round(sell_price, 6),
            "return_pct": round(ret_pct, 4),
            "pnl": round(proceeds - entry_value, 2),
            "open_closed": True,
        })
        cash = proceeds
        final_equity = cash
    else:
        final_equity = cash + units * closes[-1]

    wins = [t for t in trades if t["return_pct"] > 0]
    losses = [t for t in trades if t["return_pct"] <= 0]
    num = len(trades)
    win_rate = (len(wins) / num * 100) if num else 0.0
    avg_win = sum(t["return_pct"] for t in wins) / len(wins) if wins else 0.0
    avg_loss = sum(t["return_pct"] for t in losses) / len(losses) if losses else 0.0
    expectancy = sum(t["return_pct"] for t in trades) / num if num else 0.0
    risk_reward = abs(avg_win / avg_loss) if avg_loss != 0 else (float("inf") if avg_win > 0 else 0.0)

    holds = [t["exit_time"] - t["entry_time"] for t in trades
             if isinstance(t.get("entry_time"), (int, float))
             and isinstance(t.get("exit_time"), (int, float))]
    avg_hold_ms = round(sum(holds) / len(holds)) if holds else 0

    total_return = (final_equity / initial_capital - 1) * 100
    bench_return = (equity_curve[-1]["benchmark"] / initial_capital - 1) * 100
    beats_benchmark = total_return > bench_return

    return {
        "candles": n,
        "period_start": times[0],
        "period_end": times[-1],
        "initial_capital": initial_capital,
        "final_equity": round(final_equity, 2),
        "total_return_pct": round(total_return, 2),
        "benchmark_return_pct": round(bench_return, 2),
        "beats_benchmark": beats_benchmark,
        "num_trades": num,
        "win_rate": round(win_rate, 2),
        "avg_win_pct": round(avg_win, 3),
        "avg_loss_pct": round(avg_loss, 3),
        "expectancy_pct": round(expectancy, 4),
        "risk_reward": round(risk_reward, 3) if risk_reward != float("inf") else 999.0,
        "max_drawdown_pct": round(max_dd, 2),
        "avg_hold_ms": avg_hold_ms,
        "gate_passed": bool(beats_benchmark and expectancy > 0 and num >= 3),
        "equity_curve": equity_curve,
        "trades": trades[-100:],
    }
