"""
run_swing_breakout.py — 平台突破(基底起爆)setup 的每笔交易期望验证

思路:低/中位长期震荡(波动收敛)的票,在突破箱顶(放量)的起爆点入场,吃中段,
趋势走坏离场。绕开"追高必套"——买在主升起点而非末端。扫全流动性股,统计每笔交易期望
(笔数/胜率/盈亏比/平均持有/期望收益),并对比两种离场确认方式。

入场(信号日 t,次日收盘执行):
  ① 基底:过去40日 (最高-最低)/最低 < 30%(横盘、波动收敛)
  ② 低/中位:现价在过去250日区间下方 60% 以内(排高位)
  ③ 突破:收盘创 40 日新高
  ④ 放量:量 > 20日均量 ×1.5
离场(对比三种):
  A 跌破唐奇安20日低(单条件)   B A且跌破MA20(双确认防假摔)   固定持有20日(参照)

⚠️ 这是 setup 整体期望验证;实盘只持1-3只,单笔波动远大于此均值,仅作 edge 判断。

环境：.venv312。用法：python swing/run_swing_breakout.py --asof 2021-12-31 --n 800
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

BASE_W, BASE_MAX, POS_MAX, VOL_K, DON_EXIT, COST = 40, 0.30, 0.6, 1.5, 20, 0.0006


def _sim(c, enter_exec, exit_exec, fixed=None):
    """逐日撮合:enter/exit 已是次日执行口径,返回 [(收益, 持有天数)]。"""
    trades = []
    pos = False
    entry = ed = 0
    n = len(c)
    for t in range(1, n):
        if not pos and enter_exec[t]:
            pos = True; entry = c[t]; ed = t
        elif pos and (exit_exec[t] or (fixed is not None and t - ed >= fixed)):
            trades.append((c[t] / entry - 1 - 2 * COST, t - ed)); pos = False
    return trades


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", default="2021-12-31")
    ap.add_argument("--n", type=int, default=800)
    args = ap.parse_args()

    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    idx = con.execute("SELECT trade_date, close FROM index_daily WHERE ts_code='000001.SH' ORDER BY trade_date").fetch_df()
    idx["trade_date"] = pd.to_datetime(idx["trade_date"])
    ima = idx["close"].rolling(60).mean()
    idx["healthy"] = (idx["close"] > ima) & (ima > ima.shift(10))
    regime = dict(zip(idx["trade_date"], idx["healthy"].fillna(False)))
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

    px["trade_date"] = pd.to_datetime(px["trade_date"])
    meta = meta.set_index("ts_code")
    meta["list_date"] = pd.to_datetime(meta["list_date"], format="%Y%m%d", errors="coerce")
    meta["delist_date"] = pd.to_datetime(meta["delist_date"], format="%Y%m%d", errors="coerce")
    asof_ts = pd.Timestamp(sel_day)
    ok = set(meta.index[(meta["list_date"] <= asof_ts - pd.Timedelta(days=365)) &
                        (meta["delist_date"].isna() | (meta["delist_date"] > asof_ts + pd.Timedelta(days=365)))])

    variants = {"唐奇安(无闸门)": [], "+市场闸门(仅入场)": [], "+市场闸门(入场+离场)": []}
    n_stocks = 0
    for ts, g in px.groupby("ts_code"):
        if ts not in ok:
            continue
        g = g.sort_values("trade_date").reset_index(drop=True)
        c, h, l, v = g["c"], g["h"], g["l"], g["v"]
        hi_w = h.rolling(BASE_W).max().shift(1)
        lo_w = l.rolling(BASE_W).min().shift(1)
        rng = (hi_w - lo_w) / lo_w
        lo_y = c.rolling(250).min()
        hi_y = c.rolling(250).max()
        pos1y = (c - lo_y) / (hi_y - lo_y)
        vma = v.rolling(20).mean().shift(1)
        sig = (rng < BASE_MAX) & (pos1y < POS_MAX) & (c > hi_w) & (v > VOL_K * vma)
        donlow = l.rolling(DON_EXIT).min().shift(1)
        ma20 = c.rolling(20).mean()
        exA = c < donlow
        exB = (c < donlow) & (c < ma20)

        mask = g["trade_date"] > asof_ts
        if mask.sum() < 120:
            continue
        n_stocks += 1
        cc = c.values
        reg = g["trade_date"].map(regime).fillna(False).values
        exA_s = exA.shift(1).fillna(False).values
        regbad_s = (~g["trade_date"].map(regime).fillna(False)).shift(1).fillna(False).values
        en_base = sig.shift(1).fillna(False).values & mask.values
        en_reg = en_base & np.concatenate([[False], reg[:-1]])
        variants["唐奇安(无闸门)"] += _sim(cc, en_base, exA_s, None)
        variants["+市场闸门(仅入场)"] += _sim(cc, en_reg, exA_s, None)
        variants["+市场闸门(入场+离场)"] += _sim(cc, en_reg, exA_s | regbad_s, None)

    print(f"选池日 {asof_ts.date()} | 流动性{args.n} | 实测 {n_stocks} 只 | 测试 {px[px['trade_date']>asof_ts]['trade_date'].min().date()}~{px['trade_date'].max().date()}")
    print(f"基底<{BASE_MAX*100:.0f}% / 1年位<{POS_MAX*100:.0f}% / 突破40日新高 / 量>{VOL_K}×均量\n")
    print(f"{'离场方式':<20}{'笔数':>6}{'胜率':>7}{'均收益':>8}{'中位':>7}{'盈亏比':>7}{'均持有':>7}")
    for nm, tr in variants.items():
        if not tr:
            print(f"{nm:<20} 无交易"); continue
        a = np.array(tr)
        rets, days = a[:, 0], a[:, 1]
        win = (rets > 0).mean()
        wins = rets[rets > 0].sum()
        losses = -rets[rets < 0].sum()
        pf = wins / losses if losses > 0 else float("inf")
        print(f"{nm:<20}{len(tr):>6}{win*100:>6.0f}%{rets.mean()*100:>+7.1f}%{np.median(rets)*100:>+6.1f}%{pf:>7.2f}{days.mean():>6.0f}天")


if __name__ == "__main__":
    main()
