"""Alert system for trade notifications."""
from abc import ABC, abstractmethod
from typing import Optional
import logging

from core.models import TradeSetup, ScoreResult, ExecutionResult
from core.constants import Direction

logger = logging.getLogger(__name__)


class AlertProvider(ABC):
    """Abstract base class for alert providers."""

    @abstractmethod
    def send_trade_alert(self, trade_setup: TradeSetup, result: ExecutionResult) -> bool:
        pass

    @abstractmethod
    def send_close_alert(self, symbol: str, direction: str, profit: float, profit_r: float) -> bool:
        pass

    @abstractmethod
    def send_error_alert(self, message: str) -> bool:
        pass

    @abstractmethod
    def send_status_alert(self, message: str) -> bool:
        pass


class ConsoleAlertProvider(AlertProvider):
    """Sends formatted alerts to console with safe ASCII formatting."""

    def send_trade_alert(self, trade_setup: TradeSetup, result: ExecutionResult) -> bool:
        try:
            score = trade_setup.signal.score_result
            tag = "[BUY]" if trade_setup.signal.direction == Direction.BUY else "[SELL]"

            msg = f"""
{'='*50}
{tag} NEW TRADE
{'='*50}
Symbol: {trade_setup.signal.symbol} | Direction: {trade_setup.signal.direction.value}

Score: {score.total_score:.0f}/100 ({score.classification.value})
  * Structure:  {score.structure.score:.0f}/{score.structure.max_score:.0f}
  * Trend:      {score.trend.score:.0f}/{score.trend.max_score:.0f}
  * Momentum:   {score.momentum.score:.0f}/{score.momentum.max_score:.0f}
  * Volume:     {score.volume.score:.0f}/{score.volume.max_score:.0f}
  * Volatility: {score.volatility.score:.0f}/{score.volatility.max_score:.0f}

Entry: {trade_setup.entry_price:.5f}
SL:    {trade_setup.sl_price:.5f}
TP:    {trade_setup.tp_price:.5f}

Risk:  {trade_setup.risk_percent:.2f}% (${trade_setup.risk_money:.2f})
R:R:   {trade_setup.risk_reward_ratio:.1f}
Lot:   {trade_setup.volume:.2f}

Result: {result.message}
{'='*50}"""
            print(msg)
            logger.info(f"Trade alert: {trade_setup.signal.symbol} {trade_setup.signal.direction.value} Score={score.total_score}")
            return True
        except Exception as e:
            logger.error(f"Console alert error: {e}")
            return False

    def send_close_alert(self, symbol: str, direction: str, profit: float, profit_r: float) -> bool:
        tag = "[PROFIT]" if profit > 0 else "[LOSS]"
        msg = f"{tag} CLOSED: {symbol} {direction} | Profit: ${profit:,.2f} ({profit_r:.2f}R)"
        print(msg)
        logger.info(msg)
        return True

    def send_error_alert(self, message: str) -> bool:
        msg = f"[ERROR] {message}"
        print(msg)
        logger.error(msg)
        return True

    def send_status_alert(self, message: str) -> bool:
        print(f"[INFO] {message}")
        return True


import os
import requests


class TelegramAlertProvider(AlertProvider):
    """Telegram alert provider for sending real-time notifications to Telegram."""

    def __init__(self, bot_token: Optional[str] = None, chat_id: Optional[str] = None):
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")
        self.enabled = bool(self.bot_token and self.chat_id)
        if self.enabled:
            logger.info("TelegramAlertProvider initialized with valid credentials.")

    def _send(self, text: str) -> bool:
        if not self.enabled:
            return False
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code == 200:
                return True
            else:
                logger.error(f"Telegram send failed [{resp.status_code}]: {resp.text}")
                return False
        except Exception as e:
            logger.error(f"Telegram request exception: {e}")
            return False

    def send_trade_alert(self, trade_setup: TradeSetup, result: ExecutionResult) -> bool:
        if not self.enabled:
            return False
        try:
            score = trade_setup.signal.score_result
            is_buy = trade_setup.signal.direction == Direction.BUY
            emoji = "🟢 <b>BUY</b>" if is_buy else "🔴 <b>SELL</b>"

            msg = (
                f"⚡ <b>QUANTUM TRADE ALERT</b> ⚡\n\n"
                f"{emoji} | <b>{trade_setup.signal.symbol}</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"<b>Score:</b> {score.total_score:.0f}/100 ({score.classification.value})\n"
                f"• Structure: {score.structure.score:.0f}/{score.structure.max_score:.0f}\n"
                f"• Trend: {score.trend.score:.0f}/{score.trend.max_score:.0f}\n"
                f"• Momentum: {score.momentum.score:.0f}/{score.momentum.max_score:.0f}\n"
                f"• Volume: {score.volume.score:.0f}/{score.volume.max_score:.0f}\n"
                f"• Volatility: {score.volatility.score:.0f}/{score.volatility.max_score:.0f}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"<b>Entry:</b> <code>{trade_setup.entry_price:.5f}</code>\n"
                f"<b>SL:</b> <code>{trade_setup.sl_price:.5f}</code>\n"
                f"<b>TP:</b> <code>{trade_setup.tp_price:.5f}</code>\n\n"
                f"<b>Risk:</b> {trade_setup.risk_percent:.2f}% (${trade_setup.risk_money:.2f})\n"
                f"<b>R:R:</b> 1:{trade_setup.risk_reward_ratio:.1f}\n"
                f"<b>Lot Size:</b> {trade_setup.volume:.2f}\n"
                f"<b>Result:</b> {result.message}"
            )
            return self._send(msg)
        except Exception as e:
            logger.error(f"Telegram trade alert format error: {e}")
            return False

    def send_close_alert(self, symbol: str, direction: str, profit: float, profit_r: float) -> bool:
        if not self.enabled:
            return False
        emoji = "💰 <b>PROFIT</b>" if profit > 0 else "🛑 <b>LOSS</b>"
        sign = "+" if profit > 0 else ""
        msg = (
            f"{emoji} <b>POSITION CLOSED</b>\n\n"
            f"<b>Symbol:</b> {symbol} ({direction})\n"
            f"<b>PnL:</b> <code>{sign}${profit:,.2f}</code> ({sign}{profit_r:.2f}R)"
        )
        return self._send(msg)

    def send_error_alert(self, message: str) -> bool:
        if not self.enabled:
            return False
        msg = f"⚠️ <b>QUANTUM ERROR</b>\n\n<code>{message}</code>"
        return self._send(msg)

    def send_status_alert(self, message: str) -> bool:
        if not self.enabled:
            return False
        msg = f"ℹ️ <b>QUANTUM BOT STATUS</b>\n\n{message}"
        return self._send(msg)


class AlertManager:
    """Manages multiple alert providers."""

    def __init__(self):
        self.providers: list[AlertProvider] = []

    def add_provider(self, provider: AlertProvider) -> None:
        self.providers.append(provider)

    def send_trade_alert(self, trade_setup: TradeSetup, result: ExecutionResult) -> None:
        for provider in self.providers:
            try:
                provider.send_trade_alert(trade_setup, result)
            except Exception as e:
                logger.error(f"Alert provider error: {e}")

    def send_close_alert(self, symbol: str, direction: str, profit: float, profit_r: float) -> None:
        for provider in self.providers:
            try:
                provider.send_close_alert(symbol, direction, profit, profit_r)
            except Exception as e:
                logger.error(f"Alert provider error: {e}")

    def send_error_alert(self, message: str) -> None:
        for provider in self.providers:
            try:
                provider.send_error_alert(message)
            except Exception as e:
                logger.error(f"Alert provider error: {e}")

    def send_status_alert(self, message: str) -> None:
        for provider in self.providers:
            try:
                provider.send_status_alert(message)
            except Exception as e:
                logger.error(f"Alert provider error: {e}")
