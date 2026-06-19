"""
2lb_model_v6_deploy.py — 用 v6 评估锁定的轮数 + 全部历史数据，训练 2进4(到4板)【实盘部署模型】

与 ml_train_2lb_v6.py（评估）的分工：
  评估(v6)   : walk-forward，留干净样本外，诚实估分、定轮数（末折 ≤2023 的 best_iter）
  部署(本文件): 关早停、轮数锁死成 v6 末折 best_iter，喂 ≤今天全部数据 —— 出实盘最强模型

为什么不算泄漏：实盘预测的是「还没发生的交易日」，喂 ≤今天全部数据，模型见到的全是
「要预测那天之前」的信息。评估阶段已用干净样本外验证过配方，部署阶段把学过的数据都用上。
纪律红线：轮数/超参一律沿用评估值，不再用新数据重新挑。

label：2进4 —— 该 2板所在连板序列最终高度 boards>=4（与 ml_train_2lb_v6 一致，用 _attach_labels）
特征：ml_features_2lb_v3（31 特征）
分位档切点：沿用 v6 评估 pkl 的 tiers（全样本外合并标定，proba=cut）

产出：
  model/xgb_2lb_v6_deploy.pkl —— 实盘打分用（结构同 v6：model/feature_cols/tiers/meta/is_reg）

用法：
  python first10/2lb_model_v6_deploy.py
  python first10/2lb_model_v6_deploy.py --rounds 71   # 手动指定轮数
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

MODEL_DIR = os.path.join(os.path.dirname(__file__), "model")
V6_PATH = os.path.join(MODEL_DIR, "xgb_2lb_v6.pkl")
DEPLOY_PATH = os.path.join(MODEL_DIR, "xgb_2lb_v6_deploy.pkl")


def _load_v6():
    """读 v6 评估 pkl，返回 (末折 best_iter, tiers)。"""
    if not os.path.exists(V6_PATH):
        raise RuntimeError(f"未找到 v6 评估模型 {V6_PATH}，请先跑 ml_train_2lb_v6.py")
    with open(V6_PATH, "rb") as f:
        b = pickle.load(f)
    folds = b.get("meta", {}).get("folds")
    if not folds:
        raise RuntimeError("v6 pkl 缺 meta.folds，请重训 ml_train_2lb_v6.py。")
    return int(folds[-1]["best_iter"]), b["tiers"]


def train(rounds_override=None):
    """部署训练入口：锁定轮数 + 全量数据，关早停，出 2进4 实盘 pkl。"""
    from ml_features_2lb_v3 import build_feature_matrix, FEATURE_COLS
    from ml_train_2lb_v6 import _get_signals, _attach_labels

    t0 = time.time()
    locked_rounds, tiers = _load_v6()
    rounds = rounds_override or locked_rounds
    print("=== v6 部署训练（2进4 / 到4板）===")
    print(f"  锁定轮数：{rounds}（来源：{'手动 --rounds' if rounds_override else 'v6 末折 best_iter='+str(locked_rounds)}）")

    sig = _get_signals()
    print(f"  全量 2 板信号：{len(sig)}（{sig['trade_date'].min()}~{sig['trade_date'].max()}）")

    feat = build_feature_matrix(sig)
    data = _attach_labels(feat)
    X, y = data[FEATURE_COLS], data["y"]
    print(f"  有效训练样本：{len(X)}（到4板正例率 {y.mean():.2%}）")

    pos_ratio = float((y == 0).sum()) / max(float((y == 1).sum()), 1)
    model = xgb.XGBClassifier(
        n_estimators=rounds, max_depth=4, learning_rate=0.037,
        min_child_weight=15, subsample=0.8, colsample_bytree=0.8,
        scale_pos_weight=pos_ratio, eval_metric="auc",
        random_state=42, n_jobs=1, verbosity=0,
    )
    model.fit(X, y)
    print(f"  fit 完成：{len(X)} 样本 × {rounds} 棵树，耗时 {time.time()-t0:.2f}s")

    meta = {"target": "u4_2to4", "rounds": int(rounds), "source": "v6_deploy",
            "n_train": int(len(X)),
            "train_range": f"{sig['trade_date'].min()}~{sig['trade_date'].max()}",
            "tier_calib": "沿用 v6 评估（全样本外合并标定）"}
    with open(DEPLOY_PATH, "wb") as f:
        pickle.dump({"model": model, "feature_cols": FEATURE_COLS, "tiers": tiers,
                     "meta": meta, "is_reg": False}, f)
    print(f"\nv6 实盘部署模型已保存：{DEPLOY_PATH}")
    print("分位档切点（沿用 v6 标定，proba=到4板概率）：")
    for t in tiers:
        print(f"    {t['label']:<8s} proba≥{t['proba']:.4f}  实收 {t['ret']*100:+.2f}%  "
              f"到4板 {t['hit4']*100:.1f}%  (n={t['n']})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=None, help="手动指定轮数，覆盖 v6 末折 best_iter")
    args = ap.parse_args()
    train(args.rounds)
