# data/fetcher.py
# ============================================================
#  FinMind API 資料拉取模組
#  負責所有與 FinMind 的通訊，統一處理錯誤與 Rate Limit
# ============================================================

import time
import logging
from datetime import datetime, timedelta
from typing import Optional

import requests
import pandas as pd

from config.settings import (
    FINMIND_TOKEN, FINMIND_BASE_URL, FINMIND_BROKER_URL,
    API_SLEEP_SECONDS, LOOKBACK_DAYS, REVENUE_LOOKBACK_MONTHS
)

logger = logging.getLogger(__name__)


# ── 基礎請求函式 ──────────────────────────────────────────────

def _get(dataset: str, params: dict, retry: int = 3) -> pd.DataFrame:
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
            resp = requests.get(FINMIND_BASE_URL, params=payload, timeout=30)
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
        except requests.exceptions.RequestException as e:
            logger.error(f"[{dataset}] 請求失敗：{e}")
            time.sleep(2 ** attempt)

    logger.error(f"[{dataset}] 已重試 {retry} 次，放棄")
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
    start, end = _date_range(days_back)
    return _get("TaiwanStockPrice", {
        "data_id": stock_id,
        "start_date": start,
        "end_date": end,
    })


def fetch_all_stock_price_by_date(date: str) -> pd.DataFrame:
    """
    取得全市場單日所有股票收盤資料（Backer/Sponsor 限定）。
    用於批次掃描，避免逐支呼叫。
    """
    logger.info(f"取得全市場收盤 {date}...")
    return _get("TaiwanStockPrice", {"start_date": date, "end_date": date})


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
    start, end = _date_range(days_back)
    df = _get("TaiwanStockInstitutionalInvestorsBuySell", {
        "data_id": stock_id,
        "start_date": start,
        "end_date": end,
    })
    if df.empty:
        return df
    df["diff"] = df["buy"] - df["sell"]
    return df


def fetch_all_institutional_by_date(date: str) -> pd.DataFrame:
    """
    取得全市場單日三大法人（Backer/Sponsor 限定）。
    這是掃描器的核心呼叫，一次拿全市場，避免逐支請求。
    欄位：date, stock_id, name, buy, sell, diff
    """
    logger.info(f"取得全市場法人 {date}...")
    df = _get("TaiwanStockInstitutionalInvestorsBuySell", {
        "start_date": date,
        "end_date": date,
    })
    if df.empty:
        return df
    df["diff"] = pd.to_numeric(df["buy"], errors="coerce") - \
                 pd.to_numeric(df["sell"], errors="coerce")
    return df


def fetch_margin(stock_id: str, days_back: int = 10) -> pd.DataFrame:
    """
    取得個股融資融券。
    欄位：date, stock_id, MarginPurchaseBuy, MarginPurchaseSell,
           MarginPurchaseRedeem, MarginPurchaseTodayBalance,
           ShortSaleBuy, ShortSaleSell, ShortSaleTodayBalance...
    """
    start, end = _date_range(days_back)
    return _get("TaiwanStockMarginPurchaseShortSale", {
        "data_id": stock_id,
        "start_date": start,
        "end_date": end,
    })


def fetch_all_margin_by_date(date: str) -> pd.DataFrame:
    """
    取得全市場單日融資融券（Backer/Sponsor 限定）。
    """
    logger.info(f"取得全市場融資券 {date}...")
    return _get("TaiwanStockMarginPurchaseShortSale", {
        "start_date": date,
        "end_date": date,
    })


def fetch_shareholding(stock_id: str, days_back: int = 60) -> pd.DataFrame:
    """
    取得外資持股比例。
    欄位：date, stock_id, ForeignInvestmentSharesRatio（外資持股比例%）
    """
    start, end = _date_range(days_back)
    return _get("TaiwanStockShareholding", {
        "data_id": stock_id,
        "start_date": start,
        "end_date": end,
    })


def fetch_holding_distribution(stock_id: str, days_back: int = 60) -> pd.DataFrame:
    """
    取得股權分散表（大戶持股週變化）。需 Backer/Sponsor。
    欄位：date, stock_id, HoldingSharesLevel（持股分級）,
           NumberOfShareholderAccounts, SharesHeld, Percent
    """
    start, end = _date_range(days_back)
    return _get("TaiwanStockHoldingSharesPer", {
        "data_id": stock_id,
        "start_date": start,
        "end_date": end,
    })


# ── 基本面資料 ────────────────────────────────────────────────

def fetch_month_revenue(stock_id: str, months_back: int = REVENUE_LOOKBACK_MONTHS) -> pd.DataFrame:
    """
    取得個股月營收。
    欄位：date, stock_id, country, revenue, revenue_month, revenue_year
    """
    start, end = _month_range(months_back + 1)
    return _get("TaiwanStockMonthRevenue", {
        "data_id": stock_id,
        "start_date": start,
        "end_date": end,
    })


def fetch_all_revenue_by_date(date: str) -> pd.DataFrame:
    """
    取得全市場單月月營收（Backer/Sponsor 限定）。
    適合每月 10 日後批次更新。
    """
    logger.info(f"取得全市場月營收 {date}...")
    return _get("TaiwanStockMonthRevenue", {
        "start_date": date,
        "end_date": date,
    })


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
    # 取近 2 年財報
    start, _ = _date_range(730)
    end = datetime.today().strftime("%Y-%m-%d")
    return _get("TaiwanStockFinancialStatements", {
        "data_id": stock_id,
        "start_date": start,
        "end_date": end,
    })


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

def fetch_broker_data(stock_id: str, date: str) -> pd.DataFrame:
    """
    取得個股單日分點進出（Sponsor 限定）。
    欄位：date, stock_id, securities_trader_id（券商代碼）,
           securities_trader（券商名稱）, buy, sell, diff
    ⚠️ 此資料量極大，建議快取至本地 DB。
    """
    payload = {
        "data_id": stock_id,
        "date": date,
        "token": FINMIND_TOKEN,
    }
    try:
        resp = requests.get(FINMIND_BROKER_URL, params=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != 200:
            return pd.DataFrame()
        df = pd.DataFrame(data.get("data", []))
        time.sleep(API_SLEEP_SECONDS)
        return df
    except Exception as e:
        logger.error(f"分點資料取得失敗 {stock_id} {date}：{e}")
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
        self._margin: Optional[pd.DataFrame] = None
        self._price: Optional[pd.DataFrame] = None
        self._revenue: Optional[pd.DataFrame] = None

    def get_institutional(self) -> pd.DataFrame:
        if self._institutional is None:
            self._institutional = fetch_all_institutional_by_date(self.date)
        return self._institutional

    def get_margin(self) -> pd.DataFrame:
        if self._margin is None:
            self._margin = fetch_all_margin_by_date(self.date)
        return self._margin

    def get_price(self) -> pd.DataFrame:
        if self._price is None:
            self._price = fetch_all_stock_price_by_date(self.date)
        return self._price

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
