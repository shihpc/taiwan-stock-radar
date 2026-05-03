# config/settings.py
# ============================================================
#  台股選股掃描器 - 設定檔
#  修改此檔案來調整 Token、評分權重、篩選條件
# ============================================================

# ── FinMind API ─────────────────────────────────────────────
import os
FINMIND_TOKEN = os.environ.get("FINMIND_TOKEN", "")   # 從環境變數讀取
FINMIND_BASE_URL = "https://api.finmindtrade.com/api/v4/data"
FINMIND_BROKER_URL = "https://api.finmindtrade.com/api/v4/taiwan_stock_trading_daily_report"

# API 每小時請求上限（Sponsor: 不限；Backer: 1500/hr；免費: 600/hr）
API_RATE_LIMIT = 1500
# 每次請求間隔（秒），避免觸發封鎖
# Sponsor 無請求上限，0.1s 僅為防突發性連線問題留的最小緩衝
API_SLEEP_SECONDS = 0.1

# 磁碟快取目錄
CACHE_DIR = "cache"

# ── 資料視窗設定 ─────────────────────────────────────────────
# 技術指標計算所需的歷史天數（至少 60 天才能算 MA60）
LOOKBACK_DAYS = 90
# 投信/外資連買判斷的最大回溯天數
INSTITUTIONAL_LOOKBACK = 20
# 基本面月營收回溯月數
# ⚠️ YoY 需要比較「今月 vs 12個月前」，所以至少要 13 個月資料
# 設 14 保留一個月緩衝（避免月底資料尚未更新）
REVENUE_LOOKBACK_MONTHS = 14

# ── 評分權重設定（總和應為 100）────────────────────────────────
# Sprint 1：C面向（主力分點）暫缺，A/B/D/E 各佔 80 分滿分
SCORE_WEIGHTS = {
    "A_foreign":       20,   # 外資動向
    "B_trust":         20,   # 投信認養
    "C_broker":        20,   # 主力分點（Sprint 3 啟用）
    "D_technical":     20,   # 技術面
    "E_fundamental":   20,   # 基本面
}

# Sprint 1 候選門檻（滿分 80，取 65%）
SPRINT1_MAX_SCORE = 80       # C面向暫缺，滿分為 80
CANDIDATE_THRESHOLD_PCT = 0.65   # 候選門檻比例
CANDIDATE_THRESHOLD = int(SPRINT1_MAX_SCORE * CANDIDATE_THRESHOLD_PCT)  # = 52

# 強烈關注門檻
STRONG_THRESHOLD_PCT = 0.80
STRONG_THRESHOLD = int(SPRINT1_MAX_SCORE * STRONG_THRESHOLD_PCT)  # = 64

# ── A. 外資動向評分參數 ──────────────────────────────────────
FOREIGN_CONSEC_DAYS_MID  = 1      # 連買 1 日 → 1 分
FOREIGN_CONSEC_DAYS_HIGH = 3      # 連買 3 日 → 3 分
FOREIGN_CONSEC_DAYS_TOP  = 5      # 連買 5 日 → 5 分
FOREIGN_TURNOVER_PCT_MID  = 0.30  # 買超佔成交 30% → 3 分
FOREIGN_TURNOVER_PCT_HIGH = 0.50  # 買超佔成交 50% → 5 分
FOREIGN_HOLDING_WINDOW    = 20    # 持股比例上升判斷視窗（天）
FOREIGN_HOLDING_SLOPE_MID = 0.0   # 斜率 > 0 → 3 分
FOREIGN_HOLDING_SLOPE_TOP = 1.5   # 斜率 > 1.5 → 5 分

# ── B. 投信認養評分參數 ──────────────────────────────────────
TRUST_CONSEC_DAYS_MID  = 1        # 連買 1 日 → 3 分
TRUST_CONSEC_DAYS_HIGH = 3        # 連買 3 日 → 7 分
TRUST_CONSEC_DAYS_TOP  = 5        # 連買 5 日 → 10 分
TRUST_HOLDING_PCT_LOW  = 0.05     # 持股佔流通  5% → 5 分
TRUST_HOLDING_PCT_MID  = 0.10     # 持股佔流通 10% → 10 分
TRUST_HOLDING_PCT_HIGH = 0.20     # 持股佔流通 20% → 15 分
TRUST_PULLBACK_DROP_PCT = -0.01   # 認定拉回的跌幅門檻（-1%）

# ── D. 技術面評分參數 ────────────────────────────────────────
MA_SHORT = 5
MA_MID = 20
MA_LONG = 60
RSI_PERIOD = 14
RSI_SWEET_LOW = 50                # RSI 甜蜜區間下限
RSI_SWEET_HIGH = 70               # RSI 甜蜜區間上限
RSI_OVERBOUGHT = 80               # RSI 過熱扣分門檻
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
VOLUME_BREAKOUT_DAYS = 20         # 量突破近 N 日高點
VOLUME_BREAKOUT_MULT = 1.5        # 量需達 N 日均量的倍數

# ── E. 基本面評分參數 ────────────────────────────────────────
REVENUE_GROWTH_MONTHS = 3         # 月營收連續 YoY 正成長月數
REVENUE_GROWTH_MONTHS_BONUS = 6   # 連 6 月加分
EPS_GROWTH_MID = 0.10             # EPS 季增 10%
EPS_GROWTH_HIGH = 0.20            # EPS 季增 20%

# ── 過濾條件（黑名單）────────────────────────────────────────
MIN_MARKET_CAP_BILLION = 20       # 最低市值（億元），排除過小公司
MAX_MARGIN_RATIO = 0.60           # 最高融資使用率，超過視為散戶過度介入
EXCLUDE_ETFS = False              # 保留 ETF
EXCLUDE_WARRANTS = True           # 排除權證（由 filters.py 名稱關鍵字判斷）

# ── Sprint 2：股權分散表參數 ─────────────────────────────────
HOLDING_LOOKBACK_WEEKS = 4        # 大戶持股觀察週數
HOLDING_BIG_THRESHOLD = 400       # 大戶定義：400張以上
HOLDING_CONCENTRATION_CHANGE = 0.5 # 大戶比例4週變化 > 0.5% 才算集中

# ── Sprint 2：融資融券精細參數 ────────────────────────────────
MARGIN_CLEAN_THRESHOLD  = 0.20    # 融資率 < 20% = 籌碼乾淨
MARGIN_WARN_THRESHOLD   = 0.50    # 融資率 > 50% = 警示
MARGIN_BLOCK_THRESHOLD  = 0.60    # 融資率 > 60% = 直接過濾
SHORT_SQUEEZE_THRESHOLD = 0.60    # 融券率 > 60% = 軋空潛力（原 30%，改為 60%）

# ── Sprint 2：滿分調整（加入持股分散+融資券評分後重新計算）
# 評分結構：A(20) + B(20) + C(0) + D(20) + E(20) + 股權分散(10) + 融資券(8)
# 為維持一致性，Sprint 2 仍用 80 分基準，股權分散和融資券取代部分原有計分
SPRINT2_BONUS_MAX = 18            # 股權分散(10) + 融資券上限(8)

# ── 排程設定 ─────────────────────────────────────────────────
SCHEDULE_TIME = "18:30"           # 每日執行時間（收盤後）

# ── 輸出設定 ─────────────────────────────────────────────────
OUTPUT_DIR = "output"
OUTPUT_CSV = True                  # 輸出 CSV 檔案
OUTPUT_CONSOLE = True              # 終端機報表
TOP_N_DISPLAY = 30                 # 最多顯示幾支候選股

# ── 加乘標記條件 ─────────────────────────────────────────────
TAG_CONSENSUS_DAYS = 3             # 外資+投信同時連買幾天 → 標記「法人共識」
TAG_REVENUE_RECORD = True          # 月營收創歷史新高 → 標記「營收創高」
