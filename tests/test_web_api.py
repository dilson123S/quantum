"""Test FastAPI Web Dashboard Endpoints."""
import pytest
from fastapi.testclient import TestClient
from monitoring.web.server import app

client = TestClient(app)


def test_index_page():
    res = client.get("/")
    assert res.status_code == 200
    assert "QuantTradingBot" in res.text


def test_api_status():
    res = client.get("/api/status")
    assert res.status_code == 200
    data = res.json()
    assert "balance" in data
    assert "equity" in data
    assert "status" in data


def test_intraday_dashboard_is_isolated_and_read_only():
    res = client.get("/api/intraday/status")
    assert res.status_code == 200
    data = res.json()
    assert data["profile"]["timeframes"] == {"entry": "M5", "setup": "M15", "higher": "H1"}
    assert data["profile"]["live_trading_allowed"] is False
    assert data["validation"]["status"] in ("REJECTED", "NOT_IN_CURRENT_FLEET")
    assert "today" in data


def test_api_symbols():
    res = client.get("/api/symbols")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert len(data) > 0
    assert "symbol" in data[0]


def test_api_active_trades():
    res = client.get("/api/trades/active")
    assert res.status_code == 200
    assert isinstance(res.json(), list)


def test_api_signals():
    res = client.get("/api/signals")
    assert res.status_code == 200
    assert isinstance(res.json(), list)
    if res.json():
        assert "market_context" in res.json()[0]


def test_api_history():
    res = client.get("/api/trades/history")
    assert res.status_code == 200
    assert isinstance(res.json(), list)


def test_dashboard_controls_fail_closed_when_token_is_unset(monkeypatch):
    monkeypatch.delenv("DASHBOARD_API_TOKEN", raising=False)
    res = client.post("/api/ml/toggle_shadow")
    assert res.status_code == 503


def test_api_candles_returns_rich_indicators():
    res = client.get("/api/candles?symbol=BTCUSD&timeframe=H1&bars=50")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, dict)
    assert "candles" in data
    assert "ema20" in data
    assert "ema50" in data
    assert "ema200" in data
    assert "supports" in data
    assert "resistances" in data
    assert "metrics" in data

