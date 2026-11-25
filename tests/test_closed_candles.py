"""Regression tests for immutable-bar and MT5 pending-order semantics."""
from types import SimpleNamespace

import data.downloader as downloader_module
import execution.mt5_connector as connector_module
from data.downloader import DataDownloader
from execution.mt5_connector import MT5Connector


def test_downloader_excludes_forming_mt5_candle_by_default(monkeypatch):
    class FakeMT5:
        def __init__(self):
            self.starts = []

        def copy_rates_from_pos(self, symbol, timeframe, start_pos, count):
            self.starts.append(start_pos)
            return [(1, 1.0, 1.1, 0.9, 1.0, 10, 1, 0)] * count

        @staticmethod
        def last_error():
            return "unused"

    fake = FakeMT5()
    monkeypatch.setattr(downloader_module, "mt5", fake)
    downloader = DataDownloader()
    assert downloader.download_rates("EURUSD", "M15", count=2) is not None
    assert downloader.download_rates("EURUSD", "M15", count=2, closed_only=False) is not None
    assert fake.starts == [1, 0]


def test_placed_pending_order_is_not_retried(monkeypatch):
    class FakeMT5:
        TRADE_RETCODE_DONE = 10009
        TRADE_RETCODE_PLACED = 10008

        def __init__(self):
            self.calls = 0

        def order_send(self, request):
            self.calls += 1
            return SimpleNamespace(retcode=10008, comment="placed")

    fake = FakeMT5()
    monkeypatch.setattr(connector_module, "MT5_AVAILABLE", True)
    monkeypatch.setattr(connector_module, "mt5", fake)
    connector = MT5Connector(mock_mode=False)
    result = connector.execute_with_retry({"action": 5, "symbol": "EURUSD"}, max_retries=3)
    assert result.retcode == 10008
    assert fake.calls == 1
