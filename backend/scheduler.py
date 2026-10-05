"""Background poller: runs enabled strategies on a cadence to detect new
closed-candle signals, update paper P&L, and trigger alerts."""
import asyncio
import logging
from datetime import datetime, timezone

from db import db
from paper_engine import run_strategy_step

logger = logging.getLogger(__name__)

POLL_SECONDS = 30            # fast loop so closed-candle alerts fire promptly
EVOLVE_SECONDS = 86400       # daily background evolution
SCAN_SECONDS = 90            # hunt active instruments for tradeable setups
CANDLE_WARM_SECONDS = 15     # keep charts' candles warm & advancing (MT5 only)
_task = None
_evolve_task = None
_scan_task = None
_candle_task = None


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


async def _scan_loop():
    import trade_engine
    await asyncio.sleep(20)  # let MT5 warm up first
    logger.info("Trade auto-scan loop started")
    while True:
        try:
            await trade_engine.trail_stops()
            await trade_engine.auto_scan()
        except Exception as e:  # noqa
            logger.warning(f"Trade scan loop error: {e}")
        await asyncio.sleep(SCAN_SECONDS)


async def _candle_loop():
    import market_data as md
    await asyncio.sleep(8)
    logger.info("Candle warm loop started")
    while True:
        try:
            await md.warm_candles()
        except Exception as e:  # noqa
            logger.debug(f"Candle warm loop error: {e}")
        await asyncio.sleep(CANDLE_WARM_SECONDS)


def start_scheduler():
    global _task, _evolve_task, _scan_task, _candle_task
    if _task is None:
        _task = asyncio.create_task(_loop())
    if _evolve_task is None:
        _evolve_task = asyncio.create_task(_evolve_loop())
    if _scan_task is None:
        _scan_task = asyncio.create_task(_scan_loop())
    if _candle_task is None:
        _candle_task = asyncio.create_task(_candle_loop())
    return _task
