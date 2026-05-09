#!/usr/bin/env python3
# scanner.py
# ============================================================
#  台股主力選股掃描器｜主程式入口（完整版 Sprint 1+2+3）
#
#  評分面向：
#    A. 外資動向(20) + B. 投信認養(20) + C. 主力分點(20)
#    D. 技術面(20) + E. 基本面(20)
#    + 股權分散(10) + 融資券(8) = 滿分 118
#  候選門檻：77 分（65%）
#
#  使用方式：
#    python scanner.py                    # 掃描今日
#    python scanner.py --date 2025-04-24  # 掃描指定日期
#    python scanner.py --stock 2454       # 單股深度分析
#    python scanner.py --schedule         # 每日自動排程
#    python scanner.py --quick            # 快速模式（前100支）
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
    SCHEDULE_TIME, CANDIDATE_THRESHOLD,
    INSTITUTIONAL_LOOKBACK, LOOKBACK_DAYS
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
from engine.scorer import score_stock
from engine.etf_flow import calc_trust_5d_distribution
from engine.trust_radar import compute_trust_radar
from engine.foreign_radar import compute_foreign_radar, compute_trust_io
from engine.broker_analysis import compute_top3_brokers
from engine.breakout_radar import detect_breakout, compute_mainforce_today
from engine.margin_radar import compute_margin_radar, compute_short_radar
from engine.backtest_radar import (
    compute_historical_rankings, compute_opens_history,
)
from engine.filters import (
    filter_stock_list,
    filter_by_margin,
    quick_institutional_check,
    phase1_filter,
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


# ── 單支股票完整分析 ──────────────────────────────────────────

def analyze_single_stock(stock_id: str,
                          stock_name: str = "",
                          scan_date: str = None) -> dict:
    """
    對單支股票執行完整的資料拉取 + 評分流程（Sprint 2 更新版）。
    """
    logger.info(f"開始分析 {stock_id} {stock_name}")

    # Sprint 1 資料
    price_df         = fetch_stock_price(stock_id, days_back=LOOKBACK_DAYS)
    institutional_df = fetch_institutional(stock_id, days_back=INSTITUTIONAL_LOOKBACK)
    shareholding_df  = fetch_shareholding(stock_id)
    revenue_df       = fetch_month_revenue(stock_id)
    financial_df     = fetch_financial_statements(stock_id)

    # Sprint 2 新增資料
    from data.fetcher import fetch_holding_distribution, fetch_margin
    holding_df      = fetch_holding_distribution(stock_id, days_back=90)
    margin_hist_df  = fetch_margin(stock_id, days_back=30)

    result = score_stock(
        stock_id=stock_id,
        price_df=price_df,
        institutional_df=institutional_df,
        revenue_df=revenue_df,
        financial_df=financial_df,
        shareholding_df=shareholding_df,
        holding_df=holding_df,
        margin_history_df=margin_hist_df,
    )
    result["stock_name"] = stock_name
    return result


def print_single_stock_detail(result: dict):
    """印出單支股票的詳細評分報告（Sprint 2 更新版）。"""
    sid   = result["stock_id"]
    name  = result.get("stock_name", "")
    total = result["total_score"]
    pct   = result["pct"]

    print(f"\n{'='*60}")
    print(f"  {sid} {name}｜總分 {total}/{result.get('max_score', 158)}（{pct*100:.1f}%）")
    print(f"{'='*60}")

    sections = [
        ("A. 外資動向  ", result["A_foreign"]),
        ("B. 投信認養  ", result["B_trust"]),
        ("C. 主力分點  ", result["C_broker"]),
        ("D. 技術面    ", result["D_technical"]),
        ("E. 基本面    ", result["E_fundamental"]),
        ("F. ETF資金流", result.get("F_etf_flow", {"score": 0, "detail": {}})),
        ("S2. 股權分散 ", result.get("S2_holding", {"score": 0, "detail": {}})),
        ("S2. 融資券   ", result.get("S2_margin",  {"score": 0, "detail": {}})),
    ]
    maxs = [20, 20, 20, 20, 20, 20, 25, 13]
    for (label, sec), mx in zip(sections, maxs):
        score     = sec["score"]
        breakdown = sec.get("detail", {}).get("breakdown", {})
        bd_str    = "  ".join([f"{k}:{v}" for k, v in breakdown.items()])
        bar_filled = int(score / mx * 10) if mx > 0 else 0
        bar = "█" * bar_filled + "░" * (10 - bar_filled)
        print(f"  {label}：{score:>3}/{mx}  [{bar}]  {bd_str}")

    tags = result.get("tags", [])
    if tags:
        print(f"\n  🏷  標記：{' '.join(tags)}")

    # 關鍵數值
    a_d  = result["A_foreign"]["detail"]
    b_d  = result["B_trust"]["detail"]
    d_d  = result["D_technical"]["detail"]
    e_d  = result["E_fundamental"]["detail"]
    ma   = result.get("margin_analysis", {})
    s2h  = result.get("S2_holding", {}).get("detail", {}).get("trend", {})

    print(f"\n  📊 法人：外資連買 {a_d.get('consec_days',0)}天  "
          f"投信連買 {b_d.get('consec_days',0)}天  "
          f"投信累積 {b_d.get('cumulative_diff',0):+,}張")
    print(f"  📈 技術：RSI {d_d.get('rsi','N/A'):.1f}  "
          f"量比 {d_d.get('volume_ratio',0):.1f}x  "
          f"量能突破：{'是' if d_d.get('breakdown',{}).get('量能突破') else '否'}")
    print(f"  💰 基本：月營收連成長 {e_d.get('revenue_growth_months',0)}月  "
          f"EPS成長率 {e_d.get('eps_avg_growth',0)*100:.1f}%")
    print(f"  🏦 大戶：大戶+超大戶 {s2h.get('big_total_pct',0):.1f}%  "
          f"4週變化 {s2h.get('big_chg_4w',0):+.1f}%  "
          f"籌碼集中：{'是' if s2h.get('concentration') else '否'}")
    print(f"  📉 融資：融資率 {ma.get('margin_ratio',0)*100:.1f}%  "
          f"{'↓下降中' if ma.get('margin_declining') else '─持平'}  "
          f"融券率 {ma.get('short_ratio',0)*100:.1f}%  "
          f"{'⚡軋空潛力' if ma.get('short_squeeze_potential') else ''}")

    if result.get("error"):
        print(f"\n  ⚠️  分析錯誤：{result['error']}")

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
    logger.info("Step 1/5：取得股票清單...")
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
    logger.info("Step 2/5：批次拉取全市場資料...")
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

    # full mode：分點資料在候選股確定後才逐支補抓（Step 3.5）

    # F1 需要的全市場投信5日買超分布（強制觸發批次法人歷史拉取）
    inst_hist_all = cache.get_institutional_history(20)
    market_trust_dist = calc_trust_5d_distribution(inst_hist_all)
    # 融資券雷達需要 ≥ 21 日歷史（融券 21 日視窗）
    cache.get_margin_history(22)
    if market_trust_dist:
        logger.info(f"投信5日買超分布｜p90={market_trust_dist['p90']:.0f}張 "
                    f"p80={market_trust_dist['p80']:.0f}張 "
                    f"p70={market_trust_dist['p70']:.0f}張 "
                    f"(N={market_trust_dist['n']})")
    else:
        logger.warning("無法計算投信買超分布，F1 將為 0 分")

    # ── Step 3：逐支評分 ──────────────────────────────────────
    logger.info("Step 3/5：開始評分...")
    results = []
    skip_count = error_count = 0

    for i, (_, stock_row) in enumerate(valid_stocks.iterrows()):
        stock_id   = str(stock_row.get("stock_id", ""))
        stock_name = str(stock_row.get("stock_name", ""))

        if i % 50 == 0:
            elapsed = time.time() - start_time
            n_cand  = sum(1 for r in results
                         if r["total_score"] >= CANDIDATE_THRESHOLD)
            logger.info(f"  {i}/{total}（{i/total*100:.0f}%）｜"
                        f"{elapsed:.0f}s｜候選 {n_cand} 支")

        try:
            # 不再做任何預篩，所有股票都評分
            today_inst = cache.institutional_for(stock_id)
            today_mg   = cache.margin_for(stock_id)
            # 仍計算 margin_ratio 顯示用，但不做 pass/fail 過濾
            _, margin_ratio = filter_by_margin(stock_id, today_mg)

            # 個股歷史資料（從批次 cache，必要時自動 fallback 到個別呼叫）
            price_df    = cache.price_history_for(stock_id)
            if price_df.empty:
                skip_count += 1
                continue

            # 全部從批次 cache 撈，零個股 API 呼叫
            inst_hist   = cache.institutional_history_for(stock_id)
            share_df    = cache.shareholding_history_for(stock_id)
            margin_hist = cache.margin_history_for(stock_id)
            holding_df  = cache.holding_distribution_for(stock_id)
            revenue_df  = cache.revenue_history_for(stock_id)
            fin_df      = cache.financial_for(stock_id)

            broker_df = pd.DataFrame()  # full mode 在 Step 3.5 補抓

            # 完整評分
            result = score_stock(
                stock_id=stock_id,
                price_df=price_df,
                institutional_df=inst_hist,
                revenue_df=revenue_df,
                financial_df=fin_df,
                shareholding_df=share_df,
                holding_df=holding_df,
                margin_history_df=margin_hist,
                broker_df=broker_df,
                market_trust_dist=market_trust_dist,
            )
            result["stock_name"]   = stock_name
            result["margin_ratio"] = margin_ratio
            # 投信雷達指標（金額 / 連買 / 箱型突破，前端排行用）
            result["trust_radar"]  = compute_trust_radar(inst_hist, price_df)
            # 外資雷達指標（1/3/5/10/20 日累計買賣超張數與金額）
            result["foreign_radar"] = compute_foreign_radar(inst_hist, price_df)
            # 投信多視窗（同結構，篩投信列）
            result["trust_io"]      = compute_trust_io(inst_hist, price_df)
            # 突破雷達（5 日箱型 + 突破 ±5% + 5 倍爆量）
            result["breakout"]      = detect_breakout(price_df)
            # 融資 / 融券多視窗餘額金額 + 增減
            result["margin_radar"]  = compute_margin_radar(margin_hist, price_df)
            result["short_radar"]   = compute_short_radar(margin_hist, price_df)
            # 過去 21 日開盤價（回測 tab 用）
            result["opens_history"] = compute_opens_history(price_df, days=21)
            # 最新收盤 / 漲跌（讓前端卡片顯示用）
            if not price_df.empty:
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

    # ── Step 3.5/3.6 共用：broker 多日資料 in-memory cache ────
    sprint3_threshold = CANDIDATE_THRESHOLD
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
        trading_dates = fetch_trading_dates(days_back=30)
        recent_dates  = sorted(trading_dates)[-10:] if trading_dates else []

    # ── Step 3.5：full mode — 對候選股補抓分點資料並重算 C 分 ────
    if use_broker:
        pre_candidates = [r for r in results
                          if r["total_score"] >= sprint3_threshold]
        logger.info(f"Step 3.5：補抓分點資料（{len(pre_candidates)} 支候選股）...")

        for r in pre_candidates:
            sid = r["stock_id"]
            broker_df = _fetch_broker_multi(sid)
            if broker_df.empty:
                continue
            from engine.scorer import score_broker
            c_result = score_broker(broker_df, r.get("_price_df", pd.DataFrame()))
            old_c = r["C_broker"]["score"]
            r["C_broker"] = c_result
            r["total_score"] = r["total_score"] - old_c + c_result["score"]
            r["pct"] = round(r["total_score"] / r["max_score"], 4)

        logger.info("分點補抓完成")

    # ── Step 3.6：對外資 20 日 |淨額| 前 30 名抓 broker top3 分點 ──
    top30_foreign = sorted(
        [r for r in results if r.get("foreign_radar")],
        key=lambda r: abs(r["foreign_radar"].get("20", {}).get("net_amount_m", 0)),
        reverse=True,
    )[:30]

    if use_broker and top30_foreign:
        logger.info(f"Step 3.6：對外資 20 日金額前 {len(top30_foreign)} 名"
                    f"算 broker 雙向 top3（當日）...")
        for r in top30_foreign:
            sid = r["stock_id"]
            broker_df = _fetch_broker_multi(sid)
            if broker_df.empty:
                r["broker_top3"] = {"buy": [], "sell": []}
                r["broker_dir"]  = ""
                continue
            price_df_r = cache.price_history_for(sid)
            # 取當日 broker（依使用者需求改為當日）
            broker_today = broker_df.copy()
            if "date" in broker_today.columns:
                broker_today["date"] = broker_today["date"].astype(str).str[:10]
                df_today = broker_today[broker_today["date"] == scan_date]
                if not df_today.empty:
                    broker_today = df_today
            # 雙向 top3：前端依使用者選擇的方向 chip 切換顯示
            r["broker_top3"] = {
                "buy":  compute_top3_brokers(broker_today, price_df_r, "buy"),
                "sell": compute_top3_brokers(broker_today, price_df_r, "sell"),
            }
            r["broker_dir"]  = ""   # 保留欄位以兼容舊版前端
        logger.info("外資前 30 名分點 top3 完成")

    # ── Step 3.7：對投信 20 日 |淨額| 前 30 名抓 broker top3 分點 ──
    top30_trust = sorted(
        [r for r in results if r.get("trust_io")],
        key=lambda r: abs(r["trust_io"].get("20", {}).get("net_amount_m", 0)),
        reverse=True,
    )[:30]

    if use_broker and top30_trust:
        logger.info(f"Step 3.7：對投信 20 日金額前 {len(top30_trust)} 名"
                    f"算 broker 雙向 top3（當日）...")
        for r in top30_trust:
            sid = r["stock_id"]
            broker_df = _fetch_broker_multi(sid)   # 與 3.5/3.6 共用 cache
            if broker_df.empty:
                r["trust_broker_top3"] = {"buy": [], "sell": []}
                r["trust_broker_dir"]  = ""
                continue
            price_df_r = cache.price_history_for(sid)
            broker_today = broker_df.copy()
            if "date" in broker_today.columns:
                broker_today["date"] = broker_today["date"].astype(str).str[:10]
                df_today = broker_today[broker_today["date"] == scan_date]
                if not df_today.empty:
                    broker_today = df_today
            r["trust_broker_top3"] = {
                "buy":  compute_top3_brokers(broker_today, price_df_r, "buy"),
                "sell": compute_top3_brokers(broker_today, price_df_r, "sell"),
            }
            r["trust_broker_dir"]  = ""
        logger.info("投信前 30 名分點 top3 完成")

    # ── Step 3.8：對「箱型突破 + 爆量」個股算當日 broker 彙總 ──
    breakout_stocks = [
        r for r in results
        if r.get("breakout", {}).get("qualified_up")
        or r.get("breakout", {}).get("qualified_down")
    ]
    if use_broker and breakout_stocks:
        logger.info(f"Step 3.8：對箱型突破+爆量 {len(breakout_stocks)} 支"
                    f"算當日 broker 彙總（主力分點 tab 用）...")
        for r in breakout_stocks:
            sid = r["stock_id"]
            # 拉「scan_date」當日的 broker 資料
            df_b = fetch_broker_data(sid, scan_date)
            if df_b.empty:
                # fallback：若當日無資料（盤中未更新），用 cache 中最近一日
                cached_multi = broker_cache_run.get(sid, pd.DataFrame())
                if not cached_multi.empty:
                    cached_multi = cached_multi.copy()
                    cached_multi["date"] = cached_multi["date"].astype(str).str[:10]
                    last_day = cached_multi["date"].max()
                    df_b = cached_multi[cached_multi["date"] == last_day]
            if df_b.empty:
                r["mainforce_today"] = {}
                continue
            price_df_r = cache.price_history_for(sid)
            r["mainforce_today"] = compute_mainforce_today(df_b, price_df_r)
        logger.info("主力分點當日彙總完成")

    # ── Step 4：篩選候選 ──────────────────────────────────────
    logger.info("Step 4/5：篩選候選名單...")
    candidates = [r for r in results
                  if r["total_score"] >= sprint3_threshold]
    logger.info(f"評分 {len(results)} 支｜跳過 {skip_count}｜"
                f"錯誤 {error_count}｜候選 {len(candidates)} 支")

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

    # ── Step 5：輸出報告 ──────────────────────────────────────
    logger.info("Step 5/5：產生報告...")
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
