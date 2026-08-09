# CLAUDE.md

## Project Overview

A 股量化研究仓库:一个共享数据层(tushare → 本地 DuckDB)之上,**多个相互独立的策略,各自一个文件夹**。每个策略的逻辑/参数/实验记录都写在它
**自己文件夹内**(文件头 docstring + 该文件夹的 md),不在本文件展开。

## 共享数据层(根目录)

- `cache_tushare.py` — tushare → 本地 DuckDB 全量/增量缓存(
	daily/weekly/monthly/adj/basic/moneyflow/板块资金流/财务/连板/竞价/筹码/沪深300·中证1000·中证2000成分…)
- `test_cache_tushare.py` — 缓存数据正确性测试(纯本地,不连网/库)
- `db_loader.py` — DuckDB 读取辅助
- `stock_data_tushare.duckdb` — 本地数据库(~7GB,勿并发写)

## 策略文件夹(各自独立)

- `swing/` — **主升浪 ML 信号**(`run_ml_signals_2026.py`,LightGBM walk-forward + N字/W型突破)+ 缠论(czsc)出场/卖点提示 +
	LLM 分析(TradingAgents-CN/DeepSeek,经 `ta_bridge`/`ta_analyze`)+ 前后端 webapp。实验记录见
	`swing/FEATURE_EXPERIMENTS.md`。
- `first10/` — **连板晋级**:2板→3板 / 2进4(`2lb_*`,XGBoost)+ 每日流水线 + qlib 桥接。
- `1to2/` — **首板→2板** 晋级概率(XGBoost)。
- `regress/` — **首板 / 强势股「买入持有4天超额收益」回归**(XGBoost,walk-forward)。
- `quant_select/` — **通用截面多因子选股**(月频,多空 + 纯多头)。
- `xiaoxifu/` — **风险调整动量轮动**(复现小西西弗)3 策略:龙头股 / 全天候ETF / 行业ETF(`engine.py` 共用引擎)+ **牛熊切换组合**(沪深300 MA30&MA60 门控 龙头↔全天候,`regime_combo.py`,已验证有超额);前后端「小西西弗」页。
- `boll_narrow_exit/` — **BOLL缩口扩张+MACD金叉 择时研究**(`boll_expand_macd.py` 信号+多维口径 / `backtest.py` 真实净值 / `ml_rank.py` ML排序器(已否决)/ `robustness.py` 稳健性)。结论:小盘=陷阱、大盘可用;最优=ML池+第二次+大盘健康+MA60上行+RS跑赢+15日(夏普~1.8,下行保护型);前端「BOLL突破信号」页。
- `concept_rotation/` — **概念轮动:扩散指标 + RRG 四象限**(复现「做量化的西蒙」)。`diffusion.py` 扩散指标(成分股站上MA20的自由流通市值占比+MA20平滑,出扩散榜/扩散上升榜)+ `rrg.py` RRG相对强弱四象限(相对中证1000)+ 组合出「扩散高+领先/改善区」主线候选,含 `to_payload()` 前端结构。数据:ths_member(同花顺概念成分,静态,有前视偏差)+ ths_daily(板块指数,历史时点成分,无偏差)。前端「概念轮动」页(RRG散点+悬停4周流动轨迹)。历史落库:`python concept_rotation/rrg.py --persist`→ 写 DuckDB 表 `concept_signals`(每日全概念快照+main主线标记,幂等增量,已回填2024-12起)。
- `alpha144/` — **流动性冲击择时**(复现「@B:A 用人话讲因子·第七期」Alpha#144=Amihud 非流动性+突破5日新高)。`alpha144.py`(因子+事件驱动定槽回测,--universe/--liq-floor)。**结论:视频宣称中证500年化49%/回撤16%复现不出**——因子在中证500很弱(Q4-Q0年化差~3%),溢价是小盘效应(中证2000年化差15%);且**要求日成交额≥5000万即由+14%转-3%,是微盘流动性幻觉,不可实盘**。详见 alpha144/README.md。
- `etf_trend/` — **ETF 趋势跟踪(American 式离散进出)**,复现 Sepp & Lucic (2026)。`trend_signal.py` 出当日买卖点 + 历史交易 + 组合净值(American
	250/20:快线>慢线+1×ATR 买入、5×ATR 跟踪止损、跌破止损**且**信号熄灭才卖)。标的 510300/588000/159915/510500/513100(纳指,QDII 有折溢价与限购)。
	**为什么用离散而非连续调仓**:散户佣金每笔最低 5 元,European 连续调仓单笔仅数千元、折合 8–13bp,10.5 年吃掉本金 16%;American 每年约 1 笔/只、每笔近满仓,同期成本仅数百元。
	回测(25万,2016-01 起):年化 8.97%/夏普 0.86/回撤 -14.3%,等权买入持有 9.36%/0.61/-28.5%——加入纳指后买入持有的**年化反超**,趋势跟踪的价值只剩夏普与回撤。公式核对见 `papers/trend_following/NOTES.md`,
	A 股适用性见 `research/tf_acf_spectrum.py`(结论:A 股有短期自相关、**无长记忆**,故最优在慢端靠固定费用推动),参数与成本回测见 `research/tf_etf_backtest.py`。前端「ETF趋势跟踪」页。
	注意 `trend_signal.py` 不可改名为 `signal.py`——后端 sys.path.insert 会遮蔽标准库 signal。
- `qlib_workflow/` — qlib 工作流(Alpha158 + LGBM),吃自有 DuckDB 数据。
- `x2_library/` — x2strategy 止盈止损方法库;`old/` — 废弃旧策略(含最早的 BOLL 网格)。
- `research/` — 一次性形态/信号研究脚本(留档,不进前端);结论写在各脚本文件头 docstring,防重复踩坑。总纲:纯技术形态剥 beta 后系统性无 alpha,edge 在 regime+选股模型。
- `docs/` — PRD / SCHEMA / 数据字典。

## 模型工作准绳(全局)

任一策略**模型相关**改动(特征/标签/切分/超参/评估)一律按根目录 **`STANDARD_MODEL_WORKFLOW.md`** 走,确保正确性(无泄露)
、稳健性、鲁棒性;上线前过其 Phase 7 一票否决清单。工具:各模型 `--eval`(walk-forward + embargo + AUC/lift/IC/校准)、
`swing/model_robustness.py`(PBO/Deflated Sharpe)。

## Important Rules（全局保留）

- Do NOT run tests — user runs tests themselves
	- **例外**：每次改完 `cache_tushare.py`（抓取/增量/写入逻辑）后，必须跑一遍数据正确性测试
		`cd quart && python test_cache_tushare.py`，确认全绿再交付。该测试纯本地、不连网不连库（守护翻页取尽、增量完整性、幂等去重、行数计数、列对齐、schema）。

## Code Comment Rules（重要：用户不读 Python 代码细节）

写本项目所有 Python 文件时，**只允许两处注释**：

1. **文件开头 docstring**：说明该文件的用途、用法、依赖、关键产出
2. **函数 docstring**：说明该函数的用途（一行即可）

**禁止**：

- 行内注释（`# 解释这行代码做什么`）
- 代码块内的 SQL 内注释（`-- 这一步做什么`）
- 显然能从代码读出含义的注释（如 `# 加载模型` 紧跟 `model = load(...)`）

**例外**：仅当某段代码是**反直觉的非常规写法**（绕过 bug、API 怪癖、性能 hack）时，可加一行 `# WHY:` 注释。

理由：用户不读 Python 代码细节，注释噪声只会让文件头部说明被淹没。文件头 docstring + 函数 docstring 已经足够支撑用户阅读 /
修改时的理解需求。
