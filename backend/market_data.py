"""Crypto market data. Binance.US primary, Coinbase Exchange fallback.
Returns closed candles only (drops the currently-forming candle).
Includes retries, in-memory TTL cache, and data-health tracking."""
import os
import time
import json
import asyncio
import logging
import httpx
import websockets

logger = logging.getLogger(__name__)

BINANCE_BASE = "https://api.binance.us/api/v3"
COINBASE_BASE = "https://api.exchange.coinbase.com"

# timeframe -> (binance interval, coinbase granularity seconds, seconds per candle)
TIMEFRAMES = {
    "1m": ("1m", 60, 60),
    "5m": ("5m", 300, 300),
    "15m": ("15m", 900, 900),
    "1h": ("1h", 3600, 3600),
    "4h": ("4h", 21600, 14400),  # coinbase has no 4h (uses 6h); binance primary
    "1d": ("1d", 86400, 86400),
}

POPULAR_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
    "ADAUSDT", "AVAXUSDT", "LINKUSDT", "LTCUSDT", "BNBUSDT", "DOTUSDT", "MATICUSDT",
]

_symbols_cache = {"t": 0, "list": None}


async def get_all_symbols():
    """All TRADING USDT pairs on Binance.US, majors first. Cached 1h."""
    now = time.time()
    if _symbols_cache["list"] and now - _symbols_cache["t"] < 3600:
        return _symbols_cache["list"]
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(f"{BINANCE_BASE}/exchangeInfo")
            r.raise_for_status()
            data = r.json()
        usdt = [s["symbol"] for s in data.get("symbols", [])
                if s.get("status") == "TRADING" and s.get("quoteAsset") == "USDT"]
        pset = set(usdt)
        priority = [s for s in POPULAR_SYMBOLS if s in pset]
        rest = sorted(s for s in usdt if s not in set(POPULAR_SYMBOLS))
        ordered = priority + rest
        _symbols_cache.update({"t": now, "list": ordered})
        return ordered
    except Exception as e:  # noqa
        logger.warning(f"exchangeInfo fetch failed: {e}")
        return _symbols_cache["list"] or POPULAR_SYMBOLS


# ---------------- Deriv (forex, commodities, crypto, indices, synthetics) ----------------
DERIV_APP_ID = os.environ.get("DERIV_APP_ID", "1089")
DERIV_API_TOKEN = os.environ.get("DERIV_API_TOKEN")
DERIV_WS = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"
_DERIV_PREFIXES = ("R_", "1HZ", "frx", "cry", "OTC_", "WLD", "JD", "stpRNG",
                   "BOOM", "CRASH", "RDBULL", "RDBEAR")
_deriv_sym_cache = {"t": 0, "list": None, "fail_t": 0}
_deriv_symbol_set: set = set()


def _ds(symbol, label, market, submarket=""):
    return {"symbol": symbol, "label": label, "market": market, "submarket": submarket}


# Static list of Deriv instruments so the picker is ALWAYS populated, even when the
# Deriv WebSocket is unreachable. When the live feed is reachable it replaces this.
DERIV_FALLBACK_SYMBOLS = [
    # Commodities / metals
    _ds("frxXAUUSD", "Gold/USD", "Commodities", "Metals"),
    _ds("frxXAGUSD", "Silver/USD", "Commodities", "Metals"),
    _ds("frxXPTUSD", "Platinum/USD", "Commodities", "Metals"),
    _ds("frxXPDUSD", "Palladium/USD", "Commodities", "Metals"),
    # Crypto (Deriv CFD)
    _ds("cryBTCUSD", "BTC/USD", "Cryptocurrencies", "Crypto"),
    _ds("cryETHUSD", "ETH/USD", "Cryptocurrencies", "Crypto"),
    _ds("cryLTCUSD", "LTC/USD", "Cryptocurrencies", "Crypto"),
    _ds("cryXRPUSD", "XRP/USD", "Cryptocurrencies", "Crypto"),
    _ds("cryBCHUSD", "BCH/USD", "Cryptocurrencies", "Crypto"),
    _ds("cryEOSUSD", "EOS/USD", "Cryptocurrencies", "Crypto"),
    # Volatility / synthetic indices
    _ds("R_10", "Volatility 10 Index", "Synthetic Indices", "Continuous Indices"),
    _ds("R_25", "Volatility 25 Index", "Synthetic Indices", "Continuous Indices"),
    _ds("R_50", "Volatility 50 Index", "Synthetic Indices", "Continuous Indices"),
    _ds("R_75", "Volatility 75 Index", "Synthetic Indices", "Continuous Indices"),
    _ds("R_100", "Volatility 100 Index", "Synthetic Indices", "Continuous Indices"),
    _ds("1HZ10V", "Volatility 10 (1s) Index", "Synthetic Indices", "Continuous Indices"),
    _ds("1HZ25V", "Volatility 25 (1s) Index", "Synthetic Indices", "Continuous Indices"),
    _ds("1HZ50V", "Volatility 50 (1s) Index", "Synthetic Indices", "Continuous Indices"),
    _ds("1HZ75V", "Volatility 75 (1s) Index", "Synthetic Indices", "Continuous Indices"),
    _ds("1HZ100V", "Volatility 100 (1s) Index", "Synthetic Indices", "Continuous Indices"),
    _ds("BOOM300N", "Boom 300 Index", "Synthetic Indices", "Boom/Crash"),
    _ds("BOOM500", "Boom 500 Index", "Synthetic Indices", "Boom/Crash"),
    _ds("BOOM1000", "Boom 1000 Index", "Synthetic Indices", "Boom/Crash"),
    _ds("CRASH300N", "Crash 300 Index", "Synthetic Indices", "Boom/Crash"),
    _ds("CRASH500", "Crash 500 Index", "Synthetic Indices", "Boom/Crash"),
    _ds("CRASH1000", "Crash 1000 Index", "Synthetic Indices", "Boom/Crash"),
    _ds("stpRNG", "Step Index", "Synthetic Indices", "Step"),
    _ds("JD10", "Jump 10 Index", "Synthetic Indices", "Jump"),
    _ds("JD25", "Jump 25 Index", "Synthetic Indices", "Jump"),
    _ds("JD50", "Jump 50 Index", "Synthetic Indices", "Jump"),
    _ds("JD75", "Jump 75 Index", "Synthetic Indices", "Jump"),
    _ds("JD100", "Jump 100 Index", "Synthetic Indices", "Jump"),
    _ds("RDBEAR", "Bear Market Index", "Synthetic Indices", "Daily Reset"),
    _ds("RDBULL", "Bull Market Index", "Synthetic Indices", "Daily Reset"),
    # Forex majors
    _ds("frxEURUSD", "EUR/USD", "Forex", "Majors"),
    _ds("frxGBPUSD", "GBP/USD", "Forex", "Majors"),
    _ds("frxUSDJPY", "USD/JPY", "Forex", "Majors"),
    _ds("frxAUDUSD", "AUD/USD", "Forex", "Majors"),
    _ds("frxUSDCAD", "USD/CAD", "Forex", "Majors"),
    _ds("frxUSDCHF", "USD/CHF", "Forex", "Majors"),
    _ds("frxNZDUSD", "NZD/USD", "Forex", "Majors"),
    _ds("frxEURGBP", "EUR/GBP", "Forex", "Minors"),
    _ds("frxEURJPY", "EUR/JPY", "Forex", "Minors"),
    _ds("frxGBPJPY", "GBP/JPY", "Forex", "Minors"),
    # Stock indices
    _ds("OTC_SPC", "US 500", "Stock Indices", "Americas"),
    _ds("OTC_NDX", "US Tech 100", "Stock Indices", "Americas"),
    _ds("OTC_DJI", "Wall Street 30", "Stock Indices", "Americas"),
    _ds("OTC_FTSE", "UK 100", "Stock Indices", "Europe"),
    _ds("OTC_GDAXI", "Germany 40", "Stock Indices", "Europe"),
    _ds("OTC_N225", "Japan 225", "Stock Indices", "Asia"),
    _ds("OTC_HSI", "Hong Kong 50", "Stock Indices", "Asia"),
    _ds("OTC_AS51", "Australia 200", "Stock Indices", "Asia"),
]


def _is_deriv_symbol(sym: str) -> bool:
    if not sym:
        return False
    if sym in _deriv_symbol_set:
        return True
    if sym.upper().endswith("USDT"):
        return False
    return sym.startswith(_DERIV_PREFIXES)


async def _deriv_call(payload: dict, timeout: int = 8) -> dict:
    async with websockets.connect(
        DERIV_WS, open_timeout=timeout, close_timeout=5,
        additional_headers={"Origin": "https://app.deriv.com", "User-Agent": "Mozilla/5.0"},
    ) as ws:
        if DERIV_API_TOKEN:
            try:
                await ws.send(json.dumps({"authorize": DERIV_API_TOKEN}))
                await asyncio.wait_for(ws.recv(), timeout)
            except Exception:  # noqa
                pass
        await ws.send(json.dumps(payload))
        resp = json.loads(await asyncio.wait_for(ws.recv(), timeout))
    if resp.get("error"):
        raise RuntimeError(resp["error"].get("message", "deriv error"))
    return resp


async def get_deriv_symbols():
    now = time.time()
    if _deriv_sym_cache["list"] and now - _deriv_sym_cache["t"] < 3600:
        return _deriv_sym_cache["list"]
    if now - _deriv_sym_cache.get("fail_t", 0) < 60:  # negative cache: don't hammer a down server
        fallback = _deriv_sym_cache["list"] or DERIV_FALLBACK_SYMBOLS
        _deriv_symbol_set.update(s["symbol"] for s in fallback)
        return fallback
    try:
        resp = await asyncio.wait_for(
            _deriv_call({"active_symbols": "brief", "product_type": "basic"}, timeout=5), 6)
        syms = resp.get("active_symbols", [])
        out = [{
            "symbol": s["symbol"],
            "label": s.get("display_name", s["symbol"]),
            "market": s.get("market_display_name", "Deriv"),
            "submarket": s.get("submarket_display_name", ""),
        } for s in syms]
        _deriv_sym_cache.update({"t": now, "list": out, "fail_t": 0})
        _deriv_symbol_set.clear()
        _deriv_symbol_set.update(s["symbol"] for s in out)
        return out
    except Exception as e:  # noqa
        _deriv_sym_cache["fail_t"] = now
        logger.warning(f"Deriv active_symbols failed: {e}; serving static instrument list")
        fallback = _deriv_sym_cache["list"] or DERIV_FALLBACK_SYMBOLS
        _deriv_symbol_set.update(s["symbol"] for s in fallback)
        return fallback


async def _get_deriv_candles(symbol: str, timeframe: str, limit: int, closed_only: bool):
    gran = TIMEFRAMES[timeframe][2]
    count = max(50, min(limit + 1, 1000))
    resp = await _deriv_call({"ticks_history": symbol, "end": "latest",
                              "count": count, "style": "candles", "granularity": gran})
    rows = resp.get("candles", [])
    candles = [{"t": int(c["epoch"]) * 1000, "o": float(c["open"]), "h": float(c["high"]),
                "l": float(c["low"]), "c": float(c["close"]), "v": 0.0} for c in rows]
    if closed_only and len(candles) > 1:
        candles = candles[:-1]
    candles = candles[-limit:]
    if candles:
        health.update({"last_ok": time.time(), "source": "deriv", "error": None})
    return candles

_cache: dict = {}
_CACHE_TTL = 25  # seconds

# source health: {"last_ok": ts, "source": str, "error": str|None}
health = {"last_ok": None, "source": None, "error": None}


def _to_coinbase_product(symbol: str) -> str:
    s = symbol.upper()
    for quote in ("USDT", "USD", "USDC"):
        if s.endswith(quote):
            base = s[: -len(quote)]
            cb_quote = "USD" if quote in ("USDT", "USDC") else quote
            return f"{base}-{cb_quote}"
    return s


async def _fetch_binance(symbol: str, timeframe: str, limit: int):
    interval = TIMEFRAMES[timeframe][0]
    url = f"{BINANCE_BASE}/klines"
    params = {"symbol": symbol.upper(), "interval": interval, "limit": min(limit + 1, 1000)}
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        data = r.json()
    candles = [
        {"t": int(k[0]), "o": float(k[1]), "h": float(k[2]),
         "l": float(k[3]), "c": float(k[4]), "v": float(k[5])}
        for k in data
    ]
    return candles


async def _fetch_coinbase(symbol: str, timeframe: str, limit: int):
    gran = TIMEFRAMES[timeframe][1]
    product = _to_coinbase_product(symbol)
    url = f"{COINBASE_BASE}/products/{product}/candles"
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "Mozilla/5.0"}) as client:
        r = await client.get(url, params={"granularity": gran})
        r.raise_for_status()
        data = r.json()
    # coinbase: [time(sec), low, high, open, close, volume], newest first
    rows = sorted(data, key=lambda x: x[0])
    candles = [
        {"t": int(row[0]) * 1000, "o": float(row[3]), "h": float(row[2]),
         "l": float(row[1]), "c": float(row[4]), "v": float(row[5])}
        for row in rows
    ]
    return candles


async def get_candles(symbol: str, timeframe: str, limit: int = 300, closed_only: bool = True):
    """Return list of candle dicts (ascending by time). Closed candles only."""
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"Unsupported timeframe: {timeframe}")

    if _is_deriv_symbol(symbol):
        return await _get_deriv_candles(symbol, timeframe, limit, closed_only)

    key = f"{symbol.upper()}:{timeframe}:{limit}:{closed_only}"
    now = time.time()
    cached = _cache.get(key)
    if cached and now - cached[0] < _CACHE_TTL:
        return cached[1]

    errors = []
    candles = None
    source = None
    for name, fetch in (("binance.us", _fetch_binance), ("coinbase", _fetch_coinbase)):
        for attempt in range(2):
            try:
                candles = await fetch(symbol, timeframe, limit)
                source = name
                break
            except Exception as e:  # noqa
                errors.append(f"{name}: {e}")
                await asyncio.sleep(0.4)
        if candles:
            break

    if not candles:
        health["error"] = "; ".join(errors[-2:])
        logger.error(f"Market data fetch failed for {symbol} {timeframe}: {health['error']}")
        if cached:
            return cached[1]  # serve stale as last resort
        raise RuntimeError(f"Could not fetch market data: {health['error']}")

    if closed_only and len(candles) > 1:
        candles = candles[:-1]  # drop currently-forming candle
    candles = candles[-limit:]

    health.update({"last_ok": now, "source": source, "error": None})
    _cache[key] = (now, candles)
    return candles


_live_cache: dict = {}
_LIVE_TTL = 3  # seconds — near-real-time price/forming candle


async def get_live(symbol: str, timeframe: str):
    """Current price + the in-progress (forming) candle. Short TTL for near-real-time.
    Deriv may be unreachable here; returns price=None gracefully in that case."""
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    key = f"{symbol.upper()}:{timeframe}"
    now = time.time()
    cached = _live_cache.get(key)
    if cached and now - cached[0] < _LIVE_TTL:
        return cached[1]

    candle = None
    source = None
    if _is_deriv_symbol(symbol):
        try:
            rows = await asyncio.wait_for(
                _get_deriv_candles(symbol, timeframe, 2, closed_only=False), 6)
            if rows:
                candle, source = rows[-1], "deriv"
        except Exception as e:  # noqa
            logger.warning(f"Deriv live fetch failed for {symbol}: {e}")
    else:
        for name, fetch in (("binance.us", _fetch_binance), ("coinbase", _fetch_coinbase)):
            try:
                rows = await fetch(symbol, timeframe, 2)  # includes the forming candle
                if rows:
                    candle, source = rows[-1], name
                    break
            except Exception as e:  # noqa
                logger.warning(f"{name} live fetch failed for {symbol}: {e}")

    out = {"symbol": symbol, "timeframe": timeframe,
           "price": (candle["c"] if candle else None),
           "candle": candle, "source": source, "server_ts": int(now * 1000)}
    _live_cache[key] = (now, out)
    return out


async def get_last_price(symbol: str):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{BINANCE_BASE}/ticker/price", params={"symbol": symbol.upper()})
            r.raise_for_status()
            return float(r.json()["price"])
    except Exception:
        candles = await get_candles(symbol, "1m", 2)
        return candles[-1]["c"] if candles else None
