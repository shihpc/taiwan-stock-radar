# data/fetcher.py
# ============================================================
#  FinMind API 資料拉取模組
#  負責所有與 FinMind 的通訊，統一處理錯誤與 Rate Limit
# ============================================================

import time
import pickle
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests
import pandas as pd

from config.settings import (
    FINMIND_TOKEN, FINMIND_BASE_URL, FINMIND_BROKER_URL,
    API_SLEEP_SECONDS, LOOKBACK_DAYS, REVENUE_LOOKBACK_MONTHS,
    CACHE_DIR,
)

logger = logging.getLogger(__name__)


# ── 磁碟快取 ─────────────────────────────────────────────────
# 各 dataset 的快取週期，超過週期邊界就自動失效
_CACHE_TTL_DAYS: dict[str, int] = {
    "TaiwanStockFinancialStatements":              90,  # 季更新
    "TaiwanStockMonthRevenue":                     35,  # 月更新
    "TaiwanStockShareholding":                      8,  # 週更新
    "TaiwanStockHoldingSharesPer":                  8,  # 週更新
    "TaiwanStockPrice":                             1,  # 日更新
    "TaiwanStockInstitutionalInvestorsBuySell":     1,  # 日更新
    "TaiwanStockMarginPurchaseShortSale":           1,  # 日更新
}

_CACHE_ROOT = Path(CACHE_DIR)


def _cache_period(dataset: str) -> str:
    """根據 TTL 回傳對應的期間字串，用作快取 key 的一部分。"""
    ttl = _CACHE_TTL_DAYS.get(dataset, 1)
    today = datetime.today()
    if ttl >= 60:                          # 季資料
        q = (today.month - 1) // 3
        return f"{today.year}Q{q}"
    elif ttl >= 28:                        # 月資料
        return today.strftime("%Y-%m")
    elif ttl >= 7:                         # 週資料
        week = today.isocalendar()[1]
        return f"{today.year}W{week:02d}"
    else:                                  # 日資料
        return today.strftime("%Y-%m-%d")


def _cache_path(dataset: str, stock_id: str) -> Path:
    _CACHE_ROOT.mkdir(exist_ok=True)
    period = _cache_period(dataset)
    return _CACHE_ROOT / f"{dataset}_{stock_id}_{period}.pkl"


def _load_cache(dataset: str, stock_id: str) -> Optional[pd.DataFrame]:
    path = _cache_path(dataset, stock_id)
    if not path.exists():
        return None
    try:
        with open(path, "rb") as f:
            df = pickle.load(f)
        logger.debug(f"[cache hit] {dataset} {stock_id}")
        return df
    except Exception:
        path.unlink(missing_ok=True)
        return None


def _save_cache(dataset: str, stock_id: str, df: pd.DataFrame) -> None:
    if df.empty:
        return  # 不快取空結果，避免遮蔽之後有資料的查詢
    path = _cache_path(dataset, stock_id)
    try:
        with open(path, "wb") as f:
            pickle.dump(df, f)
    except Exception:
        pass


# ── 全市場單日批次的 cache（過去日期永久有效）────────────────

def _all_cache_path(dataset: str, date: str) -> Path:
    return _CACHE_ROOT / f"{dataset}_all_{date.replace('-', '')}.pkl"


def _load_all_cache(dataset: str, date: str) -> Optional[pd.DataFrame]:
    """讀取單日全市場 cache。當日資料不從 cache 讀（避免拿到盤中不完整資料）"""
    today = datetime.today().strftime("%Y-%m-%d")
    if date >= today:
        return None
    path = _all_cache_path(dataset, date)
    if not path.exists():
        return None
    try:
        with open(path, "rb") as f:
            df = pickle.load(f)
        logger.debug(f"[cache hit] {dataset} all {date}")
        return df
    except Exception:
        path.unlink(missing_ok=True)
        return None


def _save_all_cache(dataset: str, date: str, df: pd.DataFrame) -> None:
    """
    寫入單日全市場 cache。當日資料不寫入。
    過去日期允許寫入空 DataFrame（標記休市日，避免下次重複打 API）。
    """
    today = datetime.today().strftime("%Y-%m-%d")
    if date >= today:
        return
    if df is None:           # None = 抓取出錯，下次重試
        return
    _CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    path = _all_cache_path(dataset, date)
    try:
        with open(path, "wb") as f:
            pickle.dump(df, f)
    except Exception:
        pass


# ── 基礎請求函式 ──────────────────────────────────────────────

def _get(dataset: str, params: dict, retry: int = 1,
         timeout: int = 25) -> pd.DataFrame:
    """
    統一的 FinMind GET 請求，含重試與錯誤處理。
    """
    payload = {
        "dataset": dataset,
        "token": FINMIND_TOKEN,
        **params
    }
    for attempt in range(retry):
        try:
            resp = requests.get(FINMIND_BASE_URL, params=payload, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()

            if data.get("status") != 200:
                msg = data.get("msg", "未知錯誤")
                logger.warning(f"[{dataset}] API 回傳非 200：{msg}")
                return pd.DataFrame()

            df = pd.DataFrame(data.get("data", []))
            time.sleep(API_SLEEP_SECONDS)
            return df

        except requests.exceptions.Timeout:
            logger.warning(f"[{dataset}] 請求逾時，第 {attempt+1} 次重試...")
            time.sleep(2 ** attempt)
        except requests.exceptions.HTTPError as e:
            # 4xx 是請求本身有問題，retry 無效，直接放棄
            logger.warning(f"[{dataset}] HTTP {e.response.status_code}，不重試：{e}")
            return pd.DataFrame()
        except requests.exceptions.RequestException as e:
            logger.warning(f"[{dataset}] 請求失敗（第 {attempt+1} 次）：{e}")
            time.sleep(2 ** attempt)

    logger.warning(f"[{dataset}] 已重試 {retry} 次，放棄")
    return pd.DataFrame()


def _date_range(days_back: int) -> tuple[str, str]:
    """回傳 (start_date, end_date) 字串，格式 YYYY-MM-DD"""
    end = datetime.today()
    start = end - timedelta(days=days_back)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def fetch_trading_dates(days_back: int = 60) -> set:
    """
    從 FinMind TaiwanStockTradingDate 取得交易日清單（Free 方案可用）。
    回傳 set of 'YYYY-MM-DD' 字串，方便快速查詢某日是否為交易日。
    days_back：往前取幾天的交易日（預設 60 天，涵蓋約 3 個月）
    """
    start, end = _date_range(days_back)
    df = _get("TaiwanStockTradingDate", {
        "start_date": start,
        "end_date":   end,
    })
    if df.empty or "date" not in df.columns:
        logger.warning("TaiwanStockTradingDate 無資料，將以週一到週五代替")
        return set()
    dates = set(df["date"].str[:10].tolist())
    logger.info(f"取得交易日清單：{len(dates)} 天（{start} ~ {end}）")
    return dates


def get_last_trading_date(trading_dates: set = None) -> str:
    """
    回傳最近一個交易日的日期字串（YYYY-MM-DD）。
    優先使用 trading_dates（來自 FinMind），若為空則用週一到週五推算。
    """
    from datetime import date as date_type
    today = datetime.today().date()

    if trading_dates:
        for delta in range(10):
            candidate = (today - timedelta(days=delta)).strftime("%Y-%m-%d")
            if candidate in trading_dates:
                return candidate
        logger.warning("交易日清單裡找不到最近交易日，改用週一到週五推算")

    # Fallback：週末往前推到週五
    weekday = today.weekday()
    if weekday == 5:    # 週六
        delta = 1
    elif weekday == 6:  # 週日
        delta = 2
    else:
        delta = 0
    return (today - timedelta(days=delta)).strftime("%Y-%m-%d")


def _month_range(months_back: int) -> tuple[str, str]:
    """回傳幾個月前到今天的日期範圍"""
    end = datetime.today()
    start = end - timedelta(days=months_back * 31)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


# ── 技術面資料 ────────────────────────────────────────────────

def fetch_stock_list() -> pd.DataFrame:
    """
    取得全市場股票清單（上市 + 上櫃）。
    欄位：stock_id, stock_name, type, industry_category, market
    """
    logger.info("取得股票清單...")
    df = _get("TaiwanStockInfo", {})
    if df.empty:
        return df
    logger.info(f"共 {len(df)} 支股票")
    return df


def fetch_stock_price(stock_id: str, days_back: int = LOOKBACK_DAYS) -> pd.DataFrame:
    """
    取得個股歷史日K資料。
    欄位：date, stock_id, Trading_Volume, Trading_money, open, max, min,
           close, spread, Trading_turnover
    """
    dataset = "TaiwanStockPrice"
    cached = _load_cache(dataset, stock_id)
    if cached is not None:
        return cached
    start, end = _date_range(days_back)
    df = _get(dataset, {"data_id": stock_id, "start_date": start, "end_date": end})
    _save_cache(dataset, stock_id, df)
    return df


def fetch_all_stock_price_by_date(date: str) -> pd.DataFrame:
    """
    取得全市場單日所有股票收盤資料（Backer/Sponsor 限定）。
    用於批次掃描，避免逐支呼叫。過去日期永久 cache。
    """
    cached = _load_all_cache("TaiwanStockPrice", date)
    if cached is not None:
        return cached
    logger.info(f"取得全市場收盤 {date}...")
    df = _get("TaiwanStockPrice", {"start_date": date, "end_date": date},
              retry=2, timeout=60)
    _save_all_cache("TaiwanStockPrice", date, df)
    return df


def fetch_all_stock_price_history(end_date: str, days_back: int = LOOKBACK_DAYS) -> pd.DataFrame:
    """逐日批次拉 N 個交易日的全市場股價"""
    dates = _recent_trading_days(end_date, days_back)
    logger.info(f"逐日拉股價歷史 {dates[0]}~{dates[-1]}（{len(dates)} 個交易日）...")
    dfs = []
    for d in dates:
        df_day = fetch_all_stock_price_by_date(d)
        if not df_day.empty:
            dfs.append(df_day)
    if not dfs:
        return pd.DataFrame()
    df = pd.concat(dfs, ignore_index=True)
    df["stock_id"] = df["stock_id"].astype(str).str.strip()
    logger.info(f"股價歷史合計：{len(df):,} 筆，{df['stock_id'].nunique()} 支，{len(dfs)} 個交易日")
    return df


def fetch_per_pbr(stock_id: str, days_back: int = 30) -> pd.DataFrame:
    """
    取得個股 PER / PBR 資料。
    欄位：date, stock_id, PER, PBR, dividend_yield
    """
    start, end = _date_range(days_back)
    return _get("TaiwanStockPER", {
        "data_id": stock_id,
        "start_date": start,
        "end_date": end,
    })


# ── 籌碼面資料 ────────────────────────────────────────────────

def fetch_institutional(stock_id: str, days_back: int = 30) -> pd.DataFrame:
    """
    取得個股三大法人買賣超。
    欄位：date, stock_id, name（外資/投信/自營商）, buy, sell, diff
    """
    dataset = "TaiwanStockInstitutionalInvestorsBuySell"
    cached = _load_cache(dataset, stock_id)
    if cached is not None:
        return cached
    start, end = _date_range(days_back)
    df = _get(dataset, {"data_id": stock_id, "start_date": start, "end_date": end})
    if df.empty:
        return df
    df["diff"] = df["buy"] - df["sell"]
    _save_cache(dataset, stock_id, df)
    return df


def fetch_all_institutional_by_date(date: str) -> pd.DataFrame:
    """
    取得全市場單日三大法人（Backer/Sponsor 限定）。過去日期永久 cache。
    欄位：date, stock_id, name, buy, sell, diff
    """
    cached = _load_all_cache("TaiwanStockInstitutionalInvestorsBuySell", date)
    if cached is not None:
        return cached
    logger.info(f"取得全市場法人 {date}...")
    df = _get("TaiwanStockInstitutionalInvestorsBuySell", {
        "start_date": date,
        "end_date": date,
    }, retry=2, timeout=60)
    if df.empty:
        return df
    df["stock_id"] = df["stock_id"].astype(str).str.strip()
    df["diff"] = pd.to_numeric(df["buy"], errors="coerce") - \
                 pd.to_numeric(df["sell"], errors="coerce")

    # FinMind 批次 API 有時回傳多日資料，過濾到指定日期
    if "date" in df.columns:
        df = df[df["date"].astype(str).str[:10] == date]

    _save_all_cache("TaiwanStockInstitutionalInvestorsBuySell", date, df)
    return df


def _recent_trading_days(end_date: str, count: int) -> list:
    """回傳近 count 個非週末日（不嚴格濾國定假日，多拉幾天無妨）"""
    from datetime import datetime, timedelta
    d = datetime.strptime(end_date, "%Y-%m-%d")
    dates = []
    while len(dates) < count:
        if d.weekday() < 5:
            dates.append(d.strftime("%Y-%m-%d"))
        d -= timedelta(days=1)
    return list(reversed(dates))


def fetch_all_institutional_history(end_date: str, days_back: int = 15) -> pd.DataFrame:
    """
    取得全市場 N 個交易日法人資料（Sponsor 限定）。
    FinMind 不支援「無 data_id + 多日範圍」批次，改用逐日 by_date 串接。
    """
    dates = _recent_trading_days(end_date, days_back)
    logger.info(f"逐日拉法人歷史 {dates[0]}~{dates[-1]}（{len(dates)} 個交易日）...")
    dfs = []
    for d in dates:
        df_day = fetch_all_institutional_by_date(d)
        if not df_day.empty:
            dfs.append(df_day)
    if not dfs:
        return pd.DataFrame()
    df = pd.concat(dfs, ignore_index=True)
    logger.info(f"法人歷史合計：{len(df):,} 筆，{df['stock_id'].nunique()} 支股票，{len(dfs)} 個交易日")
    return df


def fetch_margin(stock_id: str, days_back: int = 10) -> pd.DataFrame:
    """
    取得個股融資融券。
    欄位：date, stock_id, MarginPurchaseBuy, MarginPurchaseSell,
           MarginPurchaseRedeem, MarginPurchaseTodayBalance,
           ShortSaleBuy, ShortSaleSell, ShortSaleTodayBalance...
    """
    dataset = "TaiwanStockMarginPurchaseShortSale"
    cached = _load_cache(dataset, stock_id)
    if cached is not None:
        return cached
    start, end = _date_range(days_back)
    df = _get(dataset, {"data_id": stock_id, "start_date": start, "end_date": end})
    _save_cache(dataset, stock_id, df)
    return df


def fetch_all_margin_by_date(date: str) -> pd.DataFrame:
    """全市場單日融資融券（Sponsor 限定）。過去日期永久 cache。"""
    cached = _load_all_cache("TaiwanStockMarginPurchaseShortSale", date)
    if cached is not None:
        return cached
    logger.info(f"取得全市場融資券 {date}...")
    df = _get("TaiwanStockMarginPurchaseShortSale", {
        "start_date": date,
        "end_date": date,
    }, retry=2, timeout=60)
    if not df.empty:
        df["stock_id"] = df["stock_id"].astype(str).str.strip()
    _save_all_cache("TaiwanStockMarginPurchaseShortSale", date, df)
    return df


def fetch_all_margin_history(end_date: str, days_back: int = 15) -> pd.DataFrame:
    """逐日批次拉 N 個交易日的全市場融資券（Sponsor 限定）"""
    dates = _recent_trading_days(end_date, days_back)
    logger.info(f"逐日拉融資券歷史 {dates[0]}~{dates[-1]}（{len(dates)} 個交易日）...")
    dfs = []
    for d in dates:
        df_day = fetch_all_margin_by_date(d)
        if not df_day.empty:
            dfs.append(df_day)
    if not dfs:
        return pd.DataFrame()
    df = pd.concat(dfs, ignore_index=True)
    logger.info(f"融資券歷史合計：{len(df):,} 筆，{df['stock_id'].nunique()} 支，{len(dfs)} 個交易日")
    return df


def fetch_all_shareholding_by_date(date: str) -> pd.DataFrame:
    """全市場單日外資持股（Sponsor 限定）。過去日期永久 cache。"""
    cached = _load_all_cache("TaiwanStockShareholding", date)
    if cached is not None:
        return cached
    df = _get("TaiwanStockShareholding", {
        "start_date": date, "end_date": date,
    }, retry=2, timeout=60)
    if df.empty:
        return df
    df["stock_id"] = df["stock_id"].astype(str).str.strip()
    if "date" in df.columns:
        df = df[df["date"].astype(str).str[:10] == date]
    _save_all_cache("TaiwanStockShareholding", date, df)
    return df


def fetch_all_shareholding_history(end_date: str, days_back: int = 15) -> pd.DataFrame:
    """逐日批次拉 N 個交易日的全市場外資持股"""
    dates = _recent_trading_days(end_date, days_back)
    logger.info(f"逐日拉外資持股 {dates[0]}~{dates[-1]}（{len(dates)} 個交易日）...")
    dfs = []
    for d in dates:
        df_day = fetch_all_shareholding_by_date(d)
        if not df_day.empty:
            dfs.append(df_day)
    if not dfs:
        return pd.DataFrame()
    df = pd.concat(dfs, ignore_index=True)
    logger.info(f"外資持股合計：{len(df):,} 筆，{df['stock_id'].nunique()} 支，{len(dfs)} 個交易日")
    return df


def fetch_shareholding(stock_id: str, days_back: int = 60) -> pd.DataFrame:
    """
    取得外資持股比例。
    欄位：date, stock_id, ForeignInvestmentSharesRatio（外資持股比例%）
    """
    dataset = "TaiwanStockShareholding"
    cached = _load_cache(dataset, stock_id)
    if cached is not None:
        return cached
    start, end = _date_range(days_back)
    df = _get(dataset, {"data_id": stock_id, "start_date": start, "end_date": end})
    _save_cache(dataset, stock_id, df)
    return df


def fetch_holding_distribution(stock_id: str, days_back: int = 60) -> pd.DataFrame:
    """
    取得股權分散表（大戶持股週變化）。需 Backer/Sponsor。
    欄位：date, stock_id, HoldingSharesLevel（持股分級）,
           NumberOfShareholderAccounts, SharesHeld, Percent
    """
    dataset = "TaiwanStockHoldingSharesPer"
    cached = _load_cache(dataset, stock_id)
    if cached is not None:
        return cached
    start, end = _date_range(days_back)
    # 股權分散為非核心資料，逾時直接略過，不重試浪費時間
    df = _get(dataset, {"data_id": stock_id, "start_date": start, "end_date": end},
              retry=1, timeout=8)
    _save_cache(dataset, stock_id, df)
    return df


def fetch_all_holding_distribution_by_date(date: str) -> pd.DataFrame:
    """全市場單日股權分散（Sponsor 限定）。過去日期永久 cache。"""
    cached = _load_all_cache("TaiwanStockHoldingSharesPer", date)
    if cached is not None:
        return cached
    df = _get("TaiwanStockHoldingSharesPer", {
        "start_date": date, "end_date": date,
    }, retry=2, timeout=90)
    if df.empty:
        return df
    df["stock_id"] = df["stock_id"].astype(str).str.strip()
    if "date" in df.columns:
        df = df[df["date"].astype(str).str[:10] == date]
    _save_all_cache("TaiwanStockHoldingSharesPer", date, df)
    return df


def fetch_all_holding_distribution_history(end_date: str, days_back: int = 60) -> pd.DataFrame:
    """
    股權分散每週公布。逐日嘗試（多數天會空），合併成歷史。
    """
    dates = _recent_trading_days(end_date, days_back)
    logger.info(f"逐日拉股權分散 {dates[0]}~{dates[-1]}（{len(dates)} 個交易日，週更新）...")
    dfs = []
    for d in dates:
        df_day = fetch_all_holding_distribution_by_date(d)
        if not df_day.empty:
            dfs.append(df_day)
    if not dfs:
        return pd.DataFrame()
    df = pd.concat(dfs, ignore_index=True)
    logger.info(f"股權分散合計：{len(df):,} 筆，{df['stock_id'].nunique()} 支，{len(dfs)} 天有資料")
    return df


# ── 基本面資料 ────────────────────────────────────────────────

def fetch_month_revenue(stock_id: str, months_back: int = REVENUE_LOOKBACK_MONTHS) -> pd.DataFrame:
    """
    取得個股月營收。
    欄位：date, stock_id, country, revenue, revenue_month, revenue_year
    """
    dataset = "TaiwanStockMonthRevenue"
    cached = _load_cache(dataset, stock_id)
    if cached is not None:
        return cached
    start, end = _month_range(months_back + 1)
    df = _get(dataset, {"data_id": stock_id, "start_date": start, "end_date": end})
    _save_cache(dataset, stock_id, df)
    return df


def fetch_all_revenue_by_date(date: str) -> pd.DataFrame:
    """
    取得全市場單月月營收（Sponsor 限定）。過去日期永久 cache。
    適合每月 10 日後批次更新。
    """
    cached = _load_all_cache("TaiwanStockMonthRevenue", date)
    if cached is not None:
        return cached
    logger.info(f"取得全市場月營收 {date}...")
    df = _get("TaiwanStockMonthRevenue", {
        "start_date": date,
        "end_date": date,
    }, retry=2, timeout=60)
    _save_all_cache("TaiwanStockMonthRevenue", date, df)
    return df


def fetch_all_revenue_history(end_date: str, months_back: int = 13) -> pd.DataFrame:
    """
    月營收每月 10 號附近公布，逐月對「該月 15 號」做 by_date 批次。
    """
    from datetime import datetime
    end_d = datetime.strptime(end_date, "%Y-%m-%d")
    targets = []
    for offset in range(months_back):
        y, m = end_d.year, end_d.month - offset
        while m <= 0:
            m += 12; y -= 1
        targets.append(f"{y:04d}-{m:02d}-15")
    # 跳過未來日期（資料尚未公布，永遠是空 → 浪費 API quota）
    today = datetime.today().strftime("%Y-%m-%d")
    targets = [d for d in targets if d < today]
    logger.info(f"逐月拉月營收（{len(targets)} 個月）...")
    dfs = []
    for d in targets:
        df_day = fetch_all_revenue_by_date(d)
        if not df_day.empty:
            dfs.append(df_day)
    if not dfs:
        return pd.DataFrame()
    df = pd.concat(dfs, ignore_index=True)
    df["stock_id"] = df["stock_id"].astype(str).str.strip()
    df = df.drop_duplicates(subset=["stock_id", "date"], keep="first")
    logger.info(f"月營收合計：{len(df):,} 筆，{df['stock_id'].nunique()} 支")
    return df


def fetch_financial_statements(stock_id: str) -> pd.DataFrame:
    """
    取得個股損益表（季報）。
    欄位：date, stock_id, type, value, origin_name
    重要 type：
      - EPS（每股盈餘）
      - GrossProfit（毛利）
      - Revenue（營收）
      - GrossProfitMargin（毛利率）
    """
    dataset = "TaiwanStockFinancialStatements"
    cached = _load_cache(dataset, stock_id)
    if cached is not None:
        return cached
    # 取近 2 年財報
    start, _ = _date_range(730)
    end = datetime.today().strftime("%Y-%m-%d")
    df = _get(dataset, {"data_id": stock_id, "start_date": start, "end_date": end})
    _save_cache(dataset, stock_id, df)
    return df


def fetch_all_financial_by_date(date: str) -> pd.DataFrame:
    """全市場單日財報（Sponsor 限定）。過去日期永久 cache。"""
    cached = _load_all_cache("TaiwanStockFinancialStatements", date)
    if cached is not None:
        return cached
    df = _get("TaiwanStockFinancialStatements", {
        "start_date": date, "end_date": date,
    }, retry=2, timeout=90)
    if df.empty:
        return df
    df["stock_id"] = df["stock_id"].astype(str).str.strip()
    _save_all_cache("TaiwanStockFinancialStatements", date, df)
    return df


def fetch_all_financial_history(end_date: str, days_back: int = 730) -> pd.DataFrame:
    """
    財報每季公布（5/8/11/3 月）。對 8 個季度的代表日做 by_date 批次。
    """
    from datetime import datetime
    end_d = datetime.strptime(end_date, "%Y-%m-%d")
    # 對近 8 季 + 1 個 buffer：每季結束月的 15 號
    targets = []
    for q in range(9):
        y = end_d.year
        m = end_d.month - q * 3
        while m <= 0:
            m += 12; y -= 1
        targets.append(f"{y:04d}-{m:02d}-15")
    # 跳過未來日期（資料尚未公布，永遠是空 → 浪費 API quota）
    today = datetime.today().strftime("%Y-%m-%d")
    targets = [d for d in targets if d < today]
    logger.info(f"逐季拉財報（{len(targets)} 個季度代表日）...")
    dfs = []
    for d in targets:
        df_day = fetch_all_financial_by_date(d)
        if not df_day.empty:
            dfs.append(df_day)
    if not dfs:
        return pd.DataFrame()
    df = pd.concat(dfs, ignore_index=True)
    df["stock_id"] = df["stock_id"].astype(str).str.strip()
    df = df.drop_duplicates(subset=["stock_id", "date", "type"], keep="first")
    logger.info(f"財報合計：{len(df):,} 筆，{df['stock_id'].nunique()} 支")
    return df


def fetch_balance_sheet(stock_id: str) -> pd.DataFrame:
    """取得資產負債表（選用，Sprint 2+）"""
    start, _ = _date_range(400)
    end = datetime.today().strftime("%Y-%m-%d")
    return _get("TaiwanStockBalanceSheet", {
        "data_id": stock_id,
        "start_date": start,
        "end_date": end,
    })


# ── 分點資料（Sprint 3，Sponsor 限定）────────────────────────

def _broker_cache_path(stock_id: str, date: str) -> Path:
    return _CACHE_ROOT / f"broker_{stock_id}_{date.replace('-', '')}.pkl"


def _load_broker_cache(stock_id: str, date: str) -> Optional[pd.DataFrame]:
    """當日資料不從 cache 讀（避免拿到盤中尚未完整的資料）"""
    today = datetime.today().strftime("%Y-%m-%d")
    if date >= today:
        return None
    path = _broker_cache_path(stock_id, date)
    if not path.exists():
        return None
    try:
        with open(path, "rb") as f:
            return pickle.load(f)
    except Exception:
        path.unlink(missing_ok=True)
        return None


def _save_broker_cache(stock_id: str, date: str, df: pd.DataFrame) -> None:
    today = datetime.today().strftime("%Y-%m-%d")
    if date >= today:
        return
    if df is None:
        return
    _CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    path = _broker_cache_path(stock_id, date)
    try:
        with open(path, "wb") as f:
            pickle.dump(df, f)
    except Exception:
        pass


def fetch_broker_data(stock_id: str, date: str,
                        retry: int = 3) -> pd.DataFrame:
    """
    取得個股單日分點進出（Sponsor 限定，逐筆版）。
    過去日期會永久 disk cache（broker 資料公布後不再變動）。
    對 5xx / Timeout / ConnectionError 自動重試（指數退避），4xx 直接放棄。
    """
    cached = _load_broker_cache(stock_id, date)
    if cached is not None:
        logger.debug(f"[broker cache hit] {stock_id} {date}")
        return cached

    payload = {
        "data_id": stock_id,
        "date": date,
        "token": FINMIND_TOKEN,
    }
    for attempt in range(retry):
        try:
            resp = requests.get(FINMIND_BROKER_URL, params=payload, timeout=60)
            status = resp.status_code
            if 500 <= status < 600:
                logger.warning(f"[broker] {stock_id} {date} HTTP {status}（第 {attempt+1}/{retry} 次）")
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") != 200:
                return pd.DataFrame()
            df = pd.DataFrame(data.get("data", []))
            time.sleep(API_SLEEP_SECONDS)
            _save_broker_cache(stock_id, date, df)
            return df
        except requests.exceptions.HTTPError as e:
            # 4xx 不重試（請求本身有問題）
            logger.warning(f"[broker] {stock_id} {date} HTTP {e.response.status_code}，放棄")
            return pd.DataFrame()
        except (requests.exceptions.Timeout,
                requests.exceptions.ConnectionError) as e:
            logger.warning(f"[broker] {stock_id} {date} 連線問題（第 {attempt+1}/{retry} 次）：{e}")
            time.sleep(2 ** attempt)
        except Exception as e:
            logger.error(f"分點資料取得失敗 {stock_id} {date}：{e}")
            return pd.DataFrame()

    logger.warning(f"[broker] {stock_id} {date} 已重試 {retry} 次，放棄")
    return pd.DataFrame()


def fetch_all_broker_agg(date: str) -> pd.DataFrame:
    """
    FinMind 無全市場分點批次 API，回傳空 DataFrame。
    分點資料需逐支呼叫 fetch_broker_data()。
    scanner.py 的 full mode 會在候選股確定後再逐支補抓。
    """
    logger.info("全市場分點批次 API 不支援，候選股將於評分後逐支補抓")
    return pd.DataFrame()


# ── 批次資料快取（減少重複 API 呼叫）────────────────────────────

class DailyDataCache:
    """
    單日批次資料快取器。
    掃描器每日執行時，先把全市場資料一次拉下來，
    再逐支計算，避免重複呼叫 API。
    """

    def __init__(self, scan_date: Optional[str] = None):
        self.date = scan_date or datetime.today().strftime("%Y-%m-%d")
        self._institutional: Optional[pd.DataFrame] = None
        self._inst_hist: Optional[pd.DataFrame] = None
        self._margin: Optional[pd.DataFrame] = None
        self._margin_hist: Optional[pd.DataFrame] = None
        self._share_hist: Optional[pd.DataFrame] = None
        self._holding_hist: Optional[pd.DataFrame] = None
        self._revenue_hist: Optional[pd.DataFrame] = None
        self._fin_hist: Optional[pd.DataFrame] = None
        self._price: Optional[pd.DataFrame] = None
        self._price_hist: Optional[pd.DataFrame] = None
        self._revenue: Optional[pd.DataFrame] = None

    def get_institutional(self) -> pd.DataFrame:
        if self._institutional is None:
            self._institutional = fetch_all_institutional_by_date(self.date)
        return self._institutional

    def get_institutional_history(self, days_back: int = 20) -> pd.DataFrame:
        """全市場 N 天法人歷史，逐日批次拉"""
        if self._inst_hist is None:
            self._inst_hist = fetch_all_institutional_history(self.date, days_back)
        return self._inst_hist

    def institutional_history_for(self, stock_id: str) -> pd.DataFrame:
        """從批次歷史快取中取出特定股票（取代 fetch_institutional 個別呼叫）"""
        df = self.get_institutional_history()
        if df.empty:
            return df
        return df[df["stock_id"] == stock_id].copy()

    def get_margin_history(self, days_back: int = 15) -> pd.DataFrame:
        """全市場 N 天融資券歷史，逐日批次拉"""
        if self._margin_hist is None:
            self._margin_hist = fetch_all_margin_history(self.date, days_back)
        return self._margin_hist

    def margin_history_for(self, stock_id: str) -> pd.DataFrame:
        """從批次融資券歷史 cache 撈個股（取代 fetch_margin 個別呼叫）"""
        df = self.get_margin_history()
        if df.empty:
            return df
        return df[df["stock_id"] == stock_id].copy()

    def get_shareholding_history(self, days_back: int = 20) -> pd.DataFrame:
        """全市場 N 天外資持股歷史，逐日批次拉"""
        if self._share_hist is None:
            self._share_hist = fetch_all_shareholding_history(self.date, days_back)
        return self._share_hist

    def shareholding_history_for(self, stock_id: str) -> pd.DataFrame:
        """從批次外資持股 cache 撈個股（取代 fetch_shareholding 個別呼叫）"""
        df = self.get_shareholding_history()
        if df.empty:
            return df
        return df[df["stock_id"] == stock_id].copy()

    def get_holding_distribution_history(self, days_back: int = 35) -> pd.DataFrame:
        """全市場 N 天股權分散歷史（每週公布，逐日嘗試）"""
        if self._holding_hist is None:
            self._holding_hist = fetch_all_holding_distribution_history(self.date, days_back)
        return self._holding_hist

    def holding_distribution_for(self, stock_id: str) -> pd.DataFrame:
        """從批次股權分散 cache 撈個股"""
        df = self.get_holding_distribution_history()
        if df.empty:
            return df
        return df[df["stock_id"] == stock_id].copy()

    def get_revenue_history(self, months_back: int = 13) -> pd.DataFrame:
        """全市場 N 個月月營收歷史，一次批次抓"""
        if self._revenue_hist is None:
            self._revenue_hist = fetch_all_revenue_history(self.date, months_back)
        return self._revenue_hist

    def revenue_history_for(self, stock_id: str) -> pd.DataFrame:
        """從批次月營收 cache 撈個股"""
        df = self.get_revenue_history()
        if df.empty:
            return df
        return df[df["stock_id"] == stock_id].copy()

    def get_financial_history(self, days_back: int = 730) -> pd.DataFrame:
        """全市場 N 天財報歷史，一次批次抓"""
        if self._fin_hist is None:
            self._fin_hist = fetch_all_financial_history(self.date, days_back)
        return self._fin_hist

    def financial_for(self, stock_id: str) -> pd.DataFrame:
        """從批次財報 cache 撈個股"""
        df = self.get_financial_history()
        if df.empty:
            return df
        return df[df["stock_id"] == stock_id].copy()

    def get_price_history(self, days_back: int = 75) -> pd.DataFrame:
        """全市場 N 天股價歷史，逐日批次拉（MA60 需 60 天 + buffer）"""
        if self._price_hist is None:
            self._price_hist = fetch_all_stock_price_history(self.date, days_back)
        return self._price_hist

    def price_history_for(self, stock_id: str) -> pd.DataFrame:
        """從批次股價 cache 撈個股；若 cache 為空則 fallback 到個股 fetch"""
        df = self.get_price_history()
        if df.empty:
            # 批次失敗時 fallback：個別呼叫，避免完全沒資料
            return fetch_stock_price(stock_id)
        result = df[df["stock_id"] == stock_id].copy()
        if result.empty:
            # 該股票不在批次資料裡（如新上市），fallback
            return fetch_stock_price(stock_id)
        return result

    def get_margin(self) -> pd.DataFrame:
        if self._margin is None:
            self._margin = fetch_all_margin_by_date(self.date)
        return self._margin

    def get_price(self) -> pd.DataFrame:
        if self._price is None:
            self._price = fetch_all_stock_price_by_date(self.date)
        return self._price

    def get_broker_agg(self) -> pd.DataFrame:
        """取得全市場分點彙總（SecIdAgg，一次 API 呼叫，Sponsor 限定）"""
        if not hasattr(self, '_broker_agg') or self._broker_agg is None:
            self._broker_agg = fetch_all_broker_agg(self.date)
        return self._broker_agg

    def broker_agg_for(self, stock_id: str) -> pd.DataFrame:
        """從快取篩選特定股票的分點彙總"""
        df = self.get_broker_agg()
        if df.empty:
            return df
        return df[df["stock_id"] == stock_id].copy()

    # ── Sprint 2 新增 ─────────────────────────────────────────
    def get_holding_for(self, stock_id: str, days_back: int = 90) -> pd.DataFrame:
        """
        取得個股股權分散表（Backer/Sponsor 限定）。
        資料每週更新，批次快取意義不大，改為按股票個別拉取。
        """
        return fetch_holding_distribution(stock_id, days_back=days_back)

    def get_margin_history_for(self, stock_id: str, days_back: int = 30) -> pd.DataFrame:
        """取得個股融資券歷史（用於趨勢分析）"""
        from data.fetcher import fetch_margin
        return fetch_margin(stock_id, days_back=days_back)

    def institutional_for(self, stock_id: str) -> pd.DataFrame:
        """從快取中篩選特定股票的法人資料"""
        df = self.get_institutional()
        if df.empty:
            return df
        return df[df["stock_id"] == stock_id].copy()

    def margin_for(self, stock_id: str) -> pd.DataFrame:
        """從快取中篩選特定股票的融資券資料"""
        df = self.get_margin()
        if df.empty:
            return df
        return df[df["stock_id"] == stock_id].copy()
