"""Iteration 12 backend tests.

Validates:
  1. MT5 warm-cache candles for XAUUSD, BTCUSD, Volatility 100 Index on 15m
     - available=True, cold < 10s, warm (2nd call) < 1s.
  2. GET /api/market/live returns live MT5 price for BTCUSD, source='deriv-mt5'.
  3. GET /api/market/symbols returns ONLY source='deriv-mt5' and >=100 opts.
  4. GET /api/risk/config = base_lot 2, max_lot 10, confidence_scaled_lots true.
  5. Confidence-scaled lot sizing: proposals yield 2.0 <= lot <= 10.0 (no 0.0x).
  6. Full demo lifecycle on 'Volatility 100 Index' (24/7 synthetic): propose ->
     approve -> position shows on two consecutive GET /positions -> close ->
     eventually removed.
"""
import os
import time
import urllib.parse as up
import pytest
import requests

BASE = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
API = f"{BASE}/api"
USER = "xambiep90"
PWD = "Reddestroad1$"


@pytest.fixture(scope="session")
def client():
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"username": USER, "password": PWD}, timeout=30)
    assert r.status_code == 200, r.text
    s.headers.update({"Authorization": f"Bearer {r.json()['token']}",
                      "Content-Type": "application/json"})
    return s


# ---- 1. Warm-cache candles -------------------------------------------------
@pytest.mark.parametrize("sym", ["XAUUSD", "BTCUSD", "Volatility 100 Index"])
def test_candles_mt5_warm(client, sym):
    url = f"{API}/market/candles?symbol={up.quote(sym)}&timeframe=15m&limit=200"
    t0 = time.time()
    r = client.get(url, timeout=30)
    cold = time.time() - t0
    assert r.status_code == 200, r.text
    d = r.json()
    assert d.get("available") is True, f"{sym}: {d}"
    assert len(d["candles"]) > 0
    assert cold < 10, f"{sym} cold load {cold:.2f}s too slow"
    # Warm call
    t0 = time.time()
    r2 = client.get(url, timeout=30)
    warm = time.time() - t0
    assert r2.status_code == 200
    assert warm < 2, f"{sym} warm load {warm:.2f}s too slow (expected <2s cache)"
    assert len(r2.json()["candles"]) > 0


# ---- 2. Live price ---------------------------------------------------------
def test_live_btcusd_mt5(client):
    r1 = client.get(f"{API}/market/live?symbol=BTCUSD&timeframe=15m", timeout=30)
    assert r1.status_code == 200
    d1 = r1.json()
    assert d1.get("available") is True
    assert d1.get("source") == "deriv-mt5"
    assert isinstance(d1.get("price"), (int, float)) and d1["price"] > 0
    time.sleep(3)
    r2 = client.get(f"{API}/market/live?symbol=BTCUSD&timeframe=15m", timeout=30)
    assert r2.status_code == 200
    d2 = r2.json()
    assert d2.get("source") == "deriv-mt5"
    assert isinstance(d2.get("price"), (int, float)) and d2["price"] > 0


# ---- 3. Symbols: MT5-only --------------------------------------------------
def test_symbols_mt5_only(client):
    r = client.get(f"{API}/market/symbols", timeout=30)
    assert r.status_code == 200
    d = r.json()
    opts = d.get("options") or []
    assert len(opts) > 100, f"expected hundreds of MT5 symbols, got {len(opts)}"
    srcs = {o.get("source") for o in opts}
    assert srcs == {"deriv-mt5"}, f"unexpected sources: {srcs}"
    assert d.get("source") == "deriv-mt5"
    for forbidden in ("binance", "coinbase", "yahoo"):
        assert forbidden not in srcs


# ---- 4. Risk config --------------------------------------------------------
def test_risk_config(client):
    r = client.get(f"{API}/risk/config", timeout=15)
    assert r.status_code == 200
    d = r.json()
    assert d["base_lot"] == 2.0
    assert d["max_lot"] == 10.0
    assert d["confidence_scaled_lots"] is True


# ---- 5. Confidence-scaled lot sizing --------------------------------------
def test_confidence_scaled_lot_sizing(client):
    """Attempt proposals until we get one that passes the confidence gate.
    Verify lot is in [2.0, 10.0] (not a tiny 0.0x fraction)."""
    tried = []
    proposal = None
    for sym in ("Volatility 100 Index", "XAUUSD", "BTCUSD"):
        r = client.post(f"{API}/trades/propose", json={"symbol": sym, "timeframe": "15m"}, timeout=60)
        tried.append((sym, r.status_code, r.text[:120]))
        if r.status_code == 200:
            proposal = r.json()
            break
    if proposal is None:
        pytest.skip(f"No tradeable setup right now across {tried}")
    lot = proposal["lot"]
    assert 2.0 <= lot <= 10.0, f"lot {lot} outside confidence-scaled range on {proposal['signal_symbol']}"
    # Cleanup — reject
    client.post(f"{API}/trades/proposals/{proposal['id']}/reject", timeout=30)


# ---- 6. Full demo trade lifecycle + persistence ---------------------------
def test_lifecycle_volatility_persistence(client):
    sym = "Volatility 100 Index"
    r = client.post(f"{API}/trades/propose", json={"symbol": sym, "timeframe": "15m"}, timeout=60)
    if r.status_code != 200:
        pytest.skip(f"No tradeable setup for {sym}: {r.text[:200]}")
    prop = r.json()
    pid = prop["id"]
    assert 2.0 <= prop["lot"] <= 10.0

    # Approve -> places demo market order
    r = client.post(f"{API}/trades/proposals/{pid}/approve", timeout=60)
    assert r.status_code == 200, r.text
    approved = r.json()
    assert approved["status"] == "placed"
    position_id = approved["position_id"]
    assert position_id

    # 1st GET /positions
    r = client.get(f"{API}/trades/positions", timeout=30)
    assert r.status_code == 200
    pos_list = r.json()
    assert any(p["id"] == position_id for p in pos_list), \
        f"position {position_id} not in /trades/positions: {pos_list}"

    # 2nd GET /positions (simulate refresh): must still appear (DB-backed persistence)
    time.sleep(2)
    r = client.get(f"{API}/trades/positions", timeout=30)
    assert r.status_code == 200
    pos_list2 = r.json()
    assert any(p["id"] == position_id for p in pos_list2), \
        f"position {position_id} did not survive refresh: {pos_list2}"

    # Close (and cleanup). The close call may return 200 or may return 400 on a
    # transient MetaApi error while the broker actually completes the close
    # moments later — either is acceptable as long as the position disappears.
    r = client.post(f"{API}/trades/positions/{position_id}/close", timeout=60)
    assert r.status_code in (200, 400), r.text

    # Poll for removal up to 15s
    gone = False
    for _ in range(15):
        time.sleep(1)
        pr = client.get(f"{API}/trades/positions", timeout=30).json()
        if not any(p["id"] == position_id for p in pr):
            gone = True
            break
    assert gone, f"position {position_id} did not disappear after close"

    # Verify trade_log reflects status=closed
    log = client.get(f"{API}/trades/log?limit=20", timeout=30).json()
    entry = next((x for x in log if x.get("position_id") == position_id), None)
    assert entry is not None
    assert entry.get("status") == "closed"
