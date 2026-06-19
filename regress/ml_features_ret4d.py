"""
ml_features_ret4d.py — 首板「4天超额收益回归」专用特征 + label（独立于 1to2，自包含）

特征按单因子 IC 诊断（diag_ret4d_factors.py）挑出，主题是「首板的均值反转」：
首板这一层，已涨多/位置高/超买/换手过热的票，4天后跑输；位置低、有主力净流入、市值不太小的跑赢。
与连板的动量逻辑相反。所有特征均在信号日 T0 收盘可知，PIT 安全、无未来函数。

label（回归，连续值）：
  个股 close[T+5]/open[T+1] − 1  减去  中证1000(000852.SH) 同期收益（超额收益，去大盘 beta）
  口径：首板日 T0 盘后选 → T+1 开盘买 → T+5 收盘卖（持有 4 个完整交易日）
  ±30% winsorize，避免连续涨停的极端正样本主导。

用法：
  from ml_features_ret4d import build_feature_matrix, FEATURE_COLS, FEATURE_CN
  feat = build_feature_matrix(signal_df, require_label=True)
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
    "dist_ma10", "dist_ma20", "mom_5", "dist_hi20",
    "bias2_hfq", "rsi_hfq_12", "topdays",
    "winner_rate", "chip_width",
    "turn_accel", "turnover_rate_f", "vr5",
    "net_mf_ratio", "elg_net_ratio",
    "circ_mv_log",
]

FEATURE_CN = {
    "dist_ma10":       "距10日均线(涨多了→看跌)",
    "dist_ma20":       "距20日均线",
    "mom_5":           "5日动量(近期涨幅→反转)",
    "dist_hi20":       "距20日高点",
    "bias2_hfq":       "BIAS乖离(超买→看跌)",
    "rsi_hfq_12":      "RSI12(超买→看跌)",
    "topdays":         "创阶段新高天数(越多越看跌)",
    "winner_rate":     "获利盘比例(越高越看跌)",
    "chip_width":      "筹码分散度((95%-5%)/收盘)",
    "turn_accel":      "量能加速(5日/20日均量)",
    "turnover_rate_f": "自由流通换手(越热越看跌)",
    "vr5":             "5日量比",
    "net_mf_ratio":    "净主力额/成交额(越大越看涨)",
    "elg_net_ratio":   "特大单净额/成交额",
    "circ_mv_log":     "流通市值(log，越大越看涨)",
}


_FACTOR_SQL = """
WITH s AS (SELECT ts_code, CAST(strptime(trade_date,'%Y%m%d') AS DATE) sig_date, trade_date d0 FROM sig_reg),
dwin AS (
    SELECT ts_code, trade_date, close, high, vol, amount,
        close/NULLIF(LAG(close,5)  OVER w,0)-1 AS mom_5,
        close/NULLIF(AVG(close) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 9  PRECEDING AND CURRENT ROW),0)-1 AS dist_ma10,
        close/NULLIF(AVG(close) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW),0)-1 AS dist_ma20,
        close/NULLIF(MAX(high)  OVER (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW),0)-1 AS dist_hi20,
        vol/NULLIF(AVG(vol) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING),0) AS vr5,
        AVG(vol) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW)
          /NULLIF(AVG(vol) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW),0) AS turn_accel
    FROM daily
    WINDOW w AS (PARTITION BY ts_code ORDER BY trade_date)
),
idx AS (SELECT trade_date, open io, close ic FROM index_daily WHERE ts_code='{IDX}'),
lab AS (
    SELECT s.ts_code, s.d0,
        LEAD(d.open,1)  OVER w buy_open,  LEAD(d.trade_date,1) OVER w buy_date,
        LEAD(d.close,5) OVER w sell_close, LEAD(d.trade_date,5) OVER w sell_date
    FROM s JOIN daily d ON d.ts_code=s.ts_code AND d.trade_date>=s.sig_date
    WINDOW w AS (PARTITION BY s.ts_code, s.sig_date ORDER BY d.trade_date)
    QUALIFY d.trade_date = s.sig_date
)
SELECT s.d0 AS trade_date, s.ts_code,
    d.dist_ma10, d.dist_ma20, d.mom_5, d.dist_hi20, d.vr5, d.turn_accel,
    db.turnover_rate_f,
    CASE WHEN db.circ_mv>0 THEN ln(db.circ_mv) END AS circ_mv_log,
    CASE WHEN d.amount>0 THEN mf.net_mf_amount/d.amount END AS net_mf_ratio,
    CASE WHEN d.amount>0 THEN (mf.buy_elg_amount-mf.sell_elg_amount)/d.amount END AS elg_net_ratio,
    cy.winner_rate,
    CASE WHEN d.close>0 THEN (cy.cost_95pct-cy.cost_5pct)/d.close END AS chip_width,
    sf.bias2_hfq, sf.rsi_hfq_12, sf.topdays,
    CASE WHEN lab.buy_open>0 AND ib.io>0 AND lab.sell_close IS NOT NULL
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


def build_feature_matrix(signal_df: pd.DataFrame, require_label: bool = True) -> pd.DataFrame:
    """构建特征矩阵（15 个反转/资金/市值因子）+ 4天超额收益 label。require_label=True 时丢掉无 label 的样本。"""
    if signal_df.empty:
        return pd.DataFrame()
    con = duckdb.connect(DUCK_PATH, read_only=True)
    try:
        con.register("sig_reg", signal_df[["ts_code", "trade_date"]])
        df = con.execute(_FACTOR_SQL.replace("{IDX}", INDEX_CODE)).df()
    finally:
        con.close()
    df["label"] = df["label_raw"].clip(-WINSOR, WINSOR)
    df = df.drop(columns=["label_raw"])
    if require_label:
        df = df.dropna(subset=["label"]).reset_index(drop=True)
    return df
