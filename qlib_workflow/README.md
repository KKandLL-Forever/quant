# qlib_workflow — 量化研究标准化(qlib)

把研究流程从"散兵游勇"收敛到 qlib 标准范式的落脚点。

## 环境

qlib 不支持 Python 3.14,需单独的 3.12 环境(与项目主 `.venv` 隔离):

```bash
python3.12 -m venv .venv312
source .venv312/bin/activate
pip install pyqlib
```

样例数据(qlib 官方 A 股,Yahoo 源,仅供跑通流程):

```python
from qlib.tests.data import GetData
GetData().qlib_data(target_dir='~/.qlib/qlib_data/cn_data', region='cn', exists_skip=True)
```

## 跑最小 workflow

```bash
source .venv312/bin/activate
python qlib_workflow/run_min.py
```

链路:Alpha158 因子 → LGBModel → 训练 → IC/ICIR(SignalRecord/SigAnaRecord)
→ TopkDropoutStrategy 组合回测(PortAnaRecord)。
基准结果约:IC≈0.048,Rank IC≈0.044,含成本超额年化≈5%,信息比≈0.57。

## 已知坑(已在 run_min.py 处理)

- 新版 mlflow 弃用文件存储 → 设 `MLFLOW_ALLOW_FILE_STORE=true`。
- 新版 qlib 的 `TopkDropoutStrategy` 需显式传 `signal=(model, dataset)`。

## 下一步:接入自有数据

当前用的是 qlib 官方样例数据。要用本项目的 DuckDB,需要写一个
**DuckDB → qlib bin 格式转换器**(qlib 用自有二进制列存,不能直接读 DuckDB),
之后把 `provider_uri` 指向转换产物即可复用同一套 workflow。
