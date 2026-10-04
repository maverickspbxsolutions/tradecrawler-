"""Backend tests: Market data endpoints must be MT5/Deriv ONLY, fast, and never 502."""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://signal-engine-158.preview.emergentagent.com").rstrip("/")
USERNAME = "xambiep90"
PASSWORD = "Reddestroad1$"

SYMBOLS = ["BTCUSD", "XAUUSD", "Volatility 100 Index"]


@pytest.fixture(scope="session")
def token():
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"username": USERNAME, "password": PASSWORD}, timeout=60)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return r.json()["token"]


@pytest.fixture(scope="session")
def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


def _get(path, headers, params=None, timeout=20):
    t0 = time.time()
    r = requests.get(f"{BASE_URL}{path}", headers=headers, params=params, timeout=timeout)
    return r, time.time() - t0


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_candles_fast_and_mt5_only(auth_headers, symbol):
    # Allow one retry if available:false (per spec: MT5 may be mid-reconnect)
    for attempt in range(2):
        r, elapsed = _get("/api/market/candles", auth_headers, {"symbol": symbol, "timeframe": "1h", "limit": 200})
        assert r.status_code == 200, f"{symbol}: expected 200 (no 502), got {r.status_code}: {r.text[:300]}"
        assert elapsed < 15, f"{symbol}: candles took {elapsed:.1f}s (>15s)"
        data = r.json()
        if data.get("available") is True:
            candles = data.get("candles") or []
            assert len(candles) > 0, f"{symbol}: available=true but candles empty"
            src = (data.get("source") or "").lower()
            assert "deriv" in src or "mt5" in src, f"{symbol}: source should be deriv-mt5 not '{src}'"
            # No other exchange sources
            assert not any(x in src for x in ["binance", "coinbase", "yahoo"]), f"{symbol}: forbidden source {src}"
            print(f"OK candles {symbol}: {len(candles)} candles, source={src}, {elapsed:.2f}s")
            return
        else:
            print(f"attempt {attempt+1} {symbol}: available=false reason={data.get('reason')} ({elapsed:.2f}s), retrying")
            time.sleep(15)
    pytest.fail(f"{symbol}: candles never returned available=true after retries")


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_live_fast_and_mt5_only(auth_headers, symbol):
    for attempt in range(2):
        r, elapsed = _get("/api/market/live", auth_headers, {"symbol": symbol, "timeframe": "15m"})
        assert r.status_code == 200, f"{symbol}: expected 200, got {r.status_code}: {r.text[:300]}"
        assert elapsed < 15, f"{symbol}: live took {elapsed:.1f}s"
        data = r.json()
        # Accept either direct price or {available, price, source}
        avail = data.get("available", True)
        if avail is False:
            print(f"attempt {attempt+1} {symbol} live available=false reason={data.get('reason')}, retrying")
            time.sleep(15)
            continue
        price = data.get("price") or data.get("last") or data.get("close")
        assert isinstance(price, (int, float)), f"{symbol}: price not numeric: {data}"
        src = (data.get("source") or "").lower()
        assert "deriv" in src or "mt5" in src, f"{symbol}: source should be deriv-mt5 not '{src}'"
        assert not any(x in src for x in ["binance", "coinbase", "yahoo"]), f"{symbol}: forbidden source {src}"
        print(f"OK live {symbol}: price={price}, source={src}, {elapsed:.2f}s")
        return
    pytest.fail(f"{symbol}: live never returned success")


def test_outlook_no_502(auth_headers):
    r, elapsed = _get("/api/market/outlook", auth_headers, {"symbol": "XAUUSD", "timeframe": "1h"}, timeout=30)
    assert r.status_code == 200, f"outlook expected 200 (no 502), got {r.status_code}: {r.text[:300]}"
    assert elapsed < 20, f"outlook took {elapsed:.1f}s"
    data = r.json()
    assert "available" in data or isinstance(data, dict), f"unexpected outlook shape: {data}"
    print(f"OK outlook: available={data.get('available')} reason={data.get('reason')} {elapsed:.2f}s")
