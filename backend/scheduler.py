"""Background poller: runs enabled strategies on a cadence to detect new
closed-candle signals, update paper P&L, and trigger alerts."""
import asyncio
import logging
from datetime import datetime, timezone

from db import db
from paper_engine import run_strategy_step

logger = logging.getLogger(__name__)

POLL_SECONDS = 60
EVOLVE_SECONDS = 86400       # daily background evolution
_task = None
_evolve_task = None


async def _loop():
    logger.info("Signal engine poller started")
    while True:
        try:
            strategies = await db.strategies.find({"enabled": True}, {"_id": 0}).to_list(500)
            for strat in strategies:
                try:
                    sig = await run_strategy_step(strat, source="poll")
                    if sig:
                        logger.info(f"Signal: {sig['side']} {sig['symbol']} ({strat['name']})")
                except Exception as e:  # noqa
                    logger.warning(f"Strategy step error {strat.get('name')}: {e}")
            await db.engine_status.update_one(
                {"id": "engine"},
                {"$set": {"id": "engine", "last_run": datetime.now(timezone.utc).isoformat(),
                          "active_strategies": len(strategies)}},
                upsert=True)
        except Exception as e:  # noqa
            logger.error(f"Poller loop error: {e}")
        await asyncio.sleep(POLL_SECONDS)


async def _evolve_loop():
    from evolution import evolve_strategy
    await asyncio.sleep(EVOLVE_SECONDS)
    logger.info("Adaptive evolution loop started")
    while True:
        try:
            strategies = await db.strategies.find({}, {"_id": 0}).to_list(500)
            for strat in strategies:
                try:
                    await evolve_strategy(strat)
                except Exception as e:  # noqa
                    logger.warning(f"Evolve error {strat.get('name')}: {e}")
        except Exception as e:  # noqa
            logger.error(f"Evolve loop error: {e}")
        await asyncio.sleep(EVOLVE_SECONDS)


def start_scheduler():
    global _task, _evolve_task
    if _task is None:
        _task = asyncio.create_task(_loop())
    if _evolve_task is None:
        _evolve_task = asyncio.create_task(_evolve_loop())
    return _task
