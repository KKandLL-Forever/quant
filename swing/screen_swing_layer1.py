"""
screen_swing_layer1.py — 波段选股策略「第一层规则硬筛」历史扫描

目的：
  把波段策略的确定性条件（趋势向上 + 回调企稳/放量突破 + 僵尸股过滤）量化成规则，
  全市场历史扫描，输出每日 / 每年候选数量，用于判断后续是否有足够样本上机器学习排序层。

策略两种进场（信号当日盘后确认，次日开盘买，与 2lb 流程一致）：
  A 回调企稳：上升趋势中回踩 MA20 不破、当日收阳、距近 10 日高点回落 3%~25%
  B 放量突破：收盘创近 20 日新高、量比 >= 1.5、当日涨幅 >= 2%

前提（两种都要满足，趋势向上）：
  MA5>MA20>MA60、收盘>MA20、MA60 今日 > 20 日前

僵尸股过滤：
  近 20 日日均成交额 >= 1.5 亿、剔除名称含 ST/退、上市天数 > 120

价格用后复权（close*adj_factor）计算均线与新高；成交额 / 量用原始值。

用法：
  python first10/screen_swing_layer1.py --start 20200101 --end 20991231 --mode both
  --mode  both | pullback | breakout

产出：
  控制台打印各年 / 各模式候选数 + 每日候选数分布
  候选明细写入 first10/cache/swing_layer1_candidates.parquet（ts_code/trade_date/mode + 关键字段）
"""

import argparse
import os
import sys
import time

import duckdb as _duckdb
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db_loader import _ENV

_DUCKDB_PATH = _ENV.get("LOCAL_DUCKDB_PATH", "stock_data_tushare.duckdb")
_OUT_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "cache", "swing_layer1_candidates.csv")

MA_SHORT, MA_MID, MA_LONG = 5, 20, 60
SLOPE_LOOKBACK = 20
PULLBACK_HIGH_WIN = 10
PULLBACK_MIN, PULLBACK_MAX = 0.03, 0.25
MA_TOUCH_LOW, MA_TOUCH_CLOSE = 1.03, 0.98
BREAKOUT_HIGH_WIN = 20
BREAKOUT_VOL_MULT = 1.5
BREAKOUT_PCT_MIN = 2.0
AMT_MA_WIN = 20
AMT_MIN_KILO = 150000.0
DAYS_LISTED_MIN = 120

_SQL = """
WITH base AS (
  SELECT
    d.ts_code, d.trade_date, d.close, d.vol, d.amount, d.pct_chg,
    d.close * af.adj_factor AS aclose,
    d.high  * af.adj_factor AS ahigh,
    d.low   * af.adj_factor AS alow
  FROM daily d
  JOIN adj_factor af ON af.ts_code = d.ts_code AND af.trade_date = d.trade_date
  WHERE d.trade_date >= DATE '{lb}'
),
ind AS (
  SELECT *,
    AVG(aclose)  OVER w_s   AS ma_s,
    AVG(aclose)  OVER w_m   AS ma_m,
    AVG(aclose)  OVER w_l   AS ma_l,
    AVG(amount)  OVER w_amt AS amt_ma,
    AVG(vol)     OVER w_v5  AS vol_ma5,
    MAX(ahigh)   OVER w_pbh AS hh_pullback,
    MAX(aclose)  OVER w_bk  AS hc_breakout_excl
  FROM base
  WINDOW
    w_s   AS (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN {ms_1} PRECEDING AND CURRENT ROW),
    w_m   AS (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN {mm_1} PRECEDING AND CURRENT ROW),
    w_l   AS (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN {ml_1} PRECEDING AND CURRENT ROW),
    w_amt AS (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN {amt_1} PRECEDING AND CURRENT ROW),
    w_v5  AS (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING),
    w_pbh AS (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN {pbh} PRECEDING AND 1 PRECEDING),
    w_bk  AS (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN {bk} PRECEDING AND 1 PRECEDING)
),
ind2 AS (
  SELECT *,
    LAG(ma_l, {slope}) OVER (PARTITION BY ts_code ORDER BY trade_date) AS ma_l_prev
  FROM ind
),
flagged AS (
  SELECT
    ts_code, trade_date, close, pct_chg, aclose, amt_ma, ma_m,
    CASE WHEN vol_ma5 > 0 THEN vol / vol_ma5 END AS vol_ratio5,
    CASE WHEN aclose > 0 THEN hh_pullback / aclose - 1.0 END AS pullback_depth,
    (alow <= ma_m * {touch_low}) AS alow_touch,
    hc_breakout_excl,
    (ma_s > ma_m AND ma_m > ma_l AND aclose > ma_m
     AND ma_l_prev IS NOT NULL AND ma_l > ma_l_prev) AS trend_ok,
    (amt_ma >= {amt_min}
     AND m.name NOT LIKE '%ST%' AND m.name NOT LIKE '%退%'
     AND CAST(trade_date - TRY_CAST(strptime(m.list_date, '%Y%m%d') AS DATE) AS DOUBLE) > {dlm}
    ) AS liquid_ok
  FROM ind2
  LEFT JOIN stock_meta m USING (ts_code)
  WHERE trade_date >= DATE '{start}'
),
scored AS (
  SELECT *,
    (alow_touch AND aclose >= ma_m * {touch_close} AND pct_chg > 0
     AND pullback_depth BETWEEN {pmin} AND {pmax}) AS is_pullback,
    (aclose > hc_breakout_excl AND vol_ratio5 >= {vmult} AND pct_chg >= {bpm}) AS is_breakout
  FROM flagged
  WHERE trend_ok AND liquid_ok
)
SELECT
  ts_code, trade_date, close, pct_chg, amt_ma, pullback_depth, vol_ratio5,
  CASE WHEN {use_pull} AND is_pullback THEN 'pullback'
       WHEN {use_brk}  AND is_breakout THEN 'breakout' END AS mode
FROM scored
WHERE ({use_pull} AND is_pullback) OR ({use_brk} AND is_breakout)
ORDER BY trade_date, ts_code
"""


def scan(start: str, end: str, mode: str) -> pd.DataFrame:
    """扫描第一层规则候选，返回 DataFrame（不打印、不落盘），供打分端复用。"""
    lb = f"{int(start[:4]) - 1}-01-01"
    start_dash = f"{start[:4]}-{start[4:6]}-{start[6:]}"
    sql = _SQL.format(
        lb=lb, start=start_dash,
        ms_1=MA_SHORT - 1, mm_1=MA_MID - 1, ml_1=MA_LONG - 1,
        amt_1=AMT_MA_WIN - 1, pbh=PULLBACK_HIGH_WIN, bk=BREAKOUT_HIGH_WIN,
        slope=SLOPE_LOOKBACK, touch_low=MA_TOUCH_LOW, touch_close=MA_TOUCH_CLOSE,
        amt_min=AMT_MIN_KILO, dlm=DAYS_LISTED_MIN,
        pmin=PULLBACK_MIN, pmax=PULLBACK_MAX,
        vmult=BREAKOUT_VOL_MULT, bpm=BREAKOUT_PCT_MIN,
        use_pull="TRUE" if mode in ("both", "pullback") else "FALSE",
        use_brk="TRUE" if mode in ("both", "breakout") else "FALSE",
    )
    con = _duckdb.connect(_DUCKDB_PATH, read_only=True)
    try:
        df = con.execute(sql).df()
    finally:
        con.close()
    end_ts = pd.Timestamp(f"{end[:4]}-{end[4:6]}-{end[6:]}")
    return df[df["trade_date"] <= end_ts].reset_index(drop=True)


def run(start: str, end: str, mode: str) -> None:
    """全市场扫描第一层规则，打印年度 / 每日候选数并写出候选明细。"""
    t0 = time.time()
    df = scan(start, end, mode)
    print(f"扫描完成：{time.time()-t0:.1f}s，候选 {len(df)} 条，区间 {start}~{end}，mode={mode}\n",
          flush=True)

    if df.empty:
        print("无候选，检查阈值或区间。")
        return

    df["year"] = df["trade_date"].astype(str).str[:4]
    print("各年候选数（按模式）:")
    piv = df.pivot_table(index="year", columns="mode", values="ts_code",
                         aggfunc="count", fill_value=0)
    piv["合计"] = piv.sum(axis=1)
    print(piv.to_string(), "\n")

    daily = df.groupby(df["trade_date"].astype(str)).size()
    print("每日候选数分布:")
    print(f"  交易日数 {daily.shape[0]}  日均 {daily.mean():.1f}  "
          f"中位 {daily.median():.0f}  max {daily.max()}")
    print(f"  分位 p25/p50/p75/p90: "
          f"{daily.quantile(.25):.0f}/{daily.quantile(.5):.0f}/"
          f"{daily.quantile(.75):.0f}/{daily.quantile(.9):.0f}\n")

    os.makedirs(os.path.dirname(_OUT_CSV), exist_ok=True)
    df.drop(columns=["year"]).to_csv(_OUT_CSV, index=False)
    print(f"候选明细已写出: {_OUT_CSV}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20200101")
    ap.add_argument("--end", default="20991231")
    ap.add_argument("--mode", default="both", choices=["both", "pullback", "breakout"])
    a = ap.parse_args()
    run(a.start, a.end, a.mode)


if __name__ == "__main__":
    main()
