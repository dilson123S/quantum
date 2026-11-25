import sys
sys.path.insert(0, '.')
import pandas as pd
from data.downloader import DataDownloader
from execution.mt5_connector import MT5Connector
from risk.position_size import PositionSizeCalculator
from risk.stop_loss import StopLossCalculator
from core.config_loader import load_config
from core.constants import Direction, SignalClassification
from indicators.structure import get_market_structure
from indicators.volatility import calculate_all_volatility_indicators

connector = MT5Connector()
if connector.initialize():
    cfg = load_config('config/demo_gold_h4')
    dl = DataDownloader()
    df = dl.download_rates('XAUUSD', 'H4', 650)
    df = calculate_all_volatility_indicators(df)
    struct = get_market_structure(df, df['atr'], swing_lookback=3, swing_confirmation=2)
    sl_calc = StopLossCalculator()
    tick = connector.get_symbol_tick('XAUUSD')
    sym_info = connector.get_symbol_info('XAUUSD')
    
    last_bar = df.iloc[-1]
    entry_price = float(last_bar['close'])
    atr_val = float(last_bar['atr'])
    sl_price = sl_calc.calculate_sl(
        direction=Direction.SELL,
        entry_price=entry_price,
        structure_analysis=struct,
        atr_value=atr_val,
        spread_price=0.20,
        config=cfg.volatility,
        symbol_point=sym_info.point
    )
    sl_points = abs(entry_price - sl_price) / sym_info.point
    print(f"Entry: {entry_price}, SL: {sl_price}, Dist: {abs(entry_price - sl_price):.2f}, SL points: {sl_points}")
    pos_sizer = PositionSizeCalculator()
    risk_money = pos_sizer.calculate_risk_money(9217.32, cfg.risk, SignalClassification.GOOD, 0)
    print(f"Risk money: {risk_money}")
    lot = pos_sizer.calculate_lot(
        symbol='XAUUSD',
        risk_money=risk_money,
        sl_distance_points=sl_points,
        tick_value=sym_info.trade_tick_value,
        tick_size=sym_info.trade_tick_size,
        point=sym_info.point,
        volume_min=sym_info.volume_min,
        volume_max=sym_info.volume_max,
        volume_step=sym_info.volume_step,
        min_trade_volume=cfg.risk.min_trade_volume,
    )
    print(f"Calculated lot: {lot}")
    print(f"volume_min: {sym_info.volume_min}")
    connector.shutdown()
else:
    print("Could not initialize MT5")
