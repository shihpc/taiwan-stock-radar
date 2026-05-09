# engine/broker_analysis.py
# ============================================================
#  Sprint 3：主力分點集中分析模組
#  資料來源：TaiwanStockTradingDailyReport（Sponsor 限定）
#
#  核心邏輯：
#    1. 分點集中度    → 前3大買超分點佔全日成交比例
#    2. 連續進場天數  → 同一分點連續N日買超
#    3. 低調吃貨型態  → 量增但股價未大漲（主力壓低收集）
#    4. 主力慣用分點  → 特定分點歷史勝率追蹤
#
#  ⚠️  注意事項：
#    - buy/sell 欄位單位為「股」，換算張數需 /1000
#    - 同一分點同天多筆（不同價位），需先按 trader_id 聚合
#    - 全市場單日資料量極大（約 4 分鐘才能下載完），
#      掃描器使用「按股票查詢」模式，每支約 0.5 秒
# ============================================================

import logging
from typing import Optional
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

# ── 主力分點判定改為動態（依個股實際買超分布）────────────────
# 詳見 score_broker_full 中的 has_dominant 邏輯。

# ── 資料前處理 ────────────────────────────────────────────────

def aggregate_broker_by_trader(df: pd.DataFrame) -> pd.DataFrame:
    """
    將原始分點資料（每分點多個價位各一筆）
    聚合為每分點每日一筆：buy 和 sell 加總，計算 diff 和 diff_lots（張）。

    輸入欄位：securities_trader_id, securities_trader, buy, sell, date, stock_id
    輸出新增：diff（股）, buy_lots, sell_lots, diff_lots（張）
    """
    if df.empty:
        return df

    df = df.copy()
    for col in ["buy", "sell"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    agg = df.groupby(
        ["date", "stock_id", "securities_trader_id", "securities_trader"],
        as_index=False
    ).agg(
        buy=("buy", "sum"),
        sell=("sell", "sum"),
    )
    agg["diff"]      = agg["buy"] - agg["sell"]          # 股
    agg["buy_lots"]  = (agg["buy"]  / 1000).round(0).astype(int)
    agg["sell_lots"] = (agg["sell"] / 1000).round(0).astype(int)
    agg["diff_lots"] = (agg["diff"] / 1000).round(0).astype(int)
    agg["date"]      = pd.to_datetime(agg["date"])

    return agg.sort_values(["date", "diff_lots"], ascending=[True, False]) \
              .reset_index(drop=True)


# ── 單日分析 ──────────────────────────────────────────────────

def analyze_single_day(day_df: pd.DataFrame,
                        price_df: pd.DataFrame = pd.DataFrame()) -> dict:
    """
    分析單日分點資料，回傳當日關鍵指標。

    回傳：
    {
        "top3_buy_lots":      int,    # 前3大買超分點合計（張）
        "top3_concentration": float,  # 前3大買超 / 全日總買 （比例）
        "top3_brokers":       list,   # 前3大買超分點名稱
        "total_buy_lots":     int,    # 全日總買（張）
        "total_sell_lots":    int,    # 全日總賣（張）
        "net_lots":           int,    # 全日淨買（張）
        "silent_accum":       bool,   # 量增不漲吃貨型態
    }
    """
    empty = {
        "top3_buy_lots": 0, "top3_concentration": 0.0, "top3_brokers": [],
        "total_buy_lots": 0, "total_sell_lots": 0, "net_lots": 0,
        "silent_accum": False,
    }

    if day_df.empty:
        return empty

    agg = aggregate_broker_by_trader(day_df)
    if agg.empty:
        return empty

    total_buy  = int(agg["buy_lots"].sum())
    total_sell = int(agg["sell_lots"].sum())
    net        = int(agg["diff_lots"].sum())

    # 前3大淨買超分點
    top3 = agg.nlargest(3, "diff_lots")
    top3_buy   = int(top3["diff_lots"].clip(lower=0).sum())
    top3_conc  = top3_buy / total_buy if total_buy > 0 else 0.0
    top3_names = top3["securities_trader"].tolist()

    # 量增不漲：若有股價資料，判斷今日收盤漲幅 < 1% 但買超量大
    silent_accum = False
    if not price_df.empty and top3_buy > 100:
        price_df_c = price_df.copy()
        price_df_c["date"] = pd.to_datetime(price_df_c["date"])
        today_date = agg["date"].max()
        yesterday = price_df_c[price_df_c["date"] < today_date].tail(1)
        today_price = price_df_c[price_df_c["date"] == today_date]
        if not yesterday.empty and not today_price.empty:
            prev_close = float(yesterday["close"].values[0])
            today_close_col = "close"
            today_close = float(today_price[today_close_col].values[0])
            if prev_close != 0:
                chg_pct = (today_close - prev_close) / prev_close
            else:
                chg_pct = 0.0
            # 漲幅 < 1% 但分點淨買超 > 100 張 → 低調吃貨
            silent_accum = bool(chg_pct < 0.01 and top3_buy > 100)

    return {
        "top3_buy_lots":      top3_buy,
        "top3_concentration": round(top3_conc, 4),
        "top3_brokers":       top3_names,
        "total_buy_lots":     total_buy,
        "total_sell_lots":    total_sell,
        "net_lots":           net,
        "silent_accum":       silent_accum,
    }


# ── 多日連續分析 ──────────────────────────────────────────────

def analyze_broker_consecutive(
    multi_day_df: pd.DataFrame,
    price_df: pd.DataFrame = pd.DataFrame(),
    lookback_days: int = 10,
) -> dict:
    """
    分析近 N 日的分點連續進場狀況。

    multi_day_df：包含多日原始分點資料（未聚合）
    回傳：
    {
        "max_consec_days":     int,    # 最長連續進場天數（單一分點）
        "top_consec_broker":   str,    # 連續進場最久的分點名稱
        "consec_detail":       dict,   # 每個分點的連續天數
        "avg_daily_concentration": float, # 近N日平均分點集中度
        "silent_accum_days":   int,    # 出現量增不漲的天數
        "days_analyzed":       int,    # 實際分析天數
        "daily_analysis":      list,   # 每日分析明細
    }
    """
    empty = {
        "max_consec_days": 0, "top_consec_broker": "",
        "consec_detail": {}, "avg_daily_concentration": 0.0,
        "silent_accum_days": 0, "days_analyzed": 0, "daily_analysis": [],
    }

    if multi_day_df.empty:
        return empty

    agg_all = aggregate_broker_by_trader(multi_day_df)
    if agg_all.empty:
        return empty

    dates_sorted = sorted(agg_all["date"].unique())[-lookback_days:]
    daily_results = []

    for d in dates_sorted:
        day_data = agg_all[agg_all["date"] == d]
        day_price = price_df.copy() if not price_df.empty else pd.DataFrame()
        result = analyze_single_day(day_data, day_price)
        result["date"] = str(d.date())
        daily_results.append(result)

    # 統計每個分點的連續進場天數
    broker_consec: dict[str, int] = {}
    broker_name_map: dict[str, str] = {}

    for day_r in reversed(daily_results):   # 從最新日往回算
        for broker in agg_all[agg_all["date"] == pd.Timestamp(day_r["date"])
                               ]["securities_trader_id"].unique():
            broker_day = agg_all[
                (agg_all["date"] == pd.Timestamp(day_r["date"])) &
                (agg_all["securities_trader_id"] == broker)
            ]
            if not broker_day.empty:
                net = int(broker_day["diff_lots"].sum())
                name = broker_day["securities_trader"].iloc[0]
                broker_name_map[broker] = name
                if net > 0:
                    broker_consec[broker] = broker_consec.get(broker, 0) + 1
                else:
                    # 一旦出現賣超，連續中斷
                    if broker in broker_consec:
                        broker_consec[broker] = 0

    # 找最長連續進場
    max_consec = 0
    top_broker_id = ""
    for bid, days in broker_consec.items():
        if days > max_consec:
            max_consec = days
            top_broker_id = bid

    top_broker_name = broker_name_map.get(top_broker_id, "")

    avg_conc = float(np.mean([r["top3_concentration"] for r in daily_results])) \
               if daily_results else 0.0
    silent_days = sum(1 for r in daily_results if r["silent_accum"])

    # ── 動態主導分點：該股近 N 日累計淨買超最大的分點 ──
    broker_total = agg_all.groupby(
        ["securities_trader_id", "securities_trader"], as_index=False
    )["diff_lots"].sum().sort_values("diff_lots", ascending=False)

    if not broker_total.empty and broker_total.iloc[0]["diff_lots"] > 0:
        top_buyer_id   = broker_total.iloc[0]["securities_trader_id"]
        top_buyer_name = broker_total.iloc[0]["securities_trader"]
        top_buyer_lots = int(broker_total.iloc[0]["diff_lots"])
        # 該分點佔全市場「總正向買超」的比例
        all_pos_net = float(broker_total[broker_total["diff_lots"] > 0]["diff_lots"].sum()) or 1.0
        top_buyer_share = top_buyer_lots / all_pos_net
    else:
        top_buyer_id   = ""
        top_buyer_name = ""
        top_buyer_lots = 0
        top_buyer_share = 0.0

    # 連最久分點的累計買超（用於判定持續性）
    if top_broker_id:
        top_consec_lots = int(agg_all[
            agg_all["securities_trader_id"] == top_broker_id
        ]["diff_lots"].sum())
    else:
        top_consec_lots = 0

    # 分點連續天數明細（只保留 > 0 的）
    consec_detail = {
        broker_name_map.get(bid, bid): days
        for bid, days in broker_consec.items() if days > 0
    }
    # 只取前10個
    consec_detail = dict(sorted(
        consec_detail.items(), key=lambda x: x[1], reverse=True
    )[:10])

    return {
        "max_consec_days":          max_consec,
        "top_consec_broker":        top_broker_name,
        "top_consec_lots":          top_consec_lots,    # 連最久分點累計買超（張）
        "top_buyer_name":           top_buyer_name,     # 累計買超第一名
        "top_buyer_lots":           top_buyer_lots,     # 第一名累計買超（張）
        "top_buyer_share":          round(top_buyer_share, 4),  # 占全部正買超比例
        "consec_detail":            consec_detail,
        "avg_daily_concentration":  round(avg_conc, 4),
        "silent_accum_days":        silent_days,
        "days_analyzed":            len(daily_results),
        "daily_analysis":           daily_results,
    }


# ── 評分函式（C 面向正式啟用）─────────────────────────────────

def score_broker_full(
    broker_multi_df: pd.DataFrame,
    price_df: pd.DataFrame = pd.DataFrame(),
    lookback_days: int = 10,
) -> dict:
    """
    C 面向完整評分（Sprint 3 啟用，滿分 20 分）。

    計分規則：
      指標1：前3分點集中度（最高 8 分）
        ≥ 35%  → 8 分
        ≥ 20%  → 5 分
        ≥ 10%  → 2 分

      指標2：同一分點連續進場天數（最高 7 分）
        ≥ 5 日 → 7 分
        ≥ 3 日 → 4 分
        ≥ 2 日 → 2 分

      指標3：量增不漲（低調吃貨，最高 5 分）
        ≥ 3 天出現 → 5 分
        ≥ 1 天出現 → 2 分

      加分（不超過面向滿分）：
        出現已知主力慣用分點 → +2 分
    """
    if broker_multi_df is None or broker_multi_df.empty:
        return {
            "score": 0,
            "detail": {
                "breakdown": {"資料不足": 0},
                "note": "無分點資料，請確認 Sponsor 方案已啟用",
                "broker_analysis": {}
            }
        }

    score = 0
    detail = {"breakdown": {}}

    analysis = analyze_broker_consecutive(
        broker_multi_df, price_df, lookback_days
    )
    detail["broker_analysis"] = {
        "max_consec_days":         analysis["max_consec_days"],
        "top_consec_broker":       analysis["top_consec_broker"],
        "top_consec_lots":         analysis.get("top_consec_lots", 0),
        "top_buyer_name":          analysis.get("top_buyer_name", ""),
        "top_buyer_lots":          analysis.get("top_buyer_lots", 0),
        "top_buyer_share":         analysis.get("top_buyer_share", 0.0),
        "avg_daily_concentration": analysis["avg_daily_concentration"],
        "silent_accum_days":       analysis["silent_accum_days"],
        "days_analyzed":           analysis["days_analyzed"],
        "consec_detail":           analysis["consec_detail"],
    }

    # 取最新一日的集中度代表值
    if analysis["daily_analysis"]:
        latest_day = analysis["daily_analysis"][-1]
        latest_conc = latest_day["top3_concentration"]
    else:
        latest_conc = analysis["avg_daily_concentration"]

    # ── 動態判定「主導分點」：依個股實際情況，不再用寫死清單 ──
    # 滿足任一條件即視為有主導分點：
    #   1. 連最久分點 ≥ 3 天
    #   2. 第一名分點累計買超佔比 > 25%（單股一枝獨秀）
    #   3. 連最久分點累計淨買超 > 1000 張（絕對量大）
    top_buyer_share = analysis.get("top_buyer_share", 0.0)
    top_consec_lots = analysis.get("top_consec_lots", 0)
    has_dominant = (
        analysis["max_consec_days"] >= 3 or
        top_buyer_share > 0.25 or
        top_consec_lots > 1000
    )
    # 命中時把主導分點名稱列出來（顯示用）
    if has_dominant:
        names = []
        if analysis.get("top_consec_broker"):
            names.append(analysis["top_consec_broker"])
        if analysis.get("top_buyer_name") and analysis["top_buyer_name"] not in names:
            names.append(analysis["top_buyer_name"])
        known_found = names
    else:
        known_found = []
    has_known = has_dominant   # 沿用變數名給後面使用

    # ── 指標1：分點集中度（8 分）
    if latest_conc >= 0.35:
        s1 = 8
    elif latest_conc >= 0.20:
        s1 = 5
    elif latest_conc >= 0.10:
        s1 = 2
    else:
        s1 = 0
    detail["breakdown"]["分點集中度"] = s1
    score += s1

    # ── 指標2：連續進場天數（7 分）
    consec = analysis["max_consec_days"]
    if consec >= 5:
        s2 = 7
    elif consec >= 3:
        s2 = 4
    elif consec >= 2:
        s2 = 2
    else:
        s2 = 0
    detail["breakdown"]["連續進場天數"] = s2
    score += s2

    # ── 指標3：量增不漲（5 分）
    silent = analysis["silent_accum_days"]
    if silent >= 3:
        s3 = 5
    elif silent >= 1:
        s3 = 2
    else:
        s3 = 0
    detail["breakdown"]["量增不漲"] = s3
    score += s3

    # ── 加分：已知主力分點（+2）
    s4 = 2 if has_known else 0
    detail["breakdown"]["主力慣用分點"] = s4
    detail["known_brokers"] = known_found
    score += s4

    return {
        "score": min(score, 20),
        "detail": detail,
    }


# ── 前三大金額買賣超分點（外資雷達 tab 用）────────────────────

def compute_top3_brokers(broker_df: pd.DataFrame,
                          price_df: pd.DataFrame = pd.DataFrame(),
                          direction: str = "auto") -> list:
    """
    從多日 broker 原始資料中找該股票前三大「金額」買賣超分點。

    direction:
      "buy"   → 取淨買超「金額」前 3 大分點
      "sell"  → 取淨賣超「金額」前 3 大分點（取最負）
      "auto"  → 依資料總淨額方向自動判斷

    金額計算：分點 N 日累計淨張 × 該股票同期間加權均價（簡化估算）。

    回傳：list of dict，最多 3 筆
      [{"name":"摩根...", "lots":5000, "amount_m":320.5}, ...]
    """
    if broker_df is None or broker_df.empty:
        return []

    agg = aggregate_broker_by_trader(broker_df)
    if agg.empty:
        return []

    by_trader = agg.groupby(
        ["securities_trader_id", "securities_trader"], as_index=False
    ).agg(
        buy_lots=("buy_lots",   "sum"),
        sell_lots=("sell_lots", "sum"),
        diff_lots=("diff_lots", "sum"),
    )

    # 期間加權均價（用 broker 涵蓋日期的 sum money / sum volume）
    avg_vwap = 0.0
    if not price_df.empty:
        pr = price_df.copy()
        pr["date"]  = pd.to_datetime(pr["date"])
        pr["money"] = pd.to_numeric(pr.get("Trading_money",  0), errors="coerce").fillna(0)
        pr["vol"]   = pd.to_numeric(pr.get("Trading_Volume", 0), errors="coerce").fillna(0)
        broker_dates = agg["date"].unique()
        pr_in = pr[pr["date"].isin(broker_dates)]
        total_money = float(pr_in["money"].sum())
        total_vol   = float(pr_in["vol"].sum())
        if total_vol > 0:
            avg_vwap = total_money / total_vol

    by_trader["amount_m"] = (
        by_trader["diff_lots"] * 1000 * avg_vwap / 1_000_000
    )

    if direction == "auto":
        total_diff = float(by_trader["diff_lots"].sum())
        direction = "buy" if total_diff >= 0 else "sell"

    if direction == "buy":
        sorted_df = by_trader.sort_values("diff_lots", ascending=False).head(3)
    else:
        sorted_df = by_trader.sort_values("diff_lots", ascending=True).head(3)

    out = []
    for _, row in sorted_df.iterrows():
        diff = int(row["diff_lots"])
        # 方向不一致就停（買超榜上若出現負淨 = 沒有第三名買超分點）
        if direction == "buy"  and diff <= 0: break
        if direction == "sell" and diff >= 0: break
        out.append({
            "name":     str(row["securities_trader"]),
            "lots":     diff,
            "amount_m": round(float(row["amount_m"]), 2),
        })
    return out


# ── 分點快取管理器（解決資料量大的問題）─────────────────────

class BrokerDataCache:
    """
    分點資料本地快取管理器。

    分點資料每支股票每次約 0.3–0.5 秒，全市場一天約 4 分鐘。
    使用快取避免重複拉取，支援跨日累積。

    使用方式：
        cache = BrokerDataCache()
        df = cache.get(stock_id, date)   # 自動判斷快取或 API
    """

    def __init__(self, cache_dir: str = "data/broker_cache"):
        import os
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self._memory: dict[str, pd.DataFrame] = {}

    def _key(self, stock_id: str, date: str) -> str:
        return f"{stock_id}_{date.replace('-', '')}"

    def _filepath(self, stock_id: str, date: str) -> str:
        import os
        # 優先用 parquet，若無 pyarrow 則退用 csv
        base = os.path.join(self.cache_dir, self._key(stock_id, date))
        try:
            import pyarrow  # noqa
            return base + ".parquet"
        except ImportError:
            return base + ".csv"

    def _save_df(self, filepath: str, df: pd.DataFrame):
        try:
            if filepath.endswith(".parquet"):
                df.to_parquet(filepath, index=False)
            else:
                df.to_csv(filepath, index=False)
        except Exception as e:
            logger.debug(f"快取儲存失敗：{e}")

    def _load_df(self, filepath: str) -> Optional[pd.DataFrame]:
        import os
        if not os.path.exists(filepath):
            return None
        try:
            if filepath.endswith(".parquet"):
                return pd.read_parquet(filepath)
            else:
                return pd.read_csv(filepath)
        except Exception:
            return None

    def get(self, stock_id: str, date: str,
            fetcher_fn=None) -> pd.DataFrame:
        """
        取得分點資料，優先從記憶體快取，其次磁碟，最後 API。
        """
        key = self._key(stock_id, date)

        if key in self._memory:
            return self._memory[key]

        fpath = self._filepath(stock_id, date)
        cached = self._load_df(fpath)
        if cached is not None:
            self._memory[key] = cached
            return cached

        if fetcher_fn is None:
            return pd.DataFrame()

        try:
            df = fetcher_fn(stock_id, date)
            if not df.empty:
                self._memory[key] = df
                self._save_df(fpath, df)
            return df
        except Exception as e:
            logger.error(f"分點資料 API 失敗 {stock_id} {date}：{e}")
            return pd.DataFrame()

    def get_multi_days(self, stock_id: str, dates: list,
                       fetcher_fn=None) -> pd.DataFrame:
        """
        取得多日分點資料並合併，用於連續進場分析。
        """
        frames = []
        for d in dates:
            df = self.get(stock_id, d, fetcher_fn)
            if not df.empty:
                frames.append(df)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def memory_size(self) -> int:
        """回傳記憶體快取的股票數量"""
        return len(self._memory)
