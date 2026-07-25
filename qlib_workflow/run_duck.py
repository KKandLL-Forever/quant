"""
run_duck.py — 用本项目自有数据(DuckDB→qlib bin)跑 qlib workflow

与 run_min.py 同一条链路(Alpha158 → LGBModel → IC → 组合回测)，区别是数据源换成
duckdb_to_qlib.py 产出的 ~/.qlib/qlib_data/duck_cn(本项目全市场后复权数据)。

股票池：PIT 动态池。每月初按**当时**的 circ_mv 取前 TOPN(剔 ST/北交所)，写成 qlib
带时间跨度的 instruments 文件(每只票只在它当月真正在池内的那段被纳入)，消除"用今天名单
回填历史"的幸存者/未来函数偏差。基准用上证指数(SH000001；DuckDB 的 index_daily 未含沪深300)。
回测结束日比数据末日留几日 buffer，否则 qlib 结算需"未来一日"会越界。

环境：Python 3.12 的 .venv312（需 duckdb 读池子）。
用法：
  source .venv312/bin/activate
  python qlib_workflow/run_duck.py
依赖：~/.qlib/qlib_data/duck_cn(先跑 first10/duckdb_to_qlib.py --full)；DuckDB。
"""

import os
_ROOT = os.path.dirname(os.path.abspath(__file__))
while _ROOT != "/" and not os.path.exists(os.path.join(_ROOT, "cache_tushare.py")):
    _ROOT = os.path.dirname(_ROOT)

os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")

import duckdb
import qlib
from qlib.constant import REG_CN
from qlib.utils import init_instance_by_config
from qlib.workflow import R
from qlib.workflow.record_temp import SignalRecord, SigAnaRecord, PortAnaRecord

PROVIDER_URI = os.path.expanduser("~/.qlib/qlib_data/duck_cn")
DUCKDB_PATH = os.path.join(_ROOT, "stock_data_tushare.duckdb")
BENCHMARK = "SH000001"
TOPN = 800
PIT_NAME = "pit800"


def _to_qlib(ts):
    c, m = ts.split(".")
    return f"{m}{c}"


def _build_pit_instruments(n, start, end):
    """每月初按当时 circ_mv 取前 n 只(剔 ST/北交所),写成带跨度的 qlib instruments 文件,返回市场名。"""
    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    cal = [r[0] for r in con.execute(
        "SELECT DISTINCT trade_date FROM daily WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date",
        [start, end]).fetchall()]
    rebal = []
    seen = set()
    for d in cal:
        ym = (d.year, d.month)
        if ym not in seen:
            seen.add(ym); rebal.append(d)
    spans = {}
    for i, r in enumerate(rebal):
        nxt = rebal[i + 1] if i + 1 < len(rebal) else cal[-1]
        rs = r.strftime("%Y-%m-%d")
        rows = con.execute(
            """
            SELECT ts_code FROM daily_basic
            WHERE trade_date = ? AND ts_code NOT LIKE '%.BJ'
              AND ts_code NOT IN (SELECT ts_code FROM stock_st WHERE trade_date = ?)
            ORDER BY circ_mv DESC LIMIT ?
            """, [rs, rs, n]).fetchall()
        for (ts,) in rows:
            spans.setdefault(_to_qlib(ts), []).append((rs, nxt.strftime("%Y-%m-%d")))
    con.close()
    path = os.path.join(PROVIDER_URI, "instruments", f"{PIT_NAME}.txt")
    with open(path, "w", encoding="utf-8") as f:
        for code in sorted(spans):
            for s, e in spans[code]:
                f.write(f"{code}\t{s}\t{e}\n")
    print(f"PIT 池:{len(rebal)} 个调仓月, {len(spans)} 只票曾入池, 写入 {path}")
    return PIT_NAME


def main():
    """初始化 qlib(duck_cn) → 训练 → IC/回测。"""
    qlib.init(provider_uri=PROVIDER_URI, region=REG_CN)
    universe = _build_pit_instruments(TOPN, "2020-01-01", "2026-06-26")

    dataset_config = {
        "class": "DatasetH",
        "module_path": "qlib.data.dataset",
        "kwargs": {
            "handler": {
                "class": "Alpha158",
                "module_path": "qlib.contrib.data.handler",
                "kwargs": {
                    "start_time": "2020-01-01",
                    "end_time": "2026-06-26",
                    "fit_start_time": "2020-01-01",
                    "fit_end_time": "2023-12-31",
                    "instruments": universe,
                },
            },
            "segments": {
                "train": ("2020-01-01", "2023-12-31"),
                "valid": ("2024-01-01", "2024-12-31"),
                "test": ("2025-01-01", "2026-05-30"),
            },
        },
    }
    model_config = {
        "class": "LGBModel",
        "module_path": "qlib.contrib.model.gbdt",
        "kwargs": {"loss": "mse", "learning_rate": 0.05, "num_leaves": 64, "n_estimators": 200},
    }
    port_config = {
        "strategy": {
            "class": "TopkDropoutStrategy",
            "module_path": "qlib.contrib.strategy.signal_strategy",
            "kwargs": {"topk": 30, "n_drop": 3},
        },
        "backtest": {
            "start_time": "2025-01-01",
            "end_time": "2026-05-30",
            "account": 100000000,
            "benchmark": BENCHMARK,
            "exchange_kwargs": {
                "freq": "day", "limit_threshold": 0.095, "deal_price": "close",
                "open_cost": 0.0005, "close_cost": 0.0015, "min_cost": 5,
            },
        },
    }

    model = init_instance_by_config(model_config)
    dataset = init_instance_by_config(dataset_config)
    with R.start(experiment_name="duck_workflow"):
        model.fit(dataset)
        recorder = R.get_recorder()
        SignalRecord(model, dataset, recorder).generate()
        SigAnaRecord(recorder).generate()
        port_config["strategy"]["kwargs"]["signal"] = (model, dataset)
        PortAnaRecord(recorder, port_config, "day").generate()

        print("\n==== 结果摘要(自有数据) ====")
        for k, v in sorted(recorder.list_metrics().items()):
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
