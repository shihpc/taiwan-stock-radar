# engine/foreign_radar.py
# ============================================================
#  法人多視窗雷達：1/3/5/10/20 日累計買賣超張數與金額
#
#  雖檔名為 foreign_radar，實際上提供兩個並列函式：
#    compute_foreign_radar(...)  → 外資（排除外資自營商）
#    compute_trust_io(...)       → 投信
#  兩者共用 _compute_io_windows() 私有 helper。
#
#  資料源（皆 FinMind 既有 cache）：
#    TaiwanStockInstitutionalInvestorsBuySell  → 各法人每日 buy/sell 股數
#    TaiwanStockPrice                          → 每日加權均價（money/volume）
#
#  金額計算：對每個交易日 buy/sell 股數 × 當日 vwap 後依視窗累加，
#           精確反映時段內的真實成交金額（同股票不同日 vwap 不同）。
# ============================================================

import logging
import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_WINDOWS = [1, 3, 5, 10, 20]


def _empty_windows(windows: list) -> dict:
    return {str(w): {"buy_lots":     0, "sell_lots":     0, "net_lots":     0,
                     "buy_amount_m": 0.0, "sell_amount_m": 0.0, "net_amount_m": 0.0}
            for w in windows}


def _compute_io_windows(io_df: pd.DataFrame,
                        price_df: pd.DataFrame,
                        windows: list) -> dict:
    """
    對「已篩過特定法人類別」的 io_df 算多視窗累計買賣超。
    io_df 需含欄位 date / buy / sell。
    """
    result = _empty_windows(windows)
    if io_df.empty or price_df.empty:
        return result

    df = io_df.copy()
    df["buy"]  = pd.to_numeric(df.get("buy",  0), errors="coerce").fillna(0)
    df["sell"] = pd.to_numeric(df.get("sell", 0), errors="coerce").fillna(0)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    # 每日 vwap
    pr = price_df.copy()
    pr["date"]  = pd.to_datetime(pr["date"])
    pr["money"] = pd.to_numeric(pr.get("Trading_money",  0), errors="coerce").fillna(0)
    pr["vol"]   = pd.to_numeric(pr.get("Trading_Volume", 0), errors="coerce").fillna(0)
    pr["vwap"]  = (pr["money"] / pr["vol"].replace(0, pd.NA)).fillna(0)
    pr = pr[["date", "vwap"]]

    merged = df.merge(pr, on="date", how="left")
    merged["vwap"]     = merged["vwap"].fillna(0)
    merged["buy_amt"]  = merged["buy"]  * merged["vwap"]   # 元
    merged["sell_amt"] = merged["sell"] * merged["vwap"]

    n = len(merged)
    for w in windows:
        actual = min(w, n)
        if actual <= 0:
            continue
        recent = merged.tail(actual)
        buy_lots  = int(round(float(recent["buy"].sum())  / 1000))
        sell_lots = int(round(float(recent["sell"].sum()) / 1000))
        buy_amt_m  = round(float(recent["buy_amt"].sum())  / 1_000_000, 2)
        sell_amt_m = round(float(recent["sell_amt"].sum()) / 1_000_000, 2)
        result[str(w)] = {
            "buy_lots":      buy_lots,
            "sell_lots":     sell_lots,
            "net_lots":      buy_lots - sell_lots,
            "buy_amount_m":  buy_amt_m,
            "sell_amount_m": sell_amt_m,
            "net_amount_m":  round(buy_amt_m - sell_amt_m, 2),
        }
    return result


def compute_foreign_radar(institutional_df: pd.DataFrame,
                           price_df: pd.DataFrame,
                           windows: list = DEFAULT_WINDOWS) -> dict:
    """外資多視窗（排除外資自營商，僅取主力外資）"""
    if institutional_df.empty or "name" not in institutional_df.columns:
        return _empty_windows(windows)
    foreign = institutional_df[
        institutional_df["name"].str.contains("外資及陸資|Foreign_Investor",
                                                 na=False, regex=True)
        & ~institutional_df["name"].str.contains("自營|Dealer",
                                                    na=False, regex=True)
    ]
    return _compute_io_windows(foreign, price_df, windows)


def compute_trust_io(institutional_df: pd.DataFrame,
                      price_df: pd.DataFrame,
                      windows: list = DEFAULT_WINDOWS) -> dict:
    """投信多視窗"""
    if institutional_df.empty or "name" not in institutional_df.columns:
        return _empty_windows(windows)
    trust = institutional_df[
        institutional_df["name"].str.contains("投信|Trust|trust",
                                                  na=False, regex=True)
    ]
    return _compute_io_windows(trust, price_df, windows)
