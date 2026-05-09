# engine/breakout_radar.py
# ============================================================
#  突破雷達：箱型整理 + 突破 + 爆量 偵測 + 當日分點彙總
#
#  入榜條件：
#    1. 箱型：過去 5 個交易日（不含今日）振幅 < 7%
#    2. 突破：向上 = 收盤 > 箱型上緣 × 1.03
#            向下 = 收盤 < 箱型下緣 × 0.97
#    3. 爆量：當日成交量 > 過去 5 日平均成交量 × 5
#    必須同時成立才視為「合格」(qualified_up / qualified_down)
#
#  資料源：FinMind TaiwanStockPrice（箱型/爆量）
#         TaiwanStockTradingDailyReport（broker，當日彙總 + top3）
# ============================================================

import logging
import pandas as pd

from engine.broker_analysis import (
    aggregate_broker_by_trader,
    compute_top3_brokers,
)

logger = logging.getLogger(__name__)

DEFAULT_BOX_WINDOW         = 5
DEFAULT_AMPLITUDE_MAX      = 0.07    # 振幅 < 7% 才算箱型
DEFAULT_BREAKOUT_BUFFER    = 0.03    # 上緣 × 1.03 / 下緣 × 0.97
DEFAULT_VOLUME_SURGE_MULT  = 5       # 當日量 > 5 日均量 × 5


def detect_breakout(price_df: pd.DataFrame,
                     box_window:        int   = DEFAULT_BOX_WINDOW,
                     amplitude_max:     float = DEFAULT_AMPLITUDE_MAX,
                     breakout_buffer:   float = DEFAULT_BREAKOUT_BUFFER,
                     volume_surge_mult: float = DEFAULT_VOLUME_SURGE_MULT
                     ) -> dict:
    """
    偵測該股當日是否「箱型 + 突破 + 爆量」。

    回傳：
    {
        "is_box":         bool,
        "box_high":       float,
        "box_low":        float,
        "box_amplitude":  float,
        "breakout_up":    bool,
        "breakout_down":  bool,
        "volume_surge":   bool,
        "qualified_up":   bool,   # 向上突破 + 爆量同時成立
        "qualified_down": bool,   # 向下突破 + 爆量同時成立
        "vol_ratio":      float,  # 當日量 / 5 日均量
        "today_close":    float,
    }
    """
    empty = {
        "is_box":         False,
        "box_high":       0.0,
        "box_low":        0.0,
        "box_amplitude":  0.0,
        "breakout_up":    False,
        "breakout_down":  False,
        "volume_surge":   False,
        "qualified_up":   False,
        "qualified_down": False,
        "vol_ratio":      0.0,
        "today_close":    0.0,
    }
    if price_df is None or price_df.empty:
        return empty

    df = price_df.copy()
    df["date"]   = pd.to_datetime(df["date"])
    df["high"]   = pd.to_numeric(df.get("max",   df.get("high", 0)),  errors="coerce")
    df["low"]    = pd.to_numeric(df.get("min",   df.get("low",  0)),  errors="coerce")
    df["close"]  = pd.to_numeric(df.get("close", 0),                   errors="coerce")
    df["volume"] = pd.to_numeric(df.get("Trading_Volume",
                                          df.get("volume", 0)),       errors="coerce")
    df = df.dropna(subset=["high", "low", "close", "volume"]) \
            .sort_values("date").reset_index(drop=True)

    if len(df) < box_window + 1:
        return empty

    # 過去 box_window 個交易日（不含今日）
    window_df  = df.iloc[-(box_window + 1):-1]
    today_row  = df.iloc[-1]

    today_close = float(today_row["close"])
    today_vol   = float(today_row["volume"])
    box_high    = float(window_df["high"].max())
    box_low     = float(window_df["low"].min())

    if box_low <= 0:
        return {**empty, "today_close": round(today_close, 2)}

    box_amp = (box_high - box_low) / box_low
    is_box  = box_amp < amplitude_max

    # 爆量
    avg_vol      = float(window_df["volume"].mean())
    vol_ratio    = (today_vol / avg_vol) if avg_vol > 0 else 0.0
    volume_surge = (avg_vol > 0) and (today_vol > avg_vol * volume_surge_mult)

    # 突破
    breakout_up   = is_box and today_close > box_high * (1 + breakout_buffer)
    breakout_down = is_box and today_close < box_low  * (1 - breakout_buffer)

    return {
        "is_box":         is_box,
        "box_high":       round(box_high, 2),
        "box_low":        round(box_low,  2),
        "box_amplitude":  round(box_amp,  4),
        "breakout_up":    breakout_up,
        "breakout_down":  breakout_down,
        "volume_surge":   volume_surge,
        "qualified_up":   bool(breakout_up   and volume_surge),
        "qualified_down": bool(breakout_down and volume_surge),
        "vol_ratio":      round(vol_ratio, 2),
        "today_close":    round(today_close, 2),
    }


def compute_mainforce_today(broker_df: pd.DataFrame,
                              price_df: pd.DataFrame = pd.DataFrame()
                              ) -> dict:
    """
    對該股當日所有分點計算彙總 + 雙向 top3。

    broker_df：該股當日（單日）所有分點原始資料
    price_df ：該股股價（用於當日 vwap 算金額）

    回傳：
    {
        "buy_lots":      int,
        "sell_lots":     int,
        "net_lots":      int,
        "buy_amount_m":  float,
        "sell_amount_m": float,
        "net_amount_m":  float,
        "top3_buy":  [{"name", "lots", "amount_m"}, ...],
        "top3_sell": [...],
    }
    """
    empty = {
        "buy_lots": 0, "sell_lots": 0, "net_lots": 0,
        "buy_amount_m": 0.0, "sell_amount_m": 0.0, "net_amount_m": 0.0,
        "top3_buy": [], "top3_sell": [],
    }
    if broker_df is None or broker_df.empty:
        return empty

    agg = aggregate_broker_by_trader(broker_df)
    if agg.empty:
        return empty

    total_buy_lots  = int(agg["buy_lots"].sum())
    total_sell_lots = int(agg["sell_lots"].sum())
    net_lots = total_buy_lots - total_sell_lots

    # 當日 vwap：取 broker 涵蓋日期的 sum money / sum volume
    vwap = 0.0
    if not price_df.empty:
        pr = price_df.copy()
        pr["date"] = pd.to_datetime(pr["date"])
        broker_dates = pd.to_datetime(agg["date"].unique())
        same_day = pr[pr["date"].isin(broker_dates)]
        if not same_day.empty:
            money = float(pd.to_numeric(same_day.get("Trading_money",  0),
                                            errors="coerce").sum())
            vol   = float(pd.to_numeric(same_day.get("Trading_Volume", 0),
                                            errors="coerce").sum())
            if vol > 0:
                vwap = money / vol

    buy_amt_m  = round(total_buy_lots  * 1000 * vwap / 1_000_000, 2)
    sell_amt_m = round(total_sell_lots * 1000 * vwap / 1_000_000, 2)
    net_amt_m  = round(buy_amt_m - sell_amt_m, 2)

    top3_buy  = compute_top3_brokers(broker_df, price_df, "buy")
    top3_sell = compute_top3_brokers(broker_df, price_df, "sell")

    return {
        "buy_lots":      total_buy_lots,
        "sell_lots":     total_sell_lots,
        "net_lots":      net_lots,
        "buy_amount_m":  buy_amt_m,
        "sell_amount_m": sell_amt_m,
        "net_amount_m":  net_amt_m,
        "top3_buy":      top3_buy,
        "top3_sell":     top3_sell,
    }
