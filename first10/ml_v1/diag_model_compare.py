"""
模型对照：XGBoost vs LightGBM vs 逻辑回归
- 同一份特征矩阵 / 同一时序切分（train<=2023, test>=2024）
- 目的：验证 AUC ~0.60 是数据上限还是模型上限

用法：python first10/diag_model_compare.py
"""
import os, sys, time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from sklearn.metrics import roc_auc_score
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline

import xgboost as xgb

try:
    import lightgbm as lgb
except ImportError:
    raise SystemExit("缺 lightgbm: pip install lightgbm")

from ml_train import _get_signals
from ml_features import build_feature_matrix, FEATURE_COLS

TRAIN_END = "20231231"
TEST_START = "20240101"


def metrics(y, proba):
    auc = roc_auc_score(y, proba)
    out = {"AUC": auc}
    for q in [0.05, 0.10, 0.20]:
        n = max(1, int(len(proba) * q))
        idx = np.argsort(proba)[::-1][:n]
        out[f"P@{int(q*100)}%"] = float(y.iloc[idx].mean())
    return out


def run_xgb(X_tr, y_tr, X_te, y_te):
    pos = float((y_tr == 0).sum()) / max(float((y_tr == 1).sum()), 1)
    m = xgb.XGBClassifier(
        n_estimators=400, max_depth=4, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        scale_pos_weight=pos, eval_metric="auc",
        early_stopping_rounds=30, random_state=42, n_jobs=-1, verbosity=0,
    )
    t = time.time()
    m.fit(X_tr, y_tr, eval_set=[(X_te, y_te)], verbose=False)
    fit = time.time() - t
    return metrics(y_te, m.predict_proba(X_te)[:, 1]), fit, m.best_iteration


def run_lgb(X_tr, y_tr, X_te, y_te):
    # 不用 scale_pos_weight（LGB 上语义不同），改 is_unbalance
    # 缩小 num_leaves + 降 min_data_in_leaf，避免 leaf-wise 立刻过拟合
    m = lgb.LGBMClassifier(
        n_estimators=800, learning_rate=0.03,
        num_leaves=15, max_depth=4,
        min_data_in_leaf=50,
        subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
        reg_alpha=0.1, reg_lambda=0.1,
        is_unbalance=True,
        random_state=42, n_jobs=-1, verbosity=-1,
    )
    t = time.time()
    m.fit(
        X_tr, y_tr,
        eval_set=[(X_te, y_te)], eval_metric="auc",
        callbacks=[lgb.early_stopping(50, verbose=False)],
    )
    fit = time.time() - t
    return metrics(y_te, m.predict_proba(X_te)[:, 1]), fit, m.best_iteration_


def run_lr(X_tr, y_tr, X_te, y_te):
    pipe = Pipeline([
        ("imp", SimpleImputer(strategy="median")),
        ("sc",  StandardScaler()),
        ("lr",  LogisticRegression(
            penalty="l2", C=1.0, class_weight="balanced",
            solver="lbfgs", max_iter=2000, random_state=42,
        )),
    ])
    t = time.time()
    pipe.fit(X_tr, y_tr)
    fit = time.time() - t
    return metrics(y_te, pipe.predict_proba(X_te)[:, 1]), fit, None


def main():
    print("=== 加载信号 + 特征 ===")
    sig, _ = _get_signals()
    feat = build_feature_matrix(sig)
    feat = feat.dropna(subset=["label"])
    feat = feat[feat["label"].isin([0.0, 1.0])]
    train = feat[feat["trade_date"] <= TRAIN_END]
    test = feat[feat["trade_date"] >= TEST_START]
    X_tr, y_tr = train[FEATURE_COLS], train["label"]
    X_te, y_te = test[FEATURE_COLS], test["label"]
    print(f"train={len(train)}  test={len(test)}  正例率(test)={y_te.mean():.1%}\n")

    results = []
    for name, fn in [("XGBoost", run_xgb), ("LightGBM", run_lgb), ("LogReg", run_lr)]:
        print(f"--- {name} ---")
        m, fit, best = fn(X_tr, y_tr, X_te, y_te)
        m["fit_s"] = fit
        m["best_iter"] = best
        results.append((name, m))
        for k, v in m.items():
            if isinstance(v, float):
                print(f"  {k:8s} = {v:.4f}")
            else:
                print(f"  {k:8s} = {v}")
        print()

    print("=== 汇总 ===")
    print(f"{'模型':<10s} {'AUC':>7s} {'P@5%':>7s} {'P@10%':>7s} {'P@20%':>7s} {'fit(s)':>7s}")
    for name, m in results:
        print(f"{name:<10s} {m['AUC']:>7.4f} {m['P@5%']:>7.4f} {m['P@10%']:>7.4f} {m['P@20%']:>7.4f} {m['fit_s']:>7.2f}")


if __name__ == "__main__":
    main()
