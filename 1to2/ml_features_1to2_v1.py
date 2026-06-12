"""
ml_features_1to2_v1.py — 首板→二板信号特征工程 v4（1进2 独立特征，源自 2lb_v3 克隆）

本模块自此独立于 first10/ml_features_2lb_v3，专供 1进2 使用：
  - 删除 3 个对首板恒空的 d1_*（d1_lu_time_min / d1_fd_amount_ratio / d1_open_times，源自 limit_list_d 的前板封板，首板的 T-1 非涨停日 → 恒 NULL）
  - 新增 3 个技术面因子（来自 DuckDB stk_factor_pro 表，由 cache_tushare 缓存，后复权）：
      bias2_hfq    12日乖离率（位置/动量，诊断 IC 最高）
      rsi_hfq_12   12日 RSI（有界振荡器）
      topdays      创阶段新高天数（结构信息）
  - 共 34 个特征（原 34 - 3 死特征 + 3 因子；dmi_adx_hfq 经训练 SHAP 垫底已剔）。label 与口径不变（next_pct≥9.8 = 次日涨停=晋级2板）。

集合竞价特征（继承自 2lb_v3）（信号日 T 盘后可得；竞价 vol 单位=股、daily vol=手；竞价 amount=元、daily amount=千元，已做单位换算）：
  auc_o_amt_ratio  开盘集合竞价成交额 / 昨日(T-1)全天成交额  —— 开盘抢筹资金强度
  auc_c_vol_ratio  收盘集合竞价量 / 当日(T)全天成交量        —— 尾盘竞价占比(撬板/抢筹/出货)
SQL 仍计算 auc_o_vol_ratio / auc_o_move（与 amt_ratio 共线/无预测力，经 SHAP 验证后已从 FEATURE_COLS 移除，保留列仅供查看）。
依赖新增表：stk_auction_o / stk_auction_c（cache_tushare.py 拉取）。

—— 以下为 v2 原始说明 ——
ml_features_2lb_v2.py — 二板→三板信号特征工程（独立 pipeline）

信号定义：
  T 日 limit_list_d.limit_times = 2（2 连板）→ 预测 T+1 是否涨停（3 板成功）
label：
  T+1 pct_chg >= 9.8（简单二分类，正例率预估 25-35%）

进出场假设：
  T 日盘后选股 → T+1 开盘买 → T+1 收盘观察是否封板

特征体系：
  - 复用首板 v2 的 26 个特征（pct_chg / vol_ratio_5 / fd_amount_ratio / ... / market_*）
  - 新增 4 个首板（T-1）特征：
      d1_lu_time_min     首板封板时间分钟（早 = 强势）
      d1_fd_amount_ratio 首板封单金额 / 流通市值
      d1_open_times      首板炸板次数
      d1_vol_ratio_5     首板成交量 / 前 5 日均量（爆量首板信号强度）
  - 新增 1 个：gap_2lb     二板开盘 / 首板收盘 - 1（人气延续度）
  - 新增 1 个：has_cb       信号日正股是否有存续可转债（1/0，按 cb_basic 上市/退市日做时点判断）
  - 仍保留 top10_float_ratio_sum

依赖：与 ml_features_v2 相同 + 同样需要 top10_floatholders 表。
"""

import os
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import duckdb as _duckdb
from db_loader import _ENV

_DUCKDB_PATH = _ENV.get("LOCAL_DUCKDB_PATH", "stock_data_tushare.duckdb")


FEATURE_COLS = [
    "pct_chg", "vol_ratio_5",
    "intraday_range", "pre_days_limit",
    "lu_time_min",
    "fd_amount_ratio", "open_times", "up_stat_ratio",
    "circ_mv_log", "turnover_rate", "pe_ttm_log", "pb_log",
    "winner_rate", "chip_conc",
    "days_listed",
    "market_trend", "market_ma_dir",
    "market_dt_count", "market_lu_ma5",
    "stock_lianban_hist_rate",
    "net_mf_ratio_log", "lg_elg_ratio",
    "market_max_lianban", "market_2lb_rate_ma5", "market_idx_dist_h60",
    "top10_float_ratio_sum",
    "d1_vol_ratio_5",
    "gap_2lb",
    "has_cb",
    "auc_o_amt_ratio", "auc_c_vol_ratio",
    "bias2_hfq", "rsi_hfq_12", "topdays",
]


FEATURE_CN = {
    "pct_chg": "个股信号日涨跌幅",
    "vol_ratio_5": "5日量比(当日量/5日均量)",
    "intraday_range": "当日振幅",
    "pre_days_limit": "前5日涨跌停天数",
    "lu_time_min": "封板时间(早=强)",
    "fd_amount_ratio": "封单额/流通市值",
    "open_times": "炸板次数(0=稳封)",
    "up_stat_ratio": "近期涨停频率",
    "circ_mv_log": "流通市值(log)",
    "turnover_rate": "换手率",
    "pe_ttm_log": "PE_TTM(log)",
    "pb_log": "PB(log)",
    "winner_rate": "获利盘比例",
    "chip_conc": "筹码集中度(越小越集中)",
    "days_listed": "上市天数",
    "market_trend": "大盘当日涨跌(中证1000)",
    "market_ma_dir": "大盘均线方向",
    "market_dt_count": "全市场跌停数",
    "market_lu_ma5": "全市场涨停数5日均",
    "stock_lianban_hist_rate": "个股历史连板率",
    "net_mf_ratio_log": "净主力额/价(log)",
    "lg_elg_ratio": "大单+特大单买入占比",
    "market_max_lianban": "当日最高连板高度",
    "market_2lb_rate_ma5": "全市场5日均2板晋级率",
    "market_idx_dist_h60": "中证1000距60日高点",
    "top10_float_ratio_sum": "前十大流通股东合计占比",
    "d1_vol_ratio_5": "前一日量比(爆量首板)",
    "gap_2lb": "首板高开幅度",
    "has_cb": "是否有存续可转债",
    "auc_o_amt_ratio": "开盘竞价额/昨日全天额(抢筹)",
    "auc_c_vol_ratio": "尾盘竞价量/全天量",
    "bias2_hfq": "12日乖离率(位置/动量)",
    "rsi_hfq_12": "12日RSI(超买超卖)",
    "topdays": "创阶段新高天数",
}


_FEATURE_SQL = """
WITH
  d AS (
    SELECT
      ts_code, trade_date, open, high, low, close, vol, amount, pct_chg,
      AVG(vol)        OVER w5_excl  AS ma5,
      LAG(close, 1)   OVER w_full   AS prev_close,
      LAG(vol, 1)     OVER w_full   AS prev_vol,
      LAG(amount, 1)  OVER w_full   AS prev_amount,
      LAG(trade_date, 1) OVER w_full AS prev_td,
      LEAD(pct_chg, 1) OVER w_full  AS next_pct,
      LEAD(pct_chg, 2) OVER w_full  AS next2_pct,
      COUNT(CASE WHEN abs(pct_chg) >= 9.8 THEN 1 END)
        OVER w5_excl AS pre_days_limit,
      LAG(vol, 1) OVER w_full
        / NULLIF(AVG(vol) OVER (PARTITION BY ts_code ORDER BY trade_date
                                ROWS BETWEEN 6 PRECEDING AND 2 PRECEDING), 0)
        AS d1_vol_ratio_5
    FROM daily
    WINDOW
      w_full  AS (PARTITION BY ts_code ORDER BY trade_date),
      w5_excl AS (PARTITION BY ts_code ORDER BY trade_date
                  ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING)
  ),
  k AS (
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
  k1 AS (
    SELECT
      ts_code, trade_date,
      first_time AS d1_lu_time_raw,
      fd_amount  AS d1_fd_amount,
      float_mv   AS d1_lld_float_mv,
      open_times AS d1_open_times
    FROM limit_list_d
    WHERE limit_type = 'U' AND limit_times = 1
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
  market_stats AS (
    SELECT
      trade_date,
      COUNT(CASE WHEN pct_chg <= -9.8 THEN 1 END) AS market_dt_count,
      AVG(COUNT(CASE WHEN pct_chg >= 9.8 THEN 1 END))
        OVER (ORDER BY trade_date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW)
        AS market_lu_ma5
    FROM daily
    WHERE ts_code NOT LIKE '30%'
      AND ts_code NOT LIKE '688%'
      AND ts_code NOT LIKE '%.BJ'
    GROUP BY trade_date
  ),
  mf AS (
    SELECT
      ts_code, trade_date,
      net_mf_amount,
      buy_sm_vol, buy_md_vol, buy_lg_vol, buy_elg_vol,
      (buy_sm_vol + buy_md_vol + buy_lg_vol + buy_elg_vol) AS total_buy_vol
    FROM moneyflow
  ),
  top10_agg AS (
    SELECT
      ts_code,
      CAST(strptime(ann_date, '%Y%m%d') AS DATE) AS ann_dt,
      end_date,
      SUM(hold_float_ratio) AS top10_float_ratio_sum
    FROM top10_floatholders
    WHERE ann_date IS NOT NULL AND ann_date <> ''
    GROUP BY ts_code, ann_date, end_date
  ),
  top10_pit AS (
    SELECT s.ts_code, s.sig_date, t.top10_float_ratio_sum
    FROM signals_typed s
    ASOF LEFT JOIN top10_agg t
      ON t.ts_code = s.ts_code
     AND t.ann_dt < s.sig_date
  ),
  cb_pit AS (
    SELECT
      s.ts_code, s.sig_date,
      MAX(CASE
        WHEN cb.list_date IS NOT NULL AND cb.list_date <> ''
         AND TRY_CAST(strptime(cb.list_date, '%Y%m%d') AS DATE) <= s.sig_date
         AND (cb.delist_date IS NULL OR cb.delist_date = ''
              OR TRY_CAST(strptime(cb.delist_date, '%Y%m%d') AS DATE) > s.sig_date)
        THEN 1.0 ELSE 0.0 END) AS has_cb
    FROM signals_typed s
    LEFT JOIN cb_basic cb ON cb.stk_code = s.ts_code
    GROUP BY s.ts_code, s.sig_date
  ),
  auc_o AS (
    SELECT ts_code, trade_date,
           close AS ao_close, open AS ao_open, vol AS ao_vol, amount AS ao_amt
    FROM stk_auction_o
  ),
  auc_c AS (
    SELECT ts_code, trade_date, vol AS ac_vol
    FROM stk_auction_c
  ),
  sf AS (
    SELECT ts_code, trade_date, bias2_hfq, rsi_hfq_12, dmi_adx_hfq, topdays
    FROM stk_factor_pro
  )
SELECT
  s.ts_code,
  s.trade_date_str AS trade_date,

  d.pct_chg / 100.0                                                        AS pct_chg,
  CASE WHEN d.ma5 > 0 THEN d.vol / d.ma5 END                               AS vol_ratio_5,
  CASE WHEN d.close > 0 THEN (d.high - d.low) / d.close END                AS intraday_range,
  CAST(d.pre_days_limit AS DOUBLE)                                         AS pre_days_limit,

  CASE
    WHEN k.lu_time_raw IS NULL OR k.lu_time_raw = '' THEN NULL
    ELSE TRY_CAST(substr(lpad(k.lu_time_raw, 6, '0'), 1, 2) AS INTEGER) * 60
       + TRY_CAST(substr(lpad(k.lu_time_raw, 6, '0'), 3, 2) AS INTEGER) - 570
  END                                                                      AS lu_time_min,

  CASE WHEN k.lld_float_mv > 0 THEN k.fd_amount / k.lld_float_mv END        AS fd_amount_ratio,
  CAST(k.open_times AS DOUBLE)                                             AS open_times,
  CASE WHEN k.up_stat_t > 0 THEN k.up_stat_n / k.up_stat_t END             AS up_stat_ratio,

  CASE WHEN db.circ_mv > 0 THEN ln(db.circ_mv) END                         AS circ_mv_log,
  db.turnover_rate                                                         AS turnover_rate,
  CASE WHEN db.pe_ttm IS NULL THEN NULL
       WHEN db.pe_ttm >= 0 THEN ln(1 + db.pe_ttm)
       ELSE -ln(1 + (-db.pe_ttm))
  END                                                                      AS pe_ttm_log,
  CASE WHEN db.pb IS NULL OR db.pb <= 0 THEN NULL ELSE ln(db.pb) END       AS pb_log,

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

  d.next_pct                                                               AS next_pct,
  d.next2_pct                                                              AS next2_pct,

  market_stats.market_dt_count                                             AS market_dt_count,
  market_stats.market_lu_ma5                                               AS market_lu_ma5,
  signal_stats.stock_lianban_hist_rate                                     AS stock_lianban_hist_rate,

  CASE WHEN d.close IS NULL OR d.close <= 0 OR mf.net_mf_amount IS NULL THEN NULL
       WHEN mf.net_mf_amount >= 0 THEN  ln(1 + mf.net_mf_amount / d.close)
       ELSE                            -ln(1 + (-mf.net_mf_amount) / d.close)
  END                                                                     AS net_mf_ratio_log,
  CASE WHEN mf.total_buy_vol > 0
       THEN (mf.buy_lg_vol + mf.buy_elg_vol) * 1.0 / mf.total_buy_vol END AS lg_elg_ratio,

  ms.market_max_lianban                                                   AS market_max_lianban,
  ms.market_2lb_rate_ma5                                                  AS market_2lb_rate_ma5,
  ms.market_idx_dist_h60                                                  AS market_idx_dist_h60,

  t10.top10_float_ratio_sum                                               AS top10_float_ratio_sum,

  CASE
    WHEN k1.d1_lu_time_raw IS NULL OR k1.d1_lu_time_raw = '' THEN NULL
    ELSE TRY_CAST(substr(lpad(k1.d1_lu_time_raw, 6, '0'), 1, 2) AS INTEGER) * 60
       + TRY_CAST(substr(lpad(k1.d1_lu_time_raw, 6, '0'), 3, 2) AS INTEGER) - 570
  END                                                                     AS d1_lu_time_min,
  CASE WHEN k1.d1_lld_float_mv > 0 THEN k1.d1_fd_amount / k1.d1_lld_float_mv END
                                                                          AS d1_fd_amount_ratio,
  CAST(k1.d1_open_times AS DOUBLE)                                        AS d1_open_times,
  d.d1_vol_ratio_5                                                        AS d1_vol_ratio_5,

  CASE WHEN d.prev_close > 0 THEN d.open / d.prev_close - 1.0 END         AS gap_2lb,

  COALESCE(cbp.has_cb, 0.0)                                               AS has_cb,

  CASE WHEN d.prev_vol    > 0 THEN ao.ao_vol / (d.prev_vol * 100.0) END   AS auc_o_vol_ratio,
  CASE WHEN ao.ao_open    > 0 THEN ao.ao_close / ao.ao_open - 1.0 END     AS auc_o_move,
  CASE WHEN d.prev_amount > 0 THEN ao.ao_amt / (d.prev_amount * 1000.0) END AS auc_o_amt_ratio,
  CASE WHEN d.vol         > 0 THEN ac.ac_vol / (d.vol * 100.0) END        AS auc_c_vol_ratio,

  sf.bias2_hfq                                                            AS bias2_hfq,
  sf.rsi_hfq_12                                                           AS rsi_hfq_12,
  sf.dmi_adx_hfq                                                          AS dmi_adx_hfq,
  CAST(sf.topdays AS DOUBLE)                                              AS topdays

FROM signals_typed s
JOIN d              ON d.ts_code     = s.ts_code AND d.trade_date     = s.sig_date
LEFT JOIN k         ON k.ts_code     = s.ts_code AND k.trade_date     = s.sig_date
LEFT JOIN k1        ON k1.ts_code    = s.ts_code AND k1.trade_date    = d.prev_td
LEFT JOIN daily_basic db
                    ON db.ts_code    = s.ts_code AND db.trade_date    = s.sig_date
LEFT JOIN cyq_perf c
                    ON c.ts_code     = s.ts_code AND c.trade_date     = s.sig_date
LEFT JOIN stock_meta m
                    ON m.ts_code     = s.ts_code
LEFT JOIN idx       ON idx.trade_date = s.sig_date
LEFT JOIN auc_o ao  ON ao.ts_code    = s.ts_code AND ao.trade_date = s.sig_date
LEFT JOIN auc_c ac  ON ac.ts_code    = s.ts_code AND ac.trade_date = s.sig_date
LEFT JOIN sf        ON sf.ts_code    = s.ts_code AND sf.trade_date = s.sig_date
LEFT JOIN signal_stats
                    ON signal_stats.ts_code = s.ts_code
                   AND signal_stats.sig_date = s.sig_date
LEFT JOIN market_stats
                    ON market_stats.trade_date = s.sig_date
LEFT JOIN mf        ON mf.ts_code     = s.ts_code AND mf.trade_date = s.sig_date
LEFT JOIN market_state ms
                    ON ms.trade_date = s.sig_date
LEFT JOIN top10_pit t10
                    ON t10.ts_code = s.ts_code AND t10.sig_date = s.sig_date
LEFT JOIN cb_pit cbp
                    ON cbp.ts_code = s.ts_code AND cbp.sig_date = s.sig_date
{label_filter}
ORDER BY s.sig_date, s.ts_code
"""


def build_feature_matrix(signal_df: pd.DataFrame, stock_dict=None,
                         require_label: bool = True) -> pd.DataFrame:
    """构建特征矩阵；require_label=True 训练用（要求 next_pct），False 打分用。"""
    if signal_df.empty:
        return pd.DataFrame()

    sig = signal_df[["ts_code", "trade_date"]].copy()
    sig["trade_date"] = sig["trade_date"].astype(str).str.replace("-", "")

    sql = _FEATURE_SQL.format(
        label_filter="WHERE d.next_pct IS NOT NULL" if require_label else ""
    )

    t0 = time.time()
    con = _duckdb.connect(_DUCKDB_PATH, read_only=True)
    print(f"  [features_1to2_v1] DuckDB connect: {time.time()-t0:.2f}s", flush=True)
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
        print(f"  [features_1to2_v1] register signals ({len(sig)} rows): {time.time()-t1:.2f}s", flush=True)

        t2 = time.time()
        df = con.execute(sql).df()
        print(f"  [features_1to2_v1] SQL execute + fetch ({len(df)} rows): {time.time()-t2:.2f}s", flush=True)
    finally:
        con.close()

    print(f"  [features_1to2_v1] total: {time.time()-t0:.2f}s", flush=True)
    return df
