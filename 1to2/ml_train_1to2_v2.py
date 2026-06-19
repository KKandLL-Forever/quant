"""
ml_train_1to2_v2.py — XGBoost 训练：首板→二板 晋升概率模型 v2（walk-forward 滚动前推）

与 v1 的区别只在「评估方式」，特征完全沿用 ml_features_1to2_v1（34 特征）。
v1 是单一切分（train≤2023 / test≥2024），且把测试集直接拿来早停，存在轻度泄漏、测试集偏小。
v2 改用滚动前推：每个测试年都由「它之前的全部历史」训练 + 前一年早停，逐年样本外预测，
合并后得到 2022~2026 一条零泄漏、跨熊牛的诚实业绩曲线。

各折（train 用各自全部过去 / val 选轮数 / test 干净样本外）：
  ≤2020 / 2021 / 2022
  ≤2021 / 2022 / 2023
  ≤2022 / 2023 / 2024
  ≤2023 / 2024 / 2025~2026

信号：limit_list_d.limit_times = 1（首板，非 ST，剔除 创业/科创/北交所）
label：T+1 pct_chg >= 9.8（次日封板=2 板成功，二分类）

产出：
  model/xgb_1lb_2lb_v2.pkl       —— 保存「最后一折」模型（≤2023 训练，最新、用数据最多），
                                     分位档切点用全部样本外预测（2022~2026）合并标定，跨行情更耐用。
  model/shap_summary_1to2_v2.png
  model/oos_predictions_1to2_v2.csv —— 每年样本外 proba+label，供进一步分析。

进出场假设：T 日盘后选 → T+1 开盘买 → T+1 收盘看是否封板。
依赖：先用 cache_tushare.py 补齐 stk_auction_o / stk_auction_c 历史数据。

部署提示：上实盘的模型应另用「≤今天全部数据 + 锁定轮数（取最后一折 best_iter）」重训，
见 1to2_model_v2_deploy.py，不在本文件内。
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


MODEL_DIR  = os.path.join(os.path.dirname(__file__), "model")
MODEL_PATH = os.path.join(MODEL_DIR, "xgb_1lb_2lb_v2.pkl")
SHAP_IMG   = os.path.join(MODEL_DIR, "shap_summary_1to2_v2.png")
OOS_CSV    = os.path.join(MODEL_DIR, "oos_predictions_1to2_v2.csv")

FOLDS = [
    {"train_end": "20201231", "val": ("20210101", "20211231"), "test": ("20220101", "20221231")},
    {"train_end": "20211231", "val": ("20220101", "20221231"), "test": ("20230101", "20231231")},
    {"train_end": "20221231", "val": ("20230101", "20231231"), "test": ("20240101", "20241231")},
    {"train_end": "20231231", "val": ("20240101", "20241231"), "test": ("20250101", "20991231")},
]

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
os.makedirs(CACHE_DIR, exist_ok=True)


_SIGNAL_SQL = """
SELECT
    l.ts_code,
    strftime(l.trade_date, '%Y%m%d') AS trade_date
FROM limit_list_d l
LEFT JOIN stock_st st
                 ON st.ts_code = l.ts_code AND st.trade_date = l.trade_date
WHERE l.limit_type   = 'U'
  AND l.limit_times  = 1
  AND l.ts_code NOT LIKE '688%'
  AND l.ts_code NOT LIKE '30%'
  AND l.ts_code NOT LIKE '%.BJ'
  AND st.ts_code IS NULL
ORDER BY l.trade_date, l.ts_code
"""


def _get_signals():
    """扫描全部 1 板信号（limit_times=1，非 ST，剔除 创业/科创/北交所），按日期缓存。"""
    import duckdb as _duckdb
    from db_loader import _ENV
    duck_path = _ENV.get("LOCAL_DUCKDB_PATH", "stock_data_tushare.duckdb")

    con = _duckdb.connect(duck_path, read_only=True)
    try:
        latest = con.execute(
            "SELECT strftime(MAX(trade_date), '%Y%m%d') FROM daily"
        ).fetchone()[0]
        cache_path = os.path.join(CACHE_DIR, f"signals_1to2_{latest}.pkl")

        if os.path.exists(cache_path):
            t = time.time()
            df = pd.read_pickle(cache_path)
            print(f"信号缓存命中: {cache_path} ({time.time()-t:.2f}s, {len(df)} rows)", flush=True)
            return df

        t = time.time()
        print(f"扫描 1 板信号（limit_times=1，非 ST，非创业/科创/北交所）...", flush=True)
        signal_df = con.execute(_SIGNAL_SQL).df()
        print(f"  [signals] SQL: {time.time()-t:.2f}s, {len(signal_df)} 个信号", flush=True)
    finally:
        con.close()

    if signal_df.empty:
        raise RuntimeError("未找到任何 1 板信号。")

    signal_df.to_pickle(cache_path)
    return signal_df


TIER_DEFS = [(0.01, "Top 1%"), (0.05, "Top 5%"), (0.10, "Top 10%"), (0.20, "Top 20%")]


def _compute_tiers(proba, y):
    """各分位档的 proba 切点与胜率。"""
    proba = np.asarray(proba)
    order = np.argsort(proba)[::-1]
    y = np.asarray(y, dtype=float)
    tiers = []
    for q, label in TIER_DEFS:
        n = max(1, int(len(proba) * q))
        idx = order[:n]
        tiers.append({
            "q": q, "label": label,
            "proba": float(proba[idx][-1]),
            "win": float(y[idx].mean()),
            "n": int(n),
        })
    return tiers


def _clean(feat_df):
    """丢掉无 label 的样本。"""
    d = feat_df.dropna(subset=["label"])
    return d[d["label"].isin([0.0, 1.0])]


def _make_model(pos_ratio):
    """构造与 v1 同参的 XGBClassifier，单线程保证可复现。"""
    return xgb.XGBClassifier(
        n_estimators=650,
        max_depth=4,
        learning_rate=0.037,
        min_child_weight=15,
        subsample=0.8,
        colsample_bytree=0.8,
        scale_pos_weight=pos_ratio,
        eval_metric="auc",
        early_stopping_rounds=80,
        random_state=42,
        n_jobs=1,
        verbosity=0,
    )


def _topn_line(proba, y):
    """返回 Top1/5/10/20% 胜率字符串片段。"""
    proba = np.asarray(proba)
    y = np.asarray(y, dtype=float)
    order = np.argsort(proba)[::-1]
    out = []
    for q in [0.01, 0.05, 0.10, 0.20]:
        n = max(1, int(len(proba) * q))
        out.append(y[order[:n]].mean())
    return out


def train():
    """walk-forward 训练入口：逐折训练 → 汇总样本外成绩 → 合并标定切点 → 保存最后一折模型 + SHAP。"""
    from ml_features_1to2_v1 import build_feature_matrix, FEATURE_COLS, FEATURE_CN

    t_total = time.time()

    print("=== 获取 1 板信号 ===")
    signal_df = _get_signals()
    print(f"共 {len(signal_df)} 个信号"
          f"（{signal_df['trade_date'].min()} ~ {signal_df['trade_date'].max()}）")

    print("\n=== 构建特征矩阵 ===")
    feat_df = _clean(build_feature_matrix(signal_df))
    print(f"特征矩阵（有效 label）：{feat_df.shape}")

    oos_parts = []
    fold_meta = []
    last_model = None
    last_test = None

    print("\n=== walk-forward 逐折训练 ===")
    for i, fd in enumerate(FOLDS, 1):
        tr = feat_df[feat_df["trade_date"] <= fd["train_end"]]
        va = feat_df[(feat_df["trade_date"] >= fd["val"][0]) & (feat_df["trade_date"] <= fd["val"][1])]
        te = feat_df[(feat_df["trade_date"] >= fd["test"][0]) & (feat_df["trade_date"] <= fd["test"][1])]
        if len(tr) == 0 or len(va) == 0 or len(te) == 0:
            print(f"  折{i} 跳过（某段为空）：train={len(tr)} val={len(va)} test={len(te)}")
            continue

        pos_ratio = float((tr["label"] == 0).sum()) / max(float((tr["label"] == 1).sum()), 1)
        model = _make_model(pos_ratio)
        model.fit(tr[FEATURE_COLS], tr["label"],
                  eval_set=[(va[FEATURE_COLS], va["label"])], verbose=False)

        proba = model.predict_proba(te[FEATURE_COLS])[:, 1]
        auc = roc_auc_score(te["label"], proba)
        tr_auc = roc_auc_score(tr["label"], model.predict_proba(tr[FEATURE_COLS])[:, 1])
        part = te[["ts_code", "trade_date", "label"]].copy()
        part["proba"] = proba
        part["fold"] = i
        oos_parts.append(part)

        bi = int(model.best_iteration) if model.best_iteration is not None else None
        fold_meta.append({"fold": i, "train_end": fd["train_end"],
                          "test": fd["test"], "n_train": len(tr), "n_test": len(te),
                          "best_iter": bi, "auc": float(auc), "train_auc": float(tr_auc)})
        print(f"  折{i}: train≤{fd['train_end']}({len(tr)})  val {fd['val'][0][:4]}({len(va)})  "
              f"test {fd['test'][0][:4]}~({len(te)})  best_iter={bi}  "
              f"train_AUC={tr_auc:.4f}  test_AUC={auc:.4f}  gap={tr_auc-auc:+.4f}")

        last_model, last_test = model, te

    if not oos_parts:
        raise RuntimeError("没有任何有效折。")

    oos = pd.concat(oos_parts, ignore_index=True)
    oos["year"] = oos["trade_date"].str[:4]

    overall_auc = roc_auc_score(oos["label"], oos["proba"])
    mean_train_auc = float(np.mean([f["train_auc"] for f in fold_meta]))
    print(f"\n=== 样本外汇总（{oos['year'].min()}~{oos['year'].max()}，合并 {len(oos)} 样本）===")
    print(f"  合并 AUC = {overall_auc:.4f}   基准正例率 = {oos['label'].mean():.2%}")
    print(f"  过拟合体检：各折训练 AUC 均值 {mean_train_auc:.4f}  vs  样本外合并 AUC {overall_auc:.4f}  "
          f"gap={mean_train_auc-overall_auc:+.4f}（<0.05健康 / 0.05~0.10可接受 / >0.15警惕）")

    print(f"\n  按年样本外成绩：")
    print(f"  {'年份':<6s}{'样本':>6s}{'基准':>8s}{'AUC':>8s}{'Top1%':>8s}{'Top5%':>8s}{'Top10%':>8s}{'Top20%':>8s}")
    for yr, sub in oos.groupby("year"):
        t1, t5, t10, t20 = _topn_line(sub["proba"], sub["label"])
        yauc = roc_auc_score(sub["label"], sub["proba"]) if sub["label"].nunique() > 1 else float("nan")
        print(f"  {yr:<6s}{len(sub):>6d}{sub['label'].mean():>7.1%}{yauc:>8.3f}"
              f"{t1:>7.1%}{t5:>7.1%}{t10:>7.1%}{t20:>7.1%}")

    tiers = _compute_tiers(oos["proba"], oos["label"])
    print(f"\n分位档切点（基于全部样本外 {oos['year'].min()}~{oos['year'].max()} 合并标定）：")
    for tr_ in tiers:
        print(f"    {tr_['label']:<8s} proba≥{tr_['proba']:.4f}  胜率 {tr_['win']*100:.2f}%  (n={tr_['n']})")

    meta = {"oos_auc": float(overall_auc),
            "base": float(oos["label"].mean()),
            "n_oos": int(len(oos)),
            "folds": fold_meta,
            "saved_model": "last_fold(≤20231231)",
            "tier_calib": f"walk-forward OOS {oos['year'].min()}~{oos['year'].max()}"}

    os.makedirs(MODEL_DIR, exist_ok=True)
    with open(MODEL_PATH, "wb") as f:
        pickle.dump({"model": last_model, "feature_cols": FEATURE_COLS,
                     "tiers": tiers, "meta": meta}, f)
    print(f"\n模型已保存（最后一折，≤2023 训练）: {MODEL_PATH}")

    oos.to_csv(OOS_CSV, index=False, encoding="utf-8-sig")
    print(f"样本外预测明细: {OOS_CSV}")

    if shap is not None and last_model is not None:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            print("\n=== SHAP（基于最后一折测试集）===")
            explainer = shap.TreeExplainer(last_model)
            shap_values = explainer.shap_values(last_test[FEATURE_COLS])
            plt.figure(figsize=(10, 8))
            shap.summary_plot(shap_values, last_test[FEATURE_COLS], show=False)
            plt.tight_layout()
            plt.savefig(SHAP_IMG, dpi=150, bbox_inches="tight")
            plt.close()
            print(f"SHAP 图: {SHAP_IMG}")

            mean_shap = np.abs(shap_values).mean(axis=0)
            ranking = sorted(zip(FEATURE_COLS, mean_shap), key=lambda x: -x[1])
            print("\nTop 因子（mean |SHAP|）：")
            for r, (f, v) in enumerate(ranking, 1):
                print(f"  {r:2d}. {f:<26s}  {v:.4f}  {FEATURE_CN.get(f, '')}")
        except Exception as e:
            print(f"SHAP 失败: {e}")

    print(f"\n=== 总耗时: {time.time()-t_total:.2f}s ===")


if __name__ == "__main__":
    train()
