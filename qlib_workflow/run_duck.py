"""
run_duck.py — 用本项目自有数据(DuckDB→qlib bin)跑 qlib workflow

与 run_min.py 同一条链路(Alpha158 → LGBModel → IC → 组合回测)，区别是数据源换成
duckdb_to_qlib.py 产出的 ~/.qlib/qlib_data/duck_cn(本项目全市场后复权数据)。

股票池：从 DuckDB 取最新交易日 circ_mv 前 TOPN 高流动性股(剔 ST/北交所)，作为 instruments 列表，
避免对全市场 5700+ 只跑 Alpha158 过慢。基准用上证指数(SH000001；DuckDB 的 index_daily 未含沪深300)。
回测结束日比数据末日(2026-06-24)留几日 buffer，否则 qlib 结算需"未来一日"会越界。

环境：Python 3.12 的 .venv312（需 duckdb 读池子）。
用法：
  source .venv312/bin/activate
  python qlib_workflow/run_duck.py
依赖：~/.qlib/qlib_data/duck_cn(先跑 first10/duckdb_to_qlib.py --full)；DuckDB。
"""

import os

os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")

import duckdb
import qlib
from qlib.constant import REG_CN
from qlib.utils import init_instance_by_config
from qlib.workflow import R
from qlib.workflow.record_temp import SignalRecord, SigAnaRecord, PortAnaRecord

PROVIDER_URI = os.path.expanduser("~/.qlib/qlib_data/duck_cn")
DUCKDB_PATH = os.path.expanduser("~/AI/quart/stock_data_tushare.duckdb")
BENCHMARK = "SH000001"
TOPN = 300


def _liquid_universe(n):
    """从 DuckDB 取最新交易日 circ_mv 前 n 只(剔 ST/北交所)，返回 qlib 代码列表。"""
    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    asof = con.execute("SELECT MAX(trade_date) FROM daily_basic").fetchone()[0]
    rows = con.execute(
        """
        SELECT b.ts_code FROM daily_basic b
        WHERE b.trade_date = ?
          AND b.ts_code NOT LIKE '%.BJ'
          AND b.ts_code NOT IN (SELECT ts_code FROM stock_st WHERE trade_date = ?)
        ORDER BY b.circ_mv DESC LIMIT ?
        """, [asof, asof, n]).fetchall()
    con.close()
    return [f"{c.split('.')[1]}{c.split('.')[0]}" for (c,) in rows]


def main():
    """初始化 qlib(duck_cn) → 训练 → IC/回测。"""
    qlib.init(provider_uri=PROVIDER_URI, region=REG_CN)
    universe = _liquid_universe(TOPN)
    print(f"股票池 {len(universe)} 只，示例 {universe[:3]}")

    dataset_config = {
        "class": "DatasetH",
        "module_path": "qlib.data.dataset",
        "kwargs": {
            "handler": {
                "class": "Alpha158",
                "module_path": "qlib.contrib.data.handler",
                "kwargs": {
                    "start_time": "2018-01-01",
                    "end_time": "2026-06-24",
                    "fit_start_time": "2018-01-01",
                    "fit_end_time": "2022-12-31",
                    "instruments": universe,
                },
            },
            "segments": {
                "train": ("2018-01-01", "2022-12-31"),
                "valid": ("2023-01-01", "2023-12-31"),
                "test": ("2024-01-01", "2026-05-30"),
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
            "start_time": "2024-01-01",
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
