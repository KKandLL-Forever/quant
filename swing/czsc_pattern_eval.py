"""czsc_pattern_eval.py — 统计 czsc 五笔形态(cxt_five_bi_V230619)八类对趋势判断的方向准确率。

因果无泄露:每只股逐 K 增量更新 CZSC,每当形成新的一笔就用 di=1 取当下形态(只用历史),
记录该形态出现时的 bar + 之后 5/10/20 日收益。**全程只喂截止日(默认 2026-05-31)之前的数据**
(价格序列直接截断,前向窗口也必须落在截止日之内,绝不碰之后的数据)。

看涨形态(上颈线突破/类三买/类趋势底背驰/aAb式底背驰)期望后续涨;看跌形态反之。
方向准确率 = 后续收益方向与形态预期一致的比例。

环境：.venv312。用法：python swing/czsc_pattern_eval.py [topN] [截止日]
依赖：czsc, DuckDB。
"""
import sys

import duckdb
import numpy as np
import pandas as pd
import czsc.signals as S
from czsc import CZSC, RawBar, Freq

import os
sys.path.insert(0, os.path.expanduser("~/AI/quart"))
from cache_tushare import DUCKDB_PATH

BULL = {"上颈线突破", "类三买", "类趋势底背驰", "aAb式底背驰"}
BEAR = {"下颈线突破", "类三卖", "类趋势顶背驰", "aAb式顶背驰"}
HZS = [5, 10, 20, 40]


def main():
    topn = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    cutoff = sys.argv[2] if len(sys.argv) > 2 else "2026-05-31"
    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    sel = con.execute("SELECT MAX(trade_date) FROM daily_basic WHERE trade_date<=?", [cutoff]).fetchone()[0]
    codes = [r[0] for r in con.execute("""SELECT ts_code FROM daily_basic WHERE trade_date=? AND ts_code NOT LIKE '%.BJ'
        ORDER BY circ_mv DESC LIMIT ?""", [sel, topn]).fetchall()]
    rows = []
    for n, ts in enumerate(codes):
        g = con.execute("""SELECT trade_date, open*adj_factor o, high*adj_factor h, low*adj_factor l,
            close*adj_factor c, vol FROM daily d JOIN adj_factor a USING(ts_code,trade_date)
            WHERE ts_code=? AND trade_date<=? ORDER BY trade_date""", [ts, cutoff]).fetch_df()
        if len(g) < 120:
            continue
        g["trade_date"] = pd.to_datetime(g["trade_date"])
        cc = g["c"].to_numpy()
        bars = [RawBar(symbol=ts, id=i, dt=r.trade_date, freq=Freq.D, open=r.o, close=r.c,
                       high=r.h, low=r.l, vol=r.vol, amount=0.0) for i, r in g.iterrows()]
        try:
            c = CZSC(bars[:30])
            nb = len(c.bi_list)
            for i in range(30, len(bars)):
                c.update(bars[i])
                if len(c.bi_list) == nb:
                    continue
                nb = len(c.bi_list)
                if g["trade_date"].iloc[i].year < 2022:
                    continue
                cat = str(list(S.cxt_five_bi_V230619(c, di=1).values())[0]).split("_")[0]
                if cat == "其他":
                    continue
                fr = {}
                ok = True
                for H in HZS:
                    if i + H >= len(cc):
                        ok = False; break
                    fr[H] = cc[i + H] / cc[i] - 1
                if not ok:
                    continue
                rows.append({"ts": ts, "cat": cat, **{f"f{H}": fr[H] for H in HZS}})
        except Exception:
            continue
        if (n + 1) % 50 == 0:
            print(f"  ...{n+1}/{len(codes)} 已记录 {len(rows)} 个形态", flush=True)
    con.close()
    df = pd.DataFrame(rows)
    print(f"\n截止日 {cutoff}(实际 {sel})· 池 top{topn} · 命中形态样本 {len(df)}")
    print(f"\n{'形态':<14}{'方向':<5}{'样本':>6}{'10日收益':>9}{'20日收益':>9}{'40日收益':>9}"
          f"{'准@10':>7}{'准@20':>7}{'准@40':>7}")
    for grp, cats in [("看涨", BULL), ("看跌", BEAR)]:
        for cat in cats:
            d = df[df["cat"] == cat]
            if not len(d):
                print(f"  {cat:<12}{grp:<5}{'0':>6}"); continue
            exp_up = grp == "看涨"
            a = {H: ((d[f"f{H}"] > 0) == exp_up).mean() * 100 for H in (10, 20, 40)}
            print(f"  {cat:<12}{grp:<5}{len(d):>6}{d['f10'].mean()*100:>8.1f}%{d['f20'].mean()*100:>8.1f}%"
                  f"{d['f40'].mean()*100:>8.1f}%{a[10]:>6.0f}%{a[20]:>6.0f}%{a[40]:>6.0f}%")
    for H in (10, 20, 40):
        print(f"  基准:全样本{H}日上涨概率 = {(df[f'f{H}']>0).mean()*100:.0f}%")


if __name__ == "__main__":
    main()
