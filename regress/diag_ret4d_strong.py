"""
diag_ret4d_strong.py — 「非涨停强势股」池 的 4 天超额收益 单因子 IC 筛选。

信号池（T0 收盘可判，天然可买入、无一字板逆选）：
  当日涨幅 5%~9.5%（强势但没封板）+ 量比>1.8（放量）+ 非 ST + 剔创业/科创/北交。
label：T+1 开盘买 → T+5 收盘卖，减中证1000同期（超额）。
  ⚠️ 可买入过滤：若 T+1 开盘即一字涨停（开=高且涨幅>9.5%）→ 买不进 → 该信号剔除，不计入。
候选因子同 diag_ret4d_factors（~25 个，T0 PIT 安全）。

用法：python regress/diag_ret4d_strong.py
"""

import os
import sys
import numpy as np
import pandas as pd

_QUART_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _QUART_ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import duckdb
from scipy.stats import spearmanr
from db_loader import _ENV

DUCK_PATH = _ENV.get("LOCAL_DUCKDB_PATH", "stock_data_tushare.duckdb")
INDEX_CODE = "000852.SH"

_SIGNAL_SQL = """
WITH d AS (
    SELECT ts_code, trade_date, pct_chg,
        vol/NULLIF(AVG(vol) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING),0) AS vr5
    FROM daily)
SELECT d.ts_code, strftime(d.trade_date,'%Y%m%d') AS trade_date
FROM d
LEFT JOIN stock_st st ON st.ts_code=d.ts_code AND st.trade_date=d.trade_date
WHERE d.pct_chg BETWEEN 5 AND 9.5 AND d.vr5 > 1.8
  AND d.ts_code NOT LIKE '688%' AND d.ts_code NOT LIKE '30%' AND d.ts_code NOT LIKE '%.BJ'
  AND st.ts_code IS NULL
ORDER BY d.trade_date, d.ts_code
"""

_LABEL_SQL = f"""
WITH s AS (SELECT ts_code, CAST(strptime(trade_date,'%Y%m%d') AS DATE) sig_date FROM sig_reg),
stk AS (
    SELECT s.ts_code, s.sig_date,
           LEAD(d.open,1)  OVER w buy_open,  LEAD(d.high,1) OVER w buy_high, LEAD(d.pct_chg,1) OVER w buy_pct,
           LEAD(d.trade_date,1) OVER w buy_date,
           LEAD(d.close,5) OVER w sell_close, LEAD(d.trade_date,5) OVER w sell_date
    FROM s JOIN daily d ON d.ts_code=s.ts_code AND d.trade_date>=s.sig_date
    WINDOW w AS (PARTITION BY s.ts_code, s.sig_date ORDER BY d.trade_date)
    QUALIFY d.trade_date = s.sig_date),
idx AS (SELECT trade_date, open io, close ic FROM index_daily WHERE ts_code='{INDEX_CODE}')
SELECT stk.ts_code, strftime(stk.sig_date,'%Y%m%d') trade_date,
   CASE WHEN stk.buy_open>0 AND ib.io>0 AND stk.sell_close IS NOT NULL
         AND NOT (stk.buy_open >= stk.buy_high - 1e-6 AND stk.buy_pct > 9.5)
        THEN (stk.sell_close/stk.buy_open-1.0) - (ix.ic/ib.io-1.0) END AS label
FROM stk LEFT JOIN idx ib ON ib.trade_date=stk.buy_date
         LEFT JOIN idx ix ON ix.trade_date=stk.sell_date
"""

_FACTOR_SQL = """
WITH s AS (SELECT ts_code, CAST(strptime(trade_date,'%Y%m%d') AS DATE) sig_date, trade_date d0 FROM sig_reg),
dwin AS (
    SELECT ts_code, trade_date, close, high, vol, amount, pct_chg,
        close/NULLIF(LAG(close,5)  OVER w,0)-1  AS mom_5,
        close/NULLIF(LAG(close,10) OVER w,0)-1  AS mom_10,
        close/NULLIF(LAG(close,20) OVER w,0)-1  AS mom_20,
        stddev_samp(pct_chg) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS vol_20,
        close/NULLIF(AVG(close) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 4  PRECEDING AND CURRENT ROW),0)-1 AS dist_ma5,
        close/NULLIF(AVG(close) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 9  PRECEDING AND CURRENT ROW),0)-1 AS dist_ma10,
        close/NULLIF(AVG(close) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW),0)-1 AS dist_ma20,
        close/NULLIF(MAX(high)  OVER (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW),0)-1 AS dist_hi20,
        close/NULLIF(MAX(high)  OVER (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW),0)-1 AS dist_hi60,
        ln(NULLIF(amount,0)) AS amt_log,
        vol/NULLIF(AVG(vol) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING),0) AS vr5,
        AVG(vol) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW)
          /NULLIF(AVG(vol) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW),0) AS turn_accel
    FROM daily
    WINDOW w AS (PARTITION BY ts_code ORDER BY trade_date)
)
SELECT s.d0 AS trade_date, s.ts_code,
    d.mom_5, d.mom_10, d.mom_20, d.vol_20,
    d.dist_ma5, d.dist_ma10, d.dist_ma20, d.dist_hi20, d.dist_hi60,
    d.amt_log, d.vr5, d.turn_accel, d.pct_chg/100.0 AS pct_chg,
    db.turnover_rate_f, db.volume_ratio, db.pe_ttm, db.pb, db.ps_ttm, db.dv_ratio,
    CASE WHEN db.circ_mv>0 THEN ln(db.circ_mv) END AS circ_mv_log,
    CASE WHEN db.total_share>0 THEN db.free_share/db.total_share END AS free_ratio,
    CASE WHEN d.amount>0 THEN mf.net_mf_amount/d.amount END AS net_mf_ratio,
    CASE WHEN d.amount>0 THEN (mf.buy_sm_amount-mf.sell_sm_amount)/d.amount END AS sm_net_ratio,
    CASE WHEN d.amount>0 THEN (mf.buy_elg_amount-mf.sell_elg_amount)/d.amount END AS elg_net_ratio,
    CASE WHEN d.amount>0 THEN (mf.buy_lg_amount+mf.buy_elg_amount-mf.sell_lg_amount-mf.sell_elg_amount)/d.amount END AS inst_net_ratio,
    cy.winner_rate,
    CASE WHEN cy.cost_50pct>0 THEN d.close/cy.cost_50pct-1 END AS dist_cost50,
    CASE WHEN d.close>0 THEN (cy.cost_95pct-cy.cost_5pct)/d.close END AS chip_width,
    sf.bias2_hfq, sf.rsi_hfq_12, sf.dmi_adx_hfq, sf.topdays
FROM s
JOIN dwin d        ON d.ts_code=s.ts_code AND d.trade_date=s.sig_date
LEFT JOIN daily_basic   db ON db.ts_code=s.ts_code AND db.trade_date=s.sig_date
LEFT JOIN moneyflow     mf ON mf.ts_code=s.ts_code AND mf.trade_date=s.sig_date
LEFT JOIN cyq_perf      cy ON cy.ts_code=s.ts_code AND cy.trade_date=s.sig_date
LEFT JOIN stk_factor_pro sf ON sf.ts_code=s.ts_code AND sf.trade_date=s.sig_date
"""

CANDIDATES = ["mom_5", "mom_10", "mom_20", "vol_20", "dist_ma5", "dist_ma10", "dist_ma20",
              "dist_hi20", "dist_hi60", "amt_log", "vr5", "turn_accel", "pct_chg",
              "turnover_rate_f", "volume_ratio", "pe_ttm", "pb", "ps_ttm", "dv_ratio",
              "circ_mv_log", "free_ratio", "net_mf_ratio", "sm_net_ratio", "elg_net_ratio",
              "inst_net_ratio", "winner_rate", "dist_cost50", "chip_width",
              "bias2_hfq", "rsi_hfq_12", "dmi_adx_hfq", "topdays"]

CN = {"mom_5": "5日动量", "mom_10": "10日动量", "mom_20": "20日动量", "vol_20": "20日波动率",
      "dist_ma5": "距5日均线", "dist_ma10": "距10日均线", "dist_ma20": "距20日均线",
      "dist_hi20": "距20日高点", "dist_hi60": "距60日高点", "amt_log": "成交额(log)",
      "vr5": "5日量比", "turn_accel": "量能加速", "pct_chg": "信号日涨幅",
      "turnover_rate_f": "自由流通换手", "volume_ratio": "量比", "pe_ttm": "PE_TTM",
      "pb": "PB", "ps_ttm": "PS_TTM", "dv_ratio": "股息率", "circ_mv_log": "流通市值(log)",
      "free_ratio": "自由流通占比", "net_mf_ratio": "净主力额/成交额", "sm_net_ratio": "小单净额(散户)",
      "elg_net_ratio": "特大单净额", "inst_net_ratio": "大+特大净额(机构)",
      "winner_rate": "获利盘比例", "dist_cost50": "距平均成本", "chip_width": "筹码分散度",
      "bias2_hfq": "BIAS乖离", "rsi_hfq_12": "RSI12", "dmi_adx_hfq": "ADX趋向", "topdays": "创阶段新高天数"}


def main():
    con = duckdb.connect(DUCK_PATH, read_only=True)
    sig = con.execute(_SIGNAL_SQL).df()
    print(f"非涨停强势股信号 {len(sig)}（{sig['trade_date'].min()}~{sig['trade_date'].max()}）")
    con.register("sig_reg", sig[["ts_code", "trade_date"]])
    lab = con.execute(_LABEL_SQL).df()
    fac = con.execute(_FACTOR_SQL).df()
    con.close()

    df = fac.merge(lab, on=["ts_code", "trade_date"], how="inner").dropna(subset=["label"])
    df["year"] = df["trade_date"].str[:4]
    print(f"可买入有效样本 {len(df)}（剔除T+1一字板后）  label 均值 {df['label'].mean():+.4f}  正例率 {(df['label']>0).mean():.1%}\n")

    years = sorted(df["year"].unique())
    rows = []
    for c in CANDIDATES:
        x, y = df[c].values, df["label"].values
        m = ~(np.isnan(x) | np.isnan(y))
        if m.sum() < 100 or len(np.unique(x[m])) < 5:
            continue
        ic_all = spearmanr(x[m], y[m]).correlation
        yic = []
        for yr in years:
            sub = df[df["year"] == yr]
            xx, yy = sub[c].values, sub["label"].values
            mm = ~(np.isnan(xx) | np.isnan(yy))
            yic.append(spearmanr(xx[mm], yy[mm]).correlation if mm.sum() > 50 else np.nan)
        rows.append((c, ic_all, np.nanmean(yic), np.nanstd(yic), m.mean(), yic))

    rows.sort(key=lambda r: -abs(r[1]))
    print(f"{'因子':<18s}{'全样本IC':>9s}{'年均IC':>8s}{'年IC标准差':>10s}{'覆盖':>7s}  逐年IC | 中文")
    print("-" * 108)
    for c, ic, ym, ys, cov, yic in rows:
        ystr = " ".join(f"{v:+.2f}" if not np.isnan(v) else "  - " for v in yic)
        print(f"{c:<18s}{ic:>+9.4f}{ym:>+8.3f}{ys:>10.3f}{cov:>6.0%}  [{ystr}] | {CN.get(c,'')}")
    print(f"\n逐年顺序：{years}")


if __name__ == "__main__":
    main()
