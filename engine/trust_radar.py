# engine/trust_radar.py
# ============================================================
#  投信雷達：當日投信買賣超金額排行 + 連買天數 + 箱型突破
#
#  資料來源（皆來自現有 FinMind cache，不另外抓 TWSE）：
#    TaiwanStockInstitutionalInvestorsBuySell  → 投信當日買賣超股數
#    TaiwanStockPrice                          → 當日均價、收盤、箱型判斷
#
#  指標定義：
#    當日金額 = 買賣超股數 × 當日加權均價
#               （均價 = Trading_money / Trading_Volume）
#    連買天數 = 從最新一日往回連續 diff > 0 的天數
#    箱型整理 = 過去 N 個交易日（不含今日）振幅 < X%
#    箱型突破 = 處於箱型 + 今日收盤 > 過去 N 日最高 × (1 + buffer)
#
#  預設：N=5、振幅<5%、buffer=1%（可由呼叫端覆寫）
# ============================================================

import logging
import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_BOX_WINDOW        = 5      # 觀察 N 個交易日
DEFAULT_BOX_AMPLITUDE_MAX = 0.05   # 振幅 < 5% 才視為箱型
DEFAULT_BREAKOUT_BUFFER   = 0.01   # 收盤 > 上緣 × 1.01 才算突破


def compute_trust_radar(institutional_df: pd.DataFrame,
                         price_df: pd.DataFrame,
                         box_window:        int   = DEFAULT_BOX_WINDOW,
                         box_amplitude_max: float = DEFAULT_BOX_AMPLITUDE_MAX,
                         breakout_buffer:   float = DEFAULT_BREAKOUT_BUFFER
                         ) -> dict:
    """
    對單支股票計算投信雷達指標。
    institutional_df：個股法人歷史（會自動篩出投信列）
    price_df        ：個股日 K 歷史，至少需 box_window+1 天才能評箱型

    回傳 dict（永遠回傳完整 schema，缺資料時對應欄位為 0/False）。
    """
    empty = {
        "trust_net_lots":    0,      # 當日投信買賣超張數
        "trust_amount_m":    0.0,    # 當日投信買賣超金額（百萬元）
        "trust_vwap":        0.0,    # 當日加權均價
        "trust_consec_days": 0,      # 投信連買天數
        "box_breakout":      False,
        "box_high":          0.0,
        "box_low":           0.0,
        "box_amplitude":     0.0,
        "is_box":            False,  # 過去 N 日是否處於箱型整理
    }

    if institutional_df.empty or "name" not in institutional_df.columns:
        return empty

    inst = institutional_df.copy()
    inst["diff"] = pd.to_numeric(inst.get("diff", 0), errors="coerce").fillna(0)
    trust = inst[inst["name"].str.contains("投信|Trust|trust",
                                              na=False, regex=True)].copy()
    if trust.empty:
        return empty

    trust["date"] = pd.to_datetime(trust["date"])
    trust = trust.sort_values("date").reset_index(drop=True)

    # ── 當日投信買賣超
    latest      = trust.iloc[-1]
    net_shares  = float(latest["diff"])
    net_lots    = int(round(net_shares / 1000))

    # ── 連買天數（從最新一日往回）
    consec = 0
    for v in trust["diff"].iloc[::-1]:
        if float(v) > 0:
            consec += 1
        else:
            break

    # ── 沒有股價資料就到此為止（amount 與 box 都算不出來）
    if price_df.empty:
        return {**empty,
                "trust_net_lots":    net_lots,
                "trust_consec_days": consec}

    pr = price_df.copy()
    pr["date"] = pd.to_datetime(pr["date"])
    pr = pr.sort_values("date").reset_index(drop=True)

    # 取與投信日期同日的股價列；對不上時 fallback 到最後一筆
    same_day = pr[pr["date"] == latest["date"]]
    today_row = same_day.iloc[-1] if not same_day.empty else pr.iloc[-1]

    money = float(pd.to_numeric(today_row.get("Trading_money",  0), errors="coerce") or 0)
    vol   = float(pd.to_numeric(today_row.get("Trading_Volume", 0), errors="coerce") or 0)
    vwap  = (money / vol) if vol > 0 else 0.0
    amount_yuan = net_shares * vwap                       # 元
    amount_m    = round(amount_yuan / 1_000_000, 2)       # 百萬元

    # ── 箱型整理 / 突破
    pr["high"]  = pd.to_numeric(pr.get("max",   pr.get("high", 0)),  errors="coerce")
    pr["low"]   = pd.to_numeric(pr.get("min",   pr.get("low",  0)),  errors="coerce")
    pr["close"] = pd.to_numeric(pr.get("close", 0),                   errors="coerce")
    pr = pr.dropna(subset=["high", "low", "close"])

    box_high = box_low = box_amp = 0.0
    is_box   = False
    breakout = False

    if len(pr) >= box_window + 1:
        # 過去 box_window 個交易日（不含今日）
        window_df   = pr.iloc[-(box_window + 1):-1]
        today_close = float(pr.iloc[-1]["close"])
        box_high    = float(window_df["high"].max())
        box_low     = float(window_df["low"].min())
        box_amp     = (box_high - box_low) / box_low if box_low > 0 else 0.0
        is_box      = box_amp < box_amplitude_max
        breakout    = is_box and today_close > box_high * (1 + breakout_buffer)

    return {
        "trust_net_lots":    net_lots,
        "trust_amount_m":    amount_m,
        "trust_vwap":        round(vwap, 2),
        "trust_consec_days": consec,
        "box_breakout":      breakout,
        "box_high":          round(box_high, 2),
        "box_low":           round(box_low,  2),
        "box_amplitude":     round(box_amp,  4),
        "is_box":            is_box,
    }
