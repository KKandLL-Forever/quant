# CLAUDE.md

## Project Overview

A-share grid trading backtesting system using Bollinger Bands (BOLL) dynamic boxes.

## Key Files

- `config.py` — Global configuration (capital, BOLL params, grid params, fees, etc.)
- `data_loader.py` — Load CSV data, calculate BOLL/MA/ATR indicators
- `strategy.py` — Grid trading strategy and backtest engine (`backtest_boll`)
- `test_one.py` — Single stock backtest runner
- `batch5.py` — Batch backtest across multiple stocks
- `main.py` — Main entry point
- `pattern.py` — Obsolete (old static box detection, no longer used)

## Strategy Logic

1. **Entry**: Price touches BOLL lower band → `check_entry_safe()` → build grid → full position buy (next day open)
2. **Grid**: 5 price lines between BOLL range (min lower / max upper over past 26 days), currently using geometric (equal-ratio) spacing
3. **Grid trading**: Price crosses grid lines → generate buy/sell signals (executed next day open)
4. **Stop loss**: Entry price * (1 - 20%)
5. **Take profit**: Box high * (1 + 20%)
6. **After exit**: Clear all state, wait for next BOLL lower touch

## BOLL Parameters

- Period: 26 days (aligned with user's trading software)
- Std: 2.0x
- Grid construction: Past 26 days' min(boll_lower) / max(boll_upper)

## Important Rules

- Do NOT run tests — user runs tests themselves
  - **例外**：每次改完 `cache_tushare.py`（抓取/增量/写入逻辑）后，必须跑一遍数据正确性测试 `cd quart && python test_cache_tushare.py`，确认全绿再交付。该测试纯本地、不连网不连库（守护翻页取尽、增量完整性、幂等去重、行数计数、列对齐、schema）。
- `check_entry_safe()`: MA bearish alignment + volume surge detection (do not change unless asked)

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
