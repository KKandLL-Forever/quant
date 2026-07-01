# xiaoxifu — 小西西弗动量轮动策略(复现)

复现小西西弗(MatrixSpk)公众号三个「风险调整动量 + 定期轮动」策略。原文整理见本目录三个 md。

## 通用逻辑(`engine.py`)
- 风险调整动量 `Adj_Momentum = N日日收益均值 / sqrt(N日日收益方差)`(默认 N=20)。
- 每 K 天调仓(K=1 即每日):原始动量为正 → 按 Adj_Momentum 降序取前 L → 按 Adj_Momentum 归一化配权。
- 权重滞后 1 天(T 决策 T+1 执行,规避未来函数)。空仓机制:无正动量标的则持币。
- 指标:年化收益(几何)/ 年化波动 / 最大回撤 / 夏普 / 卡玛。
- 价格一律前复权(复权方式不影响收益率)。个股走本地 DuckDB;ETF 走 tushare `fund_daily+fund_adj`(在线)。

## 三个策略
| 模块 | 标的池 | 调仓 | 基准 | 原文年化 | 本地复现 |
|---|---|---|---|---|---|
| `leader_momentum.py` 龙头动量 | 22 只赛道龙头股 | 每 K=5 天取前 L=5 | 科创50ETF | >110% | ~106% |
| `allweather.py` 全天候 | 纳指/沪深300/黄金 3 ETF | 每日,正动量全取 | 沪深300ETF | 47.4% | ~41% |
| `industry.py` 行业动量 | 13 只行业 ETF | 每 K=5 天取前 L=5 | 科创50ETF | 35.8% | ~21% |

差异来自数据源(原文 Yahoo、本地 tushare)、回测终点(本地到最新)与部分 ETF 上市较晚导致早期缺数。

## 用法
```
python xiaoxifu/leader_momentum.py --N 20 --K 5 --L 5 --start 2024-01-01
python xiaoxifu/allweather.py --N 20
python xiaoxifu/industry.py --N 20 --K 5 --L 5
```

## 前端
webapp 菜单「小西西弗」(`#/xiaoxifu`),Tab 切换三个策略;每个 = 绩效卡 + 累计收益曲线 + 调仓动作表。
后端 `POST /api/xiaoxifu`(字段 `strategy`: leader|allweather|industry)复用本目录各模块 `to_payload`。
