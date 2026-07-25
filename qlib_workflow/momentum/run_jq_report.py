"""
run_jq_report.py — 市值残差因子(SVR)实盘视角报告:选股清单 + 净值曲线,产出自包含 HTML

从 --start(默认2026-01-01)起按稳健参数(SVR / STEP10 / k10 / C1)逐期选股,记录每期
持仓清单(代码/名称/申万行业/残差/该期实现收益)与策略净值 vs 等权全市场基准,渲染成
一个自包含 HTML(matplotlib 图内嵌 base64,不依赖外网),最新一期清单置顶供实盘参考。

环境：.venv312。用法：python qlib_workflow/momentum/run_jq_report.py [--start 20260101]
依赖：DuckDB(同 run_jq_residual);sw_member 表;matplotlib。
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
sys.path.insert(0, os.path.dirname(__file__))

import duckdb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.svm import SVR
from sklearn.preprocessing import StandardScaler

from cache_tushare import DUCKDB_PATH
import run_jq_residual as R

STEP, TOPK, C = 10, 10, 1.0
OUT = os.path.join(_ROOT, "qlib_workflow/momentum/jq_residual_report.html")


def _fig_b64(navs, bench_nav, dates):
    """画策略 vs 基准净值曲线,返回 base64 PNG。"""
    plt.rcParams["font.sans-serif"] = ["Arial Unicode MS", "PingFang SC", "Heiti SC"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.plot(dates, navs, label="SVR 残差策略", linewidth=2, color="#c0392b")
    ax.plot(dates, bench_nav, label="等权全市场(基准)", linewidth=1.5, color="#7f8c8d", linestyle="--")
    ax.axhline(1.0, color="#bbb", linewidth=0.8)
    ax.set_ylabel("净值(起点=1)")
    ax.legend(loc="upper left")
    ax.grid(alpha=0.3)
    fig.autofmt_xdate()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20260101")
    args = ap.parse_args()
    start_dash = f"{args.start[:4]}-{args.start[4:6]}-{args.start[6:8]}"

    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    _, db, px, bs, inc, meta, sw, st = R._load(con)
    cal = [pd.Timestamp(r[0]) for r in con.execute(
        "SELECT DISTINCT trade_date FROM daily WHERE trade_date>=? ORDER BY trade_date", [start_dash]).fetchall()]
    con.close()

    names = meta.set_index("ts_code")["name"].to_dict()
    meta = meta.set_index("ts_code")
    meta["list_date"] = pd.to_datetime(meta["list_date"], format="%Y%m%d", errors="coerce")
    meta["delist_date"] = pd.to_datetime(meta["delist_date"], format="%Y%m%d", errors="coerce")
    st_set = set(zip(st["ts_code"], st["trade_date"]))
    pxw = px.pivot_table(index="trade_date", values="adjclose", columns="ts_code")
    rebal = cal[::STEP]

    periods, held = [], set()
    for k in range(len(rebal) - 1):
        d, d2 = rebal[k], rebal[k + 1]
        if d not in pxw.index or d2 not in pxw.index:
            continue
        list_ok = R._live_universe(meta, d)
        ind_asof = R._sw_asof(sw, d)
        out = R._features(d, db, bs, inc, ind_asof, st_set, list_ok)
        if out is None:
            continue
        X, y = out
        Xs = StandardScaler().fit_transform(X.values)
        est = SVR(C=C, epsilon=0.1, kernel="rbf").fit(Xs, y.values)
        resid = pd.Series(y.values - est.predict(Xs), index=X.index).sort_values()
        pick = list(resid.index[:TOPK])
        fwd = pxw.loc[d2] / pxw.loc[d] - 1.0
        rets = fwd.reindex(pick)
        turn = len(set(pick) - held) / TOPK
        port_ret = rets.dropna().mean() - R.COST * turn * 2
        periods.append({"date": d, "next": d2, "pick": pick, "resid": resid,
                        "fwd": fwd, "ret": port_ret, "bench": fwd.mean()})
        held = set(pick)

    last = rebal[-1]
    if last not in pxw.index:
        last = pxw.index[pxw.index <= last].max()
    out = R._features(last, db, bs, inc, R._sw_asof(sw, last), st_set, R._live_universe(meta, last))
    cur_pick, cur_resid, cur_ind = [], None, R._sw_asof(sw, last)
    if out is not None:
        X, y = out
        Xs = StandardScaler().fit_transform(X.values)
        est = SVR(C=C, epsilon=0.1, kernel="rbf").fit(Xs, y.values)
        cur_resid = pd.Series(y.values - est.predict(Xs), index=X.index).sort_values()
        cur_pick = list(cur_resid.index[:TOPK])

    rets = np.array([p["ret"] for p in periods])
    bret = np.array([p["bench"] for p in periods])
    nav = np.cumprod(1 + rets)
    bnav = np.cumprod(1 + bret)
    dates = [p["next"] for p in periods]
    ppy = 252 / STEP

    def stat(r):
        n = np.cumprod(1 + r)
        ann = n[-1] ** (ppy / len(r)) - 1
        mdd = ((n - np.maximum.accumulate(n)) / np.maximum.accumulate(n)).min()
        sh = r.mean() / r.std() * np.sqrt(ppy) if r.std() > 0 else 0
        return n[-1] - 1, ann, mdd, sh, (r > 0).mean()

    st_total, st_ann, st_mdd, st_sh, st_win = stat(rets)
    b_total, b_ann, b_mdd, b_sh, b_win = stat(bret)
    img = _fig_b64(nav, bnav, dates)

    def ind_of(c, asof):
        return asof.get(c, "—")

    cur_rows = "".join(
        f"<tr><td>{i+1}</td><td>{c}</td><td>{names.get(c,'')}</td>"
        f"<td>{ind_of(c,cur_ind)}</td><td>{cur_resid[c]:.3f}</td></tr>"
        for i, c in enumerate(cur_pick)) if cur_pick else "<tr><td colspan=5>无数据</td></tr>"

    hist = ""
    for p in reversed(periods):
        rows = "".join(
            f"<tr><td>{c}</td><td>{names.get(c,'')}</td><td>{R._sw_asof(sw,p['date']).get(c,'—')}</td>"
            f"<td>{p['resid'][c]:.3f}</td>"
            f"<td class='{'pos' if (p['fwd'].get(c,0) or 0)>=0 else 'neg'}'>{(p['fwd'].get(c,float('nan')))*100:+.1f}%</td></tr>"
            for c in p["pick"])
        cls = "pos" if p["ret"] >= 0 else "neg"
        hist += (f"<details><summary>{p['date'].date()} → {p['next'].date()} &nbsp; "
                 f"本期收益 <b class='{cls}'>{p['ret']*100:+.1f}%</b> "
                 f"(基准 {p['bench']*100:+.1f}%)</summary>"
                 f"<table class='inner'><tr><th>代码</th><th>名称</th><th>申万一级</th>"
                 f"<th>残差</th><th>本期实现</th></tr>{rows}</table></details>")

    html = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>市值残差因子(SVR)报告</title><style>
body{{font-family:-apple-system,"PingFang SC",sans-serif;max-width:980px;margin:24px auto;padding:0 16px;color:#222}}
h1{{font-size:22px}} h2{{font-size:17px;margin-top:28px;border-left:4px solid #c0392b;padding-left:8px}}
table{{border-collapse:collapse;width:100%;margin:8px 0;font-size:14px}}
th,td{{border:1px solid #ddd;padding:6px 10px;text-align:center}}
th{{background:#f7f7f7}} .pos{{color:#c0392b}} .neg{{color:#27ae60}}
.cards{{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0}}
.card{{flex:1;min-width:120px;background:#fafafa;border:1px solid #eee;border-radius:8px;padding:12px;text-align:center}}
.card .v{{font-size:22px;font-weight:700}} .card .l{{font-size:12px;color:#888;margin-top:4px}}
details{{margin:6px 0;border:1px solid #eee;border-radius:6px;padding:6px 10px}}
summary{{cursor:pointer}} table.inner{{font-size:13px}}
.note{{background:#fff8e1;border:1px solid #ffe082;border-radius:6px;padding:10px 14px;font-size:13px;color:#6d4c41}}
</style></head><body>
<h1>市值残差因子(SVR)实盘视角报告</h1>
<p>区间 <b>{start_dash} ~ {dates[-1].date() if dates else '?'}</b> &nbsp;|&nbsp; 参数 SVR / 每{STEP}日调仓 / 持有{TOPK}只 / C={C} &nbsp;|&nbsp; 申万一级行业中性、PIT 财务</p>

<div class="cards">
<div class="card"><div class="v {'pos' if st_total>=0 else 'neg'}">{st_total*100:+.1f}%</div><div class="l">策略总收益</div></div>
<div class="card"><div class="v">{st_ann*100:.1f}%</div><div class="l">年化</div></div>
<div class="card"><div class="v neg">{st_mdd*100:.1f}%</div><div class="l">最大回撤</div></div>
<div class="card"><div class="v">{st_sh:.2f}</div><div class="l">夏普</div></div>
<div class="card"><div class="v">{st_win*100:.0f}%</div><div class="l">胜率</div></div>
<div class="card"><div class="v {'pos' if (st_total-b_total)>=0 else 'neg'}">{(st_total-b_total)*100:+.1f}%</div><div class="l">超额(对基准)</div></div>
</div>
<p style="font-size:13px;color:#888">基准(等权全市场):总收益 {b_total*100:+.1f}% / 年化 {b_ann*100:.1f}% / 回撤 {b_mdd*100:.1f}% / 夏普 {b_sh:.2f}</p>

<h2>净值曲线</h2>
<img src="data:image/png;base64,{img}" style="width:100%">

<h2>当前持仓清单(最新一期 {last.date() if hasattr(last,'date') else last},按残差升序=越靠前越低估)</h2>
<table><tr><th>#</th><th>代码</th><th>名称</th><th>申万一级</th><th>残差</th></tr>{cur_rows}</table>

<h2>历史每期选股与收益(点开展开)</h2>
{hist}

<div class="note"><b>⚠️ 使用须知</b>:残差因子=买相对基本面被低估的票,属<b>弱 alpha</b>。本报告未扣除涨跌停买不进、停牌、冲击成本,实际收益会打折。
区间样本短(2026年起仅数期),数字波动大,仅供直观感受,<b>不构成投资建议</b>。残差为该期横截面 SVR 拟合残差(真实对数市值−拟合值),越小越"便宜"。</div>
</body></html>"""

    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"报告已生成:{OUT}")
    print(f"区间 {start_dash}~{dates[-1].date() if dates else '?'} | {len(periods)}期 | "
          f"策略 {st_total*100:+.1f}%(年化{st_ann*100:.1f}%) vs 基准 {b_total*100:+.1f}%")


if __name__ == "__main__":
    main()
