"""Interactive Telegram Bot Listener for Quantum Trading Bot (MT5 Direct + Journal)."""
import os
import time
import logging
import sqlite3
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("QuantumTelegramBot")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DB_PATH = os.getenv("DB_PATH", "data/journal_demo.db")

ORDER_TYPE_NAMES = {
    0: "BUY",
    1: "SELL",
    2: "BUY LIMIT",
    3: "SELL LIMIT",
    4: "BUY STOP",
    5: "SELL STOP",
    6: "BUY STOP LIMIT",
    7: "SELL STOP LIMIT"
}

def send_message(chat_id: str, text: str, parse_mode: str = "HTML") -> bool:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        return r.status_code == 200
    except Exception as e:
        logger.error(f"Error sending telegram message: {e}")
        return False

def get_mt5_live_data():
    """Queries live MetaTrader 5 terminal directly."""
    if not MT5_AVAILABLE:
        return None
    try:
        if not mt5.initialize():
            return None
        acc = mt5.account_info()
        positions = list(mt5.positions_get() or [])
        orders = list(mt5.orders_get() or [])
        mt5.shutdown()
        return {
            "account": acc,
            "positions": positions,
            "orders": orders
        }
    except Exception as e:
        logger.error(f"MT5 API query error: {e}")
        try:
            mt5.shutdown()
        except Exception:
            pass
        return None

def get_status_report() -> str:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mt5_data = get_mt5_live_data()

    open_positions_count = 0
    pending_orders_count = 0
    balance_str = ""
    equity_str = ""

    if mt5_data and mt5_data["account"]:
        acc = mt5_data["account"]
        balance_str = f"💵 <b>Balance:</b> <code>${acc.balance:,.2f} USD</code>\n📊 <b>Equity:</b> <code>${acc.equity:,.2f} USD</code>\n"
        open_positions_count = len(mt5_data["positions"])
        pending_orders_count = len(mt5_data["orders"])
    else:
        # Fallback to sqlite database
        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM trades WHERE state = 'OPEN'")
            open_positions_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM trades WHERE state = 'ORDER_PENDING'")
            pending_orders_count = cur.fetchone()[0]
            conn.close()
        except Exception as e:
            logger.error(f"DB Error: {e}")

    msg = (
        "⚡ <b>QUANTUM TRADING BOT - ESTADO</b> ⚡\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "🟢 <b>Sistema:</b> OPERATIVO / MONITOREANDO\n"
        "🎯 <b>Modo:</b> DEMO (MetaTrader 5 / Portafolio 100% Oro)\n"
        f"🕒 <b>Hora local:</b> <code>{now_str}</code>\n\n"
    )

    if balance_str:
        msg += balance_str + "\n"

    msg += (
        "📊 <b>Motores en Ejecución:</b>\n"
        "  • <b>XAUUSD (H1):</b> Breakout + Sweep Reversal (Riesgo 1.0%)\n"
        "  • <b>XAUUSD (H4):</b> Trend Breakout + Sweep Reversal (Riesgo 1.5%)\n\n"
        f"📈 <b>Operaciones activas:</b> <code>{open_positions_count}</code>\n"
        f"⏳ <b>Órdenes pendientes:</b> <code>{pending_orders_count}</code>\n"
    )

    if mt5_data and mt5_data["orders"]:
        msg += "\n📌 <b>Pendientes en MT5 (XAUUSD):</b>\n"
        for o in mt5_data["orders"]:
            if getattr(o, "symbol", "") != "XAUUSD":
                continue
            order_type = ORDER_TYPE_NAMES.get(o.type, f"Tipo {o.type}")
            msg += (
                f"  • <b>{o.symbol}</b> ({order_type})\n"
                f"    Precio: <code>{o.price_open:.2f}</code> | Lote: <code>{o.volume_current}</code>\n"
            )

    msg += (
        "\n━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 <i>Usa /trades (órdenes), /stats (rendimiento) o /news (noticias USD).</i>"
    )
    return msg

def get_trades_report() -> str:
    mt5_data = get_mt5_live_data()

    if mt5_data and (mt5_data["positions"] or mt5_data["orders"]):
        msg = "📋 <b>ÓRDENES EN METATRADER 5:</b>\n━━━━━━━━━━━━━━━━━━━━━\n"

        if mt5_data["positions"]:
            msg += "🟢 <b>POSICIONES ABIERTAS (EN VIVO):</b>\n"
            for p in mt5_data["positions"]:
                p_type = "BUY" if p.type == 0 else "SELL"
                emoji = "🟢" if p.profit >= 0 else "🔴"
                sign = "+" if p.profit >= 0 else ""
                msg += (
                    f"{emoji} <b>{p.symbol}</b> ({p_type})\n"
                    f"• Ticket: <code>{p.ticket}</code>\n"
                    f"• Entrada: <code>{p.price_open:.2f}</code> | Actual: <code>{p.price_current:.2f}</code>\n"
                    f"• SL: <code>{p.sl:.2f}</code> | TP: <code>{p.tp:.2f}</code>\n"
                    f"• Lote: <code>{p.volume}</code> | Flotante: <code>{sign}${p.profit:,.2f}</code>\n"
                    "─────────────────────\n"
                )

        if mt5_data["orders"]:
            msg += "⏳ <b>ÓRDENES PENDIENTES (LIMIT / STOP):</b>\n"
            for o in mt5_data["orders"]:
                order_type = ORDER_TYPE_NAMES.get(o.type, f"Tipo {o.type}")
                msg += (
                    f"🔷 <b>{o.symbol}</b> [{order_type}]\n"
                    f"• Ticket: <code>{o.ticket}</code>\n"
                    f"• Precio Activación: <code>{o.price_open:.2f}</code>\n"
                    f"• SL: <code>{o.sl:.2f}</code> | TP: <code>{o.tp:.2f}</code>\n"
                    f"• Lote: <code>{o.volume_current}</code>\n"
                    "─────────────────────\n"
                )
        return msg

    # Fallback to database
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            SELECT symbol, direction, entry_price, sl_price, tp_price, volume, state, total_score, ticket
            FROM trades
            WHERE state IN ('OPEN', 'ORDER_PENDING') AND symbol = 'XAUUSD'
            ORDER BY id DESC
        """)
        rows = cur.fetchall()
        conn.close()

        if not rows:
            return (
                "ℹ️ <b>Sin operaciones activas ni pendientes en Oro</b>\n\n"
                "El bot está analizando las velas de XAUUSD (H1 y H4)."
            )

        msg = "📋 <b>ÓRDENES REGISTRADAS EN QUANTUM:</b>\n━━━━━━━━━━━━━━━━━━━━━\n"
        for sym, direct, entry, sl, tp, vol, state, score, ticket in rows:
            emoji = "🟢 BUY" if direct == "BUY" else "🔴 SELL"
            msg += (
                f"{emoji} <b>{sym}</b> [{state}]\n"
                f"• Ticket: <code>{ticket}</code>\n"
                f"• Entrada: <code>{entry:.2f}</code>\n"
                f"• SL: <code>{sl:.2f}</code> | TP: <code>{tp:.2f}</code>\n"
                f"• Lote: <code>{vol:.2f}</code> | Score: <code>{score:.0f}/100</code>\n"
                "─────────────────────\n"
            )
        return msg
    except Exception as e:
        return f"⚠️ Error consultando órdenes: {e}"

def get_stats_report() -> str:
    mt5_data = get_mt5_live_data()
    acc_msg = ""
    if mt5_data and mt5_data["account"]:
        acc = mt5_data["account"]
        acc_msg = (
            f"💵 <b>Balance MT5:</b> <code>${acc.balance:,.2f} USD</code>\n"
            f"📊 <b>Equidad:</b> <code>${acc.equity:,.2f} USD</code>\n"
            f"🛡️ <b>Margen Libre:</b> <code>${acc.margin_free:,.2f} USD</code>\n\n"
        )

    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        # All-time Gold
        cur.execute("""
            SELECT COUNT(*),
                   SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END),
                   SUM(CASE WHEN profit < 0 THEN 1 ELSE 0 END),
                   SUM(profit),
                   SUM(profit_r),
                   SUM(CASE WHEN profit > 0 THEN profit ELSE 0 END),
                   SUM(CASE WHEN profit < 0 THEN abs(profit) ELSE 0 END)
            FROM trades
            WHERE state = 'CLOSED' AND symbol = 'XAUUSD'
        """)
        row = cur.fetchone()
        
        # Today Gold
        cur.execute("""
            SELECT COUNT(*),
                   SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END),
                   SUM(profit)
            FROM trades
            WHERE state = 'CLOSED' AND symbol = 'XAUUSD' AND date(exited_at) = date('now')
        """)
        row_today = cur.fetchone()
        conn.close()

        total = row[0] or 0
        wins = row[1] or 0
        losses = row[2] or 0
        net_pnl = row[3] or 0.0
        net_r = row[4] or 0.0
        gp = row[5] or 0.0
        gl = row[6] or 0.001
        pf = gp / gl if gl > 0 else 99.0
        win_rate = (wins / total * 100) if total > 0 else 0.0

        today_trades = row_today[0] or 0
        today_wins = row_today[1] or 0
        today_pnl = row_today[2] or 0.0

        pnl_sign = "+" if net_pnl >= 0 else ""
        today_sign = "+" if today_pnl >= 0 else ""
        r_sign = "+" if net_r >= 0 else ""

        return (
            "📊 <b>ESTADÍSTICAS ORO (XAUUSD) - QUANTUM</b> 📊\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            + acc_msg +
            f"📅 <b>P&amp;L Hoy:</b> <code>{today_sign}${today_pnl:,.2f} USD</code> ({today_trades} trades | {today_wins}W)\n"
            f"🏆 <b>P&amp;L Acumulado:</b> <code>{pnl_sign}${net_pnl:,.2f} USD</code>\n"
            f"🎯 <b>Win Rate:</b> <code>{win_rate:.1f}%</code> ({wins}W · {losses}L)\n"
            f"⚖️ <b>Profit Factor:</b> <code>{pf:.2f}</code>\n"
            f"📈 <b>Expectancy Total:</b> <code>{r_sign}{net_r:.2f}R</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "🥇 <i>Portafolio Exclusivo Oro: H1 Breakout &amp; H4 Trend</i>"
        )
    except Exception as e:
        return f"⚠️ Error consultando estadísticas: {e}"

def get_news_report() -> str:
    try:
        from filters.news_filter import ForexFactoryNewsProvider
        provider = ForexFactoryNewsProvider()
        events = provider.get_upcoming_events("XAUUSD", hours_ahead=24)
        if not events:
            return (
                "📰 <b>CALENDARIO ECONÓMICO (XAUUSD / USD)</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                "✅ <b>Sin noticias de alto impacto en las próximas 24 horas.</b>\n"
                "El bot puede operar normalmente sin bloqueos."
            )
        msg = (
            "📰 <b>CALENDARIO ECONÓMICO - PRÓXIMAS 24H (USD)</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "🛡️ <i>Filtro Activo: Bloqueo ±30 min en eventos de Alto Impacto.</i>\n\n"
        )
        now_utc = datetime.now(timezone.utc)
        for ev in events[:8]:
            ev_time = ev.get("time_utc")
            title = ev.get("title", "Noticia Económica")
            currency = ev.get("country", "USD")
            forecast = ev.get("forecast", "")
            previous = ev.get("previous", "")
            
            diff_minutes = (ev_time - now_utc).total_seconds() / 60
            if diff_minutes < 0:
                time_rel = f"hace {abs(int(diff_minutes))} min"
            else:
                time_rel = f"en {int(diff_minutes)} min"
            
            msg += (
                f"🔴 <b>{title}</b> ({currency})\n"
                f"🕒 <code>{ev_time.strftime('%Y-%m-%d %H:%M UTC')}</code> ({time_rel})\n"
            )
            if forecast or previous:
                msg += f"📊 Previsión: <code>{forecast}</code> | Previo: <code>{previous}</code>\n"
            msg += "─────────────────────\n"
        return msg
    except Exception as e:
        logger.error(f"Error generating news report: {e}")
        return f"⚠️ Error consultando calendario de noticias: {e}"

def get_help_message() -> str:
    return (
        "🤖 <b>QUANTUM TRADING BOT - MENÚ DE COMANDOS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Aquí puedes monitorear tu cuenta y bots de MetaTrader 5:\n\n"
        "/status - Ver estado general, balance y órdenes pendientes\n"
        "/trades - Ver detalle completo de órdenes (SL, TP, Lote)\n"
        "/stats - Ver balance, equidad y estadísticas de trades\n"
        "/news - Ver noticias económicas de alto impacto (USD/Oro)\n"
        "/help - Ver esta lista de comandos\n"
    )

def main():
    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN no configurado en .env!")
        return

    logger.info(f"Quantum Telegram Bot Listener iniciado. Base de datos: {DB_PATH}")
    offset = 0

    while True:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
            params = {"offset": offset, "timeout": 15}
            resp = requests.get(url, params=params, timeout=20)

            if resp.status_code != 200:
                time.sleep(3)
                continue

            data = resp.json()
            if not data.get("ok"):
                time.sleep(3)
                continue

            updates = data.get("result", [])
            for u in updates:
                offset = max(offset, u["update_id"] + 1)
                msg = u.get("message")
                if not msg:
                    continue

                chat_id = str(msg.get("chat", {}).get("id"))
                text = (msg.get("text") or "").strip().lower()

                if ALLOWED_CHAT_ID and chat_id != str(ALLOWED_CHAT_ID):
                    logger.warning(f"Mensaje ignorado de chat_id no autorizado: {chat_id}")
                    continue

                logger.info(f"Comando recibido: '{text}' de chat_id={chat_id}")

                if text.startswith("/status"):
                    send_message(chat_id, get_status_report())

                elif text.startswith("/trades") or text.startswith("/open"):
                    send_message(chat_id, get_trades_report())

                elif text.startswith("/stats") or text.startswith("/profit") or text.startswith("/balance"):
                    send_message(chat_id, get_stats_report())

                elif text.startswith("/news") or text.startswith("/noticias"):
                    send_message(chat_id, get_news_report())

                elif text.startswith("/start") or text.startswith("/help"):
                    send_message(chat_id, get_help_message())

                else:
                    send_message(
                        chat_id,
                        f"❓ Comando no reconocido: <code>{text}</code>\nUsa /status, /trades, /news o /help."
                    )

        except requests.exceptions.RequestException as re:
            logger.warning(f"Network error in getUpdates: {re}")
            time.sleep(5)
        except Exception as e:
            logger.error(f"Unexpected loop error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
