"""
ml_train_ret4d_v1.py — XGBoost 回归：首板个股「买入后持有4天的超额收益」预测 v1（walk-forward）

目标（回归，连续值）：
  label = 个股 4 天收益 − 中证1000(000852.SH) 同期 4 天收益（超额收益，去掉大盘 beta）
  口径：首板日 T0 盘后选 → T+1 开盘买入 → T+5 收盘卖出（持有 4 个完整交易日）
        个股收益 = close[T+5]/open[T+1] − 1
        指数收益 = idx_close[T+5]/idx_open[T+1] − 1（同样的买卖两天）
  label 做 ±30% winsorize，避免连续涨停的极端正样本主导平方误差。

为什么用超额收益：绝对正收益高度依赖行情（牛市普涨），模型会退化成判断大盘涨跌；
减掉同期指数收益后，模型学的是「这只首板能否跑赢大盘」，才是真正的选股 alpha，跨牛熊更稳。

信号：limit_list_d.limit_times = 1（首板，非 ST，剔除 创业/科创/北交所）
特征：ml_features_ret4d.FEATURE_COLS（15 个，按单因子 IC 挑出的「首板均值反转」专用因子；
      label 也由该模块产出，本文件不再依赖 1to2）

评估（回归版，复用 1to2/2lb 的 walk-forward 骨架，把分类指标换成回归指标）：
  - 排序质量用 Rank IC（预测值 vs 实际超额收益的 Spearman 相关）替代 AUC
  - 分档用「按预测收益 Top N% 的实际平均超额收益 + 胜率(%>0)」替代分类胜率
  - 过拟合体检看 train/test 的 IC 差
  - 4 折滚动前推：≤2020/2021/2022 … ≤2023/2024/2025~，每折样本外预测，合并跨熊牛诊断

各折（train 各自全部过去 / val 早停 / test 干净样本外）：
  ≤2020 / 2021 / 2022   ；  ≤2021 / 2022 / 2023
  ≤2022 / 2023 / 2024   ；  ≤2023 / 2024 / 2025~2026

产出：
  model/xgb_1lb_ret4d_v1.pkl       —— 最后一折模型 + 分档切点(预测收益→实际超额收益) + meta
  model/shap_summary_ret4d_v1.png
  model/oos_predictions_ret4d_v1.csv

部署提示：上实盘应另用「锁定 best_iter + ≤今天全量数据」重训（仿 1to2_model_v2_deploy.py）。
"""

import os
import sys
import pickle
import time
import warnings
import logging

import numpy as np
import pandas as pd

_QUART_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _QUART_ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

import xgboost as xgb
from scipy.stats import spearmanr

try:
    import shap
except ImportError:
    shap = None
    logger.warning("shap 未安装，跳过 SHAP 分析。pip install shap")


MODEL_DIR  = os.path.join(os.path.dirname(__file__), "model")
MODEL_PATH = os.path.join(MODEL_DIR, "xgb_1lb_ret4d_v1.pkl")
SHAP_IMG   = os.path.join(MODEL_DIR, "shap_summary_ret4d_v1.png")
OOS_CSV    = os.path.join(MODEL_DIR, "oos_predictions_ret4d_v1.csv")

FOLDS = [
    {"train_end": "20201231", "val": ("20210101", "20211231"), "test": ("20220101", "20221231")},
    {"train_end": "20211231", "val": ("20220101", "20221231"), "test": ("20230101", "20231231")},
    {"train_end": "20221231", "val": ("20230101", "20231231"), "test": ("20240101", "20241231")},
    {"train_end": "20231231", "val": ("20240101", "20241231"), "test": ("20250101", "20991231")},
]

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

from db_loader import _ENV
DUCK_PATH = _ENV.get("LOCAL_DUCKDB_PATH", "stock_data_tushare.duckdb")


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
    """扫描全部首板信号，按日期缓存（与 1to2 其它脚本共用同一缓存文件）。"""
    import duckdb as _duckdb
    con = _duckdb.connect(DUCK_PATH, read_only=True)
    try:
        latest = con.execute("SELECT strftime(MAX(trade_date), '%Y%m%d') FROM daily").fetchone()[0]
        cache_path = os.path.join(CACHE_DIR, f"signals_1to2_{latest}.pkl")
        if os.path.exists(cache_path):
            return pd.read_pickle(cache_path)
        signal_df = con.execute(_SIGNAL_SQL).df()
    finally:
        con.close()
    if signal_df.empty:
        raise RuntimeError("未找到任何首板信号。")
    signal_df.to_pickle(cache_path)
    return signal_df


def _make_model():
    """XGBRegressor：pseudohuber 对肥尾稳健，单线程可复现，早停。已加强正则压过拟合。"""
    return xgb.XGBRegressor(
        n_estimators=1500,
        max_depth=3,
        learning_rate=0.02,
        min_child_weight=30,
        subsample=0.8,
        colsample_bytree=0.8,
        reg_lambda=5.0,
        objective="reg:pseudohubererror",
        eval_metric="mae",
        early_stopping_rounds=80,
        random_state=42,
        n_jobs=1,
        verbosity=0,
    )


def _bucket_stats(pred, actual):
    """按预测值 Top N% 取样本，返回各档 [实际平均超额收益, 胜率(%>0)]。"""
    pred = np.asarray(pred)
    actual = np.asarray(actual, dtype=float)
    order = np.argsort(pred)[::-1]
    out = []
    for q in [0.01, 0.05, 0.10, 0.20]:
        n = max(1, int(len(pred) * q))
        a = actual[order[:n]]
        out.append((float(a.mean()), float((a > 0).mean()), n))
    return out


def _compute_tiers(pred, actual):
    """各分位档的预测切点 + 实际平均超额收益 + 胜率，随模型保存供打分端用。"""
    pred = np.asarray(pred)
    actual = np.asarray(actual, dtype=float)
    order = np.argsort(pred)[::-1]
    tiers = []
    for q, label in [(0.01, "Top 1%"), (0.05, "Top 5%"), (0.10, "Top 10%"), (0.20, "Top 20%")]:
        n = max(1, int(len(pred) * q))
        idx = order[:n]
        tiers.append({"q": q, "label": label,
                      "pred": float(pred[idx][-1]),
                      "ret": float(actual[idx].mean()),
                      "win": float((actual[idx] > 0).mean()),
                      "n": int(n)})
    return tiers


def train():
    """walk-forward 回归训练入口：逐折训练 → IC/分档汇总 → 合并标定切点 → 保存最后一折 + SHAP。"""
    from ml_features_ret4d import build_feature_matrix, FEATURE_COLS, FEATURE_CN

    t0 = time.time()
    print("=== 获取首板信号 ===")
    sig = _get_signals()
    print(f"共 {len(sig)} 个信号（{sig['trade_date'].min()} ~ {sig['trade_date'].max()}）")

    print("\n=== 构建特征矩阵（ml_features_ret4d，反转/资金/市值因子 + 4天超额label）===")
    feat = build_feature_matrix(sig[["ts_code", "trade_date"]], require_label=True)
    print(f"有效样本：{len(feat)}  label 均值 {feat['label'].mean():+.4f}  "
          f"正例率 {(feat['label']>0).mean():.2%}  标准差 {feat['label'].std():.4f}")

    oos_parts, fold_meta = [], []
    last_model = last_test = None

    print("\n=== walk-forward 逐折训练 ===")
    for i, fd in enumerate(FOLDS, 1):
        tr = feat[feat["trade_date"] <= fd["train_end"]]
        va = feat[(feat["trade_date"] >= fd["val"][0]) & (feat["trade_date"] <= fd["val"][1])]
        te = feat[(feat["trade_date"] >= fd["test"][0]) & (feat["trade_date"] <= fd["test"][1])]
        if len(tr) == 0 or len(va) == 0 or len(te) == 0:
            print(f"  折{i} 跳过：train={len(tr)} val={len(va)} test={len(te)}")
            continue

        model = _make_model()
        model.fit(tr[FEATURE_COLS], tr["label"],
                  eval_set=[(va[FEATURE_COLS], va["label"])], verbose=False)

        pred = model.predict(te[FEATURE_COLS])
        ic = spearmanr(pred, te["label"]).correlation
        tr_pred = model.predict(tr[FEATURE_COLS])
        tr_ic = spearmanr(tr_pred, tr["label"]).correlation

        part = te[["ts_code", "trade_date", "label"]].copy()
        part["pred"] = pred
        part["fold"] = i
        oos_parts.append(part)

        bi = int(model.best_iteration) if model.best_iteration is not None else None
        fold_meta.append({"fold": i, "train_end": fd["train_end"], "test": fd["test"],
                          "n_train": len(tr), "n_test": len(te), "best_iter": bi,
                          "ic": float(ic), "train_ic": float(tr_ic)})
        print(f"  折{i}: train≤{fd['train_end']}({len(tr)})  test {fd['test'][0][:4]}~({len(te)})  "
              f"best_iter={bi}  train_IC={tr_ic:.4f}  test_IC={ic:.4f}  gap={tr_ic-ic:+.4f}")
        last_model, last_test = model, te

    if not oos_parts:
        raise RuntimeError("没有任何有效折。")

    oos = pd.concat(oos_parts, ignore_index=True)
    oos["year"] = oos["trade_date"].str[:4]
    overall_ic = spearmanr(oos["pred"], oos["label"]).correlation
    mean_train_ic = float(np.mean([f["train_ic"] for f in fold_meta]))

    print(f"\n=== 样本外汇总（{oos['year'].min()}~{oos['year'].max()}，合并 {len(oos)} 样本）===")
    print(f"  合并 Rank IC = {overall_ic:.4f}   label 均值 {oos['label'].mean():+.4f}")
    print(f"  过拟合体检：各折训练 IC 均值 {mean_train_ic:.4f}  vs  样本外 IC {overall_ic:.4f}  "
          f"gap={mean_train_ic-overall_ic:+.4f}（IC gap <0.03健康 / 0.03~0.06可接受 / >0.10警惕）")

    print(f"\n  按年样本外成绩（Top N% = 按预测收益排名，显示实际平均超额收益 / 胜率）：")
    print(f"  {'年份':<6s}{'样本':>7s}{'IC':>8s}{'Top1%':>14s}{'Top5%':>14s}{'Top10%':>14s}")
    for yr, sub in oos.groupby("year"):
        yic = spearmanr(sub["pred"], sub["label"]).correlation
        b = _bucket_stats(sub["pred"], sub["label"])
        cells = "".join(f"{r*100:>+6.1f}%/{w*100:>3.0f}%" for r, w, _ in b[:3])
        print(f"  {yr:<6s}{len(sub):>7d}{yic:>8.3f}  {cells}")

    tiers = _compute_tiers(oos["pred"], oos["label"])
    print(f"\n分位档切点（基于全部样本外合并标定，pred=预测切点，ret=该档实际平均超额收益）：")
    for t in tiers:
        print(f"    {t['label']:<8s} pred≥{t['pred']:+.4f}  实际超额 {t['ret']*100:+.2f}%  "
              f"胜率 {t['win']*100:.1f}%  (n={t['n']})")

    meta = {"oos_ic": float(overall_ic), "label_mean": float(oos["label"].mean()),
            "n_oos": int(len(oos)), "folds": fold_meta,
            "target": "4d_excess_return(vs 000852.SH), buy T+1 open, sell T+5 close, winsor±0.30",
            "tier_calib": f"walk-forward OOS {oos['year'].min()}~{oos['year'].max()}"}

    os.makedirs(MODEL_DIR, exist_ok=True)
    with open(MODEL_PATH, "wb") as f:
        pickle.dump({"model": last_model, "feature_cols": FEATURE_COLS,
                     "tiers": tiers, "meta": meta}, f)
    print(f"\n模型已保存（最后一折）: {MODEL_PATH}")

    oos.to_csv(OOS_CSV, index=False, encoding="utf-8-sig")
    print(f"样本外预测明细: {OOS_CSV}")

    if shap is not None and last_model is not None:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            print("\n=== SHAP（基于最后一折测试集）===")
            explainer = shap.TreeExplainer(last_model)
            sv = explainer.shap_values(last_test[FEATURE_COLS])
            plt.figure(figsize=(10, 8))
            shap.summary_plot(sv, last_test[FEATURE_COLS], show=False)
            plt.tight_layout()
            plt.savefig(SHAP_IMG, dpi=150, bbox_inches="tight")
            plt.close()
            print(f"SHAP 图: {SHAP_IMG}")
            mean_shap = np.abs(sv).mean(axis=0)
            for r, (f, v) in enumerate(sorted(zip(FEATURE_COLS, mean_shap), key=lambda x: -x[1]), 1):
                print(f"  {r:2d}. {f:<26s}  {v:.4f}  {FEATURE_CN.get(f, '')}")
        except Exception as e:
            print(f"SHAP 失败: {e}")

    print(f"\n=== 总耗时: {time.time()-t0:.2f}s ===")


if __name__ == "__main__":
    train()
