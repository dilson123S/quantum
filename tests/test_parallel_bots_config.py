import pytest
from unittest.mock import MagicMock
from datetime import datetime, timezone

from core.config_loader import load_config
from execution.order_manager import OrderManager
from core.models import TradeSetup, Signal, ScoreResult, ComponentScore, Direction

def test_parallel_bots_configurations():
    cs = ComponentScore(name='test', score=20.0, max_score=30.0, weight=30.0, details={})
    sr = ScoreResult(
        direction=Direction.BUY,
        timestamp=datetime.now(timezone.utc),
        total_score=80.0,
        structure=cs, trend=cs, momentum=cs, volume=cs, volatility=cs,
        classification='GOOD'
    )

    configs = [
        ('config/bot_a_gold', 26082511, 'BOT-A', True, 0.45, 5),
        ('config/bot_b_gold', 26082512, 'BOT-B', True, 0.55, 5),
        ('config/bot_c_gold', 26082513, 'BOT-C', True, 0.65, 5),
        ('config/bot_d_gold', 26082514, 'BOT-D', False, 0.65, 5),
    ]

    magics = set()
    journals = set()

    for folder, expected_magic, expected_tag, expected_ml_active, expected_conf, expected_stag in configs:
        cfg = load_config(folder)
        assert cfg.bot.magic_number == expected_magic
        assert cfg.bot.magic_number not in magics
        magics.add(cfg.bot.magic_number)

        assert cfg.bot.journal_path not in journals
        journals.add(cfg.bot.journal_path)

        assert cfg.bot.comment_tag == expected_tag
        assert cfg.risk.risk_percent == 0.5
        assert cfg.trade.stagnation_exit_bars == expected_stag

        ml_is_active = cfg.ml.allow_active_filter and not cfg.ml.shadow_mode
        assert ml_is_active == expected_ml_active
        assert cfg.ml.min_confidence == expected_conf

        om = OrderManager(MagicMock(), cfg.bot)
        sig = Signal(
            symbol='XAUUSD',
            timeframe='H1',
            direction=Direction.BUY,
            score_result=sr,
            created_at=datetime.now(timezone.utc)
        )
        setup = TradeSetup(
            signal=sig,
            entry_price=2650.0,
            sl_price=2640.0,
            tp_price=2670.0,
            volume=0.05,
            risk_percent=0.5,
            risk_money=46.0,
            risk_reward_ratio=2.0
        )
        comment = om._comment_for(setup)
        assert len(comment) <= 31
        assert comment.startswith(expected_tag + ':')