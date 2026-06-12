"""
ml_score.py — 每日盘后对小量首板股打分 + SHAP 解释，输出 HTML 报告

═══════════════════════════════════════════════════════════════════════════════
用法
═══════════════════════════════════════════════════════════════════════════════

  python first10/ml_score.py                  # 最新交易日，top 15
  python first10/ml_score.py --date 20260415  # 指定历史日期复盘
  python first10/ml_score.py --top 20         # 展示更多候选
  python first10/ml_score.py --include-burst  # 同时打分爆量首板（OOD，仅参考）

每次执行都自带 SHAP 因子分解（不需要 flag），生成 HTML 报告并自动在浏览器打开，
不输出 CSV。

═══════════════════════════════════════════════════════════════════════════════
信号过滤条件（与 ml_train.py 训练样本一致）
═══════════════════════════════════════════════════════════════════════════════

  - limit_list_d.limit_type='U' AND limit_times=1   首板
  - daily.vol / 前5日均量 < 1.4                      非爆量（小量首板）
  - daily.high > daily.low                           非一字板
  - 非 ST（stock_st 表 LEFT JOIN 排除）
  - 排除 创业板（300xxx）/ 科创板（688xxx）/ 北交所（.BJ）

  --include-burst 关闭量比上限（OOD，模型对爆量样本的预测可靠性下降）

═══════════════════════════════════════════════════════════════════════════════
HTML 报告结构（first10/ml_score_<date>.html）
═══════════════════════════════════════════════════════════════════════════════

  [1] 顶部：日期、信号总数、模型基线 logit、过滤条件简述
  [2] 总表：Top N 候选 12 列
        代码 / 名称 / 连板概率 / 封单·流通市值 / 净主力·价 / 小单占比 /
        5日量比 / 当日振幅 / 获利盘 / 炸板次数 / 封板时刻 / 概念
  [3] 每只票一张卡片：
        ↑ 模型看好（正贡献 top 5 因子，含中文名 + 数值 + SHAP 贡献 + 解读）
        ↓ 模型警惕（负贡献 top 5 因子）

═══════════════════════════════════════════════════════════════════════════════
依赖
═══════════════════════════════════════════════════════════════════════════════

  - 模型: first10/model/xgb_lianban.pkl     （由 ml_train.py 生成）
  - 数据: stock_data_tushare.duckdb         （由 cache_tushare.py 维护）
  - 特征: ml_features.build_feature_matrix
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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

import duckdb as _duckdb
from db_loader import _ENV

MODEL_PATH = os.path.join(os.path.dirname(__file__), "model", "xgb_lianban.pkl")
DUCK_PATH  = _ENV.get("LOCAL_DUCKDB_PATH", "stock_data_tushare.duckdb")

# ── 因子中文名 + 解读方向 ─────────────────────────────────────────────────────
FACTOR_CN = {
    "pct_chg":              ("涨跌幅",              "信号日涨跌幅（小数，首板≈0.10）"),
    "vol_ratio_5":          ("5日量比",             "当日量/5日均量"),
    "vol_ratio_20":         ("20日量比",            "当日量/20日均量"),
    "intraday_range":       ("当日振幅",            "(高-低)/收盘"),
    "close_strength":       ("收盘强度",            "(收盘-低)/(高-低)，1=封板"),
    "gap_up":               ("高开幅度",            "开盘价 / 前一日收盘 - 1"),
    "pre_days_limit":       ("前5日涨跌停数",       "前5日内|pct_chg|≥9.8 的天数"),
    "lu_time_min":          ("封板时刻",            "首次涨停距09:30的分钟数（小=早封强）"),
    "fd_amount_ratio":      ("封单/流通市值",       "封单金额/流通市值（大=封板硬）"),
    "open_times":           ("炸板次数",            "0=一字/稳封，越大封板越弱"),
    "up_stat_ratio":        ("涨停频率",            "近 T 天涨停 N 次（N/T）"),
    "circ_mv_log":          ("流通市值(log)",       "ln(流通市值)"),
    "turnover_rate":        ("换手率",              "%"),
    "pe_ttm":               ("滚动市盈率",          "PE-TTM"),
    "pb":                   ("市净率",              "PB"),
    "winner_rate":          ("获利盘",              "成本下方筹码占比 %"),
    "chip_conc":            ("筹码集中度",          "(95%-5%)/收盘，越小越集中"),
    "days_listed":          ("上市天数",            "信号日 - 上市日"),
    "market_trend":         ("大盘涨跌",            "中证1000当日pct_chg"),
    "market_ma_dir":        ("大盘均线方向",        "+1多头/0平/-1空头"),
    "market_lu_count":      ("全市场涨停数",        ""),
    "market_dt_count":      ("全市场跌停数",        ""),
    "market_lu_ma5":        ("涨停数5日均",         ""),
    "stock_lianban_hist_rate": ("个股历史连板率",    "该票历史首板后连板的累积概率"),
    "net_mf_ratio":         ("净主力资金/价",       "净主力金额/收盘价"),
    "lg_elg_ratio":         ("大单+特大单占比",     "(大+特大)/总买入量"),
    "sm_ratio":             ("小单买入占比",        "小单/总买入（高=散户接盘多）"),
    # 概念特征（虽不进模型，仍可能在 feature_matrix.csv 里）
    "concept_lu_count_max": ("板块涨停数",          "所属概念中最大涨停数"),
    "concept_rank_min":     ("板块排名",            "所属概念最强排名（小=强）"),
    "concept_cons_nums_max":("板块连板数",          "所属概念中最大连板数"),
    "concept_count":        ("所属概念数",          ""),
    "fd_x_concept_lu":      ("封单×板块共振",       ""),
}


_SIGNAL_SQL_TEMPLATE = """
WITH d AS (
    SELECT ts_code, trade_date, high, low, vol,
           AVG(vol) OVER (PARTITION BY ts_code ORDER BY trade_date
                          ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING) AS vol_ma5
    FROM daily
)
SELECT
    l.ts_code,
    strftime(l.trade_date, '%Y%m%d') AS trade_date,
    l.name AS name_at_date,                   -- 信号当日的名字快照（历史正确）
    d.vol / d.vol_ma5 AS vr5
FROM limit_list_d l
JOIN d           ON d.ts_code  = l.ts_code AND d.trade_date  = l.trade_date
LEFT JOIN stock_st st
                 ON st.ts_code = l.ts_code AND st.trade_date = l.trade_date
WHERE l.limit_type   = 'U'
  AND l.limit_times  = 1
  AND d.high > d.low
  AND d.vol_ma5 > 0
  {volume_clause}
  AND l.ts_code NOT LIKE '688%'
  AND l.ts_code NOT LIKE '300%'
  AND l.ts_code NOT LIKE '%.BJ'
  AND st.ts_code IS NULL
  AND l.trade_date = strptime(?, '%Y%m%d')
ORDER BY l.ts_code
"""


def _load_model():
    if not os.path.exists(MODEL_PATH):
        raise FileNotFoundError(
            f"模型文件不存在: {MODEL_PATH}\n请先运行 python first10/ml_train.py")
    with open(MODEL_PATH, "rb") as f:
        bundle = pickle.load(f)
    return bundle["model"], bundle["feature_cols"]


def _latest_trade_date(con) -> str:
    return con.execute("SELECT strftime(MAX(trade_date), '%Y%m%d') FROM daily").fetchone()[0]


def _get_signals_for_date(con, trade_date: str, include_burst: bool) -> pd.DataFrame:
    vol_clause = "" if include_burst else "AND d.vol / d.vol_ma5 < 1.4"
    sql = _SIGNAL_SQL_TEMPLATE.format(volume_clause=vol_clause)
    return con.execute(sql, [trade_date]).df()


def _get_names(con, ts_codes):
    if not ts_codes:
        return {}
    placeholders = ",".join(["?"] * len(ts_codes))
    rows = con.execute(
        f"SELECT ts_code, name FROM stock_meta WHERE ts_code IN ({placeholders})",
        ts_codes,
    ).fetchall()
    return dict(rows)


def _get_concepts(con, ts_codes):
    if not ts_codes:
        return {}
    placeholders = ",".join(["?"] * len(ts_codes))
    rows = con.execute(
        f"""SELECT m.con_code, list(i.name ORDER BY i.name)
            FROM ths_member m JOIN ths_index i ON i.ts_code = m.ts_code
            WHERE m.con_code IN ({placeholders})
            GROUP BY m.con_code""",
        ts_codes,
    ).fetchall()
    return {code: ";".join(names[:5]) for code, names in rows}


def _fmt_value(col: str, x) -> str:
    """单值格式化（用于表格 / SHAP 解释）。"""
    if pd.isna(x):
        return "-"
    if col == "fd_amount_ratio":   return f"{x*100:.2f}%"
    if col == "intraday_range":    return f"{x*100:.1f}%"
    if col in ("vol_ratio_5", "vol_ratio_20"): return f"{x:.2f}x"
    if col == "winner_rate":       return f"{x:.1f}%"
    if col == "turnover_rate":     return f"{x:.2f}%"
    if col == "open_times":        return f"{int(x)}"
    if col == "pre_days_limit":    return f"{int(x)}"
    if col == "lu_time_min":
        return "一字/竞价" if x <= 0 else f"+{int(x)}min"
    if col == "pct_chg":           return f"{x*100:+.2f}%"
    if col == "gap_up":            return f"{x*100:+.2f}%"
    if col == "close_strength":    return f"{x:.2f}"
    if col == "circ_mv_log":       return f"{x:.2f}"
    if col == "days_listed":       return f"{int(x)}天"
    if col == "market_trend":      return f"{x:+.2f}%"
    if col == "market_ma_dir":     return {1.0: "↑多头", -1.0: "↓空头", 0.0: "→平"}.get(float(x), "-")
    if col in ("market_lu_count", "market_dt_count", "market_lu_ma5"): return f"{x:.0f}"
    if col == "stock_lianban_hist_rate": return f"{x*100:.1f}%"
    if col in ("up_stat_ratio", "winner_rate"): return f"{x:.2f}"
    return f"{x:.3g}"


def _shap_decompose(model, X: pd.DataFrame, feat_cols):
    """返回 (sv 二维数组, base_value)。"""
    try:
        import shap
    except ImportError:
        return None, None
    explainer = shap.TreeExplainer(model)
    sv = explainer.shap_values(X[feat_cols])
    base = float(explainer.expected_value) if np.ndim(explainer.expected_value) == 0 \
           else float(explainer.expected_value[1])
    return sv, base


# ── HTML 渲染 ─────────────────────────────────────────────────────────────────

def _render_html(title, trade_date, n_total, summary, rows, sv, feat_cols, base_value, X) -> str:
    """rows: list of dict {rank, ts_code, name, proba, concepts, feat_values...}"""
    html = []
    html.append(f"""<!doctype html><html lang="zh"><head>
<meta charset="utf-8"><title>{escape(title)}</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif;
     margin:24px auto;max-width:1280px;color:#222;line-height:1.5;padding:0 16px}}
h1{{font-size:20px;margin:0 0 8px}}
.meta{{color:#666;margin-bottom:18px;font-size:13px}}
.summary{{background:#f6f8fa;padding:10px 14px;border-radius:6px;margin-bottom:18px;font-size:13px}}
table.tbl{{border-collapse:collapse;width:100%;font-size:13px;margin-bottom:24px}}
table.tbl th,table.tbl td{{border-bottom:1px solid #eee;padding:6px 8px;text-align:right;white-space:nowrap}}
table.tbl th{{background:#fafafa;font-weight:600;text-align:right}}
table.tbl td.l,table.tbl th.l{{text-align:left}}
table.tbl tr:hover{{background:#fffaf0}}
.proba{{font-weight:700;color:#c00}}
.card{{border:1px solid #e1e4e8;border-radius:8px;padding:14px 16px;margin-bottom:14px;background:#fff}}
.card h3{{margin:0 0 6px;font-size:15px}}
.card .meta2{{font-size:12px;color:#666;margin-bottom:8px}}
.row2{{display:flex;gap:18px;margin-top:6px}}
.col{{flex:1}}
.col h4{{margin:0 0 4px;font-size:12px}}
.pos h4{{color:#0a0}}.neg h4{{color:#c00}}
.f{{font-size:12px;padding:3px 0;border-bottom:1px dashed #f0f0f0}}
.f .name{{font-weight:600}}.f .v{{color:#666;margin-left:6px}}
.f .ctr{{float:right;font-variant-numeric:tabular-nums}}
.f .ctr.p{{color:#0a0}}.f .ctr.n{{color:#c00}}
.tip{{font-size:11px;color:#999;margin-left:6px}}
</style></head><body>""")

    html.append(f"<h1>{escape(title)}</h1>")
    html.append(f"<div class='meta'>交易日 {trade_date} · 信号 {n_total} 个 · 模型基线 logit≈{base_value:+.3f}</div>")
    html.append(f"<div class='summary'>{escape(summary)}</div>")

    # 表格列定义
    table_cols = [
        ("ts_code",         "代码",           "l"),
        ("name",            "名称",           "l"),
        ("proba",           "连板概率",       ""),
        ("fd_amount_ratio", "封单/流通市值",  ""),
        ("net_mf_ratio",    "净主力/价",      ""),
        ("sm_ratio",        "小单占比",       ""),
        ("vol_ratio_5",     "5日量比",        ""),
        ("intraday_range",  "当日振幅",       ""),
        ("winner_rate",     "获利盘",         ""),
        ("open_times",      "炸板次数",       ""),
        ("lu_time_min",     "封板时刻",       ""),
        ("concepts",        "概念",           "l"),
    ]
    html.append("<table class='tbl'><thead><tr><th>#</th>")
    for col, cn, _ in table_cols:
        html.append(f"<th>{escape(cn)}</th>")
    html.append("</tr></thead><tbody>")
    for i, r in enumerate(rows, 1):
        html.append(f"<tr><td>{i}</td>")
        for col, cn, cls in table_cols:
            v = r.get(col)
            if col == "proba":
                cell = f"<span class='proba'>{v*100:.1f}%</span>" if v is not None else "-"
            elif col in ("ts_code", "name"):
                cell = escape(str(v) if v is not None else "")
            elif col == "concepts":
                cell = escape(v or "")
            else:
                cell = _fmt_value(col, v) if v is not None else "-"
            html.append(f"<td class='{cls}'>{cell}</td>")
        html.append("</tr>")
    html.append("</tbody></table>")

    # 每只票 SHAP 卡片
    if sv is not None:
        html.append("<h2 style='font-size:16px;margin:16px 0 10px'>各候选票的因子分解</h2>")
        for i, r in enumerate(rows):
            row_sv = sv[i]
            x_row  = X.iloc[i]
            contribs = sorted(
                [(f, float(row_sv[j]), x_row[f]) for j, f in enumerate(feat_cols)],
                key=lambda t: -abs(t[1]),
            )
            pos = [(f, v, x) for f, v, x in contribs if v > 0][:5]
            neg = [(f, v, x) for f, v, x in contribs if v < 0][:5]

            html.append(f"<div class='card'>")
            html.append(f"<h3>#{i+1} {escape(r['ts_code'])} {escape(r['name'] or '')} "
                        f"<span class='proba'>{r['proba']*100:.1f}%</span></h3>")
            html.append(f"<div class='meta2'>概念：{escape(r.get('concepts') or '（无映射）')}</div>")

            html.append("<div class='row2'>")
            for label, items, cls, sign_cls in [
                ("↑ 模型看好（正贡献）", pos, "pos", "p"),
                ("↓ 模型警惕（负贡献）", neg, "neg", "n"),
            ]:
                html.append(f"<div class='col {cls}'><h4>{label}</h4>")
                if not items:
                    html.append("<div class='f' style='color:#999'>（无显著）</div>")
                for f, v, x in items:
                    cn, tip = FACTOR_CN.get(f, (f, ""))
                    html.append(
                        f"<div class='f'>"
                        f"<span class='name'>{escape(cn)}</span>"
                        f"<span class='v'>= {_fmt_value(f, x)}</span>"
                        f"<span class='ctr {sign_cls}'>{v:+.3f}</span>"
                        + (f"<span class='tip'>{escape(tip)}</span>" if tip else "")
                        + "</div>"
                    )
                html.append("</div>")
            html.append("</div></div>")

    html.append(f"<div class='meta' style='margin-top:24px'>"
                f"生成时间：{datetime.now():%Y-%m-%d %H:%M:%S} · "
                f"模型路径：{escape(MODEL_PATH)}</div>")
    html.append("</body></html>")
    return "".join(html)


def score(trade_date, top_n, include_burst):
    model, feat_cols = _load_model()
    con = _duckdb.connect(DUCK_PATH, read_only=True)
    try:
        if trade_date is None:
            trade_date = _latest_trade_date(con)
            print(f"未指定 --date，自动取最新交易日 {trade_date}")

        sig = _get_signals_for_date(con, trade_date, include_burst)
        if sig.empty:
            print(f"[{trade_date}] 未找到符合条件的信号。")
            return

        names    = _get_names(con,    sig["ts_code"].tolist())
        concepts = _get_concepts(con, sig["ts_code"].tolist())
    finally:
        con.close()

    print(f"找到 {len(sig)} 个信号，构建特征...", flush=True)
    from ml_features import build_feature_matrix
    feat = build_feature_matrix(sig[["ts_code", "trade_date"]], require_label=False)
    if feat.empty:
        print("特征构建失败。")
        return

    X = feat[feat_cols].copy()
    feat["proba"]    = model.predict_proba(X)[:, 1]
    feat["concepts"] = feat["ts_code"].map(concepts).fillna("")
    # 优先用 limit_list_d 当日的名字快照（避免历史回溯时显示当下 *ST 等冠名变化）
    feat = feat.merge(sig[["ts_code", "name_at_date", "vr5"]], on="ts_code", how="left")
    feat["name"] = feat["name_at_date"].fillna(feat["ts_code"].map(names)).fillna("")
    feat = feat.sort_values("proba", ascending=False).reset_index(drop=True)

    top = feat.head(top_n).reset_index(drop=True)
    rows = top.to_dict(orient="records")

    sv, base = _shap_decompose(model, top, feat_cols)

    title = ("【小量首板】" if not include_burst else "【全部首板·含爆量OOD】") + f"连板概率打分 {trade_date}"
    summary = (f"过滤条件：limit_times=1（首板），vol/ma5<1.4（小量），非ST，非一字板，"
               f"排除创业板/科创板/北交所。共 {len(feat)} 只候选，按连板概率降序展示前 {len(top)} 只。"
               f" 提示：fd_amount_ratio（封单硬度）是当前模型最强因子；sm_ratio 越小越好（散户少）。")

    html = _render_html(title, trade_date, len(feat), summary, rows, sv, feat_cols, base or 0.0, top[feat_cols])

    out_html = os.path.join(os.path.dirname(__file__), f"ml_score_{trade_date}.html")
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nHTML 报告已生成: {out_html}")
    try:
        webbrowser.open("file://" + os.path.abspath(out_html))
    except Exception:
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="小量首板连板概率打分（HTML 报告）")
    parser.add_argument("--date", default=None, help="交易日 YYYYMMDD（默认 daily 最大日期）")
    parser.add_argument("--top",  type=int, default=15, help="展示 top N 条（默认 15）")
    parser.add_argument("--include-burst", action="store_true",
                        help="同时打分爆量首板（vol/ma5≥1.4，OOD，仅供参考）")
    args = parser.parse_args()
    score(args.date, args.top, args.include_burst)
