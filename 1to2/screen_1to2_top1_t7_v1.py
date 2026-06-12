"""
screen_1to2_top1_t7_v1.py — 首板→2板模型(v1·含集合竞价) 分位档历史选股 + T+7 收益统计

模型用 xgb_1lb_2lb_v1.pkl、特征用 ml_features_1to2_v1（35 个，含 4 技术面因子）。

用法：
  python 1to2/screen_1to2_top1_t7_v1.py                     # 默认 Top 1%
  python 1to2/screen_1to2_top1_t7_v1.py --tier 5            # Top 5%
  python 1to2/screen_1to2_top1_t7_v1.py --tier 10 --start 20250101
  python 1to2/screen_1to2_top1_t7_v1.py --tier 10 --tier-top 5  # 只取 Top10%~Top5% 之间
  python 1to2/screen_1to2_top1_t7_v1.py --proba 0.60 --proba-hi 0.66  # 手动区间切点

口径：
  信号日 = 首板日（模型于首板日盘后打分，预测次日能否晋级 2板）
  T = 信号前一交易日；T+7 = 首板日 + 6 个交易日
  收益口径（均以 首板次日开盘为买点）：
    ret_real（主口径·智能止盈）：
      止步首板(未晋级) → 次日(首板+2)开盘卖；连板 → 持有到断板当日开盘价卖出
      （默认对续板有完美预判，连板就一直拿；断板那天在开盘价离场）
    ret_t3_buy / ret_from_buy（参考·固定持有）：close(首板+2 / 首板+6) / 买点 - 1
  顶部诊断（资金曲线/回撤/去头部）均基于 ret_real。
  筛选：proba >= 所选分位档切点（从 pkl tiers 自动读取，--tier 选 1/5/10/20）

产出：
  1to2/html_output/screen_1to2_top{tier}_t7_v1.html（自动打开）
"""

import argparse
import json
import os
import pickle
import sys
import webbrowser
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

import duckdb as _duckdb
from db_loader import _ENV

MODEL_PATH = os.path.join(os.path.dirname(__file__), "model", "xgb_1lb_2lb_v1.pkl")
DUCK_PATH  = _ENV.get("LOCAL_DUCKDB_PATH", "stock_data_tushare.duckdb")
OUT_HTML   = os.path.join(os.path.dirname(__file__), "html_output", "screen_1to2_top1_t7_v1.html")

FALLBACK_TIERS = [
    {"q": 0.01, "label": "Top 1%",  "proba": 0.7112},
    {"q": 0.05, "label": "Top 5%",  "proba": 0.6624},
    {"q": 0.10, "label": "Top 10%", "proba": 0.6023},
    {"q": 0.20, "label": "Top 20%", "proba": 0.5138},
]
TIER_INDEX = {1: 0, 5: 1, 10: 2, 20: 3}


def _load_model():
    """加载 首板→2板模型，并返回 pkl 内的分位档列表（无则回退 FALLBACK_TIERS）。"""
    with open(MODEL_PATH, "rb") as f:
        b = pickle.load(f)
    tiers = b.get("tiers") or FALLBACK_TIERS
    return b["model"], b["feature_cols"], tiers


def _attach_prices(con, signal_df: pd.DataFrame) -> pd.DataFrame:
    """给 首板信号附加 信号前日收盘(T)、首板次日开盘(买点)、T+7 收盘 及对应日期。"""
    con.register("sig_raw", signal_df[["ts_code", "trade_date"]])
    con.execute("""
        CREATE OR REPLACE TEMP VIEW s AS
        SELECT ts_code,
               CAST(strptime(trade_date, '%Y%m%d') AS DATE) AS d2_date,
               trade_date AS d2_str
        FROM sig_raw
    """)
    return con.execute("""
        WITH d AS (
            SELECT ts_code, trade_date, open, close,
                   LAG(close, 1)  OVER w AS t_close_1lb,
                   LAG(trade_date, 1) OVER w AS t_date_1lb,
                   LEAD(open, 1)  OVER w AS buy_open,
                   LEAD(trade_date, 1) OVER w AS buy_date,
                   LEAD(close, 1) OVER w AS buy_close,
                   LEAD(close, 2) OVER w AS t3_close,
                   LEAD(trade_date, 2) OVER w AS t3_date,
                   LEAD(close, 3) OVER w AS t4_close,
                   LEAD(close, 4) OVER w AS t5_close,
                   LEAD(close, 5) OVER w AS t6_close,
                   LEAD(close, 6) OVER w AS t7_close,
                   LEAD(trade_date, 6) OVER w AS t7_date
            FROM daily
            WINDOW w AS (PARTITION BY ts_code ORDER BY trade_date)
        )
        SELECT s.ts_code, s.d2_str AS trade_date,
               d.close AS d2_close,
               d.t_close_1lb,
               strftime(d.t_date_1lb, '%Y%m%d') AS t_date_1lb,
               d.buy_open, d.buy_close,
               strftime(d.buy_date, '%Y%m%d') AS buy_date,
               d.t3_close,
               strftime(d.t3_date, '%Y%m%d') AS t3_date,
               d.t4_close, d.t5_close, d.t6_close,
               d.t7_close,
               strftime(d.t7_date, '%Y%m%d') AS t7_date
        FROM s JOIN d ON d.ts_code = s.ts_code AND d.trade_date = s.d2_date
    """).df()


def _attach_boards(con, signal_df: pd.DataFrame) -> pd.DataFrame:
    """给每个 首板信号附加其所在连板序列的最终高度（如 4 = 走到 4连板后断板）。"""
    con.register("sig_b", signal_df[["ts_code", "trade_date"]])
    return con.execute("""
        WITH flagged AS (
            SELECT ts_code, trade_date, limit_times,
                   CASE WHEN limit_times = LAG(limit_times) OVER w + 1
                        THEN 0 ELSE 1 END AS brk
            FROM limit_list_d
            WHERE limit_type = 'U'
            WINDOW w AS (PARTITION BY ts_code ORDER BY trade_date)
        ),
        grp AS (
            SELECT ts_code, trade_date, limit_times,
                   SUM(brk) OVER (PARTITION BY ts_code ORDER BY trade_date) AS gid
            FROM flagged
        ),
        maxg AS (
            SELECT ts_code, gid, MAX(limit_times) AS boards
            FROM grp GROUP BY ts_code, gid
        ),
        d2 AS (
            SELECT g.ts_code, g.trade_date, m.boards
            FROM grp g JOIN maxg m ON g.ts_code = m.ts_code AND g.gid = m.gid
            WHERE g.limit_times = 1
        )
        SELECT s.ts_code, s.trade_date AS trade_date, d2.boards
        FROM sig_b s
        JOIN d2 ON d2.ts_code = s.ts_code
               AND d2.trade_date = CAST(strptime(s.trade_date, '%Y%m%d') AS DATE)
    """).df()


def _attach_realized(con, signal_df: pd.DataFrame) -> pd.DataFrame:
    """按智能止盈口径算实际卖价：未晋级(止步首板)→次日开盘卖；连板→持有到断板当日开盘卖。"""
    con.register("sig_r", signal_df[["ts_code", "trade_date", "boards"]])
    return con.execute("""
        WITH sb AS (
            SELECT ts_code, CAST(strptime(trade_date, '%Y%m%d') AS DATE) AS d2,
                   trade_date AS d2_str, boards
            FROM sig_r WHERE boards IS NOT NULL
        ),
        fut AS (
            SELECT sb.ts_code, sb.d2_str, sb.boards, d.open, d.close,
                   ROW_NUMBER() OVER (PARTITION BY sb.ts_code, sb.d2
                                      ORDER BY d.trade_date) - 1 AS off
            FROM sb JOIN daily d
              ON d.ts_code = sb.ts_code AND d.trade_date >= sb.d2
            QUALIFY off <= 30
        ),
        agg AS (
            SELECT ts_code, d2_str AS trade_date, boards,
                   MAX(CASE WHEN off = 1 THEN open END) AS buy_open,
                   MAX(CASE WHEN off = 2 THEN open END) AS sell_loser,
                   MAX(CASE WHEN off = boards THEN open END) AS sell_winner
            FROM fut GROUP BY ts_code, d2_str, boards
        )
        SELECT ts_code, trade_date,
               CASE WHEN boards <= 1 THEN sell_loser ELSE sell_winner END AS sell_real
        FROM agg
    """).df()


def _get_limitdown_set(con):
    """取全部跌停记录集合 {(ts_code, 'YYYYMMDD')}，用于判断某日个股是否跌停。"""
    rows = con.execute("""
        SELECT ts_code, strftime(trade_date, '%Y%m%d')
        FROM limit_list_d WHERE limit_type = 'D'
    """).fetchall()
    return set(rows)


def _get_names(con, ts_codes):
    """批量取 ts_code → name。"""
    if not ts_codes:
        return {}
    ph = ",".join(["?"] * len(ts_codes))
    rows = con.execute(
        f"SELECT ts_code, name FROM stock_meta WHERE ts_code IN ({ph})", ts_codes
    ).fetchall()
    return dict(rows)


def _render_html(rows, start, proba_thr, tier_label) -> str:
    """渲染选股 + T+7 收益 HTML 报告（antd Table 组件）。"""
    n = len(rows)

    def _stat(col):
        a = np.array([r[col] for r in rows if r[col] is not None]) if n else np.array([])
        if len(a) == 0:
            return "-", "-", "-"
        return (f"{a.mean()*100:+.2f}%", f"{np.median(a)*100:+.2f}%",
                f"{(a > 0).mean()*100:.1f}%")

    horizons = [("T+3", "ret_t3_buy"), ("T+4", "ret_t4_buy"), ("T+5", "ret_t5_buy"),
                ("T+6", "ret_t6_buy"), ("T+7", "ret_from_buy")]
    summary = [[label] + list(_stat(col)) for label, col in horizons]

    heights = [r["boards"] for r in rows if r.get("boards") is not None]
    hb = len(heights)
    boards_dist = []
    if hb:
        for h in range(2, max(heights) + 1):
            c = heights.count(h)
            boards_dist.append([f"{h}连板", str(c), f"{c / hb * 100:.1f}%"])

    from collections import Counter
    day_counts = Counter(r["trade_date"] for r in rows)
    total_days = len(day_counts)
    cnt_freq = Counter(day_counts.values())
    daily_dist = [[str(k), str(cnt_freq[k]), f"{cnt_freq[k] / total_days * 100:.1f}%"]
                  for k in sorted(cnt_freq)] if total_days else []
    if total_days:
        cvals = list(day_counts.values())
        daily_stats = [["交易日数", str(total_days)],
                       ["信号总数", str(len(rows))],
                       ["日均信号", f"{np.mean(cvals):.2f}"],
                       ["日中位", str(int(np.median(cvals)))],
                       ["单日最多", str(max(cvals))]]
    else:
        daily_stats = []

    def _curve(by_day):
        eq, curve, means = 0.0, [0.0], []
        for d in sorted(by_day):
            vals = [v for v in by_day[d] if v is not None]
            if not vals:
                continue
            m = sum(vals) / len(vals)
            means.append(m)
            eq += m
            curve.append(eq)
        peak, mdd = 0.0, 0.0
        for x in curve:
            peak = max(peak, x)
            mdd = min(mdd, x - peak)
        stats = [["累计收益(等权·不复利)", f"{curve[-1] * 100:+.1f}%"],
                 ["日均收益(每个信号日)", f"{(np.mean(means) if means else 0) * 100:+.2f}%"],
                 ["最大回撤", f"{mdd * 100:.1f}个点"],
                 ["统计天数", str(len(curve) - 1)]]
        return curve, stats

    by_day = {}
    for r in rows:
        if r["ret_real"] is not None:
            by_day.setdefault(r["trade_date"], []).append(r["ret_real"])
    curve, equity_stats = _curve(by_day)

    from collections import defaultdict
    day_rows = defaultdict(list)
    for r in rows:
        if r.get("boards") in (13, 14):
            continue
        day_rows[r["trade_date"]].append(r)
    by_day2 = {}
    for d, rs in day_rows.items():
        rs = sorted(rs, key=lambda x: (x["proba"] if x["proba"] is not None else 0),
                    reverse=True)
        if len(rs) > 4:
            rs = rs[:4]
        by_day2[d] = [r["ret_real"] for r in rs]
    curve2, equity_stats2 = _curve(by_day2)
    year_curves = {}
    for yr in ("2024", "2025", "2026"):
        year_curves[yr] = _curve({d: v for d, v in by_day2.items() if d[:4] == yr})

    year_sig = Counter(r["trade_date"][:4] for r in rows)
    year_dist = [[y, str(year_sig[y])] for y in sorted(year_sig)]

    ym = Counter((r["trade_date"][:4], r["trade_date"][4:6]) for r in rows)
    years_sorted = sorted(year_sig)
    month_head = ["月份"] + years_sorted
    month_matrix = []
    for mm in range(1, 13):
        m = f"{mm:02d}"
        row = [f"{mm}月"] + [str(ym.get((y, m), 0)) for y in years_sorted]
        month_matrix.append(row)
    month_matrix.append(["合计"] + [str(year_sig[y]) for y in years_sorted])

    all_ret = sorted((r["ret_real"] for r in rows if r["ret_real"] is not None),
                     reverse=True)
    nret = len(all_ret)
    robust = []
    for k in (0, 1, 3, 5, 10):
        if k < nret:
            rest = all_ret[k:]
            m = sum(rest) / len(rest)
            win = sum(1 for x in rest if x > 0) / len(rest)
            label = "全部" if k == 0 else f"去掉Top{k}"
            robust.append([label, str(len(rest)), f"{m * 100:+.2f}%", f"{win * 100:.1f}%"])

    date_counts = Counter(r["trade_date"] for r in rows)
    palette = ["#fff4e6", "#e6f4ff", "#f6ffed", "#fff0f6", "#f3f0ff", "#fffbe6"]
    grp_color, ci = {}, 0
    for d in sorted(date_counts):
        if date_counts[d] >= 2:
            grp_color[d] = palette[ci % len(palette)]
            ci += 1

    data = []
    for i, r in enumerate(rows, 1):
        data.append({
            "key": i, "idx": i,
            "ts_code": r["ts_code"], "name": r["name"] or "",
            "t_date_1lb": r["t_date_1lb"] or "-",
            "grp_color": grp_color.get(r["trade_date"]),
            "boards": r["boards"], "proba": r["proba"],
            "ret_real": r["ret_real"], "next_chg": r["next_chg"],
            "next_dn": (None if pd.isna(r.get("next_dn")) else bool(r["next_dn"])),
            "ret_t3_buy": r["ret_t3_buy"], "ret_t4_buy": r["ret_t4_buy"],
            "ret_t5_buy": r["ret_t5_buy"], "ret_t6_buy": r["ret_t6_buy"],
            "ret_from_buy": r["ret_from_buy"],
        })

    def _sign_color(s):
        s = s.strip()
        return "#c00" if s.startswith("+") else ("#080" if s.startswith("-") else "#222")

    ret_all = robust[0][2] if robust else "-"
    win_all = robust[0][3] if robust else "-"
    kpis = [["样本数", str(n), "#222"],
            ["单笔均收益(实际)", ret_all, _sign_color(ret_all)],
            ["胜率(实际)", win_all, "#222"],
            ["累计收益(等权)", equity_stats[0][1], _sign_color(equity_stats[0][1])],
            ["最大回撤", equity_stats[2][1], "#080"]]

    payload = {
        "data": data, "start": start, "proba_thr": round(proba_thr, 4),
        "tier_label": tier_label, "n": n, "kpis": kpis,
        "now": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "summary": summary, "boards_dist": boards_dist, "boards_n": hb,
        "daily_dist": daily_dist, "daily_stats": daily_stats,
        "equity_curve": [round(x, 4) for x in curve], "equity_stats": equity_stats,
        "equity_curve2": [round(x, 4) for x in curve2], "equity_stats2": equity_stats2,
        "year_dist": year_dist,
        "month_head": month_head, "month_matrix": month_matrix,
        "year_curves": {y: {"curve": [round(x, 4) for x in c], "stats": s}
                        for y, (c, s) in year_curves.items()},
        "robust": robust,
    }
    payload_json = json.dumps(payload, ensure_ascii=False)

    return r"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>首板→2板 v1·含竞价 选股 T+7 收益</title>
<script src="https://cdn.staticfile.org/react/18.2.0/umd/react.production.min.js"></script>
<script src="https://cdn.staticfile.org/react-dom/18.2.0/umd/react-dom.production.min.js"></script>
<script src="https://cdn.staticfile.org/dayjs/1.11.10/dayjs.min.js"></script>
<script src="https://cdn.staticfile.org/antd/5.21.6/antd.min.js"></script>
<link rel="stylesheet" href="https://cdn.staticfile.org/antd/5.21.6/reset.min.css">
<style>body{font-family:-apple-system,"Microsoft YaHei",sans-serif;margin:24px auto;max-width:1600px;
color:#222;padding:0 16px;background:#f4f6f8}h1{font-size:21px;margin:0 0 6px}
.meta{color:#666;font-size:13px;margin-bottom:14px}
table.sum{border-collapse:collapse;margin:0;font-size:13px}
table.sum th,table.sum td{border:1px solid #e3e6ea;padding:7px 16px;text-align:right}
table.sum th{background:#f6f8fa;color:#666;font-weight:600;text-align:center}
table.sum td:first-child,table.sum th:first-child{text-align:center;font-weight:700;color:#333;background:#fafbfc}
table.sum td{font-weight:600;font-variant-numeric:tabular-nums}
.kpi{display:flex;gap:14px;flex-wrap:wrap;margin:0 0 8px}
.kpi .box{flex:1;min-width:140px;background:#fff;border:1px solid #e8ebee;border-radius:10px;
padding:14px 18px;box-shadow:0 1px 3px rgba(0,0,0,0.04)}
.kpi .lbl{font-size:12px;color:#999;margin-bottom:7px}
.kpi .val{font-size:23px;font-weight:800;font-variant-numeric:tabular-nums}
.sec-title{font-size:16px;font-weight:700;color:#222;margin:24px 0 12px;padding-left:10px;border-left:4px solid #c00}
.grid{display:flex;gap:18px;flex-wrap:wrap;align-items:stretch}
.grid4>.card{flex:1 1 0;min-width:0}
.grid4>.card .desc{min-height:58px;max-width:none}
.card{background:#fff;border:1px solid #e8ebee;border-radius:10px;padding:14px 16px;
box-shadow:0 1px 3px rgba(0,0,0,0.04)}
.card h3{font-size:13px;font-weight:700;color:#444;margin:0 0 10px}
.card .desc{font-size:12px;color:#999;line-height:1.6;margin:-2px 0 10px;max-width:380px}
.card .row2{display:flex;flex-direction:column;gap:10px}</style></head>
<body><div id="root"></div>
<script>
var P = __PAYLOAD__;
var e = React.createElement;
var Table = antd.Table;
function num(x){return (x===null||x===undefined)?"-":Number(x).toFixed(2);}
function retCell(x){
  if(x===null||x===undefined) return "-";
  var pct = (x*100>=0?"+":"")+(x*100).toFixed(2)+"%";
  return e("span",{style:{color:x>0?"#c00":"#080",fontWeight:700}},pct);
}
function sumTable(head,rowsd){
  return e("table",{className:"sum"},
    e("thead",null,e("tr",null,head.map(function(h,i){return e("th",{key:i},h);}))),
    e("tbody",null,rowsd.map(function(row,i){
      return e("tr",{key:i},row.map(function(c,j){return e("td",{key:j},c);}));})));
}
function card(title,desc,body){
  var kids=[e("h3",{key:"t"},title)];
  if(desc) kids.push(e("div",{className:"desc",key:"d"},desc));
  kids.push(e("div",{className:"row2",key:"b"},body));
  return e("div",{className:"card"},kids);
}
function kvTable(rowsd){
  return e("table",{className:"sum"},e("tbody",null,rowsd.map(function(row,i){
    return e("tr",{key:i},e("td",null,row[0]),e("td",null,row[1]));})));
}
function spark(curve){
  if(!curve||curve.length<2) return null;
  var w=380,h=110,pad=6;
  var mn=Math.min.apply(null,curve),mx=Math.max.apply(null,curve),rng=(mx-mn)||1;
  function X(i){return pad+(w-2*pad)*i/(curve.length-1);}
  function Y(v){return pad+(h-2*pad)*(1-(v-mn)/rng);}
  var pts=curve.map(function(v,i){return X(i).toFixed(1)+","+Y(v).toFixed(1);}).join(" ");
  var base=(0>=mn&&0<=mx)?e("line",{x1:pad,y1:Y(0),x2:w-pad,y2:Y(0),stroke:"#ccc","stroke-dasharray":"3 3"}):null;
  return e("svg",{width:"100%",height:h,viewBox:"0 0 "+w+" "+h,preserveAspectRatio:"none",
    style:{border:"1px solid #e3e6ea",background:"#fff",borderRadius:"6px"}},
    base,e("polyline",{points:pts,fill:"none",stroke:"#c00","strokeWidth":1.6}));
}
var columns=[
  {title:"#",dataIndex:"idx",width:48,fixed:"left"},
  {title:"代码",dataIndex:"ts_code",width:104,fixed:"left"},
  {title:"名称",dataIndex:"name",width:96,fixed:"left"},
  {title:"信号前日(T)",dataIndex:"t_date_1lb",width:104,
   sorter:function(a,b){return (a.t_date_1lb||"").localeCompare(b.t_date_1lb||"");},
   onCell:function(rec){return rec.grp_color?{style:{background:rec.grp_color,fontWeight:700}}:{};}},
  {title:"连板高度",dataIndex:"boards",width:96,align:"center",
   sorter:function(a,b){return (a.boards||0)-(b.boards||0);},
   render:function(v){
     if(v===null||v===undefined) return "-";
     var c = v>=5?"#c00":(v>=3?"#d46b08":"#555");
     return e("span",{style:{color:c,fontWeight:700}},v+"连板");}},
  {title:"proba",dataIndex:"proba",width:88,align:"right",
   sorter:function(a,b){return a.proba-b.proba;},defaultSortOrder:"descend",
   render:function(v){return e("span",{style:{color:"#c00",fontWeight:700}},(v*100).toFixed(1)+"%");}},
  {title:"智能止盈",dataIndex:"ret_real",width:100,align:"right",
   sorter:function(a,b){return (a.ret_real||-99)-(b.ret_real||-99);},render:retCell},
  {title:"次日跌停",dataIndex:"next_dn",width:88,align:"center",
   sorter:function(a,b){return (a.next_dn?1:0)-(b.next_dn?1:0);},
   render:function(v){
     if(v===null||v===undefined) return "-";
     return v?e("span",{style:{color:"#080",fontWeight:700}},"跌停")
             :e("span",{style:{color:"#bbb"}},"否");}},
  {title:"次日涨跌幅",dataIndex:"next_chg",width:104,align:"right",
   sorter:function(a,b){return (a.next_chg||-99)-(b.next_chg||-99);},render:retCell},
  {title:"T+3(从买点)",dataIndex:"ret_t3_buy",width:108,align:"right",
   sorter:function(a,b){return (a.ret_t3_buy||-99)-(b.ret_t3_buy||-99);},render:retCell},
  {title:"T+4(从买点)",dataIndex:"ret_t4_buy",width:108,align:"right",
   sorter:function(a,b){return (a.ret_t4_buy||-99)-(b.ret_t4_buy||-99);},render:retCell},
  {title:"T+5(从买点)",dataIndex:"ret_t5_buy",width:108,align:"right",
   sorter:function(a,b){return (a.ret_t5_buy||-99)-(b.ret_t5_buy||-99);},render:retCell},
  {title:"T+6(从买点)",dataIndex:"ret_t6_buy",width:108,align:"right",
   sorter:function(a,b){return (a.ret_t6_buy||-99)-(b.ret_t6_buy||-99);},render:retCell},
  {title:"T+7(从买点)",dataIndex:"ret_from_buy",width:108,align:"right",
   sorter:function(a,b){return (a.ret_from_buy||-99)-(b.ret_from_buy||-99);},render:retCell},
];
function App(){
  return e("div",null,
    e("h1",null,"首板→2板模型 v1·含竞价 · "+P.tier_label+" 档（proba ≥ "+P.proba_thr.toFixed(4)+"）历史选股"),
    e("div",{className:"meta"},"统计区间："+P.start+" 起 · 共 "+P.n+" 只（按 首板信号日）· 收益均从 首板次日开盘买点起算 · 生成 "+P.now),
    e("div",{className:"kpi"},P.kpis.map(function(k,i){
      return e("div",{className:"box",key:i},
        e("div",{className:"lbl"},k[0]),
        e("div",{className:"val",style:{color:k[2]}},k[1]));})),
    e("div",{className:"sec-title"},"收益与分布"),
    e("div",{className:"grid"},
      card("持有期收益（固定持有，从买点）",null,
        sumTable(["持有期","均收益","中位数","胜率"],P.summary)),
      card("最终连板高度分布","「1板」= 止步首板、没能开出 2板 · 样本 "+P.boards_n+" 只",
        sumTable(["最终高度","数量","占比"],P.boards_dist)),
      card("每日信号数分布",null,
        [sumTable(["单日信号数","天数","占比"],P.daily_dist),kvTable(P.daily_stats)]),
      card("各年份信号数量",null,
        sumTable(["年份","信号数"],P.year_dist)),
      card("信号月份分布（各年）",null,
        sumTable(P.month_head,P.month_matrix))),
    e("div",{className:"sec-title"},"诊断（实际收益·智能止盈：炸板次日开盘卖 / 连板持有到断板当日开盘卖）"),
    e("div",{className:"grid grid4"},
      card("累计收益曲线（全量）",
        "每个信号日固定投 1 份钱（同日多只平摊），记下当天平均收益，逐日累加（不复利）。曲线 = 累计赚到的本金倍数；如 +900% ≈ 累计约 9 倍本金的利润。",
        [spark(P.equity_curve),kvTable(P.equity_stats)]),
      card("累计收益曲线（每日取 proba 前4，剔除13/14连板）",
        "口径同左，但：单日信号 >4 只时只取 proba 最高的 4 只；并剔除 13连板 / 14连板 的极端样本。",
        [spark(P.equity_curve2),kvTable(P.equity_stats2)]),
      card("去掉头部赢家后是否还正",
        "依次剔除收益最高的 N 笔后看均收益/胜率是否仍为正——验证 edge 是否靠少数极值撑起。",
        sumTable(["口径","样本","均收益","胜率"],P.robust))),
    e("div",{className:"meta",style:{margin:"4px 0 10px"}},"分年度累计收益曲线（口径同上：每日取 proba 前4、剔除13/14连板）"),
    e("div",{className:"grid grid4"},
      ["2024","2025","2026"].map(function(y){
        var yc=P.year_curves[y];
        return card(y+" 年累计收益曲线",null,
          [spark(yc.curve),kvTable(yc.stats)]);
      })),
    e("div",{className:"sec-title"},"个股明细"),
    e("div",{className:"meta"},"「智能止盈」收益口径：买点 = 首板次日开盘；止步首板(未晋级) → 次日开盘卖出；晋级连板 → 一直持有，到断板当日开盘价卖出（默认对续板有完美预判）。"),
    e(Table,{columns:columns,dataSource:P.data,size:"small",bordered:true,
      pagination:false,scroll:{x:1530},sticky:true})
  );
}
ReactDOM.createRoot(document.getElementById("root")).render(e(App));
</script></body></html>""".replace("__PAYLOAD__", payload_json)


def run(start: str, tier_pct: int = 1, proba_thr: float = None,
        tier_top: int = None, proba_hi: float = None):
    """主流程：扫 首板信号 → 打分 → 按分位档[下限,上限)筛 → 附价 → 算 T+7 收益 → HTML。"""
    model, feat_cols, tiers = _load_model()
    ti = TIER_INDEX[tier_pct]
    tier_label = tiers[ti]["label"]
    if proba_thr is None:
        proba_thr = tiers[ti]["proba"]

    upper = proba_hi
    if upper is None and tier_top is not None:
        upper = tiers[TIER_INDEX[tier_top]]["proba"]
    if upper is not None:
        tier_label = f"{tier_label} ~ {tiers[TIER_INDEX[tier_top]]['label']}" \
            if tier_top is not None else f"{tier_label}（上限 {upper:.4f}）"

    print(f"=== 扫 首板信号（{start} 起）===")
    from ml_train_1to2_v1 import _get_signals
    sig = _get_signals()
    sig = sig[sig["trade_date"] >= start].reset_index(drop=True)
    print(f"  首板信号数：{len(sig)}")

    from ml_features_1to2_v1 import build_feature_matrix
    feat = build_feature_matrix(sig[["ts_code", "trade_date"]], require_label=False)
    feat = feat.dropna(subset=feat_cols, how="all").copy()
    feat["proba"] = model.predict_proba(feat[feat_cols])[:, 1]

    mask = feat["proba"] >= proba_thr
    if upper is not None:
        mask &= feat["proba"] < upper
    picked = feat[mask].copy()
    band = f"proba >= {proba_thr:.4f}" + (f" 且 < {upper:.4f}" if upper is not None else "")
    print(f"  {tier_label} 档（{band}）命中：{len(picked)} 只")
    if picked.empty:
        print("  无命中。")
        return

    con = _duckdb.connect(DUCK_PATH, read_only=True)
    try:
        prices = _attach_prices(con, picked)
        boards = _attach_boards(con, picked)
        picked_b = picked[["ts_code", "trade_date"]].merge(
            boards, on=["ts_code", "trade_date"], how="left")
        realized = _attach_realized(con, picked_b)
        dn_set = _get_limitdown_set(con)
        names = _get_names(con, picked["ts_code"].tolist())
    finally:
        con.close()

    df = picked[["ts_code", "trade_date", "proba"]].merge(
        prices, on=["ts_code", "trade_date"], how="left").merge(
        boards, on=["ts_code", "trade_date"], how="left").merge(
        realized, on=["ts_code", "trade_date"], how="left")
    df["name"] = df["ts_code"].map(names).fillna("")
    df["next_dn"] = [
        (tc, bd) in dn_set if isinstance(bd, str) and bd != "-" else None
        for tc, bd in zip(df["ts_code"], df["buy_date"])
    ]

    for ret_col, close_col in (("ret_t3_buy", "t3_close"), ("ret_t4_buy", "t4_close"),
                               ("ret_t5_buy", "t5_close"), ("ret_t6_buy", "t6_close"),
                               ("ret_from_buy", "t7_close")):
        df[ret_col] = np.where(
            df["buy_open"].notna() & df[close_col].notna() & (df["buy_open"] > 0),
            df[close_col] / df["buy_open"] - 1.0, np.nan)
    df["ret_real"] = np.where(
        df["buy_open"].notna() & df["sell_real"].notna() & (df["buy_open"] > 0),
        df["sell_real"] / df["buy_open"] - 1.0, np.nan)
    df["next_chg"] = np.where(
        df["d2_close"].notna() & df["buy_close"].notna() & (df["d2_close"] > 0),
        df["buy_close"] / df["d2_close"] - 1.0, np.nan)

    df = df.sort_values(["trade_date", "proba"], ascending=[True, False]).reset_index(drop=True)
    rows = df.to_dict(orient="records")
    for r in rows:
        for k in ("ret_t3_buy", "ret_t4_buy", "ret_t5_buy", "ret_t6_buy",
                  "ret_from_buy", "ret_real", "next_chg"):
            if pd.isna(r[k]):
                r[k] = None
        for k in ("t_close_1lb", "buy_open", "t7_close"):
            if pd.isna(r[k]):
                r[k] = None
        r["boards"] = None if pd.isna(r.get("boards")) else int(r["boards"])

    html = _render_html(rows, start, proba_thr, tier_label)
    out_html = os.path.join(os.path.dirname(__file__), "html_output",
                            f"screen_1to2_top{tier_pct}_t7_v1.html")
    os.makedirs(os.path.dirname(out_html), exist_ok=True)
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nHTML 已生成：{out_html}")

    valr = df["ret_real"].dropna()
    validb = df["ret_from_buy"].dropna()
    if len(valr):
        print(f"  实际收益(智能止盈)：均 {valr.mean()*100:+.2f}%  "
              f"中位 {valr.median()*100:+.2f}%  胜率 {(valr>0).mean()*100:.1f}%")
    if len(validb):
        print(f"  T+7 从首板次日开盘买：均 {validb.mean()*100:+.2f}%  "
              f"中位 {validb.median()*100:+.2f}%  胜率 {(validb>0).mean()*100:.1f}%")
    try:
        webbrowser.open("file://" + os.path.abspath(out_html))
    except Exception:
        pass


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="20260101")
    p.add_argument("--tier", type=int, default=1, choices=[1, 5, 10, 20],
                   help="分位档下限：1/5/10/20（默认 1，含更高 proba）")
    p.add_argument("--tier-top", type=int, default=None, choices=[1, 5, 10, 20],
                   help="分位档上限（更严档），如 --tier 10 --tier-top 5 取 Top10~Top5 之间")
    p.add_argument("--proba", type=float, default=None,
                   help="手动指定 proba 下限切点，覆盖 --tier")
    p.add_argument("--proba-hi", type=float, default=None,
                   help="手动指定 proba 上限切点（不含），覆盖 --tier-top")
    args = p.parse_args()
    run(args.start, args.tier, args.proba, args.tier_top, args.proba_hi)
