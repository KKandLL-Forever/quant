# xiaoxifu — 龙头动量轮动策略(复现)

复现小西西弗(MatrixSpk)公众号 R 版策略,原文《龙头动量轮动策略代码更新(年化超110%)》。
源:https://mp.weixin.qq.com/s/BsuTF4JrDikMwkSnprFiKA (原文整理在仓库根 `龙头动量轮动策略代码更新（年化超110%）.md`)。

## 逻辑
- 标的池:22 只各赛道龙头股;基准:科创50ETF(588000);辅助对照:龙头等权组合。
- 风险调整动量 `Adj_Momentum = N日日收益均值 / sqrt(N日日收益方差)`(默认 N=20)。
- 每 K 天调仓(默认 5):原始动量(N日均值)为正 → 按 Adj_Momentum 降序取前 L(默认 5)→ 按 Adj_Momentum 归一化配权。
- 权重滞后 1 天(T 决策 T+1 执行,规避未来函数)。
- 指标:年化收益 / 年化波动 / 最大回撤 / 夏普 / 卡玛。

## 数据
- 个股前复权收盘:本地 DuckDB `daily.close*adj_factor/最新adj_factor`。
- 基准 588000:tushare `fund_daily + fund_adj` 前复权(在线;`--no-bench` 可跳过)。
- 注:复权方式(前/后)对收益率与动量无影响(每列仅差一个常数,pct_change 不变)。

## 用法
```
python xiaoxifu/leader_momentum.py --N 20 --K 5 --L 5 --start 2024-01-01
```
产出在 `龙头动量轮动策略_N{N}_K{K}_L{L}/`:performance_summary / cumulative_returns / drawdowns / daily_weights_nonzero / stock_selection_summary + 两张对比图。

## 复现结果(2024-01-01 ~ 2026-07-01,N20/K5/L5)
| | 年化收益 | 年化波动 | 最大回撤 | 夏普 | 卡玛 |
|---|---|---|---|---|---|
| 龙头动量轮动策略 | 105.9% | 42.0% | 19.0% | 2.52 | 5.58 |
| 等权重组合 | 79.3% | 28.3% | 16.0% | 2.80 | 4.97 |
| 科创50ETF | 48.9% | 38.0% | 22.9% | 1.29 | 2.14 |

对上原文「年化超110%」量级(差异来自数据源与回测终点)。策略同时跑赢基准与等权对照,轮动逻辑有超额。
