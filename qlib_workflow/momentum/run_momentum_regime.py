"""
run_momentum_regime.py — RMI 抱团度择时的动量策略(动量 + 残差互信息 regime 开关)

把两条研究线合体：
  动量腿：PIT 沪深300 + 20日动量 + N=10 + hp=20(前面验证的最佳配置)。
  抱团开关：每个调仓日，用 PIT 沪深300成分 + 过去 300 日，算全市场平均残差互信息 ΔI(抱团度)。
           PIT 规则：抱团度 > 自身过去若干期滚动中位数 → 判为"抱团形成" → 当期开启动量；
           否则关闭 → 退回持有基准(上证指数)。阈值用滚动历史(PIT)，无未来函数。

对比三条：always-on 动量 / RMI 择时动量 / 纯基准，看择时是真降回撤提收益，还是同步信号帮倒忙。

抱团度逐期计算较慢，缓存到 first10/cache/market_crowding.parquet(改参数需删缓存)。
数据：~/.qlib/qlib_data/duck_cn + first10/cache/hs300_members.parquet。环境：.venv312。
用法：python qlib_workflow/run_momentum_regime.py
"""

import os
import sys

os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")
sys.path.insert(0, os.path.expanduser("~/AI/quart"))

import numpy as np
import pandas as pd
import qlib
from qlib.constant import REG_CN
from qlib.data import D
from qlib.backtest import backtest
from qlib.contrib.strategy import TopkDropoutStrategy
from qlib.contrib.evaluate import risk_analysis

from run_momentum_hs300 import (PROVIDER_URI, START, END, BENCHMARK,
                                _load_membership, _signal)
import rmi_p0
from cache_tushare import DUCKDB_PATH

N = 10
HP = 20
BINS = 7
MED_WIN = 6          # 抱团度滚动中位数窗口(期)
CROWD_CACHE = os.path.expanduser("~/AI/quart/first10/cache/market_crowding.parquet")
MEMBERS_PARQUET = os.path.expanduser("~/AI/quart/first10/cache/hs300_members.parquet")


def _members_con_asof():
    """返回 (按快照日排序的 [(Timestamp, set(con_code))], ) 用于 DuckDB 读取。"""
    df = pd.read_parquet(MEMBERS_PARQUET)
    df["dt"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
    snaps = [(dt, set(sub["con_code"])) for dt, sub in df.groupby("dt")]
    snaps.sort(key=lambda x: x[0])
    return snaps


def _asof(snaps, day):
    """trade_date <= day 的最近快照成分集合。"""
    chosen = set()
    for dt, s in snaps:
        if dt <= day:
            chosen = s
        else:
            break
    return chosen


def _crowding_series(rebal_dates):
    """每个调仓日的全市场平均 ΔI(抱团度)，带缓存。"""
    if os.path.exists(CROWD_CACHE):
        s = pd.read_parquet(CROWD_CACHE)["crowding"]
        s.index = pd.to_datetime(s.index)
        if set(rebal_dates).issubset(set(s.index)):
            return s.reindex(rebal_dates)
    snaps = _members_con_asof()
    import duckdb
    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    rng = np.random.default_rng(42)
    curve = rmi_p0._gaussian_mi_curve(rmi_p0.WINDOW, BINS, rng)
    out = {}
    for d in rebal_dates:
        members = sorted(_asof(snaps, d))
        if len(members) < 30:
            out[d] = np.nan
            continue
        ret, codes = rmi_p0._returns_matrix(con, members, d.strftime("%Y-%m-%d"))
        if ret.shape[0] < rmi_p0.WINDOW - 10 or len(codes) < 30:
            out[d] = np.nan
            continue
        _, dI, _ = rmi_p0.compute(ret, BINS, rng, curve=curve)
        iu = np.triu_indices(len(codes), 1)
        out[d] = float(dI[iu].mean())
    con.close()
    s = pd.Series(out, name="crowding")
    s.to_frame().to_parquet(CROWD_CACHE)
    return s


def _momentum_report():
    """跑 N=10/hp=20 动量回测，返回日度 report(含 return/bench/cost)。"""
    snaps, universe = _load_membership()
    signal = _signal(universe, snaps)
    strat = TopkDropoutStrategy(signal=signal, topk=N, n_drop=N, only_tradable=True)
    executor = {"class": "SimulatorExecutor", "module_path": "qlib.backtest.executor",
                "kwargs": {"time_per_step": "day", "generate_portfolio_metrics": True, "verbose": False}}
    ex = {"freq": "day", "limit_threshold": 0.095, "deal_price": "close",
          "open_cost": 0.0005, "close_cost": 0.0015, "min_cost": 5}
    pm, _ = backtest(START, END, strategy=strat, executor=executor,
                     benchmark=BENCHMARK, account=100000000, exchange_kwargs=ex)
    report, _ = pm["1day"]
    return report


def _stats(daily_ret, bench):
    """日度收益 → (绝对年化, 超额年化, 超额IR, 绝对回撤, 期末净值)。"""
    a = risk_analysis(daily_ret, freq="day")["risk"]
    e = risk_analysis(daily_ret - bench, freq="day")["risk"]
    nav = (1 + daily_ret).cumprod().iloc[-1]
    return {"绝对年化": a["annualized_return"], "超额年化": e["annualized_return"],
            "超额IR": e["information_ratio"], "绝对回撤": a["max_drawdown"], "期末净值": nav}


def main():
    """合体回测：always-on 动量 vs RMI 择时动量 vs 基准。"""
    qlib.init(provider_uri=PROVIDER_URI, region=REG_CN)
    report = _momentum_report()
    cal = list(report.index)
    rebal_dates = cal[::HP]

    crowd = _crowding_series(rebal_dates)
    med = crowd.rolling(MED_WIN, min_periods=3).median()
    enabled = (crowd > med).fillna(False)
    enabled_inv = (crowd < med).fillna(False)
    on_ratio, on_ratio_inv = enabled.mean(), enabled_inv.mean()

    def _to_daily(flags):
        s = pd.Series(np.nan, index=report.index)
        for d in rebal_dates:
            s.loc[d] = float(flags.get(d, False))
        return s.ffill().fillna(0.0).astype(bool)

    mom_net = report["return"] - report["cost"]
    bench = report["bench"]
    gated = mom_net.where(_to_daily(enabled), bench)
    gated_inv = mom_net.where(_to_daily(enabled_inv), bench)

    rows = {
        "纯基准(上证)": _stats(bench, bench),
        "always-on 动量": _stats(mom_net, bench),
        f"正向择时(高抱团开 ON{on_ratio:.0%})": _stats(gated, bench),
        f"反向择时(低抱团开 ON{on_ratio_inv:.0%})": _stats(gated_inv, bench),
    }
    df = pd.DataFrame(rows).T
    pd.set_option("display.float_format", lambda x: f"{x:,.4f}")
    print(f"\n==== 抱团择时动量 (N={N}, hp={HP}, 2020~2026) ====")
    print(df.to_string())


if __name__ == "__main__":
    main()
