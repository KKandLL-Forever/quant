"""
run_swing_exit_compare.py — 离场方式对比:唐奇安一次性 vs 分批止盈(对症"回吐")

入场与市场闸门固定(基底突破+放量+上证MA60健康),只改离场,全样本对比期望与回吐:
  A 基准:唐奇安20 一次性离场
  B 分批:涨到 +TP1(默认20%)先卖一半锁利,余下唐奇安20 跟踪
  C 分批+吊灯:+TP1 卖一半,余下用吊灯线(22日高-3ATR)更快保护
大盘转坏一律全平。额外报 MFE(最大浮盈)与"距顶回吐"=MFE−实际,看分批是否真缓解回吐痛点。

环境：.venv312。用法：python swing/run_swing_exit_compare.py --asof 2021-12-31 --n 800
依赖：DuckDB(daily/daily_basic/adj_factor/index_daily/stock_st/stock_meta)。
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.expanduser("~/AI/quart/first10"))

import duckdb
import numpy as np
import pandas as pd

from cache_tushare import DUCKDB_PATH

BASE_W, BASE_MAX, POS_MAX, VOL_K, DON_EXIT, TP1, COST = 40, 0.30, 0.6, 1.5, 20, 0.20, 0.0006


def _sim(c, h, en, regbad, don20, chand, mode):
    """逐笔撮合,返回 [(收益, 持有天, MFE)]。mode: base/partial/partial_chand。"""
    trades = []
    pos = False
    n = len(c)
    ep = ed = peak = realized = qty = 0
    took = False
    for t in range(1, n):
        if not pos and en[t]:
            pos = True; ep = c[t]; ed = t; peak = c[t]; realized = 0.0; qty = 1.0; took = False
        elif pos:
            peak = max(peak, h[t])
            if regbad[t]:
                realized += qty * (c[t] / ep - 1)
                trades.append((realized - 2 * COST, t - ed, peak / ep - 1)); pos = False; continue
            if mode != "base" and not took and h[t] >= ep * (1 + TP1):
                realized += 0.5 * TP1; qty = 0.5; took = True
            stop = (c[t] < don20[t]) if mode != "partial_chand" else (c[t] < chand[t])
            if stop:
                realized += qty * (c[t] / ep - 1)
                trades.append((realized - 2 * COST, t - ed, peak / ep - 1)); pos = False
    return trades


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", default="2021-12-31")
    ap.add_argument("--n", type=int, default=800)
    args = ap.parse_args()

    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    idx = con.execute("SELECT trade_date, close FROM index_daily WHERE ts_code='000001.SH' ORDER BY trade_date").fetch_df()
    sel_day = con.execute("SELECT MAX(trade_date) FROM daily_basic WHERE trade_date<=?", [args.asof]).fetchone()[0]
    liquid = [r[0] for r in con.execute("""
        SELECT ts_code FROM daily_basic WHERE trade_date=? AND ts_code NOT LIKE '%.BJ'
          AND ts_code NOT IN (SELECT ts_code FROM stock_st WHERE trade_date=?)
        ORDER BY circ_mv DESC LIMIT ?""", [sel_day, sel_day, args.n]).fetchall()]
    meta = con.execute("SELECT ts_code, list_date, delist_date FROM stock_meta").fetch_df()
    px = con.execute("""
        SELECT d.ts_code, d.trade_date, d.high*a.adj_factor h, d.low*a.adj_factor l,
               d.close*a.adj_factor c, d.vol v
        FROM daily d JOIN adj_factor a ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date
        WHERE d.trade_date>=? AND d.ts_code IN (SELECT UNNEST(?))""", ["2020-09-01", liquid]).fetch_df()
    con.close()

    idx["trade_date"] = pd.to_datetime(idx["trade_date"])
    ima = idx["close"].rolling(60).mean()
    idx["healthy"] = (idx["close"] > ima) & (ima > ima.shift(10))
    regime = dict(zip(idx["trade_date"], idx["healthy"].fillna(False)))
    px["trade_date"] = pd.to_datetime(px["trade_date"])
    meta = meta.set_index("ts_code")
    meta["list_date"] = pd.to_datetime(meta["list_date"], format="%Y%m%d", errors="coerce")
    meta["delist_date"] = pd.to_datetime(meta["delist_date"], format="%Y%m%d", errors="coerce")
    asof_ts = pd.Timestamp(sel_day)
    ok = set(meta.index[(meta["list_date"] <= asof_ts - pd.Timedelta(days=365)) &
                        (meta["delist_date"].isna() | (meta["delist_date"] > asof_ts + pd.Timedelta(days=365)))])

    out = {"A 唐奇安一次性": [], "B 分批+唐奇安": [], "C 分批+吊灯": []}
    n_stocks = 0
    for ts, g in px.groupby("ts_code"):
        if ts not in ok:
            continue
        g = g.sort_values("trade_date").reset_index(drop=True)
        c, h, l, v = g["c"], g["h"], g["l"], g["v"]
        hi_w = h.rolling(BASE_W).max().shift(1); lo_w = l.rolling(BASE_W).min().shift(1)
        rng = (hi_w - lo_w) / lo_w
        lo_y, hi_y = c.rolling(250).min(), c.rolling(250).max()
        pos1y = (c - lo_y) / (hi_y - lo_y)
        vma = v.rolling(20).mean().shift(1)
        reg = pd.Series(c.index.map(lambda i: regime.get(g["trade_date"].iloc[i], False)), index=c.index)
        sig = (rng < BASE_MAX) & (pos1y < POS_MAX) & (c > hi_w) & (v > VOL_K * vma)
        don20 = l.rolling(DON_EXIT).min().shift(1)
        tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1 / 22, adjust=False).mean()
        chand = h.rolling(22).max() - 3 * atr
        mask = (g["trade_date"] > asof_ts).values
        if mask.sum() < 120:
            continue
        n_stocks += 1
        en = (sig & reg).shift(1).fillna(False).values & mask
        regbad = (~reg).shift(1).fillna(False).values
        cc, hh, d20, ch = c.values, h.values, don20.values, chand.values
        out["A 唐奇安一次性"] += _sim(cc, hh, en, regbad, d20, ch, "base")
        out["B 分批+唐奇安"] += _sim(cc, hh, en, regbad, d20, ch, "partial")
        out["C 分批+吊灯"] += _sim(cc, hh, en, regbad, d20, ch, "partial_chand")

    print(f"选池日 {asof_ts.date()} | 流动性{args.n} | 实测 {n_stocks} 只 | 测试 {px[px['trade_date']>asof_ts]['trade_date'].min().date()}~{px['trade_date'].max().date()} | TP1=+{TP1*100:.0f}%先卖半\n")
    print(f"{'离场方式':<18}{'笔数':>6}{'胜率':>7}{'均收益':>8}{'盈亏比':>7}{'均最大浮盈':>10}{'均回吐':>8}{'均持有':>7}")
    for nm, t in out.items():
        a = np.array(t)
        rets, days, mfe = a[:, 0], a[:, 1], a[:, 2]
        win = (rets > 0).mean()
        pf = rets[rets > 0].sum() / -rets[rets < 0].sum() if (rets < 0).any() else float("inf")
        giveback = (mfe - rets).mean()
        print(f"{nm:<18}{len(t):>6}{win*100:>6.0f}%{rets.mean()*100:>+7.1f}%{pf:>7.2f}{mfe.mean()*100:>+9.1f}%{giveback*100:>+7.1f}%{days.mean():>6.0f}天")


if __name__ == "__main__":
    main()
