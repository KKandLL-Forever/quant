"""
run_daily_decision.py — 给定股票列表,输出每只的「全市场分位 + 强弱建议」(Alpha158+LGBM)

用途:对你的 watchlist,用 qlib(Alpha158+LGBM)算每只票在**全市场参考池**里的预测分位,
映射成 偏强(买入)/ 中性(持有)/ 偏弱(回避)。这是**相对全市场的横截面强弱读数**,
不是涨跌预言,也不含仓位(只给分和建议)。

⚠️ 单日截面分位噪声极大,故默认输出**过去 10 个交易日的平滑分位 + 趋势箭头**(--history N 可调),
看持续性和趋势,而非单日绝对值;均值持续高/低才有意义。

机制:参考池=as-of 日 circ_mv 前 POOL 只 ∪ 你的列表 → Alpha158 训练 LGBM(固定200树)
→ 预测 as-of 日 → 你列表每只的预测值在当日横截面的百分位 → 阈值映射建议。

⚠️ Alpha158 在 A 股横截面 IC 本就弱(~0.01-0.02),这是个**弱量化参考**,不是强信号;
单日预测噪声大,务必结合基本面/事件自行判断。

环境：.venv312。
用法：
  python qlib_workflow/run_daily_decision.py --codes 600519.SH,000001.SZ,300750.SZ
  python qlib_workflow/run_daily_decision.py --codes ... --asof 20260601 --pool 800
依赖：~/.qlib/qlib_data/duck_cn;DuckDB(daily_basic/stock_meta);lightgbm。
"""

import argparse
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

PROVIDER_URI = os.path.expanduser("~/.qlib/qlib_data/duck_cn")
DUCKDB_PATH = os.path.expanduser("~/AI/quart/stock_data_tushare.duckdb")


def _to_qlib(ts):
    c, m = ts.split(".")
    return f"{m}{c}"


def _ref_pool(con, asof_dash, pool, extra):
    """as-of 日 circ_mv 前 pool 只(剔ST/北交)∪ 用户列表,返回 qlib 代码列表。"""
    rows = con.execute("""
        SELECT ts_code FROM daily_basic WHERE trade_date=? AND ts_code NOT LIKE '%.BJ'
          AND ts_code NOT IN (SELECT ts_code FROM stock_st WHERE trade_date=?)
        ORDER BY circ_mv DESC LIMIT ?""", [asof_dash, asof_dash, pool]).fetchall()
    base = {_to_qlib(r[0]) for r in rows}
    return sorted(base | set(extra))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", required=True, help="watchlist,逗号分隔 ts_code,如 600519.SH,000001.SZ")
    ap.add_argument("--asof", default=None, help="as-of 日 YYYYMMDD(默认最新交易日)")
    ap.add_argument("--pool", type=int, default=800, help="全市场参考池大小(circ_mv top N)")
    ap.add_argument("--topn", type=int, default=20, help="额外打印参考池中模型最看好的前 N 只")
    ap.add_argument("--history", type=int, default=10, help="滚动 N 个交易日的平滑分位(默认10,看趋势比单日靠谱)")
    args = ap.parse_args()

    watch_ts = [c.strip() for c in args.codes.split(",") if c.strip()]
    watch = [_to_qlib(c) for c in watch_ts]

    qlib.init(provider_uri=PROVIDER_URI, region=REG_CN)
    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    asof = args.asof or con.execute("SELECT strftime(MAX(trade_date),'%Y%m%d') FROM daily").fetchone()[0]
    asof_dash = f"{asof[:4]}-{asof[4:6]}-{asof[6:8]}"
    uni = _ref_pool(con, asof_dash, args.pool, watch)
    names = dict(con.execute("SELECT ts_code, name FROM stock_meta").fetchall())
    con.close()
    print(f"as-of {asof_dash} | 参考池 {len(uni)} 只 | watchlist {len(watch)} 只 | 训练中...")

    handler = init_instance_by_config({"class": "Alpha158", "module_path": "qlib.contrib.data.handler",
        "kwargs": {"start_time": "2020-01-01", "end_time": asof_dash,
                   "fit_start_time": "2020-01-01", "fit_end_time": "2023-12-31", "instruments": uni}})
    ds = init_instance_by_config({"class": "DatasetH", "module_path": "qlib.data.dataset",
        "kwargs": {"handler": handler, "segments": {"train": ("2020-01-01", "2023-12-31"),
                   "valid": ("2024-01-01", "2024-12-31"), "test": ("2025-01-01", asof_dash)}}})
    tr, va = ds.prepare(["train", "valid"], col_set=["feature", "label"], data_key=DataHandlerLP.DK_L)
    te = ds.prepare("test", col_set=["feature"], data_key=DataHandlerLP.DK_I)

    def xy(df):
        X = df["feature"]; y = df["label"].iloc[:, 0]
        m = X.notna().all(axis=1) & y.notna()
        return X[m], y[m]
    Xtr, ytr = xy(tr); Xva, yva = xy(va)
    params = {"objective": "mse", "learning_rate": 0.05, "num_leaves": 64, "verbosity": -1}
    model = lgb.train(params, lgb.Dataset(Xtr, ytr), num_boost_round=200,
                      valid_sets=[lgb.Dataset(Xva, yva)], callbacks=[lgb.log_evaluation(0)])

    Xte = te["feature"]
    Xte = Xte[Xte.notna().all(axis=1)]
    pred = pd.Series(model.predict(Xte), index=Xte.index)
    pctall = pred.groupby(level="datetime").rank(pct=True)
    dates = sorted(pred.index.get_level_values("datetime").unique())
    win = dates[-args.history:]
    last = win[-1]
    day = pred.xs(last, level="datetime")
    pct = day.rank(pct=True)

    print(f"\n=== 决策({win[0].date()}~{last.date()},共 {len(win)} 个交易日平滑;池 {len(day)} 只)===")
    print(f"{'代码':<11}{'名称':<10}{'均值':>6}{'最新':>6}{'趋势':>5}  建议")
    rows = []
    for ts, sym in zip(watch_ts, watch):
        s = pctall[pctall.index.get_level_values("instrument") == sym]
        s = s[s.index.get_level_values("datetime").isin(win)]
        if len(s) == 0:
            rows.append((-1, ts, sym, "—", "—", "", "数据不足")); continue
        vals = s.sort_index().values
        avg = float(vals.mean()); latest = float(vals[-1])
        half = max(1, len(vals) // 2)
        diff = vals[-half:].mean() - vals[:half].mean()
        arrow = "↑" if diff > 0.1 else "↓" if diff < -0.1 else "→"
        sug = "买入/偏强" if avg >= 0.7 else "回避/偏弱" if avg < 0.3 else "持有/中性"
        rows.append((avg, ts, sym, f"{avg*100:.0f}%", f"{latest*100:.0f}%", arrow, sug))
    for avg, ts, sym, a, l, ar, sug in sorted(rows, reverse=True):
        print(f"{ts:<11}{names.get(ts,''):<10}{a:>6}{l:>6}{ar:>5}  {sug}")

    if args.topn > 0:
        def _ts(sym):
            m, c = sym[:2], sym[2:]
            return f"{c}.{m}"
        top = day.sort_values(ascending=False).head(args.topn)
        print(f"\n=== 参考池模型最看好 Top{args.topn}({last.date()})===")
        print(f"{'排名':<5}{'代码':<11}{'名称':<10}{'分位':>7}")
        for i, (sym, _) in enumerate(top.items(), 1):
            ts = _ts(sym)
            print(f"{i:<5}{ts:<11}{names.get(ts,''):<10}{pct[sym]*100:>6.0f}%")


if __name__ == "__main__":
    main()
