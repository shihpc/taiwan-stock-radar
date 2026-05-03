# backtest/report.py
# ============================================================
#  回測報告產生器
#  輸出：終端機報表 + HTML 視覺化報告
# ============================================================

import os
import json
import logging
from typing import Optional

import pandas as pd
import numpy as np

from backtest.engine import BacktestStats, Trade

logger = logging.getLogger(__name__)


# ── 終端機報表 ────────────────────────────────────────────────

def print_backtest_report(stats: BacktestStats, config=None, trades: list = None):
    """
    終端機格式化回測報告。
    """
    sep  = "=" * 65
    sep2 = "-" * 65

    print(f"\n{sep}")
    print(f"  台股主力選股掃描器｜回測績效報告")
    if config:
        print(f"  期間：{config.start_date} ~ {config.end_date}")
        print(f"  持有：{config.hold_days} 交易日｜"
              f"停損：{config.stop_loss_pct*100:.0f}%｜"
              f"停利：{config.take_profit_pct*100:.0f}%")
    print(sep)

    # ── 核心績效
    print(f"\n  【核心績效】")
    print(f"  總交易次數：{stats.total_trades:>6} 筆")
    print(f"  勝率        ：{stats.win_rate*100:>6.1f}%"
          f"  （勝 {stats.win_trades} / 負 {stats.lose_trades}）")
    print(f"  平均報酬    ：{stats.avg_return*100:>+6.2f}%")
    print(f"  平均獲利    ：{stats.avg_win*100:>+6.2f}%")
    print(f"  平均虧損    ：{stats.avg_loss*100:>+6.2f}%")
    print(f"  獲利因子    ：{stats.profit_factor:>6.2f}"
          f"  （> 1.5 為優）")

    print(f"\n  【風險指標】")
    print(f"  最大回撤    ：{stats.max_drawdown*100:>6.1f}%")
    print(f"  夏普比率    ：{stats.sharpe_ratio:>6.2f}"
          f"  （> 1.0 為優）")
    print(f"  最佳單筆    ：{stats.best_trade*100:>+6.1f}%")
    print(f"  最差單筆    ：{stats.worst_trade*100:>+6.1f}%")

    print(f"\n  【對比基準（0050）】")
    color_alpha = "▲" if stats.alpha > 0 else "▼"
    print(f"  策略總報酬  ：{stats.total_return*100:>+7.1f}%")
    print(f"  基準總報酬  ：{stats.benchmark_return*100:>+7.1f}%")
    print(f"  超額報酬α  ：{color_alpha}{abs(stats.alpha)*100:>6.1f}%")

    print(f"\n  【出場原因分布】")
    print(f"  持滿出場    ：{stats.hold_count:>4} 筆")
    print(f"  觸發停損    ：{stats.stop_loss_count:>4} 筆")
    print(f"  觸發停利    ：{stats.take_profit_count:>4} 筆")

    # ── 分數區間統計
    if stats.score_band_stats:
        print(f"\n  【分數區間績效】")
        print(f"  {'分數區間':<10}  {'筆數':>5}  {'勝率':>7}  {'平均報酬':>9}")
        print(f"  {'-'*40}")
        for band, s in stats.score_band_stats.items():
            print(f"  {band:<10}  {s['count']:>5}  "
                  f"{s['win_rate']*100:>6.1f}%  "
                  f"{s['avg_return']:>+8.2f}%")

    # ── 標記績效
    if stats.tag_stats:
        print(f"\n  【各標記平均報酬（前 8）】")
        top_tags = sorted(
            stats.tag_stats.items(),
            key=lambda x: x[1]["avg_return"],
            reverse=True
        )[:8]
        for tag, s in top_tags:
            print(f"  {tag:<15}  "
                  f"筆數:{s['count']:>3}  "
                  f"勝率:{s['win_rate']*100:>5.1f}%  "
                  f"均報酬:{s['avg_return']:>+6.2f}%")

    # ── 月份績效
    if stats.monthly_returns:
        print(f"\n  【月份平均報酬】")
        months = list(stats.monthly_returns.items())
        for i in range(0, len(months), 4):
            row = months[i:i+4]
            parts = [f"{ym}: {ret:>+5.1f}%" for ym, ret in row]
            print("  " + "  ".join(parts))

    # ── Top 10 最佳交易
    if trades:
        completed = [t for t in trades if t.exit_date]
        best10 = sorted(completed, key=lambda x: x.pnl_pct, reverse=True)[:5]
        worst5 = sorted(completed, key=lambda x: x.pnl_pct)[:5]

        print(f"\n  【最佳 5 筆交易】")
        print(f"  {'代碼':<7} {'名稱':<10} {'進場':>10} {'出場':>10} {'報酬':>8} {'原因'}")
        for t in best10:
            print(f"  {t.stock_id:<7} {t.stock_name[:8]:<10} "
                  f"{t.entry_date:>10} {t.exit_date:>10} "
                  f"{t.pnl_pct*100:>+7.1f}%  {t.exit_reason}")

        print(f"\n  【最差 5 筆交易】")
        print(f"  {'代碼':<7} {'名稱':<10} {'進場':>10} {'出場':>10} {'報酬':>8} {'原因'}")
        for t in worst5:
            print(f"  {t.stock_id:<7} {t.stock_name[:8]:<10} "
                  f"{t.entry_date:>10} {t.exit_date:>10} "
                  f"{t.pnl_pct*100:>+7.1f}%  {t.exit_reason}")

    print(f"\n{sep}")
    print("  ⚠️  回測績效不代表未來結果，請依個人風險承受度操作。")
    print(f"{sep}\n")


# ── HTML 視覺化報告 ───────────────────────────────────────────

def generate_html_report(
    stats:  BacktestStats,
    trades: list[Trade],
    config=None,
    output_path: str = "output/backtest_report.html"
) -> str:
    """
    產生完整的 HTML 互動式回測報告，含資金曲線、月報酬熱力圖等。
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # 資金曲線資料
    equity = [1.0]
    equity_dates = []
    completed = sorted(
        [t for t in trades if t.exit_date],
        key=lambda x: x.exit_date
    )
    for t in completed:
        equity.append(equity[-1] * (1 + t.pnl_pct))
        equity_dates.append(t.exit_date)

    # 月報酬資料
    monthly_data = json.dumps(stats.monthly_returns)

    # 交易記錄表格 HTML
    trade_rows = ""
    for t in sorted(completed, key=lambda x: x.exit_date, reverse=True)[:100]:
        color = "#ff4444" if t.pnl_pct > 0 else "#00cc66"
        trade_rows += f"""
        <tr>
          <td>{t.stock_id}</td>
          <td>{t.stock_name}</td>
          <td>{t.entry_date}</td>
          <td>{t.exit_date}</td>
          <td>{t.entry_price:.1f}</td>
          <td>{t.exit_price:.1f}</td>
          <td style="color:{color};font-weight:700">{t.pnl_pct*100:+.2f}%</td>
          <td>{t.score}</td>
          <td style="font-size:.75rem">{' '.join(t.tags[:3])}</td>
          <td>{t.exit_reason}</td>
        </tr>"""

    # 標記績效資料
    tag_labels = json.dumps([k for k in stats.tag_stats])
    tag_winrates = json.dumps([v["win_rate"]*100 for v in stats.tag_stats.values()])
    tag_returns  = json.dumps([v["avg_return"] for v in stats.tag_stats.values()])

    html = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>台股主力選股 回測報告</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{background:#050d1a;color:#d0e8ff;font-family:'Noto Sans TC',sans-serif;padding:16px;}}
h1{{font-size:1.3rem;color:#00e5ff;margin-bottom:16px;letter-spacing:3px;}}
h2{{font-size:.9rem;color:#00b4ff;margin:20px 0 10px;letter-spacing:2px;border-left:3px solid #00b4ff;padding-left:10px;}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px;margin-bottom:20px;}}
.card{{background:#0a1e36;border:1px solid #1a4a6e;border-radius:8px;padding:12px;text-align:center;}}
.card .lbl{{font-size:.65rem;color:#6a9dbf;margin-bottom:5px;}}
.card .val{{font-size:1.3rem;font-weight:700;font-family:monospace;}}
.pos{{color:#ff4444;}}.neg{{color:#00cc66;}}.neu{{color:#ffd740;}}
.chart-wrap{{background:#0a1e36;border:1px solid #1a4a6e;border-radius:8px;padding:14px;margin-bottom:14px;}}
canvas{{max-height:260px;}}
table{{width:100%;border-collapse:collapse;font-size:.72rem;}}
th{{background:#071525;color:#6a9dbf;padding:7px;text-align:center;position:sticky;top:0;}}
td{{padding:6px 8px;border-bottom:1px solid #0f3050;text-align:center;}}
tr:hover td{{background:rgba(0,180,255,.05);}}
.tbl-wrap{{overflow-x:auto;max-height:400px;overflow-y:auto;
  background:#0a1e36;border:1px solid #1a4a6e;border-radius:8px;}}
.badge{{display:inline-block;background:#071525;border:1px solid #1a4a6e;
  border-radius:4px;padding:2px 6px;font-size:.62rem;margin:1px;}}
footer{{text-align:center;color:#2d5a7a;font-size:.65rem;margin-top:20px;}}
</style>
</head>
<body>
<h1>📊 台股主力選股掃描器｜回測績效報告</h1>
<p style="font-size:.72rem;color:#6a9dbf;margin-bottom:16px">
  回測期間：{config.start_date if config else 'N/A'} ～ {config.end_date if config else 'N/A'}｜
  持有：{config.hold_days if config else 'N/A'} 交易日｜
  停損：{config.stop_loss_pct*100:.0f}% / 停利：{config.take_profit_pct*100:.0f}%
</p>

<h2>核心績效指標</h2>
<div class="grid">
  <div class="card"><div class="lbl">總交易次數</div><div class="val neu">{stats.total_trades}</div></div>
  <div class="card"><div class="lbl">勝率</div><div class="val {'pos' if stats.win_rate>=0.5 else 'neg'}">{stats.win_rate*100:.1f}%</div></div>
  <div class="card"><div class="lbl">平均報酬</div><div class="val {'pos' if stats.avg_return>0 else 'neg'}">{stats.avg_return*100:+.2f}%</div></div>
  <div class="card"><div class="lbl">獲利因子</div><div class="val {'pos' if stats.profit_factor>=1.5 else 'neu'}">{stats.profit_factor:.2f}</div></div>
  <div class="card"><div class="lbl">最大回撤</div><div class="val neg">-{stats.max_drawdown*100:.1f}%</div></div>
  <div class="card"><div class="lbl">夏普比率</div><div class="val {'pos' if stats.sharpe_ratio>=1 else 'neu'}">{stats.sharpe_ratio:.2f}</div></div>
  <div class="card"><div class="lbl">策略總報酬</div><div class="val {'pos' if stats.total_return>0 else 'neg'}">{stats.total_return*100:+.1f}%</div></div>
  <div class="card"><div class="lbl">超額報酬 α</div><div class="val {'pos' if stats.alpha>0 else 'neg'}">{stats.alpha*100:+.1f}%</div></div>
</div>

<h2>資金曲線</h2>
<div class="chart-wrap">
  <canvas id="equityChart"></canvas>
</div>

<h2>月份報酬分布</h2>
<div class="chart-wrap">
  <canvas id="monthlyChart"></canvas>
</div>

<h2>各標記勝率與平均報酬</h2>
<div class="chart-wrap">
  <canvas id="tagChart"></canvas>
</div>

<h2>最近 100 筆交易記錄</h2>
<div class="tbl-wrap">
<table>
  <thead><tr>
    <th>代碼</th><th>名稱</th><th>進場日</th><th>出場日</th>
    <th>進場價</th><th>出場價</th><th>損益率</th>
    <th>評分</th><th>標記</th><th>出場原因</th>
  </tr></thead>
  <tbody>{trade_rows}</tbody>
</table>
</div>

<footer>⚠️ 本回測報告僅供研究參考，不代表未來績效保證，請依個人風險承受度操作。</footer>

<script>
// 資金曲線
const equityData = {json.dumps(equity)};
const equityDates = {json.dumps(equity_dates)};
new Chart(document.getElementById('equityChart'), {{
  type: 'line',
  data: {{
    labels: equityDates,
    datasets: [{{
      label: '策略資金曲線',
      data: equityData,
      borderColor: '#00e5ff',
      backgroundColor: 'rgba(0,229,255,0.08)',
      fill: true,
      tension: 0.3,
      pointRadius: 0,
      borderWidth: 2,
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ labels: {{ color: '#6a9dbf' }} }} }},
    scales: {{
      x: {{ ticks: {{ color:'#6a9dbf', maxTicksLimit:12 }}, grid: {{ color:'rgba(15,48,80,.5)' }} }},
      y: {{ ticks: {{ color:'#6a9dbf', callback: v => v.toFixed(2)+'x' }}, grid: {{ color:'rgba(15,48,80,.5)' }} }}
    }}
  }}
}});

// 月份報酬
const monthly = {monthly_data};
const mLabels = Object.keys(monthly);
const mVals   = Object.values(monthly);
new Chart(document.getElementById('monthlyChart'), {{
  type: 'bar',
  data: {{
    labels: mLabels,
    datasets: [{{
      label: '月平均報酬 (%)',
      data: mVals,
      backgroundColor: mVals.map(v => v >= 0 ? 'rgba(255,61,90,0.75)' : 'rgba(0,204,102,0.75)'),
      borderWidth: 0,
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ labels: {{ color:'#6a9dbf' }} }} }},
    scales: {{
      x: {{ ticks: {{ color:'#6a9dbf' }}, grid: {{ color:'rgba(15,48,80,.5)' }} }},
      y: {{ ticks: {{ color:'#6a9dbf', callback: v => v+'%' }}, grid: {{ color:'rgba(15,48,80,.5)' }} }}
    }}
  }}
}});

// 標記績效
const tagLabels = {tag_labels};
const tagWinRates = {tag_winrates};
const tagReturns  = {tag_returns};
new Chart(document.getElementById('tagChart'), {{
  type: 'bar',
  data: {{
    labels: tagLabels,
    datasets: [
      {{ label:'勝率(%)', data: tagWinRates, backgroundColor:'rgba(0,180,255,0.7)', yAxisID:'y' }},
      {{ label:'平均報酬(%)', data: tagReturns, backgroundColor:'rgba(255,215,64,0.7)', type:'line', yAxisID:'y2', tension:0.3 }},
    ]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ labels: {{ color:'#6a9dbf' }} }} }},
    scales: {{
      x: {{ ticks: {{ color:'#6a9dbf', maxRotation:30 }}, grid: {{ color:'rgba(15,48,80,.5)' }} }},
      y:  {{ ticks: {{ color:'#00b4ff', callback:v=>v+'%' }}, grid: {{ color:'rgba(15,48,80,.5)' }}, position:'left' }},
      y2: {{ ticks: {{ color:'#ffd740', callback:v=>v+'%' }}, grid: {{ display:false }}, position:'right' }},
    }}
  }}
}});
</script>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    logger.info(f"HTML 報告已儲存：{output_path}")
    return output_path


# ── 便利函式：儲存 JSON 統計 ──────────────────────────────────

def save_stats_json(stats: BacktestStats,
                    output_path: str = "output/backtest_stats.json"):
    """儲存完整統計數據為 JSON"""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    data = {
        "total_trades":      stats.total_trades,
        "win_rate":          round(stats.win_rate * 100, 2),
        "avg_return_pct":    round(stats.avg_return * 100, 2),
        "avg_win_pct":       round(stats.avg_win * 100, 2),
        "avg_loss_pct":      round(stats.avg_loss * 100, 2),
        "profit_factor":     round(stats.profit_factor, 3),
        "max_drawdown_pct":  round(stats.max_drawdown * 100, 2),
        "sharpe_ratio":      round(stats.sharpe_ratio, 3),
        "total_return_pct":  round(stats.total_return * 100, 2),
        "benchmark_pct":     round(stats.benchmark_return * 100, 2),
        "alpha_pct":         round(stats.alpha * 100, 2),
        "best_trade_pct":    round(stats.best_trade * 100, 2),
        "worst_trade_pct":   round(stats.worst_trade * 100, 2),
        "stop_loss_count":   stats.stop_loss_count,
        "take_profit_count": stats.take_profit_count,
        "hold_count":        stats.hold_count,
        "tag_stats":         stats.tag_stats,
        "monthly_returns":   stats.monthly_returns,
        "score_band_stats":  stats.score_band_stats,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info(f"統計 JSON 已儲存：{output_path}")
