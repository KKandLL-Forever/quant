"""
run_momentum_oos.py — PIT 沪深300 动量：样本内选参 + 样本外验证

防过拟合的标准做法：
  样本内(IS) 2020-01-01~2023-12-31 上网格扫 N∈{10,20,30} × holdingPeriod∈{10,20,30}(returnPeriod=20)，
  按 IS 超额 IR 选出最佳参数；再把该参数拿到从未参与选参的 样本外(OOS) 2024-01-01~2026-06-15 上检验。
  若 OOS 绩效和 IS 接近 → 信号稳健；若 OOS 大幅塌缩 → IS 上的好成绩是过拟合/数据窥探。

数据：~/.qlib/qlib_data/duck_cn + first10/cache/hs300_members.parquet。基准：上证指数。
环境：.venv312。用法：python qlib_workflow/run_momentum_oos.py
"""

import os

os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")

import pandas as pd
import qlib
from qlib.constant import REG_CN
from qlib.data import D
from qlib.backtest import backtest
from qlib.contrib.strategy import TopkDropoutStrategy
from qlib.contrib.evaluate import risk_analysis

from run_momentum_hs300 import PROVIDER_URI, BENCHMARK, RETURN_PERIOD, _load_membership, _members_asof

IS_START, IS_END = "2020-01-01", "2023-12-31"
OOS_START, OOS_END = "2024-01-01", "2026-06-15"
N_LIST = [10, 20, 30]
HP_LIST = [10, 20, 30]


def _signal_window(universe, snaps, start, end, hp):
    """在 [start,end] 窗口内构造动量信号(带前置 buffer 保证首调仓日有值)。"""
    feat_start = (pd.Timestamp(start) - pd.Timedelta(days=60)).strftime("%Y-%m-%d")
    expr = f"Ref($close,1)/Ref($close,{RETURN_PERIOD + 1})-1"
    df = D.features(universe, [expr], start_time=feat_start, end_time=end)
    df.columns = ["mom"]
    wide = df["mom"].unstack(level="instrument")
    win_cal = [d for d in wide.index if d >= pd.Timestamp(start)]
    rebal = set(win_cal[::hp])
    masked = pd.DataFrame(index=wide.index, columns=wide.columns, dtype=float)
    for d in wide.index:
        if d in rebal:
            members = _members_asof(snaps, d)
            cols = [c for c in wide.columns if c in members]
            masked.loc[d, cols] = wide.loc[d, cols]
    masked = masked.ffill()
    sig = masked.stack()
    sig.index = sig.index.set_names(["datetime", "instrument"])
    return sig


def _bt(signal, n, start, end):
    """跑一次回测，返回 (超额年化, 超额IR, 绝对年化, 绝对回撤)。"""
    strat = TopkDropoutStrategy(signal=signal, topk=n, n_drop=n, only_tradable=True)
    executor = {"class": "SimulatorExecutor", "module_path": "qlib.backtest.executor",
                "kwargs": {"time_per_step": "day", "generate_portfolio_metrics": True, "verbose": False}}
    ex = {"freq": "day", "limit_threshold": 0.095, "deal_price": "close",
          "open_cost": 0.0005, "close_cost": 0.0015, "min_cost": 5}
    pm, _ = backtest(start, end, strategy=strat, executor=executor,
                     benchmark=BENCHMARK, account=100000000, exchange_kwargs=ex)
    report, _ = pm["1day"]
    a = risk_analysis(report["return"] - report["cost"], freq="day")["risk"]
    e = risk_analysis(report["return"] - report["bench"] - report["cost"], freq="day")["risk"]
    return e["annualized_return"], e["information_ratio"], a["annualized_return"], a["max_drawdown"]


def main():
    """IS 网格选参 → OOS 检验。"""
    qlib.init(provider_uri=PROVIDER_URI, region=REG_CN)
    snaps, universe = _load_membership()

    print("=== 样本内 (2020~2023) 网格选参 ===")
    sig_is = {hp: _signal_window(universe, snaps, IS_START, IS_END, hp) for hp in HP_LIST}
    is_rows = {}
    for hp in HP_LIST:
        for n in N_LIST:
            exc, ir, ab, dd = _bt(sig_is[hp], n, IS_START, IS_END)
            is_rows[(n, hp)] = {"IS超额年化": exc, "IS超额IR": ir, "IS绝对年化": ab, "IS回撤": dd}
    is_df = pd.DataFrame(is_rows).T
    is_df.index = [f"N={n},hp={hp}" for (n, hp) in is_df.index]
    pd.set_option("display.float_format", lambda x: f"{x:,.4f}")
    print(is_df.to_string())

    best = max(is_rows, key=lambda k: is_rows[k]["IS超额IR"])
    bn, bhp = best
    print(f"\n>>> IS 最佳参数(按超额IR): N={bn}, hp={bhp}  (IS超额IR={is_rows[best]['IS超额IR']:.3f})")

    print("\n=== 样本外 (2024~2026.6) 检验 ===")
    print("① IS 最佳参数在 OOS 的表现:")
    sig_oos_best = _signal_window(universe, snaps, OOS_START, OOS_END, bhp)
    exc, ir, ab, dd = _bt(sig_oos_best, bn, OOS_START, OOS_END)
    print(f"   N={bn},hp={bhp}: OOS超额年化={exc:.4f}  OOS超额IR={ir:.4f}  OOS绝对年化={ab:.4f}  OOS回撤={dd:.4f}")

    print("\n② 全网格在 OOS 的表现(看 IS 最佳是否仍居前):")
    oos_rows = {}
    sig_oos = {hp: _signal_window(universe, snaps, OOS_START, OOS_END, hp) for hp in HP_LIST}
    for hp in HP_LIST:
        for n in N_LIST:
            exc, ir, ab, dd = _bt(sig_oos[hp], n, OOS_START, OOS_END)
            oos_rows[f"N={n},hp={hp}"] = {"OOS超额年化": exc, "OOS超额IR": ir, "OOS绝对年化": ab, "OOS回撤": dd}
    print(pd.DataFrame(oos_rows).T.to_string())


if __name__ == "__main__":
    main()
