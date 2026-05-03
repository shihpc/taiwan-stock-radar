# engine/scorer.py
# ============================================================
#  五大面向評分引擎
#  Sprint 1：A外資 + B投信 + D技術 + E基本面（C主力分點待 Sprint 3）
# ============================================================

import logging
from typing import Optional
import pandas as pd
import numpy as np

from config.settings import (
    # A. 外資
    FOREIGN_CONSEC_DAYS_MID, FOREIGN_CONSEC_DAYS_HIGH, FOREIGN_CONSEC_DAYS_TOP,
    FOREIGN_TURNOVER_PCT_MID, FOREIGN_TURNOVER_PCT_HIGH, FOREIGN_HOLDING_WINDOW,
    FOREIGN_HOLDING_SLOPE_MID, FOREIGN_HOLDING_SLOPE_TOP,
    # B. 投信
    TRUST_CONSEC_DAYS_MID, TRUST_CONSEC_DAYS_HIGH, TRUST_CONSEC_DAYS_TOP,
    TRUST_HOLDING_PCT_LOW, TRUST_HOLDING_PCT_MID, TRUST_HOLDING_PCT_HIGH,
    TRUST_PULLBACK_DROP_PCT,
    # D. 技術
    RSI_SWEET_LOW, RSI_SWEET_HIGH, RSI_OVERBOUGHT,
    VOLUME_BREAKOUT_DAYS, VOLUME_BREAKOUT_MULT,
    # E. 基本面
    REVENUE_GROWTH_MONTHS, REVENUE_GROWTH_MONTHS_BONUS,
    EPS_GROWTH_MID, EPS_GROWTH_HIGH,
    # 標記
    TAG_CONSENSUS_DAYS,
)
from engine.indicators import get_technical_summary, prepare_price_df
from engine.shareholding import score_shareholding, get_total_shares_from_holding
from engine.margin_analysis import analyze_margin, score_margin
from engine.broker_analysis import score_broker_full

logger = logging.getLogger(__name__)


# ── A. 外資動向（滿分 20）────────────────────────────────────

def score_foreign(institutional_df: pd.DataFrame,
                  shareholding_df: pd.DataFrame,
                  price_df: pd.DataFrame) -> dict:
    """
    外資評分。
    institutional_df：個股三大法人歷史資料
    shareholding_df ：外資持股比例歷史資料
    price_df        ：個股歷史股價（用於計算買超佔成交比）
    回傳：{"score": int, "detail": dict}
    """
    score = 0
    detail = {
        "consec_days": 0,
        "turnover_pct": 0.0,
        "holding_rising": False,
        "breakdown": {}
    }

    if institutional_df.empty:
        return {"score": 0, "detail": detail}

    # 篩選外資資料，只取最近交易日
    foreign = institutional_df[
        institutional_df["name"].str.contains("外資", na=False)
    ].copy()
    foreign = foreign.sort_values("date").reset_index(drop=True)

    if foreign.empty:
        return {"score": 0, "detail": detail}

    foreign["diff"] = pd.to_numeric(foreign.get("diff", 0), errors="coerce").fillna(0)

    # ── 指標1：外資連續買超天數（最高 5 分）
    consec = _count_consecutive_buy(foreign["diff"])
    detail["consec_days"] = consec
    if consec >= FOREIGN_CONSEC_DAYS_TOP:    # ≥ 5 天
        s1 = 5
    elif consec >= FOREIGN_CONSEC_DAYS_HIGH: # ≥ 3 天
        s1 = 3
    elif consec >= FOREIGN_CONSEC_DAYS_MID:  # ≥ 1 天
        s1 = 1
    else:
        s1 = 0
    detail["breakdown"]["連買天數"] = s1
    score += s1

    # ── 指標2：買超佔成交比（最高 5 分）
    s2 = 0
    if not price_df.empty:
        latest_date = foreign["date"].max()
        foreign_today = foreign[foreign["date"] == latest_date]
        price_today = price_df[price_df["date"] == str(latest_date)]

        if not foreign_today.empty and not price_today.empty:
            buy_diff = float(foreign_today["diff"].sum())
            vol = pd.to_numeric(price_today.get("Trading_Volume", 0), errors="coerce").sum()
            if vol > 0:
                ratio = buy_diff / vol
                detail["turnover_pct"] = round(ratio, 4)
                if ratio >= FOREIGN_TURNOVER_PCT_HIGH:   # ≥ 50%
                    s2 = 5
                elif ratio >= FOREIGN_TURNOVER_PCT_MID:  # ≥ 30%
                    s2 = 3
    detail["breakdown"]["買超佔成交比"] = s2
    score += s2

    # ── 指標3：外資持股比例上升趨勢（最高 5 分）
    s3 = 0
    if not shareholding_df.empty:
        sh = shareholding_df.copy()
        sh["ForeignInvestmentSharesRatio"] = pd.to_numeric(
            sh.get("ForeignInvestmentSharesRatio", 0), errors="coerce"
        )
        sh = sh.sort_values("date")
        if len(sh) >= 5:
            recent = sh.tail(FOREIGN_HOLDING_WINDOW)
            slope = _linear_slope(recent["ForeignInvestmentSharesRatio"].values)
            detail["holding_rising"] = slope > 0
            if slope > FOREIGN_HOLDING_SLOPE_TOP:    # > 1.5
                s3 = 5
            elif slope > FOREIGN_HOLDING_SLOPE_MID:  # > 0
                s3 = 3
    detail["breakdown"]["持股比例上升"] = s3
    score += s3

    return {"score": min(score, 20), "detail": detail}


# ── B. 投信認養（滿分 20）────────────────────────────────────

def score_trust(institutional_df: pd.DataFrame,
                price_df: pd.DataFrame,
                total_shares: Optional[float] = None) -> dict:
    """
    投信認養評分。
    total_shares：流通在外股數（張），用來計算持股佔流通比
    """
    score = 0
    detail = {
        "consec_days": 0,
        "holding_pct": 0.0,
        "pullback_buy": False,
        "cumulative_diff": 0,
        "breakdown": {}
    }

    if institutional_df.empty:
        return {"score": 0, "detail": detail}

    trust = institutional_df[
        institutional_df["name"].str.contains("投信", na=False)
    ].copy()
    trust = trust.sort_values("date").reset_index(drop=True)

    if trust.empty:
        return {"score": 0, "detail": detail}

    trust["diff"] = pd.to_numeric(trust.get("diff", 0), errors="coerce").fillna(0)

    # ── 指標1：投信連買天數（最高 10 分）
    consec = _count_consecutive_buy(trust["diff"])
    detail["consec_days"] = consec
    detail["cumulative_diff"] = int(trust.tail(consec)["diff"].sum()) if consec > 0 else 0

    if consec >= TRUST_CONSEC_DAYS_TOP:    # ≥ 5 天
        s1 = 10
    elif consec >= TRUST_CONSEC_DAYS_HIGH: # ≥ 3 天
        s1 = 7
    elif consec >= TRUST_CONSEC_DAYS_MID:  # ≥ 1 天
        s1 = 3
    else:
        s1 = 0
    detail["breakdown"]["連買天數"] = s1
    score += s1

    # ── 指標2：累積持股佔流通比（最高 15 分）
    s2 = 0
    if total_shares and total_shares > 0:
        cumulative = trust["diff"].sum()
        pct = cumulative / total_shares
        detail["holding_pct"] = round(pct, 6)
        if pct >= TRUST_HOLDING_PCT_HIGH:  # ≥ 20%
            s2 = 15
        elif pct >= TRUST_HOLDING_PCT_MID: # ≥ 10%
            s2 = 10
        elif pct >= TRUST_HOLDING_PCT_LOW: # ≥  5%
            s2 = 5
    detail["breakdown"]["持股佔流通比"] = s2
    score += s2

    # ── 指標3：拉回不賣（護盤訊號，最高 10 分）
    s3 = 0
    if not price_df.empty and len(trust) >= 2:
        pr = prepare_price_df(price_df)
        trust["date"] = pd.to_datetime(trust["date"])
        pr["date"] = pd.to_datetime(pr["date"])
        merged = trust.merge(
            pr[["date", "close"]].rename(columns={"close": "price_close"}),
            on="date", how="inner"
        ).sort_values("date")

        pullback_days = 0
        for i in range(1, len(merged)):
            row = merged.iloc[i]
            prev = merged.iloc[i-1]
            price_chg = (row["price_close"] - prev["price_close"]) / prev["price_close"]
            if price_chg <= TRUST_PULLBACK_DROP_PCT and row["diff"] > 0:
                pullback_days += 1

        if pullback_days >= 2:
            s3 = 10
            detail["pullback_buy"] = True
        elif pullback_days == 1:
            s3 = 5
    detail["breakdown"]["拉回不賣"] = s3
    score += s3

    return {"score": min(score, 20), "detail": detail}


# ── C. 主力分點集中（滿分 20，Sprint 3）──────────────────────

def score_broker(broker_df: pd.DataFrame,
                  price_df: pd.DataFrame = pd.DataFrame(),
                  lookback_days: int = 10) -> dict:
    """
    C 面向：主力分點集中評分（Sprint 3 正式啟用）。
    broker_df：TaiwanStockTradingDailyReport 多日原始分點資料（未聚合）
    price_df ：個股日K，用於判斷量增不漲
    """
    return score_broker_full(broker_df, price_df, lookback_days)


# ── D. 技術面（滿分 20）─────────────────────────────────────

def score_technical(price_df: pd.DataFrame) -> dict:
    """技術面評分"""
    score = 0
    detail = {"breakdown": {}}

    if price_df.empty or len(price_df) < 20:
        return {"score": 0, "detail": detail}

    tech = get_technical_summary(price_df)
    if not tech["valid"]:
        return {"score": 0, "detail": detail}

    # ── 指標1：均線多頭排列（最高 6 分）
    if tech["full_bull"]:
        s1 = 6
    elif tech["partial_bull"]:
        s1 = 3
    else:
        s1 = 0
    detail["breakdown"]["均線多頭"] = s1
    score += s1

    # ── 指標2：RSI 甜蜜區間（最高 5 分，過熱扣分）
    rsi = tech["rsi"]
    if rsi < 0:
        s2 = 0
    elif RSI_SWEET_LOW <= rsi <= RSI_SWEET_HIGH:
        s2 = 5
    elif rsi > RSI_OVERBOUGHT:
        s2 = -3   # 過熱扣分
    elif rsi > RSI_SWEET_HIGH:
        s2 = 1    # 偏強但不過熱
    elif 40 <= rsi < RSI_SWEET_LOW:
        s2 = 2    # 回升中但未進甜蜜區
    else:
        s2 = 0
    detail["breakdown"]["RSI甜蜜區"] = s2
    detail["rsi"] = rsi
    score += s2

    # ── 指標3：MACD 黃金交叉（最高 4 分）
    if tech["macd_golden_cross"]:
        s3 = 4
    elif tech["macd_above_zero"]:
        s3 = 2    # 柱狀圖在零軸上，多頭延續
    else:
        s3 = 0
    detail["breakdown"]["MACD"] = s3
    score += s3

    # ── 指標4：突破前高 + 量能配合（最高 10 分）
    s4 = 10 if tech["volume_breakout"] else 0
    detail["breakdown"]["量能突破"] = s4
    detail["volume_ratio"] = tech["volume_ratio"]
    score += s4

    return {"score": min(max(score, 0), 20), "detail": detail}


# ── E. 基本面（滿分 20）─────────────────────────────────────

def score_fundamental(revenue_df: pd.DataFrame,
                       financial_df: pd.DataFrame) -> dict:
    """基本面評分"""
    score = 0
    detail = {"breakdown": {}, "revenue_growth_months": 0}

    # ── 指標1：月營收 YoY 連續正成長（最高 10 分）
    s1 = 0
    growth_months = 0
    if not revenue_df.empty:
        rev = revenue_df.copy()
        rev["revenue"] = pd.to_numeric(rev.get("revenue", 0), errors="coerce")
        rev["date"] = pd.to_datetime(rev["date"])
        rev = rev.sort_values("date")

        if len(rev) >= REVENUE_GROWTH_MONTHS + 1:
            yoy_positive = 0
            # 計算每個月的 YoY 成長
            rev_list = rev["revenue"].tolist()
            for i in range(len(rev_list) - 1, max(len(rev_list) - 13, 0), -1):
                if i < 12:
                    break
                yoy = (rev_list[i] - rev_list[i-12]) / abs(rev_list[i-12]) \
                      if rev_list[i-12] != 0 else 0
                if yoy > 0:
                    yoy_positive += 1
                else:
                    break  # 連續中斷就停止計數

            growth_months = yoy_positive
            detail["revenue_growth_months"] = growth_months
            if growth_months >= REVENUE_GROWTH_MONTHS_BONUS:
                s1 = 10
            elif growth_months >= REVENUE_GROWTH_MONTHS:
                s1 = 8
            elif growth_months >= 1:
                s1 = 3

            # 檢查月營收是否創歷史新高
            detail["revenue_record_high"] = float(rev["revenue"].iloc[-1]) >= float(rev["revenue"].max())

    detail["breakdown"]["月營收連成長"] = s1
    score += s1

    # ── 指標2：近2季 EPS 成長（最高 7 分）
    s2 = 0
    if not financial_df.empty:
        fin = financial_df.copy()
        eps_df = fin[fin.get("type", fin.get("origin_name", "")) == "EPS"].copy() \
                 if "type" in fin.columns else pd.DataFrame()

        if eps_df.empty and "type" in fin.columns:
            eps_df = fin[fin["type"].str.upper() == "EPS"].copy()

        if not eps_df.empty:
            eps_df["value"] = pd.to_numeric(eps_df["value"], errors="coerce")
            eps_df = eps_df.sort_values("date")
            eps_vals = eps_df["value"].dropna().tolist()
            if len(eps_vals) >= 3:
                # 近兩季 EPS 季增
                q1_growth = (eps_vals[-1] - eps_vals[-2]) / abs(eps_vals[-2]) \
                            if eps_vals[-2] != 0 else 0
                q2_growth = (eps_vals[-2] - eps_vals[-3]) / abs(eps_vals[-3]) \
                            if eps_vals[-3] != 0 else 0
                avg_growth = (q1_growth + q2_growth) / 2
                detail["eps_avg_growth"] = round(avg_growth, 4)
                if avg_growth >= EPS_GROWTH_HIGH:
                    s2 = 7
                elif avg_growth >= EPS_GROWTH_MID:
                    s2 = 5
                elif avg_growth > 0:
                    s2 = 2

    detail["breakdown"]["EPS成長"] = s2
    score += s2

    # ── 指標3：毛利率改善（最高 10 分）
    s3 = 0
    if not financial_df.empty and "type" in financial_df.columns:
        gpm_df = financial_df[
            financial_df["type"].str.contains("GrossProfitMargin|毛利率", na=False, regex=True)
        ].copy()
        if not gpm_df.empty:
            gpm_df["value"] = pd.to_numeric(gpm_df["value"], errors="coerce")
            gpm_df = gpm_df.sort_values("date")
            gpm_vals = gpm_df["value"].dropna().tolist()
            if len(gpm_vals) >= 2 and gpm_vals[-1] > gpm_vals[-2]:
                s3 = 10
                detail["gpm_improving"] = True

    detail["breakdown"]["毛利率改善"] = s3
    score += s3

    return {"score": min(score, 20), "detail": detail}


# ── 標記（加乘訊號，不計分）──────────────────────────────────

def generate_tags(a_detail: dict, b_detail: dict, e_detail: dict) -> list[str]:
    """
    根據各面向明細，產生加乘標記列表。
    """
    tags = []

    # 法人共識：外資+投信同時連買 N 天以上
    foreign_days = a_detail.get("consec_days", 0)
    trust_days = b_detail.get("consec_days", 0)
    if foreign_days >= TAG_CONSENSUS_DAYS and trust_days >= TAG_CONSENSUS_DAYS:
        tags.append("★法人共識")

    # 投信認養：投信連買 10 日以上
    if trust_days >= 10:
        tags.append("★投信深度認養")
    elif trust_days >= 5:
        tags.append("★投信認養中")

    # 投信護盤
    if b_detail.get("pullback_buy"):
        tags.append("★投信護盤")

    # 基本面突破
    if e_detail.get("revenue_record_high"):
        tags.append("★營收創高")

    growth_m = e_detail.get("revenue_growth_months", 0)
    if growth_m >= 6:
        tags.append("★營收連6月成長")

    return tags


# ── 工具函式 ──────────────────────────────────────────────────

def _count_consecutive_buy(diff_series: pd.Series) -> int:
    """
    從最新一筆往回算，計算連續買超（diff > 0）的天數。
    """
    values = diff_series.dropna().tolist()[::-1]  # 反轉，從最新開始
    count = 0
    for v in values:
        if v > 0:
            count += 1
        else:
            break
    return count


def _linear_slope(values: np.ndarray) -> float:
    """計算一維陣列的線性回歸斜率（用於判斷趨勢方向）"""
    if len(values) < 2:
        return 0.0
    x = np.arange(len(values))
    try:
        slope = np.polyfit(x, values, 1)[0]
        return float(slope)
    except Exception:
        return 0.0


# ── 主評分入口 ────────────────────────────────────────────────

def score_stock(
    stock_id: str,
    price_df: pd.DataFrame,
    institutional_df: pd.DataFrame,
    revenue_df: pd.DataFrame,
    financial_df: pd.DataFrame,
    shareholding_df: pd.DataFrame = pd.DataFrame(),
    broker_df: pd.DataFrame = pd.DataFrame(),
    holding_df: pd.DataFrame = pd.DataFrame(),       # Sprint 2：股權分散表
    margin_history_df: pd.DataFrame = pd.DataFrame(), # Sprint 2：融資券歷史
    total_shares: Optional[float] = None,
) -> dict:
    """
    對單支股票執行全面評分，回傳結構化結果。

    Sprint 2 新增參數：
      holding_df        TaiwanStockHoldingSharesPer（大戶持股週變化）
      margin_history_df TaiwanStockMarginPurchaseShortSale（融資券趨勢）

    回傳格式：
    {
        "stock_id":     "2454",
        "total_score":  72,
        "max_score":    98,   # Sprint 2: 80 + 18 bonus
        "pct":          0.73,
        "A_foreign":    {"score": 18, "detail": {...}},
        "B_trust":      {"score": 16, "detail": {...}},
        "C_broker":     {"score": 0,  "detail": {...}},   # Sprint 3
        "D_technical":  {"score": 15, "detail": {...}},
        "E_fundamental":{"score": 16, "detail": {...}},
        "S2_holding":   {"score": 7,  "detail": {...}},   # Sprint 2 新增
        "S2_margin":    {"score": 5,  "detail": {...}},   # Sprint 2 新增
        "tags":         ["★法人共識", "★籌碼集中"],
        "margin_analysis": {...},
        "error":        None
    }
    """
    try:
        # ── Sprint 1 四大面向 ────────────────────────────────
        # 嘗試從股權分散表取得流通張數（若未傳入 total_shares）
        if total_shares is None and not holding_df.empty:
            total_shares = get_total_shares_from_holding(holding_df)

        result_a = score_foreign(institutional_df, shareholding_df, price_df)
        result_b = score_trust(institutional_df, price_df, total_shares)
        result_c = score_broker(broker_df, price_df)   # Sprint 3：正式啟用
        result_d = score_technical(price_df)
        result_e = score_fundamental(revenue_df, financial_df)

        # ── Sprint 2 新增：股權分散 + 融資券 ─────────────────
        result_s2_holding = score_shareholding(holding_df)

        margin_analysis = analyze_margin(margin_history_df)
        result_s2_margin = score_margin(margin_analysis)

        # ── 加總 ─────────────────────────────────────────────
        base_score = (result_a["score"] + result_b["score"] +
                      result_c["score"] +
                      result_d["score"] + result_e["score"])
        bonus_score = result_s2_holding["score"] + result_s2_margin["score"]
        total = max(base_score + bonus_score, 0)

        # 新滿分計算：
        #   A(20) + B(10+15+10=35→上限20) + C(20) + D(6+5+4+10=25→上限20+5超出原上限)
        #   實際各面向仍各自 min(...,20) 上限
        #   A(20)+B(20)+C(20)+D(20)+E(20)+股權(25)+融資(13) = 138
        # 候選門檻 65% = 90 分；強烈關注 80% = 110 分
        max_score = 138
        pct = round(total / max_score, 4)

        # ── 標記 ─────────────────────────────────────────────
        tags = generate_tags(result_a["detail"], result_b["detail"], result_e["detail"])

        # Sprint 2 標記
        holding_trend = result_s2_holding["detail"].get("trend", {})
        if holding_trend.get("concentration"):
            tags.append("★籌碼集中")
        if holding_trend.get("big_rising") and holding_trend.get("small_declining"):
            tags.append("★大戶接手")
        if margin_analysis.get("chip_clean"):
            tags.append("★籌碼乾淨")
        if margin_analysis.get("short_squeeze_potential"):
            tags.append("★軋空潛力")
        if margin_analysis.get("margin_declining"):
            tags.append("★融資退場")

        # Sprint 3 新增標記
        c_detail = result_c.get("detail", {})
        broker_analysis = c_detail.get("broker_analysis", {})
        if broker_analysis.get("max_consec_days", 0) >= 5:
            tags.append("★主力連續進場")
        elif broker_analysis.get("max_consec_days", 0) >= 3:
            tags.append("★主力短線介入")
        if broker_analysis.get("silent_accum_days", 0) >= 2:
            tags.append("★主力低調吃貨")
        if c_detail.get("known_brokers"):
            tags.append("★慣用分點現身")

        return {
            "stock_id":        stock_id,
            "total_score":     total,
            "max_score":       max_score,
            "pct":             pct,
            "A_foreign":       result_a,
            "B_trust":         result_b,
            "C_broker":        result_c,
            "D_technical":     result_d,
            "E_fundamental":   result_e,
            "S2_holding":      result_s2_holding,
            "S2_margin":       result_s2_margin,
            "margin_analysis": margin_analysis,
            "tags":            tags,
            "error":           None,
        }

    except Exception as e:
        logger.error(f"評分失敗 {stock_id}：{e}", exc_info=True)
        return {
            "stock_id":        stock_id,
            "total_score":     0,
            "max_score":       118,
            "pct":             0,
            "A_foreign":       {"score": 0, "detail": {}},
            "B_trust":         {"score": 0, "detail": {}},
            "C_broker":        {"score": 0, "detail": {}},
            "D_technical":     {"score": 0, "detail": {}},
            "E_fundamental":   {"score": 0, "detail": {}},
            "S2_holding":      {"score": 0, "detail": {}},
            "S2_margin":       {"score": 0, "detail": {}},
            "margin_analysis": {},
            "tags":            [],
            "error":           str(e),
        }
