"""
run_segment_zigzag.py — 指数/ETF 的 ZigZag 事后趋势分段 + "只吃中间"理论上限(第1步:标准答案)

把指数日线用 ZigZag(价格自极值反向超过 --thr 才确认转折)切成上升段/下降段,产出:
  ① 可视化 HTML(价格曲线 + 上升段红/下降段绿着色 + 转折点标记);
  ② 量化:段数、平均段幅、"只吃每段中间 --eat 比例"的理论复利收益,对比买入持有与满段捕获。
这是天花板基准——后续实时检测器(均线/通道/变点)拿"段捕获率"对标它。

⚠️ 这是**事后分段(用了未来数据)**,只能当标准答案量上限,不是可交易信号。实时检测见后续脚本。

环境：.venv312。用法：python swing/run_segment_zigzag.py --code 000001.SH --thr 0.15
依赖：DuckDB(index_daily);matplotlib。
"""

import argparse
import base64
import io
import os
_ROOT = os.path.dirname(os.path.abspath(__file__))
while _ROOT != "/" and not os.path.exists(os.path.join(_ROOT, "cache_tushare.py")):
    _ROOT = os.path.dirname(_ROOT)
import sys

sys.path.insert(0, _ROOT)

import duckdb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from cache_tushare import DUCKDB_PATH

OUT = os.path.join(_ROOT, "swing/segment_report.html")


def _fetch_index_tushare(code, start):
    """库里没有该指数时,从 tushare index_daily 拉(读 .pyenv.local 的 token),分年拉避免8000行上限。"""
    env = os.path.join(_ROOT, ".pyenv.local")
    if os.path.exists(env):
        for line in open(env):
            if line.strip().startswith("TUSHARE_TOKEN") and "=" in line:
                os.environ["TUSHARE_TOKEN"] = line.split("=", 1)[1].strip().strip('"').strip("'")
    import tushare as ts
    ts.set_token(os.environ["TUSHARE_TOKEN"])
    pro = ts.pro_api()
    s = start.replace("-", "")
    parts = []
    for y in range(int(s[:4]), pd.Timestamp.today().year + 1):
        d = pro.index_daily(ts_code=code, start_date=f"{max(int(s), y*10000+101)}", end_date=f"{y}1231")
        if d is not None and len(d):
            parts.append(d[["trade_date", "close"]])
    if not parts:
        return None
    df = pd.concat(parts, ignore_index=True)
    df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
    return df.sort_values("trade_date").reset_index(drop=True)


def _zigzag(close, thr):
    """返回转折点索引列表(交替的谷/峰),价格自极值反向超过 thr 才确认转折(双轨追极值)。"""
    piv = [0]
    trend = 0
    hi_i = lo_i = 0
    hi_p = lo_p = close[0]
    for i in range(1, len(close)):
        p = close[i]
        if p > hi_p:
            hi_i, hi_p = i, p
        if p < lo_p:
            lo_i, lo_p = i, p
        if trend >= 0 and p <= hi_p * (1 - thr):
            if hi_i != piv[-1]:
                piv.append(hi_i)
            trend = -1
            hi_i = lo_i = i
            hi_p = lo_p = p
        elif trend <= 0 and p >= lo_p * (1 + thr):
            if lo_i != piv[-1]:
                piv.append(lo_i)
            trend = 1
            hi_i = lo_i = i
            hi_p = lo_p = p
    if piv[-1] != len(close) - 1:
        piv.append(len(close) - 1)
    return piv


def _fig_b64(dates, close, piv):
    """画价格 + 分段着色 + 转折点。"""
    plt.rcParams["font.sans-serif"] = ["Arial Unicode MS", "PingFang SC", "Heiti SC"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, ax = plt.subplots(figsize=(12, 5))
    for a, b in zip(piv[:-1], piv[1:]):
        up = close[b] >= close[a]
        ax.plot(dates[a:b + 1], close[a:b + 1], color="#c0392b" if up else "#27ae60", linewidth=1.6)
    ax.scatter([dates[i] for i in piv], [close[i] for i in piv], color="#2c3e50", s=18, zorder=5)
    ax.set_ylabel("收盘价")
    ax.grid(alpha=0.3)
    fig.autofmt_xdate()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default="000001.SH", help="指数 ts_code,如 000001.SH 上证")
    ap.add_argument("--thr", type=float, default=0.15, help="ZigZag 转折阈值(0.15=15%)")
    ap.add_argument("--eat", type=float, default=0.6, help="只吃每段中间比例(0.6=中段60%)")
    ap.add_argument("--start", default="2018-01-01")
    args = ap.parse_args()

    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    df = con.execute(
        "SELECT trade_date, close FROM index_daily WHERE ts_code=? AND trade_date>=? ORDER BY trade_date",
        [args.code, args.start]).fetch_df()
    con.close()
    if len(df) < 30:
        df = _fetch_index_tushare(args.code, args.start)
    if df is None or len(df) < 30:
        print(f"数据不足:{args.code} 仅 {0 if df is None else len(df)} 行"); return
    dates = pd.to_datetime(df["trade_date"]).tolist()
    close = df["close"].to_numpy()
    piv = _zigzag(close, args.thr)

    ups, downs = [], []
    for a, b in zip(piv[:-1], piv[1:]):
        seg = close[b] / close[a] - 1
        (ups if seg >= 0 else downs).append((a, b, seg))

    margin = (1 - args.eat) / 2
    nav_eat = 1.0
    for a, b, seg in ups:
        lo, hi = close[a], close[b]
        entry = lo + margin * (hi - lo)
        exit_ = lo + (1 - margin) * (hi - lo)
        nav_eat *= (exit_ / entry) * (1 - 0.0006 - 0.0006)
    nav_full = 1.0
    for a, b, seg in ups:
        nav_full *= (1 + seg) * (1 - 0.0012)
    bh = close[-1] / close[0] - 1

    img = _fig_b64(dates, close, piv)
    up_amp = np.mean([s for _, _, s in ups]) if ups else 0
    dn_amp = np.mean([s for _, _, s in downs]) if downs else 0

    rows = ""
    for a, b, seg in [(a, b, s) for a, b, s in sorted(ups + downs, key=lambda x: x[0])]:
        kind = "上升" if seg >= 0 else "下降"
        days = (dates[b] - dates[a]).days
        cls = "pos" if seg >= 0 else "neg"
        rows += (f"<tr><td class='{cls}'>{kind}</td><td>{dates[a].date()}</td><td>{dates[b].date()}</td>"
                 f"<td>{days}天</td><td class='{cls}'>{seg*100:+.1f}%</td></tr>")

    html = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>趋势分段({args.code})</title><style>
body{{font-family:-apple-system,"PingFang SC",sans-serif;max-width:1040px;margin:24px auto;padding:0 16px;color:#222}}
h1{{font-size:21px}} h2{{font-size:16px;border-left:4px solid #c0392b;padding-left:8px;margin-top:26px}}
table{{border-collapse:collapse;width:100%;font-size:14px;margin:8px 0}} th,td{{border:1px solid #ddd;padding:5px 9px;text-align:center}}
th{{background:#f7f7f7}} .pos{{color:#c0392b}} .neg{{color:#27ae60}}
.cards{{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0}}
.card{{flex:1;min-width:130px;background:#fafafa;border:1px solid #eee;border-radius:8px;padding:12px;text-align:center}}
.card .v{{font-size:21px;font-weight:700}} .card .l{{font-size:12px;color:#888;margin-top:4px}}
.note{{background:#fff8e1;border:1px solid #ffe082;border-radius:6px;padding:10px 14px;font-size:13px;color:#6d4c41}}
</style></head><body>
<h1>趋势分段(ZigZag 事后)— {args.code}</h1>
<p>区间 <b>{dates[0].date()} ~ {dates[-1].date()}</b> | 转折阈值 {args.thr*100:.0f}% | 只吃中段 {args.eat*100:.0f}%</p>
<div class="cards">
<div class="card"><div class="v">{len(ups)}/{len(downs)}</div><div class="l">上升段/下降段 数</div></div>
<div class="card"><div class="v pos">{up_amp*100:+.1f}%</div><div class="l">平均上升段幅</div></div>
<div class="card"><div class="v neg">{dn_amp*100:+.1f}%</div><div class="l">平均下降段幅</div></div>
<div class="card"><div class="v">{(bh)*100:+.1f}%</div><div class="l">买入持有</div></div>
<div class="card"><div class="v pos">{(nav_full-1)*100:+.1f}%</div><div class="l">满段捕获(只做上升段)</div></div>
<div class="card"><div class="v pos">{(nav_eat-1)*100:+.1f}%</div><div class="l">只吃中段{args.eat*100:.0f}%(上限)</div></div>
</div>
<h2>分段图(红=上升段,绿=下降段)</h2>
<img src="data:image/png;base64,{img}" style="width:100%">
<h2>各段明细</h2>
<table><tr><th>类型</th><th>起</th><th>止</th><th>历时</th><th>涨跌幅</th></tr>{rows}</table>
<div class="note"><b>⚠️</b> 这是<b>事后分段(用了未来数据)</b>,仅作"标准答案"量化理论上限,<b>不是可交易信号</b>。
"满段捕获"=完美吃下每个上升段;"只吃中段{args.eat*100:.0f}%"=放弃头尾、晚进晚出后的更现实上限。
实时检测器(下一步)的目标是逼近这个上限,差距=进出场滞后+假信号的代价。</div>
</body></html>"""
    out = OUT.replace("segment_report.html", f"segment_{args.code.replace('.', '_')}.html")
    with open(out, "w") as f:
        f.write(html)
    print(f"报告:{out}")
    print(f"{args.code} {dates[0].date()}~{dates[-1].date()} | 上升段{len(ups)}/下降段{len(downs)} | "
          f"买入持有{bh*100:+.1f}% | 满段{( nav_full-1)*100:+.1f}% | 吃中段{(nav_eat-1)*100:+.1f}%")


if __name__ == "__main__":
    main()
