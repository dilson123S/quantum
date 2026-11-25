"""Unit tests for context filters."""
import pytest
from datetime import datetime, timezone
from types import SimpleNamespace

from core.constants import Direction, RiskMode
from core.config_loader import FilterConfig, RiskConfig, SymbolConfig
from filters.spread_filter import SpreadFilter
from filters.session_filter import SessionFilter
from filters.drawdown_filter import DrawdownFilter
from filters.correlation_filter import CorrelationFilter
from filters.news_filter import NewsFilter, StubNewsProvider
from filters.context_filter import ContextFilter


def test_spread_filter():
    f = SpreadFilter()
    cfg = FilterConfig(
        max_spread=True,
        session_filter=True,
        news_filter=False,
        correlation_filter=True,
        trading_start="07:00",
        trading_end="17:00",
        timezone="UTC",
        max_spread_atr_percent=10,
        news_blackout_before=30,
        news_blackout_after=30
    )
    sym_cfg = SymbolConfig(volume_mode="tick", max_spread_points=20, session_start="07:00", session_end="17:00")

    # Valid spread
    res_ok = f.check(
        symbol="EURUSD",
        current_spread_points=10.0,
        atr_value=0.0020,
        point_size=0.00001,
        config=cfg,
        symbol_config=sym_cfg
    )
    assert res_ok.approved is True

    # Spread points exceeded
    res_bad_pts = f.check(
        symbol="EURUSD",
        current_spread_points=35.0,
        atr_value=0.0020,
        point_size=0.00001,
        config=cfg,
        symbol_config=sym_cfg
    )
    assert res_bad_pts.approved is False


def test_session_filter():
    f = SessionFilter()
    cfg = FilterConfig(
        max_spread=True,
        session_filter=True,
        news_filter=False,
        correlation_filter=True,
        trading_start="07:00",
        trading_end="17:00",
        timezone="UTC",
        max_spread_atr_percent=10,
        news_blackout_before=30,
        news_blackout_after=30
    )

    # 10:00 UTC -> within 07:00 - 17:00
    t_in = datetime(2026, 8, 19, 10, 0, 0, tzinfo=timezone.utc)
    res_in = f.check(t_in, "EURUSD", cfg)
    assert res_in.approved is True

    # 20:00 UTC -> out of session
    t_out = datetime(2026, 8, 19, 20, 0, 0, tzinfo=timezone.utc)
    res_out = f.check(t_out, "EURUSD", cfg)
    assert res_out.approved is False

    # Test excluded hours (e.g. 09:00 - 12:00 dead zone)
    from core.config_loader import SymbolConfig
    sym_cfg_excluded = SymbolConfig(
        session_start="07:00",
        session_end="18:00",
        excluded_hours=[9, 10, 11, 12]
    )
    # 10:00 UTC -> excluded
    res_ex = f.check(t_in, "XAUUSD", cfg, symbol_config=sym_cfg_excluded)
    assert res_ex.approved is False
    assert "excluded dead-volume hours" in res_ex.reasons[0]

    # 14:00 UTC -> allowed
    t_ny = datetime(2026, 8, 19, 14, 0, 0, tzinfo=timezone.utc)
    res_ny = f.check(t_ny, "XAUUSD", cfg, symbol_config=sym_cfg_excluded)
    assert res_ny.approved is True


def test_drawdown_filter():
    f = DrawdownFilter()
    cfg = RiskConfig(
        mode=RiskMode.PERCENT,
        risk_percent=1.0,
        risk_money=20.0,
        max_daily_loss=2.0,
        max_drawdown=10.0,
        max_consecutive_losses=3,
        max_open_positions=3,
        max_positions_per_symbol=1,
        max_correlated_positions=2,
        max_risk_per_trade=1.0,
        max_correlation=0.80
    )

    # Normal conditions
    res_ok = f.check(
        current_equity=10100.0,
        current_balance=10000.0,
        daily_pnl=50.0,
        consecutive_losses=0,
        config=cfg
    )
    assert res_ok.approved is True

    # Daily loss tripped
    res_loss = f.check(
        current_equity=9750.0,
        current_balance=10000.0,
        daily_pnl=-250.0,
        consecutive_losses=1,
        config=cfg
    )
    assert res_loss.approved is False


def test_news_filter():
    news_provider = StubNewsProvider()
    f = NewsFilter(provider=news_provider)
    cfg = FilterConfig(
        max_spread=True,
        session_filter=True,
        news_filter=False,
        correlation_filter=True,
        trading_start="07:00",
        trading_end="17:00",
        timezone="UTC",
        max_spread_atr_percent=10,
        news_blackout_before=30,
        news_blackout_after=30
    )
    res = f.is_news_blackout(datetime.now(timezone.utc), "EURUSD", cfg)
    assert res.approved is True


def test_enabled_news_filter_fails_closed_without_a_provider():
    cfg = FilterConfig(news_filter=True)
    result = NewsFilter(provider=StubNewsProvider()).is_news_blackout(
        datetime.now(timezone.utc), "EURUSD", cfg
    )
    assert result.approved is False
    assert "no reliable news provider" in result.reasons[0].lower()


def test_context_filter_enforces_position_limits():
    cfg = RiskConfig(max_open_positions=2, max_positions_per_symbol=1)
    app_cfg = SimpleNamespace(
        filters=FilterConfig(session_filter=False, max_spread=False, correlation_filter=False),
        risk=cfg,
        symbols={},
    )
    context = ContextFilter(app_cfg)
    positions = [
        SimpleNamespace(symbol="EURUSD"),
        SimpleNamespace(symbol="GBPUSD"),
    ]
    result = context.check_all(
        symbol="USDJPY",
        direction=Direction.BUY,
        current_time_utc=datetime.now(timezone.utc),
        current_spread_points=1.0,
        atr_value=0.001,
        point_size=0.00001,
        current_equity=10_000.0,
        current_balance=10_000.0,
        daily_pnl=0.0,
        consecutive_losses=0,
        open_positions=positions,
        price_history_dict={},
    )
    assert result.approved is False
    assert "Maximum open bot positions" in result.reasons[0]
