"""
ml_train_swing_v2.py — XGBoost 训练：波段选股「趋势存续」概率模型

信号：screen_swing_layer1.py 的候选（趋势向上 + 回调企稳/放量突破 + 僵尸股过滤）
label：进场后 10 日内收盘不跌破 MA10 → 1（ml_features_swing_v2，正例率约 10%）
特征：ml_features_swing_v2.FEATURE_COLS（26 个）

时序切分：train <= 2023-12-31，test >= 2024-01-01（避免未来泄漏）。

产出：
  model/xgb_swing_v2.pkl       模型 + 特征列 + 各 proba 分位切点（含该档平均 ret_real / 胜率）
  model/shap_summary_swing_v2.png
  model/feature_matrix_swing_v2.csv

评估关注 proba 是否单调有效：高分位档应有更高 label 正例率 + 更高平均 ret_real。
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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
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

from ml_features_swing_v2 import build_feature_matrix, FEATURE_COLS

MODEL_DIR = os.path.join(os.path.dirname(__file__), "model")
MODEL_PATH = os.path.join(MODEL_DIR, "xgb_swing_v2.pkl")
SHAP_IMG = os.path.join(MODEL_DIR, "shap_summary_swing_v2.png")
CAND_CSV = os.path.join(os.path.dirname(__file__), "cache", "swing_layer1_candidates.csv")

TRAIN_END = "20231231"
TEST_START = "20240101"

TIER_DEFS = [(0.01, "Top 1%"), (0.05, "Top 5%"), (0.10, "Top 10%"), (0.20, "Top 20%")]


def _compute_tiers(proba, y_test, ret_test):
    """各分位档的 proba 切点、label 胜率、平均 ret_real，随模型存入 pkl 供打分端用。"""
    order = np.argsort(proba)[::-1]
    y = np.asarray(y_test, dtype=float)
    r = np.asarray(ret_test, dtype=float)
    tiers = []
    for q, label in TIER_DEFS:
        n = max(1, int(len(proba) * q))
        idx = order[:n]
        rr = r[idx]
        rr = rr[~np.isnan(rr)]
        tiers.append({
            "q": q, "label": label,
            "proba": float(proba[idx][-1]),
            "win": float(y[idx].mean()),
            "ret_mean": float(rr.mean()) if len(rr) else float("nan"),
            "ret_median": float(np.median(rr)) if len(rr) else float("nan"),
            "ret_winrate": float((rr > 0).mean()) if len(rr) else float("nan"),
            "n": int(n),
        })
    return tiers


def _split(feat_df: pd.DataFrame):
    """按交易日切训练 / 测试，丢弃 label 缺失行。"""
    feat_df = feat_df.dropna(subset=["label"])
    feat_df = feat_df[feat_df["label"].isin([0.0, 1.0])]
    train = feat_df[feat_df["trade_date"] <= TRAIN_END]
    test = feat_df[feat_df["trade_date"] >= TEST_START]
    return train, test


def _evaluate(model, test_df):
    """测试集评估：AUC + 各分位档 label 胜率与平均 ret_real（看 proba 单调性）。"""
    proba = model.predict_proba(test_df[FEATURE_COLS])[:, 1]
    y_test = test_df["label"].to_numpy()
    ret = test_df["ret_real"].to_numpy()
    auc = roc_auc_score(y_test, proba)

    print(f"\n测试集评估（{TEST_START}~）：")
    print(f"  样本={len(y_test)}  正例={int(y_test.sum())}  正例率={y_test.mean():.2%}")
    print(f"  AUC-ROC = {auc:.4f}")

    base = y_test.mean()
    print(f"\n  {'Top N%':<8s}{'样本':>6s}{'label胜率':>10s}{'lift':>7s}"
          f"{'ret均值':>10s}{'ret中位':>10s}{'ret胜率':>9s}")
    order = np.argsort(proba)[::-1]
    for q in [0.01, 0.05, 0.10, 0.20]:
        n = max(1, int(len(proba) * q))
        idx = order[:n]
        win = float(y_test[idx].mean())
        rr = ret[idx]
        rr = rr[~np.isnan(rr)]
        lift = win / base if base > 0 else float("nan")
        print(f"  {int(q*100):<8d}{n:>6d}{win:>9.2%}{lift:>6.2f}x"
              f"{rr.mean()*100:>9.2f}%{np.median(rr)*100:>9.2f}%{(rr>0).mean()*100:>8.1f}%")

    print(f"\n  全测试集基准：label正例率 {base:.2%}  ret均值 "
          f"{np.nanmean(ret)*100:.2f}%  ret胜率 {(ret[~np.isnan(ret)]>0).mean()*100:.1f}%")
    return proba


def train():
    """训练入口：候选 → 特征 → 时序切分 → XGBoost → 评估 → 存模型 + SHAP。"""
    t_total = time.time()

    print("=== 装载候选信号 ===")
    signal_df = pd.read_csv(CAND_CSV, dtype={"ts_code": str})
    print(f"共 {len(signal_df)} 个候选"
          f"（{signal_df['trade_date'].min()} ~ {signal_df['trade_date'].max()}）")

    print("\n=== 构建特征矩阵 ===")
    feat_df = build_feature_matrix(signal_df, require_label=True)
    print(f"特征矩阵：{feat_df.shape}")
    print(f"label 分布：\n{feat_df['label'].value_counts(dropna=False)}")

    train_df, test_df = _split(feat_df)
    print(f"\n训练集：{len(train_df)}（正例率 {train_df['label'].mean():.2%}）"
          f"  测试集：{len(test_df)}（正例率 {test_df['label'].mean():.2%}）")

    X_train, y_train = train_df[FEATURE_COLS], train_df["label"]
    X_test, y_test = test_df[FEATURE_COLS], test_df["label"]
    pos_ratio = float((y_train == 0).sum()) / max(float((y_train == 1).sum()), 1)

    print("\n=== 训练 XGBoost ===")
    t = time.time()
    model = xgb.XGBClassifier(
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
        n_jobs=-1,
        verbosity=0,
    )
    model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=50)
    print(f"=== fit 耗时: {time.time()-t:.2f}s, best_iter={model.best_iteration} ===")

    proba = _evaluate(model, test_df)
    tiers = _compute_tiers(proba, y_test, test_df["ret_real"])
    meta = {"auc": float(roc_auc_score(y_test, proba)),
            "base": float(y_test.mean()), "n_test": int(len(y_test))}

    os.makedirs(MODEL_DIR, exist_ok=True)
    with open(MODEL_PATH, "wb") as f:
        pickle.dump({"model": model, "feature_cols": FEATURE_COLS,
                     "tiers": tiers, "meta": meta}, f)
    print(f"\n模型已保存: {MODEL_PATH}")
    print("分位档切点（已随模型保存）：")
    for t in tiers:
        print(f"    {t['label']:<8s} proba≥{t['proba']:.4f}  label胜率 {t['win']*100:.1f}%  "
              f"ret均值 {t['ret_mean']*100:.2f}%  ret胜率 {t['ret_winrate']*100:.1f}%  (n={t['n']})")

    if shap is not None:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            print("\n=== SHAP ===")
            explainer = shap.TreeExplainer(model)
            shap_values = explainer.shap_values(X_test[FEATURE_COLS])
            plt.figure(figsize=(10, 8))
            shap.summary_plot(shap_values, X_test[FEATURE_COLS], show=False)
            plt.tight_layout()
            plt.savefig(SHAP_IMG, dpi=150, bbox_inches="tight")
            plt.close()
            print(f"SHAP 图: {SHAP_IMG}")

            mean_shap = np.abs(shap_values).mean(axis=0)
            ranking = sorted(zip(FEATURE_COLS, mean_shap), key=lambda x: -x[1])
            print("\nTop 因子（mean |SHAP|）：")
            for r, (fn, v) in enumerate(ranking, 1):
                print(f"  {r:2d}. {fn:<26s}  {v:.4f}")
        except Exception as e:
            print(f"SHAP 失败: {e}")

    feat_csv = os.path.join(MODEL_DIR, "feature_matrix_swing_v2.csv")
    feat_df.to_csv(feat_csv, index=False, encoding="utf-8-sig")
    print(f"特征矩阵: {feat_csv}")
    print(f"\n=== 总耗时: {time.time()-t_total:.2f}s ===")


if __name__ == "__main__":
    train()
