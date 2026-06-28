"""
run_swing_count.py — N/W突破 + 固定10日离场,不同持仓数(3/5/8/10/12)组合对比

入场:N字/W型突破 且 上证MA60健康(次日执行);离场:固定持有10个交易日。
最多持 HOLD 只等权,多信号同日抢仓先到先得(选股≈随机,故不排序)。
对比 HOLD∈{3,5,8,10,12} 的组合总收益/年化/最大回撤/夏普/交易数,看分散对稳定性的影响。

环境：.venv312。用法：python swing/run_swing_count.py --asof 2021-12-31
依赖：DuckDB(daily/daily_basic/adj_factor/index_daily/stock_st/stock_meta);复用 run_patterns。
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.expanduser("~/AI/quart"))

import duckdb
import numpy as np
import pandas as pd

from cache_tushare import DUCKDB_PATH
from run_patterns import _detect

THR, HOLD_DAYS, COST = 0.09, 10, 0.0006
COUNTS = [3, 5, 8, 10, 12]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", default="2021-12-31")
    ap.add_argument("--n", type=int, default=800)
    args = ap.parse_args()

    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    idx = con.execute("SELECT trade_date,close FROM index_daily WHERE ts_code='000001.SH' ORDER BY trade_date").fetch_df()
    sel = con.execute("SELECT MAX(trade_date) FROM daily_basic WHERE trade_date<=?", [args.asof]).fetchone()[0]
    liquid = [r[0] for r in con.execute("""SELECT ts_code FROM daily_basic WHERE trade_date=? AND ts_code NOT LIKE '%.BJ'
        AND ts_code NOT IN (SELECT ts_code FROM stock_st WHERE trade_date=?) ORDER BY circ_mv DESC LIMIT ?""",
        [sel, sel, args.n]).fetchall()]
    meta = con.execute("SELECT ts_code,list_date,delist_date FROM stock_meta").fetch_df()
    px = con.execute("""SELECT d.ts_code,d.trade_date,d.close*a.adj_factor c
        FROM daily d JOIN adj_factor a ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date
        WHERE d.trade_date>=? AND d.ts_code IN (SELECT UNNEST(?))""", ["2020-09-01", liquid]).fetch_df()
    pxh = con.execute("""SELECT d.ts_code,d.trade_date,d.high*a.adj_factor h,d.low*a.adj_factor l
        FROM daily d JOIN adj_factor a ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date
        WHERE d.trade_date>=? AND d.ts_code IN (SELECT UNNEST(?))""", ["2020-09-01", liquid]).fetch_df()
    con.close()

    idx["trade_date"] = pd.to_datetime(idx["trade_date"]); ima = idx["close"].rolling(60).mean()
    regime = dict(zip(idx["trade_date"], ((idx["close"] > ima) & (ima > ima.shift(10))).fillna(False)))
    for d in (px, pxh):
        d["trade_date"] = pd.to_datetime(d["trade_date"])
    meta = meta.set_index("ts_code"); meta["list_date"] = pd.to_datetime(meta["list_date"], format="%Y%m%d", errors="coerce")
    meta["delist_date"] = pd.to_datetime(meta["delist_date"], format="%Y%m%d", errors="coerce")
    asof_ts = pd.Timestamp(sel)
    ok = set(meta.index[(meta["list_date"] <= asof_ts - pd.Timedelta(days=365)) &
                        (meta["delist_date"].isna() | (meta["delist_date"] > asof_ts + pd.Timedelta(days=365)))])

    master = pd.DatetimeIndex(sorted(px["trade_date"].unique()))
    di = {d: i for i, d in enumerate(master)}; T = len(master)
    hl = {ts: g.sort_values("trade_date") for ts, g in pxh.groupby("ts_code")}
    ENm, RETm, cols = [], [], []
    for ts, g in px.groupby("ts_code"):
        if ts not in ok or ts not in hl:
            continue
        g = g.sort_values("trade_date").reset_index(drop=True)
        cc = g["c"].to_numpy(); gd = g["trade_date"].to_numpy()
        en_m = np.zeros(T, bool); ret_m = np.zeros(T)
        rl = g["c"].pct_change().fillna(0).values
        en_local = np.zeros(len(cc), bool)
        for typ, bo, _ in _detect(cc, THR, 30):
            if regime.get(pd.Timestamp(gd[bo]), False):
                en_local[bo] = True
        en_exec = np.concatenate([[False], en_local[:-1]])
        for k in range(len(cc)):
            mi = di[pd.Timestamp(gd[k])]
            en_m[mi] = en_exec[k]; ret_m[mi] = rl[k]
        ENm.append(en_m); RETm.append(ret_m); cols.append(ts)
    EN = np.array(ENm).T; RET = np.array(RETm).T
    tidx = [i for i in range(T) if master[i] > asof_ts]

    bn = 1.0; bench = []
    for i in tidx:
        bn *= 1 + np.nanmean(RET[i]); bench.append(bn)
    bnav = np.array(bench); bpk = np.maximum.accumulate(bnav); bmdd = ((bnav - bpk) / bpk).min()

    print(f"选池日 {asof_ts.date()} | 候选{len(cols)}只 | 测试 {master[tidx[0]].date()}~{master[-1].date()} | N/W突破+固定持有{HOLD_DAYS}日\n")
    print(f"{'持仓数':<8}{'总收益':>9}{'年化':>8}{'最大回撤':>9}{'夏普':>7}{'交易数':>7}")
    print(f"{'等权全市场':<8}{(bnav[-1]-1)*100:>8.0f}%{'—':>8}{bmdd*100:>8.0f}%{'—':>7}{'—':>7}")
    for H in COUNTS:
        nav = 1.0; navs = []; held = {}; ntr = 0
        for i in tidx:
            if held:
                nav *= 1 + sum(RET[i, k] for k in held) / H
            for k in [k for k, ei in held.items() if i - ei >= HOLD_DAYS]:
                del held[k]; nav *= 1 - 2 * COST / H
            free = H - len(held)
            if free > 0:
                for k in [k for k in np.where(EN[i])[0] if k not in held][:free]:
                    held[k] = i; nav *= 1 - 2 * COST / H; ntr += 1
            navs.append(nav)
        navs = np.array(navs); peak = np.maximum.accumulate(navs); mdd = ((navs - peak) / peak).min()
        rr = np.diff(navs) / navs[:-1]; sh = rr.mean() / rr.std() * np.sqrt(252) if rr.std() > 0 else 0
        ann = navs[-1] ** (252 / len(navs)) - 1
        print(f"{'持'+str(H)+'只':<8}{(navs[-1]-1)*100:>8.0f}%{ann*100:>7.0f}%{mdd*100:>8.0f}%{sh:>7.2f}{ntr:>7}")


if __name__ == "__main__":
    main()
