"""Market data — Deriv MT5 via MetaApi ONLY. No other data source.

Candles are served from a warm cache (in-memory + MongoDB) that a background
poller keeps fresh, so charts load instantly and keep showing the last known
MT5 data even during a brief MT5 reconnect. There is no Binance/Coinbase/Yahoo
fallback — every price shown comes from Deriv MT5."""
import time
import asyncio
import logging

from db import db
import mt5_trader

logger = logging.getLogger(__name__)

# timeframe -> seconds per candle (used for resampling/live windows)
TIMEFRAMES = {
    "1m": 60, "5m": 300, "15m": 900,
    "1h": 3600, "4h": 14400, "1d": 86400,
}


class DerivUnavailable(RuntimeError):
    """Deriv MT5 market data is momentarily unreachable."""


# ---------------- MT5 universe (the Deriv symbols the account can trade) ----------------
_mt5_universe_set: set = set()
_mt5_options_cache = {"t": 0.0, "options": None}
_CRYPTO_BASES = ("BTC", "ETH", "LTC", "XRP", "BCH", "EOS", "BNB", "ADA", "SOL",
                 "DOGE", "DOT", "LINK", "AVAX", "MATIC", "XLM", "TRX", "UNI", "ALGO")
_SYNTH_KW = ("volatility", "boom", "crash", "step index", "jump ", "range break",
             "drift switch", "dex ", "multi step", "hybrid", "jump index")

# A small static MT5 instrument list so the picker is never empty before the
# live universe has loaded. These are real Deriv MT5 symbol names.
_STATIC_MT5 = [
    "XAUUSD", "BTCUSD", "ETHUSD", "Volatility 100 Index", "Volatility 75 Index",
    "Volatility 50 Index", "Volatility 25 Index", "Volatility 10 Index",
    "Boom 1000 Index", "Crash 1000 Index", "Step Index", "EURUSD", "GBPUSD",
    "USDJPY", "US 500", "US Tech 100",
]


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


def _opts_from(syms):
    return [{"symbol": s, "label": s, "market": _mt5_category(s), "source": "deriv-mt5"} for s in syms]


async def build_mt5_options():
    """Fetch the live Deriv MT5 symbol universe and categorize it. Cached 1h.
    Falls back to a small static MT5 list (still MT5 names) until it loads."""
    if not mt5_trader.is_configured():
        return _opts_from(_STATIC_MT5)
    now = time.time()
    if _mt5_options_cache["options"] and now - _mt5_options_cache["t"] < 3600:
        return _mt5_options_cache["options"]
    try:
        syms = await mt5_trader.get_universe()
    except Exception as e:  # noqa
        logger.warning(f"MT5 universe fetch failed: {e}")
        return _mt5_options_cache["options"] or _opts_from(_STATIC_MT5)
    global _mt5_universe_set
    _mt5_universe_set = set(syms)
    opts = _opts_from(syms)
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


# ---------------- warm cache (in-memory + MongoDB) ----------------
_SYNTH_PREFIXES = ("R_", "1HZ", "BOOM", "CRASH", "stpRNG", "JD", "RDBULL", "RDBEAR", "WLD")


def _is_synthetic(sym: str) -> bool:
    return bool(sym) and sym.startswith(_SYNTH_PREFIXES)


def _deriv_msg(symbol: str) -> str:
    if _is_synthetic(symbol):
        return ("Synthetic & Volatility indices come from Deriv — they appear "
                "automatically once the app can reach Deriv MT5.")
    return "Live Deriv MT5 data for this market is momentarily unavailable."


_cache: dict = {}            # mkey -> (ts, candles[500])
_CACHE_TTL = 15              # seconds a fresh in-memory candle set is reused
_STORE_MAX = 500            # how many candles we keep warm per symbol/timeframe
_recent: dict = {}          # mkey -> {"symbol","timeframe","msym","ts"} viewed recently
_RECENT_TTL = 900           # keep warming a chart for 15 min after last view
_candles_refreshing: set = set()   # mkeys with an in-flight background refresh

# source health: {"last_ok": ts, "source": str, "error": str|None}
health = {"last_ok": None, "source": "deriv-mt5", "error": None}


def _mkey(msym: str, timeframe: str) -> str:
    return f"mt5:{msym}:{timeframe}"


async def _db_load(mkey: str):
    doc = await db.candles_cache.find_one({"_id": mkey}, {"_id": 0, "candles": 1, "ts": 1})
    if doc and doc.get("candles"):
        return doc["ts"], doc["candles"]
    return None


async def _db_save(mkey: str, msym: str, timeframe: str, candles: list):
    await db.candles_cache.update_one(
        {"_id": mkey},
        {"$set": {"symbol": msym, "timeframe": timeframe,
                  "candles": candles[-_STORE_MAX:], "ts": time.time()}},
        upsert=True)


async def _fetch_mt5(msym: str, timeframe: str) -> list:
    rows = await asyncio.wait_for(
        mt5_trader.candles(msym, timeframe, _STORE_MAX, closed_only=True), 18)
    if rows:
        health.update({"last_ok": time.time(), "source": "deriv-mt5", "error": None})
    return rows or []


async def _refresh_candles(mkey: str, msym: str, timeframe: str):
    """Background candle refresh (never blocks a request)."""
    try:
        rows = await _fetch_mt5(msym, timeframe)
        if rows:
            _cache[mkey] = (time.time(), rows)
            await _db_save(mkey, msym, timeframe, rows)
    except Exception as e:  # noqa
        logger.warning(f"candle refresh {msym} {timeframe}: {e}")
        mt5_trader.ensure_bg()
    finally:
        _candles_refreshing.discard(mkey)


def _trigger_candle_refresh(mkey: str, msym: str, timeframe: str):
    """Kick off a non-blocking refresh if MT5 is ready and none is in flight."""
    if not mt5_trader.is_ready():
        mt5_trader.ensure_bg()
        return
    if mkey in _candles_refreshing:
        return
    _candles_refreshing.add(mkey)
    asyncio.create_task(_refresh_candles(mkey, msym, timeframe))


async def get_candles(symbol: str, timeframe: str, limit: int = 300, closed_only: bool = True):
    """Return closed candle dicts (ascending) from Deriv MT5 ONLY.
    Stale-while-revalidate: always answer instantly from the warm cache and
    refresh in the background, so a slow MT5 RPC never stalls the chart. Only a
    true cold start (no warm data anywhere) does a blocking fetch."""
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"Unsupported timeframe: {timeframe}")

    msym = mt5_ready_symbol(symbol)
    if not msym:
        raise DerivUnavailable(_deriv_msg(symbol))

    mkey = _mkey(msym, timeframe)
    now = time.time()
    _recent[mkey] = {"symbol": symbol, "timeframe": timeframe, "msym": msym, "ts": now}

    # 1) in-memory warm cache — return instantly, refresh in the background if stale
    mc = _cache.get(mkey)
    if mc:
        if now - mc[0] >= _CACHE_TTL:
            _trigger_candle_refresh(mkey, msym, timeframe)
        return mc[1][-limit:]

    # 2) no memory cache — hydrate from DB instantly, then refresh in background
    db_hit = await _db_load(mkey)
    if db_hit:
        _cache[mkey] = (db_hit[0], db_hit[1])
        _trigger_candle_refresh(mkey, msym, timeframe)
        return db_hit[1][-limit:]

    # 3) cold start — nothing warm anywhere; one blocking fetch for first paint
    if mt5_trader.is_ready():
        try:
            rows = await _fetch_mt5(msym, timeframe)
            if rows:
                _cache[mkey] = (now, rows)
                asyncio.create_task(_db_save(mkey, msym, timeframe, rows))
                return rows[-limit:]
        except Exception as e:  # noqa
            logger.warning(f"MT5 candles failed {symbol}->{msym}: {e}")
            health["error"] = str(e)
    mt5_trader.ensure_bg()
    raise DerivUnavailable(_deriv_msg(symbol))


_live_cache: dict = {}
_LIVE_TTL = 2  # seconds — serve this fast from cache; refresh in the background
_live_refreshing: set = set()


async def _refresh_live(symbol: str, timeframe: str, key: str, msym: str):
    """Fetch the latest tick + forming candle in parallel and update the cache.
    Runs in the background so the HTTP poll never waits on a slow MT5 RPC."""
    try:
        lp, rows = await asyncio.gather(
            asyncio.wait_for(mt5_trader.live_price(msym), 6),
            asyncio.wait_for(mt5_trader.candles(msym, timeframe, 2, closed_only=False), 8),
        )
        candle = rows[-1] if rows else None
        price = (lp or {}).get("price") or (candle["c"] if candle else None)
        if candle and price is not None:
            candle = {**candle, "c": price,
                      "h": max(candle["h"], price), "l": min(candle["l"], price)}
        if price is not None:
            now = time.time()
            _live_cache[key] = (now, {
                "symbol": symbol, "timeframe": timeframe, "price": price,
                "candle": candle, "source": "deriv-mt5", "available": True,
                "server_ts": int(now * 1000)})
            health.update({"last_ok": now, "source": "deriv-mt5", "error": None})
    except Exception as e:  # noqa
        logger.warning(f"MT5 live refresh failed {symbol}->{msym}: {e}")
        mt5_trader.ensure_bg()
    finally:
        _live_refreshing.discard(key)


async def get_live(symbol: str, timeframe: str):
    """Current price + in-progress candle from Deriv MT5 ONLY.
    Stale-while-revalidate: returns the last tick instantly and refreshes in the
    background, so the chart keeps polling snappily even if MT5 is slow."""
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    key = f"{symbol.upper()}:{timeframe}"
    now = time.time()
    cached = _live_cache.get(key)
    msym = mt5_ready_symbol(symbol)
    ready = bool(msym) and mt5_trader.is_ready()

    if cached:
        if now - cached[0] >= _LIVE_TTL and ready and key not in _live_refreshing:
            _live_refreshing.add(key)
            asyncio.create_task(_refresh_live(symbol, timeframe, key, msym))
        elif not ready:
            mt5_trader.ensure_bg()
        return {**cached[1], "server_ts": int(now * 1000)}

    # cold start — one blocking fetch so the first paint has a price
    if ready:
        await _refresh_live(symbol, timeframe, key, msym)
        hit = _live_cache.get(key)
        if hit:
            return hit[1]
    else:
        mt5_trader.ensure_bg()

    out = {"symbol": symbol, "timeframe": timeframe, "price": None, "candle": None,
           "source": None, "available": False,
           "reason": _deriv_msg(symbol), "server_ts": int(now * 1000)}
    _live_cache[key] = (now, out)
    return out


async def get_last_price(symbol: str):
    candles = await get_candles(symbol, "1m", 2)
    return candles[-1]["c"] if candles else None


# ---------------- background warmer (called by the scheduler) ----------------
# Core instruments we always keep warm on the day-trading timeframe.
_CORE_WARM = [("XAUUSD", "15m"), ("BTCUSD", "15m"), ("Volatility 100 Index", "15m")]


async def warm_candles():
    """Refresh the warm cache for core instruments + whatever charts the user is
    viewing, so chart requests stay instant and the last candle keeps advancing.
    MT5 only. Safe to call on a loop."""
    if not mt5_trader.is_configured() or not mt5_trader.is_ready():
        mt5_trader.ensure_bg()
        return 0
    now = time.time()
    targets = {}
    for sym, tf in _CORE_WARM:
        msym = mt5_ready_symbol(sym)
        if msym:
            targets[_mkey(msym, tf)] = (msym, tf)
    for mkey, info in list(_recent.items()):
        if now - info["ts"] <= _RECENT_TTL and info.get("msym"):
            targets[mkey] = (info["msym"], info["timeframe"])
    warmed = 0
    # cap simultaneous MetaApi fetches to keep subscription load light
    for mkey, (msym, tf) in list(targets.items())[:6]:
        try:
            rows = await _fetch_mt5(msym, tf)
            if rows:
                _cache[mkey] = (time.time(), rows)
                await _db_save(mkey, msym, tf, rows)
                warmed += 1
        except Exception as e:  # noqa
            logger.debug(f"warm_candles {msym} {tf} skipped: {e}")
    return warmed
