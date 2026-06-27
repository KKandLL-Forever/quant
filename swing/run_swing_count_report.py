"""
run_swing_count_report.py — N/W突破+固定10日+最多持3只 完整净值报告(含回撤+选股清单)

入场 N/W突破且上证健康(次日执行),固定持有10交易日,最多持3只等权,多信号先到先得。
产出自包含 HTML:净值+回撤曲线、汇总指标、全部交易明细(代码/名称/买卖日/收益/持有)。

环境：.venv312。用法：python swing/run_swing_count_report.py --asof 2021-12-31 --hold 3
依赖：DuckDB(daily/daily_basic/adj_factor/index_daily/stock_st/stock_meta);matplotlib;复用 run_patterns。
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
from run_patterns import _detect

THR, HOLD_DAYS, COST = 0.09, 10, 0.0006
OUT = os.path.expanduser("~/AI/quart/swing/count_report.html")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", default="2021-12-31")
    ap.add_argument("--hold", type=int, default=3)
    ap.add_argument("--n", type=int, default=800)
    args = ap.parse_args()
    H = args.hold

    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    idx = con.execute("SELECT trade_date,close FROM index_daily WHERE ts_code='000001.SH' ORDER BY trade_date").fetch_df()
    sel = con.execute("SELECT MAX(trade_date) FROM daily_basic WHERE trade_date<=?", [args.asof]).fetchone()[0]
    liquid = [r[0] for r in con.execute("""SELECT ts_code FROM daily_basic WHERE trade_date=? AND ts_code NOT LIKE '%.BJ'
        AND ts_code NOT IN (SELECT ts_code FROM stock_st WHERE trade_date=?) ORDER BY circ_mv DESC LIMIT ?""",
        [sel, sel, args.n]).fetchall()]
    names = dict(con.execute("SELECT ts_code,name FROM stock_meta").fetchall())
    meta = con.execute("SELECT ts_code,list_date,delist_date FROM stock_meta").fetch_df()
    px = con.execute("""SELECT d.ts_code,d.trade_date,d.close*a.adj_factor c
        FROM daily d JOIN adj_factor a ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date
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

    master = pd.DatetimeIndex(sorted(px["trade_date"].unique()))
    di = {d: i for i, d in enumerate(master)}; T = len(master)
    ENm, RETm, CLm, cols = [], [], [], []
    for ts, g in px.groupby("ts_code"):
        if ts not in ok:
            continue
        g = g.sort_values("trade_date").reset_index(drop=True)
        cc = g["c"].to_numpy(); gd = g["trade_date"].to_numpy()
        en_m = np.zeros(T, bool); ret_m = np.zeros(T); cl_m = np.full(T, np.nan)
        rl = g["c"].pct_change().fillna(0).values
        en_local = np.zeros(len(cc), bool)
        for typ, bo, _ in _detect(cc, THR, 30):
            if regime.get(pd.Timestamp(gd[bo]), False):
                en_local[bo] = True
        en_exec = np.concatenate([[False], en_local[:-1]])
        for k in range(len(cc)):
            mi = di[pd.Timestamp(gd[k])]
            en_m[mi] = en_exec[k]; ret_m[mi] = rl[k]; cl_m[mi] = cc[k]
        ENm.append(en_m); RETm.append(ret_m); CLm.append(cl_m); cols.append(ts)
    EN = np.array(ENm).T; RET = np.array(RETm).T; CL = np.array(CLm).T
    tidx = [i for i in range(T) if master[i] > asof_ts]

    nav = 1.0; navs = []; held = {}; trades = []
    for i in tidx:
        if held:
            nav *= 1 + sum(RET[i, k] for k in held) / H
        for k in [k for k, ei in held.items() if i - ei >= HOLD_DAYS]:
            ei = held.pop(k); nav *= 1 - 2 * COST / H
            r = CL[i, k] / CL[ei, k] - 1 - 2 * COST
            trades.append((master[ei], master[i], cols[k], r, i - ei))
        free = H - len(held)
        if free > 0:
            for k in [k for k in np.where(EN[i])[0] if k not in held][:free]:
                held[k] = i; nav *= 1 - 2 * COST / H
        navs.append(nav)
    navs = np.array(navs); dates = [master[i] for i in tidx]
    peak = np.maximum.accumulate(navs); dd = (navs - peak) / peak; mdd = dd.min()
    rr = np.diff(navs) / navs[:-1]; sh = rr.mean() / rr.std() * np.sqrt(252) if rr.std() > 0 else 0
    ann = navs[-1] ** (252 / len(navs)) - 1
    win = np.mean([t[3] > 0 for t in trades]) if trades else 0

    plt.rcParams["font.sans-serif"] = ["Arial Unicode MS", "PingFang SC", "Heiti SC"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(12, 6), gridspec_kw={"height_ratios": [3, 1]}, sharex=True)
    a1.plot(dates, navs, color="#c0392b", lw=1.6, label=f"策略(持{H}只/固定10日)")
    a1.axhline(1, color="#bbb", lw=0.8); a1.legend(loc="upper left"); a1.set_ylabel("净值"); a1.grid(alpha=0.3)
    a2.fill_between(dates, dd * 100, 0, color="#27ae60", alpha=0.4); a2.set_ylabel("回撤%"); a2.grid(alpha=0.3)
    fig.autofmt_xdate(); buf = io.BytesIO(); fig.savefig(buf, format="png", dpi=110, bbox_inches="tight"); plt.close(fig)
    img = base64.b64encode(buf.getvalue()).decode()

    trows = ""
    for ed, xd, code, r, hd in sorted(trades, key=lambda x: x[0]):
        cls = "pos" if r >= 0 else "neg"
        trows += (f"<tr><td>{ed.date()}</td><td>{xd.date()}</td><td>{code}</td><td>{names.get(code,'')}</td>"
                  f"<td class={cls}>{r*100:+.1f}%</td><td>{hd}</td></tr>")

    html = f"""<!doctype html><html lang=zh><head><meta charset=utf-8><title>固定10日持{H}只</title><style>
body{{font-family:-apple-system,"PingFang SC",sans-serif;max-width:1000px;margin:22px auto;padding:0 14px;color:#222}}
h1{{font-size:20px}} h2{{font-size:16px;border-left:4px solid #c0392b;padding-left:8px;margin-top:22px}}
table{{border-collapse:collapse;width:100%;font-size:13px}} th,td{{border:1px solid #ddd;padding:5px 8px;text-align:center}}
th{{background:#f7f7f7}} .pos{{color:#c0392b}} .neg{{color:#27ae60}}
.cards{{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0}}
.card{{flex:1;min-width:110px;background:#fafafa;border:1px solid #eee;border-radius:8px;padding:12px;text-align:center}}
.card .v{{font-size:20px;font-weight:700}} .card .l{{font-size:12px;color:#888;margin-top:4px}}
.tbl{{max-height:560px;overflow:auto;border:1px solid #eee}}
.note{{background:#fff8e1;border:1px solid #ffe082;border-radius:6px;padding:10px;font-size:13px;color:#6d4c41;margin-top:10px}}
</style></head><body>
<h1>N/W突破 + 固定持有10日 + 最多持{H}只</h1>
<p>{dates[0].date()} ~ {dates[-1].date()} | 入场:N字/W型突破且上证健康 | 离场:固定10交易日 | 等权,多信号先到先得</p>
<div class=cards>
<div class=card><div class="v {'pos' if navs[-1]>=1 else 'neg'}">{(navs[-1]-1)*100:+.0f}%</div><div class=l>总收益</div></div>
<div class=card><div class=v>{ann*100:.0f}%</div><div class=l>年化</div></div>
<div class=card><div class="v neg">{mdd*100:.0f}%</div><div class=l>最大回撤</div></div>
<div class=card><div class=v>{sh:.2f}</div><div class=l>夏普</div></div>
<div class=card><div class=v>{len(trades)}</div><div class=l>交易笔数</div></div>
<div class=card><div class=v>{win*100:.0f}%</div><div class=l>胜率</div></div>
</div>
<h2>净值 + 回撤</h2><img src="data:image/png;base64,{img}" style="width:100%">
<h2>全部交易({len(trades)}笔)</h2>
<div class=tbl><table><tr><th>买入</th><th>卖出</th><th>代码</th><th>名称</th><th>收益</th><th>持有(交易日)</th></tr>{trows}</table></div>
<div class=note><b>⚠️</b> 固定10日离场会切断大赢家,集中持仓下整体偏弱(见净值)。多信号同日按代码顺序先到先得(非择优)。
未建模涨跌停/滑点。仅供观察选股与净值形态。</div>
</body></html>"""
    with open(OUT, "w") as f:
        f.write(html)
    print(f"报告:{OUT}")
    print(f"持{H}只 | 总收益 {(navs[-1]-1)*100:+.0f}% | 回撤 {mdd*100:.0f}% | {len(trades)}笔 | 胜率 {win*100:.0f}%")


if __name__ == "__main__":
    main()
