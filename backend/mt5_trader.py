"""MetaApi (MT5 on Deriv) connection + trade primitives.
A single long-lived RPC connection is warmed at startup and reused."""
import os
import time
import asyncio
import logging
from datetime import datetime

from metaapi_cloud_sdk import MetaApi

logger = logging.getLogger("mt5_trader")

# our signal symbols -> Deriv MT5 symbol (explicit for synthetics & indices)
SYMBOL_MAP = {
    "frxXAUUSD": "XAUUSD", "XAUUSD": "XAUUSD", "GOLD": "XAUUSD",
    "cryBTCUSD": "BTCUSD", "BTCUSDT": "BTCUSD", "BTCUSD": "BTCUSD",
    "R_10": "Volatility 10 Index", "R_25": "Volatility 25 Index",
    "R_50": "Volatility 50 Index", "R_75": "Volatility 75 Index",
    "R_100": "Volatility 100 Index",
    "1HZ10V": "Volatility 10 (1s) Index", "1HZ25V": "Volatility 25 (1s) Index",
    "1HZ50V": "Volatility 50 (1s) Index", "1HZ75V": "Volatility 75 (1s) Index",
    "1HZ100V": "Volatility 100 (1s) Index",
    "BOOM300N": "Boom 300 Index", "BOOM500": "Boom 500 Index", "BOOM1000": "Boom 1000 Index",
    "CRASH300N": "Crash 300 Index", "CRASH500": "Crash 500 Index", "CRASH1000": "Crash 1000 Index",
    "stpRNG": "Step Index",
    "OTC_SPC": "US 500", "OTC_NDX": "US Tech 100", "OTC_DJI": "Wall Street 30",
    "OTC_FTSE": "UK 100", "OTC_GDAXI": "Germany 40", "OTC_N225": "Japan 225",
    "OTC_AS51": "Australia 200", "OTC_HSI": "Hong Kong 50",
}

_state = {"conn": None, "account": None, "ready": False, "connecting": False, "error": None}
_lock: asyncio.Lock | None = None
_universe = {"t": 0.0, "list": None}


def is_configured() -> bool:
    return bool(os.environ.get("METAAPI_TOKEN") and os.environ.get("MT5_ACCOUNT_ID"))


def mt5_symbol(sym: str):
    if not sym:
        return None
    if sym in SYMBOL_MAP:
        return SYMBOL_MAP[sym]
    if sym.upper() in SYMBOL_MAP:
        return SYMBOL_MAP[sym.upper()]
    if sym.startswith("frx") and len(sym) == 9:
        return sym[3:]
    if sym.startswith("cry") and len(sym) >= 6:
        return sym[3:]
    if sym.endswith("USDT"):
        return sym[:-4] + "USD"
    return None


def status() -> dict:
    return {"ready": _state["ready"], "connecting": _state["connecting"], "error": _state["error"]}


async def _connect():
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    async with _lock:
        if _state["ready"] and _state["conn"]:
            return _state["conn"]
        _state["connecting"] = True
        _state["error"] = None
        try:
            api = MetaApi(os.environ["METAAPI_TOKEN"])
            account = await api.metatrader_account_api.get_account(os.environ["MT5_ACCOUNT_ID"])
            await account.wait_connected()
            conn = account.get_rpc_connection()
            await conn.connect()
            await conn.wait_synchronized()
            _state.update(conn=conn, account=account, ready=True, connecting=False)
            logger.info("MT5 RPC connection ready")
            return conn
        except Exception as e:  # noqa
            _state.update(ready=False, connecting=False, error=str(e))
            logger.error(f"MT5 connect failed: {e}")
            raise


async def ensure():
    if _state["ready"] and _state["conn"]:
        return _state["conn"]
    return await _connect()


async def warmup():
    if is_configured():
        try:
            await _connect()
        except Exception:  # noqa
            pass


async def account_info():
    conn = await ensure()
    return await conn.get_account_information()


async def positions():
    conn = await ensure()
    return await conn.get_positions()


async def price(sym: str):
    conn = await ensure()
    return await conn.get_symbol_price(mt5_symbol(sym) or sym)


async def market_order(sym: str, side: str, volume: float, comment: str = "TradeCrawler"):
    conn = await ensure()
    s = mt5_symbol(sym) or sym
    opts = {"comment": comment[:28]}
    if side == "BUY":
        return await conn.create_market_buy_order(s, volume, None, None, opts)
    return await conn.create_market_sell_order(s, volume, None, None, opts)


async def close_position(position_id: str):
    conn = await ensure()
    return await conn.close_position(position_id)


# ---------------- market data (real Deriv prices via MT5) ----------------
def _to_ms(t) -> int:
    if isinstance(t, (int, float)):
        return int(t if t > 1e12 else t * 1000)
    if hasattr(t, "timestamp"):
        return int(t.timestamp() * 1000)
    return int(datetime.fromisoformat(str(t)).timestamp() * 1000)


async def candles(symbol: str, timeframe: str, limit: int = 300, closed_only: bool = True):
    await ensure()
    acc = _state["account"]
    rows = await acc.get_historical_candles(symbol, timeframe, None, min(limit + 1, 1000))
    out = []
    for c in rows or []:
        out.append({"t": _to_ms(c.get("time")), "o": float(c["open"]), "h": float(c["high"]),
                    "l": float(c["low"]), "c": float(c["close"]),
                    "v": float(c.get("tickVolume") or 0)})
    out.sort(key=lambda x: x["t"])
    if closed_only and len(out) > 1:
        out = out[:-1]
    return out[-limit:]


async def live_price(symbol: str):
    conn = await ensure()
    p = await conn.get_symbol_price(symbol)
    bid = p.get("bid")
    ask = p.get("ask")
    mid = (bid + ask) / 2 if bid is not None and ask is not None else (bid or ask)
    return {"price": mid, "bid": bid, "ask": ask}


async def get_universe():
    if _universe["list"] and time.time() - _universe["t"] < 3600:
        return _universe["list"]
    conn = await ensure()
    syms = await conn.get_symbols()
    lst = [s for s in syms if ".conv" not in s.lower() and not s.lower().startswith("conv")]
    _universe.update(t=time.time(), list=lst)
    return lst
