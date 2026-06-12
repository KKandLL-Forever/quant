"""
ml_train_v2.py — XGBoost 训练 + SHAP（v2 复合标签）

与 v1 的差异：
  - import ml_features_v2（新 label 口径）
  - 模型保存路径：model/xgb_lianban_v2.pkl
  - SHAP 图：model/shap_summary_v2.png
  - 信号缓存沿用（_get_signals 与 v1 相同）
  - 训练流程不变；评估补充：胜率（v1=简单连板率；v2=复合胜率）+ Top 1%/5%/10% 精度

v2 label 口径：
  label = (T+1 pct_chg >= 9.8) AND (T+2 pct_chg > 0)
        = T+1 涨停 AND T+2 收盘 > T+1 收盘

进出场假设：T 日盘后选 → T+1 开盘买 → T+2 收盘卖。
"""

import os
import sys
import pickle
import time
import warnings
import logging

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
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


MODEL_DIR  = os.path.join(os.path.dirname(os.path.dirname(__file__)), "model")
MODEL_PATH = os.path.join(MODEL_DIR, "xgb_lianban_v2.pkl")
SHAP_IMG   = os.path.join(MODEL_DIR, "shap_summary_v2.png")

TRAIN_END = "20231231"
TEST_START = "20240101"

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cache")
os.makedirs(CACHE_DIR, exist_ok=True)


_SIGNAL_SQL = """
WITH d AS (
    SELECT ts_code, trade_date, high, low, vol,
           AVG(vol) OVER (PARTITION BY ts_code ORDER BY trade_date
                          ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING) AS vol_ma5
    FROM daily
)
SELECT
    l.ts_code,
    strftime(l.trade_date, '%Y%m%d') AS trade_date
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
ORDER BY l.trade_date, l.ts_code
"""


def _get_signals():
    """扫描首板小量信号（vol/ma5<1.4，非ST非一字），按日期缓存。"""
    import duckdb as _duckdb
    from db_loader import _ENV
    duck_path = _ENV.get("LOCAL_DUCKDB_PATH", "stock_data_tushare.duckdb")

    con = _duckdb.connect(duck_path, read_only=True)
    try:
        latest = con.execute(
            "SELECT strftime(MAX(trade_date), '%Y%m%d') FROM daily"
        ).fetchone()[0]
        cache_path = os.path.join(CACHE_DIR, f"signals_smallvol_lt1.4_{latest}.pkl")

        if os.path.exists(cache_path):
            t = time.time()
            df = pd.read_pickle(cache_path)
            print(f"信号缓存命中: {cache_path} ({time.time()-t:.2f}s, {len(df)} rows)", flush=True)
            return df, None

        t = time.time()
        print(f"扫描首板小量信号（vol/ma5<1.4，limit_times=1，非ST非一字）...", flush=True)
        signal_df = con.execute(_SIGNAL_SQL).df()
        print(f"  [signals] SQL: {time.time()-t:.2f}s, {len(signal_df)} 个信号", flush=True)
    finally:
        con.close()

    if signal_df.empty:
        raise RuntimeError("未找到任何信号。")

    signal_df.to_pickle(cache_path)
    return signal_df, None


def _split(feat_df: pd.DataFrame):
    feat_df = feat_df.dropna(subset=["label"])
    feat_df = feat_df[feat_df["label"].isin([0.0, 1.0])]
    train = feat_df[feat_df["trade_date"] <= TRAIN_END]
    test  = feat_df[feat_df["trade_date"] >= TEST_START]
    return train, test


def _evaluate(model, X_test, y_test, feat_cols, test_df):
    """评估：AUC + Top N% 胜率 + KS + 分量诊断。"""
    proba = model.predict_proba(X_test[feat_cols])[:, 1]
    auc = roc_auc_score(y_test, proba)

    print(f"\n测试集评估（{TEST_START}~）：")
    print(f"  样本={len(y_test)}  正例={int(y_test.sum())}  正例率={y_test.mean():.2%}")
    print(f"  AUC-ROC = {auc:.4f}")

    print(f"\n  分位数胜率（Top N% 中复合 label=1 的比例）：")
    print(f"  {'N%':<6s}{'样本':>6s}{'胜率':>10s}{'相对基准':>10s}")
    base_rate = y_test.mean()
    for q in [0.01, 0.05, 0.10, 0.20]:
        n = max(1, int(len(proba) * q))
        idx = np.argsort(proba)[::-1][:n]
        win = float(y_test.iloc[idx].mean())
        lift = win / base_rate if base_rate > 0 else float('nan')
        print(f"  {int(q*100):<6d}{n:>6d}{win:>9.2%}{lift:>9.2f}x")

    pos_cdf = np.cumsum(np.sort(proba[y_test == 1])[::-1]) / max((y_test == 1).sum(), 1)
    neg_cdf = np.cumsum(np.sort(proba[y_test == 0])[::-1]) / max((y_test == 0).sum(), 1)
    n = min(len(pos_cdf), len(neg_cdf))
    ks = float(np.max(np.abs(pos_cdf[:n] - neg_cdf[:n]))) if n > 0 else 0.0
    print(f"\n  KS = {ks:.4f}")

    if "next_pct" in test_df.columns and "next2_pct" in test_df.columns:
        d = test_df.copy()
        d["lb_t1"] = (d["next_pct"] >= 9.8).astype(int)
        d["lb_t2pos"] = (d["next2_pct"] > 0).astype(int)
        print(f"\n  分量诊断：")
        print(f"    T+1 涨停率                    = {d['lb_t1'].mean():.2%}")
        print(f"    T+2 收盘 > T+1 收盘 (整体)     = {d['lb_t2pos'].mean():.2%}")
        m = d[d["lb_t1"] == 1]
        if len(m) > 0:
            print(f"    在 T+1 涨停子集内 T+2 溢价率  = {m['lb_t2pos'].mean():.2%}  ({len(m)} 个)")

    return proba


def train():
    """训练入口：装载信号 → 构建特征 → 训练 → 评估 → SHAP。"""
    from ml_features_v2 import build_feature_matrix, FEATURE_COLS

    t_total = time.time()

    print("=== 获取首板小量信号（v2，复合标签）===")
    signal_df, _ = _get_signals()
    print(f"共 {len(signal_df)} 个信号（{signal_df['trade_date'].min()} ~ {signal_df['trade_date'].max()}）")

    print("\n=== 构建特征矩阵 ===")
    feat_df = build_feature_matrix(signal_df)
    print(f"特征矩阵：{feat_df.shape}")
    print(f"label 分布：\n{feat_df['label'].value_counts(dropna=False)}")

    train_df, test_df = _split(feat_df)
    print(f"\n训练集：{len(train_df)}（正例率 {train_df['label'].mean():.2%}）"
          f"  测试集：{len(test_df)}（正例率 {test_df['label'].mean():.2%}）")

    X_train = train_df[FEATURE_COLS]
    y_train = train_df["label"]
    X_test  = test_df[FEATURE_COLS]
    y_test  = test_df["label"]

    pos_ratio = float((y_train == 0).sum()) / max(float((y_train == 1).sum()), 1)

    print("\n=== 训练 XGBoost（M4 调优参数）===")
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

    _evaluate(model, X_test, y_test, FEATURE_COLS, test_df)

    os.makedirs(MODEL_DIR, exist_ok=True)
    with open(MODEL_PATH, "wb") as f:
        pickle.dump({"model": model, "feature_cols": FEATURE_COLS}, f)
    print(f"\n模型已保存: {MODEL_PATH}")

    if shap is not None:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            print("\n=== SHAP ===")
            explainer = shap.TreeExplainer(model)
            shap_values = explainer.shap_values(X_test[FEATURE_COLS])
            plt.figure(figsize=(10, 7))
            shap.summary_plot(shap_values, X_test[FEATURE_COLS], show=False)
            plt.tight_layout()
            plt.savefig(SHAP_IMG, dpi=150, bbox_inches="tight")
            plt.close()
            print(f"SHAP 图: {SHAP_IMG}")

            mean_shap = np.abs(shap_values).mean(axis=0)
            ranking = sorted(zip(FEATURE_COLS, mean_shap), key=lambda x: -x[1])
            print("\nTop 因子（mean |SHAP|）：")
            for r, (f, v) in enumerate(ranking, 1):
                print(f"  {r:2d}. {f:<24s}  {v:.4f}")
        except Exception as e:
            print(f"SHAP 失败: {e}")

    feat_csv = os.path.join(MODEL_DIR, "feature_matrix_v2.csv")
    feat_df.to_csv(feat_csv, index=False, encoding="utf-8-sig")
    print(f"特征矩阵: {feat_csv}")
    print(f"\n=== 总耗时: {time.time()-t_total:.2f}s ===")


if __name__ == "__main__":
    train()
