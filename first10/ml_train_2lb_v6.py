"""
ml_train_2lb_v6.py — 二板→四板（2进4）晋级模型 v6（walk-forward 评估版）

只预测一件事：某只 2板，最终能否走到 4连板（boards>=4）。二分类。
特征沿用 ml_features_2lb_v3（31 特征），评估方式沿用 v5 的 walk-forward 滚动前推
+ 过拟合体检 + 分位档标定。

时点口径：T=首板、T+1=2板（信号日，模型于此盘后打分）、买点=T+2=2板次日开盘。

label：该 2板所在连板序列最终高度 boards>=4 → 1，否则 0。

收益口径（智能止盈，与 screen_2lb_top1_t7_v2 一致，仅用于报告各档实收）：
  买点 = 2板次日(T+2)开盘；
  止步2板(炸板) → 次日(2板+2)开盘卖；
  连板 → 一直持有到断板当日开盘卖（默认对续板完美预判）；
  real_ret = sell_real / buy_open - 1。

样本外汇总报告各分位档的【平均实收(智能止盈) / 胜率 / 到3板率 / 到4板率】。

产出：
  model/xgb_2lb_v6.pkl              最后一折模型 + 分位切点 + meta
  model/oos_predictions_2lb_v6.csv
  model/shap_summary_2lb_v6.png

用法：python ml_train_2lb_v6.py
依赖：与 v5 相同（先用 cache_tushare.py 补齐 stk_auction_o/c）。
部署提示：上实盘应另用「≤今天全部数据 + 锁定轮数」重训部署版，不在本文件内。
"""

import os
import sys
import pickle
import time
import warnings
import logging

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

import xgboost as xgb

try:
    import shap
except ImportError:
    shap = None
    logger.warning("shap 未安装，跳过 SHAP 分析。pip install shap")

from sklearn.metrics import roc_auc_score

MODEL_DIR = os.path.join(os.path.dirname(__file__), "model")
MODEL_PATH = os.path.join(MODEL_DIR, "xgb_2lb_v6.pkl")
SHAP_IMG = os.path.join(MODEL_DIR, "shap_summary_2lb_v6.png")
OOS_CSV = os.path.join(MODEL_DIR, "oos_predictions_2lb_v6.csv")
CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

FOLDS = [
    {"train_end": "20201231", "val": ("20210101", "20211231"), "test": ("20220101", "20221231")},
    {"train_end": "20211231", "val": ("20220101", "20221231"), "test": ("20230101", "20231231")},
    {"train_end": "20221231", "val": ("20230101", "20231231"), "test": ("20240101", "20241231")},
    {"train_end": "20231231", "val": ("20240101", "20241231"), "test": ("20250101", "20991231")},
]

TIER_DEFS = [(0.01, "Top 1%"), (0.05, "Top 5%"), (0.10, "Top 10%"), (0.20, "Top 20%")]

_SIGNAL_SQL = """
SELECT
    l.ts_code,
    strftime(l.trade_date, '%Y%m%d') AS trade_date
FROM limit_list_d l
LEFT JOIN stock_st st
                 ON st.ts_code = l.ts_code AND st.trade_date = l.trade_date
WHERE l.limit_type   = 'U'
  AND l.limit_times  = 2
  AND l.ts_code NOT LIKE '688%'
  AND l.ts_code NOT LIKE '30%'
  AND l.ts_code NOT LIKE '%.BJ'
  AND st.ts_code IS NULL
ORDER BY l.trade_date, l.ts_code
"""

_OUTCOME_SQL = """
WITH sig AS (
    SELECT ts_code, CAST(strptime(trade_date, '%Y%m%d') AS DATE) AS d2, trade_date AS d2_str
    FROM sig_raw
),
flagged AS (
    SELECT ts_code, trade_date, limit_times,
           CASE WHEN limit_times = LAG(limit_times) OVER w + 1 THEN 0 ELSE 1 END AS brk
    FROM limit_list_d WHERE limit_type = 'U'
    WINDOW w AS (PARTITION BY ts_code ORDER BY trade_date)
),
grp AS (
    SELECT ts_code, trade_date, limit_times,
           SUM(brk) OVER (PARTITION BY ts_code ORDER BY trade_date) AS gid
    FROM flagged
),
maxg AS (SELECT ts_code, gid, MAX(limit_times) AS boards FROM grp GROUP BY ts_code, gid),
d2b AS (
    SELECT g.ts_code, g.trade_date, m.boards
    FROM grp g JOIN maxg m ON g.ts_code = m.ts_code AND g.gid = m.gid
    WHERE g.limit_times = 2
),
sb AS (
    SELECT sig.ts_code, sig.d2, sig.d2_str, d2b.boards
    FROM sig JOIN d2b ON d2b.ts_code = sig.ts_code AND d2b.trade_date = sig.d2
),
fut AS (
    SELECT sb.ts_code, sb.d2_str, sb.boards, d.open,
           ROW_NUMBER() OVER (PARTITION BY sb.ts_code, sb.d2 ORDER BY d.trade_date) - 1 AS off
    FROM sb JOIN daily d ON d.ts_code = sb.ts_code AND d.trade_date >= sb.d2
    QUALIFY off <= 30
),
agg AS (
    SELECT ts_code, d2_str, boards,
           MAX(CASE WHEN off = 1 THEN open END) AS buy_open,
           MAX(CASE WHEN off = 2 THEN open END) AS sell_loser,
           MAX(CASE WHEN off = boards - 1 THEN open END) AS sell_winner
    FROM fut GROUP BY ts_code, d2_str, boards
)
SELECT ts_code, d2_str AS trade_date, boards, buy_open,
       CASE WHEN boards <= 2 THEN sell_loser ELSE sell_winner END AS sell_real
FROM agg
"""


def _get_signals():
    """扫描 2 板信号（limit_times=2，非 ST，剔除 创业/科创/北交所），按日期缓存。"""
    import duckdb as _duckdb
    from db_loader import _ENV
    duck_path = _ENV.get("LOCAL_DUCKDB_PATH", "stock_data_tushare.duckdb")

    con = _duckdb.connect(duck_path, read_only=True)
    try:
        latest = con.execute("SELECT strftime(MAX(trade_date), '%Y%m%d') FROM daily").fetchone()[0]
        cache_path = os.path.join(CACHE_DIR, f"signals_2lb_{latest}.pkl")
        if os.path.exists(cache_path):
            df = pd.read_pickle(cache_path)
            print(f"信号缓存命中: {cache_path} ({len(df)} rows)", flush=True)
            return df
        print("扫描 2 板信号 ...", flush=True)
        signal_df = con.execute(_SIGNAL_SQL).df()
        print(f"  {len(signal_df)} 个信号", flush=True)
    finally:
        con.close()
    if signal_df.empty:
        raise RuntimeError("未找到任何 2 板信号。")
    signal_df.to_pickle(cache_path)
    return signal_df


def _attach_outcomes(feat_df):
    """用智能止盈口径附加 boards / buy_open / sell_real / real_ret（买2板次日开盘）。"""
    import duckdb as _duckdb
    from db_loader import _ENV
    duck_path = _ENV.get("LOCAL_DUCKDB_PATH", "stock_data_tushare.duckdb")
    con = _duckdb.connect(duck_path, read_only=True)
    try:
        con.execute("SET threads TO 1")  # WHY: 与特征构建一致，保证逐次可复现
        con.register("sig_raw", feat_df[["ts_code", "trade_date"]])
        out = con.execute(_OUTCOME_SQL).df()
    finally:
        con.close()
    out["real_ret"] = np.where(
        (out["buy_open"] > 0) & out["sell_real"].notna(),
        out["sell_real"] / out["buy_open"] - 1.0, np.nan)
    return out[["ts_code", "trade_date", "boards", "real_ret"]]


def _attach_labels(feat_df):
    """合并智能止盈结果，挂上 2进4 标签 y=(boards>=4) 及板高/实收列。"""
    out = _attach_outcomes(feat_df)
    d = feat_df.merge(out, on=["ts_code", "trade_date"], how="inner")
    d["hit3"] = (d["boards"] >= 3).astype(float)
    d["hit4"] = (d["boards"] >= 4).astype(float)
    d["y"] = d["hit4"]
    return d


def _make_clf(pos_ratio):
    """2进4 二分类模型，与 v5 同参，单线程保证可复现。"""
    return xgb.XGBClassifier(
        n_estimators=650, max_depth=4, learning_rate=0.037,
        min_child_weight=15, subsample=0.8, colsample_bytree=0.8,
        scale_pos_weight=pos_ratio, eval_metric="auc",
        early_stopping_rounds=80, random_state=42, n_jobs=1, verbosity=0,
        tree_method="exact",
    )


def _tier_table(score, sub):
    """按 score 降序取各分位档，返回 [(label, cut, n, 平均实收, 胜率, 到3, 到4)]。"""
    score = np.asarray(score)
    order = np.argsort(score)[::-1]
    rr = sub["real_ret"].to_numpy(dtype=float)
    h3 = sub["hit3"].to_numpy(dtype=float)
    h4 = sub["hit4"].to_numpy(dtype=float)
    rows = []
    for q, lab in TIER_DEFS:
        n = max(1, int(len(score) * q))
        idx = order[:n]
        rows.append((lab, float(score[idx][-1]), n,
                     float(np.nanmean(rr[idx])),
                     float(np.nanmean(rr[idx] > 0)),
                     float(np.nanmean(h3[idx])),
                     float(np.nanmean(h4[idx]))))
    return rows


def train(signal_df=None, tag="v6"):
    """walk-forward 训练入口：逐折样本外预测 → 汇总（实收/胜率/板率）→ 标定切点 → 存模型 + SHAP。

    signal_df：自定义 2 板信号集（None 则用全量 _get_signals）；tag：产出文件名后缀（默认 v6）。
    """
    from ml_features_2lb_v3 import build_feature_matrix, FEATURE_COLS, FEATURE_CN
    t_total = time.time()
    model_path = os.path.join(MODEL_DIR, f"xgb_2lb_{tag}.pkl")
    shap_img = os.path.join(MODEL_DIR, f"shap_summary_2lb_{tag}.png")
    oos_csv = os.path.join(MODEL_DIR, f"oos_predictions_2lb_{tag}.csv")

    print("=== 获取 2 板信号 ===")
    if signal_df is None:
        signal_df = _get_signals()

    print("\n=== 构建特征矩阵 ===")
    feat_df = build_feature_matrix(signal_df)
    data = _attach_labels(feat_df)
    print(f"特征矩阵（2进4，有效样本）：{data.shape}")
    print(f"  到4板正例率：{data['y'].mean():.2%}")

    oos_parts, fold_meta = [], []
    last_model = last_test = None

    print("\n=== walk-forward 逐折训练 ===")
    for i, fd in enumerate(FOLDS, 1):
        tr = data[data["trade_date"] <= fd["train_end"]]
        va = data[(data["trade_date"] >= fd["val"][0]) & (data["trade_date"] <= fd["val"][1])]
        te = data[(data["trade_date"] >= fd["test"][0]) & (data["trade_date"] <= fd["test"][1])]
        if len(tr) == 0 or len(va) == 0 or len(te) == 0:
            print(f"  折{i} 跳过（某段为空）：train={len(tr)} val={len(va)} test={len(te)}")
            continue

        pos_ratio = float((tr["y"] == 0).sum()) / max(float((tr["y"] == 1).sum()), 1)
        model = _make_clf(pos_ratio)
        model.fit(tr[FEATURE_COLS], tr["y"],
                  eval_set=[(va[FEATURE_COLS], va["y"])], verbose=False)

        s_te = model.predict_proba(te[FEATURE_COLS])[:, 1]
        s_tr = model.predict_proba(tr[FEATURE_COLS])[:, 1]
        part = te[["ts_code", "trade_date", "y", "real_ret", "hit3", "hit4"]].copy()
        part["score"] = s_te
        part["fold"] = i
        oos_parts.append(part)

        bi = int(model.best_iteration) if model.best_iteration is not None else None
        te_auc = roc_auc_score(te["y"], s_te)
        tr_auc = roc_auc_score(tr["y"], s_tr)
        fold_meta.append({"fold": i, "best_iter": bi, "auc": float(te_auc), "train_auc": float(tr_auc)})
        print(f"  折{i}: train≤{fd['train_end']}({len(tr)})  test {fd['test'][0][:4]}~({len(te)})  "
              f"best_iter={bi}  train_AUC={tr_auc:.4f}  test_AUC={te_auc:.4f}  gap={tr_auc-te_auc:+.4f}")
        last_model, last_test = model, te

    if not oos_parts:
        raise RuntimeError("没有任何有效折。")

    oos = pd.concat(oos_parts, ignore_index=True)
    oos["year"] = oos["trade_date"].str[:4]

    overall_auc = roc_auc_score(oos["y"], oos["score"])
    mean_train_auc = float(np.mean([f["train_auc"] for f in fold_meta]))
    print(f"\n=== 样本外汇总（{oos['year'].min()}~{oos['year'].max()}，{len(oos)} 样本）===")
    print(f"  合并 AUC = {overall_auc:.4f}")
    print(f"  过拟合体检：各折训练 AUC 均值 {mean_train_auc:.4f}  vs  样本外 {overall_auc:.4f}  "
          f"gap={mean_train_auc-overall_auc:+.4f}（<0.05健康 / 0.05~0.10可接受 / >0.15警惕）")
    print(f"  全样本基准：平均实收 {oos['real_ret'].mean():+.2%}  到3板 {oos['hit3'].mean():.1%}  "
          f"到4板 {oos['hit4'].mean():.1%}")

    print(f"\n  按年 · 分位档实际收益（按 score 排序）：")
    print(f"  {'年份':<6s}{'样本':>6s}  Top1%(实收/到4)  Top5%(实收/到4)  Top10%(实收/到4)")
    for yr, sub in oos.groupby("year"):
        rows = _tier_table(sub["score"], sub)
        (_, _, _, r1, _, _, h41) = rows[0]
        (_, _, _, r5, _, _, h45) = rows[1]
        (_, _, _, r10, _, _, h410) = rows[2]
        print(f"  {yr:<6s}{len(sub):>6d}   {r1:+6.1%}/{h41:4.0%}     {r5:+6.1%}/{h45:4.0%}     {r10:+6.1%}/{h410:4.0%}")

    rows = _tier_table(oos["score"], oos)
    print(f"\n分位档切点（全部样本外合并标定）：")
    print(f"  {'档位':<8s}{'proba≥':>9s}{'平均实收':>10s}{'胜率':>8s}{'到3板':>8s}{'到4板':>8s}{'n':>7s}")
    tiers = []
    for lab, cut, n, rr, win, h3, h4 in rows:
        print(f"  {lab:<8s}{cut:>9.4f}{rr:>9.2%}{win:>8.1%}{h3:>8.1%}{h4:>8.1%}{n:>7d}")
        tiers.append({"label": lab, "cut": cut, "proba": cut, "n": n,
                      "ret": rr, "win": win, "hit3": h3, "hit4": h4})

    meta = {"target": "u4_2to4", "oos_auc": float(overall_auc),
            "base": float(oos["hit4"].mean()),
            "n_oos": int(len(oos)), "folds": fold_meta,
            "saved_model": "last_fold(≤20231231)",
            "tier_calib": f"walk-forward OOS {oos['year'].min()}~{oos['year'].max()}"}

    os.makedirs(MODEL_DIR, exist_ok=True)
    with open(model_path, "wb") as f:
        pickle.dump({"model": last_model, "feature_cols": FEATURE_COLS,
                     "tiers": tiers, "meta": meta, "is_reg": False}, f)
    print(f"\n模型已保存（最后一折，≤2023 训练）: {model_path}")
    oos.to_csv(oos_csv, index=False, encoding="utf-8-sig")
    print(f"样本外预测明细: {oos_csv}")

    if shap is not None and last_model is not None:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            explainer = shap.TreeExplainer(last_model)
            shap_values = explainer.shap_values(last_test[FEATURE_COLS])
            plt.figure(figsize=(10, 8))
            shap.summary_plot(shap_values, last_test[FEATURE_COLS], show=False)
            plt.tight_layout()
            plt.savefig(shap_img, dpi=150, bbox_inches="tight")
            plt.close()
            mean_shap = np.abs(shap_values).mean(axis=0)
            ranking = sorted(zip(FEATURE_COLS, mean_shap), key=lambda x: -x[1])
            print(f"\nTop 因子（mean |SHAP|）：  SHAP 图: {shap_img}")
            for r, (fc, v) in enumerate(ranking, 1):
                print(f"  {r:2d}. {fc:<26s}  {v:.4f}  {FEATURE_CN.get(fc, '')}")
        except Exception as e:
            print(f"SHAP 失败: {e}")

    print(f"\n=== 总耗时: {time.time()-t_total:.2f}s ===")


if __name__ == "__main__":
    train()
