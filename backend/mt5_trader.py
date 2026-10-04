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
_api = None  # single reused MetaApi client (avoids piling up account subscriptions)

# valid MT5 symbol names (the mapped targets) for pass-through matching
_MT5_SYMBOLS = set(SYMBOL_MAP.values())


def is_configured() -> bool:
    return bool(os.environ.get("METAAPI_TOKEN") and os.environ.get("MT5_ACCOUNT_ID"))


def mt5_symbol(sym: str):
    if not sym:
        return None
    if sym in SYMBOL_MAP:
        return SYMBOL_MAP[sym]
    if sym.upper() in SYMBOL_MAP:
        return SYMBOL_MAP[sym.upper()]
    # already a valid MT5 symbol (e.g. "Volatility 100 Index", "XAUUSD")
    if sym in _MT5_SYMBOLS:
        return sym
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
    global _lock, _api
    if _lock is None:
        _lock = asyncio.Lock()
    async with _lock:
        if _state["ready"] and _state["conn"]:
            return _state["conn"]
        _state["connecting"] = True
        _state["error"] = None
        conn = None
        # close any previous connection first so we don't leak account
        # subscriptions on MetaApi's side (max 25/user) across reconnects
        old = _state.get("conn")
        if old is not None:
            try:
                old.close()
            except Exception:  # noqa
                pass
            _state["conn"] = None
        try:
            if _api is None:
                _api = MetaApi(os.environ["METAAPI_TOKEN"])
            account = await _api.metatrader_account_api.get_account(os.environ["MT5_ACCOUNT_ID"])
            await account.wait_connected()
            conn = account.get_rpc_connection()
            await conn.connect()
            await conn.wait_synchronized()
            _state.update(conn=conn, account=account, ready=True, connecting=False)
            logger.info("MT5 RPC connection ready")
            return conn
        except Exception as e:  # noqa
            # Dispose the client so its socket stops reconnecting (and holding a
            # subscription slot) during the back-off window.
            try:
                if conn is not None:
                    conn.close()
            except Exception:  # noqa
                pass
            try:
                if _api is not None:
                    _api.close()
            except Exception:  # noqa
                pass
            _api = None
            _state.update(conn=None, account=None, ready=False, connecting=False,
                          error=str(e), last_fail=time.time())
            logger.error(f"MT5 connect failed: {e}")
            raise


async def ensure():
    if _state["ready"] and _state["conn"]:
        return _state["conn"]
    # back off during a broker outage so requests fail fast instead of each
    # waiting the full connect timeout
    since_fail = time.time() - _state.get("last_fail", 0)
    if since_fail < CONNECT_COOLDOWN:
        raise RuntimeError("MT5 temporarily unavailable (broker reconnecting)")
    return await asyncio.wait_for(_connect(), CONNECT_TIMEOUT)


def is_ready() -> bool:
    return bool(_state["ready"] and _state["conn"])


async def _bg_connect():
    try:
        await asyncio.wait_for(_connect(), CONNECT_TIMEOUT)
    except Exception as e:  # noqa
        logger.warning(f"MT5 background connect failed: {e}")


def ensure_bg():
    """Fire-and-forget (re)connect so user requests never block on a slow connect."""
    if _state["ready"] or _state["connecting"]:
        return
    if time.time() - _state.get("last_fail", 0) < CONNECT_COOLDOWN:
        return
    try:
        asyncio.get_event_loop().create_task(_bg_connect())
    except RuntimeError:
        pass


RPC_TIMEOUT = 15        # fail fast on a single RPC call instead of hanging
CONNECT_TIMEOUT = 60    # first sync can be slow, but must not hang forever
CONNECT_COOLDOWN = 120  # after a failed connect, back off long to avoid churn


async def warmup():
    if is_configured():
        try:
            await _connect()
        except Exception:  # noqa
            pass


async def shutdown():
    """Release the account subscription on shutdown so restarts don't leak
    MetaApi subscriptions (max 25/user)."""
    global _api
    conn = _state.get("conn")
    if conn is not None:
        try:
            conn.close()
        except Exception:  # noqa
            pass
    if _api is not None:
        try:
            _api.close()
        except Exception:  # noqa
            pass
    _state.update(conn=None, account=None, ready=False)
    _api = None


async def _rpc(coro, timeout=None):
    """Run an MT5 RPC with a hard timeout so a degraded connection can't hang an
    HTTP request forever. We do NOT drop the connection on a single slow call —
    that caused a reconnect storm that exhausted MetaApi's subscription quota.
    The connection is only rebuilt on an explicit connect failure."""
    try:
        return await asyncio.wait_for(coro, timeout or RPC_TIMEOUT)
    except asyncio.TimeoutError:
        _state["error"] = "RPC timeout"
        raise RuntimeError("MT5 request timed out")


async def account_info():
    conn = await ensure()
    return await _rpc(conn.get_account_information())


async def positions():
    conn = await ensure()
    return await _rpc(conn.get_positions())


async def deals_range(start, end):
    conn = await ensure()
    return await _rpc(conn.get_deals_by_time_range(start, end))


async def price(sym: str):
    conn = await ensure()
    return await _rpc(conn.get_symbol_price(mt5_symbol(sym) or sym))


async def symbol_spec(sym: str):
    conn = await ensure()
    return await _rpc(conn.get_symbol_specification(mt5_symbol(sym) or sym))


async def market_order(sym: str, side: str, volume: float, stop_loss=None,
                       take_profit=None, comment: str = "TradeCrawler", client_id=None):
    conn = await ensure()
    s = mt5_symbol(sym) or sym
    opts = {"comment": comment[:28]}
    if client_id:
        opts["clientId"] = client_id[:64]
    if side == "BUY":
        return await _rpc(conn.create_market_buy_order(s, volume, stop_loss, take_profit, opts), timeout=30)
    return await _rpc(conn.create_market_sell_order(s, volume, stop_loss, take_profit, opts), timeout=30)


async def close_position(position_id: str):
    conn = await ensure()
    return await _rpc(conn.close_position(position_id), timeout=30)


async def modify_position(position_id: str, stop_loss=None, take_profit=None):
    conn = await ensure()
    return await _rpc(conn.modify_position(position_id, stop_loss=stop_loss, take_profit=take_profit), timeout=30)


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
    rows = await _rpc(acc.get_historical_candles(symbol, timeframe, None, min(limit + 1, 1000)))
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
    p = await _rpc(conn.get_symbol_price(symbol))
    bid = p.get("bid")
    ask = p.get("ask")
    mid = (bid + ask) / 2 if bid is not None and ask is not None else (bid or ask)
    return {"price": mid, "bid": bid, "ask": ask}


async def get_universe():
    if _universe["list"] and time.time() - _universe["t"] < 3600:
        return _universe["list"]
    conn = await ensure()
    syms = await _rpc(conn.get_symbols())
    lst = [s for s in syms if ".conv" not in s.lower() and not s.lower().startswith("conv")]
    _universe.update(t=time.time(), list=lst)
    return lst
