"""
1to2_model_v2_deploy.py — 用 v2 评估锁定的轮数 + 全部历史数据，训练首板→2板【实盘部署模型】

与 ml_train_1to2_v2.py（评估）的分工：
  评估(v2)   : train ≤2023 / val 2024 / test 2025~ —— 留干净测试集，诚实估分、定轮数
  部署(本文件): 关早停、轮数锁死成 v2 折4 的 best_iter，喂 ≤今天全部数据 —— 出实盘最强模型

为什么不算泄漏：实盘预测的是「还没发生的交易日」，把 ≤今天的所有数据喂进去训练，
模型见到的全是「它要预测那天之前」的信息，没有未来函数。评估阶段已用干净测试集验证过配方，
部署阶段就该把所有学过的数据都用上。纪律红线：轮数/超参一律沿用评估值，不再用新数据重新挑。

特征：ml_features_1to2_v1（34 特征，与 v2 一致）
轮数：默认读 v2 pkl 折4 的 best_iter；--rounds 可手动覆盖
分位档切点：直接沿用 v2 pkl 的 tiers（2022~2026 全样本外合并标定，跨熊牛）

产出：
  model/xgb_1lb_2lb_v2_deploy.pkl —— 实盘打分用（结构同 v2：model/feature_cols/tiers/meta）

用法：
  python 1to2/1to2_model_v2_deploy.py
  python 1to2/1to2_model_v2_deploy.py --rounds 80   # 手动指定轮数
"""

import os
import sys
import time
import pickle
import argparse
import warnings
import logging

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

import xgboost as xgb

MODEL_DIR   = os.path.join(os.path.dirname(__file__), "model")
V2_PATH     = os.path.join(MODEL_DIR, "xgb_1lb_2lb_v2.pkl")
DEPLOY_PATH = os.path.join(MODEL_DIR, "xgb_1lb_2lb_v2_deploy.pkl")

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
        latest = con.execute("SELECT strftime(MAX(trade_date), '%Y%m%d') FROM daily").fetchone()[0]
        cache_path = os.path.join(CACHE_DIR, f"signals_1to2_{latest}.pkl")
        if os.path.exists(cache_path):
            return pd.read_pickle(cache_path)
        signal_df = con.execute(_SIGNAL_SQL).df()
    finally:
        con.close()
    if signal_df.empty:
        raise RuntimeError("未找到任何 1 板信号。")
    signal_df.to_pickle(cache_path)
    return signal_df


def _load_v2():
    """读 v2 评估 pkl，返回 (折4 best_iter, tiers)。"""
    if not os.path.exists(V2_PATH):
        raise RuntimeError(f"未找到 v2 评估模型 {V2_PATH}，请先跑 ml_train_1to2_v2.py")
    with open(V2_PATH, "rb") as f:
        b = pickle.load(f)
    folds = b.get("meta", {}).get("folds")
    if not folds:
        raise RuntimeError("v2 pkl 缺 meta.folds，请重训 ml_train_1to2_v2.py。")
    return int(folds[-1]["best_iter"]), b["tiers"]


def train(rounds_override=None):
    """部署训练入口：锁定轮数 + 全量数据，关早停，出实盘 pkl。"""
    from ml_features_1to2_v1 import build_feature_matrix, FEATURE_COLS

    t0 = time.time()
    locked_rounds, tiers = _load_v2()
    rounds = rounds_override or locked_rounds
    print(f"=== 部署训练 ===")
    print(f"  锁定轮数：{rounds}（来源：{'手动 --rounds' if rounds_override else 'v2 折4 best_iter='+str(locked_rounds)}）")

    sig = _get_signals()
    print(f"  全量 1 板信号：{len(sig)}（{sig['trade_date'].min()}~{sig['trade_date'].max()}）")

    feat = build_feature_matrix(sig)
    feat = feat.dropna(subset=["label"])
    feat = feat[feat["label"].isin([0.0, 1.0])]
    X, y = feat[FEATURE_COLS], feat["label"]
    print(f"  有效训练样本：{len(X)}（正例率 {y.mean():.2%}）")

    pos_ratio = float((y == 0).sum()) / max(float((y == 1).sum()), 1)
    model = xgb.XGBClassifier(
        n_estimators=rounds,
        max_depth=4,
        learning_rate=0.037,
        min_child_weight=15,
        subsample=0.8,
        colsample_bytree=0.8,
        scale_pos_weight=pos_ratio,
        eval_metric="auc",
        random_state=42,
        n_jobs=1,
        verbosity=0,
    )
    model.fit(X, y)
    print(f"  fit 完成：{len(X)} 样本 × {rounds} 棵树，耗时 {time.time()-t0:.2f}s")

    meta = {"rounds": int(rounds),
            "source": "v2_deploy",
            "n_train": int(len(X)),
            "train_range": f"{sig['trade_date'].min()}~{sig['trade_date'].max()}",
            "tier_calib": "沿用 v2（2022~2026 全样本外合并标定）"}
    with open(DEPLOY_PATH, "wb") as f:
        pickle.dump({"model": model, "feature_cols": FEATURE_COLS, "tiers": tiers, "meta": meta}, f)
    print(f"\n实盘部署模型已保存：{DEPLOY_PATH}")
    print(f"分位档切点（沿用 v2 跨熊牛标定）：")
    for t in tiers:
        print(f"    {t['label']:<8s} proba≥{t['proba']:.4f}  胜率 {t['win']*100:.2f}%  (n={t['n']})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=None, help="手动指定轮数，覆盖 v2 折4 best_iter")
    args = ap.parse_args()
    train(args.rounds)
