"""
ml_features_strong.py — 「非涨停强势股」池 的 4天超额收益回归 特征 + 可买入 label（自包含）

信号池：当日涨 5%~9.5%（强势非涨停）+ 量比>1.8（放量），由 ml_train_strong 的 _SIGNAL_SQL 给出。
特征按 diag_ret4d_strong 的单因子 IC 挑出，主题仍是「均值反转」：涨多/换手过热/超买的 4 天后跑输，
有主力+机构净流入、散户没追的跑赢。所有特征 T0 收盘可知，PIT 安全。

label（回归，连续值）：close[T+5]/open[T+1]−1 减 中证1000同期（超额），±30% winsorize。
  ⚠️ 可买入过滤：T+1 开盘即一字涨停（开=高且涨幅>9.5%）→ 买不进 → label 置空、训练/回测剔除。
  这是从这条线第一天起就固化的铁律：只认可成交的入场。
"""

import os
import sys

import pandas as pd

_QUART_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _QUART_ROOT)

import duckdb
from db_loader import _ENV

DUCK_PATH = _ENV.get("LOCAL_DUCKDB_PATH", "stock_data_tushare.duckdb")
INDEX_CODE = "000852.SH"
WINSOR = 0.30

FEATURE_COLS = [
    "turnover_rate_f", "dist_ma10", "dist_ma20", "mom_5", "mom_10",
    "bias2_hfq", "rsi_hfq_12", "turn_accel", "vol_20", "amt_log",
    "net_mf_ratio", "inst_net_ratio", "sm_net_ratio", "winner_rate", "circ_mv_log",
]

FEATURE_CN = {
    "turnover_rate_f": "自由流通换手(越热越看跌)",
    "dist_ma10":       "距10日均线(涨多了→看跌)",
    "dist_ma20":       "距20日均线",
    "mom_5":           "5日动量(近期涨幅→反转)",
    "mom_10":          "10日动量",
    "bias2_hfq":       "BIAS乖离(超买→看跌)",
    "rsi_hfq_12":      "RSI12(超买→看跌)",
    "turn_accel":      "量能加速(5日/20日均量)",
    "vol_20":          "20日波动率",
    "amt_log":         "成交额(log)",
    "net_mf_ratio":    "净主力额/成交额(越大越看涨)",
    "inst_net_ratio":  "大+特大单净额/成交额(机构,看涨)",
    "sm_net_ratio":    "小单净额/成交额(散户追入→看跌)",
    "winner_rate":     "获利盘比例(越高越看跌)",
    "circ_mv_log":     "流通市值(log)",
}


_SQL = """
WITH s AS (SELECT ts_code, CAST(strptime(trade_date,'%Y%m%d') AS DATE) sig_date, trade_date d0 FROM sig_reg),
dwin AS (
    SELECT ts_code, trade_date, close, high, vol, amount,
        close/NULLIF(LAG(close,5)  OVER w,0)-1 AS mom_5,
        close/NULLIF(LAG(close,10) OVER w,0)-1 AS mom_10,
        stddev_samp(pct_chg) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS vol_20,
        close/NULLIF(AVG(close) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 9  PRECEDING AND CURRENT ROW),0)-1 AS dist_ma10,
        close/NULLIF(AVG(close) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW),0)-1 AS dist_ma20,
        ln(NULLIF(amount,0)) AS amt_log,
        AVG(vol) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW)
          /NULLIF(AVG(vol) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW),0) AS turn_accel
    FROM daily
    WINDOW w AS (PARTITION BY ts_code ORDER BY trade_date)
),
idx AS (SELECT trade_date, open io, close ic FROM index_daily WHERE ts_code='{IDX}'),
lab AS (
    SELECT s.ts_code, s.d0,
        LEAD(d.open,1)  OVER w buy_open, LEAD(d.high,1) OVER w buy_high, LEAD(d.pct_chg,1) OVER w buy_pct,
        LEAD(d.trade_date,1) OVER w buy_date,
        LEAD(d.close,{SELL}) OVER w sell_close, LEAD(d.trade_date,{SELL}) OVER w sell_date
    FROM s JOIN daily d ON d.ts_code=s.ts_code AND d.trade_date>=s.sig_date
    WINDOW w AS (PARTITION BY s.ts_code, s.sig_date ORDER BY d.trade_date)
    QUALIFY d.trade_date = s.sig_date
)
SELECT s.d0 AS trade_date, s.ts_code,
    db.turnover_rate_f, d.dist_ma10, d.dist_ma20, d.mom_5, d.mom_10,
    sf.bias2_hfq, sf.rsi_hfq_12, d.turn_accel, d.vol_20, d.amt_log,
    CASE WHEN d.amount>0 THEN mf.net_mf_amount/d.amount END AS net_mf_ratio,
    CASE WHEN d.amount>0 THEN (mf.buy_lg_amount+mf.buy_elg_amount-mf.sell_lg_amount-mf.sell_elg_amount)/d.amount END AS inst_net_ratio,
    CASE WHEN d.amount>0 THEN (mf.buy_sm_amount-mf.sell_sm_amount)/d.amount END AS sm_net_ratio,
    cy.winner_rate,
    CASE WHEN db.circ_mv>0 THEN ln(db.circ_mv) END AS circ_mv_log,
    CASE WHEN lab.buy_open>0 AND ib.io>0 AND lab.sell_close IS NOT NULL
          AND NOT (lab.buy_open >= lab.buy_high - 1e-6 AND lab.buy_pct > 9.5)
         THEN (lab.sell_close/lab.buy_open-1.0) - (ix.ic/ib.io-1.0) END AS label_raw
FROM s
JOIN dwin d         ON d.ts_code=s.ts_code AND d.trade_date=s.sig_date
LEFT JOIN lab       ON lab.ts_code=s.ts_code AND lab.d0=s.d0
LEFT JOIN idx ib    ON ib.trade_date=lab.buy_date
LEFT JOIN idx ix    ON ix.trade_date=lab.sell_date
LEFT JOIN daily_basic   db ON db.ts_code=s.ts_code AND db.trade_date=s.sig_date
LEFT JOIN moneyflow     mf ON mf.ts_code=s.ts_code AND mf.trade_date=s.sig_date
LEFT JOIN cyq_perf      cy ON cy.ts_code=s.ts_code AND cy.trade_date=s.sig_date
LEFT JOIN stk_factor_pro sf ON sf.ts_code=s.ts_code AND sf.trade_date=s.sig_date
"""


def build_feature_matrix(signal_df: pd.DataFrame, require_label: bool = True,
                         hold: int = 4) -> pd.DataFrame:
    """构建强势股特征矩阵（15 个）+ 可买入超额 label（买 T+1 开盘、持有 hold 个交易日卖收盘）。
    require_label=True 时丢掉无 label（含买不进）的样本。"""
    if signal_df.empty:
        return pd.DataFrame()
    sell_offset = hold + 1
    winsor = WINSOR if hold <= 5 else min(0.10 * hold, 0.80)
    sql = _SQL.replace("{IDX}", INDEX_CODE).replace("{SELL}", str(sell_offset))
    con = duckdb.connect(DUCK_PATH, read_only=True)
    try:
        con.register("sig_reg", signal_df[["ts_code", "trade_date"]])
        df = con.execute(sql).df()
    finally:
        con.close()
    df["label"] = df["label_raw"].clip(-winsor, winsor)
    df = df.drop(columns=["label_raw"])
    if require_label:
        df = df.dropna(subset=["label"]).reset_index(drop=True)
    return df
