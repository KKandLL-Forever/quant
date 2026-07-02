"""
run_momentum_hs300.py — 经典动量策略(point-in-time 沪深300成分，去幸存者偏差)

相对 run_momentum.py 的唯一区别：股票池不再是"今天的大盘股快照"，而是
**每个调仓日取当时的沪深300成分**(tushare index_weight 历史快照，缓存于
first10/cache/hs300_members.parquet)。这样只用调仓时点已知的信息，消除幸存者偏差，
给"动量在 A 股到底行不行"一个诚实答案。

机制：先对"历年曾入选沪深300的全体"算动量因子；每个调仓日把信号 mask 成"仅当时成分"，
其余日 ffill 保持不变(=持有到下次调仓)。其余(手续费/涨跌停/TopkDropout)同 run_momentum。

数据：~/.qlib/qlib_data/duck_cn + first10/cache/hs300_members.parquet。基准：上证指数 SH000001。
环境：.venv312。用法：python qlib_workflow/run_momentum_hs300.py
"""

import os
_ROOT = os.path.dirname(os.path.abspath(__file__))
while _ROOT != "/" and not os.path.exists(os.path.join(_ROOT, "cache_tushare.py")):
    _ROOT = os.path.dirname(_ROOT)

os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")

import pandas as pd
import qlib
from qlib.constant import REG_CN
from qlib.data import D
from qlib.backtest import backtest
from qlib.contrib.strategy import TopkDropoutStrategy
from qlib.contrib.evaluate import risk_analysis

PROVIDER_URI = os.path.expanduser("~/.qlib/qlib_data/duck_cn")
MEMBERS_PARQUET = os.path.join(_ROOT, "first10/cache/hs300_members.parquet")

RETURN_PERIOD = 20
HOLDING_PERIOD = 20
RATIO = 0.1
START, END = "2020-01-01", "2026-06-15"
BENCHMARK = "SH000001"


def _to_qlib(con_code):
    """601318.SH → SH601318。"""
    c, m = con_code.split(".")
    return f"{m}{c}"


def _load_membership():
    """返回 (按快照日排序的列表[(Timestamp, set(qlib符号))], 全体符号集合)。"""
    df = pd.read_parquet(MEMBERS_PARQUET)
    df["dt"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
    df["sym"] = df["con_code"].map(_to_qlib)
    snaps = []
    for dt, sub in df.groupby("dt"):
        snaps.append((dt, set(sub["sym"])))
    snaps.sort(key=lambda x: x[0])
    universe = sorted(set(df["sym"]))
    return snaps, universe


def _members_asof(snaps, day):
    """取 trade_date <= day 的最近一张成分快照。"""
    chosen = None
    for dt, s in snaps:
        if dt <= day:
            chosen = s
        else:
            break
    return chosen or set()


def _signal(universe, snaps):
    """算动量因子，每个调仓日仅保留当时成分，其余日 ffill，返回 (datetime,instrument) Series。"""
    expr = f"Ref($close,1)/Ref($close,{RETURN_PERIOD + 1})-1"
    df = D.features(universe, [expr], start_time=START, end_time=END)
    df.columns = ["mom"]
    wide = df["mom"].unstack(level="instrument")
    cal = list(wide.index)
    rebal = set(cal[::HOLDING_PERIOD])
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


def main():
    """走通 PIT 沪深300 动量回测全流程。"""
    qlib.init(provider_uri=PROVIDER_URI, region=REG_CN)
    snaps, universe = _load_membership()
    N = max(1, int(300 * RATIO))
    print(f"参数：PIT 沪深300成分 | 历年全体 {len(universe)} 只 | 快照 {len(snaps)} 期 | "
          f"returnPeriod={RETURN_PERIOD} | holdingPeriod={HOLDING_PERIOD} | N={N}")

    signal = _signal(universe, snaps)
    print(f"动量信号已构造(每个调仓日仅当时成分)，覆盖 {signal.index.get_level_values('datetime').nunique()} 个交易日")

    strategy = TopkDropoutStrategy(signal=signal, topk=N, n_drop=N, only_tradable=True)
    executor = {"class": "SimulatorExecutor", "module_path": "qlib.backtest.executor",
                "kwargs": {"time_per_step": "day", "generate_portfolio_metrics": True, "verbose": False}}
    exchange_kwargs = {"freq": "day", "limit_threshold": 0.095, "deal_price": "close",
                       "open_cost": 0.0005, "close_cost": 0.0015, "min_cost": 5}
    pm, _ = backtest(START, END, strategy=strategy, executor=executor,
                     benchmark=BENCHMARK, account=100000000, exchange_kwargs=exchange_kwargs)
    report, _pos = pm["1day"]

    abs_ana = risk_analysis(report["return"] - report["cost"], freq="day")
    exc_ana = risk_analysis(report["return"] - report["bench"] - report["cost"], freq="day")
    print("\n==== PIT 沪深300 动量绩效 (2020-01-01 ~ 2026-06-15) ====")
    print("— 绝对收益(含成本) —"); print(abs_ana)
    print("\n— 超额收益(对 上证指数，含成本) —"); print(exc_ana)
    nav = (1 + report["return"] - report["cost"]).cumprod()
    print(f"\n期末净值={nav.iloc[-1]:.3f} | 累计收益={(nav.iloc[-1]-1):.1%}")


if __name__ == "__main__":
    main()
