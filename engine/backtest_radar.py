# engine/backtest_radar.py
# ============================================================
#  回測雷達：對 N=1/3/5/10/20，從 cache 已批次拉好的歷史資料
#           計算「掃描日 T-N 那一天當下」的榜單，供前端回測 tab 使用。
#
#  輸出兩個東西：
#    1. compute_historical_rankings()
#       → 每個 N 對應 8 個榜單（外資買賣、投信買賣、突破上下、綜合多空）
#         每榜最多 30 個 stock_id。
#    2. compute_opens_history()
#       → 每股過去 N 天開盤價（[T0, T-1, ..., T-N+1]）
#
#  進場價 = opens[N]（T-N 開盤）
#  出場價 = opens[0]（T0 = 掃描日開盤）
#  漲跌 % = (opens[0] - opens[N]) / opens[N]
#
#  注意：T-N 的 breakout 榜「沒有 broker 金額門檻」（因為 broker 資料只在
#  scanner Step 3.5/3.6/3.7/3.8 對特定股票抓，無法回溯整個歷史）。
#  即時綜合 tab 用 5 千萬門檻；回測綜合榜放寬為「qualified up/down」即可。
# ============================================================

import logging
import pandas as pd

from engine.foreign_radar  import compute_foreign_radar, compute_trust_io
from engine.breakout_radar import detect_breakout

logger = logging.getLogger(__name__)

DEFAULT_N_VALUES   = [1, 3, 5, 10, 20]
COMBINED_THRESHOLD_M = 50           # 5 千萬


def compute_historical_rankings(cache,
                                  valid_stock_ids: list,
                                  n_values: list = DEFAULT_N_VALUES) -> dict:
    """
    對每個 N 從 cache 已批次拉好的歷史資料計算 T-N 當時的榜單。

    回傳：
    {
        "trading_dates": [前 21 個交易日 ISO 字串],
        "rankings": {
            "1":  {"foreign_buy":[...],"foreign_sell":[...],
                   "trust_buy":[...],"trust_sell":[...],
                   "breakout_up":[...],"breakout_down":[...],
                   "combined_long":[...],"combined_short":[...]},
            "3": {...}, "5": {...}, "10": {...}, "20": {...}
        }
    }
    """
    inst_hist  = cache.get_institutional_history()
    price_hist = cache.get_price_history()
    if inst_hist.empty or price_hist.empty:
        logger.warning("回測：cache 缺少歷史資料，跳過")
        return {"trading_dates": [], "rankings": {}}

    inst_hist  = inst_hist.copy()
    price_hist = price_hist.copy()
    inst_hist["date"]  = pd.to_datetime(inst_hist["date"])
    price_hist["date"] = pd.to_datetime(price_hist["date"])

    all_dates = sorted(price_hist["date"].unique())
    if len(all_dates) < max(n_values) + 1:
        logger.warning(f"回測：歷史日數不足（{len(all_dates)} < {max(n_values)+1}）")
        return {"trading_dates": [], "rankings": {}}

    trading_dates = [d.strftime("%Y-%m-%d") for d in all_dates[-21:]]

    # 對每股建立索引（避免每次 filter 慢）
    inst_by_stock  = {sid: g for sid, g in inst_hist.groupby("stock_id")}
    price_by_stock = {sid: g for sid, g in price_hist.groupby("stock_id")}

    rankings: dict = {}

    for n in n_values:
        target_date = all_dates[-(n + 1)] if n < len(all_dates) else all_dates[0]

        stock_metrics = []
        for stock_id in valid_stock_ids:
            inst_stock  = inst_by_stock.get(stock_id, pd.DataFrame())
            price_stock = price_by_stock.get(stock_id, pd.DataFrame())
            if price_stock.empty:
                continue

            inst_til  = inst_stock[inst_stock["date"]   <= target_date] \
                            if not inst_stock.empty else pd.DataFrame()
            price_til = price_stock[price_stock["date"] <= target_date]
            if price_til.empty:
                continue

            f_radar  = compute_foreign_radar(inst_til, price_til)
            t_radar  = compute_trust_io(inst_til, price_til)
            breakout = detect_breakout(price_til)

            stock_metrics.append({
                "stock_id":      stock_id,
                "f_amt":         (f_radar.get(str(n)) or {}).get("net_amount_m", 0),
                "t_amt":         (t_radar.get(str(n)) or {}).get("net_amount_m", 0),
                "breakout_up":   bool(breakout.get("qualified_up")),
                "breakout_down": bool(breakout.get("qualified_down")),
            })

        # ── 八榜
        foreign_buy = sorted(
            [m for m in stock_metrics if m["f_amt"] >=  COMBINED_THRESHOLD_M],
            key=lambda m: m["f_amt"], reverse=True,
        )[:30]
        foreign_sell = sorted(
            [m for m in stock_metrics if m["f_amt"] <= -COMBINED_THRESHOLD_M],
            key=lambda m: m["f_amt"],
        )[:30]
        trust_buy = sorted(
            [m for m in stock_metrics if m["t_amt"] >=  COMBINED_THRESHOLD_M],
            key=lambda m: m["t_amt"], reverse=True,
        )[:30]
        trust_sell = sorted(
            [m for m in stock_metrics if m["t_amt"] <= -COMBINED_THRESHOLD_M],
            key=lambda m: m["t_amt"],
        )[:30]
        # breakout：依 (f+t) 為近似「強度」排序，無 broker 金額門檻
        breakout_up   = sorted(
            [m for m in stock_metrics if m["breakout_up"]],
            key=lambda m: m["f_amt"] + m["t_amt"], reverse=True,
        )[:30]
        breakout_down = sorted(
            [m for m in stock_metrics if m["breakout_down"]],
            key=lambda m: m["f_amt"] + m["t_amt"],
        )[:30]
        # combined：三方交集
        t_buy_set  = {m["stock_id"] for m in trust_buy}
        t_sell_set = {m["stock_id"] for m in trust_sell}
        b_up_set   = {m["stock_id"] for m in breakout_up}
        b_down_set = {m["stock_id"] for m in breakout_down}

        combined_long  = [m for m in foreign_buy
                            if m["stock_id"] in t_buy_set and m["stock_id"] in b_up_set][:30]
        combined_short = [m for m in foreign_sell
                            if m["stock_id"] in t_sell_set and m["stock_id"] in b_down_set][:30]

        rankings[str(n)] = {
            "foreign_buy":    [m["stock_id"] for m in foreign_buy],
            "foreign_sell":   [m["stock_id"] for m in foreign_sell],
            "trust_buy":      [m["stock_id"] for m in trust_buy],
            "trust_sell":     [m["stock_id"] for m in trust_sell],
            "breakout_up":    [m["stock_id"] for m in breakout_up],
            "breakout_down":  [m["stock_id"] for m in breakout_down],
            "combined_long":  [m["stock_id"] for m in combined_long],
            "combined_short": [m["stock_id"] for m in combined_short],
        }

        logger.info(f"回測 T-{n:2d}：外資 {len(foreign_buy):2d}/{len(foreign_sell):2d}  "
                    f"投信 {len(trust_buy):2d}/{len(trust_sell):2d}  "
                    f"突破 {len(breakout_up):2d}/{len(breakout_down):2d}  "
                    f"綜合 {len(combined_long):2d}/{len(combined_short):2d}")

    return {"trading_dates": trading_dates, "rankings": rankings}


def compute_opens_history(price_df: pd.DataFrame, days: int = 21) -> list:
    """
    對單支股票回傳近 N 天開盤價列表。
    回傳：[T0, T-1, T-2, ..., T-(days-1)]（idx 0 = 最新一天）
    若 T-N 那天沒交易，該位置以前一個有交易的日期填補（簡化）。
    """
    if price_df is None or price_df.empty:
        return []
    df = price_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df["open"] = pd.to_numeric(df.get("open", 0), errors="coerce")
    df = df.dropna(subset=["open"]).sort_values("date").reset_index(drop=True)
    if df.empty:
        return []

    recent = df.tail(days)
    return [round(float(o), 2) for o in recent["open"].tolist()[::-1]]
