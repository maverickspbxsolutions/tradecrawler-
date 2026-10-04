import logging
import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from db import db, client
from auth import auth_router, seed_admin
from routes import api
import market_data as md
from strategy_engine import Strategy
from backtester import run_backtest
from paper_engine import backfill_signals
from evolution import evolve_strategy
from scheduler import start_scheduler

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="Crypto Signal Terminal")

app.include_router(auth_router)
app.include_router(api)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DEFAULT_STRATEGIES = [
    {
        "name": "RSI Mean Reversion",
        "symbol": "BTCUSDT", "timeframe": "1h", "logic": "AND",
        "entry_rules": [{"indicator": "rsi", "params": {"period": 14, "op": "below", "value": 30}}],
        "exit_rules": [{"indicator": "rsi", "params": {"period": 14, "op": "above", "value": 70}}],
    },
    {
        "name": "EMA 9/21 Crossover",
        "symbol": "ETHUSDT", "timeframe": "4h", "logic": "AND",
        "entry_rules": [{"indicator": "ma_cross", "params": {"fast": 9, "slow": 21, "ma_type": "ema", "direction": "bull"}}],
        "exit_rules": [{"indicator": "ma_cross", "params": {"fast": 9, "slow": 21, "ma_type": "ema", "direction": "bear"}}],
    },
    {
        "name": "MACD + Volume Confirm",
        "symbol": "SOLUSDT", "timeframe": "1h", "logic": "AND",
        "entry_rules": [
            {"indicator": "macd", "params": {"fast": 12, "slow": 26, "signal": 9, "direction": "bull"}},
            {"indicator": "volume", "params": {"period": 20, "multiplier": 1.3}},
        ],
        "exit_rules": [{"indicator": "macd", "params": {"fast": 12, "slow": 26, "signal": 9, "direction": "bear"}}],
    },
]


async def seed_defaults():
    if await db.strategies.count_documents({}) == 0:
        for cfg in DEFAULT_STRATEGIES:
            strat = Strategy(**cfg)
            await db.strategies.insert_one(strat.model_dump())
        logger.info("Seeded default strategies")
    if await db.settings.count_documents({"id": "global"}) == 0:
        await db.settings.insert_one({
            "id": "global", "alert_email": "", "email_enabled": True, "sms_enabled": False,
            "max_alerts_per_hour": 10, "max_emails_per_day": 50,
            "min_minutes_between_same_strategy": 15,
        })


async def bootstrap_engine():
    """Backfill feed + run initial backtests so the dashboard isn't empty."""
    strats = await db.strategies.find({}, {"_id": 0}).to_list(100)
    for s in strats:
        try:
            if await db.signals.count_documents({"strategy_id": s["id"]}) == 0:
                await backfill_signals(s)
            if await db.backtests.count_documents({"strategy_id": s["id"]}) == 0:
                candles = await md.get_candles(s["symbol"], s["timeframe"], 500)
                result = run_backtest(candles, s)
                if "error" not in result:
                    result["strategy_id"] = s["id"]
                    result["ran_at"] = datetime.now(timezone.utc).isoformat()
                    await db.backtests.replace_one({"strategy_id": s["id"]}, result, upsert=True)
                    await db.strategies.update_one(
                        {"id": s["id"]}, {"$set": {"gate_passed": result["gate_passed"],
                                                    "live": result["gate_passed"]}})
            if await db.evolutions.count_documents({"strategy_id": s["id"]}) == 0:
                fresh = await db.strategies.find_one({"id": s["id"]}, {"_id": 0})
                await evolve_strategy(fresh)
        except Exception as e:  # noqa
            logger.warning(f"Bootstrap error for {s.get('name')}: {e}")


@app.on_event("startup")
async def on_startup():
    await db.users.create_index("username", unique=True)
    await db.signals.create_index("sig_key", unique=True)
    await db.alerts.create_index("idempotency_key", unique=True)
    await db.strategies.create_index("id", unique=True)
    await db.paper_states.create_index("strategy_id", unique=True)
    await seed_admin()
    await seed_defaults()
    # autonomy reconcile: alerts follow proven edge; default auto-improve on
    await db.strategies.update_many({"auto_evolve": {"$exists": False}}, {"$set": {"auto_evolve": True}})
    await db.strategies.update_many({"gate_passed": True}, {"$set": {"live": True}})
    await db.strategies.update_many({"gate_passed": {"$ne": True}}, {"$set": {"live": False}})
    asyncio.create_task(bootstrap_engine())
    asyncio.create_task(md.get_deriv_symbols())  # warm Deriv symbol cache in background
    asyncio.create_task(_warm_mt5())
    start_scheduler()
    logger.info("Crypto Signal Terminal backend started")


async def _warm_mt5():
    import mt5_trader
    if mt5_trader.is_configured():
        await mt5_trader.warmup()
        await md.warm_mt5_universe()


@app.get("/api/")
async def root():
    return {"service": "Crypto Signal Terminal", "status": "ok"}


@app.on_event("shutdown")
async def shutdown():
    try:
        import mt5_trader
        await mt5_trader.shutdown()
    except Exception:  # noqa
        pass
    client.close()
