#!/usr/bin/env python3
# scanner.py
# ============================================================
#  台股主力選股掃描器｜主程式入口
#
#  本版（無評分系統）對每支股票只計算「面向 radar」原始資料：
#    - foreign_radar：外資 1/3/5/10/20 日累計買賣超
#    - trust_io     ：投信 1/3/5/10/20 日累計買賣超
#    - margin/short ：融資 / 融券多視窗餘額金額
#    - breakout     ：箱型整理 + 突破 + 爆量旗標
#    - mainforce    ：當日分點彙總（突破股）
#    - trust_radar  ：投信當日金額 + 連買 + 箱型
#    - opens_history：過去 21 日開盤價（回測用）
#
#  排行榜各 tab 直接依 radar 資料排序篩選，沒有「總分」概念。
#
#  使用方式：
#    python scanner.py                    # 掃描今日
#    python scanner.py --date 2025-04-24  # 掃描指定日期
#    python scanner.py --stock 2454       # 單股深度分析
#    python scanner.py --schedule         # 每日自動排程
#    python scanner.py --quick            # 快速模式（前 100 支）
#    python scanner.py --no-broker        # 跳過分點資料（加速）
# ============================================================

import argparse
import logging
import time
from datetime import datetime, timedelta

try:
    import schedule
    HAS_SCHEDULE = True
except ImportError:
    HAS_SCHEDULE = False

import pandas as pd

from config.settings import (
    SCHEDULE_TIME, INSTITUTIONAL_LOOKBACK, LOOKBACK_DAYS
)
from data.fetcher import (
    fetch_stock_list,
    fetch_stock_price,
    fetch_institutional,
    fetch_month_revenue,
    fetch_financial_statements,
    fetch_shareholding,
    fetch_holding_distribution,
    fetch_margin,
    fetch_broker_data,
    DailyDataCache,

    fetch_trading_dates,
    get_last_trading_date,
)
from engine.trust_radar import compute_trust_radar
from engine.foreign_radar import (
    compute_foreign_radar, compute_trust_io, compute_foreign_consec_days,
)
from engine.broker_analysis import compute_top3_brokers, compute_mainforce_consec
from engine.breakout_radar import detect_breakout, compute_mainforce_today
from engine.margin_radar import compute_margin_radar, compute_short_radar
from engine.backtest_radar import (
    compute_historical_rankings, compute_opens_history,
)
from engine.filters import (
    filter_stock_list,
    filter_by_margin,
)
from output.reporter import generate_report


def resolve_scan_date() -> str:
    """
    利用 FinMind TaiwanStockTradingDate 精確判斷最近交易日。
    涵蓋週末、國定假日、連假等所有休市情況。
    若 API 無法取得交易日清單，自動 fallback 到週一到週五推算。
    """
    logger.info("查詢 FinMind 交易日清單...")
    trading_dates = fetch_trading_dates(days_back=60)
    date = get_last_trading_date(trading_dates)
    logger.info(f"確定掃描日期：{date}")
    return date

# ── 日誌設定 ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/scanner.log", encoding="utf-8"),
    ]
)
logger = logging.getLogger(__name__)


# ── 單支股票分析（純 radar 資料）──────────────────────────────

def analyze_single_stock(stock_id: str,
                          stock_name: str = "",
                          scan_date: str = None) -> dict:
    """
    對單支股票拉資料、計算各 radar 指標。不再有總分概念。
    """
    logger.info(f"開始分析 {stock_id} {stock_name}")

    price_df         = fetch_stock_price(stock_id, days_back=LOOKBACK_DAYS)
    institutional_df = fetch_institutional(stock_id, days_back=INSTITUTIONAL_LOOKBACK)
    margin_hist_df   = fetch_margin(stock_id, days_back=30)

    return {
        "stock_id":      stock_id,
        "stock_name":    stock_name,
        "trust_radar":   compute_trust_radar(institutional_df, price_df),
        "foreign_radar": compute_foreign_radar(institutional_df, price_df),
        "trust_io":      compute_trust_io(institutional_df, price_df),
        "breakout":      detect_breakout(price_df),
        "margin_radar":  compute_margin_radar(margin_hist_df, price_df),
        "short_radar":   compute_short_radar(margin_hist_df, price_df),
        "opens_history": compute_opens_history(price_df, days=21),
    }


def print_single_stock_detail(result: dict):
    """印出單支股票的 radar 摘要（無總分系統）。"""
    sid   = result["stock_id"]
    name  = result.get("stock_name", "")
    fr    = result.get("foreign_radar", {}) or {}
    ti    = result.get("trust_io", {}) or {}
    bo    = result.get("breakout", {}) or {}
    mr    = result.get("margin_radar", {}) or {}
    sr    = result.get("short_radar", {}) or {}
    tr    = result.get("trust_radar", {}) or {}

    def _amt(m):
        return f"{m:+,.1f}M" if abs(m) < 100 else f"{m/100:+,.2f}億"

    print(f"\n{'='*60}")
    print(f"  {sid} {name}")
    print(f"{'='*60}")

    # 外資 / 投信 5 視窗
    print("\n  外資進出（淨額金額，百萬元）")
    for w in ["1", "3", "5", "10", "20"]:
        v = (fr.get(w) or {}).get("net_amount_m", 0)
        print(f"    {w:>2} 日：{_amt(v)}")
    print("\n  投信進出（淨額金額，百萬元）")
    for w in ["1", "3", "5", "10", "20"]:
        v = (ti.get(w) or {}).get("net_amount_m", 0)
        print(f"    {w:>2} 日：{_amt(v)}")

    # 主力分點
    if bo.get("qualified_up") or bo.get("qualified_down"):
        d = "↗ 向上突破" if bo.get("qualified_up") else "↘ 向下突破"
        print(f"\n  🎯 主力分點：{d}（量比 {bo.get('vol_ratio',0):.1f}x）")

    # 投信雷達
    if tr.get("trust_amount_m"):
        bbox = "🚀 突破" if tr.get("box_breakout") else "📦 整理" if tr.get("is_box") else ""
        print(f"\n  💧 投信雷達：當日 {tr.get('trust_net_lots',0):+,} 張 / "
              f"{_amt(tr.get('trust_amount_m',0))}　{bbox}")

    # 融資券
    print(f"\n  ⚡ 融資餘額：{_amt(mr.get('latest_amt_m',0))}　"
          f"融券餘額：{_amt(sr.get('latest_amt_m',0))}")

    print(f"{'='*60}\n")


# ── 全市場掃描 ────────────────────────────────────────────────

def run_scan(scan_date: str = None, quick: bool = False,
             use_broker: bool = False):
    """
    執行全市場選股掃描（完整版 Sprint 1+2+3）。

    scan_date  ：YYYY-MM-DD，None 代表今日
    quick      ：True 只掃前 100 支（測試用）
    use_broker ：True 啟用分點資料（需 Sponsor，較慢）
    """
    if not scan_date:
        scan_date = resolve_scan_date()

    start_time = time.time()
    logger.info(f"{'='*55}")
    logger.info(f"掃描開始｜日期：{scan_date}｜"
                f"快速：{quick}｜分點：{use_broker}")

    # ── Step 1：股票清單 ──────────────────────────────────────
    logger.info("Step 1/4：取得股票清單...")
    stock_list_df = fetch_stock_list()
    if stock_list_df.empty:
        logger.error("無法取得股票清單，掃描中止")
        return

    valid_stocks = filter_stock_list(stock_list_df)
    if quick:
        valid_stocks = valid_stocks.head(100)
    total = len(valid_stocks)
    logger.info(f"有效股票：{total} 支")

    # ── Step 2：批次拉取全市場當日資料 ───────────────────────
    logger.info("Step 2/4：批次拉取全市場資料...")
    cache = DailyDataCache(scan_date)
    all_institutional = cache.get_institutional()
    all_margin_today  = cache.get_margin()
    logger.info(f"法人：{len(all_institutional)} 筆｜"
                f"融資券：{len(all_margin_today)} 筆")

    # 若當日無資料（FinMind 資料延遲），往前多找一天
    if all_institutional.empty:
        logger.info("當日法人資料為空，往前查前一交易日...")
        trading_dates = fetch_trading_dates(days_back=60)
        # 從 scan_date 前一天開始往前找
        check = datetime.strptime(scan_date, "%Y-%m-%d") - timedelta(days=1)
        for _ in range(10):
            candidate = check.strftime("%Y-%m-%d")
            if not trading_dates or candidate in trading_dates:
                cache2 = DailyDataCache(candidate)
                df2 = cache2.get_institutional()
                if not df2.empty:
                    scan_date = candidate
                    cache = cache2
                    all_institutional = df2
                    all_margin_today  = cache.get_margin()
                    logger.info(f"改用日期 {scan_date}｜"
                                f"法人：{len(all_institutional)} 筆｜"
                                f"融資券：{len(all_margin_today)} 筆")
                    break
            check -= timedelta(days=1)

    # 強制觸發批次法人歷史 + 22 日融資券歷史拉取
    cache.get_institutional_history(20)
    cache.get_margin_history(22)

    # ── Step 3：逐支股票算 radar 資料（無評分系統）─────────────
    logger.info("Step 3/4：計算各 radar 資料...")
    results = []
    skip_count = error_count = 0

    for i, (_, stock_row) in enumerate(valid_stocks.iterrows()):
        stock_id   = str(stock_row.get("stock_id", ""))
        stock_name = str(stock_row.get("stock_name", ""))
        market     = str(stock_row.get("type", "") or stock_row.get("market", ""))
        industry   = str(stock_row.get("industry_category", ""))

        if i % 100 == 0:
            elapsed = time.time() - start_time
            logger.info(f"  {i}/{total}（{i/total*100:.0f}%）｜{elapsed:.0f}s")

        try:
            today_mg = cache.margin_for(stock_id)
            _, margin_ratio = filter_by_margin(stock_id, today_mg)

            price_df = cache.price_history_for(stock_id)
            if price_df.empty:
                skip_count += 1
                continue

            inst_hist   = cache.institutional_history_for(stock_id)
            margin_hist = cache.margin_history_for(stock_id)

            tr_radar = compute_trust_radar(inst_hist, price_df)
            result = {
                "stock_id":      stock_id,
                "stock_name":    stock_name,
                "market":        market,
                "industry":      industry,
                # 連買天數（卡片 badge 用）— trust_days 取自 trust_radar，
                # foreign_days 額外用 helper 算
                "trust_days":    tr_radar.get("trust_consec_days", 0),
                "foreign_days":  compute_foreign_consec_days(inst_hist),
                "margin_ratio_pct": round(margin_ratio * 100, 1) if margin_ratio > 0 else 0,
                "trust_radar":   tr_radar,
                "foreign_radar": compute_foreign_radar(inst_hist, price_df),
                "trust_io":      compute_trust_io(inst_hist, price_df),
                "breakout":      detect_breakout(price_df),
                "margin_radar":  compute_margin_radar(margin_hist, price_df),
                "short_radar":   compute_short_radar(margin_hist, price_df),
                "opens_history": compute_opens_history(price_df, days=21),
            }
            # 最新收盤 / 漲跌
            pr_sort = price_df.copy()
            pr_sort["date"] = pd.to_datetime(pr_sort["date"])
            pr_sort = pr_sort.sort_values("date")
            closes = pd.to_numeric(pr_sort["close"], errors="coerce").dropna()
            if len(closes) >= 1:
                today_close = float(closes.iloc[-1])
                result["price"] = round(today_close, 2)
                if len(closes) >= 2:
                    prev_close = float(closes.iloc[-2])
                    if prev_close > 0:
                        result["chg"]     = round(today_close - prev_close, 2)
                        result["chg_pct"] = round((today_close - prev_close) / prev_close * 100, 2)
            results.append(result)

        except KeyboardInterrupt:
            logger.warning("使用者中止")
            break
        except Exception as e:
            error_count += 1
            logger.debug(f"  {stock_id} 失敗：{e}")

    # ── 為下一階段（broker 補抓）準備 cache 結構 ───────────────
    broker_cache_run: dict[str, pd.DataFrame] = {}
    recent_dates: list = []

    def _fetch_broker_multi(sid: str) -> pd.DataFrame:
        if sid in broker_cache_run:
            return broker_cache_run[sid]
        frames = []
        for d in recent_dates:
            df_b = fetch_broker_data(sid, d)
            if not df_b.empty:
                frames.append(df_b)
        out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        broker_cache_run[sid] = out
        return out

    if use_broker:
        trading_dates = fetch_trading_dates(days_back=40)
        # 拉 20 日 broker（給 5 視窗 top3 用：1/3/5/10/20 日）
        recent_dates  = sorted(trading_dates)[-20:] if trading_dates else []

    BROKER_TOP3_WINDOWS = [1, 3, 5, 10, 20]

    def _compute_windowed_top3(broker_df: pd.DataFrame,
                                price_df_r: pd.DataFrame) -> dict:
        """對單股 broker_df 算各視窗（1/3/5/10/20）雙向 top3"""
        empty_all = {str(n): {"buy": [], "sell": []} for n in BROKER_TOP3_WINDOWS}
        if broker_df is None or broker_df.empty:
            return empty_all
        df = broker_df.copy()
        df["date_str"] = df["date"].astype(str).str[:10]
        sorted_dates = sorted(df["date_str"].unique(), reverse=True)
        out = {}
        for n in BROKER_TOP3_WINDOWS:
            window_dates = set(sorted_dates[:n])
            df_w = df[df["date_str"].isin(window_dates)]
            if df_w.empty:
                out[str(n)] = {"buy": [], "sell": []}
                continue
            out[str(n)] = {
                "buy":  compute_top3_brokers(df_w, price_df_r, "buy"),
                "sell": compute_top3_brokers(df_w, price_df_r, "sell"),
            }
        return out

    # ── Step 3.5：對「外資 / 投信 5 視窗前 30 聯集 + 量比 > 3.5x 股票」
    #           抓 broker，算 windowed top3 + mainforce_consec
    #           （前端外資 / 投信 / 主力分點 tab 共用）
    broker_targets: set[str] = set()
    if use_broker:
        # 法人前 30（外資 / 投信 5 視窗聯集）— 給外資 / 投信 tab 用
        for w in ['1', '3', '5', '10', '20']:
            sorted_f = sorted(
                results,
                key=lambda r: abs(((r.get('foreign_radar') or {}).get(w) or {})
                                       .get('net_amount_m', 0)),
                reverse=True,
            )[:30]
            broker_targets.update(r['stock_id'] for r in sorted_f)
            sorted_t = sorted(
                results,
                key=lambda r: abs(((r.get('trust_io') or {}).get(w) or {})
                                       .get('net_amount_m', 0)),
                reverse=True,
            )[:30]
            broker_targets.update(r['stock_id'] for r in sorted_t)

        # 量比 > 3.5x 的成交量爆增股 — 給主力分點 tab 用
        # （涵蓋「法人沒進場、本土分點吃貨」的潛在主力股）
        VOL_RATIO_THRESHOLD = 3.5
        n_legal = len(broker_targets)
        for r in results:
            bo = r.get('breakout') or {}
            if (bo.get('vol_ratio') or 0) > VOL_RATIO_THRESHOLD:
                broker_targets.add(r['stock_id'])
        n_vol = len(broker_targets) - n_legal

        logger.info(f"Step 3.5：對 {len(broker_targets)} 支股票抓 broker"
                    f"（法人前 30 聯集 {n_legal} + 量比 > {VOL_RATIO_THRESHOLD}x"
                    f" 新增 {n_vol}）...")
        empty_mfc = {"buy":  {"trader_name": "", "consec_days": 0, "net_lots": 0,
                                  "net_amount_m": 0.0, "is_qualified": False},
                     "sell": {"trader_name": "", "consec_days": 0, "net_lots": 0,
                                  "net_amount_m": 0.0, "is_qualified": False}}
        for r in results:
            sid = r["stock_id"]
            if sid not in broker_targets:
                r["broker_top3"]        = {str(n): {"buy": [], "sell": []}
                                              for n in BROKER_TOP3_WINDOWS}
                r["mainforce_today"]    = {}
                r["mainforce_consec"]   = empty_mfc
                continue
            broker_df = _fetch_broker_multi(sid)
            price_df_r = cache.price_history_for(sid)
            r["broker_top3"] = _compute_windowed_top3(broker_df, price_df_r)
            # 主力分點 tab：每股算 5 日連續性指標
            r["mainforce_consec"] = compute_mainforce_consec(broker_df, price_df_r, days=5)
            # 突破股額外算當日彙總（保留：個股詳情頁可能用、加分提示用）
            bo = r.get("breakout") or {}
            if bo.get("qualified_up") or bo.get("qualified_down"):
                broker_today_df = pd.DataFrame()
                if not broker_df.empty and "date" in broker_df.columns:
                    bd = broker_df.copy()
                    bd["date_str"] = bd["date"].astype(str).str[:10]
                    df_today = bd[bd["date_str"] == scan_date]
                    if not df_today.empty:
                        broker_today_df = df_today
                    else:
                        last_day = bd["date_str"].max()
                        broker_today_df = bd[bd["date_str"] == last_day]
                r["mainforce_today"] = compute_mainforce_today(
                    broker_today_df, price_df_r) if not broker_today_df.empty else {}
            else:
                r["mainforce_today"] = {}
        logger.info(f"broker 補抓完成，cache 命中 {len(broker_cache_run)} 支")
    else:
        # --no-broker 模式：所有股 broker 欄位都空
        empty_mfc_nob = {"buy":  {"trader_name": "", "consec_days": 0, "net_lots": 0,
                                       "net_amount_m": 0.0, "is_qualified": False},
                         "sell": {"trader_name": "", "consec_days": 0, "net_lots": 0,
                                       "net_amount_m": 0.0, "is_qualified": False}}
        for r in results:
            r["broker_top3"]      = {str(n): {"buy": [], "sell": []}
                                        for n in BROKER_TOP3_WINDOWS}
            r["mainforce_today"]  = {}
            r["mainforce_consec"] = empty_mfc_nob

    logger.info(f"處理 {len(results)} 支｜跳過 {skip_count}｜錯誤 {error_count}")

    # ── 各面向實際最後資料日（前端 chip 顯示用）─────────────
    def _max_date(df, col="date"):
        if df is None or df.empty or col not in df.columns:
            return ""
        try:
            d = pd.to_datetime(df[col], errors="coerce").max()
            return d.strftime("%Y-%m-%d") if pd.notna(d) else ""
        except Exception:
            return ""

    # 從 cache 已批次拉好的 dataset 取最大日期
    inst_max    = _max_date(cache.get_institutional_history())
    margin_max  = _max_date(cache.get_margin_history())
    price_max   = _max_date(cache.get_price_history())
    holding_max = _max_date(cache.get_holding_distribution_history())
    revenue_max = _max_date(cache.get_revenue_history())
    # 主力分點：Step 3.5/3.6/3.7/3.8 抓的 broker 涵蓋 recent_dates
    broker_max  = recent_dates[-1] if recent_dates else scan_date

    dataset_dates = {
        "A": inst_max    or scan_date,   # 外資（同法人 dataset）
        "B": inst_max    or scan_date,   # 投信
        "C": broker_max  or scan_date,   # 主力分點
        "D": price_max   or scan_date,   # 技術面
        "E": revenue_max or "",          # 月營收（每月 10 日公布）
        "H": holding_max or "",          # 股權分散（每週公布）
        "M": margin_max  or scan_date,   # 融資券
    }
    logger.info(f"資料日期：A/B={dataset_dates['A']} C={dataset_dates['C']} "
                f"D={dataset_dates['D']} E={dataset_dates['E']} "
                f"H={dataset_dates['H']} M={dataset_dates['M']}")

    # ── 回測歷史榜單（對 N=1/3/5/10/20 重算 T-N 當時各榜）─────
    logger.info("計算歷史榜單（回測 tab 用）...")
    valid_stock_ids_for_backtest = [r["stock_id"] for r in results
                                       if r.get("opens_history")]
    historical_data = compute_historical_rankings(
        cache, valid_stock_ids_for_backtest,
    )

    # ── Step 4：輸出報告 ──────────────────────────────────────
    logger.info("Step 4/4：產生報告...")
    elapsed = time.time() - start_time
    summary_df = generate_report(
        results=results,
        scan_date=scan_date,
        total_scanned=total,
        elapsed=elapsed,
        dataset_dates=dataset_dates,
        historical_data=historical_data,
    )
    return summary_df


# ── CLI 入口 ──────────────────────────────────────────────────

def main():
    import os
    os.makedirs("logs", exist_ok=True)

    parser = argparse.ArgumentParser(
        description="台股主力選股掃描器（完整版 Sprint 1+2+3）"
    )
    parser.add_argument("--date",      type=str,  default=None,
                        help="掃描日期 YYYY-MM-DD（預設今日）")
    parser.add_argument("--stock",     type=str,  default=None,
                        help="單股深度分析（如：2454）")
    parser.add_argument("--schedule",  action="store_true",
                        help="自動排程模式，每日固定時間執行")
    parser.add_argument("--quick",     action="store_true",
                        help="快速模式（只掃前 100 支）")
    parser.add_argument("--no-broker", action="store_true",
                        help="跳過分點資料（加快速度，C面向得0分）")
    args = parser.parse_args()

    # 單股分析
    if args.stock:
        logger.info(f"單股分析模式：{args.stock}")
        result = analyze_single_stock(args.stock)
        print_single_stock_detail(result)
        return

    # 排程模式
    if args.schedule:
        if not HAS_SCHEDULE:
            logger.error("請先安裝 schedule：pip install schedule")
            return
        logger.info(f"排程模式，每日 {SCHEDULE_TIME} 執行")
        schedule.every().day.at(SCHEDULE_TIME).do(
            run_scan, use_broker=not args.no_broker
        )
        print(f"✅ 排程已設定，每日 {SCHEDULE_TIME} 自動執行（Ctrl+C 停止）")
        while True:
            schedule.run_pending()
            time.sleep(60)
        return

    # 一般掃描
    run_scan(
        scan_date=args.date,
        quick=args.quick,
        use_broker=not args.no_broker,
    )


if __name__ == "__main__":
    main()
