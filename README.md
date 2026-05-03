# 台股主力選股掃描器

> 以 FinMind API 為資料源，搭配 AI 評分邏輯，每日自動掃描台股中有主力介入、投信認養、即將或正在起漲的個股。

## 專案結構

```
stock_scanner/
├── config/settings.py       ← 所有參數：Token、評分門檻、權重
├── data/fetcher.py          ← FinMind API 封裝（含批次快取）
├── engine/
│   ├── indicators.py        ← RSI / MACD / KD / MA 純計算
│   ├── scorer.py            ← 五大面向 + 股權分散 + 融資券評分
│   ├── shareholding.py      ← 股權分散表分析（Sprint 2）
│   ├── margin_analysis.py   ← 融資券深度分析（Sprint 2）
│   ├── broker_analysis.py   ← 主力分點集中分析（Sprint 3）
│   └── filters.py           ← 黑名單與品質過濾
├── output/reporter.py       ← 終端機報表 + CSV 輸出
├── backtest/
│   ├── engine.py            ← Trade、BacktestStats 資料結構
│   ├── runner.py            ← 回測執行器（含 Sprint 2 資料）
│   ├── report.py            ← 回測報告（終端機 + HTML）
│   └── run_backtest.py      ← 回測 CLI 入口
├── scanner.py               ← 主程式入口
└── requirements.txt
```

## 評分架構（滿分 118）

| 面向 | 分 | 核心指標 | 所需方案 |
|------|----|---------|---------|
| A. 外資動向 | 20 | 連買天數、持股比例趨勢 | 免費 |
| B. 投信認養 | 20 | 連買天數、拉回護盤 | 免費 |
| C. 主力分點 | 20 | 集中度、連續進場、量增不漲 | **Sponsor** |
| D. 技術面 | 20 | 均線多頭、RSI、MACD、量能 | 免費 |
| E. 基本面 | 20 | 月營收、EPS、毛利率 | 免費 |
| 股權分散 | 10 | 大戶週持股變化 | **Backer+** |
| 融資券 | 8 | 融資率、軋空潛力 | 免費 |

候選門檻：77 分（65%）｜強烈關注：95 分（80%）

## 快速開始

```bash
# 安裝套件
pip install -r requirements.txt

# 設定 Token（編輯 config/settings.py）
FINMIND_TOKEN = "你的Token"

# 單股測試
python scanner.py --stock 2454

# 快速掃描（前100支，不含分點）
python scanner.py --quick --no-broker

# 正式掃描（全市場，不含分點）
python scanner.py --no-broker

# 完整掃描（含分點，需 Sponsor）
python scanner.py

# 每日排程
python scanner.py --schedule

# 回測驗證
python -m backtest.run_backtest --mode quick
python -m backtest.run_backtest --start 2022-01-01 --end 2024-12-31 --hold 20
```

## 標記說明

| 標記 | 意義 |
|------|------|
| ★法人共識 | 外資+投信同時連買≥3日 |
| ★投信深度認養 | 投信連買≥10日 |
| ★籌碼集中 | 大戶持股4週↑+散戶↓ |
| ★籌碼乾淨 | 融資率<20% |
| ★軋空潛力 | 融券率>30% |
| ★主力連續進場 | 同分點連買≥5日 |
| ★主力低調吃貨 | 量增不漲≥3日 |
| ★慣用分點現身 | 已知主力慣用分點出現 |
| ★營收創高 | 當月營收歷史新高 |

## 免責聲明

本系統僅供學術研究與個人投資輔助，不構成任何投資建議，操作前請依個人風險承受度判斷。
