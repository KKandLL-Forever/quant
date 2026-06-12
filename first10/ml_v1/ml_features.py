"""
ml_features.py — 首板爆量信号特征工程（DuckDB 单查询版）

入口：build_feature_matrix(signal_df) -> pd.DataFrame
  signal_df 必须含列: ts_code (str), trade_date (str YYYYMMDD)
  返回含 label 列（次日是否连板 0/1）及所有特征列的 DataFrame。

实现：把全部特征 + label 拼成一条 DuckDB SQL，所有 join / 窗口函数在
数据库内部完成，Python 侧只做最终 DataFrame 读取，避免逐行循环。
"""

import os
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── 数据源（DuckDB）。若要切回 ClickHouse，请参考 git 历史中的旧版实现。
import duckdb as _duckdb
from db_loader import _ENV

_DUCKDB_PATH = _ENV.get("LOCAL_DUCKDB_PATH", "stock_data_tushare.duckdb")


FEATURE_COLS = [
    "pct_chg", "vol_ratio_5", "vol_ratio_20",
    "intraday_range", "close_strength", "gap_up", "pre_days_limit",
    "lu_time_min",
    "fd_amount_ratio", "open_times", "up_stat_ratio",
    "circ_mv_log", "turnover_rate", "pe_ttm", "pb",
    "winner_rate", "chip_conc",
    "days_listed",
    "market_trend", "market_ma_dir",
    "market_lu_count", "market_dt_count", "market_lu_ma5",
    "stock_lianban_hist_rate",
    "net_mf_ratio", "lg_elg_ratio", "sm_ratio",
]
# 注：concept_* / fd_x_concept_lu 5 个特征 SQL 仍计算并写入 feature_matrix.csv（用于诊断），
# 但不进入模型 —— 训练集 95% NULL + 测试集无单调信号，已确认无 alpha。


_FEATURE_SQL = """
WITH
  d AS (
    SELECT
      ts_code, trade_date, open, high, low, close, vol, pct_chg,
      AVG(vol)        OVER w5_excl  AS ma5,
      AVG(vol)        OVER w20_excl AS ma20,
      LAG(close, 1)   OVER w_full   AS prev_close,
      LEAD(pct_chg,1) OVER w_full   AS next_pct,
      COUNT(CASE WHEN abs(pct_chg) >= 9.8 THEN 1 END)
        OVER w5_excl AS pre_days_limit
    FROM daily
    WINDOW
      w_full   AS (PARTITION BY ts_code ORDER BY trade_date),
      w5_excl  AS (PARTITION BY ts_code ORDER BY trade_date
                   ROWS BETWEEN  5 PRECEDING AND 1 PRECEDING),
      w20_excl AS (PARTITION BY ts_code ORDER BY trade_date
                   ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING)
  ),
  k AS (
    -- Tushare limit_list_d 替代 kpl_list（覆盖更完整，2020 起全 A 股每日）
    -- first_time 形如 "09:31:23"；up_stat 形如 "N/T"，T 天有 N 次涨停
    SELECT
      ts_code, trade_date,
      first_time            AS lu_time_raw,
      fd_amount             AS fd_amount,
      float_mv              AS lld_float_mv,
      open_times            AS open_times,
      up_stat               AS up_stat,
      TRY_CAST(split_part(up_stat, '/', 1) AS DOUBLE) AS up_stat_n,
      TRY_CAST(split_part(up_stat, '/', 2) AS DOUBLE) AS up_stat_t
    FROM limit_list_d
    WHERE limit_type = 'U'
  ),
  idx AS (
    SELECT
      trade_date,
      pct_chg AS market_trend,
      AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN  4 PRECEDING AND CURRENT ROW) AS idx_ma5,
      AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS idx_ma20
    FROM index_daily
    WHERE ts_code = '000852.SH'
  ),
  -- Phase A: 信号扩展 — 补齐 next_pct 以便计算个股历史统计
  signal_ex AS (
    SELECT
      s.ts_code,
      s.sig_date,
      d.next_pct,
      ROW_NUMBER() OVER (
        PARTITION BY s.ts_code ORDER BY s.sig_date
      ) - 1 AS seq_num_before
    FROM signals_typed s
    JOIN d ON d.ts_code = s.ts_code AND d.trade_date = s.sig_date
  ),
  -- Phase A: 个股历史连板成功率（累积口径，截至信号前一天）
  signal_stats AS (
    SELECT
      ts_code, sig_date,
      CASE WHEN seq_num_before > 0
        THEN SUM(CASE WHEN next_pct >= 9.8 THEN 1 ELSE 0 END)
               OVER (PARTITION BY ts_code ORDER BY sig_date
                     ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) * 1.0
             / seq_num_before
        ELSE NULL
      END AS stock_lianban_hist_rate
    FROM signal_ex
  ),
  -- Phase A: 市场情绪 — 全市场每日涨跌停股数
  market_stats AS (
    SELECT
      trade_date,
      COUNT(CASE WHEN pct_chg >= 9.8 THEN 1 END) AS market_lu_count,
      COUNT(CASE WHEN pct_chg <= -9.8 THEN 1 END) AS market_dt_count,
      AVG(COUNT(CASE WHEN pct_chg >= 9.8 THEN 1 END))
        OVER (ORDER BY trade_date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW)
        AS market_lu_ma5
    FROM daily
    WHERE ts_code NOT LIKE '300%'
      AND ts_code NOT LIKE '688%'
      AND ts_code NOT LIKE '%.BJ'
    GROUP BY trade_date
  ),
  -- Phase B: 资金流向
  mf AS (
    SELECT
      ts_code, trade_date,
      net_mf_amount,
      buy_sm_vol, buy_md_vol, buy_lg_vol, buy_elg_vol,
      (buy_sm_vol + buy_md_vol + buy_lg_vol + buy_elg_vol) AS total_buy_vol
    FROM moneyflow
  ),
  -- Phase C: 板块共振 — 信号股所属概念在信号日的强度
  -- 通过 ths_member (stock→concept) join limit_cpt_list (concept 当日涨停统计) 聚合
  concept_strength AS (
    SELECT
      m.con_code                           AS ts_code,
      c.trade_date                         AS sig_date,
      MAX(c.up_nums)                       AS concept_lu_count_max,
      MIN(TRY_CAST(c."rank" AS INTEGER))   AS concept_rank_min,
      MAX(c.cons_nums)                     AS concept_cons_nums_max,
      COUNT(DISTINCT c.ts_code)            AS concept_count
    FROM ths_member m
    JOIN limit_cpt_list c ON c.ts_code = m.ts_code
    GROUP BY m.con_code, c.trade_date
  )
SELECT
  s.ts_code,
  s.trade_date_str AS trade_date,

  d.pct_chg / 100.0                                                        AS pct_chg,
  CASE WHEN d.ma5  > 0 THEN d.vol / d.ma5  END                             AS vol_ratio_5,
  CASE WHEN d.ma20 > 0 THEN d.vol / d.ma20 END                             AS vol_ratio_20,
  CASE WHEN d.close > 0 THEN (d.high - d.low) / d.close END                AS intraday_range,
  CASE WHEN d.high > d.low THEN (d.close - d.low) / (d.high - d.low) END   AS close_strength,
  CASE WHEN d.prev_close > 0 THEN d.open / d.prev_close - 1 END            AS gap_up,
  CAST(d.pre_days_limit AS DOUBLE)                                         AS pre_days_limit,

  -- limit_list_d.first_time 是 HHMMSS 6 位数字（早于 10 点会丢前导零变 5 位），需先 lpad
  CASE
    WHEN k.lu_time_raw IS NULL OR k.lu_time_raw = '' THEN NULL
    ELSE TRY_CAST(substr(lpad(k.lu_time_raw, 6, '0'), 1, 2) AS INTEGER) * 60
       + TRY_CAST(substr(lpad(k.lu_time_raw, 6, '0'), 3, 2) AS INTEGER) - 570
  END                                                                      AS lu_time_min,

  -- 封单强度：封单金额 / 流通市值（limit_list_d 的 float_mv 单位与 fd_amount 同为元）
  CASE WHEN k.lld_float_mv > 0 THEN k.fd_amount / k.lld_float_mv END        AS fd_amount_ratio,
  CAST(k.open_times AS DOUBLE)                                             AS open_times,
  -- up_stat "N/T" → 近期涨停频率（T 天里有 N 次涨停）
  CASE WHEN k.up_stat_t > 0 THEN k.up_stat_n / k.up_stat_t END             AS up_stat_ratio,

  CASE WHEN db.circ_mv > 0 THEN ln(db.circ_mv) END                         AS circ_mv_log,
  db.turnover_rate                                                         AS turnover_rate,
  db.pe_ttm                                                                AS pe_ttm,
  db.pb                                                                    AS pb,

  c.winner_rate                                                            AS winner_rate,
  CASE
    WHEN d.close > 0 AND c.cost_5pct IS NOT NULL AND c.cost_95pct IS NOT NULL
    THEN (c.cost_95pct - c.cost_5pct) / d.close
  END                                                                      AS chip_conc,

  CAST(s.sig_date - TRY_CAST(strptime(m.list_date, '%Y%m%d') AS DATE)
       AS DOUBLE)                                                          AS days_listed,

  idx.market_trend                                                         AS market_trend,
  CASE
    WHEN idx.idx_ma5 > idx.idx_ma20 THEN  1.0
    WHEN idx.idx_ma5 < idx.idx_ma20 THEN -1.0
    ELSE 0.0
  END                                                                      AS market_ma_dir,

  CASE
    WHEN d.next_pct IS NULL THEN NULL
    WHEN d.next_pct >= 9.8 THEN 1.0
    ELSE 0.0
  END                                                                      AS label,

  market_stats.market_lu_count                                             AS market_lu_count,
  market_stats.market_dt_count                                             AS market_dt_count,
  market_stats.market_lu_ma5                                               AS market_lu_ma5,
  signal_stats.stock_lianban_hist_rate                                     AS stock_lianban_hist_rate,

  CASE WHEN d.close > 0 THEN mf.net_mf_amount / d.close END              AS net_mf_ratio,
  CASE WHEN mf.total_buy_vol > 0
       THEN (mf.buy_lg_vol + mf.buy_elg_vol) * 1.0 / mf.total_buy_vol END AS lg_elg_ratio,
  CASE WHEN mf.total_buy_vol > 0
       THEN mf.buy_sm_vol * 1.0 / mf.total_buy_vol END                    AS sm_ratio,

  -- 板块共振特征（信号股不在任何概念里时为 NULL，由 LEFT JOIN 兜底）
  COALESCE(cs.concept_lu_count_max, 0)                                    AS concept_lu_count_max,
  cs.concept_rank_min                                                     AS concept_rank_min,
  COALESCE(cs.concept_cons_nums_max, 0)                                   AS concept_cons_nums_max,
  COALESCE(cs.concept_count, 0)                                           AS concept_count,
  -- 交叉特征：封板硬度 × 板块共振强度
  CASE WHEN k.lld_float_mv > 0
       THEN (k.fd_amount / k.lld_float_mv)
          * COALESCE(cs.concept_lu_count_max, 0)
  END                                                                     AS fd_x_concept_lu
FROM signals_typed s
JOIN d              ON d.ts_code     = s.ts_code AND d.trade_date     = s.sig_date
LEFT JOIN k         ON k.ts_code     = s.ts_code AND k.trade_date     = s.sig_date
LEFT JOIN daily_basic db
                    ON db.ts_code    = s.ts_code AND db.trade_date    = s.sig_date
LEFT JOIN cyq_perf c
                    ON c.ts_code     = s.ts_code AND c.trade_date     = s.sig_date
LEFT JOIN stock_meta m
                    ON m.ts_code     = s.ts_code
LEFT JOIN idx       ON idx.trade_date = s.sig_date
LEFT JOIN signal_stats
                    ON signal_stats.ts_code = s.ts_code
                   AND signal_stats.sig_date = s.sig_date
LEFT JOIN market_stats
                    ON market_stats.trade_date = s.sig_date
LEFT JOIN mf         ON mf.ts_code     = s.ts_code AND mf.trade_date = s.sig_date
LEFT JOIN concept_strength cs
                    ON cs.ts_code = s.ts_code AND cs.sig_date = s.sig_date
{label_filter}
ORDER BY s.sig_date
"""


def build_feature_matrix(signal_df: pd.DataFrame, stock_dict=None,
                         require_label: bool = True) -> pd.DataFrame:
    """signal_df: columns [ts_code, trade_date(str YYYYMMDD)]
    stock_dict: 历史遗留参数，本实现未使用。
    require_label: True=训练用（要求有 next_pct 即 T+1 数据），False=打分用（最新日期 OK）。
    """
    if signal_df.empty:
        return pd.DataFrame()

    sig = signal_df[["ts_code", "trade_date"]].copy()
    sig["trade_date"] = sig["trade_date"].astype(str).str.replace("-", "")

    sql = _FEATURE_SQL.format(
        label_filter="WHERE d.next_pct IS NOT NULL" if require_label else ""
    )

    t0 = time.time()
    con = _duckdb.connect(_DUCKDB_PATH, read_only=True)
    print(f"  [features] DuckDB connect: {time.time()-t0:.2f}s", flush=True)
    try:
        t1 = time.time()
        con.register("signals_raw", sig)
        con.execute("""
            CREATE OR REPLACE TEMP VIEW signals_typed AS
            SELECT
              ts_code,
              CAST(strptime(trade_date, '%Y%m%d') AS DATE) AS sig_date,
              trade_date AS trade_date_str
            FROM signals_raw
        """)
        print(f"  [features] register signals ({len(sig)} rows): {time.time()-t1:.2f}s", flush=True)

        t2 = time.time()
        df = con.execute(sql).df()
        print(f"  [features] SQL execute + fetch ({len(df)} rows): {time.time()-t2:.2f}s", flush=True)
    finally:
        con.close()

    print(f"  [features] total: {time.time()-t0:.2f}s", flush=True)
    return df
