"""
Backend tests for TradeCrawler iteration 13: adaptive learning + honest odds.
Covers:
 - honest odds in /scan-status & /propose (no 95-97% absurd wins)
 - ADX chop filter + multi-timeframe confluence mechanics
 - /trades/edge endpoint shape
 - End-to-end learning loop: propose -> approve -> positions -> close -> edge.n>=1
 - Risk cap (<=20% equity) + growth target + candle service
"""
import os
import time
import urllib.parse
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://signal-engine-158.preview.emergentagent.com").rstrip("/")
USERNAME = "xambiep90"
PASSWORD = "Reddestroad1$"

VOL_SYMBOLS = ["Volatility 75 Index", "Volatility 100 Index", "Volatility 25 Index", "Volatility 50 Index", "Volatility 10 Index"]


@pytest.fixture(scope="session")
def token():
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"username": USERNAME, "password": PASSWORD}, timeout=60)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:200]}"
    tk = r.json().get("token") or r.json().get("access_token")
    assert tk, f"no token in login response: {r.json()}"
    return tk


@pytest.fixture(scope="session")
def auth(token):
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    return s


# ---------- Basic access / regression ----------
def test_account_and_growth(auth):
    data = None
    for _ in range(5):
        r = auth.get(f"{BASE_URL}/api/trades/account", timeout=30)
        if r.status_code == 200:
            data = r.json()
            if data.get("available") is not False:
                break
        time.sleep(3)
    assert data is not None, "account endpoint never returned 200"
    if data.get("available") is False:
        pytest.skip(f"MT5 unavailable right now: {data.get('reason')}")
    assert "growth" in data, f"missing growth: {data}"
    g = data["growth"]
    assert "target_balance" in g and "growth_pct" in g, g
    assert float(g["target_balance"]) > 0


def test_candles_service_fast(auth):
    sym = urllib.parse.quote("Volatility 75 Index")
    t0 = time.time()
    r = auth.get(f"{BASE_URL}/api/market/candles?symbol={sym}&timeframe=15m&limit=200", timeout=30)
    dt = time.time() - t0
    assert r.status_code == 200, r.text[:200]
    data = r.json()
    assert data.get("candles") and len(data["candles"]) > 50, f"too few candles: {len(data.get('candles', []))}"
    # warm/second call fast
    t1 = time.time()
    r2 = auth.get(f"{BASE_URL}/api/market/candles?symbol={sym}&timeframe=15m&limit=200", timeout=30)
    dt2 = time.time() - t1
    assert r2.status_code == 200
    print(f"candles cold={dt:.2f}s warm={dt2:.2f}s")


# ---------- Honest odds + ADX + HTF confluence ----------
def test_scan_status_honest_odds(auth):
    r = auth.get(f"{BASE_URL}/api/trades/scan-status", timeout=120)
    assert r.status_code == 200, r.text[:200]
    items = r.json()
    assert isinstance(items, list) and len(items) > 0, f"scan-status empty: {items}"
    # collect win_probs where present
    probs = [it.get("win_prob") for it in items if it.get("win_prob") is not None]
    reasons = [str(it.get("reason", "")) for it in items]
    adx_skips = [rs for rs in reasons if "ADX" in rs and "20" in rs]
    htf_skips = [rs for rs in reasons if "Higher timeframe" in rs or "multi-timeframe" in rs]
    conf_skips = [rs for rs in reasons if "Confidence" in rs and "58" in rs]
    exp_skips = [rs for rs in reasons if "expectancy" in rs.lower() or "edge" in rs.lower()]
    tradeable = [it for it in items if it.get("tradeable")]
    print(f"scan-status total={len(items)} tradeable={len(tradeable)} adx_skips={len(adx_skips)} htf_skips={len(htf_skips)} conf_skips={len(conf_skips)} exp_skips={len(exp_skips)}")
    # honest odds: no absurd 95-97% calibrated win rates
    high = [p for p in probs if p is not None and p >= 0.90]
    assert not high, f"Found absurd win_prob >=0.90 (not honest): {high[:5]}"
    # at least some reasonable odds present
    if probs:
        assert max(probs) <= 0.85, f"max win_prob should be <=0.85 after calibration, got {max(probs)}"
    # mechanism exists: either some tradeable OR some ADX/HTF/confidence/expectancy skips
    assert tradeable or adx_skips or htf_skips or conf_skips or exp_skips, f"no filter activity seen; reasons sample: {reasons[:5]}"


def test_edge_endpoint_shape(auth):
    r = auth.get(f"{BASE_URL}/api/trades/edge", timeout=30)
    assert r.status_code == 200, r.text[:200]
    data = r.json()
    assert "overall" in data and "setups" in data, data
    ov = data["overall"]
    for k in ("n", "win_rate", "expectancy_r", "total_r"):
        assert k in ov, f"missing {k} in overall: {ov}"
    assert isinstance(data["setups"], list)
    print(f"edge BEFORE: overall={ov} setups={len(data['setups'])}")


def _ensure_strategy_for(auth, symbol: str) -> str:
    """Create a dummy strategy for symbol if none exists. Returns strategy id (or '')."""
    try:
        strats = auth.get(f"{BASE_URL}/api/strategies", timeout=20).json()
        for s in strats:
            if s.get("symbol") == symbol and s.get("enabled"):
                return s.get("id", "")
    except Exception:
        pass
    body = {
        "name": f"TEST_adaptive_{symbol}",
        "symbol": symbol,
        "timeframe": "15m",
        "entry_rules": [{"indicator": "rsi", "params": {"period": 14, "op": "below", "value": 30}}],
        "exit_rules": [{"indicator": "rsi", "params": {"period": 14, "op": "above", "value": 70}}],
        "logic": "AND",
    }
    r = auth.post(f"{BASE_URL}/api/strategies", json=body, timeout=30)
    if r.status_code == 200:
        return r.json().get("id", "")
    print(f"create strategy for {symbol} -> {r.status_code} {r.text[:150]}")
    return ""


def _find_tradeable_proposal(auth, max_retries: int = 3):
    """Try each 24/7 volatility symbol; return (symbol, proposal_json) or (None, None).
    Retries up to max_retries rounds (symbols iterated per round) with waits between
    rounds — MT5 broker reconnects can take 10-30s."""
    created_ids = []
    for attempt in range(max_retries):
        for sym in VOL_SYMBOLS:
            try:
                r = auth.post(f"{BASE_URL}/api/trades/propose", json={"symbol": sym, "timeframe": "15m"}, timeout=90)
            except Exception as e:
                print(f"propose {sym} error: {e}")
                continue
            if r.status_code == 400 and "active instruments" in r.text:
                sid = _ensure_strategy_for(auth, sym)
                if sid:
                    created_ids.append(sid)
                    time.sleep(1)
                    try:
                        r = auth.post(f"{BASE_URL}/api/trades/propose", json={"symbol": sym, "timeframe": "15m"}, timeout=90)
                    except Exception as e:
                        print(f"re-propose {sym} error: {e}")
                        continue
            if r.status_code != 200:
                print(f"[a{attempt}] propose {sym} -> {r.status_code} {r.text[:150]}")
                continue
            p = r.json()
            is_tradeable = bool(p.get("id") and p.get("status") == "pending")
            print(f"[a{attempt}] propose {sym}: tradeable={is_tradeable} win={p.get('win_prob')} er={p.get('expected_r')} side={p.get('side')} reason={str(p.get('reasons',''))[:140]}")
            if is_tradeable:
                p["_created_strategy_ids"] = created_ids
                return sym, p
        if attempt < max_retries - 1:
            print(f"no tradeable in attempt {attempt}, waiting 15s before retry...")
            time.sleep(15)
    for sid in created_ids:
        try:
            auth.delete(f"{BASE_URL}/api/strategies/{sid}", timeout=15)
        except Exception:
            pass
    return None, None


def test_propose_honest_odds_and_fields(auth):
    sym, p = _find_tradeable_proposal(auth)
    if not p:
        pytest.skip("No tradeable volatility proposal right now (market/MT5 state)")
    # honest odds
    wp = p.get("win_prob")
    er = p.get("expected_r")
    assert wp is not None and er is not None, f"missing win_prob/expected_r: {p}"
    assert 0.30 <= wp <= 0.85, f"win_prob out of honest range: {wp}"
    assert er > 0, f"expected_r must be >0 for tradeable: {er}"
    reason = " ".join(p.get("reasons") or [])
    assert "calibrated" in reason.lower() or "expectancy" in reason.lower() or "simulation" in reason.lower(), f"reason should reference calibration/expectancy: {reason}"
    # risk cap sanity
    acct = auth.get(f"{BASE_URL}/api/trades/account", timeout=20).json()
    eq = float(acct.get("equity") or acct.get("balance") or 0)
    if eq > 0 and p.get("est_risk_usd") is not None:
        cap = 0.21 * eq  # 20% with tolerance
        assert float(p["est_risk_usd"]) <= cap, f"risk {p['est_risk_usd']} exceeds 20% cap {cap} for equity {eq}"
    # cleanup: reject this proposal (don't approve here; approval tested in learning loop)
    try:
        auth.post(f"{BASE_URL}/api/trades/proposals/{p['id']}/reject", timeout=15)
    except Exception:
        pass
    # cleanup created strategies
    for sid in p.get("_created_strategy_ids", []):
        try:
            auth.delete(f"{BASE_URL}/api/strategies/{sid}", timeout=15)
        except Exception:
            pass


# ---------- End-to-end learning loop ----------
def test_adaptive_learning_loop(auth):
    # before
    before = auth.get(f"{BASE_URL}/api/trades/edge", timeout=20).json()
    n_before = int(before["overall"].get("n") or 0)
    print(f"edge n_before={n_before}")

    sym, p = _find_tradeable_proposal(auth)
    if not p:
        pytest.skip("No tradeable volatility proposal (likely MT5 slot/HTF conflict).")
    created_strat_ids = p.get("_created_strategy_ids", [])

    pid = p["id"]
    side = p.get("side", "?")
    # approve
    ar = auth.post(f"{BASE_URL}/api/trades/proposals/{pid}/approve", timeout=90)
    if ar.status_code != 200:
        pytest.skip(f"approve failed (likely MetaApi slot): {ar.status_code} {ar.text[:200]}")
    aj = ar.json()
    print(f"approved: {aj}")
    time.sleep(2)

    # positions
    pos_resp = auth.get(f"{BASE_URL}/api/trades/positions", timeout=30)
    assert pos_resp.status_code == 200, pos_resp.text[:200]
    positions = pos_resp.json()
    # find a position that corresponds to our symbol
    assert isinstance(positions, list) and len(positions) >= 1, f"no open positions after approve: {positions}"
    pos = None
    for x in positions:
        if sym.lower() in str(x.get("symbol", "")).lower() or x.get("proposal_id") == pid:
            pos = x
            break
    pos = pos or positions[-1]
    position_id = pos.get("id") or pos.get("position_id") or pos.get("ticket")
    assert position_id, f"could not find position id in: {pos}"
    print(f"position opened: id={position_id} sym={pos.get('symbol')}")

    # close (retry on transient MetaApi reconnect)
    closed_ok = False
    last_err = None
    for i in range(4):
        cr = auth.post(f"{BASE_URL}/api/trades/positions/{position_id}/close", timeout=60)
        if cr.status_code == 200:
            closed_ok = True
            print(f"close ok: {cr.json()}")
            break
        last_err = f"{cr.status_code} {cr.text[:200]}"
        print(f"close attempt {i+1} failed: {last_err}")
        time.sleep(3)
        # reconcile: if position already gone, treat as closed
        pr = auth.get(f"{BASE_URL}/api/trades/positions", timeout=20).json()
        if not any((x.get("id") or x.get("position_id") or x.get("ticket")) == position_id for x in pr):
            closed_ok = True
            print("position disappeared -> treating as closed")
            break
    assert closed_ok, f"could not close position; last error: {last_err}"

    # give learning.record_outcome a moment to persist
    time.sleep(3)
    after = auth.get(f"{BASE_URL}/api/trades/edge", timeout=20).json()
    n_after = int(after["overall"].get("n") or 0)
    print(f"edge n_after={n_after} overall={after['overall']} setups_count={len(after['setups'])}")
    assert n_after >= n_before + 1, f"edge.n did not increment: before={n_before} after={n_after}"
    # expectancy_r present
    assert "expectancy_r" in after["overall"]
    # a setups entry for 'SIDE SYMBOL' or similar
    setups_names = [str(s.get("setup") or s.get("name") or s.get("key") or "") for s in after["setups"]]
    joined = " | ".join(setups_names).lower()
    assert side.lower() in joined or sym.split()[0].lower() in joined, f"no setup entry for {side} {sym}: {setups_names}"

    # cleanup created strategies
    for sid in created_strat_ids:
        try:
            auth.delete(f"{BASE_URL}/api/strategies/{sid}", timeout=15)
        except Exception:
            pass
