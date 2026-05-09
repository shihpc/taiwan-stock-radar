# engine/etf_flow.py
# ============================================================
#  F 面向：ETF 資金流強度（滿分 20 分）
#
#  主動式 ETF 申購量無直接 API，改用三個代理指標間接衡量：
#    F1. 投信集中買超強度（最高 8 分）
#        ETF 經理人買進反映在投信買超，集中於同一批股票
#        代表 ETF 競爭加碼。
#    F2. 自由流通籌碼收縮訊號（最高 7 分）
#        大戶 + 外資 + 投信三方同步持有上升 = 可交易籌碼減少。
#    F3. 換手率下降但股價上漲（最高 5 分）
#        換手率降低但價格維持或上漲 = 低流動性定價（ETF 鎖碼）。
#
#  注意：F1 需要先用 calc_trust_5d_distribution() 計算全市場分布，
#  再把結果傳給 score_etf_flow()。回測或單股模式無此分布時，
#  F1 自動回 0 分（不會出錯）。
# ============================================================

import logging
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


# ── 全市場分布計算 ────────────────────────────────────────────

def calc_trust_5d_distribution(all_inst_history: pd.DataFrame) -> dict:
    """
    從全市場法人歷史資料計算「近 5 個交易日投信累計買超」的分位數門檻。
    供 F1 比較個股相對位階。

    回傳：
      {"p90": float, "p80": float, "p70": float, "n": int}
        p90 = 前 10% 門檻（張）
        p80 = 前 20% 門檻
        p70 = 前 30% 門檻
        n   = 投信買超為正的股票數
      若資料不足回傳空 dict — 呼叫端應視為「F1 無法評估」。

    只用「買超為正」的股票算分位數，避免被全市場大量賣超污染。
    """
    if all_inst_history is None or all_inst_history.empty:
        return {}
    if "name" not in all_inst_history.columns:
        return {}

    df = all_inst_history.copy()
    df["diff"] = pd.to_numeric(df.get("diff", 0), errors="coerce").fillna(0)
    trust = df[df["name"].str.contains("投信|Trust|trust",
                                          na=False, regex=True)].copy()
    if trust.empty:
        return {}

    trust["date"] = pd.to_datetime(trust["date"])
    latest_dates = sorted(trust["date"].unique())[-5:]
    if len(latest_dates) < 3:   # 至少 3 天才有意義
        return {}

    recent = trust[trust["date"].isin(latest_dates)]
    by_stock = recent.groupby("stock_id")["diff"].sum() / 1000   # 股 → 張
    positives = by_stock[by_stock > 0]
    if len(positives) < 10:
        return {}

    return {
        "p90": float(np.percentile(positives, 90)),
        "p80": float(np.percentile(positives, 80)),
        "p70": float(np.percentile(positives, 70)),
        "n":   int(len(positives)),
    }


# ── F1：投信集中買超強度（8 分）────────────────────────────────

def _score_f1(institutional_df: pd.DataFrame,
              market_dist: dict) -> tuple[int, dict]:
    detail = {"trust_5d_lots": 0.0, "rank": "N/A"}

    if institutional_df.empty or "name" not in institutional_df.columns:
        return 0, detail
    if not market_dist:
        detail["rank"] = "無分布"
        return 0, detail

    df = institutional_df.copy()
    df["diff"] = pd.to_numeric(df.get("diff", 0), errors="coerce").fillna(0)
    trust = df[df["name"].str.contains("投信|Trust|trust",
                                          na=False, regex=True)].copy()
    if trust.empty:
        return 0, detail

    trust["date"] = pd.to_datetime(trust["date"])
    trust = trust.sort_values("date")
    recent = trust.tail(5)
    trust_5d_lots = float(recent["diff"].sum() / 1000)
    detail["trust_5d_lots"] = round(trust_5d_lots, 0)

    if trust_5d_lots <= 0:
        detail["rank"] = "賣超"
        return 0, detail

    p90 = market_dist.get("p90", float("inf"))
    p80 = market_dist.get("p80", float("inf"))
    p70 = market_dist.get("p70", float("inf"))

    if trust_5d_lots >= p90:
        detail["rank"] = "前10%"
        return 8, detail
    if trust_5d_lots >= p80:
        detail["rank"] = "前20%"
        return 5, detail
    if trust_5d_lots >= p70:
        detail["rank"] = "前30%"
        return 2, detail

    detail["rank"] = "<前30%"
    return 0, detail


# ── F2：三方同步加碼（7 分）────────────────────────────────────

def _score_f2(institutional_df: pd.DataFrame,
              shareholding_df: pd.DataFrame,
              holding_trend: dict) -> tuple[int, dict]:
    detail = {
        "big_rising": False,
        "foreign_rising": False,
        "trust_rising": False,
        "count": 0,
    }

    # 大戶上升 — 沿用 shareholding 模組已算好的 trend
    big_rising = bool(
        holding_trend.get("big_rising")
        or holding_trend.get("big_chg_4w", 0) > 0
    )

    # 外資持股比例上升（觀察視窗內最早 vs 最近）
    foreign_rising = False
    if not shareholding_df.empty and \
       "ForeignInvestmentSharesRatio" in shareholding_df.columns:
        sh = shareholding_df.copy()
        sh["ForeignInvestmentSharesRatio"] = pd.to_numeric(
            sh["ForeignInvestmentSharesRatio"], errors="coerce"
        )
        sh = sh.dropna(subset=["ForeignInvestmentSharesRatio"]) \
               .sort_values("date")
        if len(sh) >= 2:
            foreign_rising = float(sh["ForeignInvestmentSharesRatio"].iloc[-1]) \
                             > float(sh["ForeignInvestmentSharesRatio"].iloc[0])

    # 投信累計買超 > 0 視為加碼
    trust_rising = False
    if not institutional_df.empty and "name" in institutional_df.columns:
        inst = institutional_df.copy()
        inst["diff"] = pd.to_numeric(inst.get("diff", 0), errors="coerce").fillna(0)
        trust_rows = inst[inst["name"].str.contains("投信|Trust|trust",
                                                       na=False, regex=True)]
        if not trust_rows.empty:
            trust_rising = float(trust_rows["diff"].sum()) > 0

    detail["big_rising"]     = big_rising
    detail["foreign_rising"] = foreign_rising
    detail["trust_rising"]   = trust_rising
    count = int(big_rising) + int(foreign_rising) + int(trust_rising)
    detail["count"] = count

    if count >= 3:
        return 7, detail
    if count == 2:
        return 4, detail
    if count == 1:
        return 1, detail
    return 0, detail


# ── F3：換手率縮量上漲（5 分）─────────────────────────────────

def _score_f3(price_df: pd.DataFrame) -> tuple[int, dict]:
    """
    對同一支股票，流通股數為常數，換手率均值比 = 成交量均值比，
    所以這裡直接用「近 10 日 / 過去 60 日」成交量均值比代替換手率比。
    """
    detail = {"vol_ratio_10_60": 0.0, "price_chg_10d_pct": 0.0}

    if price_df.empty:
        return 0, detail

    df = price_df.copy()
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date")

    vol_col = "volume" if "volume" in df.columns else "Trading_Volume"
    if vol_col not in df.columns or "close" not in df.columns:
        return 0, detail

    df[vol_col] = pd.to_numeric(df[vol_col], errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna(subset=[vol_col, "close"]).reset_index(drop=True)

    if len(df) < 60:
        return 0, detail

    vol_10 = float(df[vol_col].tail(10).mean())
    vol_60 = float(df[vol_col].tail(60).mean())
    if vol_60 <= 0:
        return 0, detail

    ratio = vol_10 / vol_60
    detail["vol_ratio_10_60"] = round(ratio, 4)

    close_now   = float(df["close"].iloc[-1])
    close_10ago = float(df["close"].iloc[-10])
    if close_10ago <= 0:
        return 0, detail
    price_chg = (close_now - close_10ago) / close_10ago
    detail["price_chg_10d_pct"] = round(price_chg * 100, 2)

    if price_chg <= 0:
        return 0, detail

    if ratio < 0.70:
        return 5, detail
    if ratio < 0.85:
        return 2, detail
    return 0, detail


# ── 主入口 ────────────────────────────────────────────────────

def score_etf_flow(institutional_df: pd.DataFrame,
                   shareholding_df: pd.DataFrame,
                   holding_trend: dict,
                   price_df: pd.DataFrame,
                   market_trust_dist: dict = None) -> dict:
    """
    F 面向：ETF 資金流強度評分（滿分 20 分）。

    參數：
      institutional_df  個股法人歷史
      shareholding_df   個股外資持股比例歷史
      holding_trend     shareholding.analyze_holder_trend 的回傳值
                        （score_shareholding 已算過，scorer.py 會傳入）
      price_df          個股日 K 歷史（至少 60 天才能算 F3）
      market_trust_dist 全市場投信 5 日買超分位數（calc_trust_5d_distribution
                        產生）。傳 None 時 F1 = 0，F2/F3 仍正常運作。
    """
    detail = {"breakdown": {}}

    s1, d1 = _score_f1(institutional_df, market_trust_dist or {})
    detail["breakdown"]["F1_投信集中買超"] = s1
    detail["F1_detail"] = d1

    s2, d2 = _score_f2(institutional_df, shareholding_df, holding_trend or {})
    detail["breakdown"]["F2_籌碼收縮"] = s2
    detail["F2_detail"] = d2

    s3, d3 = _score_f3(price_df)
    detail["breakdown"]["F3_換手率縮量上漲"] = s3
    detail["F3_detail"] = d3

    return {"score": min(s1 + s2 + s3, 20), "detail": detail}
