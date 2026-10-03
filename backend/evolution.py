"""Adaptive Evolution Engine.

Continuously optimizes each strategy's indicator parameters using a genetic
algorithm with WALK-FORWARD validation (optimize on in-sample, validate on
out-of-sample) to avoid curve-fitting. Risk-adjusted fitness. Detects the
current market regime. Applies an evolved config ONLY if it also beats the
current config out-of-sample.

This is adaptive optimization with honest out-of-sample gating — not a
predictor. It makes the signal engine keep learning from recent price action.
"""
import copy
import math
import random
import uuid
import logging
from statistics import mean
from datetime import datetime, timezone

from db import db
import market_data as md
from strategy_engine import candles_to_df
from backtester import run_backtest
import indicators as ind

logger = logging.getLogger(__name__)

POPULATION = 12
GENERATIONS = 8
LOOKBACK = 800
INSAMPLE_FRAC = 0.7

# tunable numeric ranges per indicator: key -> (min, max, type)
RANGES = {
    "rsi": {"period": (5, 30, "int"), "value": (10, 90, "int")},
    "ma_cross": {"fast": (3, 30, "int"), "slow": (10, 120, "int")},
    "macd": {"fast": (5, 20, "int"), "slow": (21, 55, "int"), "signal": (5, 15, "int")},
    "volume": {"period": (5, 60, "int"), "multiplier": (1.0, 3.0, "float")},
}


# --------------- fitness ---------------
def fitness(bt: dict) -> float:
    """Risk-adjusted score: edge scaled by sample size, reward beating B&H,
    penalize drawdown. Discourage degenerate no-trade configs."""
    if not bt or "error" in bt:
        return -1e9
    nt = bt.get("num_trades", 0)
    if nt < 2:
        return -100.0 + nt
    dd = abs(bt.get("max_drawdown_pct", 0.0))
    edge = bt.get("expectancy_pct", 0.0) * math.sqrt(min(nt, 40))
    bench = (bt.get("total_return_pct", 0.0) - bt.get("benchmark_return_pct", 0.0)) * 0.15
    penalty = 0.35 * dd
    return round(edge + bench - penalty, 4)


# --------------- genome ops ---------------
def _fix(indicator: str, p: dict) -> dict:
    if indicator == "ma_cross" and p.get("slow", 0) <= p.get("fast", 0):
        p["slow"] = p["fast"] + max(2, int(p["fast"] * 0.5))
    if indicator == "macd" and p.get("slow", 0) <= p.get("fast", 0):
        p["slow"] = p["fast"] + 5
    return p


def _random_params(indicator, params):
    p = dict(params)
    for k, (lo, hi, typ) in RANGES.get(indicator, {}).items():
        if k not in p:
            continue
        p[k] = random.randint(lo, hi) if typ == "int" else round(random.uniform(lo, hi), 2)
    return _fix(indicator, p)


def _mutate_params(indicator, params, rate=0.5):
    p = dict(params)
    for k, (lo, hi, typ) in RANGES.get(indicator, {}).items():
        if k not in p or random.random() >= rate:
            continue
        span = hi - lo
        if typ == "int":
            delta = int(round(random.uniform(-0.25, 0.25) * span)) or random.choice([-1, 1])
            p[k] = max(lo, min(hi, int(p[k]) + delta))
        else:
            p[k] = round(max(lo, min(hi, float(p[k]) + random.uniform(-0.25, 0.25) * span)), 2)
    return _fix(indicator, p)


def _crossover_params(indicator, a, b):
    p = dict(a)
    for k in RANGES.get(indicator, {}):
        if k in p and random.random() < 0.5:
            p[k] = b.get(k, p[k])
    return _fix(indicator, p)


def _inds(strategy):
    return ([r["indicator"] for r in strategy["entry_rules"]],
            [r["indicator"] for r in strategy["exit_rules"]])


def current_genome(strategy):
    return {"entry": [dict(r["params"]) for r in strategy["entry_rules"]],
            "exit": [dict(r["params"]) for r in strategy["exit_rules"]]}


def random_genome(strategy):
    ei, xi = _inds(strategy)
    return {"entry": [_random_params(ei[i], strategy["entry_rules"][i]["params"]) for i in range(len(ei))],
            "exit": [_random_params(xi[i], strategy["exit_rules"][i]["params"]) for i in range(len(xi))]}


def mutate_genome(strategy, g):
    ei, xi = _inds(strategy)
    return {"entry": [_mutate_params(ei[i], g["entry"][i]) for i in range(len(ei))],
            "exit": [_mutate_params(xi[i], g["exit"][i]) for i in range(len(xi))]}


def crossover_genome(strategy, a, b):
    ei, xi = _inds(strategy)
    return {"entry": [_crossover_params(ei[i], a["entry"][i], b["entry"][i]) for i in range(len(ei))],
            "exit": [_crossover_params(xi[i], a["exit"][i], b["exit"][i]) for i in range(len(xi))]}


def apply_genome(strategy, g):
    s = copy.deepcopy(strategy)
    s["entry_rules"] = [{"indicator": r["indicator"], "params": g["entry"][i]}
                        for i, r in enumerate(strategy["entry_rules"])]
    s["exit_rules"] = [{"indicator": r["indicator"], "params": g["exit"][i]}
                       for i, r in enumerate(strategy["exit_rules"])]
    return s


# --------------- market regime ---------------
def detect_regime(candles: list) -> dict:
    df = candles_to_df(candles)
    c = df["c"]
    n = len(c)
    if n < 60:
        return {"trend": "unknown", "volatility": "unknown", "vol_pct": 0.0, "strength": 0}
    ema20 = ind.ema(c, 20)
    ema50 = ind.ema(c, 50)
    last = float(c.iloc[-1])
    diff = (float(ema20.iloc[-1]) - float(ema50.iloc[-1])) / last * 100
    slope = (float(ema20.iloc[-1]) - float(ema20.iloc[-10])) / float(ema20.iloc[-10]) * 100
    if diff > 0.5 and slope > 0:
        trend = "up"
    elif diff < -0.5 and slope < 0:
        trend = "down"
    else:
        trend = "sideways"
    rets = c.pct_change().dropna()
    vol = float(rets.tail(30).std() * 100)
    volhist = (rets.rolling(30).std() * 100).dropna()
    rank = float((volhist < volhist.iloc[-1]).mean() * 100) if len(volhist) else 50.0
    volatility = "high" if rank > 70 else "low" if rank < 30 else "normal"
    strength = int(min(100, abs(diff) * 20 + abs(slope) * 5))
    return {"trend": trend, "volatility": volatility, "vol_pct": round(vol, 2),
            "strength": strength, "vol_rank": round(rank)}


# --------------- main evolve ---------------
def _summ(bt: dict) -> dict:
    return {
        "total_return_pct": bt.get("total_return_pct"),
        "benchmark_return_pct": bt.get("benchmark_return_pct"),
        "expectancy_pct": bt.get("expectancy_pct"),
        "max_drawdown_pct": bt.get("max_drawdown_pct"),
        "num_trades": bt.get("num_trades"),
        "beats_benchmark": bt.get("beats_benchmark"),
    }


async def evolve_strategy(strategy: dict) -> dict:
    try:
        candles = await md.get_candles(strategy["symbol"], strategy["timeframe"], LOOKBACK)
    except Exception as e:  # noqa
        return {"error": f"data error: {e}"}
    if len(candles) < 250:
        return {"error": "insufficient history to evolve"}

    split = int(len(candles) * INSAMPLE_FRAC)
    ins, oos = candles[:split], candles[split:]

    base = current_genome(strategy)
    population = [base] + [random_genome(strategy) for _ in range(POPULATION - 1)]
    gens_log = []
    best = None  # (genome, fitness)

    for g in range(GENERATIONS):
        scored = []
        for genome in population:
            bt = run_backtest(ins, apply_genome(strategy, genome))
            scored.append((genome, fitness(bt)))
        scored.sort(key=lambda x: x[1], reverse=True)
        if best is None or scored[0][1] > best[1]:
            best = (copy.deepcopy(scored[0][0]), scored[0][1])
        vals = [s[1] for s in scored if s[1] > -1e8]
        gens_log.append({"gen": g + 1, "best": round(scored[0][1], 3),
                         "avg": round(mean(vals), 3) if vals else 0.0})
        survivors = [s[0] for s in scored[: max(2, POPULATION // 2)]]
        children = []
        while len(survivors) + len(children) < POPULATION:
            a, b = random.sample(survivors, 2)
            children.append(mutate_genome(strategy, crossover_genome(strategy, a, b)))
        population = survivors + children

    candidate = best[0]

    # walk-forward validation on out-of-sample
    cur_oos = run_backtest(oos, strategy)
    cand_oos = run_backtest(oos, apply_genome(strategy, candidate))
    cur_f, cand_f = fitness(cur_oos), fitness(cand_oos)
    improved = cand_f > cur_f and cand_oos.get("num_trades", 0) >= 2

    applied = False
    if improved and strategy.get("auto_evolve", True):
        new = apply_genome(strategy, candidate)
        await db.strategies.update_one(
            {"id": strategy["id"]},
            {"$set": {"entry_rules": new["entry_rules"], "exit_rules": new["exit_rules"]}})
        # refresh full backtest + gate on the updated config
        full = run_backtest(candles[-500:] if len(candles) > 500 else candles, new)
        if "error" not in full:
            full["strategy_id"] = strategy["id"]
            full["ran_at"] = datetime.now(timezone.utc).isoformat()
            await db.backtests.replace_one({"strategy_id": strategy["id"]}, full, upsert=True)
            new_live = strategy.get("live", False) and full["gate_passed"]
            await db.strategies.update_one(
                {"id": strategy["id"]},
                {"$set": {"gate_passed": full["gate_passed"], "live": new_live}})
        applied = True

    regime = detect_regime(candles)
    record = {
        "id": str(uuid.uuid4()),
        "strategy_id": strategy["id"],
        "strategy_name": strategy["name"],
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "generations": gens_log,
        "num_generations": GENERATIONS,
        "population": POPULATION,
        "best_in_sample_fitness": round(best[1], 4),
        "current_oos_fitness": round(cur_f, 4),
        "candidate_oos_fitness": round(cand_f, 4),
        "improvement": round(cand_f - cur_f, 4),
        "improved": improved,
        "applied": applied,
        "auto_evolve": strategy.get("auto_evolve", True),
        "regime": regime,
        "old_params": base,
        "new_params": candidate,
        "current_oos": _summ(cur_oos),
        "candidate_oos": _summ(cand_oos),
    }
    await db.evolutions.insert_one(dict(record))
    record.pop("_id", None)
    logger.info(f"Evolve {strategy['name']}: improved={improved} applied={applied} "
                f"cur={cur_f} cand={cand_f}")
    return record
