"""Training script for ML Meta-Filter with Walk-Forward Cross-Validation."""
import argparse
import logging
import os
import sys

# Ensure project root is in python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.config_loader import load_config
from execution.mt5_connector import MT5Connector
from ml.dataset_builder import build_dataset
from ml.trainer import train_model

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("TrainML")


def main():
    parser = argparse.ArgumentParser(description="Train ML Meta-Filter for QuantTradingBot")
    parser.add_argument(
        "--symbols",
        default="XAUUSD",
        help="Comma-separated list of symbols (default: XAUUSD)",
    )
    parser.add_argument("--timeframe", default="H1", help="Timeframe (default: H1)")
    parser.add_argument("--months", type=int, default=12, help="Months of historical data (default: 12)")
    parser.add_argument("--bars", type=int, default=10000, help="Max bars per symbol (default: 10000)")
    parser.add_argument("--splits", type=int, default=5, help="Walk-forward splits (default: 5)")
    parser.add_argument("--min-auc", type=float, default=0.52, help="Minimum purged walk-forward AUC-ROC threshold (default: 0.52)")
    parser.add_argument("--min-fold-auc", type=float, default=0.45, help="Minimum single-fold AUC-ROC threshold (default: 0.45)")
    parser.add_argument("--config-dir", default="config/demo_gold" if os.path.exists("config/demo_gold/settings.yaml") else "config", help="Config directory")
    parser.add_argument("--rebuild-dataset", action="store_true", help="Force rebuild dataset by downloading from MT5")
    parser.add_argument("--dataset-only", action="store_true", help="Only build dataset, don't train")
    parser.add_argument("--no-calibrate", action="store_true", help="Disable Platt probability calibration")
    parser.add_argument("--dataset-path", default="data/ml_dataset.csv", help="Path to dataset CSV")
    parser.add_argument("--model-path", default="data/ml_model.joblib", help="Path to output model")
    parser.add_argument("--report-path", default="data/ml_report.json", help="Path to metrics JSON report")

    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    cfg = load_config(args.config_dir)

    print("=" * 65)
    print("       QUANT TRADING BOT -- ML META-FILTER TRAINER")
    print("=" * 65)
    print(f"  * Config Dir:    {args.config_dir}")
    print(f"  * Symbols:       {', '.join(symbols)}")
    print(f"  * Timeframe:     {args.timeframe}")
    print(f"  * Max Bars:      {args.bars}")
    print(f"  * Walk-Forward:  {args.splits} purged temporal folds")
    print(f"  * Min AUC-ROC:   {args.min_auc}")
    print(f"  * Calibrated:    {not args.no_calibrate}")
    print("=" * 65)

    # 1. Connect to MT5 if dataset needs to be built
    if args.rebuild_dataset or not os.path.exists(args.dataset_path):
        print("\n[1/3] Connecting to MetaTrader 5 to download training data...")
        connector = MT5Connector()
        if connector.initialize():
            print("[OK] MT5 Connected successfully.")
            try:
                print("\n[2/3] Building feature dataset from MT5 history...")
                df = build_dataset(
                    symbols=symbols,
                    timeframe=args.timeframe,
                    months=args.months,
                    bars_per_download=args.bars,
                    output_path=args.dataset_path,
                    config=cfg,
                )
                if df.empty:
                    print("[ERROR] Failed to build dataset. Exiting.")
                    connector.shutdown()
                    return
                print(f"[OK] Dataset built: {len(df)} rows across {len(symbols)} symbols.")
            finally:
                connector.shutdown()
        else:
            if os.path.exists(args.dataset_path):
                print("[WARN] MT5 not available, but existing dataset found. Proceeding with existing data.")
            else:
                print("[ERROR] MT5 could not be initialized and no existing dataset found.")
                return
    else:
        print(f"\n[1/3] Using existing dataset at {args.dataset_path}")

    if args.dataset_only:
        print("\n[INFO] --dataset-only specified. Done.")
        return

    # 2. Train model with Walk-Forward cross validation
    print("\n[3/3] Training XGBoost with Walk-Forward Cross-Validation...")
    success, report = train_model(
        dataset_path=args.dataset_path,
        model_path=args.model_path,
        report_path=args.report_path,
        n_splits=args.splits,
        min_auc_threshold=args.min_auc,
        min_fold_auc_threshold=args.min_fold_auc,
        calibrate=not args.no_calibrate,
    )

    print("\n" + "=" * 65)
    print("                  TRAINING RESULTS")
    print("=" * 65)

    if not success:
        print("[REJECTED] MODEL STATUS: REJECTED")
        print(f"   Reason: {report.get('error') or 'Average AUC-ROC below threshold'}")
        if "avg_auc_roc" in report:
            print(f"   Avg AUC-ROC:   {report.get('avg_auc_roc', 0.0):.4f} (Threshold: {args.min_auc})")
        print("   The model will NOT be used to prevent overfitting / negative edge.")
    else:
        print("[ACCEPTED] MODEL STATUS: ACCEPTED & SAVED")
        print(f"   Model file:    {args.model_path}")
        print(f"   Report file:   {args.report_path}")
        print(f"   Avg AUC-ROC:   {report.get('avg_auc_roc', 0.0):.4f}")
        print(f"   Avg Accuracy:  {report.get('avg_accuracy', 0.0) * 100:.2f}%")
        print(f"   Avg Precision: {report.get('avg_precision', 0.0) * 100:.2f}%")
        print(f"   Avg F1 Score:  {report.get('avg_f1', 0.0):.4f}")

        print("\n   --- Top 8 Feature Importances ---")
        for item in report.get("feature_importance", [])[:8]:
            bar = "=" * int(item["importance"] * 50)
            print(f"   {item['feature']:<22} | {item['importance']:.4f} {bar}")

    print("=" * 65)


if __name__ == "__main__":
    main()
