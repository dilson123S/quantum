"""QuantTradingBot Web Dashboard Runner."""
import argparse
import sys
import os
import uvicorn
import logging

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitoring.logger import setup_logging

def main():
    parser = argparse.ArgumentParser(description="QuantTradingBot Web Dashboard")
    parser.add_argument("--host", default="127.0.0.1", help="Host IP to bind (default: 127.0.0.1, local only)")
    parser.add_argument("--port", type=int, default=8050, help="Port to bind (default: 8050, avoids Docker 8000 conflict)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")
    parser.add_argument(
        "--runtime-mode", choices=["paper", "demo"], default=None,
        help="Read-only display of the mode used by the launched bot processes.",
    )

    args = parser.parse_args()
    if args.runtime_mode:
        # This does not switch a bot or grant execution permission. It only
        # keeps the UI from presenting a stale YAML mode as runtime fact.
        os.environ["BOT_RUNTIME_MODE"] = args.runtime_mode.upper()

    setup_logging(log_dir="logs", log_level="INFO", bot_name="QuantTradingDashboard")
    
    print("=" * 60)
    print("      QUANT TRADING BOT -- WEB DASHBOARD TERMINAL")
    print("=" * 60)
    print(f"\n[OK] Servidor iniciado en: http://localhost:{args.port}")
    print(f"[OK] Acceso local y red: http://{args.host}:{args.port}")
    print("\nPresiona Ctrl+C para detener el servidor.\n")

    uvicorn.run(
        "monitoring.web.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="warning"
    )

if __name__ == "__main__":
    main()
