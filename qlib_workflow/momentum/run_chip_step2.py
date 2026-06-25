"""
run_chip_step2.py — 筹码因子增量检验：Alpha158 vs Alpha158+筹码

筹码分布是 Alpha158 完全没有的维度(量价之外)，理论上更可能有增量。从 DuckDB cyq_perf 取：
  winner_rate  获利盘比例
  chip_conc    筹码集中度 = (cost_95pct − cost_5pct)/cost_50pct(越小越集中)
拼进 Alpha158 特征矩阵，同一行集上各训 LGBM，比测试集 Rank IC + 看筹码因子重要性排名。

cyq_perf 自 2020 起，故 train 2020~2022 / valid 2023 / test 2024~2026.05。
股票池：历年沪深300成分并集。环境：.venv312。
用法：python qlib_workflow/momentum/run_chip_step2.py
依赖：~/.qlib/qlib_data/duck_cn；DuckDB cyq_perf + hs300_members；lightgbm/scipy。
"""

import os
import sys

os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")
sys.path.insert(0, os.path.expanduser("~/AI/quart/first10"))

import duckdb
import lightgbm as lgb
import numpy as np
import pandas as pd
import qlib
from qlib.constant import REG_CN
from qlib.utils import init_instance_by_config
from qlib.data.dataset.handler import DataHandlerLP
from scipy.stats import spearmanr

from cache_tushare import DUCKDB_PATH

PROVIDER_URI = os.path.expanduser("~/.qlib/qlib_data/duck_cn")
SEG = {"train": ("2020-01-01", "2022-12-31"), "valid": ("2023-01-01", "2023-12-31"),
       "test": ("2024-01-01", "2026-05-30")}
CHIP = ["winner_rate", "chip_conc"]


def _members_ts():
    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    rows = con.execute("SELECT DISTINCT con_code FROM hs300_members").fetchall()
    con.close()
    return [c for (c,) in rows]


def _qlib_sym(ts):
    c, m = ts.split(".")
    return f"{m}{c}"


def _chip_factors(ts_codes):
    """从 cyq_perf 取筹码因子，返回 DataFrame(index=(datetime,instrument), cols=CHIP)。"""
    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    df = con.execute(
        """
        SELECT ts_code, trade_date, winner_rate, cost_5pct, cost_50pct, cost_95pct
        FROM cyq_perf WHERE ts_code IN ({}) AND trade_date >= ?
        """.format(",".join(["?"] * len(ts_codes))),
        ts_codes + ["2019-06-01"]).fetch_df()
    con.close()
    df["chip_conc"] = (df["cost_95pct"] - df["cost_5pct"]) / df["cost_50pct"].replace(0, np.nan)
    df["instrument"] = df["ts_code"].map(_qlib_sym)
    df["datetime"] = pd.to_datetime(df["trade_date"])
    return df.set_index(["datetime", "instrument"])[CHIP]


def _rank_ic(pred, y):
    d = pd.DataFrame({"p": pred, "y": y}).dropna()
    ics = d.groupby(level="datetime").apply(
        lambda g: spearmanr(g["p"], g["y"]).correlation if len(g) > 20 else np.nan).dropna()
    return ics.mean(), ics.mean() / ics.std()


def _fit(Xtr, ytr, Xva, yva):
    """固定 200 棵树(不早停)——日频 label 噪声大，早停会退化成常数预测，故用固定轮数立起 baseline。"""
    params = {"objective": "mse", "learning_rate": 0.05, "num_leaves": 64, "verbosity": -1}
    return lgb.train(params, lgb.Dataset(Xtr, ytr), num_boost_round=200,
                     valid_sets=[lgb.Dataset(Xva, yva)], callbacks=[lgb.log_evaluation(0)])


def main():
    qlib.init(provider_uri=PROVIDER_URI, region=REG_CN)
    ts_codes = _members_ts()
    uni = [_qlib_sym(t) for t in ts_codes]
    handler = init_instance_by_config({
        "class": "Alpha158", "module_path": "qlib.contrib.data.handler",
        "kwargs": {"start_time": SEG["train"][0], "end_time": SEG["test"][1],
                   "fit_start_time": SEG["train"][0], "fit_end_time": SEG["train"][1],
                   "instruments": uni}})
    dataset = init_instance_by_config({
        "class": "DatasetH", "module_path": "qlib.data.dataset",
        "kwargs": {"handler": handler, "segments": SEG}})
    parts = dataset.prepare(["train", "valid", "test"], col_set=["feature", "label"],
                            data_key=DataHandlerLP.DK_L)
    chip = _chip_factors(ts_codes)

    def split(df):
        X = df["feature"].copy()
        y = df["label"].iloc[:, 0].rename("_y")
        for c in CHIP:
            X[c] = chip[c].reindex(X.index)
        full = pd.concat([X, y], axis=1).dropna()
        feats = [c for c in full.columns if c not in CHIP + ["_y"]]
        return full[feats], full[CHIP], full["_y"]

    Xtr, ctr, ytr = split(parts[0])
    Xva, cva, yva = split(parts[1])
    Xte, cte, yte = split(parts[2])
    feats = list(Xtr.columns)
    print(f"池 {len(uni)} 只 | Alpha158特征 {len(feats)} + 筹码 {len(CHIP)} | "
          f"train {len(Xtr)} / valid {len(Xva)} / test {len(Xte)} 行")

    base = _fit(Xtr, ytr, Xva, yva)
    ic_b, ir_b = _rank_ic(pd.Series(base.predict(Xte), index=Xte.index), yte)

    feats2 = feats + CHIP
    Xtr2 = pd.concat([Xtr, ctr], axis=1)[feats2]
    Xva2 = pd.concat([Xva, cva], axis=1)[feats2]
    Xte2 = pd.concat([Xte, cte], axis=1)[feats2]
    full = _fit(Xtr2, ytr, Xva2, yva)
    ic_f, ir_f = _rank_ic(pd.Series(full.predict(Xte2), index=Xte2.index), yte)

    imp = pd.Series(full.feature_importance(importance_type="gain"), index=feats2).sort_values(ascending=False)

    print("\n==== 筹码因子增量检验(测试集 2024~2026.05) ====")
    print(f"  Alpha158        : Rank IC = {ic_b:+.4f}   IR = {ir_b:+.3f}")
    print(f"  Alpha158 + 筹码  : Rank IC = {ic_f:+.4f}   IR = {ir_f:+.3f}")
    print(f"  → IC 增量 = {ic_f-ic_b:+.4f}")
    print(f"\n  筹码因子重要性排名 / {len(feats2)}:")
    for c in CHIP:
        print(f"    {c}: 第 {list(imp.index).index(c)+1} 名 (gain={imp[c]:.0f})")
    print(f"  重要性 Top8: {list(imp.head(8).index)}")


if __name__ == "__main__":
    main()
