"""
run_swing_holding_balance.py — 持有周期 vs 盈利 的平衡(单仓位年化口径)

短持有单笔小但翻台快、长持有单笔大但占用久。比的不是单笔收益,而是单仓位年化(收益÷时间)。
扫 N/W 突破(大盘健康)信号,单仓位顺序交易(出场后接下一个信号),对比多种离场:
  固定10/20/40日、唐奇安20破位、止盈20%/止损8%、趋势持有(唐奇安20无时限)
输出每种:平均持有天数、胜率、单笔均收益、单仓位年化、最大回撤。找"短持有又不牺牲年化"的点。

环境：.venv312。用法：python swing/run_swing_holding_balance.py --thr 0.09 --n 800
依赖：DuckDB(daily/daily_basic/adj_factor/index_daily);复用 run_patterns。
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

THR, COST = 0.09, 0.0006
RULES = ["固定5日", "固定10日", "固定20日", "固定40日", "唐奇安20", "止盈20/止损8"]


def _exit(c, h, l, don20, ei, rule):
    """从 ei 进场,按 rule 找离场,返回 (持有交易日数, ret)。"""
    n = len(c)
    ep = c[ei]
    for t in range(ei + 1, n):
        if rule == "固定5日" and t - ei >= 5:
            return t - ei, c[t] / ep - 1
        if rule == "固定10日" and t - ei >= 10:
            return t - ei, c[t] / ep - 1
        if rule == "固定20日" and t - ei >= 20:
            return t - ei, c[t] / ep - 1
        if rule == "固定40日" and t - ei >= 40:
            return t - ei, c[t] / ep - 1
        if rule == "唐奇安20" and c[t] < don20[t]:
            return t - ei, c[t] / ep - 1
        if rule == "止盈20/止损8":
            if h[t] >= ep * 1.20:
                return t - ei, 0.20
            if l[t] <= ep * 0.92:
                return t - ei, -0.08
    return n - 1 - ei, c[n - 1] / ep - 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--thr", type=float, default=0.09)
    ap.add_argument("--n", type=int, default=800)
    args = ap.parse_args()

    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    idx = con.execute("SELECT trade_date,close FROM index_daily WHERE ts_code='000001.SH' ORDER BY trade_date").fetch_df()
    sel = con.execute("SELECT MAX(trade_date) FROM daily_basic").fetchone()[0]
    liquid = [r[0] for r in con.execute("""SELECT ts_code FROM daily_basic WHERE trade_date=? AND ts_code NOT LIKE '%.BJ'
        ORDER BY circ_mv DESC LIMIT ?""", [sel, args.n]).fetchall()]
    px = con.execute("""SELECT d.ts_code,d.trade_date,d.high*a.adj_factor h,d.low*a.adj_factor l,d.close*a.adj_factor c
        FROM daily d JOIN adj_factor a ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date
        WHERE d.trade_date>=? AND d.ts_code IN (SELECT UNNEST(?))""", ["2021-06-01", liquid]).fetch_df()
    con.close()
    px["trade_date"] = pd.to_datetime(px["trade_date"])
    idx["trade_date"] = pd.to_datetime(idx["trade_date"]); ima = idx["close"].rolling(60).mean()
    regime = dict(zip(idx["trade_date"], ((idx["close"] > ima) & (ima > ima.shift(10))).fillna(False)))

    sigs = {r: [] for r in RULES}
    for ts, g in px.groupby("ts_code"):
        g = g.sort_values("trade_date").reset_index(drop=True)
        c, h, l = g["c"].to_numpy(), g["h"].to_numpy(), g["l"].to_numpy()
        gd = g["trade_date"].to_numpy()
        if len(c) < 120:
            continue
        don20 = pd.Series(l).rolling(20).min().shift(1).to_numpy()
        for typ, bo, _ in _detect(c, THR, 30):
            if bo + 2 >= len(c) or not regime.get(pd.Timestamp(gd[bo]), False):
                continue
            ei = bo + 1
            for r in RULES:
                hd, ret = _exit(c, h, l, don20, ei, r)
                sigs[r].append((hd, ret - 2 * COST))

    print(f"流动性{args.n}只 | 大盘健康日 N/W 信号 | 年化=(1+单笔)^(252/持有交易日)-1\n")
    print(f"{'离场方式':<14}{'持有(交易日)':>12}{'胜率':>7}{'单笔均收益':>11}{'单仓位年化':>11}{'笔数':>7}")
    for r in RULES:
        a = np.array(sigs[r])
        hd = a[:, 0]; ret = a[:, 1]
        avg_hold = hd.mean()
        ann = (1 + ret.mean()) ** (252 / max(avg_hold, 1)) - 1
        print(f"{r:<14}{avg_hold:>10.0f}天{(ret>0).mean()*100:>6.0f}%{ret.mean()*100:>+10.1f}%{ann*100:>+10.0f}%{len(ret):>7}")


if __name__ == "__main__":
    main()
