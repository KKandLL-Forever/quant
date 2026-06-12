"""
ml_score_swing_v2.py — 波段策略每日盘后打分 + 出每日 Top 榜

流程：
  1. 取指定交易日（默认最新）的第一层候选（screen_swing_layer1.scan）
  2. 计算当日市场广度（全A 站上 MA20 占比）→ 广度闸门 < 50% 标红「建议空仓」
  3. xgb_swing_v2 打分，筛 Top 10% 及以上候选
  4. 输出 HTML：广度横幅 + 候选表（proba/分位/模式/关键因子）+ SHAP 因子分解

交易纪律（回测口径）：信号次日开盘买、持有 10 个交易日卖出（hold10 实测最优出场）。
历史 OOS（2024+，Top10%+广度闸≥50%，hold10）：每笔均值 +1.9%、胜率 47%，
其中 2025 +2.2%、2026 +3.1%；2024 -1.2%（急涨急回反转市，策略天然回撤年）。

用法：
  python swing/ml_score_swing_v2.py                  # 最新交易日
  python swing/ml_score_swing_v2.py --date 20260605  # 指定日期复盘
  python swing/ml_score_swing_v2.py --top 20

依赖：swing/model/xgb_swing_v2.pkl、stock_data_tushare.duckdb
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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

import duckdb as _duckdb
from db_loader import _ENV

from screen_swing_layer1 import scan
from ml_features_swing_v2 import build_feature_matrix

MODEL_PATH = os.path.join(os.path.dirname(__file__), "model", "xgb_swing_v2.pkl")
DUCK_PATH = _ENV.get("LOCAL_DUCKDB_PATH", "stock_data_tushare.duckdb")
BREADTH_THR = 0.50

TIER_COLORS = ["#c00", "#e80", "#0a0", "#888"]

FACTOR_CN = {
    "mode_breakout":       ("进场模式",        "1=放量突破 / 0=回调企稳"),
    "pct_chg":             ("信号日涨跌幅",    ""),
    "ret5":                ("5日涨幅",         "近5日后复权涨幅"),
    "ret20":               ("20日涨幅",        "近20日后复权涨幅"),
    "ma_s_m":              ("MA5/MA20",        "短中均线发散度"),
    "ma_m_l":              ("MA20/MA60",       "中长均线发散度"),
    "dist_ma20":           ("距MA20",          "收盘相对20日线"),
    "ma60_slope":          ("MA60斜率",        "MA60较20日前"),
    "pullback_depth":      ("距10日高",        "距近10日高点回落幅度（突破为负）"),
    "vol_ratio5":          ("量比",            "当日量/前5日均量"),
    "dist_high60":         ("距60日高",        "上方阻力空间（越接近0越靠近新高）"),
    "net_mf_ratio_log":    ("净主力/价(log)",  "sign·ln(1+|net_mf/价|)"),
    "lg_elg_ratio":        ("大单+特大单占比", "(大+特大)/总买量，主力痕迹"),
    "turnover_rate":       ("换手率",          "%"),
    "volume_ratio":        ("量比(daily_basic)", ""),
    "lu_count_20":         ("近20日涨停数",    ""),
    "winner_rate":         ("获利盘",          "%"),
    "chip_conc":           ("筹码集中度",      "(95%-5%)/价，越小越集中"),
    "circ_mv_log":         ("流通市值(log)",   "ln(流通市值,万元)"),
    "pe_ttm_log":          ("PE(log)",         ""),
    "pb_log":              ("PB(log)",         ""),
    "market_trend":        ("大盘涨跌",        "中证1000当日"),
    "market_ma_dir":       ("大盘均线方向",    "+1多/0平/-1空"),
    "market_2lb_rate_ma5": ("5日均2板晋级率",  ""),
    "market_idx_dist_h60": ("中证1000距60日高", ""),
    "market_max_lianban":  ("当日最高连板",    ""),
}

def _load_model():
    """加载波段模型 bundle。"""
    if not os.path.exists(MODEL_PATH):
        raise FileNotFoundError(f"模型不存在: {MODEL_PATH}\n请先运行 python swing/ml_train_swing_v2.py")
    with open(MODEL_PATH, "rb") as f:
        b = pickle.load(f)
    return b["model"], b["feature_cols"], b


def _thresholds(bundle):
    """从 pkl tiers 解析 [(proba, label, ret_mean, ret_winrate, color)]。"""
    out = []
    for i, t in enumerate(bundle.get("tiers", [])):
        out.append((t["proba"], t["label"], t.get("ret_mean"),
                    t.get("ret_winrate"), TIER_COLORS[i] if i < len(TIER_COLORS) else "#888"))
    return out


def _tier_of(proba, thresholds):
    """proba → (label, ret_mean, ret_winrate, color)，低于最低档返回 None。"""
    for thr, label, rm, rw, color in thresholds:
        if proba >= thr:
            return label, rm, rw, color
    return None


def _latest_date(con):
    return con.execute("SELECT strftime(MAX(trade_date), '%Y%m%d') FROM daily").fetchone()[0]


def _breadth(con, date):
    """计算指定日全A站上MA20占比。"""
    sql = """
    SELECT AVG(CASE WHEN close >= ma20 THEN 1.0 ELSE 0.0 END)
    FROM (
      SELECT trade_date, close,
             AVG(close) OVER (PARTITION BY ts_code ORDER BY trade_date
                              ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS ma20
      FROM daily WHERE ts_code NOT LIKE '%.BJ'
        AND trade_date <= strptime(?, '%Y%m%d')
        AND trade_date > strptime(?, '%Y%m%d') - INTERVAL 60 DAY
    ) WHERE trade_date = strptime(?, '%Y%m%d')
    """
    r = con.execute(sql, [date, date, date]).fetchone()
    return float(r[0]) if r and r[0] is not None else None


def _names(con, codes):
    if not codes:
        return {}
    ph = ",".join(["?"] * len(codes))
    return dict(con.execute(
        f"SELECT ts_code, name FROM stock_meta WHERE ts_code IN ({ph})", codes).fetchall())


def _fmt(col, x):
    """单元格格式化。"""
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "-"
    if col == "mode_breakout":
        return "突破" if x >= 0.5 else "回调"
    if col in ("pct_chg", "ret5", "ret20", "ma_s_m", "ma_m_l", "dist_ma20",
               "ma60_slope", "pullback_depth", "dist_high60", "market_trend",
               "market_idx_dist_h60", "market_2lb_rate_ma5"):
        return f"{x*100:+.1f}%"
    if col in ("vol_ratio5", "volume_ratio"):
        return f"{x:.2f}x"
    if col in ("turnover_rate", "winner_rate"):
        return f"{x:.1f}%"
    if col in ("lu_count_20",):
        return f"{int(x)}"
    if col == "market_ma_dir":
        return {1.0: "↑多", -1.0: "↓空", 0.0: "→平"}.get(float(x), "-")
    if col == "circ_mv_log":
        return f"{np.exp(x)/10000:.0f}亿"
    if col in ("pe_ttm_log", "pb_log", "net_mf_ratio_log", "chip_conc"):
        return f"{x:+.2f}"
    if col == "lg_elg_ratio":
        return f"{x*100:.0f}%"
    return f"{x:.3g}"


def _shap(model, X, feat_cols):
    try:
        import shap
    except ImportError:
        return None, None
    expl = shap.TreeExplainer(model)
    sv = expl.shap_values(X[feat_cols])
    base = float(expl.expected_value) if np.ndim(expl.expected_value) == 0 \
        else float(expl.expected_value[1])
    return sv, base


TABLE_COLS = [
    ("ts_code", "代码", "l"), ("name", "名称", "l"),
    ("proba", "趋势存续概率", ""), ("tier", "分位档", "l"), ("mode_breakout", "模式", "l"),
    ("pct_chg", "信号日涨跌", ""), ("ret5", "5日涨幅", ""), ("pullback_depth", "距10日高", ""),
    ("vol_ratio5", "量比", ""), ("lg_elg_ratio", "大单占比", ""), ("turnover_rate", "换手", ""),
    ("dist_high60", "距60日高", ""), ("circ_mv_log", "流通市值", ""),
]


def _render(title, date, n_total, breadth, gate_ok, rows, sv, feat_cols, base, X,
            thresholds, auc):
    """渲染 HTML：广度横幅 + 候选表 + SHAP 卡片。"""
    bcolor = "#0a0" if gate_ok else "#c00"
    bmsg = (f"市场广度 {breadth*100:.0f}%（全A站上MA20占比）≥ {BREADTH_THR:.0%}，"
            "环境放行，可按榜操作。" if gate_ok else
            f"市场广度仅 {breadth*100:.0f}% < {BREADTH_THR:.0%}，"
            "弱势环境，<b>建议空仓</b>，以下候选仅供参考。") if breadth is not None \
        else "未取到广度数据。"
    h = [f"""<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>{escape(title)}</title>
<style>
body{{font-family:-apple-system,"Microsoft YaHei",sans-serif;margin:24px auto;max-width:1320px;color:#222;line-height:1.5;padding:0 16px}}
h1{{font-size:20px;margin:0 0 8px}}h2{{font-size:16px;margin:18px 0 10px}}
.meta{{color:#666;margin-bottom:14px;font-size:13px}}
.regime{{padding:12px 16px;border-radius:6px;margin-bottom:14px;font-size:13px;border-left:4px solid {bcolor};background:#f9f9f9}}
.regime .label{{font-weight:700;color:{bcolor};font-size:14px;margin-bottom:4px}}
.histbar{{font-size:12px;background:#fffbe6;padding:8px 14px;border-radius:6px;margin-bottom:14px;border-left:3px solid #fc0}}
table.tbl{{border-collapse:collapse;width:100%;font-size:13px;margin-bottom:24px}}
table.tbl th,table.tbl td{{border-bottom:1px solid #eee;padding:6px 8px;text-align:right;white-space:nowrap}}
table.tbl th{{background:#fafafa;font-weight:600}}
table.tbl td.l,table.tbl th.l{{text-align:left}}
table.tbl tr:hover{{background:#fffaf0}}
.proba{{font-weight:700;color:#c00}}
.card{{border:1px solid #e1e4e8;border-radius:8px;padding:14px 16px;margin-bottom:14px;background:#fff}}
.card h3{{margin:0 0 6px;font-size:15px}}.card .meta2{{font-size:12px;color:#666;margin-bottom:8px}}
.row2{{display:flex;gap:18px;margin-top:6px}}.col{{flex:1}}.col h4{{margin:0 0 4px;font-size:12px}}
.pos h4{{color:#0a0}}.neg h4{{color:#c00}}
.f{{font-size:12px;padding:3px 0;border-bottom:1px dashed #f0f0f0}}
.f .name{{font-weight:600}}.f .v{{color:#666;margin-left:6px}}
.f .ctr{{float:right;font-variant-numeric:tabular-nums}}.f .ctr.p{{color:#0a0}}.f .ctr.n{{color:#c00}}
.tip{{font-size:11px;color:#999;margin-left:6px}}
</style></head><body>"""]
    h.append(f"<h1>{escape(title)}</h1>")
    h.append(f"<div class='meta'>交易日 {date} · 第一层候选 {n_total} 只 · "
             f"模型 OOS AUC {auc:.3f} · 纪律：次日开盘买、持有10日卖</div>")
    h.append(f"<div class='regime'><div class='label'>市场广度闸门："
             f"{'✅ 放行' if gate_ok else '⛔ 建议空仓'}</div><div>{bmsg}</div></div>")

    parts = []
    for thr, label, rm, rw, _c in thresholds:
        seg = f"{label} proba≥<b>{thr:.3f}</b>"
        if rm is not None:
            seg += f"→每笔 <b>{rm*100:+.1f}%</b>·胜率 {rw*100:.0f}%"
        parts.append(seg)
    h.append("<div class='histbar'><b>分位阈值 & 历史每笔收益</b>（2024+ OOS，hold10）："
             + " / ".join(parts) + " · 2024 为策略回撤年，注意环境闸门</div>")

    h.append("<h2>候选 Top 榜（仅 Top 10% 及以上）</h2>")
    h.append("<table class='tbl'><thead><tr><th>#</th>")
    for _col, cn, _cls in TABLE_COLS:
        h.append(f"<th>{escape(cn)}</th>")
    h.append("</tr></thead><tbody>")
    for i, r in enumerate(rows, 1):
        h.append(f"<tr><td>{i}</td>")
        for col, _cn, cls in TABLE_COLS:
            v = r.get(col)
            if col == "proba":
                cell = f"<span class='proba'>{v:.3f}</span> <span style='color:#888'>({v*100:.0f}%)</span>"
            elif col == "tier":
                t = _tier_of(r.get("proba") or 0.0, thresholds)
                cell = (f"<span style='color:{t[3]};font-weight:700'>{escape(t[0])}</span>"
                        if t else "<span style='color:#bbb'>—</span>")
            elif col in ("ts_code", "name"):
                cell = escape(str(v) if v is not None else "")
            else:
                cell = _fmt(col, v)
            h.append(f"<td class='{cls}'>{cell}</td>")
        h.append("</tr>")
    h.append("</tbody></table>")

    if sv is not None:
        h.append("<h2>各入选票的因子分解（SHAP）</h2>")
        for i, r in enumerate(rows):
            contribs = sorted(
                [(f, float(sv[i][j]), X.iloc[i][f]) for j, f in enumerate(feat_cols)],
                key=lambda t: -abs(t[1]))
            pos = [(f, v, x) for f, v, x in contribs if v > 0][:5]
            neg = [(f, v, x) for f, v, x in contribs if v < 0][:5]
            h.append("<div class='card'>")
            h.append(f"<h3>#{i+1} {escape(r['ts_code'])} {escape(r['name'] or '')} "
                     f"<span class='proba'>{r['proba']*100:.0f}%</span></h3>")
            h.append("<div class='row2'>")
            for lab, items, cls, sc in [("✅ 模型看好", pos, "pos", "p"),
                                        ("⚠️ 模型警惕", neg, "neg", "n")]:
                h.append(f"<div class='col {cls}'><h4>{lab}</h4>")
                if not items:
                    h.append("<div class='f' style='color:#999'>（无显著）</div>")
                for f, v, x in items:
                    cn, tip = FACTOR_CN.get(f, (f, ""))
                    h.append(f"<div class='f'><span class='name'>{escape(cn)}</span>"
                             f"<span class='v'>= {_fmt(f, x)}</span>"
                             f"<span class='ctr {sc}'>{v:+.3f}</span>"
                             + (f"<span class='tip'>{escape(tip)}</span>" if tip else "") + "</div>")
                h.append("</div>")
            h.append("</div></div>")

    h.append(f"<div class='meta' style='margin-top:24px'>生成 {datetime.now():%Y-%m-%d %H:%M:%S} · "
             f"{escape(MODEL_PATH)} · 波段 v2</div></body></html>")
    return "".join(h)


def score(date, top_n):
    """主流程：候选 → 广度 → 打分 → Top10% → HTML。"""
    model, feat_cols, bundle = _load_model()
    thresholds = _thresholds(bundle)
    auc = (bundle.get("meta") or {}).get("auc", 0.0)

    con = _duckdb.connect(DUCK_PATH, read_only=True)
    try:
        if date is None:
            date = _latest_date(con)
            print(f"未指定 --date，取最新交易日 {date}")
        breadth = _breadth(con, date)
    finally:
        con.close()

    gate_ok = breadth is not None and breadth >= BREADTH_THR
    print(f"市场广度 {breadth*100:.1f}%  闸门：{'放行' if gate_ok else '建议空仓'}")

    cand = scan(date, date, "both")
    print(f"第一层候选 {len(cand)} 只", flush=True)
    if cand.empty:
        print("今日无候选。")
        return

    feat = build_feature_matrix(cand[["ts_code", "trade_date", "mode"]], require_label=False)
    if feat.empty:
        print("特征构建失败。")
        return

    feat["proba"] = model.predict_proba(feat[feat_cols])[:, 1]
    names = _names(_duckdb.connect(DUCK_PATH, read_only=True), feat["ts_code"].tolist())
    feat["name"] = feat["ts_code"].map(names).fillna("")
    feat = feat.sort_values("proba", ascending=False).reset_index(drop=True)
    n_total = len(feat)

    top10_thr = next((thr for thr, label, *_ in thresholds if "10%" in label), None)
    if top10_thr is not None:
        feat = feat[feat["proba"] >= top10_thr].reset_index(drop=True)
    print(f"Top10% 及以上候选 {len(feat)} 只（proba≥{top10_thr:.4f}）")
    if feat.empty:
        print("今日无 Top10% 候选，跳过 HTML。")
        return

    top = feat.head(top_n).reset_index(drop=True)
    rows = top.to_dict(orient="records")
    sv, base = _shap(model, top, feat_cols)

    title = f"【波段·v2】趋势存续概率打分 {date}"
    html = _render(title, date, n_total, breadth, gate_ok, rows, sv, feat_cols,
                   base or 0.0, top[feat_cols], thresholds, auc)
    out = os.path.join(os.path.dirname(__file__), f"ml_score_swing_v2_{date}.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nHTML 已生成: {out}")
    for i, r in enumerate(rows[:10], 1):
        print(f"  {i:2d}. {r['ts_code']} {r['name']:<8s} proba={r['proba']:.3f}  "
              f"{'突破' if r['mode_breakout']>=.5 else '回调'}")
    try:
        webbrowser.open("file://" + os.path.abspath(out))
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser(description="波段趋势存续概率打分（HTML）")
    ap.add_argument("--date", default=None, help="交易日 YYYYMMDD（默认最新）")
    ap.add_argument("--top", type=int, default=15)
    a = ap.parse_args()
    score(a.date, a.top)


if __name__ == "__main__":
    main()
