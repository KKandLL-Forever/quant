"""
run_swing_rotate.py — 集中趋势轮动(只持1-3只):趋势健康池 + 动量选最强 + 走坏即换

实盘约束:只能持有 1-3 只 → 没有分散,选错重伤。本脚本测"集中轮动":
  ① 可选池(每周动态):流动性 + 剔ST/退市 + 趋势健康(收盘>MA60 且 MA60上行);
  ② 在健康票里按动量(过去 MOM 日涨幅)选最强 K 只,等权持有;
  ③ 每周重选,持仓里趋势走坏(跌出健康)的踢掉换最强的;空头/无健康票时持币。
对比 K=1/2/3 与等权全市场基准。PIT:动量/MA 只用过去数据,次周收益为实现收益。

⚠️ 1-3 只集中持仓回测噪声极大、运气成分高,结果仅供参考,不构成承诺。

环境：.venv312。用法：python swing/run_swing_rotate.py --asof 2021-12-31 --n 800
依赖：DuckDB(daily/daily_basic/adj_factor/stock_st/stock_meta)。
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.expanduser("~/AI/quart"))

import duckdb
import numpy as np
import pandas as pd

from cache_tushare import DUCKDB_PATH

MOM, MA, STEP, COST = 60, 60, 5, 0.0006


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", default="2021-12-31")
    ap.add_argument("--n", type=int, default=800, help="流动性候选前 N")
    args = ap.parse_args()

    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    sel_day = con.execute("SELECT MAX(trade_date) FROM daily_basic WHERE trade_date<=?", [args.asof]).fetchone()[0]
    liquid = [r[0] for r in con.execute("""
        SELECT ts_code FROM daily_basic WHERE trade_date=? AND ts_code NOT LIKE '%.BJ'
          AND ts_code NOT IN (SELECT ts_code FROM stock_st WHERE trade_date=?)
        ORDER BY circ_mv DESC LIMIT ?""", [sel_day, sel_day, args.n]).fetchall()]
    meta = con.execute("SELECT ts_code, list_date, delist_date FROM stock_meta").fetch_df()
    px = con.execute("""
        SELECT d.ts_code, d.trade_date, d.close*a.adj_factor c
        FROM daily d JOIN adj_factor a ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date
        WHERE d.trade_date>=? AND d.ts_code IN (SELECT UNNEST(?))""", ["2020-09-01", liquid]).fetch_df()
    con.close()

    px["trade_date"] = pd.to_datetime(px["trade_date"])
    meta = meta.set_index("ts_code")
    meta["list_date"] = pd.to_datetime(meta["list_date"], format="%Y%m%d", errors="coerce")
    meta["delist_date"] = pd.to_datetime(meta["delist_date"], format="%Y%m%d", errors="coerce")
    asof_ts = pd.Timestamp(sel_day)
    ok = set(meta.index[(meta["list_date"] <= asof_ts - pd.Timedelta(days=365)) &
                        (meta["delist_date"].isna() | (meta["delist_date"] > asof_ts + pd.Timedelta(days=365)))])

    wide = px[px["ts_code"].isin(ok)].pivot_table(index="trade_date", values="c", columns="ts_code")
    ma = wide.rolling(MA).mean()
    healthy = (wide > ma) & (ma > ma.shift(10))
    mom = wide / wide.shift(MOM) - 1
    dates = wide.index
    test_idx = [i for i in range(len(dates)) if dates[i] > asof_ts]
    rebal = test_idx[::STEP]

    def run(K):
        nav, held = [1.0], set()
        for j in range(len(rebal) - 1):
            i, i2 = rebal[j], rebal[j + 1]
            d = dates[i]
            elig = mom.loc[d][healthy.loc[d].fillna(False)].dropna()
            pick = set(elig.sort_values(ascending=False).index[:K])
            if pick:
                r = (wide.iloc[i2][list(pick)] / wide.iloc[i][list(pick)] - 1).mean()
            else:
                r = 0.0
            turn = len(pick ^ held) / max(len(pick | held), 1)
            nav.append(nav[-1] * (1 + r - COST * turn))
            held = pick
        nav = np.array(nav)
        peak = np.maximum.accumulate(nav)
        mdd = ((nav - peak) / peak).min()
        ann = nav[-1] ** (252 / STEP / (len(nav) - 1)) - 1
        rets = np.diff(nav) / nav[:-1]
        sh = rets.mean() / rets.std() * np.sqrt(252 / STEP) if rets.std() > 0 else 0
        return nav[-1] - 1, ann, mdd, sh

    bench = [1.0]
    for j in range(len(rebal) - 1):
        i, i2 = rebal[j], rebal[j + 1]
        r = (wide.iloc[i2] / wide.iloc[i] - 1).mean()
        bench.append(bench[-1] * (1 + r))
    bnav = np.array(bench)
    bpeak = np.maximum.accumulate(bnav)
    bmdd = ((bnav - bpeak) / bpeak).min()

    print(f"选池日 {asof_ts.date()} | 候选 {len(ok & set(wide.columns))} 只 | "
          f"测试 {dates[test_idx[0]].date()}~{dates[-1].date()} | 周度重选\n")
    print(f"{'策略':<14}{'总收益':>9}{'年化':>8}{'最大回撤':>9}{'夏普':>7}")
    print(f"{'等权全市场':<14}{(bnav[-1]-1)*100:>8.0f}%{'—':>8}{bmdd*100:>8.0f}%{'—':>7}")
    for K in [1, 2, 3]:
        tot, ann, mdd, sh = run(K)
        print(f"{'持有'+str(K)+'只':<14}{tot*100:>8.0f}%{ann*100:>7.0f}%{mdd*100:>8.0f}%{sh:>7.2f}")


if __name__ == "__main__":
    main()
