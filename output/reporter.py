# output/reporter.py
# ============================================================
#  輸出模組：終端機報表 + CSV 輸出
# ============================================================

import os
import logging
from datetime import datetime
from typing import List

import pandas as pd

from config.settings import (
    OUTPUT_DIR, OUTPUT_CSV, OUTPUT_CONSOLE,
    TOP_N_DISPLAY, CANDIDATE_THRESHOLD, STRONG_THRESHOLD
)

logger = logging.getLogger(__name__)


def build_summary_df(results: list) -> pd.DataFrame:
    """將評分結果列表轉換為摘要 DataFrame（Sprint 3 更新版）。"""
    rows = []
    for r in results:
        if r.get("error"):
            continue

        ma  = r.get("margin_analysis", {})
        s2h = r.get("S2_holding", {})
        s2m = r.get("S2_margin", {})
        holding_trend  = s2h.get("detail", {}).get("trend", {})
        broker_analysis = r["C_broker"].get("detail", {}).get("broker_analysis", {})

        rows.append({
            "代碼":       r["stock_id"],
            "名稱":       r.get("stock_name", ""),
            "總分":       r["total_score"],
            "滿分":       r["max_score"],
            "得分率":     f"{r['pct']*100:.1f}%",
            "外資(A)":    r["A_foreign"]["score"],
            "投信(B)":    r["B_trust"]["score"],
            "主力(C)":    r["C_broker"]["score"],          # Sprint 3
            "技術(D)":    r["D_technical"]["score"],
            "基本面(E)":  r["E_fundamental"]["score"],
            "股權分散":   s2h.get("score", 0),
            "融資券":     s2m.get("score", 0),
            "外資連買":   r["A_foreign"]["detail"].get("consec_days", 0),
            "投信連買":   r["B_trust"]["detail"].get("consec_days", 0),
            "主力連進":   broker_analysis.get("max_consec_days", 0),   # Sprint 3
            "分點集中":   f"{broker_analysis.get('avg_daily_concentration', 0)*100:.0f}%",  # Sprint 3
            "RSI":        r["D_technical"]["detail"].get("rsi", -1),
            "融資率":     f"{ma.get('margin_ratio', 0)*100:.1f}%",
            "融資趨勢":   "↓" if ma.get("margin_declining") else "─",
            "大戶變化":   f"{holding_trend.get('big_chg_4w', 0):+.1f}%",
            "營收成長月": r["E_fundamental"]["detail"].get("revenue_growth_months", 0),
            "標記":       " ".join(r.get("tags", [])),
        })

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df = df.sort_values("總分", ascending=False).reset_index(drop=True)
    df.index = df.index + 1
    df.index.name = "排名"
    return df


def print_console_report(df: pd.DataFrame, scan_date: str,
                          total_scanned: int, elapsed: float):
    """終端機格式化報表輸出（Sprint 3 完整版）。"""
    if not OUTPUT_CONSOLE:
        return

    # Sprint 3 滿分 118，候選門檻 65% = 77，強烈關注 80% = 95
    threshold = 77
    strong    = 95

    candidates = df[df["總分"] >= threshold]
    strong_df  = df[df["總分"] >= strong]

    sep = "=" * 105

    print(f"\n{sep}")
    print(f"  台股主力選股掃描器 Sprint 3（完整版）｜{scan_date}")
    print(f"  掃描：{total_scanned:,} 支｜耗時：{elapsed:.1f}s｜"
          f"候選（≥{threshold}）：{len(candidates)} 支｜"
          f"強烈關注（≥{strong}）：{len(strong_df)} 支")
    print(sep)

    if df.empty:
        print("  本日無候選股票")
        print(sep)
        return

    display = df.head(TOP_N_DISPLAY)

    print(f"{'排':>3} {'代碼':>6} {'名稱':<9} "
          f"{'總':>4} {'A外':>3} {'B投':>3} {'C主':>3} {'D技':>3} {'E基':>3} "
          f"{'股權':>3} {'融':>3} "
          f"{'外資':>4} {'投信':>4} {'主力':>4} {'分點':>4} "
          f"{'RSI':>5} {'融資':>5} {'大戶':>6}  標記")
    print("-" * 105)

    for rank, row in display.iterrows():
        flag = "🔥" if row["總分"] >= strong else "  "
        print(f"{flag}{rank:>2} {row['代碼']:>6} {row['名稱']:<9} "
              f"{row['總分']:>4} {row['外資(A)']:>3} {row['投信(B)']:>3} "
              f"{row['主力(C)']:>3} {row['技術(D)']:>3} {row['基本面(E)']:>3} "
              f"{row['股權分散']:>3} {row['融資券']:>3} "
              f"{int(row['外資連買']):>3}天 {int(row['投信連買']):>3}天 "
              f"{int(row['主力連進']):>3}天 {row['分點集中']:>4} "
              f"{row['RSI']:>5.1f} {row['融資率']:>5} {row['大戶變化']:>6}  "
              f"{row['標記']}")

    print(sep)
    print("  ⚠️  本系統僅供研究參考，不構成投資建議。請依個人風險承受度操作。")
    print(sep + "\n")


def save_csv(df: pd.DataFrame, scan_date: str):
    """
    輸出 CSV 檔案至 output/ 目錄。
    """
    if not OUTPUT_CSV or df.empty:
        return

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filename = f"scan_{scan_date.replace('-', '')}.csv"
    filepath = os.path.join(OUTPUT_DIR, filename)

    df.to_csv(filepath, encoding="utf-8-sig")
    logger.info(f"CSV 已儲存：{filepath}")
    return filepath


def save_detail_json(results: list, scan_date: str):
    """
    儲存完整評分明細為 JSON（供後續分析）。
    """
    import json
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filename = f"detail_{scan_date.replace('-', '')}.json"
    filepath = os.path.join(OUTPUT_DIR, filename)

    # 過濾掉無評分的股票
    valid = [r for r in results if not r.get("error") and r["total_score"] > 0]
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(valid, f, ensure_ascii=False, indent=2, default=str)
    logger.info(f"明細 JSON 已儲存：{filepath}")


def save_app_csv(results: list, scan_date: str):
    """
    輸出 App 專用 CSV（scan_app.csv）。
    欄位與前端 App 的 generateMockData() 完全對應，
    讓 App 可直接載入取代 Mock 資料。
    """
    import json

    rows = []
    for r in results:
        if r.get("error") or r["total_score"] == 0:
            continue

        ma   = r.get("margin_analysis", {})
        s2h  = r.get("S2_holding", {})
        s2m  = r.get("S2_margin", {})
        trend = s2h.get("detail", {}).get("trend", {})
        broker = r["C_broker"].get("detail", {}).get("broker_analysis", {})
        a_det  = r["A_foreign"].get("detail", {})
        b_det  = r["B_trust"].get("detail", {})
        e_det  = r["E_fundamental"].get("detail", {})

        # 市場別：根據代碼判斷（真實資料由 fetcher 帶入）
        stock_id = r["stock_id"]
        market   = r.get("market", "上市")
        s_type   = r.get("type", "STOCK")
        industry = r.get("industry", "")
        name     = r.get("stock_name", "")
        price    = r.get("price", 0)
        chg      = r.get("chg", 0)
        chg_pct  = r.get("chg_pct", 0)

        # 評分
        A   = r["A_foreign"]["score"]
        B   = r["B_trust"]["score"]
        C   = r["C_broker"]["score"]
        D   = r["D_technical"]["score"]
        E   = r["E_fundamental"]["score"]
        S2h = s2h.get("score", 0)
        S2m = s2m.get("score", 0)

        # 籌碼數值
        foreign_days  = a_det.get("consec_days", 0)
        trust_days    = b_det.get("consec_days", 0)
        broker_days   = broker.get("max_consec_days", 0)
        broker_conc   = int(broker.get("avg_daily_concentration", 0) * 100)
        margin_ratio  = round(ma.get("margin_ratio", 0) * 100, 1)
        short_ratio   = round(ma.get("short_ratio", 0) * 100, 1)
        big_chg       = round(trend.get("big_chg_4w", 0), 2)
        rsi           = round(r["D_technical"].get("detail", {}).get("rsi", 50), 1)

        # 投信近1/3/5日買超（用累計 diff 估算）
        cumulative = b_det.get("cumulative_diff", 0)
        trust_d5   = cumulative
        trust_d3   = int(cumulative * 0.65)
        trust_d1   = int(cumulative * 0.35)

        # 分點資料（broker_names JSON）
        broker_names_val = json.dumps(
            broker.get("broker_names", {}),
            ensure_ascii=False
        )

        # 標記與訊號
        tags       = " ".join(r.get("tags", []))
        signal     = "BUY" if r["total_score"] >= 90 else ("WATCH" if r["total_score"] >= 77 else "NONE")
        signal_desc = r.get("signal_desc", "")

        # 資料日期
        scan_dt = scan_date

        rows.append({
            "code":         stock_id,
            "name":         name,
            "market":       market,
            "type":         s_type,
            "industry":     industry,
            "price":        price,
            "chg":          chg,
            "chg_pct":      chg_pct,
            "score":        r["total_score"],
            "A":            A,
            "B":            B,
            "C":            C,
            "D":            D,
            "E":            E,
            "S2h":          S2h,
            "S2m":          S2m,
            "foreign_days": foreign_days,
            "trust_days":   trust_days,
            "broker_days":  broker_days,
            "broker_conc":  broker_conc,
            "margin_ratio": margin_ratio,
            "short_ratio":  short_ratio,
            "big_chg":      big_chg,
            "rsi":          rsi,
            "trust_d1":     trust_d1,
            "trust_d3":     trust_d3,
            "trust_d5":     trust_d5,
            "broker_names": broker_names_val,
            "tags":         tags,
            "signal":       signal,
            "signal_desc":  signal_desc,
            "scan_date":    scan_dt,
        })

    if not rows:
        return None

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filepath = os.path.join(OUTPUT_DIR, "scan_app.csv")
    pd.DataFrame(rows).to_csv(filepath, index=False, encoding="utf-8-sig")
    logger.info(f"App CSV 已儲存：{filepath}（{len(rows)} 支）")
    return filepath


def generate_report(results: list, scan_date: str,
                    total_scanned: int, elapsed: float):
    """
    主輸出入口：產生所有輸出格式。
    """
    df = build_summary_df(results)
    
    if df.empty:
        logger.warning("候選名單為空（休市或資料未更新），跳過報告")
        return df
        
    print_console_report(df, scan_date, total_scanned, elapsed)

    if OUTPUT_CSV:
        save_csv(df, scan_date)
        save_app_csv(results, scan_date)   # App 專用 CSV

    save_detail_json(results, scan_date)

    return df
