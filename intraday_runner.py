"""Deprecated compatibility entrypoint for the intraday profile.

The original standalone runner had a divergent market-order route and a
separate YAML schema.  Keeping two execution engines makes safety review
impossible, so this module delegates to the single reconciled main loop.
"""
import argparse

from main import QuantTradingBot


def main() -> None:
    parser = argparse.ArgumentParser(description="QuantTradingBot intraday profile")
    parser.add_argument("--config", default="config/intraday", help="Canonical intraday profile directory")
    parser.add_argument("--mode", choices=["paper", "demo", "live"], help="Explicit mode override")
    parser.add_argument("--symbols", help="Comma-separated symbol override")
    args = parser.parse_args()

    bot = QuantTradingBot(config_dir=args.config, mode_override=args.mode)
    if args.symbols:
        bot.config.market.symbols = [symbol.strip() for symbol in args.symbols.split(",") if symbol.strip()]
    bot.run()


if __name__ == "__main__":
    main()
