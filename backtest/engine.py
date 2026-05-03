# backtest/engine.py
# ============================================================
#  回測引擎
#  核心概念：
#    1. 使用歷史資料「模擬」每個交易日的選股結果
#    2. 嚴格遵守「無未來資料」原則：
#       評分日只能看到該日及之前的資料
#    3. 進場：選股日收盤後隔日開盤買入
#    4. 出場：固定持有 N 日，或觸發停損/停利
#    5. 統計：勝率、平均報酬、最大回撤、夏普比率
#
#  回測流程：
#    for 每個回測日 in 歷史日期:
#        取得截至當日的歷史資料（滾動視窗）
#        執行選股評分
#        記錄候選股 → 次日開盤進場
#        持有 N 日後出場
#        計算損益
# ============================================================

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


# ── 回測設定 ──────────────────────────────────────────────────

@dataclass
class BacktestConfig:
    """回測參數設定"""
    start_date:       str   = "2020-01-01"   # 回測開始日
    end_date:         str   = "2024-12-31"   # 回測結束日
    hold_days:        int   = 20             # 持有天數（交易日）
    stop_loss_pct:    float = -0.08          # 停損：-8%
    take_profit_pct:  float = 0.20           # 停利：+20%
    score_threshold:  int   = 77             # 最低進場分數（65% of 118）
    max_positions:    int   = 10             # 最多同時持有幾支
    capital_per_trade:float = 100_000.0      # 每筆投入金額（元）
    use_adj_price:    bool  = True           # 使用還原股價（除權息調整）
    benchmark:        str   = "0050"         # 基準指數代碼


# ── 交易記錄 ──────────────────────────────────────────────────

@dataclass
class Trade:
    """單筆交易記錄"""
    stock_id:      str
    stock_name:    str
    entry_date:    str          # 進場日（評分後隔日）
    entry_price:   float        # 進場價（隔日開盤價）
    exit_date:     str   = ""   # 出場日
    exit_price:    float = 0.0  # 出場價
    exit_reason:   str   = ""   # 出場原因：hold/stop_loss/take_profit/end
    score:         int   = 0    # 進場時的評分
    score_pct:     float = 0.0  # 評分得分率
    tags:          list  = field(default_factory=list)  # 進場時的標記
    shares:        int   = 0    # 買入股數（股）

    @property
    def pnl_pct(self) -> float:
        """損益率"""
        if self.entry_price <= 0 or self.exit_price <= 0:
            return 0.0
        return (self.exit_price - self.entry_price) / self.entry_price

    @property
    def pnl_amount(self) -> float:
        """損益金額（元）"""
        return self.shares * (self.exit_price - self.entry_price)

    @property
    def is_win(self) -> bool:
        return self.pnl_pct > 0

    def to_dict(self) -> dict:
        return {
            "stock_id":    self.stock_id,
            "stock_name":  self.stock_name,
            "entry_date":  self.entry_date,
            "entry_price": self.entry_price,
            "exit_date":   self.exit_date,
            "exit_price":  self.exit_price,
            "exit_reason": self.exit_reason,
            "score":       self.score,
            "score_pct":   round(self.score_pct * 100, 1),
            "pnl_pct":     round(self.pnl_pct * 100, 2),
            "pnl_amount":  round(self.pnl_amount, 0),
            "is_win":      self.is_win,
            "tags":        " ".join(self.tags),
        }


# ── 回測績效統計 ──────────────────────────────────────────────

@dataclass
class BacktestStats:
    """回測績效統計"""
    total_trades:     int   = 0
    win_trades:       int   = 0
    lose_trades:      int   = 0
    win_rate:         float = 0.0   # 勝率
    avg_return:       float = 0.0   # 平均報酬率
    avg_win:          float = 0.0   # 平均獲利
    avg_loss:         float = 0.0   # 平均虧損
    profit_factor:    float = 0.0   # 獲利因子 = 總獲利 / 總虧損
    max_drawdown:     float = 0.0   # 最大回撤
    sharpe_ratio:     float = 0.0   # 夏普比率（年化）
    total_return:     float = 0.0   # 總報酬率（含複利）
    benchmark_return: float = 0.0   # 基準報酬率
    alpha:            float = 0.0   # Alpha（超額報酬）
    best_trade:       float = 0.0   # 最佳單筆
    worst_trade:      float = 0.0   # 最差單筆
    stop_loss_count:  int   = 0     # 觸發停損次數
    take_profit_count:int   = 0     # 觸發停利次數
    hold_count:       int   = 0     # 持滿出場次數

    # 依標記分組統計
    tag_stats:  dict = field(default_factory=dict)
    # 依月份統計
    monthly_returns: dict = field(default_factory=dict)
    # 依分數區間統計
    score_band_stats: dict = field(default_factory=dict)


def calc_stats(trades: list[Trade],
               benchmark_return: float = 0.0) -> BacktestStats:
    """
    從交易列表計算完整績效統計。
    """
    stats = BacktestStats()
    if not trades:
        return stats

    completed = [t for t in trades if t.exit_date]
    if not completed:
        return stats

    returns = [t.pnl_pct for t in completed]
    stats.total_trades    = len(completed)
    stats.win_trades      = sum(1 for r in returns if r > 0)
    stats.lose_trades     = sum(1 for r in returns if r <= 0)
    stats.win_rate        = stats.win_trades / stats.total_trades
    stats.avg_return      = float(np.mean(returns))
    stats.best_trade      = float(max(returns))
    stats.worst_trade     = float(min(returns))
    stats.stop_loss_count = sum(1 for t in completed if t.exit_reason == "stop_loss")
    stats.take_profit_count = sum(1 for t in completed if t.exit_reason == "take_profit")
    stats.hold_count      = sum(1 for t in completed if t.exit_reason == "hold")

    wins  = [r for r in returns if r > 0]
    loses = [r for r in returns if r <= 0]
    stats.avg_win  = float(np.mean(wins))  if wins  else 0.0
    stats.avg_loss = float(np.mean(loses)) if loses else 0.0

    total_win  = sum(wins)
    total_loss = abs(sum(loses))
    stats.profit_factor = total_win / total_loss if total_loss > 0 else float('inf')

    # 最大回撤（以每筆交易損益率計算資金曲線）
    equity = [1.0]
    for r in returns:
        equity.append(equity[-1] * (1 + r))
    peak = equity[0]
    max_dd = 0.0
    for e in equity:
        if e > peak:
            peak = e
        dd = (peak - e) / peak
        if dd > max_dd:
            max_dd = dd
    stats.max_drawdown = max_dd

    # 總報酬（複利）
    stats.total_return = equity[-1] - 1.0

    # 夏普比率（年化，假設無風險利率 2%）
    if len(returns) > 1:
        rf_daily = 0.02 / 252
        excess = [r - rf_daily for r in returns]
        sharpe = np.mean(excess) / (np.std(excess) + 1e-9) * np.sqrt(252)
        stats.sharpe_ratio = float(sharpe)

    stats.benchmark_return = benchmark_return
    stats.alpha = stats.total_return - benchmark_return

    # 依標記分組統計
    tag_map: dict[str, list[float]] = {}
    for t in completed:
        for tag in t.tags:
            tag_map.setdefault(tag, []).append(t.pnl_pct)
    stats.tag_stats = {
        tag: {
            "count":    len(rs),
            "win_rate": round(sum(1 for r in rs if r > 0) / len(rs), 3),
            "avg_return": round(float(np.mean(rs)) * 100, 2),
        }
        for tag, rs in tag_map.items()
    }

    # 依分數區間
    bands = [(77, 85, "77-85"), (85, 95, "85-95"), (95, 118, "95-118")]
    for lo, hi, label in bands:
        band_trades = [t for t in completed if lo <= t.score < hi]
        if band_trades:
            band_returns = [t.pnl_pct for t in band_trades]
            stats.score_band_stats[label] = {
                "count":    len(band_trades),
                "win_rate": round(sum(1 for r in band_returns if r > 0) / len(band_trades), 3),
                "avg_return": round(float(np.mean(band_returns)) * 100, 2),
            }

    # 月份統計
    monthly: dict[str, list[float]] = {}
    for t in completed:
        ym = t.exit_date[:7]
        monthly.setdefault(ym, []).append(t.pnl_pct)
    stats.monthly_returns = {
        ym: round(float(np.mean(rs)) * 100, 2)
        for ym, rs in sorted(monthly.items())
    }

    return stats
