"""
ml_score_v2.py — 每日盘后小量首板打分（双模型：分类 proba + 回归 pred_return）+ 市场状态 + 仓位建议

用法：
  python first10/ml_v2/ml_score_v2.py                  # 最新交易日，top 15
  python first10/ml_v2/ml_score_v2.py --date 20260508  # 指定历史日期复盘
  python first10/ml_v2/ml_score_v2.py --top 20         # 展示更多候选
  每次执行都自带 SHAP 因子分解 + 市场状态横幅 + 仓位建议，输出 HTML 并自动打开

双模型设计：
  分类模型 (xgb_lianban_v2.pkl)：
    proba = P(T+1 涨停 AND T+2 收盘 > T+1 收盘)
    用途：信号过滤 + 主排序（决定 Top N 谁入选）
    注意：proba 经 scale_pos_weight 训练，绝对值偏高，仅用于排序
  回归模型 (xgb_lianban_v2_regress.pkl)：
    pred_return = 预测的 T+2_open / T+1_open - 1（实际收益率）
    用途：在分类筛出的候选中精筛收益预期
    Spearman 排序相关 0.16 > 分类版的 0.10
    决策规则（基于实测分布校准，2026 市场环境）：
      pred > +0.5%   → 仓位 ×1.2（约 Top 20 池前 5%，强信号）
      pred 0 ~ 0.3%  → 仓位 ×0.7（谨慎）
      pred < 0       → 仓位 ×0.4（降级）
      pred < -0.5%   → 不买入（label「预测亏损明显」）

    注意：当前市场偏弱，pred 普遍偏负：
      - 全样本中位 -0.59%，Top 20 池中位 -0.67%
      - pred > 0 已是「跑赢基准」
      - pred > +0.5% 即「强信号」，每周可能仅 1-2 个

信号过滤条件：
  - limit_list_d.limit_type='U' AND limit_times=1     首板
  - daily.vol / 前5日均量 < 1.4                        小量首板
  - daily.high > daily.low                             非一字板
  - 非 ST，排除创业板/科创板/北交所

HTML 报告结构：
  [1] 顶部：日期、信号数、模型基线
  [2] 市场状态横幅（连板气氛 / 趋势市检测 / 警示）
  [3] 历史胜率参考（Top 1%/5%/10% 历史复合胜率）
  [4] 候选表（Top N 票，含建议仓位）
  [5] 各票 SHAP 因子分解卡片

依赖：
  - 模型: first10/model/xgb_lianban_v2.pkl
  - 数据: stock_data_tushare.duckdb
  - 市场状态: market_state 表（cache_tushare.py --update 时自动重建）
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

MODEL_PATH     = os.path.join(os.path.dirname(os.path.dirname(__file__)), "model", "xgb_lianban_v2.pkl")
MODEL_REG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "model", "xgb_lianban_v2_regress.pkl")
DUCK_PATH      = _ENV.get("LOCAL_DUCKDB_PATH", "stock_data_tushare.duckdb")

HISTORICAL_HIT_RATES = {
    "base":     0.1242,
    "top1pct":  0.2292,
    "top5pct":  0.1988,
    "top10pct": 0.1894,
}
MODEL_AUC = 0.5961

FACTOR_CN = {
    "pct_chg":              ("涨跌幅",              "信号日涨跌幅（首板≈10%）"),
    "vol_ratio_5":          ("5日量比",             "当日量 / 5日均量（<1.4 缩量）"),
    "intraday_range":       ("当日振幅",            "(高-低)/收盘"),
    "pre_days_limit":       ("前5日涨跌停数",       "前5日内|pct_chg|≥9.8 的天数"),
    "lu_time_min":          ("封板时刻",            "首封距9:30分钟数（越早封越强）"),
    "fd_amount_ratio":      ("封单/流通市值",       "封单金额/流通市值（越大封板越硬）"),
    "open_times":           ("炸板次数",            "0=一字稳封"),
    "up_stat_ratio":        ("涨停频率",            "近 T 天涨停 N 次（N/T）"),
    "circ_mv_log":          ("流通市值(log)",       "ln(流通市值)，过大过小都不利"),
    "turnover_rate":        ("换手率",              "%"),
    "pe_ttm_log":           ("PE(log)",             "sign·ln(1+|PE|)，长尾压缩"),
    "pb_log":               ("PB(log)",             "ln(PB)，长尾压缩"),
    "winner_rate":          ("获利盘",              "%（72-98% 是甜区，>99% 抛压大）"),
    "chip_conc":            ("筹码集中度",          "(95%-5%)/收盘，越小越集中"),
    "days_listed":          ("上市天数",            "信号日 - 上市日"),
    "market_trend":         ("大盘涨跌",            "中证1000当日pct_chg"),
    "market_ma_dir":        ("大盘均线方向",        "+1多/0平/-1空"),
    "market_dt_count":      ("全市场跌停数",        ""),
    "market_lu_ma5":        ("涨停数5日均",         "市场情绪宽度"),
    "stock_lianban_hist_rate": ("个股历史复合率",    "该票历史首板后T+2溢价的累积概率"),
    "net_mf_ratio_log":     ("净主力/价(log)",      "sign·ln(1+|net_mf/close|)（净流入为正）"),
    "lg_elg_ratio":         ("大单+特大单占比",     "(大+特大)/总买量"),
    "market_max_lianban":   ("当日最高连板",        "市场连板气氛高度"),
    "market_2lb_rate_ma5":  ("5日均2板晋级率",      "T日2板/T-1日1板，5日均（连板生态）"),
    "market_idx_dist_h60":  ("中证1000距60日高",  "%（>-2% = 突破期，趋势市倾向）"),
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
    l.name AS name_at_date,
    d.vol / d.vol_ma5 AS vr5
FROM limit_list_d l
JOIN d           ON d.ts_code  = l.ts_code AND d.trade_date  = l.trade_date
LEFT JOIN stock_st st
                 ON st.ts_code = l.ts_code AND st.trade_date = l.trade_date
WHERE l.limit_type   = 'U'
  AND l.limit_times  = 1
  AND d.high > d.low
  AND d.vol_ma5 > 0
  AND d.vol / d.vol_ma5 < 1.4
  AND l.ts_code NOT LIKE '688%'
  AND l.ts_code NOT LIKE '30%'
  AND l.ts_code NOT LIKE '%.BJ'
  AND st.ts_code IS NULL
  AND l.trade_date = strptime(?, '%Y%m%d')
ORDER BY l.ts_code
"""


def _load_model():
    """加载分类模型（主排序）。"""
    if not os.path.exists(MODEL_PATH):
        raise FileNotFoundError(
            f"分类模型不存在: {MODEL_PATH}\n请先运行 python first10/ml_v2/ml_train_v2.py")
    with open(MODEL_PATH, "rb") as f:
        bundle = pickle.load(f)
    return bundle["model"], bundle["feature_cols"]


def _load_regressor():
    """加载回归模型（辅助预测真实收益），可选。"""
    if not os.path.exists(MODEL_REG_PATH):
        return None, None
    with open(MODEL_REG_PATH, "rb") as f:
        bundle = pickle.load(f)
    return bundle["model"], bundle["feature_cols"]


def _latest_trade_date(con) -> str:
    """获取数据库中最新交易日（YYYYMMDD）。"""
    return con.execute("SELECT strftime(MAX(trade_date), '%Y%m%d') FROM daily").fetchone()[0]


def _get_signals_for_date(con, trade_date: str) -> pd.DataFrame:
    """取指定交易日的小量首板信号列表。"""
    return con.execute(_SIGNAL_SQL_TEMPLATE, [trade_date]).df()


def _get_market_state(con, trade_date: str) -> dict:
    """获取指定日期的市场状态（用于横幅展示）。"""
    row = con.execute("""
        SELECT n_1lb, n_2lb, n_3lb, market_max_lianban,
               market_2lb_rate, market_2lb_rate_ma5,
               market_idx_dist_h60, market_idx_breakout
        FROM market_state
        WHERE trade_date = strptime(?, '%Y%m%d')
    """, [trade_date]).fetchone()
    if not row:
        return {}
    cols = ["n_1lb", "n_2lb", "n_3lb", "max_lianban", "rate_2lb",
            "rate_2lb_ma5", "dist_h60", "breakout"]
    return dict(zip(cols, row))


def _classify_market_regime(ms: dict) -> tuple[str, str, str]:
    """根据市场状态判断当前是趋势市/题材市/震荡市，返回 (regime, color, msg)。"""
    if not ms:
        return ("未知", "#666", "未获取到市场状态数据")
    dist = ms.get("dist_h60") or 0
    rate = ms.get("rate_2lb_ma5") or 0
    height = ms.get("max_lianban") or 0
    breakout = ms.get("breakout") or 0

    is_breakout = breakout == 1 or dist > -0.02
    weak_lianban = rate < 0.15
    strong_lianban = rate > 0.22 and height >= 6

    if is_breakout and weak_lianban:
        return ("⚠️ 趋势市（连板弱）", "#c00",
                f"指数突破 + 连板生态偏弱（距60日高 {dist*100:+.1f}%，"
                f"5日均2板晋级率 {rate*100:.1f}%）。"
                "历史规律：趋势市资金往机构票流动，连板溢价压缩。建议**少出手或低仓位**。")
    if is_breakout and not weak_lianban:
        return ("📈 趋势市（连板尚可）", "#e80",
                f"指数突破中（距60日高 {dist*100:+.1f}%），但连板生态健康"
                f"（晋级率 {rate*100:.1f}%）。注意趋势随时反扑情绪。")
    if strong_lianban:
        return ("🔥 题材活跃市", "#080",
                f"连板生态活跃（最高 {int(height)} 板，晋级率 {rate*100:.1f}%）。"
                "适合参与，但注意题材切换。")
    if weak_lianban:
        return ("🥶 连板生态弱", "#a55",
                f"晋级率仅 {rate*100:.1f}%（<15%），连板接力意愿低，建议谨慎。")
    return ("➡️ 震荡市", "#555",
            f"连板气氛中性（晋级率 {rate*100:.1f}%，最高 {int(height)} 板）。常规仓位。")


def _suggest_position(rank: int, regime_color: str, pred_return: float = None) -> tuple[str, str]:
    """根据排名 + 市场状态 + 回归模型预测收益，给出仓位建议，返回 (label, css_color)。"""
    if rank == 1:
        base = (35, "首选")
    elif rank <= 3:
        base = (20, "重点")
    elif rank <= 5:
        base = (12, "关注")
    elif rank <= 10:
        base = (6, "观察")
    else:
        base = (0, "仅参考")

    pct, label = base

    if pred_return is not None and not pd.isna(pred_return):
        if pred_return < -0.005:
            pct = 0
            label = "预测亏损明显"
        elif pred_return < 0:
            pct = int(pct * 0.4)
        elif pred_return < 0.003:
            pct = int(pct * 0.7)
        elif pred_return > 0.005:
            pct = int(pct * 1.2)

    if regime_color == "#c00":
        pct = int(pct * 0.6)
    elif regime_color == "#e80":
        pct = int(pct * 0.8)

    if pct == 0:
        return ("空", "#999") if label == "仅参考" else (label, "#999")
    color = "#c00" if pct >= 25 else ("#e80" if pct >= 15 else "#555")
    return (f"{pct}% · {label}", color)


def _get_names(con, ts_codes):
    """批量取 ts_code → name 映射。"""
    if not ts_codes:
        return {}
    placeholders = ",".join(["?"] * len(ts_codes))
    rows = con.execute(
        f"SELECT ts_code, name FROM stock_meta WHERE ts_code IN ({placeholders})",
        ts_codes,
    ).fetchall()
    return dict(rows)


def _get_concepts(con, ts_codes):
    """批量取 ts_code → 同花顺概念列表（最多 5 个）。"""
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
    """单元格数值格式化（百分比/分钟/连板数等定制规则）。"""
    if pd.isna(x):
        return "-"
    if col == "fd_amount_ratio":   return f"{x*100:.2f}%"
    if col == "intraday_range":    return f"{x*100:.1f}%"
    if col == "vol_ratio_5":       return f"{x:.2f}x"
    if col == "winner_rate":       return f"{x:.1f}%"
    if col == "turnover_rate":     return f"{x:.2f}%"
    if col in ("open_times", "pre_days_limit"): return f"{int(x)}"
    if col == "lu_time_min":
        return "一字/竞价" if x <= 0 else f"+{int(x)}min"
    if col == "pct_chg":           return f"{x*100:+.2f}%"
    if col == "circ_mv_log":       return f"{x:.2f}"
    if col == "days_listed":       return f"{int(x)}天"
    if col == "market_trend":      return f"{x:+.2f}%"
    if col == "market_ma_dir":     return {1.0: "↑多头", -1.0: "↓空头", 0.0: "→平"}.get(float(x), "-")
    if col in ("market_dt_count", "market_lu_ma5"): return f"{x:.0f}"
    if col == "stock_lianban_hist_rate": return f"{x*100:.1f}%"
    if col == "up_stat_ratio":     return f"{x:.2f}"
    if col == "market_max_lianban": return f"{int(x)}板"
    if col == "market_2lb_rate_ma5": return f"{x*100:.1f}%"
    if col == "market_idx_dist_h60": return f"{x*100:+.2f}%"
    if col in ("pe_ttm_log", "pb_log", "net_mf_ratio_log"): return f"{x:+.2f}"
    return f"{x:.3g}"


def _shap_decompose(model, X: pd.DataFrame, feat_cols):
    """对入选样本做 SHAP 分解；返回 (shap_values, expected_value)。"""
    try:
        import shap
    except ImportError:
        return None, None
    explainer = shap.TreeExplainer(model)
    sv = explainer.shap_values(X[feat_cols])
    base = float(explainer.expected_value) if np.ndim(explainer.expected_value) == 0 \
           else float(explainer.expected_value[1])
    return sv, base


def _render_html(title, trade_date, n_total, regime_info, market_state,
                 rows, sv, feat_cols, base_value, X) -> str:
    """渲染 HTML 报告：横幅 + 市场指标 + 历史胜率 + 候选表 + SHAP 卡片。"""
    regime_label, regime_color, regime_msg = regime_info
    html = []
    html.append(f"""<!doctype html><html lang="zh"><head>
<meta charset="utf-8"><title>{escape(title)}</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif;
     margin:24px auto;max-width:1320px;color:#222;line-height:1.5;padding:0 16px}}
h1{{font-size:20px;margin:0 0 8px}}
h2{{font-size:16px;margin:18px 0 10px}}
.meta{{color:#666;margin-bottom:14px;font-size:13px}}
.regime{{padding:12px 16px;border-radius:6px;margin-bottom:14px;font-size:13px;
        border-left:4px solid {regime_color};background:#f9f9f9}}
.regime .label{{font-weight:700;color:{regime_color};font-size:14px;margin-bottom:4px}}
.metrics{{display:flex;gap:16px;background:#f6f8fa;padding:10px 14px;border-radius:6px;
         margin-bottom:14px;font-size:12px;flex-wrap:wrap}}
.metrics .m{{flex:1;min-width:140px}}.metrics .m .k{{color:#888}}.metrics .m .v{{font-weight:700;color:#333;margin-left:4px}}
.histbar{{font-size:12px;background:#fffbe6;padding:8px 14px;border-radius:6px;margin-bottom:14px;border-left:3px solid #fc0}}
table.tbl{{border-collapse:collapse;width:100%;font-size:13px;margin-bottom:24px}}
table.tbl th,table.tbl td{{border-bottom:1px solid #eee;padding:6px 8px;text-align:right;white-space:nowrap}}
table.tbl th{{background:#fafafa;font-weight:600;text-align:right}}
table.tbl td.l,table.tbl th.l{{text-align:left}}
table.tbl tr:hover{{background:#fffaf0}}
.proba{{font-weight:700;color:#c00}}
.posn{{font-weight:700}}
.card{{border:1px solid #e1e4e8;border-radius:8px;padding:14px 16px;margin-bottom:14px;background:#fff}}
.card.strong{{border:2px solid #2a8;background:linear-gradient(to right,#f0fdf4,#fff 30%);box-shadow:0 2px 6px rgba(34,170,80,0.15)}}
.card.super{{border:2px solid #c00;background:linear-gradient(to right,#fff5f5,#fff 30%);box-shadow:0 2px 8px rgba(204,0,0,0.18)}}
.badge{{display:inline-block;font-size:11px;padding:2px 8px;border-radius:10px;margin-left:8px;font-weight:600;vertical-align:middle}}
.badge.strong{{background:#2a8;color:#fff}}
.badge.super{{background:#c00;color:#fff}}
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
    html.append(f"<div class='meta'>交易日 {trade_date} · 信号 {n_total} 只 · "
                f"模型 OOS AUC {MODEL_AUC:.3f} · 基线 logit≈{base_value:+.3f}</div>")

    html.append(f"<div class='regime'>"
                f"<div class='label'>市场状态：{escape(regime_label)}</div>"
                f"<div>{escape(regime_msg)}</div></div>")

    if market_state:
        ms = market_state
        html.append("<div class='metrics'>")
        for label, key, fmt in [
            ("当日最高连板", "max_lianban", lambda v: f"{int(v)}板" if v else "-"),
            ("1板数", "n_1lb", lambda v: f"{int(v)}" if v else "-"),
            ("2板数", "n_2lb", lambda v: f"{int(v)}" if v else "-"),
            ("3板数", "n_3lb", lambda v: f"{int(v)}" if v else "-"),
            ("5日均2板晋级率", "rate_2lb_ma5", lambda v: f"{v*100:.1f}%" if v else "-"),
            ("中证1000距60日高", "dist_h60", lambda v: f"{v*100:+.2f}%" if v is not None else "-"),
        ]:
            v = ms.get(key)
            html.append(f"<div class='m'><span class='k'>{label}</span>"
                        f"<span class='v'>{fmt(v)}</span></div>")
        html.append("</div>")

    h = HISTORICAL_HIT_RATES
    html.append(f"<div class='histbar'>"
                f"<b>历史胜率参考</b>（2024+ OOS）："
                f"基准 <b>{h['base']*100:.1f}%</b> / "
                f"模型 Top 1% <b>{h['top1pct']*100:.1f}%</b> / "
                f"Top 5% <b>{h['top5pct']*100:.1f}%</b> / "
                f"Top 10% <b>{h['top10pct']*100:.1f}%</b>"
                f" · 下方「建议仓位」已结合排名+市场状态自动调整"
                f"</div>")

    table_cols = [
        ("ts_code",         "代码",           "l"),
        ("name",            "名称",           "l"),
        ("proba",           "复合概率",       ""),
        ("pred_return",     "预测收益",       ""),
        ("posn",            "建议仓位",       "l"),
        ("fd_amount_ratio", "封单/流通市值",  ""),
        ("net_mf_ratio_log","净主力/价(log)", ""),
        ("lg_elg_ratio",    "大单占比",       ""),
        ("vol_ratio_5",     "5日量比",        ""),
        ("intraday_range",  "当日振幅",       ""),
        ("winner_rate",     "获利盘",         ""),
        ("open_times",      "炸板次数",       ""),
        ("lu_time_min",     "封板时刻",       ""),
        ("concepts",        "概念",           "l"),
    ]
    html.append("<h2>候选 Top 榜</h2>")
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
            elif col == "pred_return":
                if v is None or pd.isna(v):
                    cell = "-"
                else:
                    color = "#0a0" if v > 0.01 else ("#c00" if v < 0 else "#888")
                    cell = f"<span style='color:{color};font-weight:600'>{v*100:+.2f}%</span>"
            elif col == "posn":
                pct, color = r.get("_posn", ("-", "#999"))
                cell = f"<span class='posn' style='color:{color}'>{escape(pct)}</span>"
            elif col in ("ts_code", "name"):
                cell = escape(str(v) if v is not None else "")
            elif col == "concepts":
                cell = escape(v or "")
            else:
                cell = _fmt_value(col, v) if v is not None else "-"
            html.append(f"<td class='{cls}'>{cell}</td>")
        html.append("</tr>")
    html.append("</tbody></table>")

    if sv is not None:
        html.append("<h2>各入选票的因子分解（SHAP）</h2>")
        for i, r in enumerate(rows):
            row_sv = sv[i]
            x_row  = X.iloc[i]
            contribs = sorted(
                [(f, float(row_sv[j]), x_row[f]) for j, f in enumerate(feat_cols)],
                key=lambda t: -abs(t[1]),
            )
            pos = [(f, v, x) for f, v, x in contribs if v > 0][:5]
            neg = [(f, v, x) for f, v, x in contribs if v < 0][:5]

            pos_sum = sum(v for _, v, _ in pos)
            neg_sum = abs(sum(v for _, v, _ in neg))
            top_pos = pos[0][1] if pos else 0
            pos_neg_ratio = pos_sum / neg_sum if neg_sum > 0 else 999

            card_cls = "card"
            badge_html = ""
            if pos_sum >= 0.6 and pos_neg_ratio >= 2.0 and top_pos >= 0.20:
                card_cls = "card super"
                badge_html = "<span class='badge super'>🔥 极强看好</span>"
            elif pos_sum >= 0.4 and pos_neg_ratio >= 1.5 and top_pos >= 0.12:
                card_cls = "card strong"
                badge_html = "<span class='badge strong'>✅ 强势看好</span>"

            pct, color = r.get("_posn", ("-", "#999"))
            html.append(f"<div class='{card_cls}'>")
            html.append(f"<h3>#{i+1} {escape(r['ts_code'])} {escape(r['name'] or '')} "
                        f"{badge_html}"
                        f"<span class='proba'>{r['proba']*100:.1f}%</span>"
                        f"<span style='float:right;color:{color}'>建议仓位 {escape(pct)}</span></h3>")
            html.append(f"<div class='meta2'>概念：{escape(r.get('concepts') or '（无映射）')}</div>")

            html.append("<div class='row2'>")
            for label, items, cls, sign_cls in [
                ("✅ 模型看好（正贡献）", pos, "pos", "p"),
                ("⚠️ 模型警惕（负贡献）", neg, "neg", "n"),
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
                f"模型路径：{escape(MODEL_PATH)} · v2 复合标签</div>")
    html.append("</body></html>")
    return "".join(html)


def score(trade_date, top_n):
    """主流程：取信号 → 双模型预测 → 排序 → SHAP → HTML。"""
    model, feat_cols = _load_model()
    model_reg, _ = _load_regressor()
    con = _duckdb.connect(DUCK_PATH, read_only=True)
    try:
        if trade_date is None:
            trade_date = _latest_trade_date(con)
            print(f"未指定 --date，自动取最新交易日 {trade_date}")

        sig = _get_signals_for_date(con, trade_date)
        if sig.empty:
            print(f"[{trade_date}] 未找到符合条件的信号。")
            return

        market_state = _get_market_state(con, trade_date)
        names    = _get_names(con,    sig["ts_code"].tolist())
        concepts = _get_concepts(con, sig["ts_code"].tolist())
    finally:
        con.close()

    regime_info = _classify_market_regime(market_state)
    print(f"市场状态：{regime_info[0]}")
    print(f"找到 {len(sig)} 个信号，构建特征...", flush=True)

    from ml_features_v2 import build_feature_matrix
    feat = build_feature_matrix(sig[["ts_code", "trade_date"]], require_label=False)
    if feat.empty:
        print("特征构建失败。")
        return

    X = feat[feat_cols].copy()
    feat["proba"] = model.predict_proba(X)[:, 1]
    if model_reg is not None:
        feat["pred_return"] = model_reg.predict(X)
    else:
        feat["pred_return"] = float("nan")
    feat["concepts"] = feat["ts_code"].map(concepts).fillna("")
    feat = feat.merge(sig[["ts_code", "name_at_date", "vr5"]], on="ts_code", how="left")
    feat["name"] = feat["name_at_date"].fillna(feat["ts_code"].map(names)).fillna("")
    feat = feat.sort_values("proba", ascending=False).reset_index(drop=True)

    top = feat.head(top_n).reset_index(drop=True)
    rows = top.to_dict(orient="records")

    regime_color = regime_info[1]
    for i, r in enumerate(rows, 1):
        r["_posn"] = _suggest_position(i, regime_color, r.get("pred_return"))

    sv, base = _shap_decompose(model, top, feat_cols)

    title = f"【小量首板·v2】首板→2连板+T+2溢价 概率打分 {trade_date}"

    html = _render_html(title, trade_date, len(feat), regime_info, market_state,
                        rows, sv, feat_cols, base or 0.0, top[feat_cols])

    out_html = os.path.join(os.path.dirname(os.path.dirname(__file__)), "html_output", f"ml_score_v2_{trade_date}.html")
    os.makedirs(os.path.dirname(out_html), exist_ok=True)
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nHTML 报告已生成: {out_html}")
    try:
        webbrowser.open("file://" + os.path.abspath(out_html))
    except Exception:
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="小量首板复合概率打分（v2 HTML 报告）")
    parser.add_argument("--date", default=None, help="交易日 YYYYMMDD（默认 daily 最大日期）")
    parser.add_argument("--top",  type=int, default=20, help="展示 top N 条（默认 20，已验证 Top 20 池均为有效候选）")
    args = parser.parse_args()
    score(args.date, args.top)
