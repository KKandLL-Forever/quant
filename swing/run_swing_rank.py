"""
run_swing_rank.py — 多信号抢仓时"挑哪K只"的排序规则对比(对症只持1-3只)

完整策略固定(基底突破+放量+上证MA60闸门+唐奇安20离场,大盘转坏全平),最多持 HOLD 只。
牛市里几十个信号同抢K个仓 → "挑哪几只"才是胜负手。对比排序规则在完整组合回测上的净值:
  vol 放量强度 / base 基底最紧 / pos 位置最低 / brk 突破力度 / dev 距MA60乖离最小 / rand 随机
输出各规则总收益/年化/回撤/夏普/交易数。

⚠️ 集中持仓路径依赖、样本有限,结果指示性为主。
环境：.venv312。用法：python swing/run_swing_rank.py --asof 2021-12-31 --hold 3
依赖：DuckDB(daily/daily_basic/adj_factor/index_daily/stock_st/stock_meta)。
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.expanduser("~/AI/quart"))

import duckdb
import numpy as np
import pandas as pd

from cache_tushare import DUCKDB_PATH

BASE_W, BASE_MAX, POS_MAX, VOL_K, DON_EXIT, COST = 40, 0.30, 0.6, 1.5, 20, 0.0006
RULES = ["vol", "base", "pos", "brk", "dev", "rand"]
RULE_NM = {"vol": "放量强度", "base": "基底最紧", "pos": "位置最低", "brk": "突破力度",
           "dev": "乖离最小", "rand": "随机(基准)"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", default="2021-12-31")
    ap.add_argument("--hold", type=int, default=3)
    ap.add_argument("--n", type=int, default=1000)
    args = ap.parse_args()
    H = args.hold

    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    idx = con.execute("SELECT trade_date,close FROM index_daily WHERE ts_code='000001.SH' ORDER BY trade_date").fetch_df()
    sel = con.execute("SELECT MAX(trade_date) FROM daily_basic WHERE trade_date<=?", [args.asof]).fetchone()[0]
    liquid = [r[0] for r in con.execute("""SELECT ts_code FROM daily_basic WHERE trade_date=? AND ts_code NOT LIKE '%.BJ'
        AND ts_code NOT IN (SELECT ts_code FROM stock_st WHERE trade_date=?) ORDER BY circ_mv DESC LIMIT ?""",
        [sel, sel, args.n]).fetchall()]
    meta = con.execute("SELECT ts_code,list_date,delist_date FROM stock_meta").fetch_df()
    px = con.execute("""SELECT d.ts_code,d.trade_date,d.high*a.adj_factor h,d.low*a.adj_factor l,
        d.close*a.adj_factor c,d.vol v FROM daily d JOIN adj_factor a ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date
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
    cols, RET, EN, EX, RK = [], [], [], [], {r: [] for r in RULES if r != "rand"}
    for ts, g in px.groupby("ts_code"):
        if ts not in ok:
            continue
        g = g.sort_values("trade_date").set_index("trade_date").reindex(master)
        c, h, l, v = g["c"], g["h"], g["l"], g["v"]
        hi_w = h.rolling(BASE_W).max().shift(1); lo_w = l.rolling(BASE_W).min().shift(1)
        rng = (hi_w - lo_w) / lo_w
        pos1y = (c - c.rolling(250).min()) / (c.rolling(250).max() - c.rolling(250).min())
        vma = v.rolling(20).mean().shift(1)
        ma60 = c.rolling(60).mean()
        sig = (rng < BASE_MAX) & (pos1y < POS_MAX) & (c > hi_w) & (v > VOL_K * vma)
        reg = pd.Series([regime.get(d, False) for d in master], index=master)
        en = (sig & reg).shift(1).fillna(False)
        ex = ((c < l.rolling(DON_EXIT).min().shift(1)) | (~reg)).shift(1).fillna(False)
        cols.append(ts); RET.append(c.pct_change().values); EN.append(en.values); EX.append(ex.values)
        RK["vol"].append((v / vma).shift(1).values)
        RK["base"].append((-rng).shift(1).values)
        RK["pos"].append((-pos1y).shift(1).values)
        RK["brk"].append((c / hi_w - 1).shift(1).values)
        RK["dev"].append((-(c / ma60 - 1).abs()).shift(1).values)

    RET = np.nan_to_num(np.array(RET).T); EN = np.array(EN).T; EX = np.array(EX).T
    RKm = {r: np.nan_to_num(np.array(RK[r]).T, nan=-1e9) for r in RK}
    tidx = [i for i, d in enumerate(master) if d > asof_ts]
    regw = np.array([regime.get(master[i], False) for i in range(len(master))])
    rng_state = np.random.RandomState(0)

    def run(rule):
        nav = 1.0; navs = []; held = []; ntr = 0; wins = []; entryp = {}
        for i in tidx:
            if held:
                nav *= 1 + sum(RET[i, k] for k in held) / H
            for k in list(held):
                if EX[i, k]:
                    wins.append(RET[i, k])  # placeholder
                    held.remove(k); nav *= 1 - 2 * COST / H; ntr += 1
            free = H - len(held)
            if free > 0 and regw[i]:
                cand = [k for k in np.where(EN[i])[0] if k not in held]
                if cand:
                    if rule == "rand":
                        rng_state.shuffle(cand); pick = cand[:free]
                    else:
                        pick = sorted(cand, key=lambda k: RKm[rule][i, k], reverse=True)[:free]
                    for k in pick:
                        held.append(k); nav *= 1 - 2 * COST / H
            navs.append(nav)
        navs = np.array(navs)
        peak = np.maximum.accumulate(navs); mdd = ((navs - peak) / peak).min()
        rets = np.diff(navs) / navs[:-1]
        sh = rets.mean() / rets.std() * np.sqrt(252) if rets.std() > 0 else 0
        ann = navs[-1] ** (252 / len(navs)) - 1
        return navs[-1] - 1, ann, mdd, sh, ntr

    print(f"选池日 {asof_ts.date()} | 候选 {len(cols)} 只 | 测试 {master[tidx[0]].date()}~{master[-1].date()} | 最多持{H}只\n")
    print(f"{'排序规则':<16}{'总收益':>9}{'年化':>8}{'最大回撤':>9}{'夏普':>7}{'交易数':>7}")
    for r in RULES:
        tot, ann, mdd, sh, ntr = run(r)
        print(f"{RULE_NM[r]:<16}{tot*100:>8.0f}%{ann*100:>7.0f}%{mdd*100:>8.0f}%{sh:>7.2f}{ntr:>7}")


if __name__ == "__main__":
    main()
