# CLAUDE.md

## Project Overview

A 股量化研究仓库:一个共享数据层(tushare → 本地 DuckDB)之上,**多个相互独立的策略,各自一个文件夹**。每个策略的逻辑/参数/实验记录都写在它**自己文件夹内**(文件头 docstring + 该文件夹的 md),不在本文件展开。

## 共享数据层(根目录)

- `cache_tushare.py` — tushare → 本地 DuckDB 全量/增量缓存(daily/weekly/monthly/adj/basic/moneyflow/板块资金流/财务/连板/竞价/筹码…)
- `test_cache_tushare.py` — 缓存数据正确性测试(纯本地,不连网/库)
- `db_loader.py` — DuckDB 读取辅助
- `stock_data_tushare.duckdb` — 本地数据库(~7GB,勿并发写)

## 策略文件夹(各自独立)

- `swing/` — **主升浪 ML 信号**(`run_ml_signals_2026.py`,LightGBM walk-forward + N字/W型突破)+ 缠论(czsc)出场/卖点提示 + LLM 分析(TradingAgents-CN/DeepSeek,经 `ta_bridge`/`ta_analyze`)+ 前后端 webapp。实验记录见 `swing/FEATURE_EXPERIMENTS.md`。
- `first10/` — **连板晋级**:2板→3板 / 2进4(`2lb_*`,XGBoost)+ 每日流水线 + qlib 桥接。
- `1to2/` — **首板→2板** 晋级概率(XGBoost)。
- `regress/` — **首板 / 强势股「买入持有4天超额收益」回归**(XGBoost,walk-forward)。
- `quant_select/` — **通用截面多因子选股**(月频,多空 + 纯多头)。
- `qlib_workflow/` — qlib 工作流(Alpha158 + LGBM),吃自有 DuckDB 数据。
- `x2_library/` — x2strategy 止盈止损方法库;`old/` — 废弃旧策略(含最早的 BOLL 网格)。
- `docs/` — PRD / SCHEMA / 数据字典。

## 模型工作准绳(全局)

任一策略**模型相关**改动(特征/标签/切分/超参/评估)一律按根目录 **`STANDARD_MODEL_WORKFLOW.md`** 走,确保正确性(无泄露)、稳健性、鲁棒性;上线前过其 Phase 7 一票否决清单。工具:各模型 `--eval`(walk-forward + embargo + AUC/lift/IC/校准)、`swing/model_robustness.py`(PBO/Deflated Sharpe)。

## Git 工作流(全局)

- **每完成一个小功能即 `git commit` 并 `git push`(master),不必逐次询问。**
- 改 `cache_tushare.py` 后,push 前**必须**先跑 `cd quart && python test_cache_tushare.py` 全绿。
- commit message 用中文、简洁说清「做了什么 + 为什么」。
- **git 版本控制只在 `quart/` 根**:唯一 `.gitignore` 在根目录,禁止在任何策略子文件夹内新建 `.gitignore`;子文件夹的忽略规则一律写进根 `.gitignore`(如 `xiaoxifu/龙头动量轮动策略_N*/`)。

## Important Rules（全局保留）

- Do NOT run tests — user runs tests themselves
  - **例外**：每次改完 `cache_tushare.py`（抓取/增量/写入逻辑）后，必须跑一遍数据正确性测试 `cd quart && python test_cache_tushare.py`，确认全绿再交付。该测试纯本地、不连网不连库（守护翻页取尽、增量完整性、幂等去重、行数计数、列对齐、schema）。

## Code Comment Rules（重要：用户不读 Python 代码细节）

写本项目所有 Python 文件时，**只允许两处注释**：

1. **文件开头 docstring**：说明该文件的用途、用法、依赖、关键产出
2. **函数 docstring**：说明该函数的用途（一行即可）

**禁止**：
- 行内注释（`# 解释这行代码做什么`）
- 代码块内的 SQL 内注释（`-- 这一步做什么`）
- 显然能从代码读出含义的注释（如 `# 加载模型` 紧跟 `model = load(...)`）

**例外**：仅当某段代码是**反直觉的非常规写法**（绕过 bug、API 怪癖、性能 hack）时，可加一行 `# WHY:` 注释。

理由：用户不读 Python 代码细节，注释噪声只会让文件头部说明被淹没。文件头 docstring + 函数 docstring 已经足够支撑用户阅读 / 修改时的理解需求。
