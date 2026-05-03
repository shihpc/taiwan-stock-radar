# engine/shareholding.py
# ============================================================
#  Sprint 2：股權分散表分析模組
#  資料來源：TaiwanStockHoldingSharesPer（Backer/Sponsor）
#
#  核心邏輯：
#    大戶（400張以上）持股比例連續兩週上升 → 籌碼集中訊號
#    散戶（1-999張）持股比例同步下降 → 籌碼移轉確認
#    超大戶（1000張以上）佔比快速上升 → 主力建倉訊號
# ============================================================

import logging
import pandas as pd
import numpy as np
from typing import Optional

logger = logging.getLogger(__name__)

# ── 持股分級定義 ──────────────────────────────────────────────
# FinMind 的 HoldingSharesLevel 實際字串值
LEVEL_SMALL = [           # 散戶（< 400 張）
    "1-999",
    "1,000-5,000",
    "5,001-10,000",
    "10,001-15,000",
    "15,001-20,000",
    "20,001-30,000",
    "30,001-40,000",
    "40,001-50,000",
    "50,001-100,000",
    "100,001-200,000",
    "200,001-400,000",
]
LEVEL_BIG = [             # 大戶（400–1000 張）
    "400,001-600,000",
    "600,001-800,000",
    "800,001-1,000,000",
]
LEVEL_SUPER = [           # 超大戶（> 1000 張）
    "1,000,001以上",
    "1,000,001-above",    # 備用格式
]


def parse_holding_distribution(df: pd.DataFrame) -> pd.DataFrame:
    """
    將原始股權分散表整理成每週三個群組的持股比例：
      - small_pct：散戶比例合計
      - big_pct  ：大戶比例合計（400–1000張）
      - super_pct：超大戶比例合計（>1000張）
      - big_total_pct：大戶+超大戶合計

    回傳以 date 為索引，每週一筆的 DataFrame。
    """
    if df.empty:
        return pd.DataFrame()

    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df["percent"] = pd.to_numeric(df.get("percent", 0), errors="coerce").fillna(0)
    df["HoldingSharesLevel"] = df["HoldingSharesLevel"].astype(str).str.strip()

    result = []
    for date, group in df.groupby("date"):
        small = group[group["HoldingSharesLevel"].isin(LEVEL_SMALL)]["percent"].sum()
        big   = group[group["HoldingSharesLevel"].isin(LEVEL_BIG)]["percent"].sum()
        super_ = group[group["HoldingSharesLevel"].isin(LEVEL_SUPER)]["percent"].sum()

        # 若分級字串格式不符合預期，嘗試數值比較（FinMind 有時欄位格式會有差異）
        if small + big + super_ < 10:
            group = _try_numeric_parse(group)
            small = group.get("small_pct", 0)
            big   = group.get("big_pct", 0)
            super_ = group.get("super_pct", 0)

        result.append({
            "date":          date,
            "small_pct":     round(small,  4),
            "big_pct":       round(big,    4),
            "super_pct":     round(super_, 4),
            "big_total_pct": round(big + super_, 4),
        })

    if not result:
        return pd.DataFrame()

    out = pd.DataFrame(result).sort_values("date").reset_index(drop=True)
    return out


def _try_numeric_parse(group: pd.DataFrame) -> dict:
    """
    備用解析：嘗試用數值範圍判斷持股分級。
    當 HoldingSharesLevel 欄位格式不是預期字串時使用。
    """
    try:
        # 嘗試從 unit（股數）估算持股張數
        group = group.copy()
        group["unit"] = pd.to_numeric(group.get("unit", 0), errors="coerce").fillna(0)
        group["shares"] = group["unit"] / 1000  # 股 → 張

        small_mask  = group["shares"] < 400_000
        big_mask    = (group["shares"] >= 400_000) & (group["shares"] < 1_000_000)
        super_mask  = group["shares"] >= 1_000_000

        return {
            "small_pct": group.loc[small_mask, "percent"].sum(),
            "big_pct":   group.loc[big_mask,   "percent"].sum(),
            "super_pct": group.loc[super_mask,  "percent"].sum(),
        }
    except Exception:
        return {"small_pct": 0, "big_pct": 0, "super_pct": 0}


# ── 核心分析函式 ──────────────────────────────────────────────

def analyze_holder_trend(parsed_df: pd.DataFrame,
                          weeks: int = 4) -> dict:
    """
    分析大戶持股近 N 週趨勢。

    回傳：
    {
        "big_rising":        bool,   # 大戶持股連續上升
        "super_rising":      bool,   # 超大戶持股連續上升
        "small_declining":   bool,   # 散戶持股同步下降（籌碼移轉確認）
        "concentration":     bool,   # 大戶+超大戶比例上升 + 散戶下降（同時成立）
        "big_total_pct":     float,  # 最新大戶+超大戶佔比
        "big_chg_4w":        float,  # 近4週大戶比例變化
        "super_chg_4w":      float,  # 近4週超大戶比例變化
        "small_chg_4w":      float,  # 近4週散戶比例變化
        "weeks_available":   int,    # 實際可用週數
    }
    """
    empty = {
        "big_rising": False, "super_rising": False,
        "small_declining": False, "concentration": False,
        "big_total_pct": 0.0, "big_chg_4w": 0.0,
        "super_chg_4w": 0.0, "small_chg_4w": 0.0,
        "weeks_available": 0,
    }

    if parsed_df.empty or len(parsed_df) < 2:
        return empty

    recent = parsed_df.tail(weeks).reset_index(drop=True)
    n = len(recent)

    latest = recent.iloc[-1]
    oldest = recent.iloc[0]

    big_chg   = latest["big_total_pct"] - oldest["big_total_pct"]
    super_chg = latest["super_pct"]     - oldest["super_pct"]
    small_chg = latest["small_pct"]     - oldest["small_pct"]

    # 連續上升：每週都比前週高
    big_rising   = all(
        recent["big_total_pct"].iloc[i] >= recent["big_total_pct"].iloc[i-1]
        for i in range(1, n)
    )
    super_rising = all(
        recent["super_pct"].iloc[i] >= recent["super_pct"].iloc[i-1]
        for i in range(1, n)
    )
    small_declining = all(
        recent["small_pct"].iloc[i] <= recent["small_pct"].iloc[i-1]
        for i in range(1, n)
    )

    # 籌碼集中：大戶上升 AND 散戶下降
    concentration = (big_chg > 0.5) and (small_chg < -0.5)

    return {
        "big_rising":       big_rising,
        "super_rising":     super_rising,
        "small_declining":  small_declining,
        "concentration":    concentration,
        "big_total_pct":    round(float(latest["big_total_pct"]), 2),
        "big_chg_4w":       round(float(big_chg),   2),
        "super_chg_4w":     round(float(super_chg), 2),
        "small_chg_4w":     round(float(small_chg), 2),
        "weeks_available":  n,
    }


def score_shareholding(holding_df: pd.DataFrame) -> dict:
    """
    股權分散評分（Sprint 2，最高 25 分）。

    計分規則：
      大戶持股連續4週上升         → +10 分
      4週變化 > 0.5%              → +5 分（未連續但有上升）
      超大戶持股上升               → +5 分
      籌碼集中（大戶↑ + 散戶↓）   → +10 分
    """
    score = 0
    detail = {"breakdown": {}, "trend": {}}

    if holding_df.empty:
        return {"score": 0, "detail": detail}

    parsed = parse_holding_distribution(holding_df)
    if parsed.empty:
        return {"score": 0, "detail": detail}

    trend = analyze_holder_trend(parsed, weeks=4)
    detail["trend"] = trend

    # ── 大戶連續上升（+10）；未連續但4週變化 > 0.5%（+5）
    if trend["big_rising"]:
        s1 = 10
    elif trend["big_chg_4w"] > 0.5:
        s1 = 5
    else:
        s1 = 0
    detail["breakdown"]["大戶持股上升"] = s1
    score += s1

    # ── 超大戶上升（+5）
    s2 = 5 if trend["super_rising"] else (2 if trend["super_chg_4w"] > 0.2 else 0)
    detail["breakdown"]["超大戶持股上升"] = s2
    score += s2

    # ── 籌碼集中（大戶↑散戶↓，+10）
    s3 = 10 if trend["concentration"] else 0
    detail["breakdown"]["籌碼集中"] = s3
    score += s3

    return {"score": min(score, 25), "detail": detail}


def get_total_shares_from_holding(holding_df: pd.DataFrame) -> Optional[float]:
    """
    從股權分散表反推流通在外張數。
    公式：unit（股）/ percent * 100 / 1000 → 總張數
    取最近一週、最大持股分級的資料估算，準確度有限但可用。
    """
    if holding_df.empty:
        return None
    try:
        df = holding_df.copy()
        df["date"] = pd.to_datetime(df["date"])
        latest_date = df["date"].max()
        latest = df[df["date"] == latest_date].copy()
        latest["unit"] = pd.to_numeric(latest.get("unit", 0), errors="coerce").fillna(0)
        latest["percent"] = pd.to_numeric(latest.get("percent", 0), errors="coerce").fillna(0)

        # 取持股最大的那一筆來反推總股數
        best = latest[latest["unit"] > 0].copy()
        if best.empty:
            return None

        best["total_est"] = best["unit"] / (best["percent"] / 100)
        median_total = best["total_est"].median()
        return float(median_total / 1000)  # 股 → 張
    except Exception as e:
        logger.debug(f"無法估算流通張數：{e}")
        return None
