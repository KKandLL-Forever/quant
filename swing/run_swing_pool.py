"""
run_swing_pool.py — 个股波段股票池验证:效率系数 ER 选池 + 唐奇安/Supertrend 向前测

验证"选对池子有没有用":PIT 在选池日按过去1年 Kaufman 效率系数 ER 把流动性股分成
高ER池/低ER池(ER=|区间净变动|/Σ|日变动|,越高越走趋势),再向前测两个检测器,
对比两池表现。若高ER池显著更好 → ER 筛选有效,趋势波段就该挑高ER的票做。

PIT 要点:ER 用选池日之前的数据算,测试只用之后的数据;选池日剔 ST/北交/次新/退市风险。

环境：.venv312。用法：python swing/run_swing_pool.py --asof 2021-12-31
依赖：DuckDB(daily/daily_basic/adj_factor/stock_st/stock_meta);复用 run_detectors 指标。
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.expanduser("~/AI/quart/first10"))

import duckdb
import numpy as np
import pandas as pd

from cache_tushare import DUCKDB_PATH
import run_detectors as D

ER_WIN = 250
POOL = 80
ADX_THR = 20


def _er(close):
    """Kaufman 效率系数:|首末净变动| / Σ|日变动|,取最后 ER_WIN 段。"""
    c = close.values[-ER_WIN:]
    if len(c) < ER_WIN // 2:
        return np.nan
    net = abs(c[-1] - c[0])
    noise = np.abs(np.diff(c)).sum()
    return net / noise if noise > 0 else np.nan


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", default="2021-12-31", help="选池日(用其之前算ER,之后做测试)")
    args = ap.parse_args()
    asof = args.asof

    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    sel_day = con.execute("SELECT MAX(trade_date) FROM daily_basic WHERE trade_date<=?", [asof]).fetchone()[0]
    liquid = con.execute("""
        SELECT ts_code FROM daily_basic WHERE trade_date=? AND ts_code NOT LIKE '%.BJ'
          AND ts_code NOT IN (SELECT ts_code FROM stock_st WHERE trade_date=?)
        ORDER BY circ_mv DESC LIMIT 1500""", [sel_day, sel_day]).fetchall()
    liquid = [r[0] for r in liquid]
    meta = con.execute("SELECT ts_code, name, list_date, delist_date FROM stock_meta").fetch_df()
    px = con.execute("""
        SELECT d.ts_code, d.trade_date, d.high*a.adj_factor h, d.low*a.adj_factor l, d.close*a.adj_factor c
        FROM daily d JOIN adj_factor a ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date
        WHERE d.trade_date>=? AND d.ts_code IN (SELECT UNNEST(?))""",
        ["2020-06-01", liquid]).fetch_df()
    con.close()

    px["trade_date"] = pd.to_datetime(px["trade_date"])
    meta = meta.set_index("ts_code")
    meta["list_date"] = pd.to_datetime(meta["list_date"], format="%Y%m%d", errors="coerce")
    meta["delist_date"] = pd.to_datetime(meta["delist_date"], format="%Y%m%d", errors="coerce")
    asof_ts = pd.Timestamp(sel_day)

    ok = set(meta.index[(meta["list_date"] <= asof_ts - pd.Timedelta(days=365)) &
                        (meta["delist_date"].isna() | (meta["delist_date"] > asof_ts + pd.Timedelta(days=365)))])
    ers = {}
    for ts, g in px[px["trade_date"] <= asof_ts].groupby("ts_code"):
        if ts in ok:
            ers[ts] = _er(g.sort_values("trade_date")["c"])
    er = pd.Series(ers).dropna().sort_values(ascending=False)
    high_pool = list(er.index[:POOL])
    low_pool = list(er.index[-POOL:])
    print(f"选池日 {asof_ts.date()} | 流动性候选 {len(liquid)} | 合格 {len(er)} | "
          f"高ER均值 {er[:POOL].mean():.2f} | 低ER均值 {er[-POOL:].mean():.2f}")

    test = px[px["trade_date"] > asof_ts]

    def run_pool(codes):
        agg = {"唐奇安+ADX": [], "Supertrend+ADX": [], "买入持有": []}
        for ts in codes:
            g = test[test["ts_code"] == ts].sort_values("trade_date")
            if len(g) < 120:
                continue
            c, h, l = g["c"].reset_index(drop=True), g["h"].reset_index(drop=True), g["l"].reset_index(drop=True)
            gate = (D._adx(h, l, c) > ADX_THR).astype(int)
            don = D._donchian_pos(c, h, l, 55, 20)
            st = D._supertrend(h, l, c, 10, 3.0)
            for nm, pos in [("唐奇安+ADX", don), ("Supertrend+ADX", st)]:
                pp = pd.Series((pos.values.astype(int) & gate.values).astype(float), index=c.index)
                _, (tot, ann, mdd, sh, tr, expo) = D._bt(c, pp, None)
                agg[nm].append((tot, mdd, sh))
            nav = np.cumprod(1 + c.pct_change().fillna(0).values)
            bdd = ((nav - np.maximum.accumulate(nav)) / np.maximum.accumulate(nav)).min()
            agg["买入持有"].append((nav[-1] - 1, bdd, 0))
        return agg

    def summ(agg, nm):
        a = np.array(agg[nm])
        return a[:, 0].mean(), a[:, 1].mean(), np.median(a[:, 2]) if nm != "买入持有" else 0, len(a)

    hi, lo = run_pool(high_pool), run_pool(low_pool)
    print(f"\n测试期 {test['trade_date'].min().date()}~{test['trade_date'].max().date()}  (池内均值)")
    print(f"{'池/策略':<22}{'平均收益':>9}{'平均回撤':>9}{'夏普中位':>9}{'股数':>6}")
    for pool_nm, agg in [("高ER", hi), ("低ER", lo)]:
        for nm in ["买入持有", "唐奇安+ADX", "Supertrend+ADX"]:
            r, d, s, n = summ(agg, nm)
            sh = f"{s:.2f}" if nm != "买入持有" else "—"
            print(f"{pool_nm+' '+nm:<22}{r*100:>8.0f}%{d*100:>8.0f}%{sh:>9}{n:>6}")


if __name__ == "__main__":
    main()
