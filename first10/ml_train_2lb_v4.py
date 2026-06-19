"""
ml_train_2lb_v4.py — XGBoost 训练：二板→三板 晋升概率模型 v4（v3 特征 + 干净的三段切分）

与 v3 的唯一区别在「评估流程」，特征完全沿用 ml_features_2lb_v3：
  - 训练集   ≤2023      ：学规律
  - 验证集   2024       ：早停选轮数（这段被「用过」，分数偏乐观，不报为实盘成绩）
  - 测试集   2025~      ：全程锁死，唯一干净、可信的成绩单（AUC / 胜率均只在此算）
v3 把测试集直接拿来早停 + 报分，存在轻度泄漏，成绩偏乐观；v4 修正之。

分位档切点（proba→胜率映射）特意用 2024+2025+2026 合并标定：2024 仅参与过早停、
泄漏极轻且方向有利（弱市拉低切点对应胜率→更保守），合并后切点跨熊牛两种行情、实盘更耐用。
绝不向前扩到 ≤2023（那是训练集，模型已拟合权重，切点会严重虚高）。

信号：limit_list_d.limit_times = 2（非 ST，剔除 创业/科创/北交所）
label：T+1 pct_chg >= 9.8（次日封板=3 板成功，二分类）

产出：
  model/xgb_2lb_3lb_v4.pkl
  model/shap_summary_2lb_v4.png
  model/feature_matrix_2lb_v4.csv

进出场假设：T 日盘后选 → T+1 开盘买 → T+1 收盘看是否封板。
依赖：先用 cache_tushare.py 补齐 stk_auction_o / stk_auction_c 历史数据。

部署提示：评估确认轮数后，上实盘的模型应另用「≤今天全部数据 + 锁定轮数」重训，
不在本文件内，避免把部署模型与评估模型混为一谈。
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
MODEL_PATH = os.path.join(MODEL_DIR, "xgb_2lb_3lb_v4.pkl")
SHAP_IMG   = os.path.join(MODEL_DIR, "shap_summary_2lb_v4.png")

TRAIN_END = "20231231"
VAL_START = "20240101"
VAL_END   = "20241231"
TEST_START = "20250101"

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
  AND l.limit_times  = 2
  AND l.ts_code NOT LIKE '688%'
  AND l.ts_code NOT LIKE '30%'
  AND l.ts_code NOT LIKE '%.BJ'
  AND st.ts_code IS NULL
ORDER BY l.trade_date, l.ts_code
"""


def _get_signals():
    """扫描 2 板信号（limit_times=2，非 ST，剔除 创业/科创/北交所），按日期缓存。"""
    import duckdb as _duckdb
    from db_loader import _ENV
    duck_path = _ENV.get("LOCAL_DUCKDB_PATH", "stock_data_tushare.duckdb")

    con = _duckdb.connect(duck_path, read_only=True)
    try:
        latest = con.execute(
            "SELECT strftime(MAX(trade_date), '%Y%m%d') FROM daily"
        ).fetchone()[0]
        cache_path = os.path.join(CACHE_DIR, f"signals_2lb_{latest}.pkl")

        if os.path.exists(cache_path):
            t = time.time()
            df = pd.read_pickle(cache_path)
            print(f"信号缓存命中: {cache_path} ({time.time()-t:.2f}s, {len(df)} rows)", flush=True)
            return df

        t = time.time()
        print(f"扫描 2 板信号（limit_times=2，非 ST，非创业/科创/北交所）...", flush=True)
        signal_df = con.execute(_SIGNAL_SQL).df()
        print(f"  [signals] SQL: {time.time()-t:.2f}s, {len(signal_df)} 个信号", flush=True)
    finally:
        con.close()

    if signal_df.empty:
        raise RuntimeError("未找到任何 2 板信号。")

    signal_df.to_pickle(cache_path)
    return signal_df


TIER_DEFS = [(0.01, "Top 1%"), (0.05, "Top 5%"), (0.10, "Top 10%"), (0.20, "Top 20%")]


def _compute_tiers(proba, y_test):
    """在测试集上算各分位档的 proba 切点与胜率，随模型存入 pkl，供打分端读取。"""
    order = np.argsort(proba)[::-1]
    y = np.asarray(y_test, dtype=float)
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


def _split(feat_df: pd.DataFrame):
    """三段切分：train ≤2023 / val 2024 / test 2025~。"""
    feat_df = feat_df.dropna(subset=["label"])
    feat_df = feat_df[feat_df["label"].isin([0.0, 1.0])]
    train = feat_df[feat_df["trade_date"] <= TRAIN_END]
    val   = feat_df[(feat_df["trade_date"] >= VAL_START) & (feat_df["trade_date"] <= VAL_END)]
    test  = feat_df[feat_df["trade_date"] >= TEST_START]
    return train, val, test


def _eval_block(proba, y_test, title):
    """打印一个评估块：AUC + Top N% 胜率 + KS。返回 auc。"""
    auc = roc_auc_score(y_test, proba)
    print(f"\n{title}：")
    print(f"  样本={len(y_test)}  正例={int(y_test.sum())}  正例率={y_test.mean():.2%}")
    print(f"  AUC-ROC = {auc:.4f}")

    print(f"\n  分位数胜率（Top N% 中 3 板成功率）：")
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
    return auc


def _eval_by_year(proba, test_df, y_test):
    """按年拆分测试集的 Top1%/5%/10% 胜率，看跨年稳健性。"""
    d = test_df.copy()
    d = d.reset_index(drop=True)
    p = np.asarray(proba)
    y = np.asarray(y_test, dtype=float)
    d["_year"] = d["trade_date"].str[:4]
    print(f"\n  按年稳健性（各年内 Top N% 3 板成功率）：")
    print(f"  {'年份':<6s}{'样本':>6s}{'基准':>8s}{'Top1%':>9s}{'Top5%':>9s}{'Top10%':>9s}")
    for yr, sub in d.groupby("_year"):
        idx_all = sub.index.values
        pp = p[idx_all]
        yy = y[idx_all]
        base = yy.mean()
        row = f"  {yr:<6s}{len(sub):>6d}{base:>7.1%}"
        for q in [0.01, 0.05, 0.10]:
            n = max(1, int(len(pp) * q))
            top = np.argsort(pp)[::-1][:n]
            row += f"{yy[top].mean():>8.1%}"
        print(row)


def train():
    """训练入口：装载信号 → 构建特征 → 三段切分训练 → 测试集评估 → SHAP。"""
    from ml_features_2lb_v3 import build_feature_matrix, FEATURE_COLS, FEATURE_CN

    t_total = time.time()

    print("=== 获取 2 板信号 ===")
    signal_df = _get_signals()
    print(f"共 {len(signal_df)} 个信号"
          f"（{signal_df['trade_date'].min()} ~ {signal_df['trade_date'].max()}）")

    print("\n=== 构建特征矩阵 ===")
    feat_df = build_feature_matrix(signal_df)
    print(f"特征矩阵：{feat_df.shape}")
    print(f"label 分布：\n{feat_df['label'].value_counts(dropna=False)}")

    train_df, val_df, test_df = _split(feat_df)
    print(f"\n训练集 ≤{TRAIN_END}：{len(train_df)}（正例率 {train_df['label'].mean():.2%}）")
    print(f"验证集 {VAL_START}~{VAL_END}：{len(val_df)}（正例率 {val_df['label'].mean():.2%}）")
    print(f"测试集 {TEST_START}~：{len(test_df)}（正例率 {test_df['label'].mean():.2%}）")

    X_train, y_train = train_df[FEATURE_COLS], train_df["label"]
    X_val,   y_val   = val_df[FEATURE_COLS],   val_df["label"]
    X_test,  y_test  = test_df[FEATURE_COLS],  test_df["label"]

    pos_ratio = float((y_train == 0).sum()) / max(float((y_train == 1).sum()), 1)

    print("\n=== 训练 XGBoost（早停看 2024 验证集）===")
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
        n_jobs=1,
        verbosity=0,
    )
    model.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=50)
    print(f"=== fit 耗时: {time.time()-t:.2f}s, best_iter={model.best_iteration} ===")

    val_proba = model.predict_proba(X_val)[:, 1]
    _eval_block(val_proba, y_val, f"验证集评估（{VAL_START}~{VAL_END}，偏乐观、仅供参考）")

    test_proba = model.predict_proba(X_test)[:, 1]
    test_auc = _eval_block(test_proba, y_test, f"★ 测试集评估（{TEST_START}~，干净成绩单）")
    _eval_by_year(test_proba, test_df, y_test)

    train_auc = roc_auc_score(y_train, model.predict_proba(X_train)[:, 1])
    print(f"\n=== 过拟合体检 ===")
    print(f"  训练集 AUC={train_auc:.4f}  测试集 AUC={test_auc:.4f}  "
          f"gap={train_auc-test_auc:+.4f}（<0.05健康 / 0.05~0.10可接受 / >0.15警惕）")

    tier_proba = np.concatenate([val_proba, test_proba])
    tier_y = pd.concat([y_val, y_test], ignore_index=True)
    tiers = _compute_tiers(tier_proba, tier_y)
    meta = {"auc": float(test_auc),
            "base": float(y_test.mean()),
            "n_test": int(len(y_test)),
            "tier_calib": f"{VAL_START}~（2024+2025+2026 合并）",
            "best_iteration": int(model.best_iteration) if model.best_iteration is not None else None}

    os.makedirs(MODEL_DIR, exist_ok=True)
    with open(MODEL_PATH, "wb") as f:
        pickle.dump({"model": model, "feature_cols": FEATURE_COLS,
                     "tiers": tiers, "meta": meta}, f)
    print(f"\n模型已保存: {MODEL_PATH}")
    print(f"分位档切点（基于 2024+2025+2026 合并标定，跨熊牛更耐用，已随模型保存）：")
    for tr in tiers:
        print(f"    {tr['label']:<8s} proba≥{tr['proba']:.4f}  胜率 {tr['win']*100:.2f}%  (n={tr['n']})")

    if shap is not None:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            print("\n=== SHAP（基于测试集）===")
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
            for r, (f, v) in enumerate(ranking, 1):
                print(f"  {r:2d}. {f:<26s}  {v:.4f}  {FEATURE_CN.get(f, '')}")
        except Exception as e:
            print(f"SHAP 失败: {e}")

    feat_csv = os.path.join(MODEL_DIR, "feature_matrix_2lb_v4.csv")
    feat_df.to_csv(feat_csv, index=False, encoding="utf-8-sig")
    print(f"特征矩阵: {feat_csv}")
    print(f"\n=== 总耗时: {time.time()-t_total:.2f}s ===")


if __name__ == "__main__":
    train()
