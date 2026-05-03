# engine/filters.py
# ============================================================
#  過濾模組：黑名單與品質門檻
#  在評分之前先過濾掉不符條件的股票，節省 API 呼叫次數
# ============================================================

import logging
import pandas as pd

from config.settings import (
    MIN_MARKET_CAP_BILLION,
    MAX_MARGIN_RATIO,
    EXCLUDE_ETFS,
    EXCLUDE_WARRANTS,
)

logger = logging.getLogger(__name__)

# 固定排除清單（可依需求擴充）
BLACKLIST_KEYWORDS = [
    "存託憑證", "受益憑證", "債", "REITs", "不動產"
]

# ETF 代碼前綴
ETF_PREFIXES = ("0050", "0051", "0052", "0053", "0054", "0055",
                "0056", "006", "00")


def is_valid_stock(row: pd.Series) -> bool:
    """
    判斷股票是否通過基本過濾條件。
    row：TaiwanStockInfo 的一筆資料
    """
    stock_id = str(row.get("stock_id", ""))
    name = str(row.get("stock_name", ""))
    type_ = str(row.get("type", ""))

    # 排除 ETF
    if EXCLUDE_ETFS:
        if stock_id.startswith(ETF_PREFIXES):
            return False
        if "ETF" in name.upper() or type_ in ("ETF", "etf"):
            return False

    # 排除權證
    if EXCLUDE_WARRANTS:
        if len(stock_id) > 4:   # 權證代碼通常 6 位
            return False

    # 排除特定關鍵字
    for kw in BLACKLIST_KEYWORDS:
        if kw in name:
            return False

    # 只保留 4 位數字代碼
    if not stock_id.isdigit() or len(stock_id) != 4:
        return False

    return True


def filter_stock_list(stock_list_df: pd.DataFrame) -> pd.DataFrame:
    """
    對全市場股票清單做基本過濾，回傳有效股票 DataFrame。
    """
    if stock_list_df.empty:
        return stock_list_df

    before = len(stock_list_df)
    mask = stock_list_df.apply(is_valid_stock, axis=1)
    result = stock_list_df[mask].reset_index(drop=True)
    after = len(result)
    logger.info(f"股票過濾：{before} → {after} 支（移除 {before - after} 支）")
    return result


def filter_by_margin(stock_id: str, margin_df: pd.DataFrame) -> tuple[bool, float]:
    """
    融資使用率過濾。
    回傳：(是否通過, 融資使用率)
    融資使用率 = 融資餘額 / 融資限額
    若資料不足則直接通過（保守策略：不因缺資料而排除）
    """
    if margin_df.empty:
        return True, -1.0

    margin = margin_df[margin_df["stock_id"] == stock_id].copy()
    if margin.empty:
        return True, -1.0

    margin = margin.sort_values("date")
    latest = margin.iloc[-1]

    try:
        balance = float(latest.get("MarginPurchaseTodayBalance", 0) or 0)
        limit = float(latest.get("MarginPurchaseLimit", 0) or 0)
        ratio = balance / limit if limit > 0 else 0.0
    except (ValueError, TypeError, ZeroDivisionError):
        return True, -1.0

    passed = ratio <= MAX_MARGIN_RATIO
    return passed, round(ratio, 4)


def filter_by_market_cap(stock_id: str, price: float,
                          total_shares: float) -> tuple[bool, float]:
    """
    市值過濾。
    total_shares：流通股數（張），price：收盤價（元）
    市值（億）= 收盤 × 流通股數（張）× 1000 / 1億
    """
    if price <= 0 or total_shares <= 0:
        return True, 0.0  # 缺資料則通過

    market_cap_billion = (price * total_shares * 1000) / 1e8
    passed = market_cap_billion >= MIN_MARKET_CAP_BILLION
    return passed, round(market_cap_billion, 1)


def quick_institutional_check(institutional_df: pd.DataFrame,
                               min_days: int = 1) -> bool:
    """
    快速籌碼預篩：最近 N 天內是否有任何法人買超。
    若完全沒有法人關注，跳過詳細評分節省時間。
    """
    if institutional_df.empty:
        return False

    recent = institutional_df.tail(min_days * 3)  # 3種法人 × N天
    diff = pd.to_numeric(recent.get("diff", 0), errors="coerce").fillna(0)
    return bool(diff.sum() > 0)
