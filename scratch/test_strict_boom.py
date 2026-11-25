import pandas as pd
import numpy as np
import MetaTrader5 as mt5

def calculate_indicators(df):
    df = df.copy()
    close = df["close"]
    high = df["high"]
    low = df["low"]
    open_ = df["open"]

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["atr"] = tr.rolling(window=14).mean().fillna(tr)

    body = (close - open_).abs()
    med_body = body.rolling(window=20).median().fillna(1.0)
    spike_thresh = np.maximum(med_body * 3.0, df["atr"] * 1.5)
    df["is_boom_spike"] = (close - open_) >= spike_thresh
    df["is_crash_spike"] = (open_ - close) >= spike_thresh

    df["ema_50"] = close.ewm(span=50, adjust=False).mean()
    df["ema_200"] = close.ewm(span=200, adjust=False).mean()

    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.rolling(window=14, min_periods=14).mean()
    avg_loss = loss.rolling(window=14, min_periods=14).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = (100.0 - (100.0 / (1.0 + rs))).fillna(50.0)

    return df

def simulate_strategy(symbol, df_m5, df_h1, rsi_max=42.0, touch_atr=0.25, close_near_atr=0.35, cooldown_bars=6, min_sl_pts=15.0):
    df_m5 = calculate_indicators(df_m5)
    df_h1 = calculate_indicators(df_h1)

    # Precompute H1 EMA 50
    h1_map = {}
    for idx, row in df_h1.iterrows():
        h1_map[row["time"]] = (row["close"], row["ema_50"])

    trades = []
    in_pos = False
    entry_price = 0.0
    sl_price = 0.0
    tp_price = 0.0
    entry_bar = 0
    last_trade_bar = -999

    n_bars = len(df_m5)
    days = (df_m5["time"].iloc[-1] - df_m5["time"].iloc[0]).total_seconds() / 86400.0

    for i in range(50, n_bars - 1):
        curr_time = df_m5["time"].iloc[i]
        c_open = df_m5["open"].iloc[i]
        c_high = df_m5["high"].iloc[i]
        c_low = df_m5["low"].iloc[i]
        c_close = df_m5["close"].iloc[i]
        c_atr = df_m5["atr"].iloc[i]
        c_rsi = df_m5["rsi"].iloc[i]
        c_ema50 = df_m5["ema_50"].iloc[i]

        # Manage open position
        if in_pos:
            # Check TP / SL on current bar
            if c_high >= tp_price:
                # Win
                r = (tp_price - entry_price) / abs(entry_price - sl_price)
                trades.append({"result": "WIN", "pnl_r": r, "bars": i - entry_bar})
                in_pos = False
                last_trade_bar = i
            elif c_low <= sl_price:
                # Loss
                trades.append({"result": "LOSS", "pnl_r": -1.0, "bars": i - entry_bar})
                in_pos = False
                last_trade_bar = i
            elif (i - entry_bar) >= 12: # Time decay
                exit_p = c_close
                r = (exit_p - entry_price) / abs(entry_price - sl_price)
                trades.append({"result": "DECAY", "pnl_r": r, "bars": i - entry_bar})
                in_pos = False
                last_trade_bar = i
            continue

        if i - last_trade_bar < cooldown_bars:
            continue

        # H1 Regime filter (find latest completed H1 bar before curr_time)
        h1_time = curr_time.floor("h")
        h1_data = h1_map.get(h1_time)
        if h1_data:
            h1_c, h1_ema = h1_data
            if h1_c < h1_ema:
                continue # Block Boom BUY when H1 is bearish

        # M5 Trend & RSI Filter
        if c_rsi > rsi_max:
            continue

        # Candlestick rejection check: must show absorption wick, not closing on dead bottom
        bar_range = c_high - c_low
        if bar_range > 0.5 and c_close < c_low + (bar_range * 0.15):
            continue

        # Structural Demand Zone (prior completed bars)
        prior_bars = df_m5.iloc[max(0, i-25):i]
        structural_low = float(prior_bars["low"].min())
        demand_zone = structural_low

        # Jump Clustering Detection
        spike_bars = prior_bars[prior_bars["is_boom_spike"]].tail(5)
        has_recent_spike = False
        for idx, row in spike_bars.iterrows():
            bars_ago = i - df_m5.index.get_loc(idx)
            if 1 <= bars_ago <= 6:
                last_spike_origin = float(row["low"])
                if abs(c_close - last_spike_origin) <= c_atr * 0.35:
                    has_recent_spike = True
                    demand_zone = last_spike_origin
                    break

        touch_low = c_low <= demand_zone + (c_atr * touch_atr)
        close_near = abs(c_close - demand_zone) <= (c_atr * close_near_atr)
        not_broken = c_close >= demand_zone - (c_atr * 0.40)

        if (touch_low and close_near and not_broken) or has_recent_spike:
            # Valid Entry
            entry_price = df_m5["close"].iloc[i]
            sl_buffer = max(c_atr * 1.0, min_sl_pts)
            sl_price = demand_zone - sl_buffer
            risk_pts = max(abs(entry_price - sl_price), min_sl_pts)
            sl_price = entry_price - risk_pts
            tp_price = entry_price + (risk_pts * 2.0) # 2.0 RR

            in_pos = True
            entry_bar = i

    df_res = pd.DataFrame(trades)
    if len(df_res) == 0:
        return {"trades": 0, "trades_per_day": 0, "win_rate": 0, "net_r": 0, "pf": 0}

    wins = len(df_res[df_res["pnl_r"] > 0])
    losses = len(df_res[df_res["pnl_r"] < 0])
    total = len(df_res)
    win_rate = (wins / total) * 100.0 if total > 0 else 0.0
    net_r = df_res["pnl_r"].sum()
    gross_win = df_res[df_res["pnl_r"] > 0]["pnl_r"].sum()
    gross_loss = abs(df_res[df_res["pnl_r"] < 0]["pnl_r"].sum())
    pf = (gross_win / gross_loss) if gross_loss > 0 else 999.0
    trades_per_day = total / days

    return {
        "symbol": symbol,
        "days": round(days, 1),
        "trades": total,
        "trades_per_day": round(trades_per_day, 2),
        "win_rate": round(win_rate, 2),
        "net_r": round(net_r, 2),
        "profit_factor": round(pf, 2)
    }

if __name__ == "__main__":
    mt5.initialize()
    for sym, min_sl in [("Boom 1000 Index", 15.0), ("Boom 500 Index", 8.0)]:
        r_m5 = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M5, 0, 5000)
        r_h1 = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H1, 0, 1000)
        df_m5 = pd.DataFrame(r_m5); df_m5["time"] = pd.to_datetime(df_m5["time"], unit="s")
        df_h1 = pd.DataFrame(r_h1); df_h1["time"] = pd.to_datetime(df_h1["time"], unit="s")

        print(f"\n=== Param Sweep for {sym} (Target: 5 to 6 trades/day) ===")
        for rsi_max in [38.0, 42.0, 46.0]:
            for cooldown in [4, 6, 8]:
                res = simulate_strategy(sym, df_m5, df_h1, rsi_max=rsi_max, cooldown_bars=cooldown, min_sl_pts=min_sl)
                print(f"RSI < {rsi_max:4.1f} | Cooldown {cooldown:2d}b -> Trades/day: {res['trades_per_day']:4.2f} (Total: {res['trades']:3d}) | WinRate: {res['win_rate']:4.1f}% | Net R: {res['net_r']:+6.1f}R | PF: {res['profit_factor']:.2f}")
    mt5.shutdown()
