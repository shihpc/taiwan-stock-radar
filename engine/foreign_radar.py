# engine/foreign_radar.py
# ============================================================
#  外資雷達：1/3/5/10/20 日累計買賣超張數與金額
#
#  資料源（皆 FinMind 既有 cache）：
#    TaiwanStockInstitutionalInvestorsBuySell  → 外資每日 buy/sell 股數
#    TaiwanStockPrice                          → 每日加權均價（money/volume）
#
#  金額計算：對每個交易日 buy/sell 股數 × 當日 vwap 後依視窗累加，
#           精確反映時段內的真實成交金額（同一支股票不同日 vwap 不同）。
#
#  「外資」定義：排除外資自營商，僅取主力外資（Foreign_Investor / 外資及陸資）
# ============================================================

import logging
import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_WINDOWS = [1, 3, 5, 10, 20]


def compute_foreign_radar(institutional_df: pd.DataFrame,
                           price_df: pd.DataFrame,
                           windows: list = DEFAULT_WINDOWS) -> dict:
    """
    對單支股票算多個時間視窗的外資累計買賣超。

    回傳 dict：
      { "1":  {"buy_lots":int, "sell_lots":int, "net_lots":int,
               "buy_amount_m":float, "sell_amount_m":float, "net_amount_m":float},
        "3":  {...},
        "5":  {...},
        "10": {...},
        "20": {...} }

    金額單位：百萬元；張數單位：張（FinMind buy/sell 為股，÷1000）。
    資料不足某視窗時，該視窗用實際可用天數累計（不會回傳 None）。
    """
    empty_w = {
        "buy_lots": 0, "sell_lots": 0, "net_lots": 0,
        "buy_amount_m": 0.0, "sell_amount_m": 0.0, "net_amount_m": 0.0,
    }
    result = {str(w): dict(empty_w) for w in windows}

    if institutional_df.empty or "name" not in institutional_df.columns:
        return result

    inst = institutional_df.copy()
    inst["buy"]  = pd.to_numeric(inst.get("buy",  0), errors="coerce").fillna(0)
    inst["sell"] = pd.to_numeric(inst.get("sell", 0), errors="coerce").fillna(0)
    inst["date"] = pd.to_datetime(inst["date"])

    # 主力外資：排除外資自營商
    foreign = inst[
        inst["name"].str.contains("外資及陸資|Foreign_Investor",
                                     na=False, regex=True)
        & ~inst["name"].str.contains("自營|Dealer",
                                        na=False, regex=True)
    ].copy()
    foreign = foreign.sort_values("date").reset_index(drop=True)
    if foreign.empty or price_df.empty:
        return result

    # 每日 vwap
    pr = price_df.copy()
    pr["date"] = pd.to_datetime(pr["date"])
    pr["money"] = pd.to_numeric(pr.get("Trading_money",  0), errors="coerce").fillna(0)
    pr["vol"]   = pd.to_numeric(pr.get("Trading_Volume", 0), errors="coerce").fillna(0)
    pr["vwap"]  = (pr["money"] / pr["vol"].replace(0, pd.NA)).fillna(0)
    pr = pr[["date", "vwap"]]

    merged = foreign.merge(pr, on="date", how="left")
    merged["vwap"]      = merged["vwap"].fillna(0)
    merged["buy_amt"]   = merged["buy"]  * merged["vwap"]   # 元
    merged["sell_amt"]  = merged["sell"] * merged["vwap"]
    merged = merged.sort_values("date").reset_index(drop=True)

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
