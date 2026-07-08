"""
ml_score_2lb_v6.py — 每日盘后 2 板个股「2进4（到4板）」概率打分 v6（实盘部署模型）

用 xgb_2lb_v6_deploy.pkl（31 特征，特征同 ml_features_2lb_v3）：由 2lb_model_v6_deploy.py
产出——锁定 v6 评估末折轮数、喂 ≤今天全部数据训练，是实盘最强 2进4 模型。
分位档切点沿用 v6 评估（全样本外合并标定）。
需先用 cache_tushare.py 补齐 stk_auction_o/c 数据，并先跑 2lb_model_v6_deploy.py 生成部署 pkl。

proba = P(boards>=4)，即 P(2进4·到4板)，用于排序 + 信号过滤。
（注意：与 v5 的 proba=P(3板成功) 含义不同，v6 预测的是更高的 4连板。）

复用 ml_score_2lb_v5 的全部渲染/SHAP/市场状态/仓位建议逻辑，仅替换模型、标签、分位解析。

用法：
  python first10/ml_score_2lb_v6.py                  # 最新交易日
  python first10/ml_score_2lb_v6.py --date 20260508  # 指定历史日期复盘
  python first10/ml_score_2lb_v6.py --top 20

依赖：
  - 模型: first10/model/xgb_2lb_v6_deploy.pkl（先跑 2lb_model_v6_deploy.py）
  - 数据: stock_data_tushare.duckdb，market_state 表
"""

import argparse
import os
import pickle
import sys
import webbrowser

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

import duckdb as _duckdb
from db_loader import _ENV

import ml_score_2lb_v5 as base
from ml_score_2lb_v5 import (
    _classify_market_regime, _suggest_position, _get_names, _get_concepts,
    _get_market_state, _shap_decompose, _latest_trade_date,
    _get_signals_for_date, _render_html, _print_tier_summary, TIER_COLORS,
)

MODEL_PATH = os.path.join(os.path.dirname(__file__), "model", "xgb_2lb_v6_deploy.pkl")
DUCK_PATH = _ENV.get("LOCAL_DUCKDB_PATH", "stock_data_tushare.duckdb")

PROBA_HEADER = "到4板概率(2进4)"
FOOTER_LABEL = "2进4·到4板 v6·部署"


def _load_model():
    """加载 v6 部署模型（2进4 分类）。"""
    if not os.path.exists(MODEL_PATH):
        raise FileNotFoundError(
            f"模型不存在: {MODEL_PATH}\n请先运行 python first10/2lb_model_v6_deploy.py")
    with open(MODEL_PATH, "rb") as f:
        bundle = pickle.load(f)
    return bundle["model"], bundle["feature_cols"], bundle


def _resolve_tiers(bundle):
    """解析 v6 tiers（用 cut/proba，无 q）：返回 (thresholds, hit_rates, auc)。"""
    tiers = bundle.get("tiers") or []
    meta = bundle.get("meta") or {}
    thresholds = []
    for i, t in enumerate(tiers):
        color = TIER_COLORS[i] if i < len(TIER_COLORS) else "#888"
        win = t["win"] if i < 3 else None
        thresholds.append((t["proba"], t["label"], win, color))
    hit = {"base": meta.get("base", 0.0)}
    for k, t in zip(["top1pct", "top5pct", "top10pct"], tiers):
        hit[k] = t["win"]
    return thresholds, hit, meta.get("oos_auc", meta.get("auc", 0.0))


def score(trade_date, top_n):
    """主流程：取 2 板信号 → v6 预测 2进4 概率 → 排序 → SHAP → HTML。"""
    base.MODEL_PATH = MODEL_PATH
    model, feat_cols, bundle = _load_model()
    thresholds, hit_rates, auc = _resolve_tiers(bundle)
    con = _duckdb.connect(DUCK_PATH, read_only=True)
    try:
        if trade_date is None:
            trade_date = _latest_trade_date(con)
            print(f"未指定 --date，自动取最新交易日 {trade_date}")
        sig = _get_signals_for_date(con, trade_date)
        if sig.empty:
            print(f"[{trade_date}] 未找到 2 板信号。")
            return
        market_state = _get_market_state(con, trade_date)
        names = _get_names(con, sig["ts_code"].tolist())
        concepts = _get_concepts(con, sig["ts_code"].tolist())
    finally:
        con.close()

    regime_info = _classify_market_regime(market_state)
    print(f"市场状态：{regime_info[0]}")
    print(f"找到 {len(sig)} 个 2 板信号，构建特征...", flush=True)

    from ml_features_2lb_v3 import build_feature_matrix
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

    top10_thr = next((thr for thr, label, *_ in thresholds if "10%" in label), None)
    if top10_thr is not None:
        feat = feat[feat["proba"] >= top10_thr].reset_index(drop=True)
    print(f"Top 10% 及以上候选 {len(feat)} 只（proba ≥ {top10_thr:.4f}）", flush=True)
    if feat.empty:
        print(f"今日无 Top 10% 及以上候选，跳过 HTML。全部 {len(feat_all)} 只信号 proba：")
        for r in feat_all.to_dict(orient="records"):
            print(f"  · {r['ts_code']} {r.get('name') or '':<8s} proba={r['proba']:.3f}")
        return

    top = feat.head(top_n).reset_index(drop=True)
    rows = top.to_dict(orient="records")
    regime_color = regime_info[1]
    for i, r in enumerate(rows, 1):
        r["_posn"] = _suggest_position(i, regime_color)

    sv, base_val = _shap_decompose(model, top, feat_cols)
    title = f"【2进4·到4板·v6·部署】晋级概率打分 {trade_date}"
    html = _render_html(title, trade_date, n_signals, regime_info, market_state,
                        rows, sv, feat_cols, base_val or 0.0, top[feat_cols],
                        thresholds, hit_rates, auc,
                        proba_header=PROBA_HEADER, footer_label=FOOTER_LABEL,
                        model_path=MODEL_PATH)

    out_html = os.path.join(os.path.dirname(__file__), "html_output", f"ml_score_2lb_v6_{trade_date}.html")
    os.makedirs(os.path.dirname(out_html), exist_ok=True)
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nHTML 报告已生成: {out_html}")
    _print_tier_summary(trade_date, rows, thresholds)
    try:
        webbrowser.open("file://" + os.path.abspath(out_html))
    except Exception:
        pass


def score_data(trade_date=None, top_n=20):
    """同 score() 的数据层,但返回 JSON 可序列化 dict(供 webapp 后端),不出 HTML/SHAP/浏览器。"""
    base.MODEL_PATH = MODEL_PATH
    model, feat_cols, bundle = _load_model()
    thresholds, hit_rates, auc = _resolve_tiers(bundle)
    con = _duckdb.connect(DUCK_PATH, read_only=True)
    try:
        if trade_date is None:
            trade_date = _latest_trade_date(con)
        trade_date = str(trade_date)
        sig = _get_signals_for_date(con, trade_date)
        if sig.empty:
            return {"ok": True, "date": trade_date, "n_signals": 0, "signals": [], "note": "该日无 2 板信号"}
        market_state = _get_market_state(con, trade_date)
        names = _get_names(con, sig["ts_code"].tolist())
        concepts = _get_concepts(con, sig["ts_code"].tolist())
    finally:
        con.close()
    regime = _classify_market_regime(market_state)
    from ml_features_2lb_v3 import build_feature_matrix
    feat = build_feature_matrix(sig[["ts_code", "trade_date"]], require_label=False)
    if feat.empty:
        return {"ok": False, "error": "特征构建失败", "date": trade_date}
    feat["proba"] = model.predict_proba(feat[feat_cols].copy())[:, 1]
    feat["concepts"] = feat["ts_code"].map(concepts).fillna("")
    feat = feat.merge(sig[["ts_code", "name_at_date"]], on="ts_code", how="left")
    feat["name"] = feat["name_at_date"].fillna(feat["ts_code"].map(names)).fillna("")
    feat = feat.sort_values("proba", ascending=False).reset_index(drop=True)
    top10_thr = next((thr for thr, label, *_ in thresholds if "10%" in label), None)

    def _tier(p):
        for thr, label, *_ in thresholds:
            if p >= thr:
                return label
        return None

    sigs, rank = [], 0
    for _, r in feat.iterrows():
        p = float(r["proba"])
        istop = top10_thr is not None and p >= top10_thr
        posn = None
        if istop:
            rank += 1
            posn = _suggest_position(rank, regime[1])[0]
        sigs.append({"ts_code": r["ts_code"], "name": r.get("name") or "", "proba": round(p, 4),
                     "tier": _tier(p), "top10": bool(istop), "rank": rank if istop else None,
                     "posn": posn, "concepts": r.get("concepts") or ""})
    ms = {k: (float(v) if isinstance(v, (int, float, np.floating)) and not isinstance(v, bool) else v)
          for k, v in dict(market_state or {}).items()}
    return {"ok": True, "date": trade_date, "n_signals": int(len(feat)),
            "regime": {"label": regime[0], "color": regime[1], "msg": regime[2]},
            "market_state": ms, "auc": round(float(auc), 4),
            "tiers": [{"proba": round(float(thr), 4), "label": label, "win": (rest[0] if rest else None)}
                      for thr, label, *rest in thresholds],
            "top10_thr": None if top10_thr is None else round(float(top10_thr), 4),
            "signals": sigs}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="2 板→4 板(2进4)晋级概率打分（HTML 报告）")
    parser.add_argument("--date", default=None, help="交易日 YYYYMMDD（默认 daily 最大日期）")
    parser.add_argument("--top", type=int, default=15, help="展示 top N 条（默认 15）")
    parser.add_argument("--json-out", default=None, help="把打分结果写成 JSON 到该路径(供 webapp 后端),不出 HTML")
    args = parser.parse_args()
    if args.json_out:
        import json
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(score_data(args.date, args.top), f, ensure_ascii=False)
        print(f"JSON 已写: {args.json_out}")
    else:
        score(args.date, args.top)
