#!/usr/bin/env python3
# backtest/run_backtest.py
# ============================================================
#  回測執行入口
#
#  使用方式：
#    # 快速驗證（抽樣 50 支股票，週頻掃描）
#    python -m backtest.run_backtest --mode quick
#
#    # 完整回測（全市場，週頻掃描）
#    python -m backtest.run_backtest --mode full
#
#    # 自訂參數
#    python -m backtest.run_backtest \
#      --start 2022-01-01 --end 2024-12-31 \
#      --hold 20 --stop-loss -0.08 --take-profit 0.20 \
#      --threshold 77 --freq weekly
#
#    # 只回測特定股票清單
#    python -m backtest.run_backtest --stocks 2330 2454 3661 6669
# ============================================================

import os
import sys
import argparse
import logging
from datetime import datetime

# 確保可以 import 專案其他模組
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.engine import BacktestConfig
from backtest.runner import BacktestRunner
from backtest.report import (
    print_backtest_report,
    generate_html_report,
    save_stats_json,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/backtest.log", encoding="utf-8"),
    ]
)
logger = logging.getLogger(__name__)


# ── 預設股票池 ────────────────────────────────────────────────

QUICK_STOCKS = [
    # 科技龍頭
    "2330", "2454", "2317", "3661", "2303",
    "3711", "2308", "2382", "2345", "3034",
    # 金融
    "2881", "2882", "2891", "2884", "2885",
    # 傳產/其他
    "1301", "1303", "2002", "2412", "6505",
    # 中小型成長股
    "6669", "6278", "3231", "8046", "6488",
    "3008", "2379", "4966", "6443", "3037",
    # 生技
    "4711", "6547", "3217", "1752",
    # 半導體供應鏈
    "3576", "6239", "3533", "2449", "3715",
    # 電子零組件
    "2357", "2395", "6415", "2369", "3014",
]

SAMPLE_NAMES = {
    "2330": "台積電", "2454": "聯發科", "2317": "鴻海",
    "3661": "世芯-KY", "2303": "聯電",  "2381": "華宇",
    "3711": "日月光投控", "2882": "國泰金", "2881": "富邦金",
    "6669": "緯穎", "6278": "台表科", "2412": "中華電",
    "0050": "元大台灣50",
}


def main():
    os.makedirs("logs",   exist_ok=True)
    os.makedirs("output", exist_ok=True)

    parser = argparse.ArgumentParser(description="台股主力選股 回測系統")
    parser.add_argument("--mode", choices=["quick", "full", "custom"],
                        default="quick", help="回測模式")
    parser.add_argument("--start",      default="2020-01-01")
    parser.add_argument("--end",        default="2024-12-31")
    parser.add_argument("--hold",       type=int,   default=20)
    parser.add_argument("--stop-loss",  type=float, default=-0.08)
    parser.add_argument("--take-profit",type=float, default=0.20)
    parser.add_argument("--threshold",  type=int,   default=88)
    parser.add_argument("--max-pos",    type=int,   default=10)
    parser.add_argument("--freq",       choices=["daily","weekly","monthly"],
                        default="weekly")
    parser.add_argument("--stocks",     nargs="+",  default=None)
    parser.add_argument("--no-html",    action="store_true")
    args = parser.parse_args()

    # ── 建立回測設定
    config = BacktestConfig(
        start_date       = args.start,
        end_date         = args.end,
        hold_days        = args.hold,
        stop_loss_pct    = args.stop_loss,
        take_profit_pct  = args.take_profit,
        score_threshold  = args.threshold,
        max_positions    = args.max_pos,
    )

    # ── 選擇股票池
    if args.stocks:
        stock_ids = args.stocks
        logger.info(f"自訂股票池：{stock_ids}")
    elif args.mode == "quick":
        stock_ids = QUICK_STOCKS
        logger.info(f"快速模式：{len(stock_ids)} 支股票")
    else:  # full
        from data.fetcher import fetch_stock_list
        from engine.filters import filter_stock_list
        logger.info("載入全市場股票清單...")
        stock_list = fetch_stock_list()
        filtered   = filter_stock_list(stock_list)
        stock_ids  = filtered["stock_id"].tolist()
        logger.info(f"完整模式：{len(stock_ids)} 支股票")

    # ── 執行回測
    runner = BacktestRunner(config, stock_ids, SAMPLE_NAMES)

    logger.info(f"開始回測（{args.freq} 掃描）...")
    t0 = datetime.now()
    stats = runner.run(scan_frequency=args.freq)
    elapsed = (datetime.now() - t0).total_seconds()
    logger.info(f"回測完成，耗時 {elapsed:.1f} 秒")

    # ── 輸出報告
    print_backtest_report(stats, config, runner.trades)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    runner.save_trades_csv(f"output/trades_{timestamp}.csv")
    save_stats_json(stats, f"output/stats_{timestamp}.json")

    if not args.no_html:
        html_path = generate_html_report(
            stats, runner.trades, config,
            output_path=f"output/backtest_{timestamp}.html"
        )
        print(f"\n  📊 HTML 報告：{html_path}")

    print(f"  📁 交易記錄：output/trades_{timestamp}.csv")
    print(f"  📁 統計數據：output/stats_{timestamp}.json\n")


if __name__ == "__main__":
    main()
