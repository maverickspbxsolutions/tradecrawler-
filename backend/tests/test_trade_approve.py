"""Backend test: approve-trade flow — ensures the recent 'Invalid stops in the request'
bug is fixed by _fresh_stops() re-anchoring SL/TP at approval time."""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/")
USERNAME = "xambiep90"
PASSWORD = "Reddestroad1$"

APPROVE_TIMEOUT_S = 45  # must respond within ~40s


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"username": USERNAME, "password": PASSWORD}, timeout=20)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    token = r.json().get("token")
    assert token
    s.headers.update({"Authorization": f"Bearer {token}"})
    return s


def _try_propose(client, symbols):
    """Try proposing across the given symbol list; return (proposal, used_symbol) or (None, errors)."""
    errors = []
    for sym in symbols:
        for attempt in range(3):
            r = client.post(f"{BASE_URL}/api/trades/propose",
                            json={"symbol": sym}, timeout=60)
            if r.status_code == 200:
                p = r.json()
                if p.get("id") and p.get("status") == "pending":
                    return p, sym, errors
                errors.append(f"{sym} attempt{attempt}: 200 but not pending: {p}")
            else:
                errors.append(f"{sym} attempt{attempt}: {r.status_code} {r.text[:160]}")
                # selective-reject (400) — try next symbol / retry
            time.sleep(1.2)
    return None, None, errors


def test_approve_trade_no_invalid_stops(client):
    # 1) Fetch active symbols; prefer BTCUSD (24/7), then Volatility 100 Index, then rest
    r = client.get(f"{BASE_URL}/api/trades/active-symbols", timeout=20)
    assert r.status_code == 200, f"active-symbols {r.status_code} {r.text}"
    active = r.json()
    assert isinstance(active, list) and active, f"no active instruments: {active}"
    values = [a["value"] for a in active if a.get("value")]
    preferred = []
    for want in ("BTCUSD", "Volatility 100 Index"):
        if want in values:
            preferred.append(want)
    for v in values:
        if v not in preferred and v != "XAUUSD":  # skip XAUUSD (closed on weekends)
            preferred.append(v)
    print(f"Trying symbols in order: {preferred}")

    # 2) Build a pending proposal
    proposal, used_sym, errs = _try_propose(client, preferred)
    if not proposal:
        pytest.skip(f"No tradeable proposal available right now (selective engine). "
                    f"Errors: {errs[-5:]}")
    pid = proposal["id"]
    print(f"Pending proposal {pid} on {used_sym}: side={proposal['side']} "
          f"lot={proposal['lot']} sl={proposal['stop_loss']} tp={proposal['take_profit']}")

    # 3) Approve — THIS is the bug-fix assertion
    t0 = time.time()
    r = client.post(f"{BASE_URL}/api/trades/proposals/{pid}/approve",
                    timeout=APPROVE_TIMEOUT_S)
    elapsed = time.time() - t0
    body_text = r.text or ""
    print(f"Approve returned {r.status_code} in {elapsed:.1f}s: {body_text[:400]}")
    assert elapsed < APPROVE_TIMEOUT_S, f"approve took too long: {elapsed:.1f}s"
    assert "Invalid stops" not in body_text, f"BUG STILL PRESENT: {body_text}"

    if r.status_code != 200:
        # Report but don't swallow — some non-stops errors may still legitimately occur
        pytest.fail(f"approve failed ({r.status_code}): {body_text}")

    placed = r.json()
    assert placed.get("status") == "placed", f"status not placed: {placed}"
    position_id = placed.get("position_id")
    assert position_id, f"missing position_id: {placed}"
    assert placed.get("stop_loss") is not None, "stop_loss missing after approve"
    assert placed.get("take_profit") is not None, "take_profit missing after approve"

    # 4) GET /trades/positions should include it
    r = client.get(f"{BASE_URL}/api/trades/positions", timeout=20)
    assert r.status_code == 200, f"positions {r.status_code} {r.text}"
    positions = r.json()
    ids = [str(p.get("id")) for p in positions]
    assert position_id in ids, f"placed position {position_id} not in positions list {ids}"

    # 5) GET /trades/proposals should show the approved proposal as placed
    r = client.get(f"{BASE_URL}/api/trades/proposals", timeout=20)
    assert r.status_code == 200
    props = r.json()
    this = next((p for p in props if p["id"] == pid), None)
    assert this and this["status"] == "placed", f"proposal not reflected as placed: {this}"
    assert this.get("stop_loss") is not None and this.get("take_profit") is not None

    # 6) Clean up — ALWAYS close the position we opened
    r = client.post(f"{BASE_URL}/api/trades/positions/{position_id}/close",
                    timeout=APPROVE_TIMEOUT_S)
    print(f"Close returned {r.status_code}: {r.text[:200]}")
    assert r.status_code == 200, f"close failed: {r.status_code} {r.text}"
    close_body = r.json()
    assert close_body.get("ok") is True, f"close ok=false: {close_body}"

    # 7) Confirm it's gone
    time.sleep(1.5)
    r = client.get(f"{BASE_URL}/api/trades/positions", timeout=20)
    assert r.status_code == 200
    still_open = [str(p.get("id")) for p in r.json()]
    assert position_id not in still_open, \
        f"position {position_id} still open after close: {still_open}"


def test_active_symbols_shape(client):
    r = client.get(f"{BASE_URL}/api/trades/active-symbols", timeout=20)
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    for item in data:
        assert "value" in item and "label" in item
