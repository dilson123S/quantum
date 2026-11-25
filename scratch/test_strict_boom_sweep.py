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

    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.rolling(window=14, min_periods=14).mean()
    avg_loss = loss.rolling(window=14, min_periods=14).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = (100.0 - (100.0 / (1.0 + rs))).fillna(50.0)

    return df

def test_config(symbol, df_m5, df_m15, regime_filter="m15", rsi_max=48.0, touch_atr=0.45, close_near_atr=0.55, min_sl_pts=15.0):
    df_m5 = calculate_indicators(df_m5)
    df_m15 = calculate_indicators(df_m15)

    m15_map = {row["time"]: (row["close"], row["ema_50"]) for _, row in df_m15.iterrows()}

    trades = []
    in_pos = False
    entry_price = sl_price = tp_price = 0.0
    entry_bar = 0

    n_bars = len(df_m5)
    days = (df_m5["time"].iloc[-1] - df_m5["time"].iloc[0]).total_seconds() / 86400.0

    for i in range(50, n_bars - 1):
        c_high = df_m5["high"].iloc[i]
        c_low = df_m5["low"].iloc[i]
        c_close = df_m5["close"].iloc[i]
        c_atr = df_m5["atr"].iloc[i]
        c_rsi = df_m5["rsi"].iloc[i]
        curr_time = df_m5["time"].iloc[i]

        if in_pos:
            if c_high >= tp_price:
                trades.append({"pnl_r": 2.0, "win": 1})
                in_pos = False
            elif c_low <= sl_price:
                trades.append({"pnl_r": -1.0, "win": 0})
                in_pos = False
            elif (i - entry_bar) >= 10:
                exit_p = c_close
                r = (exit_p - entry_price) / abs(entry_price - sl_price)
                trades.append({"pnl_r": r, "win": 1 if r > 0 else 0})
                in_pos = False
            continue

        # M15 regime filter
        if regime_filter == "m15":
            m15_time = curr_time.floor("15min")
            m15_data = m15_map.get(m15_time)
            if m15_data and m15_data[0] < m15_data[1]:
                continue # Block counter-trend M15

        if c_rsi > rsi_max:
            continue

        prior_bars = df_m5.iloc[max(0, i-25):i]
        structural_low = float(prior_bars["low"].min())
        demand_zone = structural_low

        # Jump cluster check
        spike_bars = prior_bars[prior_bars["is_boom_spike"]].tail(5)
        has_recent_spike = False
        for idx in spike_bars.index:
            bars_ago = i - df_m5.index.get_loc(idx)
            if 1 <= bars_ago <= 6:
                last_spike_origin = float(df_m5.loc[idx, "low"])
                if abs(c_close - last_spike_origin) <= c_atr * close_near_atr:
                    has_recent_spike = True
                    demand_zone = last_spike_origin
                    break

        touch_low = c_low <= demand_zone + (c_atr * touch_atr)
        close_near = abs(c_close - demand_zone) <= (c_atr * close_near_atr)
        not_broken = c_close >= demand_zone - (c_atr * 0.40)

        # Candlestick rejection / wick test
        bar_range = c_high - c_low
        has_wick = (bar_range <= 0.5) or (c_close >= c_low + (bar_range * 0.15))

        if (touch_low and close_near and not_broken and has_wick) or has_recent_spike:
            entry_price = c_close
            sl_buffer = max(c_atr * 1.0, min_sl_pts)
            sl_price = demand_zone - sl_buffer
            risk_pts = max(abs(entry_price - sl_price), min_sl_pts)
            sl_price = entry_price - risk_pts
            tp_price = entry_price + (risk_pts * 2.0)
            in_pos = True
            entry_bar = i

    df_res = pd.DataFrame(trades)
    if len(df_res) == 0:
        return None
    wins = len(df_res[df_res["pnl_r"] > 0])
    total = len(df_res)
    wr = (wins / total) * 100.0
    net_r = df_res["pnl_r"].sum()
    gw = df_res[df_res["pnl_r"] > 0]["pnl_r"].sum()
    gl = abs(df_res[df_res["pnl_r"] < 0]["pnl_r"].sum())
    pf = gw / gl if gl > 0 else 99.0
    tpd = total / days
    return {"tpd": tpd, "total": total, "wr": wr, "net_r": net_r, "pf": pf, "days": days}

if __name__ == "__main__":
    mt5.initialize()
    for sym, sl in [("Boom 1000 Index", 15.0), ("Boom 500 Index", 8.0)]:
        r_m5 = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M5, 0, 5000)
        r_m15 = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M15, 0, 2000)
        df_m5 = pd.DataFrame(r_m5); df_m5["time"] = pd.to_datetime(df_m5["time"], unit="s")
        df_m15 = pd.DataFrame(r_m15); df_m15["time"] = pd.to_datetime(df_m15["time"], unit="s")

        print(f"\n=================== {sym} (Aiming for 5 - 6 trades/day) ===================")
        for rsi in [48.0, 52.0, 56.0]:
            for touch in [0.40, 0.55, 0.70]:
                res = test_config(sym, df_m5, df_m15, regime_filter="m15", rsi_max=rsi, touch_atr=touch, close_near_atr=touch+0.1, min_sl_pts=sl)
                if res:
                    print(f"RSI<{rsi:.0f} Touch<{touch:.2f} -> Trades/Day: {res['tpd']:4.2f} (Total:{res['total']:3d}) | WinRate:{res['wr']:4.1f}% | Net:{res['net_r']:+6.1f}R | PF:{res['pf']:.2f}")
    mt5.shutdown()
