"""
run_patterns.py — 标记 N字型突破 与 W型突破(基于 ZigZag 转折点结构)

用细 ZigZag(--thr 默认9%)取转折点,按结构匹配两种形态的"突破确认日":
  N字型(上升中继):低A → 高B → 回调低C(A<C<B,抬高的低点)→ 收盘突破B
  W型(双底反转):高 → 低B → 高C(颈线)→ 低D(D≈B 双底)→ 收盘突破颈线C
各抽样可视化:灰=价格,标 A/B/C(/D) 转折点,★=突破日。供观察形态与启动关系。

环境：.venv312。用法：python swing/run_patterns.py --thr 0.09 --sample 8
依赖：DuckDB(daily/daily_basic/adj_factor/stock_meta);matplotlib;复用 run_segment_zigzag。
"""

import argparse
import base64
import io
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.expanduser("~/AI/quart"))

import duckdb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from cache_tushare import DUCKDB_PATH
from run_segment_zigzag import _zigzag

OUT = os.path.expanduser("~/AI/quart/swing/patterns_samples.html")
W_TOL = 0.06


def _pivots_typed(c, thr):
    """返回 [(idx, is_low)],基于 ZigZag。"""
    piv = _zigzag(c, thr)
    if len(piv) < 2:
        return []
    out = []
    for j, p in enumerate(piv):
        if j < len(piv) - 1:
            is_low = c[piv[j + 1]] > c[p]
        else:
            is_low = c[piv[j - 1]] < c[p]
        out.append((p, is_low))
    return out


def _detect(c, thr, start_i):
    """逐日检测 N字/W型突破(首次突破当日),返回 [(type, bo_idx, [piv idxs])]。"""
    pv = _pivots_typed(c, thr)
    if len(pv) < 3:
        return []
    events = []
    n = len(c)
    pidx = [p[0] for p in pv]
    for t in range(max(start_i, 30), n):
        prior = [k for k in range(len(pv)) if pidx[k] < t]
        if len(prior) < 3:
            continue
        # N字: 末三个 = 低A 高B 低C
        a, b, cc = prior[-3], prior[-2], prior[-1]
        if pv[a][1] and (not pv[b][1]) and pv[cc][1]:
            pa, pb, pcc = c[pidx[a]], c[pidx[b]], c[pidx[cc]]
            if pa < pcc < pb and c[t] > pb and c[t - 1] <= pb:
                events.append(("N字型", t, [pidx[a], pidx[b], pidx[cc]]))
                continue
        # W型: 末四个 = 高 低B 高C 低D
        if len(prior) >= 4:
            h0, lb, hc, ld = prior[-4], prior[-3], prior[-2], prior[-1]
            if (not pv[h0][1]) and pv[lb][1] and (not pv[hc][1]) and pv[ld][1]:
                pB, pC, pD = c[pidx[lb]], c[pidx[hc]], c[pidx[ld]]
                if abs(pD - pB) / pB < W_TOL and pC > pB and pC > pD and c[t] > pC and c[t - 1] <= pC:
                    events.append(("W型", t, [pidx[lb], pidx[hc], pidx[ld]]))
    return events


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--thr", type=float, default=0.09)
    ap.add_argument("--sample", type=int, default=8)
    ap.add_argument("--n", type=int, default=600)
    args = ap.parse_args()

    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    sel = con.execute("SELECT MAX(trade_date) FROM daily_basic").fetchone()[0]
    liquid = [r[0] for r in con.execute("""SELECT ts_code FROM daily_basic WHERE trade_date=? AND ts_code NOT LIKE '%.BJ'
        ORDER BY circ_mv DESC LIMIT ?""", [sel, args.n]).fetchall()]
    names = dict(con.execute("SELECT ts_code,name FROM stock_meta").fetchall())
    px = con.execute("""SELECT d.ts_code,d.trade_date,d.close*a.adj_factor c
        FROM daily d JOIN adj_factor a ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date
        WHERE d.trade_date>=? AND d.ts_code IN (SELECT UNNEST(?))""", ["2022-06-01", liquid]).fetch_df()
    con.close()
    px["trade_date"] = pd.to_datetime(px["trade_date"])

    allev = {"N字型": [], "W型": []}
    for ts, g in px.groupby("ts_code"):
        g = g.sort_values("trade_date").reset_index(drop=True)
        c = g["c"].to_numpy()
        if len(c) < 60:
            continue
        for typ, bo, pvs in _detect(c, args.thr, 30):
            allev[typ].append((ts, bo, pvs, g))

    print(f"流动性{args.n}只 | ZigZag阈值{args.thr*100:.0f}% | N字型 {len(allev['N字型'])} 个, W型 {len(allev['W型'])} 个\n")
    rs = np.random.RandomState(0)
    panels = []
    for typ in ["N字型", "W型"]:
        ev = allev[typ]
        if not ev:
            continue
        for i in rs.choice(len(ev), min(args.sample, len(ev)), replace=False):
            panels.append((typ, *ev[i]))

    plt.rcParams["font.sans-serif"] = ["Arial Unicode MS", "PingFang SC", "Heiti SC"]
    plt.rcParams["axes.unicode_minus"] = False
    cols = 4
    rows = (len(panels) + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(16, 3.2 * rows))
    axes = np.array(axes).reshape(-1)
    table = []
    for ax, (typ, ts, bo, pvs, g) in zip(axes, panels):
        c = g["c"].to_numpy(); dts = g["trade_date"].to_numpy()
        s = max(0, pvs[0] - 20); e = min(len(c), bo + 40)
        ax.plot(dts[s:e], c[s:e], color="#888", lw=1)
        letters = ["A", "B", "C", "D"]
        for k, p in enumerate(pvs):
            ax.scatter([dts[p]], [c[p]], color="#2c3e50", s=28, zorder=5)
            ax.annotate(letters[k], (dts[p], c[p]), fontsize=9, color="#2c3e50")
        ax.scatter([dts[bo]], [c[bo]], marker="*", color="#c0392b", s=160, zorder=6)
        clr = "#c0392b" if typ == "N字型" else "#8e44ad"
        ax.set_title(f"[{typ}] {names.get(ts,ts)} {pd.Timestamp(dts[bo]).date()}", fontsize=9, color=clr)
        ax.tick_params(labelsize=7)
        table.append((typ, ts, names.get(ts, ts), str(pd.Timestamp(dts[bo]).date())))
    for ax in axes[len(panels):]:
        ax.axis("off")
    fig.tight_layout()
    buf = io.BytesIO(); fig.savefig(buf, format="png", dpi=90, bbox_inches="tight"); plt.close(fig)
    img = base64.b64encode(buf.getvalue()).decode()

    trows = "".join(f"<tr><td>{a}</td><td>{b}</td><td>{c}</td><td>{d}</td></tr>" for a, b, c, d in table)
    html = f"""<!doctype html><html lang=zh><head><meta charset=utf-8><title>N字/W型突破</title><style>
body{{font-family:-apple-system,"PingFang SC",sans-serif;max-width:1180px;margin:20px auto;padding:0 14px;color:#222}}
h1{{font-size:20px}} table{{border-collapse:collapse;width:100%;font-size:13px;margin-top:10px}}
th,td{{border:1px solid #ddd;padding:5px 8px;text-align:center}} th{{background:#f7f7f7}}
.note{{background:#fff8e1;border:1px solid #ffe082;border-radius:6px;padding:10px;font-size:13px;color:#6d4c41;margin-top:10px}}
</style></head><body>
<h1>N字型 / W型 突破样本(★=突破确认日,A/B/C/D=转折点)</h1>
<p>N字型 全市场 {len(allev['N字型'])} 个 | W型 {len(allev['W型'])} 个 | 各抽 {args.sample} 个</p>
<img src="data:image/png;base64,{img}" style="width:100%">
<table><tr><th>形态</th><th>代码</th><th>名称</th><th>突破日</th></tr>{trows}</table>
<div class=note>N字型=低A→高B→抬高低C→破B;W型=低B→颈线C→低D(双底)→破C。
看★突破后是否真的走出主升浪——这决定形态作为"埋伏信号"的有效性,下一步可统计突破后N日收益分布。</div>
</body></html>"""
    with open(OUT, "w") as f:
        f.write(html)
    print(f"报告:{OUT}")
    for a, b, c, d in table:
        print(f"  [{a}] {b} {c} {d}")


if __name__ == "__main__":
    main()
