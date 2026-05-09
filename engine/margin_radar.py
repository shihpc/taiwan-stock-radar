# engine/margin_radar.py
# ============================================================
#  融資券雷達：個股融資 / 融券餘額金額多視窗 + 增減
#
#  資料源：FinMind TaiwanStockMarginPurchaseShortSale（每日餘額）
#         TaiwanStockPrice（每日收盤，用於餘額金額換算）
#
#  指標定義：
#    餘額金額（百萬元） = 餘額張數 × 當日收盤價 / 1000
#    diff (X 日)        = 最近一日餘額金額 - X 日前餘額金額
#                         （正值＝累計增加，負值＝累計減少）
#
#  注意：融資 / 融券是「餘額」(stock)，不是「流量」(flow)。
#       每個視窗的「前 X 日」指該交易日當天的快照值。
# ============================================================

import logging
import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_MARGIN_WINDOWS = [1, 3, 5, 10, 20]   # 融資
DEFAULT_SHORT_WINDOWS  = [1, 3, 5, 10, 21]   # 融券（依使用者要求 21 日）


def _empty_radar(windows: list) -> dict:
    return {
        "latest_lots":  0,
        "latest_amt_m": 0.0,
        "latest_close": 0.0,
        "windows": {str(w): {"ago_lots":  0,  "ago_amt_m":  0.0,
                              "diff_lots": 0,  "diff_amt_m": 0.0}
                     for w in windows},
    }


def _compute_balance_radar(margin_df: pd.DataFrame,
                            price_df:  pd.DataFrame,
                            balance_col: str,
                            windows: list) -> dict:
    """
    通用核心：對 balance_col（MarginPurchaseTodayBalance / ShortSaleTodayBalance）
    算多視窗餘額金額 + 增減。
    """
    result = _empty_radar(windows)
    if margin_df is None or margin_df.empty:
        return result
    if balance_col not in margin_df.columns:
        return result

    df = margin_df.copy()
    df["date"]        = pd.to_datetime(df["date"])
    df[balance_col]   = pd.to_numeric(df[balance_col], errors="coerce").fillna(0)
    df = df.sort_values("date").reset_index(drop=True)
    if df.empty:
        return result

    # 對齊每日收盤價
    if not price_df.empty:
        pr = price_df.copy()
        pr["date"]  = pd.to_datetime(pr["date"])
        pr["close"] = pd.to_numeric(pr.get("close", 0), errors="coerce").fillna(0)
        pr = pr[["date", "close"]]
        merged = df.merge(pr, on="date", how="left")
    else:
        merged = df.copy()
        merged["close"] = 0.0

    merged["close"] = pd.to_numeric(merged["close"], errors="coerce").fillna(0)
    # 餘額金額（百萬元）= 張數 × 收盤 / 1000
    merged["amt_m"] = merged[balance_col] * merged["close"] / 1000

    n = len(merged)
    latest        = merged.iloc[-1]
    latest_lots   = int(latest[balance_col])
    latest_close  = float(latest["close"])
    latest_amt_m  = round(float(latest["amt_m"]), 2)

    result["latest_lots"]  = latest_lots
    result["latest_amt_m"] = latest_amt_m
    result["latest_close"] = round(latest_close, 2)

    for w in windows:
        # 「前 w 日」= 最近一日往回第 w 個交易日
        # 資料不足時退回最早一筆（diff 會接近 0 或不準，使用者自行判斷）
        idx        = max(0, n - 1 - w)
        ago        = merged.iloc[idx]
        ago_lots   = int(ago[balance_col])
        ago_amt_m  = round(float(ago["amt_m"]), 2)
        result["windows"][str(w)] = {
            "ago_lots":   ago_lots,
            "ago_amt_m":  ago_amt_m,
            "diff_lots":  latest_lots - ago_lots,
            "diff_amt_m": round(latest_amt_m - ago_amt_m, 2),
        }

    return result


def compute_margin_radar(margin_history_df: pd.DataFrame,
                          price_df: pd.DataFrame,
                          windows: list = DEFAULT_MARGIN_WINDOWS) -> dict:
    """融資餘額多視窗（張數 × 收盤金額 + 5 視窗增減）"""
    return _compute_balance_radar(
        margin_history_df, price_df,
        "MarginPurchaseTodayBalance", windows,
    )


def compute_short_radar(margin_history_df: pd.DataFrame,
                         price_df: pd.DataFrame,
                         windows: list = DEFAULT_SHORT_WINDOWS) -> dict:
    """融券餘額多視窗（張數 × 收盤金額 + 5 視窗增減）"""
    return _compute_balance_radar(
        margin_history_df, price_df,
        "ShortSaleTodayBalance", windows,
    )
