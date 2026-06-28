"""
run_volbreak.py — "双放量突破"形态 检测 + 主升浪有效性验证

形态(用户图示):横盘/下跌后第一次放量①蓄势,之后第二次放量②同时突破前高 → 可能启动主升。
检测:当日 量>VK×20日均量(放量) 且 收盘突破前 PH 日高 且 过去 LOOKBACK 日内已有过一次放量
      且 处于1年区间下方 POS_MAX(低/中位)。
验证:突破后 20/40/60 日收益 + 60日内最高≥50%(主升浪)命中率,对比随机基础率,并按大盘环境分组。

环境：.venv312。用法：python swing/run_volbreak.py --vk 2.0 --n 800
依赖：DuckDB(daily/daily_basic/adj_factor/index_daily)。
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.expanduser("~/AI/quart"))

import duckdb
import numpy as np
import pandas as pd

from cache_tushare import DUCKDB_PATH

PH, LOOKBACK, POS_MAX, MW_GAIN, MW_DAYS = 40, 40, 0.7, 0.50, 60


def _detect_volbreak(c, h, v, vk):
    """返回双放量突破的突破日索引列表。"""
    c = pd.Series(c); h = pd.Series(h); v = pd.Series(v)
    vma = v.rolling(20).mean().shift(1)
    spike = v > vk * vma
    prior_high = h.rolling(PH).max().shift(1)
    had_prior = spike.shift(1).rolling(LOOKBACK).max().fillna(0).astype(bool)
    pos1y = (c - c.rolling(250).min()) / (c.rolling(250).max() - c.rolling(250).min())
    brk = (c > prior_high) & (c.shift(1) <= prior_high)
    sig = spike & brk & had_prior & (pos1y < POS_MAX)
    return [i for i in range(30, len(c)) if bool(sig.iloc[i])]


def _fwd(c, t):
    if t + MW_DAYS >= len(c):
        return None
    return c[t + 20] / c[t] - 1, c[t + 40] / c[t] - 1, c[t + 60] / c[t] - 1, c[t + 1:t + MW_DAYS + 1].max() / c[t] - 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vk", type=float, default=2.0)
    ap.add_argument("--n", type=int, default=800)
    args = ap.parse_args()

    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    sel = con.execute("SELECT MAX(trade_date) FROM daily_basic").fetchone()[0]
    liquid = [r[0] for r in con.execute("""SELECT ts_code FROM daily_basic WHERE trade_date=? AND ts_code NOT LIKE '%.BJ'
        ORDER BY circ_mv DESC LIMIT ?""", [sel, args.n]).fetchall()]
    idx = con.execute("SELECT trade_date,close FROM index_daily WHERE ts_code='000001.SH' ORDER BY trade_date").fetch_df()
    px = con.execute("""SELECT d.ts_code,d.trade_date,d.high*a.adj_factor h,d.close*a.adj_factor c,d.vol v
        FROM daily d JOIN adj_factor a ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date
        WHERE d.trade_date>=? AND d.ts_code IN (SELECT UNNEST(?))""", ["2022-06-01", liquid]).fetch_df()
    con.close()
    px["trade_date"] = pd.to_datetime(px["trade_date"])
    idx["trade_date"] = pd.to_datetime(idx["trade_date"]); ima = idx["close"].rolling(60).mean()
    regime = dict(zip(idx["trade_date"], ((idx["close"] > ima) & (ima > ima.shift(10))).fillna(False)))

    grp = {"双放量突破": [], "随机基础率": []}
    greg = []
    rs = np.random.RandomState(0)
    for ts, g in px.groupby("ts_code"):
        g = g.sort_values("trade_date").reset_index(drop=True)
        c = g["c"].to_numpy(); h = g["h"].to_numpy(); v = g["v"].to_numpy(); gd = g["trade_date"].to_numpy()
        if len(c) < 120:
            continue
        for bo in _detect_volbreak(c, h, v, args.vk):
            f = _fwd(c, bo)
            if f:
                grp["双放量突破"].append(f)
                greg.append((regime.get(pd.Timestamp(gd[bo]), False), f))
        for t in rs.choice(range(30, len(c) - MW_DAYS), min(15, len(c) - MW_DAYS - 30), replace=False):
            f = _fwd(c, t)
            if f:
                grp["随机基础率"].append(f)

    def summ(rows):
        a = np.array(rows)
        return a[:, 0].mean(), a[:, 1].mean(), a[:, 2].mean(), (a[:, 3] >= MW_GAIN).mean(), len(a)

    print(f"流动性{args.n}只 | 2022-06~今 | 放量阈值 {args.vk}× | 主升浪=60日内最高≥{MW_GAIN*100:.0f}%\n")
    print(f"{'信号':<12}{'+20日':>8}{'+40日':>8}{'+60日':>8}{'命中率':>8}{'样本':>8}")
    base = summ(grp["随机基础率"])[3]
    for nm in ["随机基础率", "双放量突破"]:
        r20, r40, r60, hit, n = summ(grp[nm])
        lift = f"(×{hit/base:.2f})" if nm != "随机基础率" else ""
        print(f"{nm:<12}{r20*100:>+7.1f}%{r40*100:>+7.1f}%{r60*100:>+7.1f}%{hit*100:>6.0f}%{lift:<8}{n:>6}")

    print("\n按突破时大盘环境:")
    for label, want in [("大盘健康", True), ("大盘走坏", False)]:
        rows = [f for r, f in greg if r == want]
        if rows:
            _, _, r60, hit, n = summ(rows)
            print(f"  双放量·{label}: +60日 {r60*100:+.1f}% | 命中率 {hit*100:.0f}% | 样本 {n}")


if __name__ == "__main__":
    main()
