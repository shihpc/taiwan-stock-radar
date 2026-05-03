# backtest/runner.py
# ============================================================
#  回測執行器
#  流程：
#    1. 載入指定時間範圍的歷史資料（股價＋法人＋財報）
#    2. 逐日滾動視窗評分
#    3. 記錄進出場，計算損益
#    4. 輸出完整績效報告
# ============================================================

import os
import json
import logging
import time
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import numpy as np

from backtest.engine import BacktestConfig, Trade, BacktestStats, calc_stats
from engine.scorer import score_stock
from engine.indicators import prepare_price_df

logger = logging.getLogger(__name__)


# ── 歷史資料載入器 ────────────────────────────────────────────

class HistoricalDataLoader:
    """
    批次載入並快取歷史資料，避免回測中重複 API 呼叫。
    建議先執行 prefetch() 把所有資料下載到本地，
    再執行回測，速度可快 100 倍以上。
    """

    def __init__(self, cache_dir: str = "backtest/cache"):
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self._price_cache:       dict[str, pd.DataFrame] = {}
        self._inst_cache:        dict[str, pd.DataFrame] = {}
        self._revenue_cache:     dict[str, pd.DataFrame] = {}
        self._financial_cache:   dict[str, pd.DataFrame] = {}
        self._margin_cache:      dict[str, pd.DataFrame] = {}

    # ── 快取讀寫 ──────────────────────────────────────────────

    def _cache_path(self, key: str) -> str:
        safe = key.replace("/", "_").replace(":", "_")
        base = os.path.join(self.cache_dir, safe)
        try:
            import pyarrow  # noqa
            return base + ".parquet"
        except ImportError:
            return base + ".csv"

    def _save(self, key: str, df: pd.DataFrame):
        try:
            path = self._cache_path(key)
            if path.endswith(".parquet"):
                df.to_parquet(path, index=False)
            else:
                df.to_csv(path, index=False)
        except Exception as e:
            logger.debug(f"快取儲存失敗 {key}：{e}")

    def _load(self, key: str) -> Optional[pd.DataFrame]:
        path = self._cache_path(key)
        if os.path.exists(path):
            try:
                if path.endswith(".parquet"):
                    return pd.read_parquet(path)
                else:
                    return pd.read_csv(path)
            except Exception:
                pass
        return None

    # ── 資料取得（優先快取）──────────────────────────────────

    def get_price(self, stock_id: str,
                  start: str, end: str) -> pd.DataFrame:
        key = f"price_{stock_id}_{start}_{end}"
        if key in self._price_cache:
            return self._price_cache[key]
        cached = self._load(key)
        if cached is not None:
            self._price_cache[key] = cached
            return cached

        from data.fetcher import _get
        df = _get("TaiwanStockPrice", {
            "data_id": stock_id,
            "start_date": start,
            "end_date": end
        })
        if not df.empty:
            self._save(key, df)
            self._price_cache[key] = df
        return df

    def get_institutional(self, stock_id: str,
                          start: str, end: str) -> pd.DataFrame:
        key = f"inst_{stock_id}_{start}_{end}"
        if key in self._inst_cache:
            return self._inst_cache[key]
        cached = self._load(key)
        if cached is not None:
            self._inst_cache[key] = cached
            return cached

        from data.fetcher import _get
        df = _get("TaiwanStockInstitutionalInvestorsBuySell", {
            "data_id": stock_id,
            "start_date": start,
            "end_date": end
        })
        if not df.empty:
            df["diff"] = pd.to_numeric(df["buy"], errors="coerce") - \
                         pd.to_numeric(df["sell"], errors="coerce")
            self._save(key, df)
            self._inst_cache[key] = df
        return df

    def get_revenue(self, stock_id: str, start: str) -> pd.DataFrame:
        key = f"rev_{stock_id}_{start}"
        if key in self._revenue_cache:
            return self._revenue_cache[key]
        cached = self._load(key)
        if cached is not None:
            self._revenue_cache[key] = cached
            return cached

        from data.fetcher import _get
        df = _get("TaiwanStockMonthRevenue", {
            "data_id": stock_id,
            "start_date": start
        })
        if not df.empty:
            self._save(key, df)
            self._revenue_cache[key] = df
        return df

    def get_financial(self, stock_id: str, start: str) -> pd.DataFrame:
        key = f"fin_{stock_id}_{start}"
        if key in self._financial_cache:
            return self._financial_cache[key]
        cached = self._load(key)
        if cached is not None:
            self._financial_cache[key] = cached
            return cached

        from data.fetcher import _get
        df = _get("TaiwanStockFinancialStatements", {
            "data_id": stock_id,
            "start_date": start
        })
        if not df.empty:
            self._save(key, df)
            self._financial_cache[key] = df
        return df

    def get_margin(self, stock_id: str,
                   start: str, end: str) -> pd.DataFrame:
        key = f"margin_{stock_id}_{start}_{end}"
        if key in self._margin_cache:
            return self._margin_cache[key]
        cached = self._load(key)
        if cached is not None:
            self._margin_cache[key] = cached
            return cached

        from data.fetcher import _get
        df = _get("TaiwanStockMarginPurchaseShortSale", {
            "data_id": stock_id,
            "start_date": start,
            "end_date": end
        })
        if not df.empty:
            self._save(key, df)
            self._margin_cache[key] = df
        return df

    def get_holding(self, stock_id: str,
                    start: str, end: str) -> pd.DataFrame:
        """
        取得股權分散表（Sprint 2，Backer/Sponsor 限定）。
        資料每週更新，回測快取以「股票+起始月」為 key。
        """
        key = f"holding_{stock_id}_{start[:7]}"
        if key in self._margin_cache:   # 共用快取 dict
            return self._margin_cache[key]
        cached = self._load(key)
        if cached is not None:
            self._margin_cache[key] = cached
            return cached

        from data.fetcher import _get
        df = _get("TaiwanStockHoldingSharesPer", {
            "data_id": stock_id,
            "start_date": start,
            "end_date": end
        })
        if not df.empty:
            self._save(key, df)
            self._margin_cache[key] = df
        return df

    def prefetch(self, stock_ids: list[str], start: str, end: str):
        """
        預先批次下載所有股票的歷史資料到本地快取。
        建議回測前先執行一次，之後回測不需再呼叫 API。
        """
        total = len(stock_ids)
        logger.info(f"預下載 {total} 支股票資料（{start} ~ {end}）...")
        for i, sid in enumerate(stock_ids):
            if i % 20 == 0:
                logger.info(f"  進度 {i}/{total}")
            self.get_price(sid, start, end)
            self.get_institutional(sid, start, end)
            self.get_revenue(sid, start[:7] + "-01")
            self.get_financial(sid, start)
            self.get_margin(sid, start, end)
            time.sleep(0.3)   # 避免 API 限速
        logger.info("預下載完成！")


# ── 滾動視窗評分器 ────────────────────────────────────────────

def score_on_date(
    stock_id:   str,
    eval_date:  str,
    loader:     HistoricalDataLoader,
    lookback:   int = 90,
    fund_start: str = "2019-01-01",
) -> Optional[dict]:
    """
    在指定評分日，使用截至當日的歷史資料評分一支股票。
    嚴格確保無未來資料洩漏（所有資料只取 eval_date 之前）。

    包含：Sprint 1（法人/技術/基本面）+ Sprint 2（股權分散/融資券）
    Sprint 3（分點）因資料量過大，回測中預設不啟用。
    """
    start_dt = (datetime.strptime(eval_date, "%Y-%m-%d")
                - timedelta(days=int(lookback * 1.5))).strftime("%Y-%m-%d")

    # Sprint 1 資料
    price_df  = loader.get_price(stock_id, start_dt, eval_date)
    inst_df   = loader.get_institutional(stock_id, start_dt, eval_date)
    margin_df = loader.get_margin(stock_id, start_dt, eval_date)
    rev_df    = loader.get_revenue(stock_id, fund_start)
    fin_df    = loader.get_financial(stock_id, fund_start)

    # Sprint 2：股權分散（週資料，從半年前開始）
    holding_start = (datetime.strptime(eval_date, "%Y-%m-%d")
                     - timedelta(days=180)).strftime("%Y-%m-%d")
    holding_df = loader.get_holding(stock_id, holding_start, eval_date)

    # 截斷未來資料
    for df_ref, df in [("rev", rev_df), ("fin", fin_df)]:
        pass  # 在 loader 層已按 eval_date 過濾

    if not rev_df.empty and "date" in rev_df.columns:
        rev_df = rev_df[rev_df["date"] <= eval_date].copy()
    if not fin_df.empty and "date" in fin_df.columns:
        fin_df = fin_df[fin_df["date"] <= eval_date].copy()
    if not holding_df.empty and "date" in holding_df.columns:
        holding_df = holding_df[holding_df["date"] <= eval_date].copy()

    if price_df.empty or len(price_df) < 20:
        return None

    try:
        result = score_stock(
            stock_id=stock_id,
            price_df=price_df,
            institutional_df=inst_df,
            revenue_df=rev_df,
            financial_df=fin_df,
            margin_history_df=margin_df,  # Sprint 2
            holding_df=holding_df,         # Sprint 2
            broker_df=pd.DataFrame(),      # Sprint 3：回測預設不啟用
        )
        return result
    except Exception as e:
        logger.debug(f"評分失敗 {stock_id} {eval_date}：{e}")
        return None


# ── 主回測執行器 ──────────────────────────────────────────────

class BacktestRunner:
    """
    主回測執行器。

    使用方式：
        runner = BacktestRunner(config, stock_ids)
        results = runner.run()
        runner.print_report(results)
    """

    def __init__(self, config: BacktestConfig,
                 stock_ids: list[str],
                 stock_names: dict[str, str] = None):
        self.config      = config
        self.stock_ids   = stock_ids
        self.stock_names = stock_names or {}
        self.loader      = HistoricalDataLoader()
        self.trades:     list[Trade] = []

    def _get_trading_dates(self) -> list[str]:
        """取得回測期間所有交易日（以月頻率掃描，實際以資料為準）"""
        from data.fetcher import _get
        df = _get("TaiwanStockTradingDate", {})
        if not df.empty and "date" in df.columns:
            dates = df[
                (df["date"] >= self.config.start_date) &
                (df["date"] <= self.config.end_date)
            ]["date"].tolist()
            return sorted(dates)
        # fallback：用週頻率
        start = datetime.strptime(self.config.start_date, "%Y-%m-%d")
        end   = datetime.strptime(self.config.end_date,   "%Y-%m-%d")
        dates = []
        cur = start
        while cur <= end:
            if cur.weekday() < 5:
                dates.append(cur.strftime("%Y-%m-%d"))
            cur += timedelta(days=7)   # 每週掃描一次（加速回測）
        return dates

    def _get_next_open_price(self, stock_id: str,
                              entry_date: str) -> Optional[float]:
        """取得進場日的開盤價（評分日後第一個交易日）"""
        end = (datetime.strptime(entry_date, "%Y-%m-%d")
               + timedelta(days=5)).strftime("%Y-%m-%d")
        df = self.loader.get_price(stock_id, entry_date, end)
        if df.empty:
            return None
        df["date"] = pd.to_datetime(df["date"])
        future = df[df["date"] > pd.Timestamp(entry_date)]
        if future.empty:
            return None
        return float(pd.to_numeric(future.iloc[0]["open"], errors="coerce"))

    def _get_exit_price(self, stock_id: str,
                         entry_date: str) -> tuple[float, str, str]:
        """
        取得出場價、出場日、出場原因。
        優先判斷停損/停利，否則持滿 hold_days 出場。
        回傳：(exit_price, exit_date, exit_reason)
        """
        start = (datetime.strptime(entry_date, "%Y-%m-%d")
                 + timedelta(days=1)).strftime("%Y-%m-%d")
        end   = (datetime.strptime(entry_date, "%Y-%m-%d")
                 + timedelta(days=int(self.config.hold_days * 1.5))
                 ).strftime("%Y-%m-%d")

        df = self.loader.get_price(stock_id, entry_date, end)
        if df.empty:
            return 0.0, "", "no_data"

        df["date"]  = pd.to_datetime(df["date"])
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df["open"]  = pd.to_numeric(df["open"],  errors="coerce")
        df          = df.sort_values("date")

        # 取進場價（entry_date 隔日開盤）
        after_entry = df[df["date"] > pd.Timestamp(entry_date)]
        if after_entry.empty:
            return 0.0, "", "no_data"
        entry_price = float(after_entry.iloc[0]["open"])
        if entry_price <= 0:
            return 0.0, "", "no_data"

        hold_count = 0
        for _, row in after_entry.iterrows():
            close = float(row["close"])
            chg   = (close - entry_price) / entry_price

            # 停損
            if chg <= self.config.stop_loss_pct:
                return close, str(row["date"].date()), "stop_loss"

            # 停利
            if chg >= self.config.take_profit_pct:
                return close, str(row["date"].date()), "take_profit"

            hold_count += 1
            if hold_count >= self.config.hold_days:
                return close, str(row["date"].date()), "hold"

        # 沒走完就到回測結束
        last = after_entry.iloc[-1]
        return float(last["close"]), str(last["date"].date()), "end"

    def run(self, scan_frequency: str = "weekly") -> BacktestStats:
        """
        執行完整回測。

        scan_frequency：
          "weekly"  → 每週掃描一次（快，適合初次驗證）
          "monthly" → 每月掃描一次（更快，適合長期趨勢）
          "daily"   → 每日掃描（慢，最精確）
        """
        logger.info("="*55)
        logger.info(f"回測開始｜{self.config.start_date} ~ {self.config.end_date}")
        logger.info(f"股票池：{len(self.stock_ids)} 支｜"
                    f"持有：{self.config.hold_days} 日｜"
                    f"閾值：{self.config.score_threshold}")
        logger.info("="*55)

        # 產生掃描日期
        start_dt = datetime.strptime(self.config.start_date, "%Y-%m-%d")
        end_dt   = datetime.strptime(self.config.end_date,   "%Y-%m-%d")
        scan_dates = []
        cur = start_dt
        step = {"weekly": 7, "monthly": 30, "daily": 1}[scan_frequency]
        while cur <= end_dt:
            if cur.weekday() < 5:
                scan_dates.append(cur.strftime("%Y-%m-%d"))
            cur += timedelta(days=step)

        logger.info(f"掃描日期：{len(scan_dates)} 個（{scan_frequency}）")

        open_positions: set[str] = set()  # 目前持倉中的股票
        all_trades: list[Trade] = []

        for i, eval_date in enumerate(scan_dates):
            if i % 4 == 0:
                logger.info(f"  [{eval_date}] 進度 {i}/{len(scan_dates)}｜"
                            f"累計交易 {len(all_trades)} 筆")

            candidates = []

            # 對每支股票評分
            for stock_id in self.stock_ids:
                # 已持有則跳過
                if stock_id in open_positions:
                    continue
                if len(open_positions) >= self.config.max_positions:
                    break

                result = score_on_date(
                    stock_id=stock_id,
                    eval_date=eval_date,
                    loader=self.loader,
                )
                if result is None:
                    continue
                if result["total_score"] >= self.config.score_threshold:
                    candidates.append(result)

            # 排序，取前 N 支
            candidates.sort(key=lambda x: x["total_score"], reverse=True)
            slots = self.config.max_positions - len(open_positions)
            to_enter = candidates[:slots]

            for result in to_enter:
                sid   = result["stock_id"]
                name  = self.stock_names.get(sid, sid)
                score = result["total_score"]
                tags  = result.get("tags", [])

                # 取進場價（評分日後第一個交易日開盤）
                entry_price = self._get_next_open_price(sid, eval_date)
                if not entry_price or entry_price <= 0:
                    continue

                # 計算股數
                shares = int(self.config.capital_per_trade / entry_price / 1000) * 1000
                if shares <= 0:
                    continue

                # 取出場資訊
                exit_price, exit_date, exit_reason = self._get_exit_price(
                    sid, eval_date
                )

                entry_date_actual = (
                    datetime.strptime(eval_date, "%Y-%m-%d") + timedelta(days=1)
                ).strftime("%Y-%m-%d")

                trade = Trade(
                    stock_id=sid,
                    stock_name=name,
                    entry_date=entry_date_actual,
                    entry_price=entry_price,
                    exit_date=exit_date,
                    exit_price=exit_price,
                    exit_reason=exit_reason,
                    score=score,
                    score_pct=result["pct"],
                    tags=tags,
                    shares=shares,
                )
                all_trades.append(trade)

                if exit_date <= eval_date or exit_reason == "end":
                    pass
                else:
                    open_positions.add(sid)

            # 移除已出場的持倉
            completed_ids = {
                t.stock_id for t in all_trades
                if t.exit_date and t.exit_date <= eval_date
            }
            open_positions -= completed_ids

        self.trades = all_trades
        logger.info(f"回測完成！共 {len(all_trades)} 筆交易")

        # 計算基準報酬（0050）
        benchmark_return = self._calc_benchmark_return()

        stats = calc_stats(all_trades, benchmark_return)
        return stats

    def _calc_benchmark_return(self) -> float:
        """計算基準指數（0050）在回測期間的總報酬率"""
        try:
            df = self.loader.get_price(
                self.config.benchmark,
                self.config.start_date,
                self.config.end_date
            )
            if df.empty:
                return 0.0
            df["close"] = pd.to_numeric(df["close"], errors="coerce")
            df = df.dropna(subset=["close"]).sort_values("date")
            start_p = float(df.iloc[0]["close"])
            end_p   = float(df.iloc[-1]["close"])
            return (end_p - start_p) / start_p
        except Exception:
            return 0.0

    def save_trades_csv(self, filepath: str = "output/backtest_trades.csv"):
        """儲存所有交易記錄到 CSV"""
        if not self.trades:
            return
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        rows = [t.to_dict() for t in self.trades]
        pd.DataFrame(rows).to_csv(filepath, encoding="utf-8-sig", index=False)
        logger.info(f"交易記錄已儲存：{filepath}")
