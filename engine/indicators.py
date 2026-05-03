# engine/indicators.py
# ============================================================
#  技術指標計算模組
#  輸入：pandas DataFrame（含 date, close, open, high, low, volume）
#  輸出：計算好的指標值（純計算，不依賴外部 API）
# ============================================================

import numpy as np
import pandas as pd
import logging

logger = logging.getLogger(__name__)


def prepare_price_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    標準化欄位名稱，確保後續計算一致。
    FinMind TaiwanStockPrice 欄位：
      open, max（最高）, min（最低）, close, Trading_Volume
    """
    if df.empty:
        return df

    df = df.copy()
    df = df.rename(columns={
        "max": "high",
        "min": "low",
        "Trading_Volume": "volume",
    })
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["close"])
    return df


# ── 移動平均線 ────────────────────────────────────────────────

def calc_ma(df: pd.DataFrame, periods: list[int] = [5, 20, 60]) -> pd.DataFrame:
    """計算多條 MA 並附加到 DataFrame"""
    df = df.copy()
    for p in periods:
        df[f"ma{p}"] = df["close"].rolling(window=p, min_periods=p).mean().round(2)
    return df


def is_bullish_alignment(df: pd.DataFrame) -> tuple[bool, bool]:
    """
    判斷均線多頭排列。
    回傳：(完整多頭排列: MA5>MA20>MA60, 短期多頭: MA5>MA20)
    """
    if df.empty or len(df) < 60:
        return False, False

    # 確認欄位存在
    if not all(c in df.columns for c in ["ma5", "ma20", "ma60"]):
        df = calc_ma(df, [5, 20, 60])

    last = df.iloc[-1]
    try:
        full = (last["ma5"] > last["ma20"] > last["ma60"])
        partial = (last["ma5"] > last["ma20"]) and not full
        return full, partial
    except (KeyError, TypeError):
        return False, False


# ── RSI ───────────────────────────────────────────────────────

def calc_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """
    Wilder's RSI 計算。
    """
    df = df.copy()
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    # 使用 EWM 模擬 Wilder's Smoothing（alpha = 1/period）
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = (100 - 100 / (1 + rs)).round(2)
    return df


def get_latest_rsi(df: pd.DataFrame, period: int = 14) -> float:
    """取得最新 RSI 值，若資料不足回傳 -1"""
    if len(df) < period + 5:
        return -1.0
    df = calc_rsi(df, period)
    return float(df["rsi"].dropna().iloc[-1]) if not df["rsi"].dropna().empty else -1.0


# ── MACD ──────────────────────────────────────────────────────

def calc_macd(df: pd.DataFrame,
              fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """
    標準 MACD 計算。
    新增欄位：macd_dif, macd_dea, macd_hist
    """
    df = df.copy()
    ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
    ema_slow = df["close"].ewm(span=slow, adjust=False).mean()
    df["macd_dif"] = (ema_fast - ema_slow).round(4)
    df["macd_dea"] = df["macd_dif"].ewm(span=signal, adjust=False).mean().round(4)
    df["macd_hist"] = (2 * (df["macd_dif"] - df["macd_dea"])).round(4)
    return df


def is_macd_golden_cross(df: pd.DataFrame) -> bool:
    """
    判斷 MACD 黃金交叉：DIF 由下方向上穿越 DEA。
    條件：前一日 DIF < DEA，今日 DIF >= DEA
    """
    if len(df) < 30:
        return False
    if "macd_dif" not in df.columns:
        df = calc_macd(df)
    df_clean = df.dropna(subset=["macd_dif", "macd_dea"])
    if len(df_clean) < 2:
        return False
    prev = df_clean.iloc[-2]
    curr = df_clean.iloc[-1]
    return (prev["macd_dif"] < prev["macd_dea"]) and (curr["macd_dif"] >= curr["macd_dea"])


def is_macd_above_zero(df: pd.DataFrame) -> bool:
    """MACD 柱狀圖 > 0（多頭動能）"""
    if "macd_hist" not in df.columns:
        df = calc_macd(df)
    hist = df["macd_hist"].dropna()
    return float(hist.iloc[-1]) > 0 if not hist.empty else False


# ── KD 指標 ───────────────────────────────────────────────────

def calc_kd(df: pd.DataFrame, period: int = 9, smooth: int = 3) -> pd.DataFrame:
    """
    KD 隨機指標計算（Stochastic Oscillator）。
    新增欄位：kd_k, kd_d
    """
    df = df.copy()
    low_n = df["low"].rolling(window=period, min_periods=1).min()
    high_n = df["high"].rolling(window=period, min_periods=1).max()
    rsv = ((df["close"] - low_n) / (high_n - low_n).replace(0, np.nan) * 100)

    k = rsv.ewm(alpha=1/smooth, adjust=False).mean()
    d = k.ewm(alpha=1/smooth, adjust=False).mean()

    df["kd_k"] = k.round(2)
    df["kd_d"] = d.round(2)
    return df


# ── 成交量分析 ────────────────────────────────────────────────

def is_volume_breakout(df: pd.DataFrame,
                       lookback: int = 20, multiplier: float = 1.5) -> bool:
    """
    判斷量能突破：今日成交量 > 近 N 日均量的 multiplier 倍。
    且今日收盤 > 近 N 日最高收盤（突破前高）。
    """
    if len(df) < lookback + 1:
        return False

    recent = df.iloc[-(lookback+1):-1]  # 不含今日
    today = df.iloc[-1]

    avg_vol = recent["volume"].mean()
    max_close = recent["close"].max()

    vol_ok = today["volume"] > avg_vol * multiplier
    price_ok = today["close"] >= max_close
    return bool(vol_ok and price_ok)


def calc_volume_ratio(df: pd.DataFrame, days: int = 5) -> float:
    """今日量 / 近 N 日均量"""
    if len(df) < days + 1:
        return 0.0
    avg = df["volume"].iloc[-(days+1):-1].mean()
    today_vol = df["volume"].iloc[-1]
    return round(float(today_vol / avg) if avg > 0 else 0.0, 2)


# ── 綜合技術指標摘要 ──────────────────────────────────────────

def get_technical_summary(df: pd.DataFrame) -> dict:
    """
    一次計算所有技術指標，回傳摘要 dict。
    供評分引擎使用，避免重複計算。
    """
    if df.empty or len(df) < 10:
        return {
            "rsi": -1, "macd_golden_cross": False, "macd_above_zero": False,
            "full_bull": False, "partial_bull": False,
            "volume_breakout": False, "volume_ratio": 0,
            "ma5": None, "ma20": None, "ma60": None,
            "close": None, "valid": False
        }

    df = prepare_price_df(df)
    df = calc_ma(df, [5, 20, 60])
    df = calc_rsi(df)
    df = calc_macd(df)
    df = calc_kd(df)

    full_bull, partial_bull = is_bullish_alignment(df)
    last = df.iloc[-1]

    return {
        "rsi": get_latest_rsi(df),
        "macd_golden_cross": is_macd_golden_cross(df),
        "macd_above_zero": is_macd_above_zero(df),
        "full_bull": full_bull,
        "partial_bull": partial_bull,
        "volume_breakout": is_volume_breakout(df),
        "volume_ratio": calc_volume_ratio(df),
        "ma5": round(float(last.get("ma5", 0) or 0), 2),
        "ma20": round(float(last.get("ma20", 0) or 0), 2),
        "ma60": round(float(last.get("ma60", 0) or 0), 2),
        "kd_k": round(float(last.get("kd_k", 0) or 0), 2),
        "kd_d": round(float(last.get("kd_d", 0) or 0), 2),
        "macd_dif": round(float(last.get("macd_dif", 0) or 0), 4),
        "macd_dea": round(float(last.get("macd_dea", 0) or 0), 4),
        "close": round(float(last.get("close", 0) or 0), 2),
        "valid": True
    }
