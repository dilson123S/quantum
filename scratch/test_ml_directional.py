import sys, os
sys.path.insert(0, os.path.abspath("."))

import pandas as pd
from core.config_loader import load_config
from core.constants import Direction
from data.downloader import DataDownloader
from data.cleaner import DataCleaner
from indicators.trend import calculate_all_trend_indicators
from indicators.momentum import calculate_all_momentum_indicators
from indicators.volatility import calculate_all_volatility_indicators
from indicators.volume import calculate_all_volume_indicators
from ml.predictor import MLPredictor
from ml.features import extract_features

from execution.mt5_connector import MT5Connector
connector = MT5Connector()
if not connector.initialize():
    print("Failed to initialize MT5")
    sys.exit(1)

downloader = DataDownloader()
cleaner = DataCleaner()
df_h1_raw = downloader.download_rates("XAUUSD", "H1", count=100)
df_h1, _ = cleaner.clean(df_h1_raw)
cfg = load_config("config/bot_a_gold")

df_h1 = calculate_all_trend_indicators(df_h1, ema_fast_period=20, ema_medium_period=50, ema_slow_period=200, adx_period=14)
df_h1 = calculate_all_momentum_indicators(df_h1, rsi_period=14, macd_fast=12, macd_slow=26, macd_signal=9)
df_h1 = calculate_all_volatility_indicators(df_h1, atr_period=14, atr_avg_period=50)
df_h1 = calculate_all_volume_indicators(df_h1, volume_period=20, volume_col="tick_volume", use_obv=True)

# Test on Bar 11:00 UTC (index -8 or similar where Asian breakdown happened)
predictor = MLPredictor()

print("Testing ML predictions for Bar 11:00 UTC:")
# Let's locate the bar near 4353
target_bar_idx = None
for i in range(len(df_h1)):
    b = df_h1.iloc[i]
    if "11:00" in str(b["time"]):
        target_bar_idx = i

if target_bar_idx is not None:
    sub_df = df_h1.iloc[:target_bar_idx + 1]
    
    # 1. Without directional alignment (Old behavior)
    feat_old = extract_features(sub_df, structure_score=30.0, trend_score=20.0, spread_points=15.0, atr_value=float(sub_df['atr'].iloc[-1]), point_size=0.01)
    X_old = [[feat_old.get(col, 0.0) for col in predictor.features]]
    prob_old = predictor.model.predict_proba(X_old)[0][1]
    print(f"Probabilidad VIEJA (sin dirección): {prob_old*100:.2f}% (¡Rechazada!)")

    # 2. With directional alignment for SELL
    feat_new = feat_old.copy()
    feat_new["ema20_slope"] = -feat_new["ema20_slope"]
    feat_new["ema50_slope"] = -feat_new["ema50_slope"]
    feat_new["price_vs_ema200"] = -feat_new["price_vs_ema200"]
    feat_new["rsi_norm"] = -feat_new["rsi_norm"]
    feat_new["macd_hist_slope"] = -feat_new["macd_hist_slope"]
    feat_new["obv_trend"] = -feat_new["obv_trend"]

    X_new = [[feat_new.get(col, 0.0) for col in predictor.features]]
    prob_new = predictor.model.predict_proba(X_new)[0][1]
    print(f"Probabilidad NUEVA (con dirección SELL): {prob_new*100:.2f}%")
