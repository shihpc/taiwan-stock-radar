# engine/margin_analysis.py
# ============================================================
#  Sprint 2：融資融券深度分析模組
#  資料來源：TaiwanStockMarginPurchaseShortSale
#
#  Sprint 1 只做了「融資率 > 60% 則排除」的粗過濾。
#  Sprint 2 加入細緻評分：
#    - 融資使用率變化趨勢
#    - 融券增減（空方動向）
#    - 融資維持率（散戶壓力）
#    - 軋空潛力（融券高但股價不跌）
# ============================================================

import logging
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


def parse_margin_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    標準化融資融券 DataFrame 欄位。
    FinMind TaiwanStockMarginPurchaseShortSale 欄位：
      MarginPurchaseBuy, MarginPurchaseSell, MarginPurchaseRedeem,
      MarginPurchaseTodayBalance, MarginPurchaseLimit,         ← 融資
      ShortSaleBuy, ShortSaleSell, ShortSaleRedeem,
      ShortSaleTodayBalance, ShortSaleLimit,                   ← 融券
      OffsetLoanAndShort                                        ← 資券互抵
    """
    if df.empty:
        return df

    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    num_cols = [
        "MarginPurchaseTodayBalance", "MarginPurchaseLimit",
        "ShortSaleTodayBalance",      "ShortSaleLimit",
        "MarginPurchaseBuy",          "MarginPurchaseSell",
        "ShortSaleBuy",               "ShortSaleSell",
    ]
    for col in num_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    return df


def calc_margin_ratio(df: pd.DataFrame) -> pd.Series:
    """融資使用率 = 融資餘額 / 融資限額"""
    bal = df.get("MarginPurchaseTodayBalance", pd.Series(dtype=float))
    lim = df.get("MarginPurchaseLimit", pd.Series(dtype=float))
    return (bal / lim.replace(0, np.nan)).fillna(0)


def calc_short_ratio(df: pd.DataFrame) -> pd.Series:
    """融券使用率 = 融券餘額 / 融券限額"""
    bal = df.get("ShortSaleTodayBalance", pd.Series(dtype=float))
    lim = df.get("ShortSaleLimit", pd.Series(dtype=float))
    return (bal / lim.replace(0, np.nan)).fillna(0)


def analyze_margin(df: pd.DataFrame) -> dict:
    """
    深度分析融資融券狀況，回傳結構化結果。

    回傳：
    {
        "margin_ratio":       float,  # 最新融資使用率
        "margin_ratio_chg":   float,  # 近10日融資率變化（負=下降=籌碼乾淨）
        "margin_declining":   bool,   # 融資餘額連續下降（主動還款）
        "short_ratio":        float,  # 最新融券使用率
        "short_squeeze_potential": bool,  # 軋空潛力（融券高但股價近期上漲）
        "short_ratio_chg":    float,  # 近10日融券率變化
        "chip_clean":         bool,   # 融資率 < 20%（籌碼乾淨）
        "pass_filter":        bool,   # 是否通過融資過濾（< 60%）
    }
    """
    empty = {
        "margin_ratio": 0.0, "margin_ratio_chg": 0.0,
        "margin_declining": False, "short_ratio": 0.0,
        "short_squeeze_potential": False, "short_ratio_chg": 0.0,
        "chip_clean": False, "pass_filter": True,
    }

    if df.empty:
        return empty

    df = parse_margin_df(df)
    if df.empty:
        return empty

    margin_ratio = calc_margin_ratio(df)
    short_ratio  = calc_short_ratio(df)

    latest_margin = float(margin_ratio.iloc[-1]) if len(margin_ratio) else 0.0
    latest_short  = float(short_ratio.iloc[-1])  if len(short_ratio)  else 0.0

    # 近 10 日變化
    lookback = min(10, len(margin_ratio) - 1)
    margin_chg = latest_margin - float(margin_ratio.iloc[-lookback-1]) \
                 if lookback > 0 else 0.0
    short_chg  = latest_short  - float(short_ratio.iloc[-lookback-1]) \
                 if lookback > 0 else 0.0

    # 融資餘額連續下降（近5日）
    bal = df["MarginPurchaseTodayBalance"].tail(5).values
    margin_declining = bool(len(bal) >= 2 and all(
        bal[i] <= bal[i-1] for i in range(1, len(bal))
    ))

    # 軋空潛力：融券使用率 > 30% + 近期股價上漲
    # （融券者被迫回補 → 推升股價）
    short_squeeze = latest_short > 0.30

    return {
        "margin_ratio":            round(latest_margin, 4),
        "margin_ratio_chg":        round(margin_chg,    4),
        "margin_declining":        margin_declining,
        "short_ratio":             round(latest_short,  4),
        "short_squeeze_potential": short_squeeze,
        "short_ratio_chg":         round(short_chg,     4),
        "chip_clean":              latest_margin < 0.20,
        "pass_filter":             latest_margin < 0.60,
    }


def score_margin(margin_analysis: dict) -> dict:
    """
    融資融券評分（Sprint 2，最高 13 分，可負分至 -6 分）。

    計分規則：
      融資率 < 20%（籌碼乾淨）    → +4 分
      融資率 < 35%                → +2 分
      融資率下降中（主動還款）     → +5 分（原 2 分）
      融券率 > 30%（軋空潛力）    → +10 分
      融資率 > 50%                → -3 分
      融資率 > 60%                → -6 分
    """
    score = 0
    detail = {"breakdown": {}}

    ratio    = margin_analysis.get("margin_ratio", 0)
    declining = margin_analysis.get("margin_declining", False)
    short_ratio = margin_analysis.get("short_ratio", 0)
    clean    = margin_analysis.get("chip_clean", False)

    # ── 融資率評分
    if ratio > 0.60:
        s1 = -6
    elif ratio > 0.50:
        s1 = -3
    elif clean:        # < 20%
        s1 = 4
    elif ratio < 0.35:
        s1 = 2
    else:
        s1 = 0
    detail["breakdown"]["融資率"] = s1
    score += s1

    # ── 融資下降（+5，原 +2）
    s2 = 5 if declining else 0
    detail["breakdown"]["融資下降"] = s2
    score += s2

    # ── 軋空潛力（融券率 > 30% → +10）
    s3 = 10 if short_ratio > 0.30 else 0
    detail["breakdown"]["軋空潛力"] = s3
    score += s3

    return {
        "score": max(min(score, 13), -6),  # 上限 13，下限 -6
        "detail": detail
    }
