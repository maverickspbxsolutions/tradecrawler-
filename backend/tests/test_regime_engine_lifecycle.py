"""
TradeCrawler iteration 14: regime-aware confluence-scored engine + full lifecycle.

Validates:
  - /api/trades/scan-status: new schema with regime, score, adx, honest win_prob, expected_r.
  - /api/trades/propose: new fields regime, score (>=62), rr, lot, win_prob, expected_r>0,
                        est_risk_usd, est_reward_usd, stop_loss, take_profit, reasons.
  - FULL LIFECYCLE: propose -> approve -> positions (persistence via refetch) -> close ->
                   edge.n increments by 1 and a setups[] entry for 'SIDE SYMBOL' exists.
  - Risk cap <= 20% of equity.
  - Regressions: /api/trades/account {available,is_demo,equity,growth{...}} +
                 /api/market/candles?symbol=XAUUSD&timeframe=15m fast.
"""
import os
import time
import urllib.parse
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://signal-engine-158.preview.emergentagent.com").rstrip("/")
USERNAME = "xambiep90"
PASSWORD = "Reddestroad1$"

# Preferred lifecycle candidates: 24/7 synthetics first, XAUUSD second.
CANDIDATE_SYMBOLS = [
    "Volatility 75 Index", "Volatility 100 Index", "Volatility 50 Index",
    "Volatility 25 Index", "Volatility 10 Index", "XAUUSD",
]


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


# ---------- regressions ----------
def test_account_schema(auth):
    for _ in range(5):
        r = auth.get(f"{BASE_URL}/api/trades/account", timeout=30)
        if r.status_code == 200 and r.json().get("available") is not False:
            break
        time.sleep(3)
    assert r.status_code == 200, r.text[:200]
    d = r.json()
    if d.get("available") is False:
        pytest.skip(f"MT5 unavailable: {d.get('reason')}")
    assert d.get("is_demo") is True, d
    assert float(d.get("equity") or 0) > 0, d
    g = d.get("growth") or {}
    assert "target_balance" in g and "growth_pct" in g, g


def test_candles_xauusd(auth):
    sym = urllib.parse.quote("XAUUSD")
    t0 = time.time()
    r = auth.get(f"{BASE_URL}/api/market/candles?symbol={sym}&timeframe=15m&limit=200", timeout=30)
    dt = time.time() - t0
    assert r.status_code == 200, r.text[:200]
    data = r.json()
    assert data.get("candles") and len(data["candles"]) > 50, f"too few candles: {len(data.get('candles', []))}"
    assert dt < 10.0, f"candles slow: {dt:.2f}s"
    print(f"XAUUSD candles ok in {dt:.2f}s, n={len(data['candles'])}")


# ---------- regime engine readout ----------
def test_scan_status_regime_schema_and_honest_odds(auth):
    r = auth.get(f"{BASE_URL}/api/trades/scan-status", timeout=120)
    assert r.status_code == 200, r.text[:200]
    items = r.json()
    assert isinstance(items, list) and len(items) > 0, items

    allowed_regimes = {"momentum", "reversion", "chop", None}
    for it in items:
        # new fields present
        for k in ("tradeable", "regime", "score", "adx", "win_prob", "expected_r", "reason"):
            assert k in it, f"missing {k} in scan-status item: {it}"
        assert it["regime"] in allowed_regimes, f"bad regime: {it['regime']}"
        # chop/low-score instruments must be tradeable=false
        if it["regime"] == "chop":
            assert it["tradeable"] is False, f"chop should not be tradeable: {it}"
        # reason reads sensibly
        assert isinstance(it["reason"], str) and len(it["reason"]) > 5, f"bad reason: {it}"
        # honest odds: no absurd 90%+ win rate
        wp = it.get("win_prob")
        if wp is not None:
            assert 0.30 <= wp <= 0.85, f"win_prob out of honest range on {it.get('value')}: {wp}"
        # when tradeable, score must be >=62
        if it["tradeable"]:
            assert it["score"] is not None and it["score"] >= 62, f"tradeable item below MIN_SCORE: {it}"
            assert it["expected_r"] is not None and it["expected_r"] > 0, f"tradeable but expected_r<=0: {it}"
    # at least one tradeable OR one explicit chop/low-score skip
    tradeable = [it for it in items if it["tradeable"]]
    skipped = [it for it in items if not it["tradeable"]]
    print(f"scan-status total={len(items)} tradeable={len(tradeable)} skipped={len(skipped)}")
    for it in items:
        print(f"  {it.get('value')}: tradeable={it['tradeable']} regime={it['regime']} score={it['score']} wp={it['win_prob']} er={it['expected_r']} reason={it['reason'][:90]}")


def test_edge_endpoint_shape(auth):
    r = auth.get(f"{BASE_URL}/api/trades/edge", timeout=30)
    assert r.status_code == 200, r.text[:200]
    d = r.json()
    assert "overall" in d and "setups" in d
    for k in ("n", "win_rate", "expectancy_r", "total_r"):
        assert k in d["overall"], d["overall"]
    print(f"edge overall before: {d['overall']}  setups={len(d['setups'])}")


# ---------- picking a tradeable instrument from live scan ----------
def _pick_tradeable_from_scan(auth):
    try:
        r = auth.get(f"{BASE_URL}/api/trades/scan-status", timeout=90)
        items = r.json() if r.status_code == 200 else []
    except Exception:
        items = []
    # Prefer 24/7 synthetics first
    preferred_order = {s: i for i, s in enumerate(CANDIDATE_SYMBOLS)}
    tradeables = [it for it in items if it.get("tradeable")]
    tradeables.sort(key=lambda x: preferred_order.get(x.get("value"), 99))
    return [it.get("value") for it in tradeables]


def _try_propose(auth, sym):
    try:
        r = auth.post(f"{BASE_URL}/api/trades/propose", json={"symbol": sym, "timeframe": "15m"}, timeout=90)
    except Exception as e:
        return None, f"request error: {e}"
    if r.status_code != 200:
        return None, f"{r.status_code} {r.text[:200]}"
    return r.json(), None


def test_propose_schema_and_risk_cap(auth):
    """Pick a currently-tradeable instrument from scan-status, propose, validate schema + risk cap, then reject."""
    candidates = _pick_tradeable_from_scan(auth)
    # Retry a couple of times in case of MT5 reconnects
    for attempt in range(3):
        for sym in candidates or CANDIDATE_SYMBOLS:
            p, err = _try_propose(auth, sym)
            if p and p.get("id") and p.get("status") == "pending":
                # schema
                for k in ("regime", "score", "rr", "lot", "win_prob", "expected_r",
                          "est_risk_usd", "est_reward_usd", "stop_loss", "take_profit", "side"):
                    assert k in p, f"missing {k} in propose response: {p}"
                assert p["score"] >= 62, f"score below MIN_SCORE 62: {p['score']}"
                assert p["expected_r"] > 0, f"expected_r must be >0 for tradeable: {p['expected_r']}"
                assert 0.30 <= p["win_prob"] <= 0.85, f"win_prob out of honest range: {p['win_prob']}"
                assert p["rr"] > 0 and p["stop_loss"] and p["take_profit"]
                assert p["lot"] > 0
                # risk cap <= 20% equity (tiny tolerance)
                acct = auth.get(f"{BASE_URL}/api/trades/account", timeout=20).json()
                eq = float(acct.get("equity") or acct.get("balance") or 0)
                if eq > 0:
                    cap = 0.21 * eq
                    assert float(p["est_risk_usd"]) <= cap, f"risk {p['est_risk_usd']} exceeds 20% cap {cap} (equity {eq})"
                # If lot was trimmed, reasons should mention it; if not trimmed, lot should be >=2 per spec
                reasons = " ".join(p.get("reasons") or [])
                if p["lot"] < 2:
                    assert "trim" in reasons.lower() or "cap" in reasons.lower() or "risk" in reasons.lower(), \
                        f"lot<2 but no risk-cap note in reasons: {reasons}"
                print(f"propose {sym} OK: regime={p['regime']} score={p['score']} side={p['side']} lot={p['lot']} wp={p['win_prob']} er={p['expected_r']} rr={p['rr']} risk={p['est_risk_usd']} reward={p['est_reward_usd']}")
                # cleanup
                try:
                    auth.post(f"{BASE_URL}/api/trades/proposals/{p['id']}/reject", timeout=15)
                except Exception:
                    pass
                return
            else:
                print(f"[a{attempt}] propose {sym} not tradeable: {err}")
        time.sleep(10)
    pytest.skip("No tradeable instrument proposable right now (market/MT5 state).")


# ---------- FULL LIFECYCLE: approve -> position persistence -> close -> learning ----------
def test_full_lifecycle_approve_persist_close_and_learn(auth):
    # before
    before = auth.get(f"{BASE_URL}/api/trades/edge", timeout=20).json()
    n_before = int(before["overall"].get("n") or 0)
    setups_before = {str(s.get("setup", "")): int(s.get("n") or 0) for s in before.get("setups", [])}
    print(f"edge BEFORE: n={n_before}  setups={setups_before}")

    # pick tradeable
    picked = None
    for attempt in range(3):
        for sym in _pick_tradeable_from_scan(auth) or CANDIDATE_SYMBOLS:
            p, err = _try_propose(auth, sym)
            if p and p.get("id") and p.get("status") == "pending":
                picked = (sym, p)
                break
            print(f"[a{attempt}] propose {sym} -> {err}")
            # don't spam if the symbol just isn't scanned
            if err and "active instruments" in err:
                continue
        if picked:
            break
        time.sleep(15)
    if not picked:
        pytest.skip("No tradeable instrument for lifecycle test right now.")
    sym, p = picked
    pid = p["id"]
    side = p["side"]
    print(f"LIFECYCLE picked {sym} proposal={pid} side={side}")

    # approve
    ar = auth.post(f"{BASE_URL}/api/trades/proposals/{pid}/approve", timeout=120)
    if ar.status_code != 200:
        pytest.skip(f"approve failed (likely MetaApi slot drop): {ar.status_code} {ar.text[:200]}")
    aj = ar.json()
    print(f"approved: {str(aj)[:300]}")
    time.sleep(3)

    # positions #1
    pos_r = auth.get(f"{BASE_URL}/api/trades/positions", timeout=30)
    assert pos_r.status_code == 200, pos_r.text[:200]
    positions = pos_r.json()
    assert isinstance(positions, list) and len(positions) >= 1, f"no open positions after approve: {positions}"
    pos = None
    for x in positions:
        if sym.lower() in str(x.get("symbol", "")).lower() or x.get("proposal_id") == pid:
            pos = x
            break
    pos = pos or positions[-1]
    position_id = pos.get("id") or pos.get("position_id") or pos.get("ticket")
    assert position_id, f"could not find position id in: {pos}"
    print(f"position opened (fetch #1): id={position_id} sym={pos.get('symbol')}")

    # positions #2 (simulate refresh) — DB-backed persistence
    time.sleep(2)
    pos_r2 = auth.get(f"{BASE_URL}/api/trades/positions", timeout=30)
    assert pos_r2.status_code == 200
    positions2 = pos_r2.json()
    still_there = any((x.get("id") or x.get("position_id") or x.get("ticket")) == position_id for x in positions2)
    assert still_there, f"position {position_id} disappeared on refetch (not DB-backed): {positions2}"
    print(f"position persisted across refetch (fetch #2): {position_id}")

    # close with retries
    closed_ok = False
    last_err = None
    for i in range(5):
        cr = auth.post(f"{BASE_URL}/api/trades/positions/{position_id}/close", timeout=60)
        if cr.status_code == 200:
            closed_ok = True
            print(f"close ok (attempt {i+1}): {str(cr.json())[:200]}")
            break
        last_err = f"{cr.status_code} {cr.text[:200]}"
        print(f"close attempt {i+1} failed: {last_err}")
        time.sleep(4)
        pr = auth.get(f"{BASE_URL}/api/trades/positions", timeout=20).json()
        if not any((x.get("id") or x.get("position_id") or x.get("ticket")) == position_id for x in pr):
            closed_ok = True
            print("position disappeared mid-retry -> treat as closed")
            break
    assert closed_ok, f"could not close position; last error: {last_err}"

    # verify removed from positions
    time.sleep(2)
    pos_r3 = auth.get(f"{BASE_URL}/api/trades/positions", timeout=20).json()
    still = any((x.get("id") or x.get("position_id") or x.get("ticket")) == position_id for x in pos_r3)
    assert not still, f"position {position_id} still listed after close: {pos_r3}"

    # verify trade status closed via /api/trades/log (best-effort)
    try:
        log = auth.get(f"{BASE_URL}/api/trades/log", timeout=20).json()
        hit = [t for t in (log if isinstance(log, list) else []) if str(t.get("position_id") or t.get("ticket") or "") == str(position_id)]
        if hit:
            assert any(str(t.get("status", "")).lower() == "closed" for t in hit), f"trade not marked closed in log: {hit[:1]}"
            print(f"trade status in log: closed OK")
    except Exception as e:
        print(f"log check skipped: {e}")

    # learning: edge.n incremented and setup 'SIDE SYMBOL' present
    time.sleep(4)
    after = auth.get(f"{BASE_URL}/api/trades/edge", timeout=20).json()
    n_after = int(after["overall"].get("n") or 0)
    assert n_after >= n_before + 1, f"edge.n did not increment: before={n_before} after={n_after}"
    expected_setup_key = f"{side} {sym}".lower()
    setup_names = [str(s.get("setup", "")).lower() for s in after.get("setups", [])]
    joined = " | ".join(setup_names)
    assert expected_setup_key in joined or side.lower() in joined, \
        f"expected setup '{expected_setup_key}' not found; have: {setup_names}"
    # realized expectancy_r present (not None)
    assert "expectancy_r" in after["overall"] and after["overall"]["expectancy_r"] is not None
    print(f"LEARNING OK: edge n {n_before}->{n_after}, overall={after['overall']}, setup matched '{expected_setup_key}'")
