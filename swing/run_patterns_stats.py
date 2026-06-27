"""
run_patterns_stats.py — N字/W型突破的"埋伏有效性"统计验证

对全市场 N字型/W型突破事件,统计突破后 20/40/60 日收益分布,以及"突破后60日内最高涨幅≥50%
(走出主升浪)"的命中率,对比随机买入的基础率,并按突破时上证是否健康(MA60上行)分组。
回答:这两个形态当"主升浪前埋伏信号"是否显著优于瞎买。

环境：.venv312。用法：python swing/run_patterns_stats.py --thr 0.09 --n 600
依赖：DuckDB(daily/daily_basic/adj_factor/index_daily);复用 run_patterns 检测。
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

MW_GAIN, MW_DAYS = 0.50, 60


def _fwd(c, t):
    """返回 (r20, r40, r60, max60),数据不足返回 None。"""
    if t + MW_DAYS >= len(c):
        return None
    r = lambda k: c[t + k] / c[t] - 1
    mx = c[t + 1:t + MW_DAYS + 1].max() / c[t] - 1
    return r(20), r(40), r(60), mx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--thr", type=float, default=0.09)
    ap.add_argument("--n", type=int, default=600)
    args = ap.parse_args()

    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    sel = con.execute("SELECT MAX(trade_date) FROM daily_basic").fetchone()[0]
    liquid = [r[0] for r in con.execute("""SELECT ts_code FROM daily_basic WHERE trade_date=? AND ts_code NOT LIKE '%.BJ'
        ORDER BY circ_mv DESC LIMIT ?""", [sel, args.n]).fetchall()]
    idx = con.execute("SELECT trade_date,close FROM index_daily WHERE ts_code='000001.SH' ORDER BY trade_date").fetch_df()
    px = con.execute("""SELECT d.ts_code,d.trade_date,d.close*a.adj_factor c
        FROM daily d JOIN adj_factor a ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date
        WHERE d.trade_date>=? AND d.ts_code IN (SELECT UNNEST(?))""", ["2022-06-01", liquid]).fetch_df()
    con.close()
    px["trade_date"] = pd.to_datetime(px["trade_date"])
    idx["trade_date"] = pd.to_datetime(idx["trade_date"]); ima = idx["close"].rolling(60).mean()
    regime = dict(zip(idx["trade_date"], ((idx["close"] > ima) & (ima > ima.shift(10))).fillna(False)))

    groups = {"N字型": [], "W型": [], "随机基础率": []}
    greg = {"N字型": [], "W型": []}
    rs = np.random.RandomState(0)
    for ts, g in px.groupby("ts_code"):
        g = g.sort_values("trade_date").reset_index(drop=True)
        c = g["c"].to_numpy(); dts = g["trade_date"].to_numpy()
        if len(c) < 120:
            continue
        for typ, bo, _ in _detect(c, args.thr, 30):
            f = _fwd(c, bo)
            if f:
                groups[typ].append(f)
                greg[typ].append((regime.get(pd.Timestamp(dts[bo]), False), f))
        for t in rs.choice(range(30, len(c) - MW_DAYS), min(15, len(c) - MW_DAYS - 30), replace=False):
            f = _fwd(c, t)
            if f:
                groups["随机基础率"].append(f)

    def summ(rows):
        a = np.array(rows)
        return a[:, 0].mean(), a[:, 1].mean(), a[:, 2].mean(), (a[:, 3] >= MW_GAIN).mean(), len(a)

    print(f"流动性{args.n}只 | 2022-06~今 | 主升浪命中=突破后{MW_DAYS}日内最高涨幅≥{MW_GAIN*100:.0f}%\n")
    print(f"{'信号':<12}{'+20日':>8}{'+40日':>8}{'+60日':>8}{'命中率':>8}{'样本':>8}")
    base_hit = None
    for nm in ["随机基础率", "N字型", "W型"]:
        r20, r40, r60, hit, n = summ(groups[nm])
        if nm == "随机基础率":
            base_hit = hit
        lift = f"(×{hit/base_hit:.2f})" if base_hit and nm != "随机基础率" else ""
        print(f"{nm:<12}{r20*100:>+7.1f}%{r40*100:>+7.1f}%{r60*100:>+7.1f}%{hit*100:>6.0f}%{lift:<8}{n:>6}")

    print(f"\n按突破时大盘环境(上证MA60):")
    print(f"{'信号/环境':<16}{'+60日':>8}{'命中率':>8}{'样本':>8}")
    for typ in ["N字型", "W型"]:
        for label, want in [("大盘健康", True), ("大盘走坏", False)]:
            rows = [f for r, f in greg[typ] if r == want]
            if rows:
                _, _, r60, hit, n = summ(rows)
                print(f"{typ+'·'+label:<16}{r60*100:>+7.1f}%{hit*100:>6.0f}%{n:>8}")


if __name__ == "__main__":
    main()
