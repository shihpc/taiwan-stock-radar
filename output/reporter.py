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
)

logger = logging.getLogger(__name__)


def build_summary_df(results: list) -> pd.DataFrame:
    """簡單摘要 DataFrame（無評分系統，僅列基本面向訊號統計）。"""
    rows = []
    for r in results:
        if r.get("error"):
            continue
        fr = r.get("foreign_radar", {}) or {}
        ti = r.get("trust_io", {}) or {}
        bo = r.get("breakout", {}) or {}
        rows.append({
            "代碼":   r["stock_id"],
            "名稱":   r.get("stock_name", ""),
            "外資5日金額":  (fr.get("5") or {}).get("net_amount_m", 0),
            "投信5日金額":  (ti.get("5") or {}).get("net_amount_m", 0),
            "向上突破":      "✓" if bo.get("qualified_up")   else "",
            "向下突破":      "✓" if bo.get("qualified_down") else "",
        })
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df.index = df.index + 1
    df.index.name = "序"
    return df


def print_console_report(df: pd.DataFrame, scan_date: str,
                          total_scanned: int, elapsed: float,
                          results: list = None):
    """終端機格式化報表（無評分系統，列各 tab 訊號統計）。"""
    if not OUTPUT_CONSOLE:
        return

    sep = "=" * 80
    print(f"\n{sep}")
    print(f"  台股雷達掃描器｜{scan_date}")
    print(f"  掃描：{total_scanned:,} 支｜耗時：{elapsed:.1f}s")
    print(sep)

    if results:
        # 各 tab 訊號統計（5 視窗 × 買 / 賣）
        def _count_window_dir(field, dir_):
            n = 0
            for r in results:
                # 該股每個視窗都看一次（任一視窗符合 ≥ 5 千萬就算）
                radar = r.get(field) or {}
                for w in ['1', '3', '5', '10', '20']:
                    v = (radar.get(w) or {}).get('net_amount_m', 0)
                    if dir_ == 'buy' and v >=  50:  n += 1; break
                    if dir_ == 'sell' and v <= -50: n += 1; break
            return n

        f_buy  = _count_window_dir('foreign_radar', 'buy')
        f_sell = _count_window_dir('foreign_radar', 'sell')
        t_buy  = _count_window_dir('trust_io',      'buy')
        t_sell = _count_window_dir('trust_io',      'sell')
        bup    = sum(1 for r in results if (r.get('breakout') or {}).get('qualified_up'))
        bdn    = sum(1 for r in results if (r.get('breakout') or {}).get('qualified_down'))

        print(f"  外資  買超 ≥5千萬：{f_buy:>4} 支    賣超 ≥5千萬：{f_sell:>4} 支")
        print(f"  投信  買超 ≥5千萬：{t_buy:>4} 支    賣超 ≥5千萬：{t_sell:>4} 支")
        print(f"  主力  向上突破+爆量：{bup:>4} 支    向下突破+爆量：{bdn:>4} 支")
        print(sep)

    print("  ⚠️  本系統僅供研究參考，不構成投資建議。")
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
    """儲存完整 radar 明細為 JSON（供後續分析）。"""
    import json
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filename = f"detail_{scan_date.replace('-', '')}.json"
    filepath = os.path.join(OUTPUT_DIR, filename)
    valid = [r for r in results if not r.get("error")]
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(valid, f, ensure_ascii=False, indent=2, default=str)
    logger.info(f"明細 JSON 已儲存：{filepath}")


def save_app_csv(results: list, scan_date: str, dataset_dates: dict = None):
    """
    輸出 App 專用 CSV（scan_app.csv）。
    無評分系統 — 只帶 radar 原始資料給前端排行榜各 tab 使用。
    """
    import json

    dd = dataset_dates or {}

    rows = []
    for r in results:
        if r.get("error"):
            continue

        # 投信雷達（投信雷達 tab）
        tr = r.get("trust_radar", {}) or {}
        # 突破雷達（主力分點 tab）
        bo = r.get("breakout", {}) or {}

        rows.append({
            # 基本資料
            "code":         r["stock_id"],
            "name":         r.get("stock_name", ""),
            "market":       r.get("market", ""),
            "industry":     r.get("industry", ""),
            "price":        r.get("price", 0),
            "chg":          r.get("chg", 0),
            "chg_pct":      r.get("chg_pct", 0),
            # 投信雷達 tab 用（單日彙總 + 箱型）
            "trust_amount_m": tr.get("trust_amount_m", 0),
            "trust_net_lots": tr.get("trust_net_lots", 0),
            "trust_vwap":     tr.get("trust_vwap",     0),
            "box_breakout":   int(tr.get("box_breakout", False)),
            "box_high":       tr.get("box_high",      0),
            "box_low":        tr.get("box_low",       0),
            "box_amplitude":  tr.get("box_amplitude", 0),
            "is_box":         int(tr.get("is_box",    False)),
            # 多視窗法人 radar（外資 / 投信 tab 用）
            "foreign_radar":  json.dumps(r.get("foreign_radar", {}), ensure_ascii=False),
            "trust_io":       json.dumps(r.get("trust_io",      {}), ensure_ascii=False),
            # 主力分點 broker top3（雙向 + 5 視窗）
            "broker_top3":    json.dumps(r.get("broker_top3",   {}), ensure_ascii=False),
            # 主力分點突破旗標 + 當日彙總
            "breakout_up":     int(bo.get("qualified_up",   False)),
            "breakout_down":   int(bo.get("qualified_down", False)),
            "vol_ratio_5d":    bo.get("vol_ratio", 0),
            "mainforce_today": json.dumps(r.get("mainforce_today", {}), ensure_ascii=False),
            # 融資 / 融券 5 視窗
            "margin_radar":   json.dumps(r.get("margin_radar", {}), ensure_ascii=False),
            "short_radar":    json.dumps(r.get("short_radar",  {}), ensure_ascii=False),
            # 回測用：21 日開盤價
            "opens_history":  json.dumps(r.get("opens_history", []), ensure_ascii=False),
            # 各面向資料更新日期（每行重複，前端讀第一筆）
            "dataset_date_A": dd.get("A", ""),
            "dataset_date_B": dd.get("B", ""),
            "dataset_date_C": dd.get("C", ""),
            "dataset_date_D": dd.get("D", ""),
            "dataset_date_E": dd.get("E", ""),
            "dataset_date_H": dd.get("H", ""),
            "dataset_date_M": dd.get("M", ""),
            "scan_date":      scan_date,
        })

    if not rows:
        return None

    df_out = pd.DataFrame(rows).drop_duplicates(subset=["code"], keep="first")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filepath = os.path.join(OUTPUT_DIR, "scan_app.csv")
    df_out.to_csv(filepath, index=False, encoding="utf-8-sig")
    logger.info(f"App CSV 已儲存：{filepath}（{len(df_out)} 支）")
    return filepath


def save_scan_history_json(historical_data: dict, scan_date: str):
    """
    儲存回測用的歷史榜單 JSON（每個 N 對應 8 個榜，30 名 stock_id list）。
    輸出：output/scan_history.json，前端 fetch 後依 chip 組合渲染。
    """
    import json as _json
    if not historical_data or not historical_data.get("rankings"):
        logger.info("無歷史榜單資料，跳過 scan_history.json")
        return None
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filepath = os.path.join(OUTPUT_DIR, "scan_history.json")
    payload = {
        "scan_date":     scan_date,
        "trading_dates": historical_data.get("trading_dates", []),
        "rankings":      historical_data.get("rankings", {}),
    }
    with open(filepath, "w", encoding="utf-8") as f:
        _json.dump(payload, f, ensure_ascii=False, indent=0, separators=(",", ":"))
    logger.info(f"歷史榜單已儲存：{filepath}")
    return filepath


def generate_report(results: list, scan_date: str,
                    total_scanned: int, elapsed: float,
                    dataset_dates: dict = None,
                    historical_data: dict = None):
    """
    主輸出入口：產生所有輸出格式。
    dataset_dates  ：各面向實際最後資料日（傳給 save_app_csv）
    historical_data：回測歷史榜單（傳給 save_scan_history_json）
    """
    df = build_summary_df(results)

    print_console_report(df, scan_date, total_scanned, elapsed, results=results)

    if OUTPUT_CSV:
        if not df.empty:
            save_csv(df, scan_date)
        save_app_csv(results, scan_date, dataset_dates)
        save_scan_history_json(historical_data, scan_date)

    save_detail_json(results, scan_date)

    return df
