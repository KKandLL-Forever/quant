"""
run_momentum_regime_oos.py — 反向抱团 gate 的样本外验证(分 IS/OOS 两段看一致性)

反向 gate(ΔI 抱团度 < 滚动中位数才开动量)在全样本上 IR 0.79、回撤 −45%，但那是看了数据
才提出的假设、又在同段验证(数据窥探)。这里把 always-on / 反向gate / 基准 三条，分别在
  IS 2020-01~2023-12  与  OOS 2024-01~2026-06
两段分开统计——若反向 gate 在两个 regime 里都降回撤/提 IR，才算稳;只在一段有效则存疑。

阈值(滚动中位数)与抱团度均为 PIT(只用trailing数据)，复用 run_momentum_regime 的组件与缓存。
环境：.venv312。用法：python qlib_workflow/run_momentum_regime_oos.py
"""

import os
import sys

os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")
sys.path.insert(0, os.path.expanduser("~/AI/quart"))

import numpy as np
import pandas as pd
import qlib
from qlib.constant import REG_CN

from run_momentum_regime import (PROVIDER_URI, HP, MED_WIN,
                                 _momentum_report, _crowding_series, _stats)

SPLITS = {"IS 2020~2023": ("2020-01-01", "2023-12-31"),
          "OOS 2024~2026.6": ("2024-01-01", "2026-06-15")}


def main():
    """全程算一次，分段比较 always-on / 反向gate / 基准。"""
    qlib.init(provider_uri=PROVIDER_URI, region=REG_CN)
    report = _momentum_report()
    cal = list(report.index)
    rebal = cal[::HP]
    crowd = _crowding_series(rebal)
    med = crowd.rolling(MED_WIN, min_periods=3).median()
    enabled_inv = (crowd < med).fillna(False)

    flags = pd.Series(np.nan, index=report.index)
    for d in rebal:
        flags.loc[d] = float(enabled_inv.get(d, False))
    flags = flags.ffill().fillna(0.0).astype(bool)

    mom_net = report["return"] - report["cost"]
    bench = report["bench"]
    gated_inv = mom_net.where(flags, bench)

    for name, (s, e) in SPLITS.items():
        m = (report.index >= pd.Timestamp(s)) & (report.index <= pd.Timestamp(e))
        rows = {
            "纯基准": _stats(bench[m], bench[m]),
            "always-on 动量": _stats(mom_net[m], bench[m]),
            "反向gate 动量": _stats(gated_inv[m], bench[m]),
        }
        df = pd.DataFrame(rows).T
        pd.set_option("display.float_format", lambda x: f"{x:,.4f}")
        print(f"\n==== {name} | 反向gate ON占比={flags[m].mean():.0%} ====")
        print(df.to_string())


if __name__ == "__main__":
    main()
