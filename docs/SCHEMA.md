# tushare_stock 数据库说明

A 股回测系统的 ClickHouse 主库，数据全部来自 [Tushare Pro](https://tushare.pro)，由 `cache_tushare.py` 拉取写入。

---

## 一、连接信息

| 项 | 值 |
|---|---|
| 部署 | 自建 ClickHouse 24.x（双机：阿里云 ECS + AWS EC2，团队选其一连） |
| 阿里云 IP | `47.117.166.2` |
| AWS IP | `47.129.255.54` |
| 端口 | `9000`（native protocol，给 `clickhouse-driver` / Python）；`8123` HTTP（给 curl/浏览器） |
| 数据库 | `tushare_stock` |
| 字符集 | UTF-8 |
| 时区 | Asia/Shanghai |

**Python 连接示例**：
```python
from clickhouse_driver import Client
ck = Client(host="47.117.166.2", port=9000,
            user="<你的账号>", password="<你的密码>",
            database="tushare_stock")
print(ck.execute("SHOW TABLES"))
```

**命令行**（在服务器上 / 装了 clickhouse-client 的本机）：
```bash
clickhouse-client --host 47.117.166.2 -u <账号> --password '<密码>' \
  --database tushare_stock
```

**摸结构的标准三步**：
```sql
SHOW TABLES;
SHOW CREATE TABLE daily;
DESCRIBE TABLE daily;
```

---

## 二、全局约定（看数据前必读 ⚠️）

ClickHouse 字段**默认不可空**（避免空值开销），代码里用以下哨兵值代表「没有」：

| 类型 | NULL 哨兵 | 过滤方法 |
|---|---|---|
| `String` | 空字符串 `""` | `WHERE col != ''` |
| `Float32/Float64` | `NaN` | `WHERE isFinite(col)` |
| `Date` | `1970-01-01` | `WHERE col != toDate('1970-01-01')` |

**ReplacingMergeTree 引擎注意点**：
- 写入幂等，按 `ORDER BY` 主键自动去重，但**合并是后台异步**的
- 同一主键短时间内可能同时存在多个版本 → 查询时加 `FINAL` 强制去重：
  ```sql
  SELECT * FROM daily FINAL WHERE ts_code = '000001.SZ';
  ```
- 想立即合并：`OPTIMIZE TABLE daily FINAL`（耗时操作，慎用）

**日期约定**：
- 表里 `trade_date` / `imp_date` 等 `Date` 类型 → ClickHouse 原生日期
- `list_date` / `delist_date` / `cal_date` / `pub_date` 等 **`String` 类型** → 8 位 `YYYYMMDD` 字符串（Tushare 原始格式）

**保留字字段**：
- `index_daily.change`：CH 保留字，写 SQL 必须反引号 `` `change` ``

---

## 三、表清单（共 11 张）

| 表 | 用途 | 来源 API | 行数级别 | 主键 |
|---|---|---|---|---|
| [stock_meta](#1-stock_meta-股票基础信息) | 股票基础信息 | `stock_basic` | ~5K | `ts_code` |
| [daily](#2-daily-日线行情) | 日线行情 | `daily` | ~10M+ | `ts_code, trade_date` |
| [adj_factor](#3-adj_factor-复权因子) | 复权因子 | `adj_factor` | ~10M+ | `ts_code, trade_date` |
| [daily_basic](#4-daily_basic-每日指标) | 每日指标（pe/pb/市值等） | `daily_basic` | ~10M+ | `ts_code, trade_date` |
| [index_daily](#5-index_daily-指数日线) | 指数日线 | `index_daily` | ~50K | `ts_code, trade_date` |
| [limit_step](#6-limit_step-连板天梯) | 连板天梯 | `limit_step` | ~100K | `ts_code, trade_date` |
| [cyq_perf](#7-cyq_perf-每日筹码及胜率) | 每日筹码分布 | `cyq_perf` | ~10M+ | `ts_code, trade_date` |
| [trade_cal](#8-trade_cal-交易日历) | 交易日历 | `trade_cal` | ~10K | `exchange, cal_date` |
| [stock_st](#9-stock_st-st股票日列表) | ST 股票日列表 | `stock_st` | ~30K | `ts_code, trade_date` |
| [st](#10-st-st风险变更记录) | ST 风险变更（事件） | `st` | ~5K | `ts_code, pub_date` |
| [kpl_list](#11-kpl_list-涨停榜单) | 开盘啦涨停榜 | `kpl_list` | ~500K | `ts_code, trade_date, tag` |

---

### 1. `stock_meta` 股票基础信息

来源：[Tushare stock_basic（doc_id=25）](https://tushare.pro/document/2?doc_id=25)
更新：每次 `--update` 全量重拉（数据量小）

| 字段 | 类型 | 含义 | 示例 / 备注 |
|---|---|---|---|
| `ts_code` | LowCardinality(String) | 股票代码 | `000001.SZ`、`600000.SH` |
| `name` | String | 股票名称 | `平安银行` |
| `area` | LowCardinality(String) | 所在地域 | `深圳`、`上海` |
| `industry` | LowCardinality(String) | 所属行业（Tushare 分类） | `银行`、`化工` |
| `list_date` | String | 上市日期 YYYYMMDD | `19910403` |
| `delist_date` | String | 退市日期 YYYYMMDD | 未退市为 `""` |

> 包含上市（L）和已退市（D）股票全部历史，不含暂停（P）。

---

### 2. `daily` 日线行情

来源：[Tushare daily（doc_id=27）](https://tushare.pro/document/2?doc_id=27)
更新：按交易日缺口增量
分区：按月（`toYYYYMM(trade_date)`）

| 字段 | 类型 | 含义 | 单位 / 备注 |
|---|---|---|---|
| `ts_code` | LowCardinality(String) | 股票代码 | — |
| `trade_date` | Date | 交易日 | — |
| `open` | Float32 | 开盘价 | 元，**未复权** |
| `high` | Float32 | 最高价 | 元，**未复权** |
| `low` | Float32 | 最低价 | 元，**未复权** |
| `close` | Float32 | 收盘价 | 元，**未复权** |
| `vol` | Float64 | 成交量 | 手 |
| `amount` | Float64 | 成交额 | 千元 |
| `pct_chg` | Float32 | 涨跌幅 | %，**已含除权**（Tushare 原始） |

> 计算复权价格请 JOIN `adj_factor`：复权价 = 当前价 × `adj_factor` / 最新 `adj_factor`。

---

### 3. `adj_factor` 复权因子

来源：[Tushare adj_factor（doc_id=28）](https://tushare.pro/document/2?doc_id=28)
更新：按交易日缺口增量

| 字段 | 类型 | 含义 |
|---|---|---|
| `ts_code` | LowCardinality(String) | 股票代码 |
| `trade_date` | Date | 交易日 |
| `adj_factor` | Float64 | 复权因子（前复权基准） |

---

### 4. `daily_basic` 每日指标

来源：[Tushare daily_basic（doc_id=32）](https://tushare.pro/document/2?doc_id=32)
更新：按交易日缺口增量

| 字段 | 类型 | 含义 | 单位 |
|---|---|---|---|
| `ts_code` | LowCardinality(String) | 股票代码 | — |
| `trade_date` | Date | 交易日 | — |
| `close` | Float32 | 当日收盘价 | 元 |
| `turnover_rate` | Float32 | 换手率 | % |
| `turnover_rate_f` | Float32 | 换手率（自由流通） | % |
| `volume_ratio` | Float32 | 量比 | — |
| `pe` | Float32 | 市盈率（总） | 倍 |
| `pe_ttm` | Float32 | 市盈率 TTM | 倍 |
| `pb` | Float32 | 市净率 | 倍 |
| `ps` | Float32 | 市销率 | 倍 |
| `ps_ttm` | Float32 | 市销率 TTM | 倍 |
| `dv_ratio` | Float32 | 股息率 | % |
| `dv_ttm` | Float32 | 股息率 TTM | % |
| `total_share` | Float64 | 总股本 | 万股 |
| `float_share` | Float64 | 流通股本 | 万股 |
| `free_share` | Float64 | 自由流通股本 | 万股 |
| `total_mv` | Float64 | 总市值 | **万元** |
| `circ_mv` | Float64 | 流通市值 | **万元** |

> 市值字段单位是「万元」，不是元，写 SQL 时注意。

---

### 5. `index_daily` 指数日线

来源：[Tushare index_daily（doc_id=95）](https://tushare.pro/document/2?doc_id=95)
更新：按指数代码增量
**仅同步 5 只指数**：

| 代码 | 名称 |
|---|---|
| `000001.SH` | 上证指数 |
| `399006.SZ` | 创业板指 |
| `000680.SH` | 科创综指 |
| `000852.SH` | 中证 1000（沪） |
| `399852.SZ` | 中证 1000（深） |

| 字段 | 类型 | 含义 |
|---|---|---|
| `ts_code` | LowCardinality(String) | 指数代码 |
| `trade_date` | Date | 交易日 |
| `close` | Float64 | 收盘点位 |
| `open` | Float64 | 开盘点位 |
| `high` | Float64 | 最高点位 |
| `low` | Float64 | 最低点位 |
| `pre_close` | Float64 | 前收盘点位 |
| `` `change` `` | Float64 | 涨跌点（**保留字，加反引号**） |
| `pct_chg` | Float32 | 涨跌幅 (%) |
| `vol` | Float64 | 成交量（手） |
| `amount` | Float64 | 成交额（千元） |

---

### 6. `limit_step` 连板天梯

来源：[Tushare limit_step（doc_id=356）](https://tushare.pro/document/2?doc_id=356)
更新：按交易日缺口增量

| 字段 | 类型 | 含义 |
|---|---|---|
| `ts_code` | LowCardinality(String) | 股票代码 |
| `name` | String | 股票名称（接口当日返回） |
| `trade_date` | Date | 交易日 |
| `nums` | Int8 | 当前连板数（`2`=2 板，`5`=5 板，以此类推） |

> 只有当日**连板**的股票会出现在表里（`nums >= 2`）；首板不在此表，需自行从 `daily` 计算。

---

### 7. `cyq_perf` 每日筹码及胜率

来源：[Tushare cyq_perf（doc_id=293）](https://tushare.pro/document/2?doc_id=293)
更新：**按 ts_code 增量**（接口必须传 ts_code，不能按日期拉）

| 字段 | 类型 | 含义 |
|---|---|---|
| `ts_code` | LowCardinality(String) | 股票代码 |
| `trade_date` | Date | 交易日 |
| `his_low` | Float32 | 历史最低价（建仓以来） |
| `his_high` | Float32 | 历史最高价 |
| `cost_5pct` | Float32 | 5% 分位成本 |
| `cost_15pct` | Float32 | 15% 分位成本 |
| `cost_50pct` | Float32 | 50% 分位成本（中位数） |
| `cost_85pct` | Float32 | 85% 分位成本 |
| `cost_95pct` | Float32 | 95% 分位成本 |
| `weight_avg` | Float32 | 加权平均成本 |
| `winner_rate` | Float32 | 胜率（当前价上方筹码占比，0~100） |

---

### 8. `trade_cal` 交易日历

来源：[Tushare trade_cal（doc_id=26）](https://tushare.pro/document/2?doc_id=26)
更新：每次 `--update` 全量重拉
范围：仅 `exchange='SSE'`（上交所）

| 字段 | 类型 | 含义 |
|---|---|---|
| `exchange` | LowCardinality(String) | 交易所代码（目前只存 `SSE`） |
| `cal_date` | String | 日历日期 YYYYMMDD |
| `is_open` | UInt8 | `1`=交易日 / `0`=休市 |
| `pretrade_date` | String | 上一交易日 YYYYMMDD |

---

### 9. `stock_st` ST股票日列表

来源：[Tushare stock_st（doc_id=397）](https://tushare.pro/document/2?doc_id=397)
更新：按交易日缺口增量

| 字段 | 类型 | 含义 |
|---|---|---|
| `ts_code` | LowCardinality(String) | 股票代码 |
| `name` | String | 股票名称（含 `*ST` 前缀等） |
| `trade_date` | Date | 交易日 |
| `st_type` | LowCardinality(String) | ST 类型（接口字段 `type`，CH 保留字，已重命名为 `st_type`） |
| `type_name` | String | 类型中文说明 |

---

### 10. `st` ST风险变更记录

来源：[Tushare st（doc_id=423）](https://tushare.pro/document/2?doc_id=423)
更新：每次 `--update` 全量重拉（事件型，不按日期分页）

| 字段 | 类型 | 含义 |
|---|---|---|
| `ts_code` | LowCardinality(String) | 股票代码 |
| `name` | String | 股票名称 |
| `pub_date` | String | 公告日期 YYYYMMDD |
| `imp_date` | String | 实施日期 YYYYMMDD |
| `st_type` | LowCardinality(String) | ST 类型（接口原字段 `st_tpye` 是文档拼写错误，已重命名修正） |
| `st_reason` | String | ST 原因 |
| `st_explain` | String | 风险说明 |

---

### 11. `kpl_list` 涨停榜单

来源：[Tushare kpl_list（开盘啦）](https://tushare.pro/document/2?doc_id=) — 开盘啦涨停榜
更新：按交易日缺口增量；**起始日 2020-01-01**（接口无此前数据）
分区：按月

| 字段 | 类型 | 含义 |
|---|---|---|
| `ts_code` | LowCardinality(String) | 股票代码 |
| `trade_date` | Date | 交易日 |
| `tag` | LowCardinality(String) | 榜单分类，**主键之一**：`涨停`/`竞价`/`炸板` 三选一 |
| `name` | String | 股票名称 |
| `lu_desc` | String | 涨停描述（如 "首板"、"3 连板"） |
| `theme` | String | 题材 |
| `status` | String | 状态（如 `首板`、`连板`、`断板`） |
| `lu_time` | String | 涨停时间 `HH:MM:SS` |
| `net_change` | Float64 | 主力净流入 |
| `bid_amount` | Float64 | 竞价金额 |
| `bid_change` | Float64 | 竞价涨幅相关 |
| `bid_turnover` | Float64 | 竞价换手 |
| `lu_bid_vol` | Float64 | 涨停竞价封单量 |
| `pct_chg` | Float32 | 涨跌幅 (%) |
| `bid_pct_chg` | Float32 | 竞价涨跌幅 (%) |
| `rt_pct_chg` | Float32 | 实时涨跌幅 (%) |
| `limit_order` | Float64 | 涨停封单金额 |
| `amount` | Float64 | 成交额 |
| `turnover_rate` | Float32 | 换手率 (%) |
| `free_float` | Float64 | 自由流通市值 |
| `lu_limit_order` | Float64 | 涨停时封单金额 |

> 一只股票一天可能在多个 tag 下出现（早盘竞价涨停 + 收盘也涨停），所以 `tag` 是主键的一部分。

**典型查询：当日涨停股**
```sql
SELECT ts_code, name, lu_desc, status
FROM kpl_list FINAL
WHERE trade_date = '2024-05-15' AND tag = '涨停'
ORDER BY ts_code;
```

---

## 四、常用查询示例

**1. 某只股票的复权收盘价**
```sql
SELECT d.trade_date,
       d.close * a.adj_factor / latest.adj_factor AS qfq_close
FROM daily d
INNER JOIN adj_factor a USING (ts_code, trade_date)
INNER JOIN (
    SELECT argMax(adj_factor, trade_date) AS adj_factor
    FROM adj_factor WHERE ts_code = '000001.SZ'
) latest
WHERE d.ts_code = '000001.SZ'
ORDER BY d.trade_date;
```

**2. 全市场最新流通市值排行**
```sql
SELECT ts_code,
       argMax(circ_mv, trade_date) / 10000 AS circ_mv_yi  -- 转「亿元」
FROM daily_basic
GROUP BY ts_code
HAVING isFinite(circ_mv_yi)
ORDER BY circ_mv_yi DESC
LIMIT 20;
```

**3. 某天连板≥3 的股票**
```sql
SELECT ts_code, name, nums
FROM limit_step FINAL
WHERE trade_date = '2024-05-15' AND nums >= 3
ORDER BY nums DESC;
```

**4. 中证 1000 近 60 日均线方向**
```sql
SELECT trade_date, close,
       avg(close) OVER (ORDER BY trade_date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) AS ma60
FROM index_daily FINAL
WHERE ts_code = '000852.SH'
ORDER BY trade_date DESC
LIMIT 100;
```

**5. 排除 ST 与北/创/科创板的「干净股票池」**
```sql
SELECT ts_code, name
FROM stock_meta
WHERE delist_date = ''
  AND name NOT LIKE '%ST%'
  AND substring(ts_code, 1, 3) NOT IN ('300', '301')   -- 创业板
  AND substring(ts_code, 1, 3) != '688'                -- 科创板
  AND substring(ts_code, 1, 1) != '8'                  -- 北交所
  AND substring(ts_code, 1, 1) != '4';                 -- 北交所
```

---

## 五、数据更新

| 命令 | 说明 |
|---|---|
| `python cache_tushare.py --full` | 首次全量拉取（DEFAULT_START=2015-01-01 → 今天） |
| `python cache_tushare.py --update` | 增量更新：扫描各表 `trade_date` 缺口自动补齐 |
| `python cache_tushare.py --update --date 20240515` | 只更新指定单日 |

每天市场收盘后（约 15:30 之后）跑一次 `--update` 即可。

---

## 六、踩过的坑

| 现象 | 原因 | 处理 |
|---|---|---|
| 远程 `EOFError`、连不上 9000 | `listen_host` 没开 | 服务器改 `config.xml`：`<listen_host>0.0.0.0</listen_host>` |
| `'NoneType'.encode` 写入失败 | String 列写了 None | CH String 不可空，写之前 None → `""` |
| `not a float`（pe_ttm/pct_chg 等） | Float 列写了 None | None → `float('nan')` |
| `Too many partitions for single INSERT block` | 单批跨太多月 | INSERT 加 `settings={'max_partitions_per_insert_block': 2000}` |
| `change` 字段建表失败 | CH 保留字 | DDL 加反引号 `` `change` ``；查询时也加 |
| `limit_step.nums` TypeMismatchError | Tushare 返回字符串 `"2"`，CH 字段是 Int8 | 写入前 `int(v)` 转换 |
| `SELECT *` 看到重复行 | ReplacingMergeTree 后台合并未触发 | 加 `FINAL`，或 `OPTIMIZE TABLE x FINAL` |

---

## 七、文档维护

新增 / 修改字段时同步更新本文件。表 DDL 修改后，把以下三处一起改：

1. `cache_tushare.py` 的 `DDL` / `COLUMNS` / `STRING_COLS` / `FLOAT_COLS` / `INT_COLS`
2. 已有 ClickHouse 表（`ALTER TABLE ... ADD/MODIFY COLUMN ...`）
3. 本文件对应章节

— 最后更新：2026-05-04
