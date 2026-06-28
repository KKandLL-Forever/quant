"""
run_swing_strategy.py — 完整波段策略实盘执行报告(最多持N只,默认3)

按"完整形态"从 --start 起逐日执行并产出自包含 HTML:
  入场:低/中位长期震荡(基底<30%)+ 放量突破40日新高(起爆点)
  持有:唐奇安20日跟踪离场(吃中段)
  闸门:上证 MA60 上行才允许入场;大盘转坏强制离场
  集中:最多同时持 HOLD 只(实盘约束),多信号抢仓时按放量强度优先,等权,空仓持币
PIT:信号用 t-1 数据,t 收盘执行。报告含净值曲线、每笔交易明细、当前持仓、汇总指标。

⚠️ 集中持仓单笔波动极大、样本少(2026仅数月),仅供直观感受,不构成投资建议。

环境：.venv312。用法：python swing/run_swing_strategy.py --start 2026-01-01 --hold 3
依赖：DuckDB(daily/daily_basic/adj_factor/index_daily/stock_st/stock_meta);matplotlib。
"""

import argparse
import base64
import io
import os
import sys

sys.path.insert(0, os.path.expanduser("~/AI/quart"))

import duckdb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from cache_tushare import DUCKDB_PATH

BASE_W, BASE_MAX, POS_MAX, VOL_K, DON_EXIT, COST = 40, 0.30, 0.6, 1.5, 20, 0.0006
OUT = os.path.expanduser("~/AI/quart/swing/strategy_report.html")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--hold", type=int, default=3)
    ap.add_argument("--n", type=int, default=1000, help="流动性候选前N")
    args = ap.parse_args()
    start = args.start

    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    sel_day = con.execute("SELECT MAX(trade_date) FROM daily_basic WHERE trade_date<?", [start]).fetchone()[0]
    liquid = [r[0] for r in con.execute("""
        SELECT ts_code FROM daily_basic WHERE trade_date=? AND ts_code NOT LIKE '%.BJ'
          AND ts_code NOT IN (SELECT ts_code FROM stock_st WHERE trade_date=?)
        ORDER BY circ_mv DESC LIMIT ?""", [sel_day, sel_day, args.n]).fetchall()]
    meta = con.execute("SELECT ts_code, name, list_date, delist_date FROM stock_meta").fetch_df()
    px = con.execute("""
        SELECT d.ts_code, d.trade_date, d.high*a.adj_factor h, d.low*a.adj_factor l,
               d.close*a.adj_factor c, d.vol v
        FROM daily d JOIN adj_factor a ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date
        WHERE d.trade_date>=? AND d.ts_code IN (SELECT UNNEST(?))""", ["2024-06-01", liquid]).fetch_df()
    idx = con.execute("SELECT trade_date, close FROM index_daily WHERE ts_code='000001.SH' ORDER BY trade_date").fetch_df()
    con.close()

    names = dict(zip(meta["ts_code"], meta["name"]))
    px["trade_date"] = pd.to_datetime(px["trade_date"])
    idx["trade_date"] = pd.to_datetime(idx["trade_date"])
    ima = idx["close"].rolling(60).mean()
    idx["healthy"] = (idx["close"] > ima) & (ima > ima.shift(10))
    regime = dict(zip(idx["trade_date"], idx["healthy"].fillna(False)))
    meta = meta.set_index("ts_code")
    meta["list_date"] = pd.to_datetime(meta["list_date"], format="%Y%m%d", errors="coerce")
    meta["delist_date"] = pd.to_datetime(meta["delist_date"], format="%Y%m%d", errors="coerce")
    sd = pd.Timestamp(sel_day)
    ok = set(meta.index[(meta["list_date"] <= sd - pd.Timedelta(days=365)) &
                        (meta["delist_date"].isna() | (meta["delist_date"] > pd.Timestamp.today() + pd.Timedelta(days=1)))])

    start_ts = pd.Timestamp(start)
    ret, en, ex, vr, price = {}, {}, {}, {}, {}
    for ts, g in px.groupby("ts_code"):
        if ts not in ok:
            continue
        g = g.sort_values("trade_date").set_index("trade_date")
        c, h, l, v = g["c"], g["h"], g["l"], g["v"]
        hi_w = h.rolling(BASE_W).max().shift(1)
        lo_w = l.rolling(BASE_W).min().shift(1)
        rng = (hi_w - lo_w) / lo_w
        lo_y, hi_y = c.rolling(250).min(), c.rolling(250).max()
        pos1y = (c - lo_y) / (hi_y - lo_y)
        vma = v.rolling(20).mean().shift(1)
        sig = (rng < BASE_MAX) & (pos1y < POS_MAX) & (c > hi_w) & (v > VOL_K * vma)
        donbreak = c < l.rolling(DON_EXIT).min().shift(1)
        reg = c.index.map(regime).fillna(False)
        regbad = ~pd.Series(reg, index=c.index)
        ret[ts] = c.pct_change()
        en[ts] = (sig & pd.Series(reg, index=c.index)).shift(1).fillna(False)
        ex[ts] = (donbreak | regbad).shift(1).fillna(False)
        vr[ts] = (v / vma).shift(1)
        price[ts] = c

    dates = sorted([d for d in px["trade_date"].unique() if pd.Timestamp(d) >= start_ts])
    dates = [pd.Timestamp(d) for d in dates]
    held = {}
    nav = 1.0
    navs, trades = [], []
    for t in dates:
        port_ret = 0.0
        for code in held:
            r = ret[code].get(t, 0.0)
            port_ret += (0.0 if pd.isna(r) else r) / args.hold
        nav *= (1 + port_ret)
        for code in list(held):
            if bool(ex[code].get(t, False)) or t not in price[code].index:
                ep = price[code].get(t, held[code]["ep"])
                tr = ep / held[code]["ep"] - 1 - 2 * COST
                nav *= (1 - 2 * COST / args.hold)
                reason = "大盘转坏" if not regime.get(t, False) else "唐奇安破位"
                trades.append({**held[code], "exit_d": t, "exit_p": ep, "ret": tr, "reason": reason})
                del held[code]
        free = args.hold - len(held)
        if free > 0 and regime.get(t, False):
            cands = [(vr[c0].get(t, 0), c0) for c0 in en
                     if bool(en[c0].get(t, False)) and c0 not in held and t in price[c0].index]
            cands.sort(reverse=True)
            for _, code in cands[:free]:
                held[code] = {"code": code, "entry_d": t, "ep": price[code].get(t)}
                nav *= (1 - 2 * COST / args.hold)
        navs.append(nav)

    navs = np.array(navs)
    peak = np.maximum.accumulate(navs)
    mdd = ((navs - peak) / peak).min()
    closed = [tr for tr in trades]
    win = np.mean([t["ret"] > 0 for t in closed]) if closed else 0

    plt.rcParams["font.sans-serif"] = ["Arial Unicode MS", "PingFang SC", "Heiti SC"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, ax = plt.subplots(figsize=(11, 4.5))
    ax.plot(dates, navs, color="#c0392b", linewidth=2, label="策略净值")
    ax.axhline(1.0, color="#bbb", lw=0.8)
    ax.set_ylabel("净值(起点1)")
    ax.grid(alpha=0.3); ax.legend(loc="upper left")
    fig.autofmt_xdate()
    buf = io.BytesIO(); fig.savefig(buf, format="png", dpi=110, bbox_inches="tight"); plt.close(fig)
    img = base64.b64encode(buf.getvalue()).decode()

    trows = ""
    for tr in sorted(closed, key=lambda x: x["entry_d"]):
        cls = "pos" if tr["ret"] >= 0 else "neg"
        trows += (f"<tr><td>{tr['code']}</td><td>{names.get(tr['code'],'')}</td>"
                  f"<td>{tr['entry_d'].date()}</td><td>{tr['exit_d'].date()}</td>"
                  f"<td class={cls}>{tr['ret']*100:+.1f}%</td><td>{tr['reason']}</td></tr>")
    orows = ""
    for code, p in held.items():
        cur = price[code].iloc[-1]
        ur = cur / p["ep"] - 1
        cls = "pos" if ur >= 0 else "neg"
        orows += (f"<tr><td>{code}</td><td>{names.get(code,'')}</td><td>{p['entry_d'].date()}</td>"
                  f"<td class={cls}>{ur*100:+.1f}%</td></tr>")
    orows = orows or "<tr><td colspan=4>当前空仓</td></tr>"

    html = f"""<!doctype html><html lang=zh><head><meta charset=utf-8><title>波段策略报告</title><style>
body{{font-family:-apple-system,"PingFang SC",sans-serif;max-width:980px;margin:24px auto;padding:0 16px;color:#222}}
h1{{font-size:21px}} h2{{font-size:16px;border-left:4px solid #c0392b;padding-left:8px;margin-top:24px}}
table{{border-collapse:collapse;width:100%;font-size:14px;margin:8px 0}} th,td{{border:1px solid #ddd;padding:6px 9px;text-align:center}}
th{{background:#f7f7f7}} .pos{{color:#c0392b}} .neg{{color:#27ae60}}
.cards{{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0}}
.card{{flex:1;min-width:120px;background:#fafafa;border:1px solid #eee;border-radius:8px;padding:12px;text-align:center}}
.card .v{{font-size:21px;font-weight:700}} .card .l{{font-size:12px;color:#888;margin-top:4px}}
.note{{background:#fff8e1;border:1px solid #ffe082;border-radius:6px;padding:10px 14px;font-size:13px;color:#6d4c41}}
</style></head><body>
<h1>完整波段策略执行报告</h1>
<p>区间 <b>{dates[0].date()}~{dates[-1].date()}</b> | 最多持 {args.hold} 只 | 基底突破入场 + 唐奇安离场 + 上证MA60闸门</p>
<div class=cards>
<div class=card><div class="v {'pos' if navs[-1]>=1 else 'neg'}">{(navs[-1]-1)*100:+.1f}%</div><div class=l>策略总收益</div></div>
<div class=card><div class="v neg">{mdd*100:.1f}%</div><div class=l>最大回撤</div></div>
<div class=card><div class=v>{len(closed)}</div><div class=l>已平仓笔数</div></div>
<div class=card><div class=v>{win*100:.0f}%</div><div class=l>胜率</div></div>
<div class=card><div class=v>{len(held)}</div><div class=l>当前持仓</div></div>
</div>
<h2>净值曲线</h2><img src="data:image/png;base64,{img}" style="width:100%">
<h2>当前持仓(浮动盈亏)</h2>
<table><tr><th>代码</th><th>名称</th><th>买入日</th><th>浮动盈亏</th></tr>{orows}</table>
<h2>已平仓交易明细</h2>
<table><tr><th>代码</th><th>名称</th><th>买入日</th><th>卖出日</th><th>收益</th><th>离场原因</th></tr>{trows or '<tr><td colspan=6>无</td></tr>'}</table>
<div class=note><b>⚠️</b> 最多持{args.hold}只的集中执行,单笔波动极大、{dates[0].year}样本仅数月,
净值受少数交易主导,<b>仅供直观感受,不构成投资建议</b>。未建模涨跌停跳空/滑点,实际成交更差。
离场"大盘转坏"=上证跌破MA60趋势;"唐奇安破位"=个股跌破20日低。</div>
</body></html>"""
    with open(OUT, "w") as f:
        f.write(html)
    print(f"报告:{OUT}")
    print(f"{dates[0].date()}~{dates[-1].date()} | 总收益 {(navs[-1]-1)*100:+.1f}% | 回撤 {mdd*100:.1f}% | "
          f"平仓 {len(closed)}笔 胜率 {win*100:.0f}% | 当前持仓 {len(held)}只")


if __name__ == "__main__":
    main()
