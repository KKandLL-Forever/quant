"""
对照实验：训练窗口 2018+ vs 2021+（注册制时代）
测试集统一为 2024-01-01 之后，确保可比。

用法：python first10/diag_train_window.py
"""
import os, sys, time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

import xgboost as xgb
from sklearn.metrics import roc_auc_score

from ml_train import _get_signals
from ml_features import build_feature_matrix, FEATURE_COLS


TEST_START = "20240101"
TRAIN_END = "20231231"


def run(feat_df: pd.DataFrame, train_start: str, label: str):
    train = feat_df[(feat_df["trade_date"] >= train_start) & (feat_df["trade_date"] <= TRAIN_END)]
    test = feat_df[feat_df["trade_date"] >= TEST_START]
    X_tr, y_tr = train[FEATURE_COLS], train["label"]
    X_te, y_te = test[FEATURE_COLS], test["label"]
    pos_ratio = float((y_tr == 0).sum()) / max(float((y_tr == 1).sum()), 1)

    t = time.time()
    model = xgb.XGBClassifier(
        n_estimators=400, max_depth=4, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        scale_pos_weight=pos_ratio,
        eval_metric="auc", early_stopping_rounds=30,
        random_state=42, n_jobs=-1, verbosity=0,
    )
    model.fit(X_tr, y_tr, eval_set=[(X_te, y_te)], verbose=False)
    fit_t = time.time() - t

    proba = model.predict_proba(X_te)[:, 1]
    auc = roc_auc_score(y_te, proba)

    # Precision @ 各分位
    results = {}
    for q in [0.05, 0.10, 0.20]:
        n = max(1, int(len(proba) * q))
        idx = np.argsort(proba)[::-1][:n]
        results[f"P@{int(q*100)}%"] = float(y_te.iloc[idx].mean())

    print(f"\n=== {label} ===")
    print(f"  训练区间: {train_start} ~ {TRAIN_END}")
    print(f"  训练样本: {len(train):>6d}  正例率: {y_tr.mean():.1%}  best_iter: {model.best_iteration}")
    print(f"  测试样本: {len(test):>6d}  正例率: {y_te.mean():.1%}")
    print(f"  AUC      = {auc:.4f}")
    for k, v in results.items():
        print(f"  {k:8s} = {v:.4f}")
    print(f"  fit time = {fit_t:.2f}s")
    return auc, results


def main():
    print("=== 加载信号 + 特征 ===")
    signal_df, _ = _get_signals()
    feat_df = build_feature_matrix(signal_df)
    feat_df = feat_df.dropna(subset=["label"])
    feat_df = feat_df[feat_df["label"].isin([0.0, 1.0])]
    print(f"特征矩阵: {feat_df.shape}, 信号区间: {feat_df['trade_date'].min()} ~ {feat_df['trade_date'].max()}")

    a1 = run(feat_df, "20180101", "A: 2018-2023 训练（当前）")
    a2 = run(feat_df, "20210101", "B: 2021-2023 训练（注册制）")
    a3 = run(feat_df, "20230101", "C: 2023 训练（极短窗口）")

    print("\n=== 对照汇总 ===")
    print(f"{'方案':<30s} {'AUC':>8s} {'P@5%':>8s} {'P@10%':>8s} {'P@20%':>8s}")
    for label, (auc, r) in [("A 2018+", a1), ("B 2021+", a2), ("C 2023", a3)]:
        print(f"{label:<30s} {auc:>8.4f} {r['P@5%']:>8.4f} {r['P@10%']:>8.4f} {r['P@20%']:>8.4f}")


if __name__ == "__main__":
    main()
