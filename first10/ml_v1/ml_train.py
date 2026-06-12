"""
ml_train.py — XGBoost 训练 + SHAP 因子分析

用法：
  python first10/ml_train.py

流程：
  1. 从 ClickHouse 拿出所有历史首板爆量信号（复用 1stLimitUpAnd2secLimitUp 的扫描逻辑）
  2. 调用 ml_features.build_feature_matrix() 构建特征
  3. 时序分割：train=2020~2023, test=2024~
  4. 训练 XGBoostClassifier
  5. 评估 AUC-ROC / Precision@Top20% / KS
  6. SHAP summary_plot → 图片保存
  7. 保存模型 → first10/model/xgb_lianban.pkl

================================================================
当前所有特征指标说明（共 31 个，定义见 ml_features.py 中的 SQL）
================================================================
label                    次日 pct_chg ≥ 9.8% 即为 1（次日连板），否则 0

—— 信号日量价 (daily) ——
pct_chg                  信号日涨跌幅（已 /100，单位为小数；首板≈0.10）
vol_ratio_5              信号日成交量 / 前 5 日均量（不含当日）→ 爆量倍数
vol_ratio_20             信号日成交量 / 前 20 日均量（不含当日）
intraday_range           当日振幅 = (high - low) / close
close_strength           收盘强度 = (close - low) / (high - low) ∈ [0,1]
gap_up                   高开幅度 = open / 前一日 close - 1
pre_days_limit           前 5 日内涨跌停天数（|pct_chg| ≥ 9.8 计数）

—— 涨停盘口 (limit_list_d, 替代旧 kpl_list) ——
lu_time_min              首次涨停时刻距 09:30 的分钟数（越小=越早封板）
fd_amount_ratio          封单金额 / 流通市值（封单强度归一化，越高越强）
open_times               炸板次数（0=一字/稳封，越大封板越弱）
up_stat_ratio            up_stat = "N/T" → N/T，近 T 天涨停频率
                         （越高=最近反复涨停的热门股）

—— 估值/流通盘 (daily_basic) ——
circ_mv_log              ln(流通市值)
turnover_rate            换手率 (%)
pe_ttm                   滚动市盈率
pb                       市净率

—— 筹码结构 (cyq_perf) ——
winner_rate              获利盘比例 ∈ [0,1]
chip_conc                筹码集中度 = (cost_95pct - cost_5pct) / close
                         （越小代表筹码越集中）

—— 上市信息 (stock_meta) ——
days_listed              上市天数（信号日 - list_date）

—— 大盘环境（中证 1000 指数 000852.SH）——
market_trend             指数当日 pct_chg
market_ma_dir            指数 MA5 vs MA20 方向（+1 多头 / 0 平 / -1 空头）

—— 市场情绪（全市场 daily 聚合，已剔除创业板/科创板/北交所）——
market_lu_count          全市场当日涨停数（pct_chg ≥ 9.8）
market_dt_count          全市场当日跌停数（pct_chg ≤ -9.8）
market_lu_ma5            涨停数 5 日均值

—— 个股自身历史（Phase A）——
stock_lianban_hist_rate  截至信号前一天，该股累积 (次日连板次数 / 历史信号次数)
                         窗口已修正为严格小于当前行（之前因含当前行造成标签泄漏）

—— 资金流 (moneyflow) ——
net_mf_ratio             净主力资金 / close（按收盘价归一）
lg_elg_ratio             大单+特大单买入量 / 总买入量
sm_ratio                 小单买入量 / 总买入量

—— 板块共振 (ths_member × limit_cpt_list) ——
concept_lu_count_max     信号股所属概念里最大涨停数（板块共振强度）
                         例：信号股属于"AI概念"，AI 当日有 27 只涨停 → 27
concept_rank_min         所属概念里最强排名（数字越小越强；rank=1 为当日最强板块）
concept_cons_nums_max    所属概念里最大连板数（板块梯队高度）
concept_count            该股属于多少个概念（小票通常 <3，热门票 5+）

—— 交叉特征 ——
fd_x_concept_lu          fd_amount_ratio × concept_lu_count_max
                         捕捉"封板硬 × 板块共振"的乘积效应

—— 已删除的特征 ——
recent_max_lianban       近 60 日最大连板数 —— 在首板信号场景下天然无效
                         （SHAP=0，按 bucket pos_rate 完全不单调），已删

================================================================
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

try:
    import xgboost as xgb
except ImportError:
    raise SystemExit("请先安装 xgboost：pip install xgboost")

try:
    import shap
except ImportError:
    shap = None
    logger.warning("shap 未安装，跳过 SHAP 分析。pip install shap")

try:
    from sklearn.metrics import roc_auc_score, precision_score
except ImportError:
    raise SystemExit("请先安装 scikit-learn：pip install scikit-learn")

MODEL_DIR  = os.path.join(os.path.dirname(__file__), "model")
MODEL_PATH = os.path.join(MODEL_DIR, "xgb_lianban.pkl")
SHAP_IMG   = os.path.join(MODEL_DIR, "shap_summary.png")

TRAIN_END = "20231231"   # 训练集截止日
TEST_START = "20240101"  # 测试集起始日


CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
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
  AND l.limit_times  = 1                          -- 首板（连续涨停天数=1）
  AND d.high > d.low                               -- 非一字板
  AND d.vol_ma5 > 0
  AND d.vol / d.vol_ma5 < 1.4                      -- 非爆量（含严缩量）
  AND l.ts_code NOT LIKE '688%'                    -- 排除科创板
  AND l.ts_code NOT LIKE '300%'                    -- 排除创业板
  AND l.ts_code NOT LIKE '%.BJ'                    -- 排除北交所
  AND st.ts_code IS NULL                           -- 非 ST
ORDER BY l.trade_date, l.ts_code
"""


def _get_signals():
    """返回 (signal_df, None)。

    SQL 基于 limit_list_d 直接生成首板小量信号（limit_times=1, vol/ma5<1.4, 非一字非ST）。
    缓存按 daily 表最大日期 + 信号定义版本号命名。
    """
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
        raise RuntimeError("未找到任何信号，请检查 limit_list_d / daily / stock_st 表是否齐全。")

    signal_df.to_pickle(cache_path)
    print(f"信号缓存已保存: {cache_path}")
    return signal_df, None


def _split(feat_df: pd.DataFrame):
    feat_df = feat_df.dropna(subset=["label"])
    feat_df = feat_df[feat_df["label"].isin([0.0, 1.0])]

    train = feat_df[feat_df["trade_date"] <= TRAIN_END]
    test  = feat_df[feat_df["trade_date"] >= TEST_START]
    return train, test


def _evaluate(model, X_test: pd.DataFrame, y_test: pd.Series, feat_cols: list[str]):
    from ml_features import FEATURE_COLS
    proba = model.predict_proba(X_test[feat_cols])[:, 1]
    auc = roc_auc_score(y_test, proba)

    # Precision @ Top 20%
    n_top = max(1, int(len(proba) * 0.2))
    top_idx = np.argsort(proba)[::-1][:n_top]
    prec_top20 = y_test.iloc[top_idx].mean()

    # KS
    pos_cdf = np.cumsum(np.sort(proba[y_test == 1])[::-1]) / (y_test == 1).sum()
    neg_cdf = np.cumsum(np.sort(proba[y_test == 0])[::-1]) / (y_test == 0).sum()
    ks = float(np.max(np.abs(pos_cdf[:min(len(pos_cdf), len(neg_cdf))]
                              - neg_cdf[:min(len(pos_cdf), len(neg_cdf))])))

    print(f"\n测试集评估（{TEST_START}~）：")
    print(f"  样本={len(y_test)}  正例={int(y_test.sum())}  正例率={y_test.mean():.1%}")
    print(f"  AUC-ROC      = {auc:.4f}")
    print(f"  Precision@20% = {prec_top20:.4f}")
    print(f"  KS            = {ks:.4f}")
    return proba


def train():
    from ml_features import build_feature_matrix, FEATURE_COLS

    t_total = time.time()

    print("=== 获取首板爆量信号 ===")
    t = time.time()
    signal_df, stock_dict = _get_signals()
    print(f"=== 信号阶段总耗时: {time.time()-t:.2f}s ===")
    print(f"共 {len(signal_df)} 个信号（{signal_df['trade_date'].min()} ~ {signal_df['trade_date'].max()}）")

    print("\n=== 构建特征矩阵 ===")
    t = time.time()
    feat_df = build_feature_matrix(signal_df, stock_dict=stock_dict)
    print(f"=== 特征构建总耗时: {time.time()-t:.2f}s ===")
    print(f"特征矩阵：{feat_df.shape}，label 分布：\n{feat_df['label'].value_counts(dropna=False)}")

    train_df, test_df = _split(feat_df)
    print(f"\n训练集：{len(train_df)}  测试集：{len(test_df)}")

    X_train = train_df[FEATURE_COLS]
    y_train = train_df["label"]
    X_test  = test_df[FEATURE_COLS]
    y_test  = test_df["label"]

    pos_ratio = float((y_train == 0).sum()) / max(float((y_train == 1).sum()), 1)

    print("\n=== 训练 XGBoost ===")
    t = time.time()
    model = xgb.XGBClassifier(
        n_estimators=400,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        scale_pos_weight=pos_ratio,
        eval_metric="auc",
        early_stopping_rounds=30,
        use_label_encoder=False,
        random_state=42,
        n_jobs=-1,
        verbosity=0,
    )
    model.fit(
        X_train, y_train,
        eval_set=[(X_test, y_test)],
        verbose=50,
    )
    print(f"=== XGBoost fit 耗时: {time.time()-t:.2f}s ===")

    t = time.time()
    proba = _evaluate(model, X_test, y_test, FEATURE_COLS)
    print(f"=== 评估耗时: {time.time()-t:.2f}s ===")

    os.makedirs(MODEL_DIR, exist_ok=True)
    with open(MODEL_PATH, "wb") as f:
        pickle.dump({"model": model, "feature_cols": FEATURE_COLS}, f)
    print(f"\n模型已保存: {MODEL_PATH}")

    # SHAP 分析
    if shap is not None:
        print("\n=== SHAP 因子分析 ===")
        t = time.time()
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            explainer = shap.TreeExplainer(model)
            shap_values = explainer.shap_values(X_test[FEATURE_COLS])
            print(f"  [shap] compute: {time.time()-t:.2f}s", flush=True)

            plt.figure(figsize=(10, 7))
            shap.summary_plot(shap_values, X_test[FEATURE_COLS], show=False)
            plt.tight_layout()
            plt.savefig(SHAP_IMG, dpi=150, bbox_inches="tight")
            plt.close()
            print(f"SHAP 图已保存: {SHAP_IMG}")

            # 文字版 top 因子
            mean_shap = np.abs(shap_values).mean(axis=0)
            ranking = sorted(zip(FEATURE_COLS, mean_shap), key=lambda x: -x[1])
            print("\nTop 因子（mean |SHAP|）：")
            for rank, (feat, val) in enumerate(ranking, 1):
                print(f"  {rank:2d}. {feat:<22s}  {val:.4f}")
        except Exception as e:
            print(f"SHAP 分析失败: {e}")
    else:
        # XGBoost 原生特征重要性
        imp = model.feature_importances_
        ranking = sorted(zip(FEATURE_COLS, imp), key=lambda x: -x[1])
        print("\n特征重要性（gain）：")
        for rank, (feat, val) in enumerate(ranking, 1):
            print(f"  {rank:2d}. {feat:<22s}  {val:.4f}")

    # 保存特征矩阵（方便后续调试）
    feat_csv = os.path.join(MODEL_DIR, "feature_matrix.csv")
    feat_df.to_csv(feat_csv, index=False, encoding="utf-8-sig")
    print(f"特征矩阵已保存: {feat_csv}")

    print(f"\n=== 全流程总耗时: {time.time()-t_total:.2f}s ===")


if __name__ == "__main__":
    train()
