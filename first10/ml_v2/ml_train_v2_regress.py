"""
ml_train_v2_regress.py — 回归版训练（路径 1：换 label 提升 ranking 区分度）

label：T+2_open / T+1_open - 1（T+2 开盘卖时的真实收益率）
模型：XGBRegressor（回归而非分类）
评估：Spearman 排序相关 + Top N 实际平均收益

与 ml_train_v2.py 的差异：
  - label 从二分类 → 连续收益率
  - 模型从 XGBClassifier → XGBRegressor
  - 评估指标从 AUC/KS → Spearman / Top N 实际收益
  - 输出模型保存为 xgb_lianban_v2_regress.pkl
"""

import os
import sys
import pickle
import time
import warnings

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.stdout.reconfigure(encoding="utf-8")

warnings.filterwarnings("ignore")

import xgboost as xgb
import duckdb as _duckdb
from db_loader import _ENV
from scipy.stats import spearmanr

try:
    import shap
except ImportError:
    shap = None


MODEL_DIR  = os.path.join(os.path.dirname(os.path.dirname(__file__)), "model")
MODEL_PATH = os.path.join(MODEL_DIR, "xgb_lianban_v2_regress.pkl")
SHAP_IMG   = os.path.join(MODEL_DIR, "shap_summary_v2_regress.png")
DUCK = _ENV.get("LOCAL_DUCKDB_PATH", "stock_data_tushare.duckdb")

TRAIN_END = "20231231"
TEST_START = "20240101"


def get_returns(con, sig_df: pd.DataFrame) -> pd.DataFrame:
    """根据 (ts_code, trade_date) 查 T+1 open 和 T+2 open，计算回归 label"""
    con.register("sigs_in", sig_df[["ts_code", "trade_date"]])
    df = con.execute("""
        WITH t AS (
            SELECT ts_code, trade_date,
                   LEAD(open, 1) OVER (PARTITION BY ts_code ORDER BY trade_date) AS t1_open,
                   LEAD(open, 2) OVER (PARTITION BY ts_code ORDER BY trade_date) AS t2_open
            FROM daily
        )
        SELECT s.ts_code, s.trade_date, t.t1_open, t.t2_open
        FROM sigs_in s
        JOIN t ON t.ts_code = s.ts_code
              AND t.trade_date = strptime(s.trade_date, '%Y%m%d')
    """).df()
    df["label_reg"] = df["t2_open"] / df["t1_open"] - 1
    return df[["ts_code", "trade_date", "label_reg"]]


def evaluate(model, X_test, y_test, feat_cols, test_df):
    """打印 Spearman / Pearson / Top N% 实际平均收益"""
    pred = model.predict(X_test[feat_cols])
    rho_s, _ = spearmanr(pred, y_test)
    rho_p = float(np.corrcoef(pred, y_test)[0, 1])

    print(f"\n测试集评估（{TEST_START}~）：")
    print(f"  样本={len(y_test)}  实际收益均值={y_test.mean()*100:+.2f}%  std={y_test.std()*100:.2f}%")
    print(f"  Spearman 排序相关 = {rho_s:.4f}（关键：>0.10 即比 v2 分类版好）")
    print(f"  Pearson  线性相关 = {rho_p:.4f}")

    df = test_df.copy()
    df["pred"] = pred

    print(f"\n  分位数 Top N% 中实际平均收益：")
    print(f"  {'N%':<6s}{'样本':>6s}{'实际均值':>12s}{'胜率(>0)':>12s}{'Top 1预测':>12s}")
    for q in [0.01, 0.05, 0.10, 0.20]:
        n = max(1, int(len(pred) * q))
        idx = np.argsort(pred)[::-1][:n]
        sub = y_test.iloc[idx]
        win = float((sub > 0).mean())
        avg = float(sub.mean())
        first_pred = float(pred[idx[0]])
        print(f"  {int(q*100):<6d}{n:>6d}{avg*100:>+11.2f}%{win:>11.2%}{first_pred*100:>+11.2f}%")

    print("\n  Rank 1-20 平均预测 vs 实际收益（看预测排序与实际是否单调）：")
    df_rank = df.assign(pred=pred).sort_values(["trade_date", "pred"], ascending=[True, False])
    df_rank["rank"] = df_rank.groupby("trade_date")["pred"].rank(method="first", ascending=False).astype(int)
    rows = []
    for r in range(1, 21):
        sub = df_rank[df_rank["rank"] == r]
        if len(sub) == 0:
            continue
        rows.append((r, len(sub), float(sub["pred"].mean()), float(sub["label_reg"].mean()),
                    float((sub["label_reg"] > 0).mean())))
    df_show = pd.DataFrame(rows, columns=["rank", "n", "pred_avg", "actual_avg", "win_rate"])
    df_show["pred_avg"]   = df_show["pred_avg"].apply(lambda v: f"{v*100:+.2f}%")
    df_show["actual_avg"] = df_show["actual_avg"].apply(lambda v: f"{v*100:+.2f}%")
    df_show["win_rate"]   = df_show["win_rate"].apply(lambda v: f"{v*100:.1f}%")
    print(df_show.to_string(index=False))


def train():
    """主训练流程"""
    from ml_train_v2 import _get_signals
    from ml_features_v2 import build_feature_matrix, FEATURE_COLS

    t_total = time.time()

    print("=== 获取信号 ===")
    sig, _ = _get_signals()
    print(f"共 {len(sig)} 个")

    print("\n=== 构建特征 ===")
    feat = build_feature_matrix(sig, require_label=False)
    print(f"特征矩阵 {feat.shape}")

    print("\n=== 计算回归 label（T+2_open / T+1_open - 1）===")
    con = _duckdb.connect(DUCK, read_only=True)
    rets = get_returns(con, sig)
    con.close()
    feat = feat.merge(rets, on=["ts_code", "trade_date"], how="left")
    feat = feat.dropna(subset=["label_reg"])
    print(f"含完整价格的样本 {len(feat)}, label 均值={feat['label_reg'].mean()*100:+.2f}%, "
          f"std={feat['label_reg'].std()*100:.2f}%")

    train_df = feat[feat["trade_date"] <= TRAIN_END]
    test_df  = feat[feat["trade_date"] >= TEST_START]
    print(f"\n训练集 {len(train_df)}  测试集 {len(test_df)}")

    X_train = train_df[FEATURE_COLS]; y_train = train_df["label_reg"]
    X_test  = test_df[FEATURE_COLS];  y_test  = test_df["label_reg"]

    print("\n=== 训练 XGBRegressor ===")
    t = time.time()
    model = xgb.XGBRegressor(
        n_estimators=650, max_depth=4, learning_rate=0.037,
        min_child_weight=15, subsample=0.8, colsample_bytree=0.8,
        objective="reg:squarederror", eval_metric="rmse",
        early_stopping_rounds=80, random_state=42, n_jobs=-1, verbosity=0,
    )
    model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=50)
    print(f"=== fit 耗时 {time.time()-t:.2f}s, best_iter={model.best_iteration} ===")

    evaluate(model, X_test, y_test, FEATURE_COLS, test_df)

    os.makedirs(MODEL_DIR, exist_ok=True)
    with open(MODEL_PATH, "wb") as f:
        pickle.dump({"model": model, "feature_cols": FEATURE_COLS}, f)
    print(f"\n模型保存：{MODEL_PATH}")

    if shap is not None:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            print("\n=== SHAP ===")
            explainer = shap.TreeExplainer(model)
            sv = explainer.shap_values(X_test[FEATURE_COLS])
            plt.figure(figsize=(10, 7))
            shap.summary_plot(sv, X_test[FEATURE_COLS], show=False)
            plt.tight_layout(); plt.savefig(SHAP_IMG, dpi=150, bbox_inches="tight"); plt.close()
            print(f"SHAP 图：{SHAP_IMG}")

            mean_shap = np.abs(sv).mean(axis=0)
            ranking = sorted(zip(FEATURE_COLS, mean_shap), key=lambda x: -x[1])
            print("\nTop 因子（mean |SHAP|）：")
            for r, (f, v) in enumerate(ranking, 1):
                print(f"  {r:2d}. {f:<24s}  {v:.4f}")
        except Exception as e:
            print(f"SHAP 失败: {e}")

    print(f"\n=== 总耗时 {time.time()-t_total:.2f}s ===")


if __name__ == "__main__":
    train()
