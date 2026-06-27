"""
run_ml_signals_2026.py — 模型 top10% 信号清单(2026-01-01 之后,不管仓位)

模型(LightGBM)用 2022-2025 事件训练,对 2026-01-01 之后的 N/W 突破信号打分,取预测概率
top10% 全部列出(不做仓位管理)。每条标:日期/代码/名称/形态/ML分/至今最大涨幅/状态
(已走出主升浪≥50% / 进行中 / 未达)。产出 HTML 清单。

环境：.venv312。用法：python swing/run_ml_signals_2026.py --start 20260101 --tier 10
  --start/--end 信号时间范围(YYYYMMDD);--tier 只显示 ML 评分前百分之几(5=top5%,100=全部)
依赖：DuckDB(daily/daily_basic/adj_factor/cyq_perf/moneyflow);lightgbm;复用 run_patterns。
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.expanduser("~/AI/quart/first10"))

import duckdb
import numpy as np
import pandas as pd
import lightgbm as lgb

from cache_tushare import DUCKDB_PATH
from run_patterns import _detect

THR, MW_GAIN, MW_DAYS = 0.09, 0.50, 60
FEATS = ["ptype", "brk", "pos1y", "basew", "dma20", "dma60", "atrp", "adx", "ret20", "ret60",
         "volr", "winrate", "cyqconc", "mfnet20", "pe", "pb", "lnmv"]
OUT = os.path.expanduser("~/AI/quart/swing/ml_signals_2026.html")


def _adx(h, l, c, n=14):
    up = h.diff(); dn = -l.diff()
    plus = np.where((up > dn) & (up > 0), up, 0.0); minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    pc = c.shift(1); tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / n, adjust=False).mean()
    pdi = 100 * pd.Series(plus, index=h.index).ewm(alpha=1 / n, adjust=False).mean() / atr
    mdi = 100 * pd.Series(minus, index=h.index).ewm(alpha=1 / n, adjust=False).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean(), atr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--thr", type=float, default=0.09)
    ap.add_argument("--n", type=int, default=800)
    ap.add_argument("--start", default="20260101", help="信号起始日 YYYYMMDD")
    ap.add_argument("--end", default=None, help="信号结束日 YYYYMMDD,默认到最新")
    ap.add_argument("--tier", type=int, default=10, help="只显示 ML 评分前百分之几(如 5=top5%%、10=top10%%、100=全部)")
    args = ap.parse_args()
    start_ts = pd.Timestamp(args.start)
    end_ts = pd.Timestamp(args.end) if args.end else None

    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    sel = con.execute("SELECT MAX(trade_date) FROM daily_basic").fetchone()[0]
    liquid = [r[0] for r in con.execute("""SELECT ts_code FROM daily_basic WHERE trade_date=? AND ts_code NOT LIKE '%.BJ'
        ORDER BY circ_mv DESC LIMIT ?""", [sel, args.n]).fetchall()]
    names = dict(con.execute("SELECT ts_code,name FROM stock_meta").fetchall())
    px = con.execute("""SELECT d.ts_code,d.trade_date,d.high*a.adj_factor h,d.low*a.adj_factor l,
        d.close*a.adj_factor c,d.vol v FROM daily d JOIN adj_factor a ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date
        WHERE d.trade_date>=? AND d.ts_code IN (SELECT UNNEST(?))""", ["2021-01-01", liquid]).fetch_df()
    db = con.execute("""SELECT ts_code,trade_date,pe_ttm,pb,circ_mv FROM daily_basic
        WHERE trade_date>=? AND ts_code IN (SELECT UNNEST(?))""", ["2021-01-01", liquid]).fetch_df()
    cyq = con.execute("""SELECT ts_code,trade_date,winner_rate,cost_15pct,cost_50pct,cost_85pct FROM cyq_perf
        WHERE trade_date>=? AND ts_code IN (SELECT UNNEST(?))""", ["2021-01-01", liquid]).fetch_df()
    mf = con.execute("""SELECT ts_code,trade_date,
        buy_lg_amount+buy_elg_amount-sell_lg_amount-sell_elg_amount AS net_lg,
        buy_sm_amount+buy_md_amount+buy_lg_amount+buy_elg_amount+sell_sm_amount+sell_md_amount+sell_lg_amount+sell_elg_amount AS tot
        FROM moneyflow WHERE trade_date>=? AND ts_code IN (SELECT UNNEST(?))""", ["2021-01-01", liquid]).fetch_df()
    con.close()

    for d in (px, db, cyq, mf):
        d["trade_date"] = pd.to_datetime(d["trade_date"])
    db = db.set_index(["ts_code", "trade_date"])
    cyq["cyqconc"] = (cyq["cost_85pct"] - cyq["cost_15pct"]) / cyq["cost_50pct"]
    cyq = cyq.set_index(["ts_code", "trade_date"]); mf = mf.set_index(["ts_code", "trade_date"])

    rows = []
    for ts, g in px.groupby("ts_code"):
        g = g.sort_values("trade_date").reset_index(drop=True)
        c, h, l, v = g["c"], g["h"], g["l"], g["v"]
        cc = c.to_numpy(); gd = g["trade_date"].to_numpy()
        if len(cc) < 180:
            continue
        ma20, ma60 = c.rolling(20).mean(), c.rolling(60).mean()
        adx, atr = _adx(h, l, c)
        pos1y = (c - c.rolling(250).min()) / (c.rolling(250).max() - c.rolling(250).min())
        basew = (h.rolling(40).max().shift(1) - l.rolling(40).min().shift(1)) / l.rolling(40).min().shift(1)
        vma = v.rolling(20).mean()
        ret20 = c / c.shift(20) - 1; ret60 = c / c.shift(60) - 1
        try:
            mfg = mf.loc[ts].reindex(g["trade_date"])
            mf_net20 = (mfg["net_lg"].rolling(20).sum() / mfg["tot"].rolling(20).sum().abs()).values
        except KeyError:
            mf_net20 = np.full(len(cc), np.nan)
        for typ, bo, pvs in _detect(cc, args.thr, 30):
            d = pd.Timestamp(gd[bo])
            if d.year < 2022:
                continue
            endi = min(bo + MW_DAYS, len(cc) - 1)
            maxfwd = cc[bo + 1:endi + 1].max() / cc[bo] - 1 if endi > bo else np.nan
            label = int(maxfwd >= MW_GAIN) if (bo + MW_DAYS < len(cc)) else -1
            seg = cc[bo:endi + 1]
            peak_off = int(seg.argmax())
            launch = int(seg[:peak_off + 1].argmin()) if peak_off > 0 else None
            try:
                dbr = db.loc[(ts, d)]; cyr = cyq.loc[(ts, d)]
            except KeyError:
                dbr = cyr = None
            rows.append({
                "date": d, "ts": ts, "year": d.year, "label": label, "maxfwd": maxfwd,
                "launch": launch, "done": bo + MW_DAYS < len(cc), "typ": typ,
                "ptype": 0 if typ == "N字型" else 1, "brk": cc[bo] / cc[pvs[1]] - 1,
                "pos1y": pos1y.iloc[bo], "basew": basew.iloc[bo],
                "dma20": cc[bo] / ma20.iloc[bo] - 1, "dma60": cc[bo] / ma60.iloc[bo] - 1,
                "atrp": atr.iloc[bo] / cc[bo], "adx": adx.iloc[bo],
                "ret20": ret20.iloc[bo], "ret60": ret60.iloc[bo],
                "volr": v.iloc[bo] / vma.iloc[bo] if vma.iloc[bo] else np.nan,
                "winrate": cyr["winner_rate"] if cyr is not None else np.nan,
                "cyqconc": cyr["cyqconc"] if cyr is not None else np.nan,
                "mfnet20": mf_net20[bo],
                "pe": dbr["pe_ttm"] if dbr is not None else np.nan,
                "pb": dbr["pb"] if dbr is not None else np.nan,
                "lnmv": np.log(dbr["circ_mv"]) if dbr is not None and dbr["circ_mv"] > 0 else np.nan,
            })
    df = pd.DataFrame(rows)
    tr = df[(df["date"] < start_ts) & (df["label"] >= 0)]
    te = df[df["date"] >= start_ts].copy()
    if end_ts is not None:
        te = te[te["date"] <= end_ts].copy()
    m = lgb.LGBMClassifier(n_estimators=200, learning_rate=0.03, num_leaves=31, min_child_samples=40,
                           subsample=0.8, colsample_bytree=0.8, verbosity=-1)
    m.fit(tr[FEATS], tr["label"])
    te["score"] = m.predict_proba(te[FEATS])[:, 1]
    pct = te["score"].rank(pct=True)
    te["tier"] = np.where(pct >= 0.95, "top5", np.where(pct >= 0.90, "top10",
                 np.where(pct >= 0.80, "top20", np.where(pct >= 0.70, "top30", "其他"))))
    top = te[pct >= 1 - args.tier / 100].sort_values("score", ascending=False)

    done = top[top["done"]]
    hit = (done["label"] == 1).mean() if len(done) else float("nan")
    print(f"信号 {args.start}~{args.end or '今'} 共{len(te)}条 | top{args.tier}% = {len(top)}条 | "
          f"已满60日{len(done)}条 走出主升浪 {hit*100:.0f}%")

    import json
    data = []
    for _, r in top.iterrows():
        st = ("已走出主升浪" if r["label"] == 1 else "未达") if r["done"] else "进行中"
        code = r["ts"][:3]
        board = "科创" if r["ts"].startswith(("688", "689")) else "创业" if code in ("300", "301") else "主板"
        data.append({
            "date": str(r["date"].date()), "ts": r["ts"], "name": names.get(r["ts"], ""),
            "board": board,
            "tier": r["tier"], "typ": r["typ"], "score": round(float(r["score"]), 3),
            "maxfwd": None if pd.isna(r["maxfwd"]) else round(float(r["maxfwd"]) * 100, 0),
            "launch": None if pd.isna(r["launch"]) else int(r["launch"]),
            "status": st,
        })
    djson = json.dumps(data, ensure_ascii=False)
    html = f"""<!doctype html><html lang=zh><head><meta charset=utf-8><title>ML top信号 2026</title>
<link rel=stylesheet href="https://unpkg.com/antd@4.24.15/dist/antd.min.css">
<style>body{{font-family:-apple-system,"PingFang SC",sans-serif;max-width:1000px;margin:22px auto;padding:0 14px;color:#222}}
h1{{font-size:20px}} .pos{{color:#c0392b}} .neg{{color:#27ae60}}
.note{{background:#fff8e1;border:1px solid #ffe082;border-radius:6px;padding:10px;font-size:13px;color:#6d4c41;margin:10px 0}}</style>
</head><body>
<h1>ML 主升浪信号清单 — {args.start} ~ {args.end or '今'}(top{args.tier}%)</h1>
<p>模型用 {args.start} 之前数据训练,打分该区间信号 | 共 {len(te)} 条,列出 top{args.tier}% = {len(top)} 条 |
已满60日的 {len(done)} 条中走出主升浪(≥50%) {hit*100:.0f}% | 档位列: top5/top10/top20/top30</p>
<div class=note><b>⚠️</b> "至今最大涨幅"=突破日到现在(或满60日)的最高浮盈;"启动用时"=突破后到主升浪启动点(回踩最低点)的交易日数;
"进行中"=不满60日结果未定。点表头可排序。模型严格用2026前数据训练,无未来函数。</div>
<div id=root></div>
<script src="https://unpkg.com/react@17/umd/react.production.min.js"></script>
<script src="https://unpkg.com/react-dom@17/umd/react-dom.production.min.js"></script>
<script src="https://unpkg.com/moment@2.29.4/min/moment.min.js"></script>
<script src="https://unpkg.com/antd@4.24.15/dist/antd.min.js"></script>
<style>.cards{{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0}}
.card{{flex:1;min-width:150px;background:#fafafa;border:1px solid #eee;border-radius:8px;padding:12px}}
.card .v{{font-size:22px;font-weight:700}} .card .l{{font-size:13px;color:#444;margin-top:2px}}
.card .calc{{font-size:11px;color:#999;margin-top:4px;line-height:1.4}}</style>
<script>
var DATA={djson};
var e=React.createElement;
var cols=[
 {{title:'突破日',dataIndex:'date',defaultSortOrder:'descend',sorter:function(a,b){{return a.date<b.date?-1:1;}}}},
 {{title:'板块',dataIndex:'board',filters:[{{text:'主板',value:'主板'}},{{text:'科创',value:'科创'}},{{text:'创业',value:'创业'}}],onFilter:function(v,r){{return r.board===v;}}}},
 {{title:'档位',dataIndex:'tier',filters:[{{text:'top5',value:'top5'}},{{text:'top10',value:'top10'}},{{text:'top20',value:'top20'}},{{text:'top30',value:'top30'}}],onFilter:function(v,r){{return r.tier===v;}}}},
 {{title:'代码',dataIndex:'ts'}},
 {{title:'名称',dataIndex:'name'}},
 {{title:'形态',dataIndex:'typ',filters:[{{text:'N字型',value:'N字型'}},{{text:'W型',value:'W型'}}],onFilter:function(v,r){{return r.typ===v;}}}},
 {{title:'ML分',dataIndex:'score',defaultSortOrder:'descend',sorter:function(a,b){{return a.score-b.score;}}}},
 {{title:'至今最大涨幅',dataIndex:'maxfwd',sorter:function(a,b){{return (a.maxfwd||-999)-(b.maxfwd||-999);}},
   render:function(v){{return v==null?'—':e('span',{{className:v>=0?'pos':'neg'}},(v>=0?'+':'')+v+'%');}}}},
 {{title:'启动用时(交易日)',dataIndex:'launch',sorter:function(a,b){{return (a.launch==null?9999:a.launch)-(b.launch==null?9999:b.launch);}},
   render:function(v){{return v==null?'—':v+'天';}}}},
 {{title:'状态',dataIndex:'status',filters:[{{text:'已走出主升浪',value:'已走出主升浪'}},{{text:'未达',value:'未达'}},{{text:'进行中',value:'进行中'}}],onFilter:function(v,r){{return r.status===v;}}}}
];
function card(v,l,calc){{return e('div',{{className:'card'}},e('div',{{className:'v'}},v),e('div',{{className:'l'}},l),e('div',{{className:'calc'}},calc));}}
function App(){{
 var s=React.useState(true),showKc=s[0],setKc=s[1];
 var s2=React.useState(true),showCy=s2[0],setCy=s2[1];
 var rows=DATA.filter(function(r){{return (showKc||r.board!=='科创')&&(showCy||r.board!=='创业');}});
 var done=rows.filter(function(r){{return r.status!=='进行中';}});
 var hit=done.filter(function(r){{return r.status==='已走出主升浪';}});
 var succ=done.length?(hit.length/done.length*100).toFixed(0)+'%':'—';
 var fwds=rows.filter(function(r){{return r.maxfwd!=null;}}).map(function(r){{return r.maxfwd;}});
 var avgfwd=fwds.length?(fwds.reduce(function(a,b){{return a+b;}},0)/fwds.length).toFixed(1)+'%':'—';
 var lc=hit.filter(function(r){{return r.launch!=null;}}).map(function(r){{return r.launch;}});
 var avglc=lc.length?(lc.reduce(function(a,b){{return a+b;}},0)/lc.length).toFixed(0)+'天':'—';
 var ongoing=rows.filter(function(r){{return r.status==='进行中';}}).length;
 var udays={{}};rows.forEach(function(r){{udays[r.date]=1;}});var nd=Object.keys(udays).length;
 var perday=nd?(rows.length/nd).toFixed(1):'—';
 return e('div',null,
  e('div',{{style:{{margin:'8px 0'}}}},
    e(antd.Checkbox,{{checked:showKc,onChange:function(ev){{setKc(ev.target.checked);}}}},'显示科创板(688/689)'),
    e('span',{{style:{{marginLeft:16}}}}),
    e(antd.Checkbox,{{checked:showCy,onChange:function(ev){{setCy(ev.target.checked);}}}},'显示创业板(300/301)')),
  e('div',{{className:'cards'}},
    card(rows.length,'信号数','当前筛选下的信号条数'),
    card(perday,'平均每日信号','信号数 ÷ 区间出现信号的不同交易日数('+rows.length+'/'+nd+')'),
    card(succ,'成功率','已满60日的信号中,走出主升浪(≥50%)的占比 = 命中数 ÷ 已满60日数('+hit.length+'/'+done.length+')'),
    card(avgfwd,'平均最大涨幅','所有信号突破后至今(或满60日)最高浮盈的均值;非实际买卖收益'),
    card(avglc,'平均启动用时','走出主升浪的信号,从突破日到启动点(回踩最低)的交易日均值'),
    card(ongoing,'进行中','突破不满60交易日、结果未定的条数')),
  e(antd.Table,{{columns:cols,dataSource:rows,rowKey:function(r){{return r.ts+r.date;}},size:'small',pagination:{{pageSize:30}}}})
 );
}}
ReactDOM.render(e(App),document.getElementById('root'));
</script>
</body></html>"""
    with open(OUT, "w") as f:
        f.write(html)
    print(f"报告:{OUT}")


if __name__ == "__main__":
    main()
