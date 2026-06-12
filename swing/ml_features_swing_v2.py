"""
ml_features_swing_v2.py — 波段选股策略特征工程 + label（独立 pipeline）

输入：
  screen_swing_layer1.py 产出的候选信号（ts_code / trade_date / mode）。

进出场假设（三重门 / triple-barrier，不固定持有时间）：
  信号当日 T 盘后确认 → T+1 开盘买；之后先触发哪个门就按哪个出：
    上门(止盈) 盘中最高价 >= 买入 ×(1+TAKE_PROFIT) → 按止盈价卖（收益固定 = TAKE_PROFIT）
    下门(止损) 收盘跌破 MA10 → 破位次日开盘卖
    时间门     兜底上限 EXIT_WINDOW=60 个交易日（都没触发则第 61 日开盘卖）

label（二分类）：
  在跌破 MA10 之前先摸到 +TAKE_PROFIT 止盈 → 正例 1；
  先破 MA10、或到期都没摸到止盈 → 0；窗口未走满（信号太靠近数据末端）→ NULL（丢弃）。

ret_real：止盈胜出 = TAKE_PROFIT（固定）；止损/到期 = sell_open/buy_open - 1。
  —— label 只看前 10 日是否破位，收益让赢家骑完整段趋势，避免 10 日窗口腰斩右尾。

特征体系（全部信号当日 T 可得，后复权计算价格类指标）：
  趋势/动量：pct_chg / ret5 / ret20 / ma_s_m / ma_m_l / dist_ma20 / ma60_slope
  形态：    mode_breakout / pullback_depth / vol_ratio5 / dist_high60
  主力痕迹： net_mf_ratio_log / lg_elg_ratio / turnover_rate / volume_ratio / lu_count_20
  筹码：    winner_rate / chip_conc
  估值规模： circ_mv_log / pe_ttm_log / pb_log
  市场环境： market_trend / market_ma_dir / market_2lb_rate_ma5 / market_idx_dist_h60 / market_max_lianban

依赖：daily / adj_factor / daily_basic / cyq_perf / moneyflow / limit_list_d / index_daily / market_state / stock_meta
"""

import os
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import duckdb as _duckdb
from db_loader import _ENV

_DUCKDB_PATH = _ENV.get("LOCAL_DUCKDB_PATH", "stock_data_tushare.duckdb")
_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "cache", "swing_layer1_candidates.csv")
_OUT_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "cache", "swing_features_v2.csv")

EXIT_WINDOW = 60
TAKE_PROFIT = 0.40

FEATURE_COLS = [
    "mode_breakout",
    "pct_chg", "ret5", "ret20",
    "ma_s_m", "ma_m_l", "dist_ma20", "ma60_slope",
    "pullback_depth", "vol_ratio5", "dist_high60",
    "net_mf_ratio_log", "lg_elg_ratio",
    "turnover_rate", "volume_ratio", "lu_count_20",
    "winner_rate", "chip_conc",
    "circ_mv_log", "pe_ttm_log", "pb_log",
    "market_trend", "market_ma_dir",
    "market_2lb_rate_ma5", "market_idx_dist_h60", "market_max_lianban",
]

_SQL = """
WITH
  sig AS (
    SELECT ts_code, sig_date,
           CASE WHEN mode = 'breakout' THEN 1.0 ELSE 0.0 END AS mode_breakout
    FROM signals_typed
  ),
  dall AS (
    SELECT
      d.ts_code, d.trade_date, d.pct_chg, d.vol, d.amount,
      d.close * af.adj_factor AS aclose,
      d.open  * af.adj_factor AS aopen,
      d.high  * af.adj_factor AS ahigh,
      ROW_NUMBER() OVER w_full AS seq,
      AVG(d.close * af.adj_factor) OVER w5   AS ma5,
      AVG(d.close * af.adj_factor) OVER w10  AS ma10,
      AVG(d.close * af.adj_factor) OVER w20  AS ma20,
      AVG(d.close * af.adj_factor) OVER w60  AS ma60,
      LAG(d.close * af.adj_factor, 5)  OVER w_full AS ac5,
      LAG(d.close * af.adj_factor, 20) OVER w_full AS ac20,
      AVG(d.vol) OVER w_v5  AS vol_ma5,
      MAX(d.high * af.adj_factor) OVER w_h10 AS hh10,
      MAX(d.close * af.adj_factor) OVER w_h60 AS hc60
    FROM daily d
    JOIN adj_factor af USING (ts_code, trade_date)
    WINDOW
      w_full AS (PARTITION BY ts_code ORDER BY trade_date),
      w5   AS (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN  4 PRECEDING AND CURRENT ROW),
      w10  AS (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN  9 PRECEDING AND CURRENT ROW),
      w20  AS (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW),
      w60  AS (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW),
      w_v5 AS (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN  5 PRECEDING AND 1 PRECEDING),
      w_h10 AS (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING),
      w_h60 AS (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 60 PRECEDING AND CURRENT ROW)
  ),
  dslope AS (
    SELECT *, LAG(ma60, 20) OVER (PARTITION BY ts_code ORDER BY trade_date) AS ma60_prev
    FROM dall
  ),
  sd AS (
    SELECT s.ts_code, s.sig_date, s.mode_breakout, ds.*
    FROM sig s
    JOIN dslope ds ON ds.ts_code = s.ts_code AND ds.trade_date = s.sig_date
  ),
  buyp AS (
    SELECT sd.ts_code, sd.sig_date, sd.seq, b.aopen AS buy_open
    FROM sd JOIN dall b ON b.ts_code = sd.ts_code AND b.seq = sd.seq + 1
  ),
  fwd AS (
    SELECT
      bp.ts_code, bp.sig_date, bp.seq, bp.buy_open,
      MIN(CASE WHEN f.ahigh >= bp.buy_open * (1 + {tp}) THEN f.seq END) AS tp_seq,
      MIN(CASE WHEN f.aclose < f.ma10 THEN f.seq END)                  AS stop_seq,
      COUNT(*)                                                         AS nfut
    FROM buyp bp
    JOIN dall f ON f.ts_code = bp.ts_code
               AND f.seq BETWEEN bp.seq + 1 AND bp.seq + {exitwin}
    GROUP BY bp.ts_code, bp.sig_date, bp.seq, bp.buy_open
  ),
  ex AS (
    SELECT *,
      (tp_seq IS NOT NULL AND (stop_seq IS NULL OR tp_seq <= stop_seq)) AS tp_win,
      CASE WHEN tp_seq IS NOT NULL AND (stop_seq IS NULL OR tp_seq <= stop_seq) THEN tp_seq
           WHEN stop_seq IS NOT NULL THEN stop_seq + 1
           ELSE seq + {exitwin} + 1 END AS exit_seq
    FROM fwd
  ),
  exj AS (
    SELECT ex.ts_code, ex.sig_date, ex.buy_open, ex.tp_win, ex.stop_seq, ex.nfut,
           g.aopen AS sell_open,
           ex.exit_seq - (ex.seq + 1) AS hold_days
    FROM ex
    LEFT JOIN dall g ON g.ts_code = ex.ts_code AND g.seq = ex.exit_seq
  ),
  lu AS (
    SELECT s.ts_code, s.sig_date, COUNT(l.trade_date) AS lu_count_20
    FROM sig s
    LEFT JOIN limit_list_d l
      ON l.ts_code = s.ts_code AND l.limit_type = 'U'
     AND l.trade_date <= s.sig_date
     AND l.trade_date > s.sig_date - INTERVAL 30 DAY
    GROUP BY s.ts_code, s.sig_date
  ),
  idx AS (
    SELECT trade_date, pct_chg AS market_trend,
      AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN  4 PRECEDING AND CURRENT ROW) AS idx_ma5,
      AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS idx_ma20
    FROM index_daily WHERE ts_code = '000852.SH'
  )
SELECT
  sd.ts_code,
  strftime(sd.sig_date, '%Y%m%d')                                          AS trade_date,

  sd.mode_breakout                                                         AS mode_breakout,
  CASE WHEN sd.mode_breakout = 1 THEN 'breakout' ELSE 'pullback' END        AS mode,
  sd.pct_chg / 100.0                                                       AS pct_chg,
  CASE WHEN sd.ac5  > 0 THEN sd.aclose / sd.ac5  - 1.0 END                 AS ret5,
  CASE WHEN sd.ac20 > 0 THEN sd.aclose / sd.ac20 - 1.0 END                 AS ret20,
  CASE WHEN sd.ma20 > 0 THEN sd.ma5  / sd.ma20 - 1.0 END                   AS ma_s_m,
  CASE WHEN sd.ma60 > 0 THEN sd.ma20 / sd.ma60 - 1.0 END                   AS ma_m_l,
  CASE WHEN sd.ma20 > 0 THEN sd.aclose / sd.ma20 - 1.0 END                 AS dist_ma20,
  CASE WHEN sd.ma60_prev > 0 THEN sd.ma60 / sd.ma60_prev - 1.0 END         AS ma60_slope,
  CASE WHEN sd.aclose > 0 THEN sd.hh10 / sd.aclose - 1.0 END               AS pullback_depth,
  CASE WHEN sd.vol_ma5 > 0 THEN sd.vol / sd.vol_ma5 END                    AS vol_ratio5,
  CASE WHEN sd.hc60 > 0 THEN sd.aclose / sd.hc60 - 1.0 END                 AS dist_high60,

  CASE WHEN sd.aclose <= 0 OR mf.net_mf_amount IS NULL THEN NULL
       WHEN mf.net_mf_amount >= 0 THEN  ln(1 + mf.net_mf_amount / sd.aclose)
       ELSE                            -ln(1 + (-mf.net_mf_amount) / sd.aclose)
  END                                                                      AS net_mf_ratio_log,
  CASE WHEN (mf.buy_sm_vol + mf.buy_md_vol + mf.buy_lg_vol + mf.buy_elg_vol) > 0
       THEN (mf.buy_lg_vol + mf.buy_elg_vol) * 1.0
          / (mf.buy_sm_vol + mf.buy_md_vol + mf.buy_lg_vol + mf.buy_elg_vol) END
                                                                           AS lg_elg_ratio,
  db.turnover_rate                                                         AS turnover_rate,
  db.volume_ratio                                                          AS volume_ratio,
  CAST(lu.lu_count_20 AS DOUBLE)                                           AS lu_count_20,

  c.winner_rate                                                            AS winner_rate,
  CASE WHEN sd.aclose > 0 AND c.cost_5pct IS NOT NULL AND c.cost_95pct IS NOT NULL
       THEN (c.cost_95pct - c.cost_5pct) / sd.aclose END                  AS chip_conc,

  CASE WHEN db.circ_mv > 0 THEN ln(db.circ_mv) END                         AS circ_mv_log,
  CASE WHEN db.pe_ttm IS NULL THEN NULL
       WHEN db.pe_ttm >= 0 THEN ln(1 + db.pe_ttm)
       ELSE -ln(1 + (-db.pe_ttm)) END                                     AS pe_ttm_log,
  CASE WHEN db.pb IS NULL OR db.pb <= 0 THEN NULL ELSE ln(db.pb) END       AS pb_log,

  idx.market_trend                                                         AS market_trend,
  CASE WHEN idx.idx_ma5 > idx.idx_ma20 THEN 1.0
       WHEN idx.idx_ma5 < idx.idx_ma20 THEN -1.0 ELSE 0.0 END             AS market_ma_dir,
  ms.market_2lb_rate_ma5                                                  AS market_2lb_rate_ma5,
  ms.market_idx_dist_h60                                                  AS market_idx_dist_h60,
  CAST(ms.market_max_lianban AS DOUBLE)                                   AS market_max_lianban,

  CASE WHEN exj.tp_win THEN 1.0
       WHEN exj.stop_seq IS NOT NULL THEN 0.0
       WHEN exj.nfut >= {exitwin} THEN 0.0
       ELSE NULL END                                                      AS label,
  CASE WHEN exj.tp_win THEN {tp}
       WHEN exj.buy_open > 0 AND exj.sell_open IS NOT NULL
       THEN exj.sell_open / exj.buy_open - 1.0 END                        AS ret_real,
  CAST(exj.hold_days AS DOUBLE)                                           AS hold_days

FROM sd
LEFT JOIN exj ON exj.ts_code = sd.ts_code AND exj.sig_date = sd.sig_date
LEFT JOIN lu  ON lu.ts_code  = sd.ts_code AND lu.sig_date  = sd.sig_date
LEFT JOIN daily_basic db ON db.ts_code = sd.ts_code AND db.trade_date = sd.sig_date
LEFT JOIN cyq_perf c     ON c.ts_code  = sd.ts_code AND c.trade_date  = sd.sig_date
LEFT JOIN moneyflow mf   ON mf.ts_code = sd.ts_code AND mf.trade_date = sd.sig_date
LEFT JOIN idx            ON idx.trade_date = sd.sig_date
LEFT JOIN market_state ms ON ms.trade_date = sd.sig_date
{label_filter}
ORDER BY sd.sig_date, sd.ts_code
"""


def build_feature_matrix(signal_df: pd.DataFrame, require_label: bool = True) -> pd.DataFrame:
    """构建波段策略特征矩阵 + label；require_label=True 训练用（丢弃 label 为空的行）。"""
    if signal_df.empty:
        return pd.DataFrame()

    sig = signal_df[["ts_code", "trade_date", "mode"]].copy()
    sig["trade_date"] = sig["trade_date"].astype(str).str.replace("-", "").str[:8]

    sql = _SQL.format(
        exitwin=EXIT_WINDOW, tp=TAKE_PROFIT,
        label_filter="WHERE exj.tp_win OR exj.stop_seq IS NOT NULL OR exj.nfut >= %d" % EXIT_WINDOW
                     if require_label else "",
    )

    t0 = time.time()
    con = _duckdb.connect(_DUCKDB_PATH, read_only=True)
    try:
        con.register("signals_raw", sig)
        con.execute("""
            CREATE OR REPLACE TEMP VIEW signals_typed AS
            SELECT ts_code,
                   CAST(strptime(trade_date, '%Y%m%d') AS DATE) AS sig_date,
                   mode
            FROM signals_raw
        """)
        df = con.execute(sql).df()
    finally:
        con.close()

    print(f"  [features_swing_v2] {len(df)} rows, {time.time()-t0:.1f}s", flush=True)
    return df


def main():
    """从候选 CSV 构建特征矩阵并写出，打印 label 正例率与收益分布。"""
    sig = pd.read_csv(_CSV, dtype={"ts_code": str})
    df = build_feature_matrix(sig, require_label=True)
    if df.empty:
        print("无特征行")
        return

    lab = df["label"].dropna()
    print(f"\n样本 {len(df)}  有效 label {len(lab)}  正例率 {lab.mean()*100:.1f}%")
    rr = df["ret_real"].dropna()
    print(f"ret_real: 均值 {rr.mean()*100:.2f}%  中位 {rr.median()*100:.2f}%  "
          f"胜率(>0) {(rr>0).mean()*100:.1f}%  p10/p90 {rr.quantile(.1)*100:.1f}%/{rr.quantile(.9)*100:.1f}%")
    print(f"hold_days 均值 {df['hold_days'].mean():.1f}")
    miss = df[FEATURE_COLS].isna().mean().sort_values(ascending=False)
    print("\n特征缺失率 Top8:")
    print((miss.head(8) * 100).round(1).to_string())

    df.to_csv(_OUT_CSV, index=False)
    print(f"\n特征矩阵已写出: {_OUT_CSV}")


if __name__ == "__main__":
    main()
