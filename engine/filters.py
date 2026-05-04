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

# 權證辨識關鍵字（名稱含任一 → 排除）
WARRANT_KEYWORDS = ["認購", "認售", "牛證", "熊證"]

# 固定排除清單
BLACKLIST_KEYWORDS = [
    "存託憑證", "受益憑證", "REITs", "不動產"
]

# FinMind TaiwanStockInfo type 欄位：只保留上市/上櫃
# 興櫃(rotc)、公開發行(pub/pubcd)、其他非交易所掛牌者一律排除
_ALLOWED_TYPES = {"twse", "tpex", "sii", "otc", "上市", "上櫃"}
_EXCLUDED_TYPES = {"rotc", "pub", "pubcd", "興櫃", "公開發行", "公開"}


def is_valid_stock(row: pd.Series) -> bool:
    """
    保留：上市/上櫃普通股、ETF、特別股（2002A）
    排除：權證、興櫃、公開發行
    """
    stock_id = str(row.get("stock_id", ""))
    name = str(row.get("stock_name", ""))

    # 排除權證（名稱關鍵字優先判斷）
    for kw in WARRANT_KEYWORDS:
        if kw in name:
            return False

    # 排除其他黑名單
    for kw in BLACKLIST_KEYWORDS:
        if kw in name:
            return False

    # type 欄位過濾：有明確非上市/上櫃 type 則排除
    stock_type = str(row.get("type", "")).strip().lower()
    if stock_type in {t.lower() for t in _EXCLUDED_TYPES}:
        return False

    # 4 位純數字 → 普通股 / 4 碼 ETF（0050, 0056 等）
    if stock_id.isdigit() and len(stock_id) == 4:
        return True

    # 4 位數字 + 1 大寫字母 → 特別股（2002A, 2880A）
    if len(stock_id) == 5 and stock_id[:4].isdigit() and stock_id[4].isupper():
        return True

    # 5~6 位且以 "00" 開頭 → ETF / 主動型基金（006205, 00878, 00981A 等）
    # 允許尾部一個大寫字母（如 00981A 凱基台灣優選高息30 主動式 ETF）
    if len(stock_id) in (5, 6) and stock_id.startswith("00"):
        body = stock_id[:-1] if stock_id[-1].isupper() else stock_id
        if body.isdigit():
            return True

    return False


def filter_stock_list(stock_list_df: pd.DataFrame) -> pd.DataFrame:
    """
    對全市場股票清單做基本過濾，回傳有效股票 DataFrame。
    """
    if stock_list_df.empty:
        return stock_list_df

    # 診斷日誌：顯示 type 分布，幫助確認過濾邏輯是否正確
    if "type" in stock_list_df.columns:
        type_dist = stock_list_df["type"].value_counts().to_dict()
        logger.info(f"股票清單 type 分布：{type_dist}")

    before = len(stock_list_df)
    mask = stock_list_df.apply(is_valid_stock, axis=1)
    result = (stock_list_df[mask]
              .drop_duplicates(subset=["stock_id"], keep="first")
              .sort_values("stock_id")
              .reset_index(drop=True))
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


def phase1_filter(today_inst: pd.DataFrame) -> bool:
    """
    第一階段篩選，使用 DailyDataCache 當日批次資料（零額外 API 呼叫）。

    通過條件：外資 net > 0 OR 投信 net > 0

    FinMind name 欄對應（批次 API 回傳英文）：
      Foreign_Investor   → 外資及陸資（主力外資）
      Foreign_Dealer_Self→ 外資自營商
      Investment_Trust   → 投信
      Dealer_self        → 自營商自行買賣
      Dealer_Hedging     → 自營商避險
    """
    if today_inst.empty:
        return False

    inst = today_inst.copy()
    inst["diff"] = pd.to_numeric(inst.get("diff", 0), errors="coerce").fillna(0)

    if "name" not in inst.columns:
        return bool(inst["diff"].sum() > 0)

    foreign_mask = inst["name"].str.contains("外資|Foreign|foreign", na=False, regex=True)
    trust_mask   = inst["name"].str.contains("投信|Trust|trust", na=False, regex=True)

    foreign_net = inst.loc[foreign_mask, "diff"].sum()
    trust_net   = inst.loc[trust_mask,   "diff"].sum()

    if not foreign_mask.any() and not trust_mask.any():
        # name 欄存在但完全沒有外資/投信匹配：降級為整體 diff > 0
        return bool(inst["diff"].sum() > 0)

    return bool((foreign_net > 0) or (trust_net > 0))
