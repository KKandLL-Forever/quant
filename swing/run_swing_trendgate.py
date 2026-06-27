"""
run_swing_trendgate.py — 个股波段"长期趋势健康"准入闸门验证

回答:在唐奇安+ADX 之上,再加一道"长期趋势没走坏才持有"的池子准入闸门,有没有用?
对全流动性股每只跑两版对比:
  A = 唐奇安55/20 + ADX>20
  B = A 且 长期趋势健康(收盘>MA120 且 MA120 上行)  ← 走坏(跌破/120线拐头向下)就踢出不碰
看 B 是否比 A 收益更高/回撤更小。PIT:MA120/ADX 只用过去数据,测试期 asof 之后。

环境：.venv312。用法：python swing/run_swing_trendgate.py --asof 2021-12-31 --n 600
依赖：DuckDB(daily/daily_basic/adj_factor/stock_st/stock_meta);复用 run_detectors。
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

ADX_THR = 20


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", default="2021-12-31")
    ap.add_argument("--n", type=int, default=600, help="流动性前 N 只")
    args = ap.parse_args()

    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    sel_day = con.execute("SELECT MAX(trade_date) FROM daily_basic WHERE trade_date<=?", [args.asof]).fetchone()[0]
    liquid = [r[0] for r in con.execute("""
        SELECT ts_code FROM daily_basic WHERE trade_date=? AND ts_code NOT LIKE '%.BJ'
          AND ts_code NOT IN (SELECT ts_code FROM stock_st WHERE trade_date=?)
        ORDER BY circ_mv DESC LIMIT ?""", [sel_day, sel_day, args.n]).fetchall()]
    meta = con.execute("SELECT ts_code, list_date, delist_date FROM stock_meta").fetch_df()
    px = con.execute("""
        SELECT d.ts_code, d.trade_date, d.high*a.adj_factor h, d.low*a.adj_factor l, d.close*a.adj_factor c
        FROM daily d JOIN adj_factor a ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date
        WHERE d.trade_date>=? AND d.ts_code IN (SELECT UNNEST(?))""", ["2020-06-01", liquid]).fetch_df()
    con.close()

    px["trade_date"] = pd.to_datetime(px["trade_date"])
    meta = meta.set_index("ts_code")
    meta["list_date"] = pd.to_datetime(meta["list_date"], format="%Y%m%d", errors="coerce")
    meta["delist_date"] = pd.to_datetime(meta["delist_date"], format="%Y%m%d", errors="coerce")
    asof_ts = pd.Timestamp(sel_day)
    ok = set(meta.index[(meta["list_date"] <= asof_ts - pd.Timedelta(days=365)) &
                        (meta["delist_date"].isna() | (meta["delist_date"] > asof_ts + pd.Timedelta(days=365)))])

    res = {"A 唐奇安+ADX": [], "B +趋势健康闸门": [], "买入持有": []}
    bbeats = 0
    n_used = 0
    for ts, g in px.groupby("ts_code"):
        if ts not in ok:
            continue
        g = g.sort_values("trade_date").reset_index(drop=True)
        c, h, l = g["c"], g["h"], g["l"]
        ma120 = c.rolling(120).mean()
        healthy = (c > ma120) & (ma120 > ma120.shift(20))
        gate = (D._adx(h, l, c) > ADX_THR).astype(int)
        don = D._donchian_pos(c, h, l, 55, 20)
        posA = pd.Series((don.values.astype(int) & gate.values).astype(float), index=c.index)
        posB = pd.Series((posA.values.astype(int) & healthy.fillna(False).values.astype(int)).astype(float), index=c.index)

        mask = (g["trade_date"] > asof_ts).values
        if mask.sum() < 120:
            continue
        ct = c[mask].reset_index(drop=True)
        for nm, pos in [("A 唐奇安+ADX", posA), ("B +趋势健康闸门", posB)]:
            pt = pos[mask].reset_index(drop=True)
            _, (tot, ann, mdd, sh, tr, expo) = D._bt(ct, pt, None)
            res[nm].append((tot, mdd, sh, expo))
        nav = np.cumprod(1 + ct.pct_change().fillna(0).values)
        bdd = ((nav - np.maximum.accumulate(nav)) / np.maximum.accumulate(nav)).min()
        res["买入持有"].append((nav[-1] - 1, bdd, 0, 1))
        if res["B +趋势健康闸门"][-1][2] > res["A 唐奇安+ADX"][-1][2]:
            bbeats += 1
        n_used += 1

    print(f"选池日 {asof_ts.date()} | 流动性前{args.n} | 实测 {n_used} 只 | "
          f"测试期 {px[px['trade_date']>asof_ts]['trade_date'].min().date()}~{px['trade_date'].max().date()}")
    print(f"\n{'策略':<20}{'平均收益':>9}{'平均回撤':>9}{'夏普中位':>9}{'平均在场':>9}")
    for nm in ["买入持有", "A 唐奇安+ADX", "B +趋势健康闸门"]:
        a = np.array(res[nm])
        sh = f"{np.median(a[:,2]):.2f}" if nm != "买入持有" else "—"
        print(f"{nm:<20}{a[:,0].mean()*100:>8.0f}%{a[:,1].mean()*100:>8.0f}%{sh:>9}{a[:,3].mean()*100:>8.0f}%")
    print(f"\nB 夏普优于 A 的个股占比:{bbeats/n_used*100:.0f}%")


if __name__ == "__main__":
    main()
