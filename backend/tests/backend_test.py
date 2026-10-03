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

    # ---------- Live price (near-real-time, 3s cache) ----------
    def test_live_btc(self, client):
        r = client.get(f"{API}/market/live?symbol=BTCUSDT&timeframe=1m", timeout=30)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["price"] is not None and isinstance(d["price"], (int, float))
        assert d["source"] == "binance.us"
        c = d["candle"]
        for k in ["o", "h", "l", "c", "t"]:
            assert k in c, f"missing candle.{k}"
        assert isinstance(d.get("server_ts"), int)

    # ---------- Market outlook ----------
    def test_outlook_btc(self, client):
        r = client.get(f"{API}/market/outlook?symbol=BTCUSDT&timeframe=1h", timeout=60)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["direction"] in ("Up", "Down", "Neutral")
        assert isinstance(d["confidence"], int) and 0 <= d["confidence"] <= 100
        assert isinstance(d["score"], int) and -100 <= d["score"] <= 100
        assert isinstance(d["reasons"], list) and len(d["reasons"]) > 0
        m = d["metrics"]
        for k in ["rsi", "ema20", "ema50", "macd_hist", "trend_slope_pct", "volatility_label"]:
            assert k in m, f"missing metrics.{k}"
        assert isinstance(d.get("disclaimer"), str) and d["disclaimer"]

    # ---------- Symbols: Binance + Deriv (fallback) ----------
    def test_symbols_includes_binance_and_deriv(self, client):
        r = client.get(f"{API}/market/symbols", timeout=30)
        assert r.status_code == 200
        d = r.json()
        opts = d["options"]
        srcs = {o["source"] for o in opts}
        assert "binance" in srcs, "binance options missing"
        assert "deriv" in srcs, "deriv options missing (fallback list should load)"
        assert d["deriv_available"] is True
        syms = {o["symbol"] for o in opts}
        # required deriv instruments per spec
        for req in ("frxXAUUSD", "cryBTCUSD", "R_10", "R_75", "R_100"):
            assert req in syms, f"deriv symbol {req} missing from options"
        # Binance crypto
        assert "BTCUSDT" in syms
        assert d["default"] == "BTCUSDT"

    # ---------- Yahoo-backed Deriv symbols (real-world equivalents) ----------
    def test_candles_frxXAUUSD_via_yahoo(self, client):
        r = client.get(f"{API}/market/candles?symbol=frxXAUUSD&timeframe=1h", timeout=60)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("available") is True, f"expected available:true, got {d}"
        assert isinstance(d["candles"], list) and len(d["candles"]) > 0
        c = d["candles"][0]
        for k in ["o", "h", "l", "c", "t"]:
            assert k in c

    def test_candles_cryBTCUSD_4h_via_yahoo(self, client):
        r = client.get(f"{API}/market/candles?symbol=cryBTCUSD&timeframe=4h", timeout=60)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("available") is True
        assert len(d["candles"]) > 0

    def test_candles_frxEURUSD_via_yahoo(self, client):
        r = client.get(f"{API}/market/candles?symbol=frxEURUSD&timeframe=1h", timeout=60)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("available") is True
        assert len(d["candles"]) > 0

    def test_candles_OTC_SPC_via_yahoo(self, client):
        r = client.get(f"{API}/market/candles?symbol=OTC_SPC&timeframe=1d", timeout=60)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("available") is True
        assert len(d["candles"]) > 0

    def test_outlook_frxXAUUSD_via_yahoo(self, client):
        r = client.get(f"{API}/market/outlook?symbol=frxXAUUSD&timeframe=1h", timeout=60)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("available") is True
        assert d["direction"] in ("Up", "Down", "Neutral")
        assert isinstance(d["confidence"], int) and 0 <= d["confidence"] <= 100

    def test_live_frxXAUUSD_via_yahoo(self, client):
        r = client.get(f"{API}/market/live?symbol=frxXAUUSD&timeframe=1h", timeout=60)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("price") is not None
        assert d.get("source") == "yahoo"

    # ---------- Synthetic/Volatility (Deriv-only) graceful message ----------
    def test_candles_R_100_synthetic_message(self, client):
        r = client.get(f"{API}/market/candles?symbol=R_100&timeframe=1h", timeout=30)
        assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text[:200]}"
        d = r.json()
        assert d.get("available") is False
        reason = (d.get("reason") or "").lower()
        assert "synthetic" in reason or "volatility" in reason or "deriv" in reason, \
            f"reason should mention Deriv-only synthetic/volatility indices, got: {d.get('reason')!r}"


# ---------- Settings / Alerts (Resend) ----------
class TestSettingsAlerts:
    def test_get_put_settings(self, client):
        r = client.get(f"{API}/settings", timeout=30)
        assert r.status_code == 200
        r = client.put(f"{API}/settings", json={"alert_emails": ["delivered@resend.dev"], "email_enabled": True}, timeout=30)
        assert r.status_code == 200
        assert "delivered@resend.dev" in (r.json().get("alert_emails") or [])

    def test_alerts_test_email(self, client):
        client.put(f"{API}/settings", json={"alert_emails": ["delivered@resend.dev"]}, timeout=30)
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
