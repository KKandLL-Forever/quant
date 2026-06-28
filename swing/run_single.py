"""
run_single.py — 单只股票的唐奇安+ADX 趋势跟踪报告(你选股,系统管进出/吃中段)

对指定个股从 --start 起跑 唐奇安55/20 + ADX>20 的多/空仓择时(突破进、破位出、ADX闸门),
产出自包含 HTML:价格图(唐奇安上下轨 + 持仓区间着色 + 进出场点)、每笔交易、对比同期买入持有。
定位:不替你选股,只对你选中的票给"何时进/何时该撤"的趋势纪律。

PIT:信号用 t-1 收盘,t 收盘执行。环境：.venv312。
用法：python swing/run_single.py --code 002463.SZ --start 2026-01-01
依赖：DuckDB(daily/adj_factor/stock_meta);matplotlib。
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
import run_detectors as D

DON_N, DON_EXIT, ADX_THR, COST = 55, 20, 20, 0.0006
OUT = os.path.expanduser("~/AI/quart/swing/single_{code}.html")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default="002463.SZ")
    ap.add_argument("--start", default="2026-01-01")
    args = ap.parse_args()

    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    g = con.execute("""SELECT d.trade_date td, d.high*a.adj_factor h, d.low*a.adj_factor l, d.close*a.adj_factor c
        FROM daily d JOIN adj_factor a ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date
        WHERE d.ts_code=? AND d.trade_date>=? ORDER BY d.trade_date""",
        [args.code, "2025-01-01"]).fetch_df()
    nm = con.execute("SELECT name FROM stock_meta WHERE ts_code=?", [args.code]).fetchone()
    con.close()
    nm = nm[0] if nm else args.code
    g["td"] = pd.to_datetime(g["td"]); g = g.set_index("td")
    c, h, l = g["c"], g["h"], g["l"]
    don_up = h.rolling(DON_N).max().shift(1)
    don_dn = l.rolling(DON_EXIT).min().shift(1)
    adx_ok = (D._adx(h, l, c) > ADX_THR)
    breakout = (c > don_up)
    breakdown = (c < don_dn)

    start = pd.Timestamp(args.start)
    mask = c.index >= start
    ct = c[mask]
    dates = ct.index
    bo = breakout.shift(1).fillna(False)[mask].values
    bd = breakdown.shift(1).fillna(False)[mask].values
    ax = adx_ok.shift(1).fillna(False)[mask].values
    parr = []
    hold = True
    for i in range(len(dates)):
        if i > 0:
            if hold and bd[i]:
                hold = False
            elif (not hold) and bo[i] and ax[i]:
                hold = True
        parr.append(1.0 if hold else 0.0)
    pe = pd.Series(parr, index=dates)
    ret = ct.pct_change().fillna(0).values
    p = pe.values
    chg = np.abs(np.diff(np.concatenate([[0], p])))
    sr = p * ret - chg * COST
    nav = np.cumprod(1 + sr)
    bh = np.cumprod(1 + ret)

    # 交易区间
    trades = []
    inpos = False; ep = ed = None
    for i in range(len(dates)):
        if not inpos and p[i] > 0:
            inpos = True; ep = ct.iloc[i]; ed = dates[i]
        elif inpos and p[i] == 0:
            trades.append((ed, dates[i], ct.iloc[i] / ep - 1)); inpos = False
    if inpos:
        trades.append((ed, dates[-1], ct.iloc[-1] / ep - 1))

    peak = np.maximum.accumulate(nav); mdd = ((nav - peak) / peak).min()

    plt.rcParams["font.sans-serif"] = ["Arial Unicode MS", "PingFang SC", "Heiti SC"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(dates, ct.values, color="#333", lw=1.5, label="收盘价")
    ax.plot(dates, don_up[mask].values, color="#c0392b", lw=0.9, ls="--", label="唐奇安55日上轨(进场)")
    ax.plot(dates, don_dn[mask].values, color="#27ae60", lw=0.9, ls="--", label="唐奇安20日下轨(离场)")
    for s, e, r in trades:
        ax.axvspan(s, e, color="#c0392b" if r >= 0 else "#27ae60", alpha=0.12)
        ax.scatter([s], [ct.loc[s]], marker="^", color="#c0392b", s=70, zorder=5)
        ax.scatter([e], [ct.loc[e]], marker="v", color="#27ae60", s=70, zorder=5)
    ax.legend(loc="upper left", fontsize=9); ax.grid(alpha=0.3); fig.autofmt_xdate()
    buf = io.BytesIO(); fig.savefig(buf, format="png", dpi=110, bbox_inches="tight"); plt.close(fig)
    img = base64.b64encode(buf.getvalue()).decode()

    trows = ""
    for s, e, r in trades:
        cls = "pos" if r >= 0 else "neg"
        open_tag = " (持仓中)" if e == dates[-1] and p[-1] > 0 else ""
        trows += f"<tr><td>{s.date()}</td><td>{e.date()}{open_tag}</td><td class={cls}>{r*100:+.1f}%</td><td>{(e-s).days}天</td></tr>"
    trows = trows or "<tr><td colspan=4>区间内无信号(未触发进场)</td></tr>"

    html = f"""<!doctype html><html lang=zh><head><meta charset=utf-8><title>{nm} 趋势跟踪</title><style>
body{{font-family:-apple-system,"PingFang SC",sans-serif;max-width:980px;margin:24px auto;padding:0 16px;color:#222}}
h1{{font-size:21px}} h2{{font-size:16px;border-left:4px solid #c0392b;padding-left:8px;margin-top:22px}}
table{{border-collapse:collapse;width:100%;font-size:14px}} th,td{{border:1px solid #ddd;padding:6px 10px;text-align:center}}
th{{background:#f7f7f7}} .pos{{color:#c0392b}} .neg{{color:#27ae60}}
.cards{{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0}}
.card{{flex:1;min-width:120px;background:#fafafa;border:1px solid #eee;border-radius:8px;padding:12px;text-align:center}}
.card .v{{font-size:21px;font-weight:700}} .card .l{{font-size:12px;color:#888;margin-top:4px}}
.note{{background:#fff8e1;border:1px solid #ffe082;border-radius:6px;padding:10px 14px;font-size:13px;color:#6d4c41}}
</style></head><body>
<h1>{nm}（{args.code}）唐奇安+ADX 趋势跟踪</h1>
<p>{dates[0].date()} 起<b>开盘即买入</b>,之后跌破唐奇安20日下轨则<b>卖出</b>、再突破55日上轨(ADX&gt;{ADX_THR})则<b>买回</b> | 红区=持有 绿区=空仓</p>
<div class=cards>
<div class=card><div class="v {'pos' if nav[-1]>=1 else 'neg'}">{(nav[-1]-1)*100:+.1f}%</div><div class=l>策略收益</div></div>
<div class=card><div class="v {'pos' if bh[-1]>=1 else 'neg'}">{(bh[-1]-1)*100:+.1f}%</div><div class=l>买入持有</div></div>
<div class=card><div class="v neg">{mdd*100:.1f}%</div><div class=l>策略最大回撤</div></div>
<div class=card><div class=v>{len(trades)}</div><div class=l>交易笔数</div></div>
<div class=card><div class=v>{'持仓' if p[-1]>0 else '空仓'}</div><div class=l>当前状态</div></div>
</div>
<h2>价格 + 进出场</h2><img src="data:image/png;base64,{img}" style="width:100%">
<h2>交易明细</h2>
<table><tr><th>进场</th><th>离场</th><th>收益</th><th>持有</th></tr>{trows}</table>
<div class=note><b>⚠️</b> 单只样本极小、仅供直观感受。{dates[0].date()} 开盘强制买入,之后按唐奇安信号进出。
卖出=跌破唐奇安20日下轨;买回=突破55日上轨且ADX&gt;{ADX_THR}。若回调是假摔,会被洗出后高位接回(whipsaw)。未建模涨跌停/滑点。</div>
</body></html>"""
    out = OUT.format(code=args.code.replace(".", "_"))
    with open(out, "w") as f:
        f.write(html)
    print(f"报告:{out}")
    print(f"{nm} {dates[0].date()}~{dates[-1].date()} | 策略 {(nav[-1]-1)*100:+.1f}% vs 买入持有 {(bh[-1]-1)*100:+.1f}% | "
          f"回撤 {mdd*100:.1f}% | {len(trades)}笔 | 当前{'持仓' if p[-1]>0 else '空仓'}")
    for s, e, r in trades:
        print(f"  {s.date()} → {e.date()}  {r*100:+.1f}%")


if __name__ == "__main__":
    main()
