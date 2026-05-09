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

        f_etf = r.get("F_etf_flow", {})
        f_det = f_etf.get("detail", {})
        f1_d  = f_det.get("F1_detail", {})
        f2_d  = f_det.get("F2_detail", {})

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
            "ETF(F)":     f_etf.get("score", 0),
            "F1排名":     f1_d.get("rank", "N/A"),
            "F2三方":     f2_d.get("count", 0),
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

    # 滿分 158（含 F 面向）｜候選門檻 ~55% = 88，強烈關注 ~70% = 110
    threshold = 88
    strong    = 110

    candidates = df[df["總分"] >= threshold]
    strong_df  = df[df["總分"] >= strong]

    sep = "=" * 110

    print(f"\n{sep}")
    print(f"  台股主力選股掃描器（含 F 面向 ETF 資金流）｜{scan_date}")
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
          f"{'總':>4} {'A外':>3} {'B投':>3} {'C主':>3} {'D技':>3} {'E基':>3} {'F流':>3} "
          f"{'股權':>3} {'融':>3} "
          f"{'外資':>4} {'投信':>4} {'主力':>4} {'分點':>4} "
          f"{'RSI':>5} {'融資':>5} {'大戶':>6}  標記")
    print("-" * 110)

    for rank, row in display.iterrows():
        flag = "🔥" if row["總分"] >= strong else "  "
        print(f"{flag}{rank:>2} {row['代碼']:>6} {row['名稱']:<9} "
              f"{row['總分']:>4} {row['外資(A)']:>3} {row['投信(B)']:>3} "
              f"{row['主力(C)']:>3} {row['技術(D)']:>3} {row['基本面(E)']:>3} "
              f"{row['ETF(F)']:>3} "
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


def save_app_csv(results: list, scan_date: str, dataset_dates: dict = None):
    """
    輸出 App 專用 CSV（scan_app.csv）。
    欄位與前端 App 的 generateMockData() 完全對應，
    讓 App 可直接載入取代 Mock 資料。
    dataset_dates：各面向實際最後資料日（每行 row 重複寫入，前端讀第一筆即可）
    """
    import json

    dd = dataset_dates or {}
    dd_A = dd.get("A", "")
    dd_B = dd.get("B", "")
    dd_C = dd.get("C", "")
    dd_D = dd.get("D", "")
    dd_E = dd.get("E", "")
    dd_H = dd.get("H", "")
    dd_M = dd.get("M", "")

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
        F   = r.get("F_etf_flow", {}).get("score", 0)
        S2h = s2h.get("score", 0)
        S2m = s2m.get("score", 0)

        # F 面向子細節（前端可選用）
        f_det  = r.get("F_etf_flow", {}).get("detail", {})
        f1_det = f_det.get("F1_detail", {})
        f2_det = f_det.get("F2_detail", {})
        f3_det = f_det.get("F3_detail", {})
        etf_trust_5d  = f1_det.get("trust_5d_lots", 0)
        etf_rank      = f1_det.get("rank", "N/A")
        etf_chip_cnt  = f2_det.get("count", 0)
        etf_vol_ratio = f3_det.get("vol_ratio_10_60", 0)

        # 投信雷達（前端「投信雷達」tab 用）
        tr = r.get("trust_radar", {})
        trust_amount_m  = tr.get("trust_amount_m",  0)
        trust_net_lots  = tr.get("trust_net_lots",  0)
        trust_vwap      = tr.get("trust_vwap",      0)
        box_breakout    = int(tr.get("box_breakout", False))
        box_high        = tr.get("box_high",        0)
        box_low         = tr.get("box_low",         0)
        box_amplitude   = tr.get("box_amplitude",   0)
        is_box          = int(tr.get("is_box",      False))

        # 外資雷達（5 視窗法人金額 + top3 分點）
        foreign_radar_json = json.dumps(r.get("foreign_radar", {}),
                                          ensure_ascii=False)
        broker_top3_json   = json.dumps(r.get("broker_top3", []),
                                          ensure_ascii=False)
        broker_dir         = r.get("broker_dir", "")

        # 投信進出（5 視窗 + 投信方向 top3 分點）
        trust_io_json      = json.dumps(r.get("trust_io", {}),
                                          ensure_ascii=False)
        trust_broker_top3_json = json.dumps(r.get("trust_broker_top3", []),
                                              ensure_ascii=False)
        trust_broker_dir   = r.get("trust_broker_dir", "")

        # 突破雷達（主力分點 tab 用）
        bo = r.get("breakout", {})
        breakout_up_qual    = int(bo.get("qualified_up",   False))
        breakout_down_qual  = int(bo.get("qualified_down", False))
        vol_ratio_5d        = bo.get("vol_ratio", 0)
        mainforce_today_json = json.dumps(r.get("mainforce_today", {}),
                                              ensure_ascii=False)

        # 融資 / 融券多視窗餘額金額（融資券 tab 用）
        margin_radar_json   = json.dumps(r.get("margin_radar", {}),
                                              ensure_ascii=False)
        short_radar_json    = json.dumps(r.get("short_radar", {}),
                                              ensure_ascii=False)

        # 籌碼數值
        foreign_days  = a_det.get("consec_days", 0)
        trust_days    = b_det.get("consec_days", 0)
        broker_days   = broker.get("max_consec_days", 0)
        broker_conc   = int(broker.get("avg_daily_concentration", 0) * 100)
        margin_ratio  = round(ma.get("margin_ratio", 0) * 100, 1)
        short_ratio   = round(ma.get("short_ratio", 0) * 100, 1)
        big_chg       = round(trend.get("big_chg_4w", 0), 2)
        rsi           = round(r["D_technical"].get("detail", {}).get("rsi", 50), 1)

        # 投信近 1/3/5 個交易日累計買超（張）— 從 scorer 直接算好
        trust_d1 = b_det.get("trust_d1_lots", 0)
        trust_d3 = b_det.get("trust_d3_lots", 0)
        trust_d5 = b_det.get("trust_d5_lots", 0)

        # C 子參數
        c_det        = r["C_broker"].get("detail", {})
        broker_silent = broker.get("silent_accum_days", 0)
        broker_known  = int(len(c_det.get("known_brokers", [])) > 0)

        # A/B/D/E 子參數
        turnover_pct      = round(a_det.get("turnover_pct", 0) * 100, 1)
        holding_rising    = int(a_det.get("holding_rising", False))
        trust_holding_pct = round(b_det.get("holding_pct", 0) * 100, 2)
        trust_pullback    = int(b_det.get("pullback_buy", False))
        d_det             = r["D_technical"].get("detail", {})
        volume_ratio      = round(d_det.get("volume_ratio", 0), 2)
        ma_score          = d_det.get("breakdown", {}).get("均線多頭", 0)
        ma_trend          = 2 if ma_score >= 6 else (1 if ma_score >= 3 else 0)
        revenue_months    = e_det.get("revenue_growth_months", 0)
        eps_growth        = round(e_det.get("eps_avg_growth", 0) * 100, 1)
        gpm_improving     = int(e_det.get("gpm_improving", False))

        # 分點資料（broker_names JSON）
        broker_names_val = json.dumps(
            broker.get("broker_names", {}),
            ensure_ascii=False
        )

        # 標記與訊號
        tags       = " ".join(r.get("tags", []))
        signal     = "BUY" if r["total_score"] >= 110 else ("WATCH" if r["total_score"] >= 88 else "NONE")
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
            "F":            F,
            "S2h":          S2h,
            "S2m":          S2m,
            "etf_trust_5d":  etf_trust_5d,
            "etf_rank":      etf_rank,
            "etf_chip_cnt":  etf_chip_cnt,
            "etf_vol_ratio": etf_vol_ratio,
            "trust_amount_m":  trust_amount_m,
            "trust_net_lots":  trust_net_lots,
            "trust_vwap":      trust_vwap,
            "box_breakout":    box_breakout,
            "box_high":        box_high,
            "box_low":         box_low,
            "box_amplitude":   box_amplitude,
            "is_box":          is_box,
            "foreign_radar":     foreign_radar_json,
            "broker_top3":       broker_top3_json,
            "broker_dir":        broker_dir,
            "trust_io":          trust_io_json,
            "trust_broker_top3": trust_broker_top3_json,
            "trust_broker_dir":  trust_broker_dir,
            "breakout_up":       breakout_up_qual,
            "breakout_down":     breakout_down_qual,
            "vol_ratio_5d":      vol_ratio_5d,
            "mainforce_today":   mainforce_today_json,
            "margin_radar":      margin_radar_json,
            "short_radar":       short_radar_json,
            # 各面向實際最後資料日（每行重複寫入，前端讀第一筆）
            "dataset_date_A":    dd_A,
            "dataset_date_B":    dd_B,
            "dataset_date_C":    dd_C,
            "dataset_date_D":    dd_D,
            "dataset_date_E":    dd_E,
            "dataset_date_H":    dd_H,
            "dataset_date_M":    dd_M,
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
            "broker_names":    broker_names_val,
            "broker_silent":   broker_silent,
            "broker_known":    broker_known,
            "turnover_pct":      turnover_pct,
            "holding_rising":    holding_rising,
            "trust_holding_pct": trust_holding_pct,
            "trust_pullback":    trust_pullback,
            "volume_ratio":      volume_ratio,
            "ma_trend":          ma_trend,
            "revenue_months":    revenue_months,
            "eps_growth":        eps_growth,
            "gpm_improving":     gpm_improving,
            "tags":         tags,
            "signal":       signal,
            "signal_desc":  signal_desc,
            "scan_date":    scan_dt,
        })

    if not rows:
        return None

    df_out = pd.DataFrame(rows).drop_duplicates(subset=["code"], keep="first")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filepath = os.path.join(OUTPUT_DIR, "scan_app.csv")
    df_out.to_csv(filepath, index=False, encoding="utf-8-sig")
    logger.info(f"App CSV 已儲存：{filepath}（{len(df_out)} 支）")
    return filepath


def generate_report(results: list, scan_date: str,
                    total_scanned: int, elapsed: float,
                    dataset_dates: dict = None):
    """
    主輸出入口：產生所有輸出格式。
    dataset_dates：各面向實際最後資料日（傳給 save_app_csv 寫入 CSV）
    """
    df = build_summary_df(results)

    if df.empty:
        logger.warning("候選名單為空（休市或資料未更新），跳過報告")
        return df

    print_console_report(df, scan_date, total_scanned, elapsed)

    if OUTPUT_CSV:
        save_csv(df, scan_date)
        save_app_csv(results, scan_date, dataset_dates)   # App 專用 CSV

    save_detail_json(results, scan_date)

    return df
