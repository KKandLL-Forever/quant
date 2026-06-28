"""
cne6_factors.py — Barra CNE6 中国风格因子库(全套 16 因子,从 DuckDB 计算,输出 (datetime,instrument))

实现 MSCI Barra CNE6 中国 A 股模型的 16 个风格因子。价量/估值类只用 daily/daily_basic/index_daily;
财务类用 fina_indicator/forecast 做 PIT as-of join(每个交易日取 ann_date<=当日的最新财报值)。
横截面标准化用按日 winsorize+zscore;ResidualVol 正交于 Beta/Size、MidCap 正交于 Size。

公式按 CNE6 标准定义,部分描述子做了务实简化(已注明)。16 个因子:
  size                 LNCAP = ln(总市值)
  midcap               非线性市值 = zscore(Size)³ 正交于 Size
  beta                 对市场(上证)252日 EWM(hl63)回归 beta
  momentum             RSTR = 21日滞后对数收益 hl126 EWMA(≈504日加权)
  resvol               0.74·DASTD+0.16·CMRA+0.10·HSIGMA,正交于 Beta/Size
  liquidity            0.35·STOM+0.35·STOQ+0.30·STOA(月/季/年对数换手)
  long_term_reversal   过去 ~13~50个月的累计收益(长期反转,IC 预期为负)
  btop                 1/PB(价值)
  earnings_yield       0.68·EPFWD+0.21·CETOP+0.11·ETOP
  dividend_yield       dv_ttm(股息率)
  growth               0.30·预测净利变动+0.30·净利yoy+0.40·营收yoy(简化5年回归)
  leverage             0.5·债/资产+0.5·资产/权益(省略MLEV市值杠杆版)
  profitability        ROE/ROA/毛利率/资产周转 等权(质量)
  investment_quality   −资产增速(扩张快的未来跑输)
  earnings_quality     −应计利润(eps−每股OCF,应计高=质量低)
  earnings_variability −净利yoy滚动波动(盈利越不稳越差)

用法：python qlib_workflow/momentum/cne6_factors.py   # 计算 + 打印各因子单因子 Rank IC(全程/OOS)
环境：.venv312。依赖：DuckDB(daily/adj_factor/daily_basic/index_daily/fina_indicator/forecast/hs300_members)；scipy。
"""

import os
import sys

sys.path.insert(0, os.path.expanduser("~/AI/quart"))

import duckdb
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from cache_tushare import DUCKDB_PATH

MARKET = "000001.SH"
START_CALC = "2016-01-01"
IC_START, OOS_START, END = "2020-01-01", "2024-01-01", "2026-05-30"


def _universe(con):
    return [c for (c,) in con.execute("SELECT DISTINCT con_code FROM hs300_members").fetchall()]


def _load(con, codes):
    """价量/估值宽表:前复权收盘、对数收益、市场收益、总市值、pb、pe_ttm、换手、总股本、股息率。"""
    ph = ",".join(["?"] * len(codes))
    px = con.execute(f"""
        SELECT d.trade_date, d.ts_code, d.close*a.adj_factor AS adj,
               db.total_mv, db.pb, db.pe_ttm, db.turnover_rate, db.total_share, db.dv_ttm
        FROM daily d JOIN adj_factor a ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date
        LEFT JOIN daily_basic db ON db.ts_code=d.ts_code AND db.trade_date=d.trade_date
        WHERE d.ts_code IN ({ph}) AND d.trade_date>=? ORDER BY d.trade_date
    """, list(codes) + [START_CALC]).fetch_df()
    px["trade_date"] = pd.to_datetime(px["trade_date"])
    wide = lambda c: px.pivot(index="trade_date", columns="ts_code", values=c).sort_index()
    close = wide("adj")
    ret = np.log(close / close.shift(1))
    mkt = con.execute("SELECT trade_date, close FROM index_daily WHERE ts_code=? AND trade_date>=? ORDER BY trade_date",
                      [MARKET, START_CALC]).fetch_df()
    mkt["trade_date"] = pd.to_datetime(mkt["trade_date"])
    mret = np.log(mkt.set_index("trade_date")["close"]).diff().reindex(ret.index)
    return dict(close=close, ret=ret, mret=mret, mv=wide("total_mv"), pb=wide("pb"),
                pe=wide("pe_ttm"), turn=wide("turnover_rate"), tshare=wide("total_share"), dv=wide("dv_ttm"))


def _zscore_xs(df):
    return df.sub(df.mean(axis=1), axis=0).div(df.std(axis=1).replace(0, np.nan), axis=0)


def _winsor_xs(df, k=3.0):
    mu, sd = df.mean(axis=1), df.std(axis=1)
    return df.clip(lower=(mu - k * sd), upper=(mu + k * sd), axis=0)


def _ortho_xs(target, factors):
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


def _asof_wide(con, sql, codes, idx):
    """财务 PIT as-of:取 ann_date<=交易日的最新值,前向填充到每个交易日。"""
    df = con.execute(sql.format(ph=",".join(["?"] * len(codes))), list(codes)).fetch_df().dropna(subset=["val"])
    if df.empty:
        return pd.DataFrame(index=idx, columns=codes, dtype=float)
    df["ann_date"] = pd.to_datetime(df["ann_date"])
    df = df.sort_values(["ts_code", "ann_date", "end_date"]).drop_duplicates(["ts_code", "ann_date"], keep="last")
    w = df.pivot(index="ann_date", columns="ts_code", values="val").sort_index()
    return w.reindex(idx.union(w.index)).sort_index().ffill().reindex(idx)


def _asof_var(con, col, codes, idx, win=8):
    """盈利波动:按报告期对 col 算滚动 std,再 as-of 到每个交易日。"""
    df = con.execute(f"SELECT ts_code, ann_date, end_date, {col} AS val FROM fina_indicator "
                     f"WHERE ts_code IN ({','.join(['?'] * len(codes))})", list(codes)).fetch_df().dropna(subset=["val"])
    if df.empty:
        return pd.DataFrame(index=idx, columns=codes, dtype=float)
    df["ann_date"] = pd.to_datetime(df["ann_date"])
    df = df.sort_values(["ts_code", "end_date"])
    df["v"] = df.groupby("ts_code")["val"].transform(lambda s: s.rolling(win, min_periods=4).std())
    df = df.dropna(subset=["v"]).sort_values(["ts_code", "ann_date", "end_date"]).drop_duplicates(["ts_code", "ann_date"], keep="last")
    w = df.pivot(index="ann_date", columns="ts_code", values="v").sort_index()
    return w.reindex(idx.union(w.index)).sort_index().ffill().reindex(idx)


def compute_all(con, codes):
    """计算 CNE6 全套 16 个因子,返回 ({name: (datetime,instrument) Series}, close)。"""
    L = _load(con, codes)
    close, ret, mret, mv, pb, pe, turn, tshare, dv = (L[k] for k in
        ["close", "ret", "mret", "mv", "pb", "pe", "turn", "tshare", "dv"])
    idx = close.index
    f = {}

    f["size"] = np.log(mv)
    f["midcap"] = _ortho_xs(_zscore_xs(np.log(mv)) ** 3, [np.log(mv)])
    f["btop"] = 1.0 / pb.replace(0, np.nan)
    f["dividend_yield"] = dv
    f["momentum"] = ret.shift(21).ewm(halflife=126, min_periods=250).mean()
    f["long_term_reversal"] = ret.shift(273).rolling(780, min_periods=400).sum()

    erm = ret.mul(mret, axis=0).ewm(halflife=63, min_periods=120).mean()
    er = ret.ewm(halflife=63, min_periods=120).mean()
    em = mret.ewm(halflife=63, min_periods=120).mean()
    em2 = (mret ** 2).ewm(halflife=63, min_periods=120).mean()
    var = (em2 - em ** 2).replace(0, np.nan)
    beta = erm.sub(er.mul(em, axis=0), axis=0).div(var, axis=0)
    f["beta"] = beta

    dastd = ret.ewm(halflife=42, min_periods=120).std()
    hsigma = ret.sub(beta.mul(mret, axis=0)).ewm(halflife=63, min_periods=120).std()
    sums = np.stack([ret.rolling(21 * k, min_periods=21 * k).sum().values for k in range(1, 13)])
    cmra = pd.DataFrame(np.nanmax(sums, axis=0) - np.nanmin(sums, axis=0), index=idx, columns=ret.columns)
    f["resvol"] = _ortho_xs(0.74 * _zscore_xs(dastd) + 0.16 * _zscore_xs(cmra) + 0.10 * _zscore_xs(hsigma), [beta, f["size"]])

    t = (turn / 100.0).replace(0, np.nan)
    f["liquidity"] = (0.35 * _zscore_xs(np.log(t.rolling(21, min_periods=15).sum()))
                      + 0.35 * _zscore_xs(np.log(t.rolling(63, min_periods=40).sum() / 3.0))
                      + 0.30 * _zscore_xs(np.log(t.rolling(252, min_periods=120).sum() / 12.0)))

    q = lambda c, tbl="fina_indicator", extra="": _asof_wide(
        con, f"SELECT ts_code, ann_date, end_date, {c} AS val FROM {tbl} WHERE ts_code IN ({{ph}}){extra}", codes, idx)
    ocfps, npyoy, tryoy = q("ocfps"), q("netprofit_yoy"), q("tr_yoy")
    dtoa, a2e, eps_q = q("debt_to_assets"), q("assets_to_eqt"), q("eps")
    roe, roa, gpm, aturn, assets_yoy = q("roe"), q("roa"), q("grossprofit_margin"), q("assets_turn"), q("assets_yoy")
    npfwd = q("(net_profit_min+net_profit_max)/2", "forecast", " AND net_profit_min IS NOT NULL")
    pchg = q("(p_change_min+p_change_max)/2", "forecast", " AND p_change_min IS NOT NULL")

    etop = 1.0 / pe.replace(0, np.nan)
    cetop = ocfps * tshare / mv.replace(0, np.nan)
    epfwd = npfwd / mv.replace(0, np.nan)
    f["earnings_yield"] = 0.68 * _zscore_xs(epfwd) + 0.21 * _zscore_xs(cetop) + 0.11 * _zscore_xs(etop)
    f["growth"] = 0.30 * _zscore_xs(pchg) + 0.30 * _zscore_xs(npyoy) + 0.40 * _zscore_xs(tryoy)
    f["leverage"] = 0.5 * _zscore_xs(dtoa) + 0.5 * _zscore_xs(a2e)
    f["profitability"] = (_zscore_xs(roe) + _zscore_xs(roa) + _zscore_xs(gpm) + _zscore_xs(aturn)) / 4.0
    f["investment_quality"] = -_zscore_xs(assets_yoy)
    f["earnings_quality"] = -_zscore_xs(eps_q - ocfps)
    f["earnings_variability"] = -_asof_var(con, "netprofit_yoy", codes, idx)

    out = {}
    for name, df in f.items():
        s = _winsor_xs(df).stack()
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
    print(f"池 {len(codes)} 只,计算 CNE6 全套 16 因子...")
    factors, close = compute_all(con, codes)
    con.close()
    fwd = (close.shift(-1) / close - 1).stack()
    fwd.index = fwd.index.set_names(["datetime", "instrument"])
    print(f"\n{'因子':<22}{'全程IC':>10}{'全程IR':>9}{'OOS_IC':>10}{'OOS_IR':>9}")
    for name, s in factors.items():
        ic_a, ir_a = _ic(s, fwd, IC_START, END)
        ic_o, ir_o = _ic(s, fwd, OOS_START, END)
        print(f"{name:<22}{ic_a:>+10.4f}{ir_a:>+9.3f}{ic_o:>+10.4f}{ir_o:>+9.3f}")


if __name__ == "__main__":
    main()
