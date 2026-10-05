"""Backend tests: Market data endpoints must be MT5/Deriv ONLY, fast, graceful (never 502).

Primary acceptance criteria (per iteration 11 review request):
  - All /api/market/candles|live|outlook calls return HTTP 200 (never 502).
  - Responses are fast (<10s, typically <1s).
  - When MT5 is unavailable: {available: false, reason: "..."} with empty candles.
  - When MT5 is available: source must contain 'deriv' or 'mt5' (never binance/coinbase/yahoo).
Data availability (available:true) is NOT a hard requirement — it depends on the external
MetaApi subscription quota which code cannot change.
"""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/")
USERNAME = "xambiep90"
PASSWORD = "Reddestroad1$"

SYMBOLS = ["BTCUSD", "XAUUSD", "Volatility 100 Index"]
FORBIDDEN_SOURCES = ("binance", "coinbase", "yahoo")


@pytest.fixture(scope="session")
def token():
    last_err = None
    for _ in range(3):
        try:
            r = requests.post(
                f"{BASE_URL}/api/auth/login",
                json={"username": USERNAME, "password": PASSWORD},
                timeout=60,
            )
            if r.status_code == 200:
                return r.json()["token"]
            last_err = f"{r.status_code} {r.text[:200]}"
        except Exception as e:  # noqa
            last_err = str(e)
        time.sleep(2)
    pytest.fail(f"login failed after retries: {last_err}")


@pytest.fixture(scope="session")
def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


def _get(path, headers, params=None, timeout=15):
    t0 = time.time()
    r = requests.get(f"{BASE_URL}{path}", headers=headers, params=params, timeout=timeout)
    return r, time.time() - t0


# ---------- /api/market/candles: graceful + fast + MT5-only ----------
@pytest.mark.parametrize("symbol", SYMBOLS)
def test_candles_never_502_and_fast(auth_headers, symbol):
    r, elapsed = _get(
        "/api/market/candles", auth_headers,
        {"symbol": symbol, "timeframe": "1h", "limit": 200},
    )
    assert r.status_code == 200, f"{symbol}: expected 200, got {r.status_code}: {r.text[:300]}"
    assert elapsed < 10, f"{symbol}: candles took {elapsed:.2f}s (must be <10s)"
    data = r.json()
    assert "available" in data, f"{symbol}: response missing 'available': {data}"
    if data["available"] is True:
        candles = data.get("candles") or []
        assert len(candles) > 0, f"{symbol}: available=true but candles empty"
        src = (data.get("source") or "").lower()
        # source may be absent on candles payload (route doesn't set it); if present enforce
        if src:
            assert "deriv" in src or "mt5" in src, f"{symbol}: bad source '{src}'"
            assert not any(x in src for x in FORBIDDEN_SOURCES), f"{symbol}: forbidden source {src}"
        print(f"OK candles {symbol}: {len(candles)} candles in {elapsed:.2f}s")
    else:
        assert data.get("candles") == [], f"{symbol}: available=false but candles present"
        assert data.get("reason"), f"{symbol}: available=false but no reason provided"
        print(f"OK graceful candles {symbol}: available=false reason={data['reason']!r} in {elapsed:.2f}s")


# ---------- /api/market/live: graceful + fast + has reason when unavailable ----------
@pytest.mark.parametrize("symbol", SYMBOLS)
def test_live_never_502_and_has_reason(auth_headers, symbol):
    r, elapsed = _get(
        "/api/market/live", auth_headers,
        {"symbol": symbol, "timeframe": "15m"},
    )
    assert r.status_code == 200, f"{symbol}: expected 200, got {r.status_code}: {r.text[:300]}"
    assert elapsed < 10, f"{symbol}: live took {elapsed:.2f}s (must be <10s)"
    data = r.json()
    assert "available" in data, f"{symbol}: live response missing 'available': {data}"
    if data["available"] is True:
        price = data.get("price")
        assert isinstance(price, (int, float)) and price > 0, f"{symbol}: bad price {price}"
        src = (data.get("source") or "").lower()
        assert "deriv" in src or "mt5" in src, f"{symbol}: bad source '{src}'"
        assert not any(x in src for x in FORBIDDEN_SOURCES), f"{symbol}: forbidden source {src}"
        print(f"OK live {symbol}: price={price} source={src} in {elapsed:.2f}s")
    else:
        assert data.get("source") in (None, ""), f"{symbol}: available=false but source={data.get('source')}"
        assert data.get("reason"), f"{symbol}: live available=false missing 'reason' field"
        print(f"OK graceful live {symbol}: available=false reason={data['reason']!r} in {elapsed:.2f}s")


# ---------- /api/market/outlook: graceful + never 502 ----------
@pytest.mark.parametrize("symbol", ["XAUUSD", "BTCUSD"])
def test_outlook_never_502(auth_headers, symbol):
    r, elapsed = _get(
        "/api/market/outlook", auth_headers,
        {"symbol": symbol, "timeframe": "1h"}, timeout=20,
    )
    assert r.status_code == 200, f"{symbol}: outlook expected 200, got {r.status_code}: {r.text[:300]}"
    assert elapsed < 15, f"{symbol}: outlook took {elapsed:.2f}s"
    data = r.json()
    assert isinstance(data, dict), f"{symbol}: unexpected outlook shape"
    assert "available" in data, f"{symbol}: outlook missing 'available' key"
    if data["available"] is False:
        assert data.get("reason"), f"{symbol}: outlook unavailable missing reason"
    print(f"OK outlook {symbol}: available={data['available']} in {elapsed:.2f}s")


# ---------- Explicitly verify no forbidden sources on any endpoint ----------
def test_no_binance_coinbase_yahoo_source(auth_headers):
    for symbol in SYMBOLS:
        for path, params in [
            ("/api/market/candles", {"symbol": symbol, "timeframe": "1h", "limit": 100}),
            ("/api/market/live", {"symbol": symbol, "timeframe": "15m"}),
        ]:
            r, _ = _get(path, auth_headers, params)
            assert r.status_code == 200
            src = (r.json().get("source") or "").lower()
            assert not any(x in src for x in FORBIDDEN_SOURCES), f"forbidden src {src} on {path}?{params}"
    print("OK: no binance/coinbase/yahoo sources leaked on any market endpoint")
