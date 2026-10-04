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
from urllib.parse import quote as urlquote

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


# ---------------- Deriv MT5 via MetaApi (real Deriv prices for ALL items) ----------------
import mt5_trader  # noqa: E402

_mt5_universe_set: set = set()
_mt5_options_cache = {"t": 0.0, "options": None}
_CRYPTO_BASES = ("BTC", "ETH", "LTC", "XRP", "BCH", "EOS", "BNB", "ADA", "SOL",
                 "DOGE", "DOT", "LINK", "AVAX", "MATIC", "XLM", "TRX", "UNI", "ALGO")
_SYNTH_KW = ("volatility", "boom", "crash", "step index", "jump ", "range break",
             "drift switch", "dex ", "multi step", "hybrid", "jump index")


def _mt5_category(name: str) -> str:
    n = name.lower()
    if any(k in n for k in _SYNTH_KW):
        return "Synthetic & Volatility"
    if name[:3] in ("XAU", "XAG", "XPT", "XPD") or any(k in n for k in ("gold", "silver", "platinum", "palladium")):
        return "Metals"
    if any(name.startswith(b) for b in _CRYPTO_BASES):
        return "Crypto"
    if name.endswith(".US"):
        return "Stocks"
    if "index" in n or any(k in n for k in ("wall street", "us 500", "us tech", "nasdaq", "germany",
                                            "japan", "uk 100", "australia", "france", "netherlands",
                                            "hong kong", "us 30", "swiss", "spain", "europe 50")):
        return "Stock Indices"
    if any(k in n for k in ("oil", "wti", "brent", "natural gas")):
        return "Energies"
    if len(name) == 6 and name.isalpha() and name.isupper():
        return "Forex"
    if "/" in name:
        return "Forex"
    return "Other"


async def build_mt5_options():
    """Fetch the live Deriv MT5 symbol universe and categorize it. Cached 1h."""
    if not mt5_trader.is_configured():
        return None
    now = time.time()
    if _mt5_options_cache["options"] and now - _mt5_options_cache["t"] < 3600:
        return _mt5_options_cache["options"]
    try:
        syms = await mt5_trader.get_universe()
    except Exception as e:  # noqa
        logger.warning(f"MT5 universe fetch failed: {e}")
        return _mt5_options_cache["options"]
    global _mt5_universe_set
    _mt5_universe_set = set(syms)
    opts = [{"symbol": s, "label": s, "market": _mt5_category(s), "source": "deriv-mt5"} for s in syms]
    _mt5_options_cache.update({"t": now, "options": opts})
    return opts


async def warm_mt5_universe():
    try:
        await build_mt5_options()
        logger.info(f"MT5 universe ready: {len(_mt5_universe_set)} symbols")
    except Exception:  # noqa
        pass


def mt5_ready_symbol(symbol: str):
    """Resolve our symbol to its MT5 symbol. Not gated on live-connection state —
    get_candles reconnects on demand; this just maps the name so a transient MT5
    drop doesn't divert us anywhere else (we are MT5/Deriv-only)."""
    if not mt5_trader.is_configured():
        return None
    if symbol in _mt5_universe_set:
        return symbol
    return mt5_trader.mt5_symbol(symbol)


# ---------------- Deriv (forex, commodities, crypto, indices, synthetics) ----------------
DERIV_APP_ID = os.environ.get("DERIV_APP_ID", "1089")
DERIV_API_TOKEN = os.environ.get("DERIV_API_TOKEN")
DERIV_WS = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"
_DERIV_PREFIXES = ("R_", "1HZ", "frx", "cry", "OTC_", "WLD", "JD", "stpRNG",
                   "BOOM", "CRASH", "RDBULL", "RDBEAR")


class DerivUnavailable(RuntimeError):
    """Deriv market data source is currently unreachable (expected in some networks)."""


_deriv_block_until = 0.0


def _deriv_blocked() -> bool:
    return time.time() < _deriv_block_until


def _block_deriv(seconds: int = 60):
    global _deriv_block_until
    _deriv_block_until = time.time() + seconds
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


# ---------------- Yahoo Finance (real data for Gold, crypto, forex, indices) ----------------
# Synthetic/Volatility indices are Deriv-only and have no external source.
_SYNTH_PREFIXES = ("R_", "1HZ", "BOOM", "CRASH", "stpRNG", "JD", "RDBULL", "RDBEAR", "WLD")

# Deriv symbol -> Yahoo Finance ticker (real-world equivalent)
DERIV_TO_YAHOO = {
    "frxXAUUSD": "GC=F", "frxXAGUSD": "SI=F", "frxXPTUSD": "PL=F", "frxXPDUSD": "PA=F",
    "cryBTCUSD": "BTC-USD", "cryETHUSD": "ETH-USD", "cryLTCUSD": "LTC-USD",
    "cryXRPUSD": "XRP-USD", "cryBCHUSD": "BCH-USD", "cryEOSUSD": "EOS-USD",
    "frxEURUSD": "EURUSD=X", "frxGBPUSD": "GBPUSD=X", "frxUSDJPY": "USDJPY=X",
    "frxAUDUSD": "AUDUSD=X", "frxUSDCAD": "USDCAD=X", "frxUSDCHF": "USDCHF=X",
    "frxNZDUSD": "NZDUSD=X", "frxEURGBP": "EURGBP=X", "frxEURJPY": "EURJPY=X",
    "frxGBPJPY": "GBPJPY=X",
    "OTC_SPC": "^GSPC", "OTC_NDX": "^NDX", "OTC_DJI": "^DJI", "OTC_FTSE": "^FTSE",
    "OTC_GDAXI": "^GDAXI", "OTC_N225": "^N225", "OTC_HSI": "^HSI", "OTC_AS51": "^AXJO",
}

# timeframe -> (yahoo interval, yahoo range)
YF_TF = {
    "1m": ("1m", "5d"), "5m": ("5m", "1mo"), "15m": ("15m", "1mo"),
    "1h": ("60m", "3mo"), "4h": ("60m", "6mo"), "1d": ("1d", "1y"),
}


def _is_synthetic(sym: str) -> bool:
    return bool(sym) and sym.startswith(_SYNTH_PREFIXES)


def _yahoo_for(symbol: str):
    return DERIV_TO_YAHOO.get(symbol)


def _deriv_msg(symbol: str) -> str:
    if _is_synthetic(symbol):
        return ("Synthetic & Volatility indices are generated only by Deriv — they come online "
                "automatically once the app can reach Deriv (works from the deployed site).")
    return "Live data for this market is temporarily unavailable."


def _resample(candles: list, seconds: int) -> list:
    out, order = {}, []
    for c in candles:
        b = ((int(c["t"]) // 1000) // seconds) * seconds * 1000
        if b not in out:
            out[b] = {"t": b, "o": c["o"], "h": c["h"], "l": c["l"], "c": c["c"], "v": c["v"]}
            order.append(b)
        else:
            x = out[b]
            x["h"] = max(x["h"], c["h"])
            x["l"] = min(x["l"], c["l"])
            x["c"] = c["c"]
            x["v"] += c["v"]
    return [out[b] for b in order]


async def _fetch_yahoo(ticker: str, timeframe: str, limit: int, closed_only: bool = True):
    interval, rng = YF_TF[timeframe]
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urlquote(ticker, safe='')}"
    async with httpx.AsyncClient(timeout=12, headers={"User-Agent": "Mozilla/5.0"}) as client:
        r = await client.get(url, params={"interval": interval, "range": rng})
        r.raise_for_status()
        data = r.json()
    res = (data.get("chart", {}).get("result") or [None])[0]
    if not res:
        return []
    ts = res.get("timestamp") or []
    q = (res.get("indicators", {}).get("quote") or [{}])[0]
    o, h, l, c, v = (q.get("open", []), q.get("high", []), q.get("low", []),
                     q.get("close", []), q.get("volume", []))
    candles = []
    for i in range(len(ts)):
        if i >= len(c) or c[i] is None or o[i] is None or h[i] is None or l[i] is None:
            continue
        candles.append({"t": int(ts[i]) * 1000, "o": float(o[i]), "h": float(h[i]),
                        "l": float(l[i]), "c": float(c[i]),
                        "v": float(v[i]) if i < len(v) and v[i] is not None else 0.0})
    if timeframe == "4h":
        candles = _resample(candles, 14400)
    if closed_only and len(candles) > 1:
        candles = candles[:-1]
    return candles[-limit:]


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
    """Return candle dicts (ascending). MT5/Deriv ONLY — no other data sources."""
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"Unsupported timeframe: {timeframe}")

    msym = mt5_ready_symbol(symbol)
    if not msym:
        raise DerivUnavailable(_deriv_msg(symbol))

    mkey = f"mt5:{msym}:{timeframe}:{limit}:{closed_only}"
    mnow = time.time()
    mc = _cache.get(mkey)
    if mc and mnow - mc[0] < _CACHE_TTL:
        return mc[1]
    # Never block a chart request on a slow (re)connect: if MT5 isn't ready yet,
    # kick off a background connect and serve cached data (or a friendly message).
    if not mt5_trader.is_ready():
        mt5_trader.ensure_bg()
        if mc:
            return mc[1]
        raise DerivUnavailable(_deriv_msg(symbol))
    try:
        rows = await asyncio.wait_for(
            mt5_trader.candles(msym, timeframe, limit, closed_only), 18)
        if rows:
            health.update({"last_ok": mnow, "source": "deriv-mt5", "error": None})
            _cache[mkey] = (mnow, rows)
            return rows
    except Exception as e:  # noqa
        logger.warning(f"MT5 candles failed {symbol}->{msym}: {e}")
        mt5_trader.ensure_bg()
        if mc:
            return mc[1]  # serve stale during a transient MT5 reconnect
        health["error"] = str(e)
        raise DerivUnavailable(_deriv_msg(symbol)) from e
    if mc:
        return mc[1]
    raise DerivUnavailable(_deriv_msg(symbol))


_live_cache: dict = {}
_LIVE_TTL = 3  # seconds — near-real-time price/forming candle


async def get_live(symbol: str, timeframe: str):
    """Current price + in-progress candle from MT5/Deriv ONLY. Short TTL."""
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    key = f"{symbol.upper()}:{timeframe}"
    now = time.time()
    cached = _live_cache.get(key)
    if cached and now - cached[0] < _LIVE_TTL:
        return cached[1]

    msym = mt5_ready_symbol(symbol)
    if msym and mt5_trader.is_ready():
        try:
            lp = await asyncio.wait_for(mt5_trader.live_price(msym), 10)
            rows = await asyncio.wait_for(
                mt5_trader.candles(msym, timeframe, 2, closed_only=False), 12)
            candle = rows[-1] if rows else None
            price = lp.get("price") or (candle["c"] if candle else None)
            if candle and price is not None:
                candle = {**candle, "c": price,
                          "h": max(candle["h"], price), "l": min(candle["l"], price)}
            out = {"symbol": symbol, "timeframe": timeframe, "price": price,
                   "candle": candle, "source": "deriv-mt5", "available": True,
                   "server_ts": int(now * 1000)}
            _live_cache[key] = (now, out)
            return out
        except Exception as e:  # noqa
            logger.warning(f"MT5 live failed {symbol}->{msym}: {e}")
            mt5_trader.ensure_bg()
            if cached:
                return cached[1]  # serve last known price during a transient drop
    elif msym:
        mt5_trader.ensure_bg()
        if cached:
            return cached[1]

    out = {"symbol": symbol, "timeframe": timeframe, "price": None, "candle": None,
           "source": None, "available": False, "server_ts": int(now * 1000)}
    _live_cache[key] = (now, out)
    return out


async def get_last_price(symbol: str):
    candles = await get_candles(symbol, "1m", 2)
    return candles[-1]["c"] if candles else None
