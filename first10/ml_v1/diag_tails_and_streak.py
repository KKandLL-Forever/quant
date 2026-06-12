"""一次性诊断：(1) Top 5/10/20% 精度  (2) recent_max_lianban 分布。"""
import os, sys, pickle
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
FEAT_CSV = os.path.join(HERE, "model", "feature_matrix.csv")
MODEL_PATH = os.path.join(HERE, "model", "xgb_lianban.pkl")

feat = pd.read_csv(FEAT_CSV, dtype={"trade_date": str})
print(f"feature_matrix: {feat.shape}")

with open(MODEL_PATH, "rb") as f:
    obj = pickle.load(f)
model = obj["model"]
feat_cols = obj["feature_cols"]

test = feat[feat["trade_date"] >= "20240101"].dropna(subset=["label"]).copy()
test = test[test["label"].isin([0.0, 1.0])]
y = test["label"].astype(float).reset_index(drop=True)
proba = model.predict_proba(test[feat_cols])[:, 1]

print(f"\n=== Tail precision ===  test n={len(y)}  pos_rate={y.mean():.4f}")
order = np.argsort(proba)[::-1]
for pct in [0.01, 0.02, 0.05, 0.10, 0.20]:
    n = max(1, int(len(proba) * pct))
    idx = order[:n]
    p = y.iloc[idx].mean()
    lift = p / y.mean() if y.mean() > 0 else float("nan")
    print(f"  Precision@{pct:>4.0%}  n={n:>4d}  precision={p:.4f}  lift={lift:.2f}x")

print(f"\n=== fd_amount_ratio vs label (test set, by quintile) ===")
tg = test.copy()
tg["bucket"] = pd.qcut(tg["fd_amount_ratio"], 5, labels=False, duplicates="drop")
agg = tg.groupby("bucket").agg(n=("label", "size"), pos_rate=("label", "mean"),
                                fd_min=("fd_amount_ratio", "min"),
                                fd_max=("fd_amount_ratio", "max"))
print(agg)

print(f"\n=== concept_lu_count_max vs label (test set, by integer bucket) ===")
tg = test.copy()
tg["bucket"] = tg["concept_lu_count_max"].fillna(0).clip(upper=20).astype(int)
agg = tg.groupby("bucket").agg(n=("label", "size"), pos_rate=("label", "mean"))
print(agg)

print(f"\n=== concept_rank_min vs label (test set, top ranks vs rest) ===")
tg = test.copy()
tg["rank_bucket"] = pd.cut(
    tg["concept_rank_min"].fillna(999),
    bins=[-1, 3, 10, 30, 100, 1000],
    labels=["top3", "top4-10", "top11-30", "top31-100", ">100/null"]
)
agg = tg.groupby("rank_bucket", observed=True).agg(
    n=("label", "size"), pos_rate=("label", "mean"))
print(agg)

print(f"\n=== fd_amount_ratio ≥ 0.0092 AND concept_lu_count_max ≥ 5（叠加过滤）===")
mask = (test["fd_amount_ratio"] >= 0.0092) & (test["concept_lu_count_max"] >= 5)
sub = test[mask]
print(f"  样本数: {len(sub)}  pos_rate: {sub['label'].mean():.2%}  lift: {sub['label'].mean()/test['label'].mean():.2f}x")
