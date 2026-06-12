"""
screen_1to3lb_top1_hold_v2.py — 首板→3板模型 Top 1% 档选股 + 持有 5/7 天收益

用法：
  python first10/ml_v2/screen_1to3lb_top1_hold_v2.py
  python first10/ml_v2/screen_1to3lb_top1_hold_v2.py --start 20240101 --proba 0.8155

口径：
  信号日 = 首板日(T)，模型于首板日盘后打分
  买入 = T+1 开盘（2板当天开盘，挂隔夜单/竞价进）
  持有 5 天 → 卖在 T+5 收盘；持有 7 天 → 卖在 T+7 收盘
  收益：ret5 = close(T+5)/open(T+1) - 1，ret7 = close(T+7)/open(T+1) - 1
  筛选：proba >= Top 1% 阈值（默认 0.8155，2024+ 测试集分位）

产出：
  first10/html_output/screen_1to3lb_top1_hold_v2.html（自动打开）
"""

import argparse
import os
import pickle
import sys
import webbrowser
from datetime import datetime
from html import escape

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.stdout.reconfigure(encoding="utf-8")

import duckdb as _duckdb
from db_loader import _ENV

MODEL_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "model", "xgb_1to3lb_v2.pkl")
DUCK_PATH  = _ENV.get("LOCAL_DUCKDB_PATH", "stock_data_tushare.duckdb")
OUT_HTML   = os.path.join(os.path.dirname(os.path.dirname(__file__)), "html_output", "screen_1to3lb_top1_hold_v2.html")

TOP1_PROBA = 0.8155


def _load_model():
    """加载首板→3板模型。"""
    with open(MODEL_PATH, "rb") as f:
        b = pickle.load(f)
    return b["model"], b["feature_cols"]


def _attach_prices(con, signal_df: pd.DataFrame) -> pd.DataFrame:
    """给首板信号附加 T+1开盘(买点)、T+5收盘、T+7收盘 及对应日期。"""
    con.register("sig_raw", signal_df[["ts_code", "trade_date"]])
    con.execute("""
        CREATE OR REPLACE TEMP VIEW s AS
        SELECT ts_code,
               CAST(strptime(trade_date, '%Y%m%d') AS DATE) AS t_date,
               trade_date AS t_str
        FROM sig_raw
    """)
    return con.execute("""
        WITH d AS (
            SELECT ts_code, trade_date, close,
                   LEAD(open, 1)  OVER w AS buy_open,
                   LEAD(trade_date, 1) OVER w AS buy_date,
                   LEAD(pct_chg, 1) OVER w AS t1_pct,
                   LEAD(close, 5) OVER w AS c5,
                   LEAD(trade_date, 5) OVER w AS d5,
                   LEAD(close, 7) OVER w AS c7,
                   LEAD(trade_date, 7) OVER w AS d7
            FROM daily
            WINDOW w AS (PARTITION BY ts_code ORDER BY trade_date)
        )
        SELECT s.ts_code, s.t_str AS trade_date,
               d.close AS t_close,
               d.buy_open, strftime(d.buy_date, '%Y%m%d') AS buy_date,
               d.t1_pct,
               d.c5, strftime(d.d5, '%Y%m%d') AS d5,
               d.c7, strftime(d.d7, '%Y%m%d') AS d7
        FROM s JOIN d ON d.ts_code = s.ts_code AND d.trade_date = s.t_date
    """).df()


def _get_names(con, ts_codes):
    """批量取 ts_code → name。"""
    if not ts_codes:
        return {}
    ph = ",".join(["?"] * len(ts_codes))
    rows = con.execute(
        f"SELECT ts_code, name FROM stock_meta WHERE ts_code IN ({ph})", ts_codes
    ).fetchall()
    return dict(rows)


def _render_html(rows, start, proba_thr) -> str:
    """渲染选股 + 持有 5/7 天收益 HTML 报告。"""
    n = len(rows)
    r5 = np.array([r["ret5"] for r in rows if r["ret5"] is not None])
    r7 = np.array([r["ret7"] for r in rows if r["ret7"] is not None])

    def _stat(a):
        if len(a) == 0:
            return "-", "-", "-"
        return (f"{a.mean()*100:+.2f}%", f"{np.median(a)*100:+.2f}%",
                f"{(a > 0).mean()*100:.1f}%")

    m5, md5, w5 = _stat(r5)
    m7, md7, w7 = _stat(r7)

    h = ['<!doctype html><html lang="zh"><head><meta charset="utf-8">',
         '<title>首板→3板 Top1% 选股 持有5/7天收益</title><style>',
         'body{font-family:-apple-system,"Microsoft YaHei",sans-serif;margin:24px auto;'
         'max-width:1180px;color:#222;line-height:1.5;padding:0 16px}',
         'h1{font-size:20px;margin:0 0 6px}.meta{color:#666;font-size:13px;margin-bottom:14px}',
         '.summary{display:flex;gap:16px;background:#f6f8fa;padding:12px 16px;border-radius:6px;'
         'margin-bottom:16px;flex-wrap:wrap;font-size:13px}',
         '.summary .m{flex:1;min-width:130px}.summary .k{color:#888;font-size:12px}'
         '.summary .v{font-weight:700;font-size:16px;color:#333}',
         'table{border-collapse:collapse;width:100%;font-size:13px}',
         'th,td{border-bottom:1px solid #eee;padding:6px 8px;text-align:right;white-space:nowrap}',
         'th{background:#fafafa;font-weight:600}td.l,th.l{text-align:left}',
         'tr:hover{background:#fffaf0}.pos{color:#c00;font-weight:700}.neg{color:#080;font-weight:700}',
         '.proba{color:#c00;font-weight:700}</style></head><body>']

    h.append(f"<h1>首板→3板模型 · Top 1% 档（proba ≥ {proba_thr:.4f}）选股 + 持有 5/7 天收益</h1>")
    h.append(f"<div class='meta'>统计区间：{start} 起 · 共 {n} 只（按首板信号日）· "
             f"T = 首板日，买入 = T+1 开盘 · 持有5天=T+5收盘卖 / 持有7天=T+7收盘卖 · "
             f"生成 {datetime.now():%Y-%m-%d %H:%M}</div>")

    h.append("<div class='summary'>")
    for k, v in [("样本数", str(n)),
                 ("持有5天 均收益", m5), ("中位数", md5), ("胜率", w5),
                 ("持有7天 均收益", m7), ("中位数 ", md7), ("胜率 ", w7)]:
        h.append(f"<div class='m'><div class='k'>{k}</div><div class='v'>{v}</div></div>")
    h.append("</div>")

    h.append("<table><thead><tr>"
             "<th>#</th><th class='l'>代码</th><th class='l'>名称</th>"
             "<th class='l'>首板日(T)</th><th>proba</th>"
             "<th class='l'>买点(T+1)</th><th>T+1开盘</th><th>T+1涨幅</th>"
             "<th class='l'>T+5日</th><th>持有5天</th>"
             "<th class='l'>T+7日</th><th>持有7天</th></tr></thead><tbody>")

    def _cell_ret(x):
        if x is None or (isinstance(x, float) and np.isnan(x)):
            return "<td>-</td>"
        cls = "pos" if x > 0 else "neg"
        return f"<td class='{cls}'>{x*100:+.2f}%</td>"

    def _cell_num(x, suf=""):
        return f"<td>{x:.2f}{suf}</td>" if x is not None and not (isinstance(x, float) and np.isnan(x)) else "<td>-</td>"

    for i, r in enumerate(rows, 1):
        h.append(
            f"<tr><td>{i}</td>"
            f"<td class='l'>{escape(r['ts_code'])}</td>"
            f"<td class='l'>{escape(r['name'] or '')}</td>"
            f"<td class='l'>{escape(r['trade_date'])}</td>"
            f"<td class='proba'>{r['proba']*100:.1f}%</td>"
            f"<td class='l'>{escape(r['buy_date'] or '-')}</td>")
        h.append(_cell_num(r["buy_open"]))
        h.append(f"<td>{r['t1_pct']:+.1f}%</td>" if r['t1_pct'] is not None and not pd.isna(r['t1_pct']) else "<td>-</td>")
        h.append(f"<td class='l'>{escape(r['d5'] or '-')}</td>")
        h.append(_cell_ret(r["ret5"]))
        h.append(f"<td class='l'>{escape(r['d7'] or '-')}</td>")
        h.append(_cell_ret(r["ret7"]))
        h.append("</tr>")
    h.append("</tbody></table></body></html>")
    return "".join(h)


def run(start: str, proba_thr: float):
    """主流程：扫首板信号 → 打分 → 筛 Top1% → 附价 → 算持有5/7天收益 → HTML。"""
    model, feat_cols = _load_model()

    print(f"=== 扫首板信号（{start} 起）===")
    from ml_train_1to3lb_v2 import _get_signals
    sig = _get_signals()
    sig = sig[sig["trade_date"] >= start].reset_index(drop=True)
    print(f"  首板信号数：{len(sig)}")

    from ml_features_v2 import build_feature_matrix
    feat = build_feature_matrix(sig, require_label=False)
    feat = feat.dropna(subset=feat_cols, how="all").copy()
    feat["proba"] = model.predict_proba(feat[feat_cols])[:, 1]

    picked = feat[feat["proba"] >= proba_thr].copy()
    print(f"  Top 1% 档（proba >= {proba_thr}）命中：{len(picked)} 只")
    if picked.empty:
        print("  无命中。")
        return

    con = _duckdb.connect(DUCK_PATH, read_only=True)
    try:
        prices = _attach_prices(con, picked)
        names = _get_names(con, picked["ts_code"].tolist())
    finally:
        con.close()

    df = picked[["ts_code", "trade_date", "proba"]].merge(
        prices, on=["ts_code", "trade_date"], how="left")
    df["name"] = df["ts_code"].map(names).fillna("")

    df["ret5"] = np.where(df["buy_open"].notna() & df["c5"].notna() & (df["buy_open"] > 0),
                          df["c5"] / df["buy_open"] - 1.0, np.nan)
    df["ret7"] = np.where(df["buy_open"].notna() & df["c7"].notna() & (df["buy_open"] > 0),
                          df["c7"] / df["buy_open"] - 1.0, np.nan)

    df = df.sort_values(["trade_date", "proba"], ascending=[True, False]).reset_index(drop=True)
    rows = df.to_dict(orient="records")
    for r in rows:
        for k in ("ret5", "ret7", "buy_open", "t_close", "c5", "c7", "t1_pct"):
            if pd.isna(r[k]):
                r[k] = None

    html = _render_html(rows, start, proba_thr)
    os.makedirs(os.path.dirname(OUT_HTML), exist_ok=True)
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nHTML 已生成：{OUT_HTML}")

    for lab, col in [("持有5天", "ret5"), ("持有7天", "ret7")]:
        v = df[col].dropna()
        if len(v):
            print(f"  {lab}：n={len(v)} 均 {v.mean()*100:+.2f}%  "
                  f"中位 {v.median()*100:+.2f}%  胜率 {(v>0).mean()*100:.1f}%")
    try:
        webbrowser.open("file://" + os.path.abspath(OUT_HTML))
    except Exception:
        pass


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="20240101")
    p.add_argument("--proba", type=float, default=TOP1_PROBA)
    args = p.parse_args()
    run(args.start, args.proba)
