"""
cne5_factors.py — Barra CNE5 中国风格因子库(从 DuckDB 计算，输出 (datetime,instrument) 因子)

实现 MSCI Barra CNE5 的风格因子。Part 1(本文件当前)：7 个价量/估值因子,只用 daily/daily_basic/
index_daily,PIT 干净。Part 2(待加)：EarningsYield/Growth/Leverage,需财务表 PIT as-of join。

公式按 CNE5 标准定义,部分描述子做了务实简化(已在各函数注明)。横截面标准化用按日 winsorize+zscore;
ResidualVol 对 Beta/Size 正交、NLSize 对 Size 正交(CNE5 规定)。

已实现(Part 1):
  size       LNCAP = ln(总市值)
  beta       对市场(上证)252日 EWM(hl63)回归 beta
  momentum   RSTR = 21日滞后对数收益的 hl126 EWMA(≈504日加权)
  resvol     0.74·DASTD + 0.16·CMRA + 0.10·HSIGMA,正交于 Beta、Size
  nlsize     zscore(Size)³,正交于 Size
  btop       1/PB
  liquidity  0.35·STOM + 0.35·STOQ + 0.30·STOA(月/季/年对数换手)

用法：python qlib_workflow/momentum/cne5_factors.py   # 计算 + 打印各因子单因子 Rank IC(全程/OOS)
环境：.venv312。依赖：DuckDB(daily/adj_factor/daily_basic/index_daily/hs300_members)；scipy。
"""

import os
import sys

sys.path.insert(0, os.path.expanduser("~/AI/quart/first10"))

import duckdb
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from cache_tushare import DUCKDB_PATH

MARKET = "000001.SH"
START_CALC = "2018-01-01"
IC_START, OOS_START, END = "2020-01-01", "2024-01-01", "2026-05-30"


def _universe(con):
    rows = con.execute("SELECT DISTINCT con_code FROM hs300_members").fetchall()
    return [c for (c,) in rows]


def _load(con, codes):
    """取前复权收盘价、对数收益、市场收益、总市值、pb、pe_ttm、换手率、总股本(均 date×stock 宽表)。"""
    ph = ",".join(["?"] * len(codes))
    px = con.execute(f"""
        SELECT d.trade_date, d.ts_code, d.close*a.adj_factor AS adj,
               db.total_mv, db.pb, db.pe_ttm, db.turnover_rate, db.total_share
        FROM daily d JOIN adj_factor a ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date
        LEFT JOIN daily_basic db ON db.ts_code=d.ts_code AND db.trade_date=d.trade_date
        WHERE d.ts_code IN ({ph}) AND d.trade_date>=? ORDER BY d.trade_date
    """, list(codes) + [START_CALC]).fetch_df()
    px["trade_date"] = pd.to_datetime(px["trade_date"])
    wide = lambda col: px.pivot(index="trade_date", columns="ts_code", values=col).sort_index()
    close = wide("adj")
    ret = np.log(close / close.shift(1))
    mkt = con.execute("SELECT trade_date, close FROM index_daily WHERE ts_code=? AND trade_date>=? ORDER BY trade_date",
                      [MARKET, START_CALC]).fetch_df()
    mkt["trade_date"] = pd.to_datetime(mkt["trade_date"])
    mret = np.log(mkt.set_index("trade_date")["close"]).diff().reindex(ret.index)
    return (close, ret, mret, wide("total_mv"), wide("pb"), wide("pe_ttm"),
            wide("turnover_rate"), wide("total_share"))


def _asof_wide(con, sql, codes, daily_index):
    """财务/预测的 PIT as-of 宽表:取 ann_date<=交易日的最新值,前向填充到每个交易日。"""
    df = con.execute(sql.format(ph=",".join(["?"] * len(codes))), list(codes)).fetch_df()
    df = df.dropna(subset=["val"])
    if df.empty:
        return pd.DataFrame(index=daily_index, columns=codes, dtype=float)
    df["ann_date"] = pd.to_datetime(df["ann_date"])
    df = df.sort_values(["ts_code", "ann_date", "end_date"]).drop_duplicates(["ts_code", "ann_date"], keep="last")
    w = df.pivot(index="ann_date", columns="ts_code", values="val").sort_index()
    return w.reindex(daily_index.union(w.index)).sort_index().ffill().reindex(daily_index)


def _zscore_xs(df):
    """按日(行)横截面 zscore。"""
    return df.sub(df.mean(axis=1), axis=0).div(df.std(axis=1).replace(0, np.nan), axis=0)


def _winsor_xs(df, k=3.0):
    """按日横截面去极值(均值±k倍标准差截断)。"""
    mu, sd = df.mean(axis=1), df.std(axis=1)
    return df.clip(lower=(mu - k * sd), upper=(mu + k * sd), axis=0)


def _ortho_xs(target, factors):
    """按日把 target 对 factors 做横截面 OLS,返回残差(正交化)。"""
    out = pd.DataFrame(index=target.index, columns=target.columns, dtype=float)
    fs = [_zscore_xs(f) for f in factors]
    for d in target.index:
        y = target.loc[d]
        X = np.column_stack([np.ones(len(y))] + [f.loc[d].values for f in fs])
        m = np.isfinite(y.values) & np.isfinite(X).all(axis=1)
        if m.sum() < 20:
            continue
        coef, *_ = np.linalg.lstsq(X[m], y.values[m], rcond=None)
        out.loc[d, y.index[m]] = y.values[m] - X[m] @ coef
    return out


def compute_all(con, codes):
    """计算 CNE5 全套 10 个因子,返回 ({name: (datetime,instrument) Series}, close)。"""
    close, ret, mret, mv, pb, pe, turn, tshare = _load(con, codes)
    f = {}

    f["size"] = np.log(mv)
    f["btop"] = 1.0 / pb.replace(0, np.nan)
    f["momentum"] = ret.shift(21).ewm(halflife=126, min_periods=250).mean()

    erm = ret.mul(mret, axis=0).ewm(halflife=63, min_periods=120).mean()
    er = ret.ewm(halflife=63, min_periods=120).mean()
    em = mret.ewm(halflife=63, min_periods=120).mean()
    em2 = (mret ** 2).ewm(halflife=63, min_periods=120).mean()
    cov = erm.sub(er.mul(em, axis=0), axis=0)
    var = (em2 - em ** 2).replace(0, np.nan)
    beta = cov.div(var, axis=0)
    f["beta"] = beta

    dastd = ret.ewm(halflife=42, min_periods=120).std()
    resid = ret.sub(beta.mul(mret, axis=0))
    hsigma = resid.ewm(halflife=63, min_periods=120).std()
    sums = [ret.rolling(21 * k, min_periods=21 * k).sum() for k in range(1, 13)]
    arr = np.stack([s.values for s in sums])
    cmra = pd.DataFrame(np.nanmax(arr, axis=0) - np.nanmin(arr, axis=0), index=ret.index, columns=ret.columns)
    resvol_raw = 0.74 * _zscore_xs(dastd) + 0.16 * _zscore_xs(cmra) + 0.10 * _zscore_xs(hsigma)
    f["resvol"] = _ortho_xs(resvol_raw, [beta, f["size"]])

    f["nlsize"] = _ortho_xs(_zscore_xs(f["size"]) ** 3, [f["size"]])

    t = (turn / 100.0).replace(0, np.nan)
    stom = np.log(t.rolling(21, min_periods=15).sum())
    stoq = np.log(t.rolling(63, min_periods=40).sum() / 3.0)
    stoa = np.log(t.rolling(252, min_periods=120).sum() / 12.0)
    f["liquidity"] = 0.35 * _zscore_xs(stom) + 0.35 * _zscore_xs(stoq) + 0.30 * _zscore_xs(stoa)

    # ── Part 2:财务因子(PIT as-of join) ──
    idx = close.index
    ocfps = _asof_wide(con, "SELECT ts_code, ann_date, end_date, ocfps AS val FROM fina_indicator WHERE ts_code IN ({ph})", codes, idx)
    npyoy = _asof_wide(con, "SELECT ts_code, ann_date, end_date, netprofit_yoy AS val FROM fina_indicator WHERE ts_code IN ({ph})", codes, idx)
    tryoy = _asof_wide(con, "SELECT ts_code, ann_date, end_date, tr_yoy AS val FROM fina_indicator WHERE ts_code IN ({ph})", codes, idx)
    dtoa = _asof_wide(con, "SELECT ts_code, ann_date, end_date, debt_to_assets AS val FROM fina_indicator WHERE ts_code IN ({ph})", codes, idx)
    a2e = _asof_wide(con, "SELECT ts_code, ann_date, end_date, assets_to_eqt AS val FROM fina_indicator WHERE ts_code IN ({ph})", codes, idx)
    npfwd = _asof_wide(con, "SELECT ts_code, ann_date, end_date, (net_profit_min+net_profit_max)/2 AS val FROM forecast WHERE ts_code IN ({ph}) AND net_profit_min IS NOT NULL", codes, idx)
    pchg = _asof_wide(con, "SELECT ts_code, ann_date, end_date, (p_change_min+p_change_max)/2 AS val FROM forecast WHERE ts_code IN ({ph}) AND p_change_min IS NOT NULL", codes, idx)

    etop = 1.0 / pe.replace(0, np.nan)                       # 滚动 E/P
    cetop = ocfps * tshare / mv.replace(0, np.nan)           # 现金盈利收益率(每股OCF×股本/市值)
    epfwd = npfwd / mv.replace(0, np.nan)                    # 预测 E/P(预测净利/市值,单位均万元)
    f["earnings_yield"] = 0.68 * _zscore_xs(epfwd) + 0.21 * _zscore_xs(cetop) + 0.11 * _zscore_xs(etop)
    # 成长:CNE5 用5年回归+分析师预测,此处用 净利yoy/营收yoy/公司预测净利变动 务实替代
    f["growth"] = 0.30 * _zscore_xs(pchg) + 0.30 * _zscore_xs(npyoy) + 0.40 * _zscore_xs(tryoy)
    # 杠杆:DTOA(债/资产)+ 账面杠杆(资产/权益);省略 MLEV 市值杠杆版
    f["leverage"] = 0.5 * _zscore_xs(dtoa) + 0.5 * _zscore_xs(a2e)

    out = {}
    for name, df in f.items():
        df = _winsor_xs(df)
        s = df.stack()
        s.index = s.index.set_names(["datetime", "instrument"])
        out[name] = s
    return out, close


def _ic(factor, fwd, s, e):
    d = pd.DataFrame({"f": factor, "r": fwd}).dropna()
    dt = d.index.get_level_values("datetime")
    d = d[(dt >= pd.Timestamp(s)) & (dt <= pd.Timestamp(e))]
    ics = d.groupby(level="datetime").apply(lambda g: spearmanr(g.f, g.r).correlation if len(g) > 20 else np.nan).dropna()
    return ics.mean(), ics.mean() / ics.std()


def main():
    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    codes = _universe(con)
    print(f"池 {len(codes)} 只,计算 CNE5 全套 10 因子...")
    factors, close = compute_all(con, codes)
    con.close()

    fwd = (close.shift(-1) / close - 1).stack()
    fwd.index = fwd.index.set_names(["datetime", "instrument"])
    print(f"\n{'因子':<12}{'全程IC':>10}{'全程IR':>9}{'OOS_IC':>10}{'OOS_IR':>9}")
    for name, s in factors.items():
        ic_a, ir_a = _ic(s, fwd, IC_START, END)
        ic_o, ir_o = _ic(s, fwd, OOS_START, END)
        print(f"{name:<12}{ic_a:>+10.4f}{ir_a:>+9.3f}{ic_o:>+10.4f}{ir_o:>+9.3f}")


if __name__ == "__main__":
    main()
