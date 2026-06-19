"""
screen_ret4d.py — 首板「4天超额收益回归」模型 选股回测（看选出来都是什么票）

基于样本外预测 model/oos_predictions_ret4d_v1.csv（2022~2026 每年由各自 walk-forward 折干净预测，
无泄漏）。买卖规则固定：T+1 开盘买 → T+5 收盘卖（持有 4 个交易日）。
选股 = 预测超额收益排进 Top 档（默认 Top 5%，按所选区间内 pred 分位切）。

展示：每只票的 信号日/代码/名称/预测超额/买入价/卖出价/实际超额收益/实际原始收益，
      KPI（数量、平均/中位超额、胜率）、按年统计。

用法：
  python regress/screen_ret4d.py                      # 全样本(2022~) Top5%
  python regress/screen_ret4d.py --tier 1             # Top1%
  python regress/screen_ret4d.py --start 20250101     # 只看 2025 起
"""

import os
import sys
import argparse
import webbrowser
from datetime import datetime
from html import escape

import numpy as np
import pandas as pd

_QUART_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _QUART_ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import duckdb
from db_loader import _ENV

DUCK_PATH = _ENV.get("LOCAL_DUCKDB_PATH", "stock_data_tushare.duckdb")
OOS_CSV   = os.path.join(os.path.dirname(__file__), "model", "oos_predictions_ret4d_v1.csv")
TIER_Q = {1: 0.01, 5: 0.05, 10: 0.10, 20: 0.20}


def _enrich(picked: pd.DataFrame) -> pd.DataFrame:
    """给选中信号补 名称 + 买入(T+1开盘)/卖出(T+5收盘) 日期价格 + 原始收益。"""
    con = duckdb.connect(DUCK_PATH, read_only=True)
    try:
        con.register("pk", picked[["ts_code", "trade_date"]])
        df = con.execute("""
            WITH s AS (SELECT ts_code, CAST(strptime(trade_date,'%Y%m%d') AS DATE) sig_date,
                              trade_date d0 FROM pk),
            f AS (
                SELECT s.ts_code, s.d0,
                    LEAD(d.open,1)  OVER w buy_open,  strftime(LEAD(d.trade_date,1) OVER w,'%Y%m%d') buy_date,
                    LEAD(d.close,5) OVER w sell_close, strftime(LEAD(d.trade_date,5) OVER w,'%Y%m%d') sell_date
                FROM s JOIN daily d ON d.ts_code=s.ts_code AND d.trade_date>=s.sig_date
                WINDOW w AS (PARTITION BY s.ts_code, s.sig_date ORDER BY d.trade_date)
                QUALIFY d.trade_date = s.sig_date)
            SELECT f.ts_code, f.d0 AS trade_date, m.name,
                   f.buy_date, f.buy_open, f.sell_date, f.sell_close,
                   CASE WHEN f.buy_open>0 THEN f.sell_close/f.buy_open-1 END AS raw_ret
            FROM f LEFT JOIN stock_meta m ON m.ts_code=f.ts_code
        """).df()
    finally:
        con.close()
    return picked.merge(df, on=["ts_code", "trade_date"], how="left")


def _stat_row(label, sub):
    """一行统计：数量 / 平均超额 / 中位超额 / 胜率 / 平均原始收益。"""
    ex = sub["label"].values
    rr = sub["raw_ret"].dropna().values
    return [label, len(sub), f"{ex.mean()*100:+.2f}%", f"{np.median(ex)*100:+.2f}%",
            f"{(ex>0).mean()*100:.1f}%", f"{rr.mean()*100:+.2f}%" if len(rr) else "-"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", type=int, default=5, choices=[1, 5, 10, 20])
    ap.add_argument("--start", default="20220101")
    ap.add_argument("--end", default="20991231")
    args = ap.parse_args()

    oos = pd.read_csv(OOS_CSV, dtype={"trade_date": str})
    oos = oos[(oos["trade_date"] >= args.start) & (oos["trade_date"] <= args.end)].copy()
    if oos.empty:
        print("区间内无样本外预测。")
        return

    thr = oos["pred"].quantile(1 - TIER_Q[args.tier])
    picked = oos[oos["pred"] >= thr].sort_values("trade_date").reset_index(drop=True)
    picked = _enrich(picked)
    print(f"区间 {args.start}~{args.end} · Top{args.tier}%（pred≥{thr:+.4f}）选出 {len(picked)} 只")
    print(f"  平均超额 {picked['label'].mean()*100:+.2f}%  胜率 {(picked['label']>0).mean()*100:.1f}%")

    kpis = [["选出票数", str(len(picked)), "#222"],
            ["平均超额收益", f"{picked['label'].mean()*100:+.2f}%",
             "#c00" if picked['label'].mean() >= 0 else "#080"],
            ["中位超额", f"{picked['label'].median()*100:+.2f}%", "#222"],
            ["胜率(超额>0)", f"{(picked['label']>0).mean()*100:.1f}%", "#222"],
            ["平均原始收益", f"{picked['raw_ret'].mean()*100:+.2f}%", "#222"]]

    by_year = [_stat_row(y, sub) for y, sub in picked.assign(
        y=picked["trade_date"].str[:4]).groupby("y")]

    rows_html = []
    for i, r in enumerate(picked.itertuples(index=False), 1):
        ex, rr = r.label, r.raw_ret
        exc = "#c00" if ex > 0 else "#080"
        rrc = "#c00" if (pd.notna(rr) and rr > 0) else "#080"
        rows_html.append(
            f"<tr><td>{i}</td><td class=l>{escape(str(r.ts_code))}</td>"
            f"<td class=l>{escape(str(r.name or ''))}</td>"
            f"<td>{r.trade_date}</td><td style='color:#c00;font-weight:700'>{r.pred*100:+.2f}%</td>"
            f"<td>{r.buy_date or '-'}</td><td>{r.buy_open:.2f}</td>"
            f"<td>{r.sell_date or '-'}</td><td>{r.sell_close:.2f}</td>"
            f"<td style='color:{rrc};font-weight:700'>{rr*100:+.2f}%</td>"
            f"<td style='color:{exc};font-weight:700'>{ex*100:+.2f}%</td></tr>")

    def _tbl(head, rows):
        h = "".join(f"<th>{c}</th>" for c in head)
        b = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
        return f"<table class=sum><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table>"

    kpi_html = "".join(f"<div class=box><div class=lbl>{k[0]}</div>"
                       f"<div class=val style='color:{k[2]}'>{k[1]}</div></div>" for k in kpis)

    _dcols = ["#", "代码", "名称", "信号日", "预测超额", "买入日(T+1)", "买入开盘",
              "卖出日(T+5)", "卖出收盘", "原始收益", "实际超额"]
    _detail_head = "".join(
        f"<th class=s onclick='sortT({j})'>{c}</th>" for j, c in enumerate(_dcols))

    html = f"""<!doctype html><html lang=zh><head><meta charset=utf-8>
<title>首板4天超额收益 选股回测</title><style>
body{{font-family:-apple-system,"Microsoft YaHei",sans-serif;margin:24px auto;max-width:1280px;
color:#222;padding:0 16px;background:#f4f6f8}}h1{{font-size:20px;margin:0 0 6px}}
.meta{{color:#666;font-size:13px;margin-bottom:14px}}
.kpi{{display:flex;gap:14px;flex-wrap:wrap;margin:0 0 16px}}
.box{{flex:1;min-width:130px;background:#fff;border:1px solid #e8ebee;border-radius:10px;padding:13px 16px}}
.lbl{{font-size:12px;color:#999;margin-bottom:6px}}.val{{font-size:21px;font-weight:800}}
.sec{{font-size:15px;font-weight:700;margin:18px 0 10px;padding-left:10px;border-left:4px solid #c00}}
table.sum{{border-collapse:collapse;font-size:13px;width:100%;background:#fff}}
table.sum th,table.sum td{{border:1px solid #e3e6ea;padding:6px 10px;text-align:right;white-space:nowrap}}
table.sum th{{background:#f6f8fa;color:#666}}table.sum td.l,table.sum th.l{{text-align:left}}
table.sum tbody tr:hover{{background:#fffaf0}}.scroll{{max-height:640px;overflow:auto}}
th.s{{cursor:pointer;user-select:none}}th.s:hover{{color:#c00}}th.s::after{{content:" \\2195";color:#bbb;font-size:11px}}</style></head><body>
<h1>首板 · 4天超额收益模型 · 选股回测（Top{args.tier}%）</h1>
<div class=meta>区间 {args.start}~{args.end} · 样本外预测(无泄漏) · 规则：T+1开盘买→T+5收盘卖（持有4天）·
超额=个股收益−中证1000同期 · 生成 {datetime.now():%Y-%m-%d %H:%M}</div>
<div class=kpi>{kpi_html}</div>
<div class=sec>按年表现</div>
{_tbl(["年份","票数","平均超额","中位超额","胜率","平均原始收益"], by_year)}
<div class=sec>选出个股明细（{len(picked)} 只，点表头可排序）</div>
<div class=scroll><table class=sum id=detail><thead><tr>{_detail_head}</tr></thead><tbody>
{''.join(rows_html)}</tbody></table></div>
<script>
function sortT(idx){{
  var tb=document.querySelector('#detail tbody');
  var rows=[].slice.call(tb.rows);
  var dir=tb.getAttribute('data-dir')==='asc'&&tb.getAttribute('data-col')==String(idx)?'desc':'asc';
  function val(td){{var t=td.textContent.replace(/[%,\\s]/g,'');var n=parseFloat(t);return isNaN(n)?td.textContent:n;}}
  rows.sort(function(a,b){{var x=val(a.cells[idx]),y=val(b.cells[idx]);
    if(typeof x==='number'&&typeof y==='number')return dir==='asc'?x-y:y-x;
    return dir==='asc'?String(x).localeCompare(String(y)):String(y).localeCompare(String(x));}});
  rows.forEach(function(r){{tb.appendChild(r);}});
  tb.setAttribute('data-dir',dir);tb.setAttribute('data-col',idx);
}}
</script>
</body></html>"""

    out = os.path.join(os.path.dirname(__file__), "html_output",
                       f"screen_ret4d_top{args.tier}_{args.start}_{args.end}.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"HTML 已生成：{out}")
    try:
        webbrowser.open("file://" + os.path.abspath(out))
    except Exception:
        pass


if __name__ == "__main__":
    main()
