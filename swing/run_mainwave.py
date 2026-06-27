"""
run_mainwave.py — ZigZag 标注"主升浪"并抽样可视化(看靶子长什么样)

主升浪定义:ZigZag 上升腿(谷→峰)涨幅≥MIN_GAIN 且≤MAX_DAYS 交易日内完成(是"浪"非慢牛)。
在流动性股上扫出全部主升浪,随机抽 SAMPLE 个画成小图网格(启动前后),供人工观察启动前特征。

环境：.venv312。用法：python swing/run_mainwave.py --gain 0.5 --maxdays 120 --sample 20
依赖：DuckDB(daily/daily_basic/adj_factor/stock_st/stock_meta);matplotlib;复用 run_segment_zigzag。
"""

import argparse
import base64
import io
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.expanduser("~/AI/quart/first10"))

import duckdb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from cache_tushare import DUCKDB_PATH
from run_segment_zigzag import _zigzag

OUT = os.path.expanduser("~/AI/quart/swing/mainwave_samples.html")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gain", type=float, default=0.5)
    ap.add_argument("--maxdays", type=int, default=120)
    ap.add_argument("--sample", type=int, default=20)
    ap.add_argument("--n", type=int, default=800)
    ap.add_argument("--thr", type=float, default=0.18)
    args = ap.parse_args()

    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    sel = con.execute("SELECT MAX(trade_date) FROM daily_basic").fetchone()[0]
    liquid = [r[0] for r in con.execute("""SELECT ts_code FROM daily_basic WHERE trade_date=? AND ts_code NOT LIKE '%.BJ'
        ORDER BY circ_mv DESC LIMIT ?""", [sel, args.n]).fetchall()]
    names = dict(con.execute("SELECT ts_code,name FROM stock_meta").fetchall())
    px = con.execute("""SELECT d.ts_code,d.trade_date,d.close*a.adj_factor c
        FROM daily d JOIN adj_factor a ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date
        WHERE d.trade_date>=? AND d.ts_code IN (SELECT UNNEST(?))""", ["2021-06-01", liquid]).fetch_df()
    con.close()
    px["trade_date"] = pd.to_datetime(px["trade_date"])

    waves = []
    for ts, g in px.groupby("ts_code"):
        g = g.sort_values("trade_date").reset_index(drop=True)
        c = g["c"].to_numpy()
        if len(c) < 60:
            continue
        piv = _zigzag(c, args.thr)
        for a, b in zip(piv[:-1], piv[1:]):
            gain = c[b] / c[a] - 1
            days = b - a
            if gain >= args.gain and days <= args.maxdays and days >= 5:
                waves.append((ts, a, b, gain, days, g))

    print(f"流动性{args.n}只 | 主升浪定义: 涨幅≥{args.gain*100:.0f}% 且 ≤{args.maxdays}交易日")
    print(f"共扫出主升浪 {len(waves)} 个,随机抽 {args.sample} 个可视化\n")
    if not waves:
        return
    rs = np.random.RandomState(0)
    idxs = rs.choice(len(waves), min(args.sample, len(waves)), replace=False)
    sample = [waves[i] for i in idxs]

    cols = 4
    rows = (len(sample) + cols - 1) // cols
    plt.rcParams["font.sans-serif"] = ["Arial Unicode MS", "PingFang SC", "Heiti SC"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, axes = plt.subplots(rows, cols, figsize=(16, 3.2 * rows))
    axes = np.array(axes).reshape(-1)
    table = []
    for ax, (ts, a, b, gain, days, g) in zip(axes, sample):
        c = g["c"].to_numpy(); dts = g["trade_date"].to_numpy()
        s = max(0, a - 50); e = min(len(c), b + 20)
        ax.plot(dts[s:e], c[s:e], color="#888", lw=1)
        ax.plot(dts[a:b + 1], c[a:b + 1], color="#c0392b", lw=2)
        ax.scatter([dts[a]], [c[a]], color="#27ae60", s=30, zorder=5)
        ax.set_title(f"{names.get(ts,ts)} +{gain*100:.0f}%/{days}d", fontsize=10)
        ax.tick_params(labelsize=7)
        sd = pd.Timestamp(dts[a]).date(); ed = pd.Timestamp(dts[b]).date()
        table.append((ts, names.get(ts, ts), str(sd), str(ed), f"+{gain*100:.0f}%", f"{days}d"))
    for ax in axes[len(sample):]:
        ax.axis("off")
    fig.tight_layout()
    buf = io.BytesIO(); fig.savefig(buf, format="png", dpi=90, bbox_inches="tight"); plt.close(fig)
    img = base64.b64encode(buf.getvalue()).decode()

    trows = "".join(f"<tr><td>{a}</td><td>{b}</td><td>{c}</td><td>{d}</td><td class=pos>{e}</td><td>{f}</td></tr>"
                    for a, b, c, d, e, f in table)
    html = f"""<!doctype html><html lang=zh><head><meta charset=utf-8><title>主升浪样本</title><style>
body{{font-family:-apple-system,"PingFang SC",sans-serif;max-width:1180px;margin:20px auto;padding:0 14px;color:#222}}
h1{{font-size:20px}} table{{border-collapse:collapse;width:100%;font-size:13px;margin-top:10px}}
th,td{{border:1px solid #ddd;padding:5px 8px;text-align:center}} th{{background:#f7f7f7}} .pos{{color:#c0392b}}
.note{{background:#fff8e1;border:1px solid #ffe082;border-radius:6px;padding:10px;font-size:13px;color:#6d4c41;margin-top:10px}}
</style></head><body>
<h1>主升浪样本(灰=前后,红=主升浪腿,绿点=启动谷底)</h1>
<p>定义:涨幅≥{args.gain*100:.0f}% 且 ≤{args.maxdays}交易日 | 全市场共 {len(waves)} 个 | 随机抽 {len(sample)} 个</p>
<img src="data:image/png;base64,{img}" style="width:100%">
<table><tr><th>代码</th><th>名称</th><th>启动日</th><th>见顶日</th><th>涨幅</th><th>历时</th></tr>{trows}</table>
<div class=note>看每张图<b>绿点(启动)左侧</b>的形态:是否有共同的"蓄势"特征(横盘收敛/缩量/位置低)?
这决定了"主升浪前埋伏"在统计上有没有可学的规律。</div>
</body></html>"""
    with open(OUT, "w") as f:
        f.write(html)
    print(f"报告:{OUT}")
    print(f"{'代码':<11}{'名称':<9}{'启动':<12}{'见顶':<12}{'涨幅':>7}{'历时':>6}")
    for a, b, c, d, e, f in table:
        print(f"{a:<11}{b:<9}{c:<12}{d:<12}{e:>7}{f:>6}")


if __name__ == "__main__":
    main()
