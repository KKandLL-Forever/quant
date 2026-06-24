"""
run_min.py — qlib 最小可跑 workflow 模板（LightGBM + Alpha158 + CSI300）

目的：把"散兵游勇"的研究流程收敛到 qlib 标准范式，跑通一条完整链路作为模板：
  数据(qlib bin) → 特征(Alpha158 因子集) → 模型(LGBModel) → 训练 →
  信号/IC 记录(SignalRecord) → 组合回测(TopkDropoutStrategy + 回测报告)。

用的是 qlib 官方 A 股样例数据(~/.qlib/qlib_data/cn_data，Yahoo 源，仅供跑通流程)，
与本项目的 DuckDB 无关——接入自有数据是后续"DuckDB→qlib bin 转换器"的事。

环境：需 Python 3.12 的 .venv312（qlib 不支持 3.14）。
用法：
  source .venv312/bin/activate
  python qlib_workflow/run_min.py
产出：控制台打印 IC/ICIR 与回测年化/夏普/回撤；mlflow 记录落在 qlib_workflow/mlruns。
"""

import os

os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")

import qlib
from qlib.constant import REG_CN
from qlib.utils import init_instance_by_config
from qlib.workflow import R
from qlib.workflow.record_temp import SignalRecord, SigAnaRecord, PortAnaRecord

PROVIDER_URI = os.path.expanduser("~/.qlib/qlib_data/cn_data")
MARKET = "csi300"
BENCHMARK = "SH000300"

DATASET_CONFIG = {
    "class": "DatasetH",
    "module_path": "qlib.data.dataset",
    "kwargs": {
        "handler": {
            "class": "Alpha158",
            "module_path": "qlib.contrib.data.handler",
            "kwargs": {
                "start_time": "2008-01-01",
                "end_time": "2020-08-01",
                "fit_start_time": "2008-01-01",
                "fit_end_time": "2014-12-31",
                "instruments": MARKET,
            },
        },
        "segments": {
            "train": ("2008-01-01", "2014-12-31"),
            "valid": ("2015-01-01", "2016-12-31"),
            "test": ("2017-01-01", "2020-08-01"),
        },
    },
}

MODEL_CONFIG = {
    "class": "LGBModel",
    "module_path": "qlib.contrib.model.gbdt",
    "kwargs": {
        "loss": "mse",
        "learning_rate": 0.05,
        "num_leaves": 64,
        "n_estimators": 200,
    },
}

PORT_ANALYSIS_CONFIG = {
    "strategy": {
        "class": "TopkDropoutStrategy",
        "module_path": "qlib.contrib.strategy.signal_strategy",
        "kwargs": {"topk": 50, "n_drop": 5},
    },
    "backtest": {
        "start_time": "2017-01-01",
        "end_time": "2020-08-01",
        "account": 100000000,
        "benchmark": BENCHMARK,
        "exchange_kwargs": {
            "freq": "day",
            "limit_threshold": 0.095,
            "deal_price": "close",
            "open_cost": 0.0005,
            "close_cost": 0.0015,
            "min_cost": 5,
        },
    },
}


def main():
    """初始化 qlib → 训练模型 → 记录 IC/回测。"""
    qlib.init(provider_uri=PROVIDER_URI, region=REG_CN)

    model = init_instance_by_config(MODEL_CONFIG)
    dataset = init_instance_by_config(DATASET_CONFIG)

    with R.start(experiment_name="min_workflow"):
        model.fit(dataset)
        R.save_objects(trained_model=model)
        recorder = R.get_recorder()

        sr = SignalRecord(model, dataset, recorder)
        sr.generate()
        sar = SigAnaRecord(recorder)
        sar.generate()
        PORT_ANALYSIS_CONFIG["strategy"]["kwargs"]["signal"] = (model, dataset)
        par = PortAnaRecord(recorder, PORT_ANALYSIS_CONFIG, "day")
        par.generate()

        print("\n==== 结果摘要 ====")
        print("Recorder:", recorder.id, "| 详细指标见 qlib_workflow/mlruns")
        metrics = recorder.list_metrics()
        for k in sorted(metrics):
            print(f"  {k}: {metrics[k]}")


if __name__ == "__main__":
    main()
