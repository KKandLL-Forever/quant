"""
run_swing_pattern_strategy.py — N字/W型突破策略回测(每笔期望 + 最多持3只组合)

已验证 N字/W型突破命中率1.6×随机、大盘健康时~2×。本脚本把它做成可交易策略:
  入场:N字 或 W型 突破当日 且 上证MA60健康(次日执行)
  离场:跌破唐奇安20日低 或 大盘转坏(次日执行)
输出:① 全样本每笔交易期望;② 最多持 HOLD 只的组合净值/回撤/夏普(多信号同日先到先得)。

环境：.venv312。用法：python swing/run_swing_pattern_strategy.py --asof 2021-12-31 --hold 3
依赖：DuckDB(daily/daily_basic/adj_factor/index_daily/stock_st/stock_meta);复用 run_patterns 检测。
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
from run_patterns import _detect

THR, DON_EXIT, COST = 0.09, 20, 0.0006


def _sim(c, h, en, ex):
    trades = []
    pos = False
    ep = ed = peak = 0
    for t in range(1, len(c)):
        if not pos and en[t]:
            pos = True; ep = c[t]; ed = t; peak = c[t]
        elif pos:
            peak = max(peak, h[t])
            if ex[t]:
                trades.append((c[t] / ep - 1 - 2 * COST, t - ed, peak / ep - 1)); pos = False
    return trades


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", default="2021-12-31")
    ap.add_argument("--hold", type=int, default=3)
    ap.add_argument("--n", type=int, default=800)
    args = ap.parse_args()
    H = args.hold

    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    idx = con.execute("SELECT trade_date,close FROM index_daily WHERE ts_code='000001.SH' ORDER BY trade_date").fetch_df()
    sel = con.execute("SELECT MAX(trade_date) FROM daily_basic WHERE trade_date<=?", [args.asof]).fetchone()[0]
    liquid = [r[0] for r in con.execute("""SELECT ts_code FROM daily_basic WHERE trade_date=? AND ts_code NOT LIKE '%.BJ'
        AND ts_code NOT IN (SELECT ts_code FROM stock_st WHERE trade_date=?) ORDER BY circ_mv DESC LIMIT ?""",
        [sel, sel, args.n]).fetchall()]
    meta = con.execute("SELECT ts_code,list_date,delist_date FROM stock_meta").fetch_df()
    px = con.execute("""SELECT d.ts_code,d.trade_date,d.high*a.adj_factor h,d.low*a.adj_factor l,d.close*a.adj_factor c
        FROM daily d JOIN adj_factor a ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date
        WHERE d.trade_date>=? AND d.ts_code IN (SELECT UNNEST(?))""", ["2020-09-01", liquid]).fetch_df()
    con.close()

    idx["trade_date"] = pd.to_datetime(idx["trade_date"]); ima = idx["close"].rolling(60).mean()
    regime = dict(zip(idx["trade_date"], ((idx["close"] > ima) & (ima > ima.shift(10))).fillna(False)))
    px["trade_date"] = pd.to_datetime(px["trade_date"])
    meta = meta.set_index("ts_code"); meta["list_date"] = pd.to_datetime(meta["list_date"], format="%Y%m%d", errors="coerce")
    meta["delist_date"] = pd.to_datetime(meta["delist_date"], format="%Y%m%d", errors="coerce")
    asof_ts = pd.Timestamp(sel)
    ok = set(meta.index[(meta["list_date"] <= asof_ts - pd.Timedelta(days=365)) &
                        (meta["delist_date"].isna() | (meta["delist_date"] > asof_ts + pd.Timedelta(days=365)))])

    master = pd.DatetimeIndex(sorted(px["trade_date"].unique()))
    di = {d: i for i, d in enumerate(master)}
    T = len(master)
    ENm, EXm, RETm, cols = [], [], [], []
    trade_rows = []
    for ts, g in px.groupby("ts_code"):
        if ts not in ok:
            continue
        g = g.sort_values("trade_date").reset_index(drop=True)
        c, h, l = g["c"], g["h"], g["l"]
        cc = c.to_numpy(); hh = h.to_numpy()
        gd = g["trade_date"].to_numpy()
        en_local = np.zeros(len(cc), bool)
        for typ, bo, _ in _detect(cc, THR, 30):
            if regime.get(pd.Timestamp(gd[bo]), False):
                en_local[bo] = True
        reg = np.array([regime.get(pd.Timestamp(d), False) for d in gd])
        ex_local = ((c < l.rolling(DON_EXIT).min().shift(1)) | (~pd.Series(reg, index=c.index))).shift(1).fillna(False).values
        en_exec = np.concatenate([[False], en_local[:-1]])
        trade_rows += _sim(cc, hh, en_exec, ex_local)
        # map to master grid
        en_m = np.zeros(T, bool); ex_m = np.zeros(T, bool); ret_m = np.zeros(T)
        ret_local = c.pct_change().fillna(0).values
        for k in range(len(cc)):
            mi = di[pd.Timestamp(gd[k])]
            en_m[mi] = en_exec[k]; ex_m[mi] = ex_local[k]; ret_m[mi] = ret_local[k]
        ENm.append(en_m); EXm.append(ex_m); RETm.append(ret_m); cols.append(ts)

    EN = np.array(ENm).T; EX = np.array(EXm).T; RET = np.array(RETm).T
    tidx = [i for i in range(T) if master[i] > asof_ts]

    # 每笔期望
    a = np.array(trade_rows)
    rets, days, mfe = a[:, 0], a[:, 1], a[:, 2]
    pf = rets[rets > 0].sum() / -rets[rets < 0].sum()
    print(f"选池日 {asof_ts.date()} | 候选 {len(cols)} 只 | 测试 {master[tidx[0]].date()}~{master[-1].date()}")
    print(f"\n【每笔交易期望(全部信号)】")
    print(f"  笔数 {len(rets)} | 胜率 {(rets>0).mean()*100:.0f}% | 均收益 {rets.mean()*100:+.1f}% | 盈亏比 {pf:.2f} | 均回吐 {(mfe-rets).mean()*100:.1f}% | 均持有 {days.mean():.0f}天")

    # 组合(最多持H只,同日多信号先到先得=按列序)
    nav = 1.0; navs = []; held = []
    for i in tidx:
        if held:
            nav *= 1 + sum(RET[i, k] for k in held) / H
        for k in list(held):
            if EX[i, k]:
                held.remove(k); nav *= 1 - 2 * COST / H
        free = H - len(held)
        if free > 0:
            cand = [k for k in np.where(EN[i])[0] if k not in held]
            for k in cand[:free]:
                held.append(k); nav *= 1 - 2 * COST / H
        navs.append(nav)
    navs = np.array(navs)
    peak = np.maximum.accumulate(navs); mdd = ((navs - peak) / peak).min()
    rr = np.diff(navs) / navs[:-1]
    sh = rr.mean() / rr.std() * np.sqrt(252) if rr.std() > 0 else 0
    bench = []
    bn = 1.0
    for i in tidx:
        bn *= 1 + np.nanmean(RET[i])
        bench.append(bn)
    bnav = np.array(bench); bpk = np.maximum.accumulate(bnav); bmdd = ((bnav - bpk) / bpk).min()
    print(f"\n【最多持{H}只组合】")
    print(f"  总收益 {(navs[-1]-1)*100:+.0f}% | 年化 {(navs[-1]**(252/len(navs))-1)*100:.0f}% | 最大回撤 {mdd*100:.0f}% | 夏普 {sh:.2f}")
    print(f"  等权全市场基准: 总收益 {(bnav[-1]-1)*100:+.0f}% | 回撤 {bmdd*100:.0f}%")


if __name__ == "__main__":
    main()
