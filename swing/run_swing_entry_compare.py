"""
run_swing_entry_compare.py — 入场方式对比:突破追高 vs 回调买入(精准度验证)

离场与市场闸门固定(唐奇安20破位 或 上证转坏全平),只改入场,全样本对比期望与回吐:
  突破版:基底<30% + 1年位<60% + 突破40日新高 + 放量(买在冲高点)
  回调版:确认上升趋势(MA20>MA50 且 MA50上行 且 价>MA50)后,近5日回踩MA20、今日收回MA20上方
          且收阳(买在趋势中的回调低点,进场更近支撑)
两者都需上证MA60健康才入场。看回调版是否进场更精准:盈亏比更高、距顶回吐更小。

环境：.venv312。用法：python swing/run_swing_entry_compare.py --asof 2021-12-31 --n 800
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

BASE_W, BASE_MAX, POS_MAX, VOL_K, DON_EXIT, COST = 40, 0.30, 0.6, 1.5, 20, 0.0006


def _sim(c, h, en, ex):
    """逐笔:en/ex 为次日执行口径,返回 [(收益, 持有天, MFE)]。"""
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
    ap.add_argument("--n", type=int, default=800)
    args = ap.parse_args()

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

    out = {"突破追高": [], "回调买入": []}
    n_stocks = 0
    for ts, g in px.groupby("ts_code"):
        if ts not in ok:
            continue
        g = g.sort_values("trade_date").reset_index(drop=True)
        c, h, l, v = g["c"], g["h"], g["l"], g["v"]
        reg = pd.Series([regime.get(d, False) for d in g["trade_date"]], index=c.index)
        hi_w = h.rolling(BASE_W).max().shift(1); lo_w = l.rolling(BASE_W).min().shift(1)
        rng = (hi_w - lo_w) / lo_w
        pos1y = (c - c.rolling(250).min()) / (c.rolling(250).max() - c.rolling(250).min())
        vma = v.rolling(20).mean().shift(1)
        ma10, ma20, ma50 = c.rolling(10).mean(), c.rolling(20).mean(), c.rolling(50).mean()

        sig_brk = (rng < BASE_MAX) & (pos1y < POS_MAX) & (c > hi_w) & (v > VOL_K * vma)
        uptrend = (ma20 > ma50) & (ma50 > ma50.shift(10)) & (c > ma50)
        touched = (l.rolling(5).min() <= ma20 * 1.01)
        sig_pb = uptrend & touched & (c > ma20) & (c > c.shift(1))

        ex = ((c < l.rolling(DON_EXIT).min().shift(1)) | (~reg)).shift(1).fillna(False).values
        mask = (g["trade_date"] > asof_ts).values
        if mask.sum() < 120:
            continue
        n_stocks += 1
        cc, hh = c.values, h.values
        en_brk = (sig_brk & reg).shift(1).fillna(False).values & mask
        en_pb = (sig_pb & reg).shift(1).fillna(False).values & mask
        out["突破追高"] += _sim(cc, hh, en_brk, ex)
        out["回调买入"] += _sim(cc, hh, en_pb, ex)

    print(f"选池日 {asof_ts.date()} | 流动性{args.n} | 实测 {n_stocks} 只 | 测试 {px[px['trade_date']>asof_ts]['trade_date'].min().date()}~{px['trade_date'].max().date()}\n")
    print(f"{'入场方式':<12}{'笔数':>7}{'胜率':>7}{'均收益':>8}{'盈亏比':>7}{'均最大浮盈':>10}{'均回吐':>8}{'均持有':>7}")
    for nm, t in out.items():
        a = np.array(t)
        rets, days, mfe = a[:, 0], a[:, 1], a[:, 2]
        win = (rets > 0).mean()
        pf = rets[rets > 0].sum() / -rets[rets < 0].sum() if (rets < 0).any() else float("inf")
        print(f"{nm:<12}{len(t):>7}{win*100:>6.0f}%{rets.mean()*100:>+7.1f}%{pf:>7.2f}{mfe.mean()*100:>+9.1f}%{(mfe-rets).mean()*100:>+7.1f}%{days.mean():>6.0f}天")


if __name__ == "__main__":
    main()
