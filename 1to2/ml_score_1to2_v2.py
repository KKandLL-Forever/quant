"""
ml_score_1to2_v2.py — 每日盘后 首板个股晋级 2 板成功率打分 v2（实盘部署模型）

用 xgb_1lb_2lb_v2_deploy.pkl（34 特征，特征同 ml_features_1to2_v1）：该模型由
1to2_model_v2_deploy.py 产出——锁定 v2 评估的轮数、喂 ≤今天全部数据训练，是实盘最强模型。
分位档切点沿用 v2（2022~2026 全样本外合并标定，跨熊牛，比训练集分位更保守、更耐用）。
需先用 cache_tushare.py 补齐 stk_auction_o/c 数据，并先跑 1to2_model_v2_deploy.py 生成部署 pkl。

proba = P(T+1 pct_chg >= 9.8)，即 P(2 板成功，即首板晋级2板)，用于排序 + 信号过滤。

用法：
  python 1to2/ml_score_1to2_v2.py                 # 最新交易日
  python 1to2/ml_score_1to2_v2.py --date 20260508 # 指定历史日期复盘
  python 1to2/ml_score_1to2_v2.py --top 20        # 上限条数（候选已先按 Top 1% 过滤）
  每次执行自带 SHAP 因子分解 + 市场状态横幅，输出 HTML 并自动打开
  候选榜只展示 proba ≥ Top 1% 阈值的个股（无 Top 1% 候选则不出 HTML，仅终端打印 proba）

信号过滤条件：
  - limit_list_d.limit_type='U' AND limit_times=1     首板
  - 非 ST，排除创业板/科创板/北交所

依赖：
  - 模型: 1to2/model/xgb_1lb_2lb_v2_deploy.pkl（先跑 1to2_model_v2_deploy.py）
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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

import duckdb as _duckdb
from db_loader import _ENV

MODEL_PATH = os.path.join(os.path.dirname(__file__), "model", "xgb_1lb_2lb_v2_deploy.pkl")
DUCK_PATH  = _ENV.get("LOCAL_DUCKDB_PATH", "stock_data_tushare.duckdb")

HISTORICAL_HIT_RATES = {
    "base":     0.3600,
    "top1pct":  1.0000,
    "top5pct":  0.8148,
    "top10pct": 0.7598,
}
MODEL_AUC = 0.6921

PROBA_THRESHOLDS = [
    (0.7112, "Top 1%",  1.0000, "#c00"),
    (0.6624, "Top 5%",  0.8148, "#e80"),
    (0.6023, "Top 10%", 0.7598, "#0a0"),
    (0.5138, "Top 20%", None,   "#888"),
]

TIER_COLORS = ["#c00", "#e80", "#0a0", "#888"]


def _resolve_tiers(bundle):
    """从 pkl 的 tiers/meta 解析出 (thresholds, hit_rates, auc)；旧模型无 tiers 时回退到写死常量。"""
    tiers = bundle.get("tiers")
    meta = bundle.get("meta") or {}
    if not tiers:
        return PROBA_THRESHOLDS, HISTORICAL_HIT_RATES, MODEL_AUC
    thresholds = []
    for i, t in enumerate(tiers):
        color = TIER_COLORS[i] if i < len(TIER_COLORS) else "#888"
        win = t["win"] if t["q"] < 0.20 else None
        thresholds.append((t["proba"], t["label"], win, color))
    hit = {"base": meta.get("base", 0.0)}
    keys = ["top1pct", "top5pct", "top10pct"]
    for k, t in zip(keys, tiers):
        hit[k] = t["win"]
    return thresholds, hit, meta.get("auc", 0.0)


def _proba_tier(proba: float, thresholds):
    """把 proba 映射到分位档（返回 (label, win_rate, color)），低于最低档返回 None。"""
    for thr, label, win, color in thresholds:
        if proba >= thr:
            return label, win, color
    return None


def _print_tier_summary(trade_date: str, rows, thresholds) -> None:
    """把 Top 1%/5%/10% 达标候选按分位档分组打印到 console，供 daily_2lb 流水线查看。"""
    groups = {}
    for r in rows:
        tier = _proba_tier(r.get("proba") or 0.0, thresholds)
        if tier:
            groups.setdefault(tier[0], []).append(r)
    print(f"\n===== {trade_date} 分位档达标候选 =====")
    any_hit = False
    for label in ("Top 1%", "Top 5%", "Top 10%"):
        items = groups.get(label, [])
        if not items:
            continue
        any_hit = True
        print(f"\n【{label}】{len(items)} 只：")
        for r in items:
            print(f"  · {r.get('ts_code','')} {r.get('name') or ''}"
                  f"  proba={r.get('proba') or 0.0:.3f}")
    if not any_hit:
        print("（今日无 Top 1% 达标候选）")

FACTOR_CN = {
    "pct_chg":              ("涨跌幅",              "信号日涨跌幅（首板≈10%）"),
    "vol_ratio_5":          ("5日量比",             "当日量 / 5日均量"),
    "intraday_range":       ("当日振幅",            "(高-低)/收盘"),
    "pre_days_limit":       ("前5日涨跌停数",       "前5日内|pct_chg|≥9.8 的天数"),
    "lu_time_min":          ("首板封板时间",         "首封距9:30分钟数（越早封越强）"),
    "fd_amount_ratio":      ("首板封单/流通市值",    "封单金额/流通市值（越大封板越硬）"),
    "open_times":           ("首板炸板次数",         "0=稳封"),
    "up_stat_ratio":        ("涨停频率",            "近 T 天涨停 N 次（N/T）"),
    "circ_mv_log":          ("流通市值(log)",       "ln(流通市值)"),
    "turnover_rate":        ("换手率",              "%"),
    "pe_ttm_log":           ("PE(log)",             "sign·ln(1+|PE|)，长尾压缩"),
    "pb_log":               ("PB(log)",             "ln(PB)，长尾压缩"),
    "winner_rate":          ("获利盘",              "%"),
    "chip_conc":            ("筹码集中度",          "(95%-5%)/收盘，越小越集中"),
    "days_listed":          ("上市天数",            "信号日 - 上市日"),
    "market_trend":         ("大盘涨跌",            "中证1000当日pct_chg"),
    "market_ma_dir":        ("大盘均线方向",        "+1多/0平/-1空"),
    "market_dt_count":      ("全市场跌停数",        ""),
    "market_lu_ma5":        ("涨停数5日均",         "市场情绪宽度"),
    "stock_lianban_hist_rate": ("个股历史连板率",   "该票历史信号 T+1 涨停的累积率"),
    "net_mf_ratio_log":     ("净主力/价(log)",      "sign·ln(1+|net_mf/close|)"),
    "lg_elg_ratio":         ("大单+特大单占比",     "(大+特大)/总买量"),
    "market_max_lianban":   ("当日最高连板",        ""),
    "market_2lb_rate_ma5":  ("5日均2板晋级率",      ""),
    "market_idx_dist_h60":  ("中证1000距60日高",  "%"),
    "top10_float_ratio_sum":("前10流通股东合计",    "%（PIT 安全，最近披露期）"),
    "d1_lu_time_min":       ("首板封板时刻",        "首板距9:30分钟数"),
    "d1_fd_amount_ratio":   ("首板封单/流通市值",   ""),
    "d1_open_times":        ("首板炸板次数",        ""),
    "d1_vol_ratio_5":       ("首板量比",            "首板量 / 前5日均量（爆量首板信号）"),
    "gap_2lb":              ("首板高开幅度",         "首板开盘/前日收盘 - 1"),
    "auc_o_vol_ratio":      ("开盘竞价量比",        "开盘集合竞价量/昨日全天量（抢筹强度）"),
    "auc_o_move":           ("开盘竞价异动",        "9:15→9:25 价格变动（被抢高+/被打下-）"),
    "auc_o_amt_ratio":      ("开盘竞价额比",        "开盘竞价额/昨日全天额"),
    "auc_c_vol_ratio":      ("尾盘竞价量占比",      "收盘竞价量/当日全天量（撬板/出货）"),
}


_SIGNAL_SQL_TEMPLATE = """
SELECT
    l.ts_code,
    strftime(l.trade_date, '%Y%m%d') AS trade_date,
    l.name AS name_at_date
FROM limit_list_d l
LEFT JOIN stock_st st
                 ON st.ts_code = l.ts_code AND st.trade_date = l.trade_date
WHERE l.limit_type   = 'U'
  AND l.limit_times  = 1
  AND l.ts_code NOT LIKE '688%'
  AND l.ts_code NOT LIKE '30%'
  AND l.ts_code NOT LIKE '%.BJ'
  AND st.ts_code IS NULL
  AND l.trade_date = strptime(?, '%Y%m%d')
ORDER BY l.ts_code
"""


def _load_model():
    """加载 1板→2板分类模型。"""
    if not os.path.exists(MODEL_PATH):
        raise FileNotFoundError(
            f"模型不存在: {MODEL_PATH}\n请先运行 python 1to2/1to2_model_v2_deploy.py")
    with open(MODEL_PATH, "rb") as f:
        bundle = pickle.load(f)
    return bundle["model"], bundle["feature_cols"], bundle


def _latest_trade_date(con) -> str:
    """获取数据库中最新交易日（YYYYMMDD）。"""
    return con.execute("SELECT strftime(MAX(trade_date), '%Y%m%d') FROM daily").fetchone()[0]


def _get_signals_for_date(con, trade_date: str) -> pd.DataFrame:
    """取指定交易日的 1 板（首板）信号列表。"""
    return con.execute(_SIGNAL_SQL_TEMPLATE, [trade_date]).df()


def _get_market_state(con, trade_date: str) -> dict:
    """取指定日期的市场状态行（用于横幅展示）。"""
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
                "趋势市连板溢价压缩，3 板成功率会下降，建议低仓位。")
    if is_breakout and not weak_lianban:
        return ("📈 趋势市（连板尚可）", "#e80",
                f"指数突破中（距60日高 {dist*100:+.1f}%），但连板生态健康"
                f"（晋级率 {rate*100:.1f}%）。")
    if strong_lianban:
        return ("🔥 题材活跃市", "#080",
                f"连板生态活跃（最高 {int(height)} 板，晋级率 {rate*100:.1f}%）。"
                "3 板接力意愿强，正常仓位。")
    if weak_lianban:
        return ("🥶 连板生态弱", "#a55",
                f"晋级率仅 {rate*100:.1f}%（<15%），连板接力意愿低。")
    return ("➡️ 震荡市", "#555",
            f"连板气氛中性（晋级率 {rate*100:.1f}%，最高 {int(height)} 板）。")


def _suggest_position(rank: int, regime_color: str) -> tuple[str, str]:
    """根据排名 + 市场状态给出仓位建议，返回 (label, css_color)。"""
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
    if col in ("fd_amount_ratio", "d1_fd_amount_ratio"):   return f"{x*100:.2f}%"
    if col == "intraday_range":    return f"{x*100:.1f}%"
    if col in ("vol_ratio_5", "d1_vol_ratio_5"):       return f"{x:.2f}x"
    if col == "winner_rate":       return f"{x:.1f}%"
    if col == "turnover_rate":     return f"{x:.2f}%"
    if col in ("open_times", "pre_days_limit", "d1_open_times"): return f"{int(x)}"
    if col in ("lu_time_min", "d1_lu_time_min"):
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
    if col == "top10_float_ratio_sum": return f"{x:.1f}%"
    if col == "gap_2lb":           return f"{x*100:+.2f}%"
    if col in ("auc_o_vol_ratio", "auc_o_amt_ratio", "auc_c_vol_ratio"): return f"{x*100:.1f}%"
    if col == "auc_o_move":        return f"{x*100:+.2f}%"
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
                 rows, sv, feat_cols, base_value, X,
                 thresholds, hit_rates, auc) -> str:
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
    html.append(f"<div class='meta'>交易日 {trade_date} · 首板信号 {n_total} 只 · "
                f"模型 OOS AUC {auc:.3f} · 基线 logit≈{base_value:+.3f}</div>")

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

    parts = []
    for thr, label, win, _color in thresholds:
        if win is not None:
            parts.append(f"{label} proba≥<b>{thr:.3f}</b>→胜率 <b>{win*100:.1f}%</b>")
        else:
            parts.append(f"{label} proba≥<b>{thr:.3f}</b>")
    html.append(f"<div class='histbar'>"
                f"<b>分位阈值 & 历史胜率</b>（2024+ OOS，基线 <b>{hit_rates['base']*100:.1f}%</b>）："
                + " / ".join(parts)
                + " · Top 1% 测试集样本小，优先看 Top 5% 更稳</div>")

    table_cols = [
        ("ts_code",            "代码",           "l"),
        ("name",               "名称",           "l"),
        ("proba",              "2板晋级概率",    ""),
        ("tier",               "分位档",         "l"),
        ("fd_amount_ratio",    "首板封单/流通",   ""),
        ("net_mf_ratio_log",   "净主力/价",      ""),
        ("lg_elg_ratio",       "大单占比",       ""),
        ("auc_o_amt_ratio",    "开盘竞价额比",   ""),
        ("auc_c_vol_ratio",    "尾盘竞价占比",   ""),
        ("intraday_range",     "首板振幅",        ""),
        ("vol_ratio_5",        "首板量比",        ""),
        ("d1_vol_ratio_5",     "首板量比",       ""),
        ("d1_lu_time_min",     "首板封板时刻",   ""),
        ("lu_time_min",        "首板封板时间",    ""),
        ("gap_2lb",            "首板高开",        ""),
        ("concepts",           "概念",           "l"),
    ]
    html.append("<h2>候选 Top 榜（仅 Top 1%）</h2>")
    html.append("<table class='tbl'><thead><tr><th>#</th>")
    for col, cn, _ in table_cols:
        html.append(f"<th>{escape(cn)}</th>")
    html.append("</tr></thead><tbody>")
    for i, r in enumerate(rows, 1):
        html.append(f"<tr><td>{i}</td>")
        for col, cn, cls in table_cols:
            v = r.get(col)
            if col == "proba":
                cell = f"<span class='proba'>{v:.3f}</span> <span style='color:#888'>({v*100:.1f}%)</span>" if v is not None else "-"
            elif col == "tier":
                tier = _proba_tier(r.get("proba") or 0.0, thresholds)
                if tier:
                    label, win, color = tier
                    win_txt = f" · {win*100:.0f}%" if win else ""
                    cell = f"<span style='color:{color};font-weight:700'>{escape(label)}{win_txt}</span>"
                else:
                    cell = "<span style='color:#bbb'>—</span>"
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
                f"模型路径：{escape(MODEL_PATH)} · 首板→2板 v2·部署</div>")
    html.append("</body></html>")
    return "".join(html)


def score(trade_date, top_n):
    """主流程：取 1 板信号 → 模型预测 → 排序 → SHAP → HTML。"""
    model, feat_cols, bundle = _load_model()
    thresholds, hit_rates, auc = _resolve_tiers(bundle)
    con = _duckdb.connect(DUCK_PATH, read_only=True)
    try:
        if trade_date is None:
            trade_date = _latest_trade_date(con)
            print(f"未指定 --date，自动取最新交易日 {trade_date}")

        sig = _get_signals_for_date(con, trade_date)
        if sig.empty:
            print(f"[{trade_date}] 未找到 1 板信号。")
            return

        market_state = _get_market_state(con, trade_date)
        names    = _get_names(con,    sig["ts_code"].tolist())
        concepts = _get_concepts(con, sig["ts_code"].tolist())
    finally:
        con.close()

    regime_info = _classify_market_regime(market_state)
    print(f"市场状态：{regime_info[0]}")
    print(f"找到 {len(sig)} 个 1 板信号，构建特征...", flush=True)

    from ml_features_1to2_v1 import build_feature_matrix
    feat = build_feature_matrix(sig[["ts_code", "trade_date"]], require_label=False)
    if feat.empty:
        print("特征构建失败。")
        return

    X = feat[feat_cols].copy()
    feat["proba"] = model.predict_proba(X)[:, 1]
    feat["concepts"] = feat["ts_code"].map(concepts).fillna("")
    feat = feat.merge(sig[["ts_code", "name_at_date"]], on="ts_code", how="left")
    feat["name"] = feat["name_at_date"].fillna(feat["ts_code"].map(names)).fillna("")
    feat = feat.sort_values("proba", ascending=False).reset_index(drop=True)
    n_signals = len(feat)
    feat_all = feat

    top1_thr = next((thr for thr, label, *_ in thresholds if "1%" in label), None)
    if top1_thr is not None:
        feat = feat[feat["proba"] >= top1_thr].reset_index(drop=True)
    print(f"Top 1% 候选 {len(feat)} 只（proba ≥ {top1_thr:.4f}）", flush=True)
    if feat.empty:
        print(f"今日无 Top 1% 候选，跳过 HTML。全部 {len(feat_all)} 只信号 proba：")
        for r in feat_all.to_dict(orient="records"):
            print(f"  · {r['ts_code']} {r.get('name') or '':<8s} proba={r['proba']:.3f}")
        return

    top = feat.head(top_n).reset_index(drop=True)
    rows = top.to_dict(orient="records")

    regime_color = regime_info[1]
    for i, r in enumerate(rows, 1):
        r["_posn"] = _suggest_position(i, regime_color)

    sv, base = _shap_decompose(model, top, feat_cols)

    title = f"【首板→2板·v2·部署】晋升概率打分 {trade_date}"

    html = _render_html(title, trade_date, n_signals, regime_info, market_state,
                        rows, sv, feat_cols, base or 0.0, top[feat_cols],
                        thresholds, hit_rates, auc)

    out_html = os.path.join(os.path.dirname(__file__), "html_output", f"ml_score_1to2_v2_{trade_date}.html")
    os.makedirs(os.path.dirname(out_html), exist_ok=True)
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nHTML 报告已生成: {out_html}")
    _print_tier_summary(trade_date, rows, thresholds)
    try:
        webbrowser.open("file://" + os.path.abspath(out_html))
    except Exception:
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="首板→2 板晋升概率打分（HTML 报告）")
    parser.add_argument("--date", default=None, help="交易日 YYYYMMDD（默认 daily 最大日期）")
    parser.add_argument("--top",  type=int, default=15, help="展示 top N 条（默认 15）")
    args = parser.parse_args()
    score(args.date, args.top)
