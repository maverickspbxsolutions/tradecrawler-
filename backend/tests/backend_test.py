"""Crypto Signal Terminal — Backend API tests."""
import os
import time
import pytest
import requests

BASE = os.environ["REACT_APP_BACKEND_URL"].rstrip("/") if os.environ.get("REACT_APP_BACKEND_URL") else "https://signal-engine-158.preview.emergentagent.com"
API = f"{BASE}/api"

USER = "xambiep90"
PWD = "Reddestroad1$"


# ---------- Fixtures ----------
@pytest.fixture(scope="session")
def token():
    r = requests.post(f"{API}/auth/login", json={"username": USER, "password": PWD}, timeout=30)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="session")
def client(token):
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    return s


# ---------- Auth ----------
class TestAuth:
    def test_login_success(self):
        r = requests.post(f"{API}/auth/login", json={"username": USER, "password": PWD}, timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert d["token"] and d["user"]["username"] == USER

    def test_login_wrong_password(self):
        r = requests.post(f"{API}/auth/login", json={"username": USER, "password": "bad"}, timeout=30)
        assert r.status_code == 401

    def test_me_with_bearer(self, client):
        r = client.get(f"{API}/auth/me", timeout=30)
        assert r.status_code == 200
        assert r.json()["username"] == USER

    def test_protected_requires_auth(self):
        r = requests.get(f"{API}/strategies", timeout=30)
        assert r.status_code == 401

    def test_root_public(self):
        r = requests.get(f"{API}/", timeout=30)
        assert r.status_code == 200


# ---------- Strategies list / enrichment ----------
class TestStrategies:
    def test_list_seeded(self, client):
        r = client.get(f"{API}/strategies", timeout=60)
        assert r.status_code == 200
        strats = r.json()
        assert len(strats) >= 3
        for s in strats:
            assert "id" in s and "name" in s
            assert "gate_passed" in s
            assert "backtest" in s
            assert "paper" in s

    def test_create_strategy_and_backtest_and_live_gate(self, client):
        payload = {
            "name": "TEST_RSI_" + str(int(time.time())),
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "logic": "AND",
            "entry_rules": [{"indicator": "rsi", "params": {"period": 14, "op": "below", "value": 35}}],
            "exit_rules": [{"indicator": "rsi", "params": {"period": 14, "op": "above", "value": 65}}],
        }
        r = client.post(f"{API}/strategies", json=payload, timeout=60)
        assert r.status_code == 200, r.text
        strat = r.json()
        sid = strat["id"]
        assert strat["name"] == payload["name"]

        # It should appear in list
        r = client.get(f"{API}/strategies", timeout=60)
        assert any(x["id"] == sid for x in r.json())

        # live without gate should 400
        r = client.put(f"{API}/strategies/{sid}", json={"live": True}, timeout=30)
        assert r.status_code == 400
        assert "gate" in r.json().get("detail", "").lower()

        # Run backtest
        r = client.post(f"{API}/strategies/{sid}/backtest", json={"limit": 500}, timeout=90)
        assert r.status_code == 200, r.text
        bt = r.json()
        for k in ["expectancy_pct", "risk_reward", "max_drawdown_pct",
                  "total_return_pct", "benchmark_return_pct", "beats_benchmark",
                  "equity_curve", "trades", "gate_passed"]:
            assert k in bt, f"missing {k}"
        assert isinstance(bt["equity_curve"], list)

        # If gate passed, we can enable live; otherwise still 400
        r = client.put(f"{API}/strategies/{sid}", json={"live": True}, timeout=30)
        if bt["gate_passed"]:
            assert r.status_code == 200
            assert r.json()["live"] is True
        else:
            assert r.status_code == 400

        # Scan idempotency: two scans should NOT create duplicate signal
        r1 = client.post(f"{API}/strategies/{sid}/scan", timeout=60)
        assert r1.status_code == 200
        sig1 = r1.json().get("signal")
        r2 = client.post(f"{API}/strategies/{sid}/scan", timeout=60)
        assert r2.status_code == 200
        sig2 = r2.json().get("signal")
        if sig1 and sig2:
            # same sig_key on closed candle -> second should be None (idempotent)
            assert sig2 is None or sig1.get("sig_key") != sig2.get("sig_key") or sig1["id"] == sig2["id"]

        # cleanup
        client.delete(f"{API}/strategies/{sid}", timeout=30)


# ---------- Signals / Paper ----------
class TestSignalsPaper:
    def test_signals_feed(self, client):
        r = client.get(f"{API}/signals?limit=20", timeout=30)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_paper_endpoint(self, client):
        strats = client.get(f"{API}/strategies", timeout=30).json()
        sid = strats[0]["id"]
        r = client.get(f"{API}/paper/{sid}", timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert "state" in d and "trades" in d and "equity_curve" in d
        assert isinstance(d["equity_curve"], list)


# ---------- Market ----------
class TestMarket:
    def test_candles(self, client):
        r = client.get(f"{API}/market/candles?symbol=BTCUSDT&timeframe=1h&limit=100", timeout=60)
        assert r.status_code == 200
        d = r.json()
        assert len(d["candles"]) > 0
        c = d["candles"][0]
        for k in ["o", "h", "l", "c", "v", "t"]:
            assert k in c

    def test_regime(self, client):
        r = client.get(f"{API}/market/regime?symbol=BTCUSDT&timeframe=1h", timeout=60)
        assert r.status_code == 200
        d = r.json()
        for k in ["trend", "volatility", "strength"]:
            assert k in d


# ---------- Settings / Alerts (Resend) ----------
class TestSettingsAlerts:
    def test_get_put_settings(self, client):
        r = client.get(f"{API}/settings", timeout=30)
        assert r.status_code == 200
        r = client.put(f"{API}/settings", json={"alert_email": "delivered@resend.dev", "email_enabled": True}, timeout=30)
        assert r.status_code == 200
        assert r.json()["alert_email"] == "delivered@resend.dev"

    def test_alerts_test_email(self, client):
        client.put(f"{API}/settings", json={"alert_email": "delivered@resend.dev"}, timeout=30)
        r = client.post(f"{API}/alerts/test", timeout=60)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("sent") is True


# ---------- Evolution ----------
class TestEvolution:
    def test_evolve_and_history(self, client):
        strats = client.get(f"{API}/strategies", timeout=30).json()
        sid = strats[0]["id"]
        r = client.post(f"{API}/strategies/{sid}/evolve", timeout=120)
        assert r.status_code == 200, r.text
        d = r.json()
        for k in ["generations", "regime", "current_oos_fitness",
                  "candidate_oos_fitness", "improvement", "applied"]:
            assert k in d, f"missing {k}"
        assert isinstance(d["generations"], list)
        regime = d["regime"]
        for k in ["trend", "volatility", "strength"]:
            assert k in regime

        r = client.get(f"{API}/strategies/{sid}/evolution", timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert "latest" in d and "history" in d
