# 数据字典（DuckDB 本地镜像）

**数据库**：`stock_data_tushare.duckdb`
**上游**：Tushare Pro API
**更新策略**：每日 18:00 后增量（`cyq_perf` 限制 20:00 后跑）
**审计日期**：2026-05

---

## 表清单与 PIT 安全性

| 表名 | 行数 | 主键 | 时间列 | PIT 状态 | 备注 |
|---|---|---|---|---|---|
| `daily` | 11.2M | (ts_code, trade_date) | trade_date | ✅ 安全 | **不复权** |
| `daily_basic` | 11.1M | (ts_code, trade_date) | trade_date | ✅ 安全 | 估值/换手 |
| `cyq_perf` | 9.0M | (ts_code, trade_date) | trade_date | ✅ 安全 | 筹码 |
| `moneyflow` | 10.9M | (ts_code, trade_date) | trade_date | ✅ 安全 | 资金流 |
| `index_daily` | 11K | (ts_code, trade_date) | trade_date | ✅ 安全 | 指数行情 |
| `limit_list_d` | 152K | (ts_code, trade_date) | trade_date | ✅ 安全 | 涨停盘口（含历史 name 快照） |
| `limit_step` | 12K | (ts_code, trade_date) | trade_date | ✅ 安全 | 连板梯队 |
| `limit_cpt_list` | 12K | (ts_code, trade_date) | trade_date | ✅ 安全 | 概念涨停统计 |
| `kpl_list` | 323K | (ts_code, trade_date) | trade_date | ✅ 安全 | 历史遗留，已替换为 limit_list_d |
| `stock_st` | 320K | (ts_code, trade_date) | trade_date | ✅ 安全 | ST 状态历史日线 |
| `st` | 1K | (ts_code, pub_date) | pub_date | ⚠️ 公告快照 | 不直接用，已被 stock_st 替代 |
| `adj_factor` | 11.7M | (ts_code, trade_date) | trade_date | ✅ 安全 | 复权因子（当前未使用） |
| `stock_meta` | 5.8K | ts_code | — | ⚠️ 当前快照 | name/list_date 等是当前值 |
| `ths_index` | 408 | ts_code | — | ⚠️ 当前快照 | 概念基础信息 |
| `ths_member` | 70K | (ts_code, con_code) | — | ⚠️ 当前快照 | 概念成分股，无历史 |
| `trade_cal` | 4K | (exchange, cal_date) | cal_date | ✅ 安全 | 交易日历 |

---

## PIT 审计结论

### ✅ PIT 安全（按 trade_date 自然时序）

`daily / daily_basic / cyq_perf / moneyflow / index_daily / limit_list_d / limit_step / limit_cpt_list / stock_st / trade_cal`

这些表每行都打上了 `trade_date`，回测时只要保证 `feature.trade_date <= signal_date` 就不会泄漏未来。当前所有特征 SQL 都遵守这一约束。

### ⚠️ 当前快照表（潜在 PIT 风险）

#### 1. `stock_meta` — 名称/上市日期当前快照

| 字段 | 风险 | 当前处理 |
|---|---|---|
| `name` | 公司可能改名 / 戴 ST / 摘 ST，name 跟着变 | 已用 `limit_list_d.name` 替代（每日快照，历史正确） |
| `list_date` | 几乎不变（仅退市重新上市等罕见情况） | 直接用，影响极小 |
| `industry` | 申万分类可能变更 | 当前未作为特征，无影响 |
| `delist_date` | 当前快照，但只对已退市股有意义 | 当前未使用 |

**结论**：除 `name` 已修正外，`list_date` 用于计算 `days_listed` 接受为安全。

#### 2. `ths_member` — 概念成分当前快照

| 字段 | 风险 | 当前处理 |
|---|---|---|
| (ts_code, con_code) | 历史中股票可能加入/退出概念，本表只有当前 | 概念特征已删除，影响消除 |

**结论**：暂不影响。**未来若加回概念特征，必须先获取历史成分变动数据**（Tushare `ths_index` + 历史成分接口可能需要更高积分）。

#### 3. `st` — ST 公告（快照式，已弃用）

被 `stock_st` 替代（后者每日精确）。当前 SQL 没有引用。

---

## 详细字段定义

### `daily` — A 股日线行情（**不复权**）

| 字段 | 类型 | 单位/含义 | PIT |
|---|---|---|---|
| `ts_code` | VARCHAR | 标的代码 (e.g. 600000.SH) | — |
| `trade_date` | DATE | 交易日 | 自然时序 |
| `open` | FLOAT | 开盘价（元，**不复权**） | T 日盘后 |
| `high` | FLOAT | 最高价（元，不复权） | T 日盘后 |
| `low` | FLOAT | 最低价（元，不复权） | T 日盘后 |
| `close` | FLOAT | 收盘价（元，不复权） | T 日盘后 |
| `vol` | DOUBLE | 成交量（手） | T 日盘后 |
| `amount` | DOUBLE | 成交额（千元） | T 日盘后 |
| `pct_chg` | FLOAT | 涨跌幅（%，不复权口径） | T 日盘后 |

**重要**：因不复权，绝对价格跨除权日不可比。当前所有特征均为**比例类**（pct_chg、vol/ma5、close/prev_close 等），**复权差异已自然抵消**。如未来加入「价格突破均线」类特征，须改用前复权或加 adj_factor 调整。

### `daily_basic` — 每日估值/流通指标

| 字段 | 类型 | 单位/含义 |
|---|---|---|
| `turnover_rate` | FLOAT | 换手率 (%) |
| `turnover_rate_f` | FLOAT | 自由流通股换手率 (%) |
| `volume_ratio` | FLOAT | 量比（vs 5 日均量） |
| `pe` / `pe_ttm` | FLOAT | 市盈率（静态/TTM） |
| `pb` | FLOAT | 市净率 |
| `ps` / `ps_ttm` | FLOAT | 市销率 |
| `dv_ratio` / `dv_ttm` | FLOAT | 股息率 (%) |
| `total_share` / `float_share` / `free_share` | DOUBLE | 总/流通/自由流通股本（万股） |
| `total_mv` / `circ_mv` | DOUBLE | 总/流通市值（万元） |

### `cyq_perf` — 筹码分布

| 字段 | 类型 | 含义 |
|---|---|---|
| `his_low` / `his_high` | FLOAT | 历史最低/最高成交价 |
| `cost_5pct` ~ `cost_95pct` | FLOAT | 成本分位（5/15/50/85/95%） |
| `weight_avg` | FLOAT | 加权平均成本 |
| `winner_rate` | FLOAT | 获利盘比例 (%, **0-100 范围**) |

**坑**：`winner_rate` 单位是百分比 0-100，不是 0-1。

### `limit_list_d` — 每日涨停盘口

| 字段 | 类型 | 含义 |
|---|---|---|
| `limit_type` | VARCHAR | U=涨停, D=跌停, Z=炸板 |
| `name` | VARCHAR | **当日**名称快照（PIT 安全） |
| `close` | FLOAT | 收盘价 |
| `pct_chg` | FLOAT | 涨跌幅 (%) |
| `amount` | DOUBLE | 成交额 |
| `limit_amount` | DOUBLE | 涨停板成交额 |
| `float_mv` / `total_mv` | DOUBLE | 流通/总市值 |
| `turnover_ratio` | FLOAT | 换手率 |
| `fd_amount` | DOUBLE | 封单金额 |
| `first_time` | VARCHAR | **首次封板时间**，HHMMSS（注意可能丢前导零） |
| `last_time` | VARCHAR | 最后封板时间 |
| `open_times` | INTEGER | 炸板次数 |
| `up_stat` | VARCHAR | "N/T" 表示 T 天内 N 次涨停 |
| `limit_times` | INTEGER | 当前连续涨停天数（首板=1） |

**坑**：
1. `first_time` 是 6 位字符串 HHMMSS，10 点前会丢前导零变 5 位，需 `lpad(x, 6, '0')`
2. `up_stat` 需 split_part 解析

### `moneyflow` — 资金流（按单子大小）

| 字段 | 类型 | 含义 |
|---|---|---|
| `buy_sm_*` | BIGINT/DOUBLE | 小单买入量/额 |
| `buy_md_*` | BIGINT/DOUBLE | 中单买入量/额 |
| `buy_lg_*` | BIGINT/DOUBLE | 大单买入量/额 |
| `buy_elg_*` | BIGINT/DOUBLE | 特大单买入量/额 |
| `net_mf_vol` / `net_mf_amount` | BIGINT/DOUBLE | 净主力（大+特大）流入 |

### `stock_st` — 每日 ST 状态

每个 ST/退市状态的股票每日一行。**用 `ts_code + trade_date` 精确判断 T 日是否 ST**。

| 字段 | 类型 | 含义 |
|---|---|---|
| `st_type` | VARCHAR | ST 类型代码 |
| `type_name` | VARCHAR | 类型描述 |

### `index_daily` — 指数行情

当前主要使用：
- `000852.SH`（中证 1000）— `market_trend` / `market_ma_dir` 大盘环境

### `limit_step` / `limit_cpt_list` / `kpl_list` / `ths_index` / `ths_member`

详见原 `cache_tushare.py`。当前模型未使用 ths_member（概念特征已删），limit_step / limit_cpt_list 为 M4 候选。

---

## 数据起始日期（TABLE_START）

`cache_tushare.py` 中已配置：

| 表 | 起始日期 | 原因 |
|---|---|---|
| 默认 | 2015-01-01 | 历史回溯 |
| `kpl_list` | 2018-01-01 | 接口数据起始 |
| `limit_list_d` | 2020-01-01 | 接口数据起始 |
| `stock_st` | 2016-08-01 | 接口数据起始 |
| `limit_step` | 2023-11-01 | 接口数据起始 |
| `limit_cpt_list` | 2023-11-01 | 接口数据起始 |

**当前模型样本起点 2020-01-02**（被 `limit_list_d` 起始日限制）。

---

## 派生表 / 物化表

### `market_state` — 市场状态指标（M4 新增）

由 `compute_market_state_v2.py` 物化，覆盖 2020-01+。每个交易日一行。

| 字段 | 含义 |
|---|---|
| `trade_date` | 交易日 |
| `n_1lb` / `n_2lb` / `n_3lb` | 当日 1 板 / 2 板 / 3 板股票数（主板，排除 ST） |
| `market_max_lianban` | 当日最高连板数 |
| `market_2lb_rate` | T 日 2 板数 / T-1 日 1 板数（"2 板晋级率"） |
| `market_2lb_rate_ma5` | 上述 5 日均（连板生态健康度核心指标） |
| `market_idx_dist_h60` | 中证 1000 收盘 / 60 日高点 - 1 |
| `market_idx_breakout` | 1 if dist_h60 > -2% else 0 |

**用法**：
- `ml_features_v2.py` LEFT JOIN 此表获取 3 个市场状态特征
- `ml_score_v2.py` 通过此表生成「市场状态横幅」+ 仓位建议调整
- 用户工作流：`market_2lb_rate_ma5 < 0.18` 时建议空仓 / 降仓

**更新**：`compute_market_state_v2.py` 每次 `cache_tushare.py` 增量更新后重跑（重建整表，速度 < 5s）。

---

## 已知问题与待办

- [ ] `daily` 是否需要切到前复权（`adj=qfq`）：暂不动（特征均为比例类，影响有限）
- [ ] 加回概念特征前需获取历史成分股变动数据
- [x] `top10_floatholders` 接口已评估为不加（季度更新滞后大）
- [x] `anns_d`（重大公告）需更高 Tushare 积分，已放弃
- [x] news 接口无权限，已放弃
- [ ] 龙虎榜（`top_list` / `top_inst`）尚未加入特征 —— M4 后续候选

---

## 模型与脚本依赖关系

```
cache_tushare.py
    ↓ 维护
DuckDB (daily / limit_list_d / cyq_perf / moneyflow / index_daily / stock_st / ...)
    ↓
compute_market_state_v2.py → market_state 表
    ↓
ml_features_v2.py (build_feature_matrix)
    ↓
ml_train_v2.py → model/xgb_lianban_v2.pkl
    ↓
ml_score_v2.py（每日推荐 HTML）
    ↓
（用户人工筛选）
```

回测脚本（独立链路，复用 model 和 features）：
- `backtest_top2_v2.py`：基线 Top 2 机械回测
- `backtest_scenarios_v2.py`：A/B/C/E 多场景对照
- `backtest_gap_filter_v2.py`：T+1 高开桶分析
- `backtest_t2open_exit_v2.py`：T+2 高开则卖（带幸存者偏差）
- `backtest_full_strategy_v2.py`：完整策略 + 市场状态过滤

诊断脚本：
- `diag_missing_outliers_v2.py` / `diag_corr_matrix_v2.py` / `diag_monotonicity_v2.py`（M2）
- `diag_stability_v2.py` / `diag_signal_threshold_v2.py` / `diag_walkforward_v2.py` / `diag_multidim_buckets_v2.py` / `diag_optuna_v2.py`（M3）

---

## 更新流程

```bash
python first10/cache_tushare.py            # Tushare 增量更新（每日盘后）
python first10/compute_market_state_v2.py  # 重建市场状态表
python first10/ml_score_v2.py              # 当日推荐 HTML
```

历史回补：

```bash
python first10/cache_tushare.py --date 20260508
python first10/compute_market_state_v2.py
python first10/ml_score_v2.py --date 20260508
```

更新顺序与依赖详见 `cache_tushare.py` 中的 `run_full()` / `update()`。
