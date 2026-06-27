"""
run_jq_residual.py — 复刻聚宽《机器学习多因子策略》的市值残差因子(线性/随机森林/SVR 三模型)

原文思路:把对数市值 m 对基本面横截面回归
    m = α0·IND + α1·b + α2·ln(NI)⁺ + α3·I(NI<0)·ln(NI)⁺ + α4·LEV + ε
其中 IND=行业哑变量、b=对数净资产、NI=净利润、LEV=财务杠杆(负债/资产)。
**因子=残差 ε(真实市值−拟合市值)**;残差越小=相对基本面越被低估→买入。
每 10 个交易日横截面回归一次,按残差升序取前 TOPK 只等权持有,10 日后调仓。
原文用 OLS,本脚本按要求换成 线性回归 / 随机森林回归 / SVR 三种,各跑一遍对比。

与原文差异(诚实记录):
  - 行业用申万一级(sw_member,按 in_date/out_date 做 PIT 归属);股池用全 A 剔 ST/北交/次新近似中证全指。
  - PIT:财务取 ann_date≤调仓日 的最新一期,市值取调仓日当日,持有期收益用后复权收盘,杜绝未来函数。
  - 退市陷阱过滤:调仓日距 delist_date 不足 DELIST_BUFFER_DAYS(1年)的票剔除——退市整理期个股暴跌、
    市值畸小会被残差因子误判为"严重低估"专门选中,这是幸存者/前视陷阱,必须 PIT 剔掉。
  - 收益按调仓日收盘到下一调仓日收盘计,单边费 0.1%(换手部分)。

环境：.venv312。用法：python qlib_workflow/momentum/run_jq_residual.py
依赖：DuckDB(daily/daily_basic/adj_factor/balancesheet/income/stock_meta/stock_st)。
"""

import os
import sys

sys.path.insert(0, os.path.expanduser("~/AI/quart/first10"))

import duckdb
import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from sklearn.ensemble import RandomForestRegressor
from sklearn.svm import SVR
from sklearn.preprocessing import StandardScaler

from cache_tushare import DUCKDB_PATH

START, END = "2024-01-01", "2026-12-31"
STEP = 10
TOPK = 10
COST = 0.001
MIN_LIST_DAYS = 180
DELIST_BUFFER_DAYS = 365


def _models():
    """返回三个回归器(名→estimator),SVR 因量纲敏感单独配标准化。"""
    return {
        "线性回归": LinearRegression(),
        "随机森林": RandomForestRegressor(n_estimators=100, max_depth=8, min_samples_leaf=20, n_jobs=-1, random_state=0),
        "SVR": SVR(C=1.0, epsilon=0.1, kernel="rbf"),
    }


def _load(con):
    """一次性取回所有需要的数据,返回各 DataFrame。"""
    cal = [pd.Timestamp(r[0]) for r in con.execute(
        "SELECT DISTINCT trade_date FROM daily WHERE trade_date>=? ORDER BY trade_date", [START]).fetchall()]
    rebal = cal[::STEP]

    db = con.execute(
        "SELECT ts_code, trade_date, total_mv FROM daily_basic WHERE trade_date>=?", [START]).fetch_df()
    px = con.execute("""
        SELECT d.ts_code, d.trade_date, d.close*a.adj_factor AS adjclose
        FROM daily d JOIN adj_factor a ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date
        WHERE d.trade_date>=?""", [START]).fetch_df()
    bs = con.execute("""
        SELECT ts_code, ann_date, end_date, total_hldr_eqy_exc_min_int AS eqy, total_liab, total_assets
        FROM balancesheet WHERE ann_date IS NOT NULL""").fetch_df()
    inc = con.execute(
        "SELECT ts_code, ann_date, end_date, n_income FROM income WHERE ann_date IS NOT NULL").fetch_df()
    meta = con.execute("SELECT ts_code, name, industry, list_date, delist_date FROM stock_meta").fetch_df()
    sw = con.execute("SELECT ts_code, l1_name, in_date, out_date FROM sw_member").fetch_df()
    st = con.execute("SELECT DISTINCT ts_code, trade_date FROM stock_st").fetch_df()
    for df in (db, px):
        df["trade_date"] = pd.to_datetime(df["trade_date"])
    for df in (bs, inc):
        df["ann_date"] = pd.to_datetime(df["ann_date"], format="%Y%m%d", errors="coerce")
        df["end_date"] = pd.to_datetime(df["end_date"], format="%Y%m%d", errors="coerce")
    st["trade_date"] = pd.to_datetime(st["trade_date"])
    sw["in_date"] = pd.to_datetime(sw["in_date"], format="%Y%m%d", errors="coerce")
    sw["out_date"] = pd.to_datetime(sw["out_date"], format="%Y%m%d", errors="coerce")
    return rebal, db, px, bs, inc, meta, sw, st


def _asof(fin, d):
    """取每只票 ann_date<=d 的最新一期(按 end_date 再 ann_date 排序),返回 ts_code→行。"""
    sub = fin[fin["ann_date"] <= d]
    sub = sub.sort_values(["end_date", "ann_date"]).groupby("ts_code").tail(1)
    return sub.set_index("ts_code")


def _sw_asof(sw, d):
    """取每只票在 d 当日的申万一级(in_date<=d 且 out_date 为空或>d),返回 ts_code→l1_name。"""
    m = (sw["in_date"] <= d) & (sw["out_date"].isna() | (sw["out_date"] > d))
    sub = sw[m].sort_values("in_date").groupby("ts_code").tail(1)
    return sub.set_index("ts_code")["l1_name"]


def _live_universe(meta, d):
    """返回调仓日 d 可纳入的股票集合:上市满 MIN_LIST_DAYS 且距退市日>DELIST_BUFFER_DAYS(PIT 剔退市陷阱)。"""
    cutoff = d - pd.Timedelta(days=MIN_LIST_DAYS)
    alive = meta["delist_date"].isna() | (meta["delist_date"] > d + pd.Timedelta(days=DELIST_BUFFER_DAYS))
    return set(meta.index[(meta["list_date"] <= cutoff) & alive])


def _features(d, db, bs, inc, ind_asof, st_set, list_ok):
    """构造调仓日 d 的横截面特征矩阵 X、目标 m、可交易股列表。"""
    mv = db[db["trade_date"] == d].set_index("ts_code")["total_mv"]
    b = _asof(bs, d)
    i = _asof(inc, d)
    codes = mv.index.intersection(b.index).intersection(i.index).intersection(list_ok)
    codes = [c for c in codes if (c, d) not in st_set
             and not c.endswith(".BJ") and mv.get(c, 0) > 0]
    rows = []
    for c in codes:
        eqy = b.at[c, "eqy"]; ta = b.at[c, "total_assets"]; tl = b.at[c, "total_liab"]
        ni = i.at[c, "n_income"]
        if not (eqy and eqy > 0 and ta and ta > 0):
            continue
        m = np.log(mv[c])
        feat = {
            "b": np.log(eqy),
            "lnNI": np.log(abs(ni)) if ni else 0.0,
            "lnNI_neg": (np.log(abs(ni)) if ni else 0.0) if (ni is not None and ni < 0) else 0.0,
            "LEV": (tl / ta) if tl is not None else 0.0,
            "IND": ind_asof.get(c, "NA"),
        }
        rows.append((c, m, feat))
    if len(rows) < 50:
        return None
    idx = [r[0] for r in rows]
    y = pd.Series([r[1] for r in rows], index=idx)
    X = pd.DataFrame([r[2] for r in rows], index=idx)
    X = pd.get_dummies(X, columns=["IND"], dtype=float)
    return X, y


def main():
    """跑三模型市值残差策略,输出各自年化/回撤/胜率等。"""
    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    rebal, db, px, bs, inc, meta, sw, st = _load(con)
    con.close()

    meta = meta.set_index("ts_code")
    meta["list_date"] = pd.to_datetime(meta["list_date"], format="%Y%m%d", errors="coerce")
    meta["delist_date"] = pd.to_datetime(meta["delist_date"], format="%Y%m%d", errors="coerce")
    st_set = set(zip(st["ts_code"], st["trade_date"]))
    pxw = px.pivot_table(index="trade_date", values="adjclose", columns="ts_code")

    results = {name: [] for name in _models()}
    held = {name: set() for name in _models()}
    used_dates = []

    for k in range(len(rebal) - 1):
        d, d2 = rebal[k], rebal[k + 1]
        list_ok = _live_universe(meta, d)
        ind_asof = _sw_asof(sw, d)
        out = _features(d, db, bs, inc, ind_asof, st_set, list_ok)
        if out is None:
            continue
        X, y = out
        if d not in pxw.index or d2 not in pxw.index:
            continue
        fwd = (pxw.loc[d2] / pxw.loc[d] - 1.0)
        used_dates.append(d2)

        for name, est in _models().items():
            if name == "SVR":
                Xs = StandardScaler().fit_transform(X.values)
                est.fit(Xs, y.values); pred = est.predict(Xs)
            else:
                est.fit(X.values, y.values); pred = est.predict(X.values)
            resid = pd.Series(y.values - pred, index=X.index).sort_values()
            pick = list(resid.index[:TOPK])
            r = fwd.reindex(pick).dropna()
            ret = r.mean() if len(r) else 0.0
            turnover = len(set(pick) - held[name]) / max(TOPK, 1)
            ret -= COST * turnover * 2
            held[name] = set(pick)
            results[name].append(ret)

    print(f"调仓次数 {len(used_dates)} | {START}~{used_dates[-1] if used_dates else '?'} | 每期持有{TOPK}只 | {STEP}日调仓\n")
    bench = []
    for k in range(len(rebal) - 1):
        d, d2 = rebal[k], rebal[k + 1]
        if d in pxw.index and d2 in pxw.index:
            bench.append((pxw.loc[d2] / pxw.loc[d] - 1.0).mean())
    periods_per_year = 252 / STEP

    def stats(rets):
        rets = np.array(rets)
        nav = np.cumprod(1 + rets)
        total = nav[-1] - 1
        ann = nav[-1] ** (periods_per_year / len(rets)) - 1
        peak = np.maximum.accumulate(nav)
        mdd = ((nav - peak) / peak).min()
        sharpe = rets.mean() / rets.std() * np.sqrt(periods_per_year) if rets.std() > 0 else 0
        win = (rets > 0).mean()
        return total, ann, mdd, sharpe, win

    bt, ba, bm, bs_, bw = stats(bench)
    print(f"{'模型':<10}{'总收益':>9}{'年化':>9}{'最大回撤':>10}{'夏普':>7}{'胜率':>7}")
    for name in results:
        t, a, m, s, w = stats(results[name])
        print(f"{name:<10}{t*100:>8.1f}%{a*100:>8.1f}%{m*100:>9.1f}%{s:>7.2f}{w*100:>6.0f}%")
    print(f"{'等权全市场':<10}{bt*100:>8.1f}%{ba*100:>8.1f}%{bm*100:>9.1f}%{bs_:>7.2f}{bw*100:>6.0f}%")


if __name__ == "__main__":
    main()
