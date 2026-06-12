"""
cache_tushare.py — Tushare A股日线数据缓存到 ClickHouse

═══════════════════════════════════════════════════════════════════════════════
用法
═══════════════════════════════════════════════════════════════════════════════

  python cache_tushare.py --full                   # 首次全量拉取（DEFAULT_START → 今天）
  python cache_tushare.py --full --start 20200101  # 全量拉取指定起始日
  python cache_tushare.py --update                 # 增量更新（自动扫描并补齐缺失日期）
  python cache_tushare.py --update --date 20240515 # 只更新指定单日（所有表）
  python cache_tushare.py --update --workers 8     # 自定义并发数
  python cache_tushare.py --update --no-cloud      # 跳过云端 ClickHouse，只更新本地 DuckDB
                                                   #   （网络连云不稳时用；缺失日期改从本地 DuckDB 推算）
                                                   #   需在 .pyenv.local 配置 LOCAL_DUCKDB_PATH
  python cache_tushare.py --update --cyq-rate 150  # 调低 cyq_perf 每分钟限速（频率报错多时用）

  数据更新成功后会自动重建本地 DuckDB 的 market_state 表（连板生态 + 中证1000 指数位置，
  SQL 见 _MARKET_STATE_SQL / rebuild_market_state）。
  仅在配置了 LOCAL_DUCKDB_PATH 时执行；中断（Ctrl+C）则跳过重建。

  Ctrl+C 中断：
    第 1 次 — 优雅停止：发送停止信号，已写入数据保留，未启动任务取消
    第 2 次 — 强制退出（os._exit 130）

═══════════════════════════════════════════════════════════════════════════════
环境变量 / Token
═══════════════════════════════════════════════════════════════════════════════

  TUSHARE_TOKEN=your_token        # 优先读环境变量
  或在项目目录创建 .pyenv.local 文件，写入：TUSHARE_TOKEN=your_token
  （格式：每行 KEY=VALUE，# 开头为注释）

  CH_HOST / CH_PORT / CH_USER / CH_PASSWORD / CH_DATABASE  ClickHouse 连接（可选）

═══════════════════════════════════════════════════════════════════════════════
关键参数（顶部常量可直接修改）
═══════════════════════════════════════════════════════════════════════════════

  CH_HOST/PORT/USER/PASSWORD/DATABASE   ClickHouse 连接配置
  DEFAULT_START         "20150101"   全量/缺口扫描的最早起始日
  MAX_REQUESTS_PER_MIN  480          全局滑动窗口限速（8000积分账户留余量）
  CYQ_MAX_PER_MIN       200          cyq_perf 专用限速（其每分钟配额低于 480，单独压速避免频率报错）
  WORKERS               8            ThreadPoolExecutor 并发数
  COMMIT_EVERY          100          每 N 个完成任务批量写入一次

═══════════════════════════════════════════════════════════════════════════════
数据库表（init_db 自动建库 + 建表，共 11 张）
═══════════════════════════════════════════════════════════════════════════════

  ReplacingMergeTree 引擎，按主键自动去重，有 trade_date 的表按月分区。

  stock_meta   股票基础信息（ts_code, name, area, industry, list_date, delist_date）
  cb_basic     可转债基础信息（含正股 stk_code、上市/退市日，判断个股有无转债，全量重拉）
  daily        日线行情      （open/high/low/close/vol/amount/pct_chg）
  adj_factor   前复权因子    （adj_factor）
  daily_basic  每日指标（18 字段，含 pe/pb/circ_mv/total_mv 等）
  index_daily  指数日线（5 只：000001.SH/399006.SZ/000680.SH/000852.SH/399852.SZ）
  limit_step   连板天梯      （ts_code, name, trade_date, nums）
  cyq_perf     每日筹码及胜率（10 字段，按股票代码逐只拉取）
  trade_cal    交易日历      （SSE，全量，每次 update 重拉）
  stock_st     ST股票日列表  （主键 ts_code+trade_date）
  st           ST风险变更记录（事件型，全量，每次 update 重拉）
  kpl_list     涨停榜单（21 字段，主键 ts_code+trade_date+tag）
                 tag ∈ {涨停, 竞价, 炸板}，每天三 tag 合并入库
  limit_list_d Tushare 涨跌停/炸板（18 字段，主键 ts_code+trade_date+limit_type）
                 limit_type ∈ {U,D,Z}，2020 起每日全市场，比 kpl_list 覆盖更完整
  ths_index    同花顺概念指数列表（type=N 概念，~270 个，主键 ts_code）
                 ts_code 形如 885728.TI，与 limit_cpt_list 对齐
  ths_member   同花顺概念成分（主键 ts_code+con_code，静态映射）
                 stock → concept 关系；首次拉完，--update 自动跳过

═══════════════════════════════════════════════════════════════════════════════
增量更新算法（断点续传）
═══════════════════════════════════════════════════════════════════════════════

  不再用 MAX(trade_date)+1，而是：
    1. 拉完整交易日历（DEFAULT_START → 今天）
    2. 各表 SELECT DISTINCT trade_date，与日历做 set diff
    3. 只拉缺失的日期 → 即使中间任意一天失败，下次 --update 自动补
"""

import argparse
import math
import os
import signal
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, date

import pandas as pd
from clickhouse_driver import Client

try:
    import duckdb as _duckdb
    _DUCKDB_AVAILABLE = True
except ImportError:
    _duckdb = None
    _DUCKDB_AVAILABLE = False

# ── .pyenv.local 加载 ───────────────────────────────────────────────────────

# .pyenv.local 在父目录 quart/
PYENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".pyenv.local")


def _load_pyenv() -> dict[str, str]:
    """解析 .pyenv.local 文件，返回 {KEY: VALUE} 字典。
    格式：每行 KEY=VALUE，#开头为注释，空行忽略。"""
    if not os.path.exists(PYENV_FILE):
        return {}
    out: dict[str, str] = {}
    with open(PYENV_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


_ENV = _load_pyenv()

# ── ClickHouse 配置 ─────────────────────────────────────────────────────────
# 切换云：注释掉一组、启用另一组（成本/速度评估完成后再删除其中之一）

# —— 阿里云 ECS（当前停用）
# CH_HOST     = _ENV.get("ALI_CLICKHOUSE_IP",       "47.117.166.2")
# CH_USER     = _ENV.get("ALI_CLICKHOUSE_USERNAME", "default")
# CH_PASSWORD = _ENV.get("ALI_CLICKHOUSE_PASSWORD", "")

# —— AWS EC2（当前启用）
CH_HOST     = _ENV.get("AWS_CLICKHOUSE_IP",       "47.129.255.54")
CH_USER     = _ENV.get("AWS_CLICKHOUSE_USERNAME", "default")
CH_PASSWORD = _ENV.get("AWS_CLICKHOUSE_PASSWORD", "")

CH_PORT     = int(os.environ.get("CH_PORT", "9000"))
CH_DATABASE = os.environ.get("CH_DATABASE", "tushare_stock")

# ── DuckDB 配置（本地） ──────────────────────────────────────────────────────
# 在 .pyenv.local 中设置 LOCAL_DUCKDB_PATH=/path/to/stock_data_tushare.duckdb 即可启用。
# 不设置或留空则不写本地库，ClickHouse 照常工作。
DUCKDB_PATH = _ENV.get("LOCAL_DUCKDB_PATH", "").strip()

# ── 通用配置 ────────────────────────────────────────────────────────────────

DEFAULT_START        = "20150101"
MAX_REQUESTS_PER_MIN = 480
# cyq_perf 在 Tushare 端有独立且更低的每分钟配额，用 480 会持续触发频率报错+重试雪崩。
# 给它单独一个更低的限速（可 --cyq-rate 覆盖），主动压速避免服务端拒绝。
CYQ_MAX_PER_MIN      = 200
WORKERS              = 8
COMMIT_EVERY         = 100
INSERT_SETTINGS      = {'max_partitions_per_insert_block': 2000}

# --no-cloud 时置 False：跳过云端 ClickHouse 写入，读查询改走本地 DuckDB（见 _DuckReadAdapter）
CLOUD_ENABLED = True

# 各接口的真实数据起始日（由实际 MIN(trade_date) 反推，避免每次 --update 都重试拉空的早期日期）
TABLE_START = {
    "kpl_list":       "20180101",
    "limit_list_d":   "20200101",
    "stock_st":       "20160801",
    "limit_step":     "20231101",
    "limit_cpt_list": "20231101",
    "stk_auction_o":  "20180101",
    "stk_auction_c":  "20180101",
    "stk_factor_pro":     "20200101",
}
KPL_START = TABLE_START["kpl_list"]   # 兼容旧引用

# 全局停止信号 —— 主线程收到 Ctrl+C 时 set，worker 通过它中断 sleep
STOP_EVENT = threading.Event()
_SIGINT_COUNT = [0]


def _sleep_or_stop(seconds: float) -> bool:
    """可中断的 sleep。返回 True 表示被中断（应当尽快退出）。"""
    return STOP_EVENT.wait(seconds)


def _install_sigint_handler() -> None:
    def handler(sig, frame):
        _SIGINT_COUNT[0] += 1
        if _SIGINT_COUNT[0] == 1:
            print("\n[Ctrl+C] 收到中断信号，正在优雅停止... 再按一次 Ctrl+C 强制退出", flush=True)
            STOP_EVENT.set()
        else:
            print("\n[Ctrl+C] 强制退出", flush=True)
            os._exit(130)
    signal.signal(signal.SIGINT, handler)

# ── Token ────────────────────────────────────────────────────────────────────

def _get_token() -> str:
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if token:
        return token
    token = _ENV.get("TUSHARE_TOKEN", "").strip()
    if token:
        return token
    raise RuntimeError(
        "未找到 Tushare Token。\n"
        "请设置环境变量 TUSHARE_TOKEN=xxx\n"
        "或在项目目录创建 .pyenv.local 文件，写入：TUSHARE_TOKEN=your_token"
    )

# ── ClickHouse 表结构 ────────────────────────────────────────────────────────

DDL = {
    "stock_meta": """
        CREATE TABLE IF NOT EXISTS stock_meta (
            ts_code     LowCardinality(String),
            name        String,
            area        LowCardinality(String),
            industry    LowCardinality(String),
            list_date   String,
            delist_date String
        ) ENGINE = ReplacingMergeTree
        ORDER BY ts_code
    """,
    "cb_basic": """
        CREATE TABLE IF NOT EXISTS cb_basic (
            ts_code         LowCardinality(String),
            bond_short_name String,
            stk_code        LowCardinality(String),
            stk_short_name  String,
            list_date       String,
            delist_date     String,
            value_date      String,
            maturity_date   String,
            issue_size      Float64,
            remain_size     Float64
        ) ENGINE = ReplacingMergeTree
        ORDER BY ts_code
    """,
    "daily": """
        CREATE TABLE IF NOT EXISTS daily (
            ts_code    LowCardinality(String),
            trade_date Date,
            open       Float32,
            high       Float32,
            low        Float32,
            close      Float32,
            vol        Float64,
            amount     Float64,
            pct_chg    Float32
        ) ENGINE = ReplacingMergeTree
        PARTITION BY toYYYYMM(trade_date)
        ORDER BY (ts_code, trade_date)
    """,
    "adj_factor": """
        CREATE TABLE IF NOT EXISTS adj_factor (
            ts_code    LowCardinality(String),
            trade_date Date,
            adj_factor Float64
        ) ENGINE = ReplacingMergeTree
        PARTITION BY toYYYYMM(trade_date)
        ORDER BY (ts_code, trade_date)
    """,
    "daily_basic": """
        CREATE TABLE IF NOT EXISTS daily_basic (
            ts_code         LowCardinality(String),
            trade_date      Date,
            close           Float32,
            turnover_rate   Float32,
            turnover_rate_f Float32,
            volume_ratio    Float32,
            pe              Float32,
            pe_ttm          Float32,
            pb              Float32,
            ps              Float32,
            ps_ttm          Float32,
            dv_ratio        Float32,
            dv_ttm          Float32,
            total_share     Float64,
            float_share     Float64,
            free_share      Float64,
            total_mv        Float64,
            circ_mv         Float64
        ) ENGINE = ReplacingMergeTree
        PARTITION BY toYYYYMM(trade_date)
        ORDER BY (ts_code, trade_date)
    """,
    "index_daily": """
        CREATE TABLE IF NOT EXISTS index_daily (
            ts_code    LowCardinality(String),
            trade_date Date,
            close      Float64,
            open       Float64,
            high       Float64,
            low        Float64,
            pre_close  Float64,
            `change`   Float64,
            pct_chg    Float32,
            vol        Float64,
            amount     Float64
        ) ENGINE = ReplacingMergeTree
        PARTITION BY toYYYYMM(trade_date)
        ORDER BY (ts_code, trade_date)
    """,
    "limit_step": """
        CREATE TABLE IF NOT EXISTS limit_step (
            ts_code    LowCardinality(String),
            name       String,
            trade_date Date,
            nums       Int8
        ) ENGINE = ReplacingMergeTree
        PARTITION BY toYYYYMM(trade_date)
        ORDER BY (ts_code, trade_date)
    """,
    "cyq_perf": """
        CREATE TABLE IF NOT EXISTS cyq_perf (
            ts_code     LowCardinality(String),
            trade_date  Date,
            his_low     Float32,
            his_high    Float32,
            cost_5pct   Float32,
            cost_15pct  Float32,
            cost_50pct  Float32,
            cost_85pct  Float32,
            cost_95pct  Float32,
            weight_avg  Float32,
            winner_rate Float32
        ) ENGINE = ReplacingMergeTree
        PARTITION BY toYYYYMM(trade_date)
        ORDER BY (ts_code, trade_date)
    """,
    "trade_cal": """
        CREATE TABLE IF NOT EXISTS trade_cal (
            exchange      LowCardinality(String),
            cal_date      String,
            is_open       UInt8,
            pretrade_date String
        ) ENGINE = ReplacingMergeTree
        ORDER BY (exchange, cal_date)
    """,
    "stock_st": """
        CREATE TABLE IF NOT EXISTS stock_st (
            ts_code    LowCardinality(String),
            name       String,
            trade_date Date,
            st_type    LowCardinality(String),
            type_name  String
        ) ENGINE = ReplacingMergeTree
        PARTITION BY toYYYYMM(trade_date)
        ORDER BY (ts_code, trade_date)
    """,
    "st": """
        CREATE TABLE IF NOT EXISTS st (
            ts_code    LowCardinality(String),
            name       String,
            pub_date   String,
            imp_date   String,
            st_type    LowCardinality(String),
            st_reason  String,
            st_explain String
        ) ENGINE = ReplacingMergeTree
        ORDER BY (ts_code, pub_date)
    """,
    "kpl_list": """
        CREATE TABLE IF NOT EXISTS kpl_list (
            ts_code        LowCardinality(String),
            trade_date     Date,
            tag            LowCardinality(String),
            name           String,
            lu_desc        String,
            theme          String,
            status         String,
            lu_time        String,
            net_change     Float64,
            bid_amount     Float64,
            bid_change     Float64,
            bid_turnover   Float64,
            lu_bid_vol     Float64,
            pct_chg        Float32,
            bid_pct_chg    Float32,
            rt_pct_chg     Float32,
            limit_order    Float64,
            amount         Float64,
            turnover_rate  Float32,
            free_float     Float64,
            lu_limit_order Float64
        ) ENGINE = ReplacingMergeTree
        PARTITION BY toYYYYMM(trade_date)
        ORDER BY (ts_code, trade_date, tag)
    """,
    "moneyflow": """
        CREATE TABLE IF NOT EXISTS moneyflow (
            ts_code         LowCardinality(String),
            trade_date      Date,
            buy_sm_vol      Int64,
            buy_sm_amount   Float64,
            sell_sm_vol     Int64,
            sell_sm_amount  Float64,
            buy_md_vol      Int64,
            buy_md_amount   Float64,
            sell_md_vol     Int64,
            sell_md_amount  Float64,
            buy_lg_vol      Int64,
            buy_lg_amount   Float64,
            sell_lg_vol     Int64,
            sell_lg_amount  Float64,
            buy_elg_vol     Int64,
            buy_elg_amount  Float64,
            sell_elg_vol    Int64,
            sell_elg_amount Float64,
            net_mf_vol      Int64,
            net_mf_amount   Float64
        ) ENGINE = ReplacingMergeTree
        PARTITION BY toYYYYMM(trade_date)
        ORDER BY (ts_code, trade_date)
    """,
    "limit_cpt_list": """
        CREATE TABLE IF NOT EXISTS limit_cpt_list (
            ts_code    LowCardinality(String),
            name       String,
            trade_date Date,
            days       Int16,
            up_stat    String,
            cons_nums  Int16,
            up_nums    Int16,
            pct_chg    Float32,
            `rank`     Int16
        ) ENGINE = ReplacingMergeTree
        PARTITION BY toYYYYMM(trade_date)
        ORDER BY (ts_code, trade_date)
    """,
    "limit_list_d": """
        CREATE TABLE IF NOT EXISTS limit_list_d (
            ts_code        LowCardinality(String),
            trade_date     Date,
            limit_type     LowCardinality(String),
            industry       LowCardinality(String),
            name           String,
            close          Float32,
            pct_chg        Float32,
            amount         Float64,
            limit_amount   Float64,
            float_mv       Float64,
            total_mv       Float64,
            turnover_ratio Float32,
            fd_amount      Float64,
            first_time     String,
            last_time      String,
            open_times     Int32,
            up_stat        String,
            limit_times    Int32
        ) ENGINE = ReplacingMergeTree
        PARTITION BY toYYYYMM(trade_date)
        ORDER BY (ts_code, trade_date, limit_type)
    """,
    "ths_index": """
        CREATE TABLE IF NOT EXISTS ths_index (
            ts_code   LowCardinality(String),
            name      String,
            `count`   Int32,
            exchange  LowCardinality(String),
            list_date String,
            type      LowCardinality(String)
        ) ENGINE = ReplacingMergeTree
        ORDER BY ts_code
    """,
    "ths_member": """
        CREATE TABLE IF NOT EXISTS ths_member (
            ts_code  LowCardinality(String),
            con_code LowCardinality(String),
            con_name String
        ) ENGINE = ReplacingMergeTree
        ORDER BY (ts_code, con_code)
    """,
    "top10_floatholders": """
        CREATE TABLE IF NOT EXISTS top10_floatholders (
            ts_code          LowCardinality(String),
            ann_date         String,
            end_date         String,
            holder_name      String,
            hold_amount      Float64,
            hold_ratio       Float32,
            hold_float_ratio Float32,
            holder_type      LowCardinality(String)
        ) ENGINE = ReplacingMergeTree
        ORDER BY (ts_code, end_date, holder_name)
    """,
    "stk_auction_o": """
        CREATE TABLE IF NOT EXISTS stk_auction_o (
            ts_code    LowCardinality(String),
            trade_date Date,
            close      Float32,
            open       Float32,
            high       Float32,
            low        Float32,
            vol        Float64,
            amount     Float64,
            vwap       Float32
        ) ENGINE = ReplacingMergeTree
        PARTITION BY toYYYYMM(trade_date)
        ORDER BY (ts_code, trade_date)
    """,
    "stk_auction_c": """
        CREATE TABLE IF NOT EXISTS stk_auction_c (
            ts_code    LowCardinality(String),
            trade_date Date,
            close      Float32,
            open       Float32,
            high       Float32,
            low        Float32,
            vol        Float64,
            amount     Float64,
            vwap       Float32
        ) ENGINE = ReplacingMergeTree
        PARTITION BY toYYYYMM(trade_date)
        ORDER BY (ts_code, trade_date)
    """,
    "stk_factor_pro": """
        CREATE TABLE IF NOT EXISTS stk_factor_pro (
            ts_code     LowCardinality(String),
            trade_date  Date,
            bias2_hfq   Float64,
            rsi_hfq_12  Float64,
            dmi_adx_hfq Float64,
            topdays     Float64
        ) ENGINE = ReplacingMergeTree
        PARTITION BY toYYYYMM(trade_date)
        ORDER BY (ts_code, trade_date)
    """,
}

COLUMNS = {
    "stock_meta":  ["ts_code","name","area","industry","list_date","delist_date"],
    "cb_basic":    ["ts_code","bond_short_name","stk_code","stk_short_name",
                    "list_date","delist_date","value_date","maturity_date",
                    "issue_size","remain_size"],
    "daily":       ["ts_code","trade_date","open","high","low","close","vol","amount","pct_chg"],
    "adj_factor":  ["ts_code","trade_date","adj_factor"],
    "daily_basic": ["ts_code","trade_date","close","turnover_rate","turnover_rate_f",
                    "volume_ratio","pe","pe_ttm","pb","ps","ps_ttm","dv_ratio","dv_ttm",
                    "total_share","float_share","free_share","total_mv","circ_mv"],
    "index_daily": ["ts_code","trade_date","close","open","high","low","pre_close","change","pct_chg","vol","amount"],
    "limit_step":  ["ts_code","name","trade_date","nums"],
    "cyq_perf":    ["ts_code","trade_date","his_low","his_high","cost_5pct","cost_15pct",
                    "cost_50pct","cost_85pct","cost_95pct","weight_avg","winner_rate"],
    "trade_cal":   ["exchange","cal_date","is_open","pretrade_date"],
    "stock_st":    ["ts_code","name","trade_date","st_type","type_name"],
    "st":          ["ts_code","name","pub_date","imp_date","st_type","st_reason","st_explain"],
    "kpl_list":    ["ts_code","trade_date","tag","name","lu_desc","theme","status","lu_time",
                    "net_change","bid_amount","bid_change","bid_turnover","lu_bid_vol",
                    "pct_chg","bid_pct_chg","rt_pct_chg","limit_order","amount",
                    "turnover_rate","free_float","lu_limit_order"],
    "moneyflow":   ["ts_code","trade_date",
                    "buy_sm_vol","buy_sm_amount","sell_sm_vol","sell_sm_amount",
                    "buy_md_vol","buy_md_amount","sell_md_vol","sell_md_amount",
                    "buy_lg_vol","buy_lg_amount","sell_lg_vol","sell_lg_amount",
                    "buy_elg_vol","buy_elg_amount","sell_elg_vol","sell_elg_amount",
                    "net_mf_vol","net_mf_amount"],
    "limit_cpt_list": ["ts_code","name","trade_date","days","up_stat",
                       "cons_nums","up_nums","pct_chg","rank"],
    "limit_list_d": ["ts_code","trade_date","limit_type","industry","name",
                     "close","pct_chg","amount","limit_amount","float_mv","total_mv",
                     "turnover_ratio","fd_amount","first_time","last_time",
                     "open_times","up_stat","limit_times"],
    "ths_index":   ["ts_code","name","count","exchange","list_date","type"],
    "ths_member":  ["ts_code","con_code","con_name"],
    "top10_floatholders": ["ts_code","ann_date","end_date","holder_name",
                           "hold_amount","hold_ratio","hold_float_ratio","holder_type"],
    "stk_auction_o": ["ts_code","trade_date","close","open","high","low","vol","amount","vwap"],
    "stk_auction_c": ["ts_code","trade_date","close","open","high","low","vol","amount","vwap"],
    "stk_factor_pro": ["ts_code","trade_date","bias2_hfq","rsi_hfq_12","dmi_adx_hfq","topdays"],
}

STRING_COLS = {
    "stock_meta":  ["name","area","industry","list_date","delist_date"],
    "cb_basic":    ["bond_short_name","stk_code","stk_short_name",
                    "list_date","delist_date","value_date","maturity_date"],
    "daily":       [],
    "adj_factor":  [],
    "daily_basic": [],
    "index_daily": [],
    "limit_step":  ["name"],
    "cyq_perf":    [],
    "trade_cal":   ["exchange","pretrade_date"],
    "stock_st":    ["name","st_type","type_name"],
    "st":          ["name","pub_date","imp_date","st_type","st_reason","st_explain"],
    "kpl_list":    ["name","lu_desc","theme","status","lu_time"],
    "moneyflow":   [],
    "limit_cpt_list": ["name","up_stat"],
    "limit_list_d": ["limit_type","industry","name","first_time","last_time","up_stat"],
    "ths_index":    ["name","exchange","list_date","type"],
    "ths_member":   ["con_name"],
    "top10_floatholders": ["ann_date","end_date","holder_name","holder_type"],
}

FLOAT_COLS = {
    "stock_meta":  [],
    "cb_basic":    ["issue_size","remain_size"],
    "daily":       ["open","high","low","close","vol","amount","pct_chg"],
    "adj_factor":  ["adj_factor"],
    "daily_basic": ["close","turnover_rate","turnover_rate_f","volume_ratio",
                    "pe","pe_ttm","pb","ps","ps_ttm","dv_ratio","dv_ttm",
                    "total_share","float_share","free_share","total_mv","circ_mv"],
    "index_daily": ["close","open","high","low","pre_close","change","pct_chg","vol","amount"],
    "limit_step":  [],
    "cyq_perf":    ["his_low","his_high","cost_5pct","cost_15pct","cost_50pct",
                    "cost_85pct","cost_95pct","weight_avg","winner_rate"],
    "trade_cal":   [],
    "stock_st":    [],
    "st":          [],
    "kpl_list":    ["net_change","bid_amount","bid_change","bid_turnover","lu_bid_vol",
                    "pct_chg","bid_pct_chg","rt_pct_chg","limit_order","amount",
                    "turnover_rate","free_float","lu_limit_order"],
    "moneyflow":   ["buy_sm_amount","sell_sm_amount",
                    "buy_md_amount","sell_md_amount",
                    "buy_lg_amount","sell_lg_amount",
                    "buy_elg_amount","sell_elg_amount",
                    "net_mf_amount"],
    "limit_cpt_list": ["pct_chg"],
    "limit_list_d": ["close","pct_chg","amount","limit_amount","float_mv","total_mv",
                     "turnover_ratio","fd_amount"],
    "ths_index":    [],
    "ths_member":   [],
    "top10_floatholders": ["hold_amount","hold_ratio","hold_float_ratio"],
    "stk_auction_o": ["close","open","high","low","vol","amount","vwap"],
    "stk_auction_c": ["close","open","high","low","vol","amount","vwap"],
    "stk_factor_pro": ["bias2_hfq","rsi_hfq_12","dmi_adx_hfq","topdays"],
}

DATE_COLS = {"trade_date"}
INT_COLS = {
    "limit_step": ["nums"],
    "moneyflow":  ["buy_sm_vol","sell_sm_vol","buy_md_vol","sell_md_vol",
                   "buy_lg_vol","sell_lg_vol","buy_elg_vol","sell_elg_vol",
                   "net_mf_vol"],
    "limit_cpt_list": ["days","cons_nums","up_nums","rank"],
    "limit_list_d": ["open_times","limit_times"],
    "ths_index":    ["count"],
    "ths_member":   [],
}

NAN = float('nan')
_EPOCH = date(1970, 1, 1)


def _to_date(s):
    """'YYYYMMDD' / 'YYYY-MM-DD' / date → date"""
    if s is None or s == "" or (isinstance(s, float) and pd.isna(s)):
        return _EPOCH
    if isinstance(s, date):
        return s
    s = str(s)
    if len(s) == 8 and s.isdigit():
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    if len(s) == 10 and s[4] == '-':
        return date(int(s[:4]), int(s[5:7]), int(s[8:10]))
    return _EPOCH


def _is_null(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))

# ── 数据库初始化 ─────────────────────────────────────────────────────────────

def make_client(database: str | None = CH_DATABASE) -> Client:
    return Client(
        host=CH_HOST, port=CH_PORT,
        user=CH_USER, password=CH_PASSWORD,
        database=database or "default",
    )


def init_db() -> Client:
    """建库 + 建表，返回连到目标 database 的 Client。"""
    boot = make_client(database="default")
    boot.execute(f"CREATE DATABASE IF NOT EXISTS {CH_DATABASE}")
    boot.disconnect()
    ck = make_client()
    for table, ddl in DDL.items():
        ck.execute(ddl)
    return ck


class _DuckReadAdapter:
    """--no-cloud 模式下替代 ClickHouse Client：读查询走本地 DuckDB，写入交给 DuckDBWriter（此处 no-op）。

    与 clickhouse_driver.Client.execute 接口对齐：
      读（仅 query 一个参数）→ 返回 DuckDB fetchall 的行列表
      写（带 params）        → no-op（实际写入由 _bulk_write 早返回后跳过，此处兜底）
    """

    def __init__(self, path: str):
        if not _DUCKDB_AVAILABLE:
            raise RuntimeError("duckdb 未安装，--no-cloud 需要本地 DuckDB。pip install duckdb")
        self._con = _duckdb.connect(path)

    def execute(self, query, params=None, **kwargs):
        if params is not None:
            return None
        return self._con.execute(query).fetchall()

    def disconnect(self) -> None:
        try:
            self._con.close()
        except Exception:
            pass


# ── DuckDB 支持 ──────────────────────────────────────────────────────────────

# DuckDB 建表语句（标准 SQL 类型，无 ReplacingMergeTree）
_DUCK_DDL = {
    "stock_meta": """
        CREATE TABLE IF NOT EXISTS stock_meta (
            ts_code     VARCHAR, name VARCHAR, area VARCHAR,
            industry    VARCHAR, list_date VARCHAR, delist_date VARCHAR,
            PRIMARY KEY (ts_code)
        )""",
    "cb_basic": """
        CREATE TABLE IF NOT EXISTS cb_basic (
            ts_code VARCHAR, bond_short_name VARCHAR,
            stk_code VARCHAR, stk_short_name VARCHAR,
            list_date VARCHAR, delist_date VARCHAR,
            value_date VARCHAR, maturity_date VARCHAR,
            issue_size DOUBLE, remain_size DOUBLE,
            PRIMARY KEY (ts_code)
        )""",
    "daily": """
        CREATE TABLE IF NOT EXISTS daily (
            ts_code VARCHAR, trade_date DATE,
            open FLOAT, high FLOAT, low FLOAT, close FLOAT,
            vol DOUBLE, amount DOUBLE, pct_chg FLOAT,
            PRIMARY KEY (ts_code, trade_date)
        )""",
    "adj_factor": """
        CREATE TABLE IF NOT EXISTS adj_factor (
            ts_code VARCHAR, trade_date DATE, adj_factor DOUBLE,
            PRIMARY KEY (ts_code, trade_date)
        )""",
    "daily_basic": """
        CREATE TABLE IF NOT EXISTS daily_basic (
            ts_code VARCHAR, trade_date DATE,
            close FLOAT, turnover_rate FLOAT, turnover_rate_f FLOAT,
            volume_ratio FLOAT, pe FLOAT, pe_ttm FLOAT, pb FLOAT,
            ps FLOAT, ps_ttm FLOAT, dv_ratio FLOAT, dv_ttm FLOAT,
            total_share DOUBLE, float_share DOUBLE, free_share DOUBLE,
            total_mv DOUBLE, circ_mv DOUBLE,
            PRIMARY KEY (ts_code, trade_date)
        )""",
    "index_daily": """
        CREATE TABLE IF NOT EXISTS index_daily (
            ts_code VARCHAR, trade_date DATE,
            close DOUBLE, open DOUBLE, high DOUBLE, low DOUBLE,
            pre_close DOUBLE, change DOUBLE, pct_chg FLOAT,
            vol DOUBLE, amount DOUBLE,
            PRIMARY KEY (ts_code, trade_date)
        )""",
    "limit_step": """
        CREATE TABLE IF NOT EXISTS limit_step (
            ts_code VARCHAR, name VARCHAR, trade_date DATE, nums TINYINT,
            PRIMARY KEY (ts_code, trade_date)
        )""",
    "cyq_perf": """
        CREATE TABLE IF NOT EXISTS cyq_perf (
            ts_code VARCHAR, trade_date DATE,
            his_low FLOAT, his_high FLOAT,
            cost_5pct FLOAT, cost_15pct FLOAT, cost_50pct FLOAT,
            cost_85pct FLOAT, cost_95pct FLOAT,
            weight_avg FLOAT, winner_rate FLOAT,
            PRIMARY KEY (ts_code, trade_date)
        )""",
    "trade_cal": """
        CREATE TABLE IF NOT EXISTS trade_cal (
            exchange VARCHAR, cal_date VARCHAR,
            is_open UTINYINT, pretrade_date VARCHAR,
            PRIMARY KEY (exchange, cal_date)
        )""",
    "stock_st": """
        CREATE TABLE IF NOT EXISTS stock_st (
            ts_code VARCHAR, name VARCHAR, trade_date DATE,
            st_type VARCHAR, type_name VARCHAR,
            PRIMARY KEY (ts_code, trade_date)
        )""",
    "st": """
        CREATE TABLE IF NOT EXISTS st (
            ts_code VARCHAR, name VARCHAR, pub_date VARCHAR,
            imp_date VARCHAR, st_type VARCHAR,
            st_reason VARCHAR, st_explain VARCHAR,
            PRIMARY KEY (ts_code, pub_date)
        )""",
    "kpl_list": """
        CREATE TABLE IF NOT EXISTS kpl_list (
            ts_code VARCHAR, trade_date DATE, tag VARCHAR,
            name VARCHAR, lu_desc VARCHAR, theme VARCHAR,
            status VARCHAR, lu_time VARCHAR,
            net_change DOUBLE, bid_amount DOUBLE, bid_change DOUBLE,
            bid_turnover DOUBLE, lu_bid_vol DOUBLE,
            pct_chg FLOAT, bid_pct_chg FLOAT, rt_pct_chg FLOAT,
            limit_order DOUBLE, amount DOUBLE,
            turnover_rate FLOAT, free_float DOUBLE, lu_limit_order DOUBLE,
            PRIMARY KEY (ts_code, trade_date, tag)
        )""",
    "moneyflow": """
        CREATE TABLE IF NOT EXISTS moneyflow (
            ts_code VARCHAR, trade_date DATE,
            buy_sm_vol BIGINT,  buy_sm_amount  DOUBLE,
            sell_sm_vol BIGINT, sell_sm_amount DOUBLE,
            buy_md_vol BIGINT,  buy_md_amount  DOUBLE,
            sell_md_vol BIGINT, sell_md_amount DOUBLE,
            buy_lg_vol BIGINT,  buy_lg_amount  DOUBLE,
            sell_lg_vol BIGINT, sell_lg_amount DOUBLE,
            buy_elg_vol BIGINT, buy_elg_amount DOUBLE,
            sell_elg_vol BIGINT,sell_elg_amount DOUBLE,
            net_mf_vol BIGINT,  net_mf_amount  DOUBLE,
            PRIMARY KEY (ts_code, trade_date)
        )""",
    "limit_cpt_list": """
        CREATE TABLE IF NOT EXISTS limit_cpt_list (
            ts_code VARCHAR, name VARCHAR, trade_date DATE,
            days SMALLINT, up_stat VARCHAR,
            cons_nums SMALLINT, up_nums SMALLINT,
            pct_chg FLOAT, "rank" SMALLINT,
            PRIMARY KEY (ts_code, trade_date)
        )""",
    "limit_list_d": """
        CREATE TABLE IF NOT EXISTS limit_list_d (
            ts_code VARCHAR, trade_date DATE, limit_type VARCHAR,
            industry VARCHAR, name VARCHAR,
            close FLOAT, pct_chg FLOAT,
            amount DOUBLE, limit_amount DOUBLE,
            float_mv DOUBLE, total_mv DOUBLE,
            turnover_ratio FLOAT, fd_amount DOUBLE,
            first_time VARCHAR, last_time VARCHAR,
            open_times INTEGER, up_stat VARCHAR, limit_times INTEGER,
            PRIMARY KEY (ts_code, trade_date, limit_type)
        )""",
    "ths_index": """
        CREATE TABLE IF NOT EXISTS ths_index (
            ts_code VARCHAR, name VARCHAR, "count" INTEGER,
            exchange VARCHAR, list_date VARCHAR, type VARCHAR,
            PRIMARY KEY (ts_code)
        )""",
    "ths_member": """
        CREATE TABLE IF NOT EXISTS ths_member (
            ts_code VARCHAR, con_code VARCHAR, con_name VARCHAR,
            PRIMARY KEY (ts_code, con_code)
        )""",
    "top10_floatholders": """
        CREATE TABLE IF NOT EXISTS top10_floatholders (
            ts_code VARCHAR, ann_date VARCHAR, end_date VARCHAR,
            holder_name VARCHAR,
            hold_amount DOUBLE, hold_ratio FLOAT, hold_float_ratio FLOAT,
            holder_type VARCHAR,
            PRIMARY KEY (ts_code, end_date, holder_name)
        )""",
    "stk_auction_o": """
        CREATE TABLE IF NOT EXISTS stk_auction_o (
            ts_code VARCHAR, trade_date DATE,
            close FLOAT, open FLOAT, high FLOAT, low FLOAT,
            vol DOUBLE, amount DOUBLE, vwap FLOAT,
            PRIMARY KEY (ts_code, trade_date)
        )""",
    "stk_auction_c": """
        CREATE TABLE IF NOT EXISTS stk_auction_c (
            ts_code VARCHAR, trade_date DATE,
            close FLOAT, open FLOAT, high FLOAT, low FLOAT,
            vol DOUBLE, amount DOUBLE, vwap FLOAT,
            PRIMARY KEY (ts_code, trade_date)
        )""",
    "stk_factor_pro": """
        CREATE TABLE IF NOT EXISTS stk_factor_pro (
            ts_code VARCHAR, trade_date DATE,
            bias2_hfq DOUBLE, rsi_hfq_12 DOUBLE, dmi_adx_hfq DOUBLE, topdays DOUBLE,
            PRIMARY KEY (ts_code, trade_date)
        )""",
}


import queue as _queue


def _prepare_duck_df(table: str, df: pd.DataFrame) -> pd.DataFrame:
    """把 DataFrame 整理成 DuckDB 可直接 INSERT 的格式（列顺序、类型处理）。"""
    cols     = COLUMNS[table]
    str_cols = STRING_COLS.get(table, [])
    flt_cols = FLOAT_COLS.get(table, [])
    int_cols = INT_COLS.get(table, [])
    df = df.copy()
    for c in cols:
        if c not in df.columns:
            df[c] = None
    df = df[cols]
    for col in DATE_COLS:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col].apply(_to_date)).dt.date
    for col in str_cols:
        df[col] = df[col].fillna("").astype(str)
    for col in int_cols:
        df[col] = df[col].fillna(0).astype(int)
    for col in flt_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


class DuckDBWriter:
    """
    非阻塞 DuckDB 写入器。
    worker 调用 put() 立刻返回，后台线程攒批后用 DataFrame 注册法批量写入
    （比 executemany 快 10-50x，且与拉取线程完全异步）。
    """

    BATCH_SIZE = 50   # 攒够 N 个 DataFrame 批量写一次

    def __init__(self, path: str):
        if not _DUCKDB_AVAILABLE:
            raise RuntimeError("duckdb 未安装，请执行: pip install duckdb")
        self._con = _duckdb.connect(path)
        for ddl in _DUCK_DDL.values():
            self._con.execute(ddl)
        self._queue: _queue.Queue = _queue.Queue()
        self._stop   = threading.Event()
        self._thread = threading.Thread(target=self._worker, name="duckdb-writer", daemon=True)
        self._written = 0

    def start(self) -> "DuckDBWriter":
        self._thread.start()
        return self

    def put(self, table: str, df: pd.DataFrame) -> None:
        """非阻塞：把 (table, df) 放入队列，立刻返回。"""
        if df is not None and not df.empty:
            self._queue.put((table, df.copy()))

    def stop(self) -> int:
        """停止后台线程（等待写完剩余数据），返回总写入行数。"""
        self._stop.set()
        self._thread.join()
        self._con.close()
        return self._written

    def _worker(self) -> None:
        pending: list[tuple[str, pd.DataFrame]] = []
        while True:
            try:
                item = self._queue.get(timeout=0.3)
                pending.append(item)
                if len(pending) >= self.BATCH_SIZE:
                    self._flush(pending)
                    pending = []
            except _queue.Empty:
                if pending:
                    self._flush(pending)
                    pending = []
                if self._stop.is_set() and self._queue.empty():
                    break

    def _flush(self, items: list[tuple[str, pd.DataFrame]]) -> None:
        """按表合并后用 DataFrame 注册法批量写入。"""
        by_table: dict[str, list[pd.DataFrame]] = {}
        for table, df in items:
            by_table.setdefault(table, []).append(df)
        for table, dfs in by_table.items():
            big = _prepare_duck_df(table, pd.concat(dfs, ignore_index=True))
            if big.empty:
                continue
            self._con.register("_w", big)
            self._con.execute(f"INSERT OR REPLACE INTO {table} SELECT * FROM _w")
            self._con.unregister("_w")
            self._written += len(big)


_DUCK_IO_ERR = getattr(_duckdb, "IOException", Exception) if _DUCKDB_AVAILABLE else Exception


def _kill_duckdb_lockers(path: str) -> int:
    """找出正占用 duckdb 文件的其它进程并强制结束，返回结束的进程数。"""
    try:
        import psutil
    except ImportError:
        print("[DuckDB] psutil 未安装，无法自动释放文件锁。pip install psutil")
        return 0
    target = os.path.normcase(os.path.abspath(path))
    me = os.getpid()
    killed = 0
    for proc in psutil.process_iter(["pid", "name"]):
        if proc.info["pid"] == me:
            continue
        try:
            for f in proc.open_files():
                if os.path.normcase(f.path) == target:
                    print(f"[DuckDB] 文件被 PID {proc.pid} ({proc.info['name']}) 占用，强制结束。")
                    proc.kill()
                    proc.wait(timeout=5)
                    killed += 1
                    break
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return killed


def init_duckdb() -> "DuckDBWriter | None":
    """初始化本地 DuckDB，返回已启动的 DuckDBWriter；未配置路径则返回 None。

    若文件被其它进程独占（日常只更新时不该发生），自动结束占用进程后重试一次。"""
    if not DUCKDB_PATH:
        return None
    if not _DUCKDB_AVAILABLE:
        print("[DuckDB] duckdb 未安装，跳过本地库。pip install duckdb")
        return None
    try:
        return DuckDBWriter(DUCKDB_PATH).start()
    except _DUCK_IO_ERR as e:
        msg = str(e)
        if not any(s in msg for s in ("being used", "另一个程序", "Conflicting lock",
                                      "Could not set lock", "无法访问")):
            raise
        print(f"[DuckDB] 打开失败（{msg.splitlines()[0]}），尝试释放文件锁…")
        n = _kill_duckdb_lockers(DUCKDB_PATH)
        if n == 0:
            print("[DuckDB] 未定位到占用进程，请手动关闭后重试。")
            raise
        time.sleep(1.0)
        return DuckDBWriter(DUCKDB_PATH).start()


_MARKET_STATE_SQL = """
CREATE OR REPLACE TABLE market_state AS
WITH
  d_marked AS (
    SELECT
      ts_code, trade_date, pct_chg,
      SUM(CASE WHEN pct_chg < 9.8 THEN 1 ELSE 0 END)
        OVER (PARTITION BY ts_code ORDER BY trade_date
              ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS grp
    FROM daily
    WHERE ts_code NOT LIKE '30%'
      AND ts_code NOT LIKE '688%'
      AND ts_code NOT LIKE '%.BJ'
      AND trade_date >= DATE '2019-01-01'
  ),
  d_height_raw AS (
    SELECT
      ts_code, trade_date, pct_chg,
      CASE WHEN pct_chg >= 9.8
        THEN SUM(CASE WHEN pct_chg >= 9.8 THEN 1 ELSE 0 END)
               OVER (PARTITION BY ts_code, grp ORDER BY trade_date
                     ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
        ELSE 0
      END AS lianban_height
    FROM d_marked
  ),
  d_height AS (
    SELECT h.ts_code, h.trade_date, h.pct_chg, h.lianban_height
    FROM d_height_raw h
    LEFT JOIN stock_st st
      ON st.ts_code = h.ts_code AND st.trade_date = h.trade_date
    WHERE st.ts_code IS NULL
  ),
  daily_agg AS (
    SELECT
      trade_date,
      MAX(lianban_height)                                   AS market_max_lianban,
      SUM(CASE WHEN lianban_height = 1 THEN 1 ELSE 0 END)   AS n_1lb,
      SUM(CASE WHEN lianban_height = 2 THEN 1 ELSE 0 END)   AS n_2lb,
      SUM(CASE WHEN lianban_height = 3 THEN 1 ELSE 0 END)   AS n_3lb
    FROM d_height
    GROUP BY trade_date
  ),
  with_rate AS (
    SELECT
      trade_date,
      market_max_lianban, n_1lb, n_2lb, n_3lb,
      n_2lb * 1.0 / NULLIF(LAG(n_1lb, 1) OVER (ORDER BY trade_date), 0) AS market_2lb_rate
    FROM daily_agg
  ),
  with_ma AS (
    SELECT
      trade_date,
      market_max_lianban, n_1lb, n_2lb, n_3lb, market_2lb_rate,
      AVG(market_2lb_rate) OVER (ORDER BY trade_date
                                  ROWS BETWEEN 4 PRECEDING AND CURRENT ROW)
        AS market_2lb_rate_ma5
    FROM with_rate
  ),
  idx AS (
    SELECT
      trade_date,
      close,
      MAX(close) OVER (ORDER BY trade_date
                        ROWS BETWEEN 60 PRECEDING AND 1 PRECEDING) AS h60
    FROM index_daily
    WHERE ts_code = '000852.SH'
  ),
  idx_breakout AS (
    SELECT
      trade_date,
      CASE WHEN h60 > 0 THEN close / h60 - 1 END AS market_idx_dist_h60,
      CASE WHEN h60 > 0 AND close / h60 - 1 > -0.02 THEN 1 ELSE 0 END AS market_idx_breakout
    FROM idx
  )
SELECT
  m.trade_date,
  m.n_1lb, m.n_2lb, m.n_3lb,
  m.market_max_lianban,
  m.market_2lb_rate,
  m.market_2lb_rate_ma5,
  ix.market_idx_dist_h60,
  ix.market_idx_breakout
FROM with_ma m
LEFT JOIN idx_breakout ix ON ix.trade_date = m.trade_date
WHERE m.trade_date >= DATE '2020-01-01'
ORDER BY m.trade_date
"""


def rebuild_market_state(duck_path: str) -> None:
    """数据更新落地后，重建本地 DuckDB 的 market_state 表（连板生态 + 中证1000 指数位置）。

    口径：全A非创业/科创/北交、剔除ST，按9.8%阈值算连板高度，统计每日 1/2/3 板家数、
    2板晋级率(及5日均)、中证1000 距60日高点；仅 DuckDB（特征侧也从 DuckDB 读）。"""
    if not _DUCKDB_AVAILABLE or not duck_path:
        print("[market_state] 未配置本地 DuckDB，跳过市场状态重建。")
        return
    print("[market_state] 重建市场状态表...", end=" ", flush=True)
    t = time.time()
    con = _duckdb.connect(duck_path)
    try:
        con.execute(_MARKET_STATE_SQL)
        n = con.execute("SELECT COUNT(*) FROM market_state").fetchone()[0]
    finally:
        con.close()
    print(f"{n} 行，{time.time()-t:.1f}s")

# ── 限速器 ───────────────────────────────────────────────────────────────────

class RateLimiter:
    """滑动窗口限速器，线程安全。每分钟最多 max_per_min 次。"""

    def __init__(self, max_per_min: int):
        self.max    = max_per_min
        self.window = 60.0
        self.calls: deque[float] = deque()
        self.lock   = threading.Lock()

    def acquire(self) -> bool:
        while True:
            if STOP_EVENT.is_set():
                return False
            with self.lock:
                now = time.monotonic()
                while self.calls and self.calls[0] < now - self.window:
                    self.calls.popleft()
                if len(self.calls) < self.max:
                    self.calls.append(now)
                    return True
                wait_s = self.window - (now - self.calls[0]) + 0.01
            if _sleep_or_stop(wait_s):
                return False


def _retry_call(fn, limiter: RateLimiter, label: str, **kwargs):
    for attempt in range(5):
        if STOP_EVENT.is_set():
            return None
        if not limiter.acquire():
            return None
        try:
            return fn(**kwargs)
        except Exception as e:
            msg = str(e)
            if "频率" in msg or "frequency" in msg.lower() or "每分钟" in msg:
                wait_s = 15 * (attempt + 1)
                print(f"    [限速] {label} 第{attempt+1}次重试，等待{wait_s}s（Ctrl+C 可中断）")
                if _sleep_or_stop(wait_s):
                    return None
                continue
            print(f"    [错误] {label}: {msg}")
            return None
    print(f"    [放弃] {label} 重试5次后仍失败")
    return None

# ── DB 工具 ──────────────────────────────────────────────────────────────────

def _df_to_records(table: str, df: pd.DataFrame) -> list[dict]:
    cols     = COLUMNS[table]
    str_cols = STRING_COLS.get(table, [])
    flt_cols = FLOAT_COLS.get(table, [])
    int_cols = INT_COLS.get(table, [])
    # 补齐缺列（保证列顺序与 DDL 一致）
    for c in cols:
        if c not in df.columns:
            df[c] = None
    df = df[cols]
    records: list[dict] = []
    for row in df.itertuples(index=False, name=None):
        d = dict(zip(cols, row))
        for dc in DATE_COLS:
            if dc in d:
                d[dc] = _to_date(d[dc])
        for sc in str_cols:
            if _is_null(d.get(sc)):
                d[sc] = ""
        for fc in flt_cols:
            if _is_null(d.get(fc)):
                d[fc] = NAN
        for ic in int_cols:
            v = d.get(ic)
            d[ic] = 0 if _is_null(v) else int(v)
        records.append(d)
    return records


_BULK_CHUNK_SIZE = 10_000
_BULK_MAX_RETRY = 3
_BULK_BACKOFF_SEC = 5


def _bulk_write(ck: Client, table: str, df: pd.DataFrame) -> int:
    """分片 + 重试写入 ClickHouse。

    远端 ClickHouse 在单批 > 几十万行时易触发 EOFError（OOM/超时断连）。
    切成 _BULK_CHUNK_SIZE 行/批，失败时强制重连并退避重试。
    """
    if df is None or df.empty:
        return 0
    if not CLOUD_ENABLED:
        return 0
    records = _df_to_records(table, df)
    cols = COLUMNS[table]
    insert_sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES"

    n_total = len(records)
    written = 0
    for start in range(0, n_total, _BULK_CHUNK_SIZE):
        chunk = records[start:start + _BULK_CHUNK_SIZE]
        for attempt in range(1, _BULK_MAX_RETRY + 1):
            try:
                ck.execute(insert_sql, chunk, settings=INSERT_SETTINGS)
                written += len(chunk)
                break
            except (EOFError, ConnectionError, OSError) as e:
                if attempt == _BULK_MAX_RETRY:
                    print(f"  [_bulk_write] {table} chunk {start}-{start+len(chunk)} "
                          f"重试 {attempt} 次仍失败: {e}", flush=True)
                    raise
                wait = _BULK_BACKOFF_SEC * attempt
                print(f"  [_bulk_write] {table} chunk {start}-{start+len(chunk)} "
                      f"第 {attempt} 次失败 ({type(e).__name__})，{wait}s 后重连重试", flush=True)
                try:
                    ck.disconnect()
                except Exception:
                    pass
                time.sleep(wait)
    return written


def _existing_dates(ck: Client, table: str) -> set[str]:
    """返回 YYYYMMDD 字符串集合，与 Tushare 日期格式一致。"""
    rows = ck.execute(f"SELECT DISTINCT trade_date FROM {table}")
    out = set()
    for (d,) in rows:
        if d and d != _EPOCH:
            out.add(d.strftime("%Y%m%d"))
    return out


def _missing_dates(ck: Client, table: str, all_dates: list[str]) -> list[str]:
    have = _existing_dates(ck, table)
    return [d for d in all_dates if d not in have]


def _trading_dates(pro, limiter: RateLimiter, start: str, end: str) -> list[str]:
    df = _retry_call(
        pro.trade_cal, limiter, f"trade_cal {start}-{end}",
        exchange="SSE", start_date=start, end_date=end, is_open="1",
    )
    if df is None or df.empty:
        return []
    return sorted(df["cal_date"].tolist())

# ── 各类 fetcher（仅返回 DataFrame，不写库） ──────────────────────────────

_API_PAGE = 6000


def _fetch_paged(method, limiter: RateLimiter, label: str, **kwargs):
    """对单次返回有硬上限（_API_PAGE 行）的接口做 offset 翻页，拼齐整批。

    # WHY: tushare 全市场查询单次硬上限 6000 行，单次调用会被静默截断（曾致 top10 每季只到
    #      ~600 只；daily 等按日全市场表已逼近 6000）。循环到返回不足整页为止；不足整页的小
    #      结果（绝大多数按日查询）只调用一次，无额外开销。
    """
    frames = []
    offset = 0
    while True:
        df = _retry_call(method, limiter, f"{label} offset={offset}",
                         offset=offset, limit=_API_PAGE, **kwargs)
        if df is None:
            break
        frames.append(df)
        if len(df) < _API_PAGE:
            break
        offset += len(df)
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True)


def fetch_daily_df(pro, limiter, trade_date):
    return _fetch_paged(
        pro.daily, limiter, f"daily {trade_date}",
        trade_date=trade_date,
        fields="ts_code,trade_date,open,high,low,close,vol,amount,pct_chg",
    )


def fetch_adj_date_df(pro, limiter, trade_date):
    return _fetch_paged(
        pro.adj_factor, limiter, f"adj_factor date={trade_date}",
        trade_date=trade_date, fields="ts_code,trade_date,adj_factor",
    )


_BASIC_FIELDS = (
    "ts_code,trade_date,close,turnover_rate,turnover_rate_f,volume_ratio,"
    "pe,pe_ttm,pb,ps,ps_ttm,dv_ratio,dv_ttm,"
    "total_share,float_share,free_share,total_mv,circ_mv"
)


def fetch_basic_date_df(pro, limiter, trade_date):
    return _fetch_paged(
        pro.daily_basic, limiter, f"daily_basic date={trade_date}",
        trade_date=trade_date, fields=_BASIC_FIELDS,
    )


def fetch_adj_code_df(pro, limiter, ts_code):
    return _retry_call(
        pro.adj_factor, limiter, f"adj_factor code={ts_code}",
        ts_code=ts_code, fields="ts_code,trade_date,adj_factor",
    )


def fetch_basic_code_df(pro, limiter, ts_code):
    return _retry_call(
        pro.daily_basic, limiter, f"daily_basic code={ts_code}",
        ts_code=ts_code, fields=_BASIC_FIELDS,
    )


def fetch_stock_st_df(pro, limiter, trade_date):
    df = _retry_call(
        pro.stock_st, limiter, f"stock_st {trade_date}",
        trade_date=trade_date,
        fields="ts_code,name,trade_date,type,type_name",
    )
    if df is not None and not df.empty:
        df = df.rename(columns={"type": "st_type"})
    return df


INDEX_CODES = [
    "000001.SH",  # 上证指数
    "399006.SZ",  # 创业板指
    "000680.SH",  # 科创综指
    "000852.SH",  # 中证1000（沪）
    "399852.SZ",  # 中证1000（深）
]

_INDEX_FIELDS = "ts_code,trade_date,close,open,high,low,pre_close,change,pct_chg,vol,amount"


def fetch_index_daily_df(pro, limiter, item):
    ts_code, start_date = item
    return _retry_call(
        pro.index_daily, limiter, f"index_daily {ts_code} from {start_date}",
        ts_code=ts_code, start_date=start_date, fields=_INDEX_FIELDS,
    )


def _index_daily_update_start(ck: Client) -> str:
    rows = ck.execute("SELECT MAX(trade_date) FROM index_daily")
    d = rows[0][0] if rows and rows[0][0] else None
    if d and d != _EPOCH:
        return d.strftime("%Y%m%d")
    return DEFAULT_START


def fetch_limit_step_df(pro, limiter, trade_date):
    return _retry_call(
        pro.limit_step, limiter, f"limit_step {trade_date}",
        trade_date=trade_date,
        fields="ts_code,name,trade_date,nums",
    )


_MONEYFLOW_FIELDS = (
    "ts_code,trade_date,"
    "buy_sm_vol,buy_sm_amount,sell_sm_vol,sell_sm_amount,"
    "buy_md_vol,buy_md_amount,sell_md_vol,sell_md_amount,"
    "buy_lg_vol,buy_lg_amount,sell_lg_vol,sell_lg_amount,"
    "buy_elg_vol,buy_elg_amount,sell_elg_vol,sell_elg_amount,"
    "net_mf_vol,net_mf_amount"
)


def fetch_moneyflow_df(pro, limiter, trade_date):
    """按 trade_date 一次性拉取当日全市场个股资金流向。"""
    return _fetch_paged(
        pro.moneyflow, limiter, f"moneyflow {trade_date}",
        trade_date=trade_date, fields=_MONEYFLOW_FIELDS,
    )


_LIMIT_LIST_D_FIELDS = (
    "trade_date,ts_code,industry,name,close,pct_chg,amount,limit_amount,"
    "float_mv,total_mv,turnover_ratio,fd_amount,first_time,last_time,"
    "open_times,up_stat,limit_times,limit"
)
_LIMIT_LIST_D_COLS = COLUMNS["limit_list_d"]


def fetch_limit_list_d_df(pro, limiter, trade_date):
    """一日 limit_list_d = U(涨停) + D(跌停) + Z(炸板) 三类合并。
    Tushare 返回字段 limit ∈ {U,D,Z}，本地重命名为 limit_type 以避开 SQL 关键字。"""
    frames = []
    for lt in ("U", "D", "Z"):
        df = _retry_call(
            pro.limit_list_d, limiter, f"limit_list_d {trade_date} type={lt}",
            trade_date=trade_date, limit_type=lt, fields=_LIMIT_LIST_D_FIELDS,
        )
        if df is not None and not df.empty:
            frames.append(df)
    if not frames:
        return None
    merged = pd.concat(frames, ignore_index=True)
    if "limit" in merged.columns:
        merged = merged.rename(columns={"limit": "limit_type"})
    for col in _LIMIT_LIST_D_COLS:
        if col not in merged.columns:
            merged[col] = None
    merged = merged[_LIMIT_LIST_D_COLS].drop_duplicates(
        subset=["ts_code", "trade_date", "limit_type"]
    )
    return merged


def fetch_ths_index_df(pro, limiter):
    """同花顺概念指数列表（一次拉完，无日期切片）。仅取概念指数 type=N。"""
    df = _retry_call(
        pro.ths_index, limiter, "ths_index N",
        type="N", exchange="A",
        fields="ts_code,name,count,exchange,list_date,type",
    )
    return df


def fetch_ths_member_df(pro, limiter, ts_code):
    """单个概念的成员股列表。"""
    return _retry_call(
        pro.ths_member, limiter, f"ths_member {ts_code}",
        ts_code=ts_code, fields="ts_code,con_code,con_name",
    )


def fetch_limit_cpt_list_df(pro, limiter, trade_date):
    """按 trade_date 一次性拉取当日涨停最强板块统计。"""
    return _retry_call(
        pro.limit_cpt_list, limiter, f"limit_cpt_list {trade_date}",
        trade_date=trade_date,
        fields="ts_code,name,trade_date,days,up_stat,cons_nums,up_nums,pct_chg,rank",
    )


_AUCTION_FIELDS = "ts_code,trade_date,close,open,high,low,vol,amount,vwap"


def fetch_stk_auction_o_df(pro, limiter, trade_date):
    """按 trade_date 一次拉全市场开盘集合竞价（9:30）。"""
    return _fetch_paged(
        pro.stk_auction_o, limiter, f"stk_auction_o {trade_date}",
        trade_date=trade_date, fields=_AUCTION_FIELDS,
    )


def fetch_stk_auction_c_df(pro, limiter, trade_date):
    """按 trade_date 一次拉全市场收盘集合竞价（15:00）。"""
    return _fetch_paged(
        pro.stk_auction_c, limiter, f"stk_auction_c {trade_date}",
        trade_date=trade_date, fields=_AUCTION_FIELDS,
    )


_STK_FACTOR_FIELDS = "ts_code,trade_date,bias2_hfq,rsi_hfq_12,dmi_adx_hfq,topdays"


def fetch_stk_factor_df(pro, limiter, trade_date):
    """按 trade_date 一次拉全市场技术面因子（仅 1进2 模型用到的 4 个，后复权）。"""
    return _fetch_paged(
        pro.stk_factor_pro, limiter, f"stk_factor_pro {trade_date}",
        trade_date=trade_date, fields=_STK_FACTOR_FIELDS,
    )


_CYQ_FIELDS = ("ts_code,trade_date,his_low,his_high,cost_5pct,cost_15pct,"
               "cost_50pct,cost_85pct,cost_95pct,weight_avg,winner_rate")


def fetch_cyq_code_df(pro, limiter, ts_code):
    """全量：拉单只股票全部历史筹码数据。"""
    return _retry_call(
        pro.cyq_perf, limiter, f"cyq_perf {ts_code}",
        ts_code=ts_code, fields=_CYQ_FIELDS,
    )


def fetch_cyq_code_range_df(pro, limiter, item):
    """增量：拉单只股票指定起始日之后的筹码数据。"""
    ts_code, start_date = item
    return _retry_call(
        pro.cyq_perf, limiter, f"cyq_perf {ts_code} from {start_date}",
        ts_code=ts_code, start_date=start_date, fields=_CYQ_FIELDS,
    )


_TOP10_FIELDS = ("ts_code,ann_date,end_date,holder_name,hold_amount,"
                 "hold_ratio,hold_float_ratio,holder_type")
TOP10_PERIOD_START = "20180101"


def fetch_top10_holders_period_df(pro, limiter, period):
    """按报告期（季度末 YYYYMMDD）翻页拉全市场十大流通股东（一季约 5 万条，必翻页）。"""
    return _fetch_paged(
        pro.top10_floatholders, limiter, f"top10_floatholders period={period}",
        period=period, fields=_TOP10_FIELDS,
    )


def _quarter_end_periods(start: str, end: str) -> list[str]:
    """枚举 [start, end] 之间所有季度末日期 YYYYMMDD。"""
    s = _to_date(start)
    e = _to_date(end)
    out = []
    for y in range(s.year, e.year + 1):
        for m, d in ((3, 31), (6, 30), (9, 30), (12, 31)):
            q = date(y, m, d)
            if s <= q <= e:
                out.append(q.strftime("%Y%m%d"))
    return out


TOP10_MIN_STOCKS = 4000


def _top10_missing_periods(ck: Client, start: str = TOP10_PERIOD_START) -> list[str]:
    """需要拉取的季度末：尚未落地、或落地个股数不足全市场（曾因未翻页被截断）。

    # WHY: 旧逻辑只看 end_date 是否存在，导致每季被截断到 ~600 只后永不重抓；
    #      改为按已落库个股数判断，低于 TOP10_MIN_STOCKS 视为不完整需重拉。
    """
    end = datetime.today().strftime("%Y%m%d")
    candidates = _quarter_end_periods(start, end)
    rows = ck.execute(
        "SELECT end_date, COUNT(DISTINCT ts_code) FROM top10_floatholders GROUP BY end_date"
    )
    have = {r[0]: r[1] for r in rows} if rows else {}
    today = _to_date(end)
    out = []
    for p in candidates:
        if have.get(p, 0) >= TOP10_MIN_STOCKS:
            continue
        q = _to_date(p)
        if (today - q).days < 35:
            continue
        out.append(p)
    return out


def _cyq_update_start(ck: Client) -> str:
    """取 cyq_perf 表最新日期，增量从该日期起拉（没有数据则从 DEFAULT_START）。"""
    rows = ck.execute("SELECT MAX(trade_date) FROM cyq_perf")
    d = rows[0][0] if rows and rows[0][0] else None
    if d and d != _EPOCH:
        return d.strftime("%Y%m%d")
    return DEFAULT_START


def fetch_and_write_trade_cal(pro, ck: Client, limiter, start: str, duck_writer=None) -> None:
    """拉取 SSE 交易日历（全量）并写入，is_open 转 UInt8。"""
    print("拉取交易日历...", end=" ", flush=True)
    end = datetime.today().strftime("%Y%m%d")
    df = _retry_call(
        pro.trade_cal, limiter, "trade_cal",
        exchange="SSE", start_date=start, end_date=end,
        fields="exchange,cal_date,is_open,pretrade_date",
    )
    if df is None or df.empty:
        print("0 条")
        return
    df["is_open"] = df["is_open"].fillna(0).astype(int)
    df["pretrade_date"] = df["pretrade_date"].fillna("").astype(str)
    n = _bulk_write(ck, "trade_cal", df)
    if duck_writer is not None:
        duck_writer.put("trade_cal", df)
    print(f"{n} 条")


def fetch_and_write_st(pro, ck: Client, limiter, duck_writer=None) -> None:
    """一次性拉取全部 ST 变更记录（事件型，不按日期分页）并写入。"""
    print("拉取 ST 变更记录...", end=" ", flush=True)
    df = _retry_call(pro.st, limiter, "st all")
    if df is None or df.empty:
        print("0 条")
        return
    df = df.rename(columns={"st_tpye": "st_type"})  # 修正 API 文档笔误
    n = _bulk_write(ck, "st", df)
    if duck_writer is not None:
        duck_writer.put("st", df)
    print(f"{n} 条")


def fetch_and_write_cb_basic(pro, ck: Client, limiter, duck_writer=None) -> None:
    """一次性拉取全市场可转债基础信息（含正股代码 stk_code 与上市/退市日），覆盖式写入。"""
    print("拉取可转债基础信息 cb_basic...", end=" ", flush=True)
    df = _retry_call(
        pro.cb_basic, limiter, "cb_basic all",
        fields=("ts_code,bond_short_name,stk_code,stk_short_name,"
                "list_date,delist_date,value_date,maturity_date,"
                "issue_size,remain_size"),
    )
    if df is None or df.empty:
        print("0 条")
        return
    n = _bulk_write(ck, "cb_basic", df)
    if duck_writer is not None:
        duck_writer.put("cb_basic", df)
    print(f"{n} 条")


KPL_FIELDS = (
    "ts_code,trade_date,tag,name,lu_desc,theme,status,lu_time,"
    "net_change,bid_amount,bid_change,bid_turnover,lu_bid_vol,"
    "pct_chg,bid_pct_chg,rt_pct_chg,limit_order,amount,"
    "turnover_rate,free_float,lu_limit_order"
)
KPL_COLS = KPL_FIELDS.split(",")


def fetch_kpl_df(pro, limiter, trade_date):
    """一日 kpl_list = 涨停 + 竞价 + 炸板 三 tag 合并。"""
    frames = []
    for tag in ("涨停", "竞价", "炸板"):
        df = _retry_call(
            pro.kpl_list, limiter, f"kpl {trade_date} tag={tag}",
            trade_date=trade_date, tag=tag, fields=KPL_FIELDS,
        )
        if df is not None and not df.empty:
            frames.append(df)
    if not frames:
        return None
    merged = pd.concat(frames, ignore_index=True)
    for col in KPL_COLS:
        if col not in merged.columns:
            merged[col] = None
    merged = merged[KPL_COLS].drop_duplicates(subset=["ts_code", "trade_date", "tag"])
    return merged

# ── 并发执行器 ───────────────────────────────────────────────────────────────

def _run_concurrent(
    pro, ck: Client, table, items, fetcher, label,
    *, limiter: RateLimiter, workers: int = WORKERS,
    commit_every: int = COMMIT_EVERY,
    duck_writer: "DuckDBWriter | None" = None,
) -> int:
    if not items:
        return 0

    pending: list[pd.DataFrame] = []
    total = 0
    done  = 0
    t_start = time.monotonic()

    def flush():
        nonlocal total, pending
        if not pending:
            return
        big = pd.concat(pending, ignore_index=True)
        _bulk_write(ck, table, big)
        if duck_writer is not None:
            duck_writer.put(table, big)   # 非阻塞，立刻返回
        total += len(big)
        pending = []

    print(f"  [{label}] 任务={len(items)}  workers={workers}  限速={MAX_REQUESTS_PER_MIN}/min")

    pool = ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix=f"ts-{label}",
    )
    futures = {pool.submit(fetcher, pro, limiter, it): it for it in items}
    interrupted = False
    try:
        remaining = set(futures.keys())
        while remaining and not STOP_EVENT.is_set():
            try:
                ready, remaining = wait(remaining, timeout=0.5, return_when=FIRST_COMPLETED)
            except KeyboardInterrupt:
                STOP_EVENT.set()
                break
            for f in ready:
                df = f.result()
                done += 1
                if df is not None and not df.empty:
                    pending.append(df)
                if done % commit_every == 0:
                    flush()
                    elapsed = time.monotonic() - t_start
                    rate = done / elapsed if elapsed > 0 else 0
                    eta  = (len(items) - done) / rate if rate > 0 else 0
                    print(f"    [{done:>5}/{len(items)}] 累计 {total} 行  {rate:.1f}任务/s  ETA {eta:.0f}s",
                          flush=True)

        if STOP_EVENT.is_set():
            interrupted = True
            print(f"\n  [{label}] 收到停止信号，取消未启动任务...", flush=True)
            for fut in remaining:
                fut.cancel()
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
        flush()

    elapsed = time.monotonic() - t_start
    if interrupted:
        print(f"  [{label}] 已中断：写入 {total} 行  耗时 {elapsed:.1f}s")
        raise KeyboardInterrupt
    print(f"  [{label}] 完成：{total} 行  耗时 {elapsed:.1f}s")
    return total

# ── 顶层任务 ─────────────────────────────────────────────────────────────────

def fetch_ths_concepts(pro, ck: Client, limiter: RateLimiter, workers: int, duck_writer=None) -> None:
    """ths_index 一次性 + ths_member 按概念循环。成员关系是静态的（同花顺接口不返时间），
    所以全量拉一次即可，--update 时若已有数据则跳过。"""
    rows = ck.execute("SELECT count() FROM ths_member")
    if rows and rows[0][0] > 0:
        print(f"ths_member 已有 {rows[0][0]} 行，跳过。如需重拉请先 TRUNCATE TABLE ths_member。")
        return

    print("拉取 ths_index 概念列表...", end=" ", flush=True)
    df_idx = fetch_ths_index_df(pro, limiter)
    if df_idx is None or df_idx.empty:
        print("0 条")
        return
    n = _bulk_write(ck, "ths_index", df_idx)
    if duck_writer is not None:
        duck_writer.put("ths_index", df_idx)
    print(f"{n} 个概念")

    codes = df_idx["ts_code"].tolist()
    print(f"\n拉取 ths_member（{len(codes)} 个概念循环）...")
    _run_concurrent(pro, ck, "ths_member", codes, fetch_ths_member_df, "ths_member",
                    limiter=limiter, workers=workers, duck_writer=duck_writer)


def fetch_stock_meta(pro, ck: Client, limiter: RateLimiter, duck_writer=None) -> None:
    print("拉取股票列表...", end=" ", flush=True)
    df_l = _retry_call(
        pro.stock_basic, limiter, "stock_basic L",
        exchange="", list_status="L",
        fields="ts_code,name,area,industry,list_date,delist_date",
    )
    df_d = _retry_call(
        pro.stock_basic, limiter, "stock_basic D",
        exchange="", list_status="D",
        fields="ts_code,name,area,industry,list_date,delist_date",
    )
    parts = [d for d in (df_l, df_d) if d is not None and not d.empty]
    if not parts:
        print("0 只")
        return
    df = pd.concat(parts, ignore_index=True).drop_duplicates("ts_code")
    n = _bulk_write(ck, "stock_meta", df)
    if duck_writer is not None:
        duck_writer.put("stock_meta", df)
    print(f"共 {n} 只股票")


def _all_codes(ck: Client) -> list[str]:
    rows = ck.execute("SELECT DISTINCT ts_code FROM stock_meta ORDER BY ts_code")
    return [r[0] for r in rows]

# ── 全量拉取 ─────────────────────────────────────────────────────────────────

def run_full(pro, ck: Client, start: str, workers: int, duck_writer=None) -> None:
    end     = datetime.today().strftime("%Y%m%d")
    limiter = RateLimiter(MAX_REQUESTS_PER_MIN)
    print(f"全量拉取：{start} → {end}  (workers={workers})")
    if duck_writer is not None:
        print(f"  同步写入本地 DuckDB: {DUCKDB_PATH}")

    fetch_stock_meta(pro, ck, limiter, duck_writer)
    fetch_and_write_trade_cal(pro, ck, limiter, start, duck_writer)

    print("获取交易日列表...", end=" ", flush=True)
    dates = _trading_dates(pro, limiter, start, end)
    print(f"{len(dates)} 个交易日\n")

    print("[1/8] 日线数据")
    _run_concurrent(pro, ck, "daily", dates, fetch_daily_df, "daily",
                    limiter=limiter, workers=workers, duck_writer=duck_writer)

    print("\n[2/8] 复权因子（按日期）")
    _run_concurrent(pro, ck, "adj_factor", dates, fetch_adj_date_df, "adj_factor",
                    limiter=limiter, workers=workers, duck_writer=duck_writer)

    print("\n[3/8] 每日指标（按日期）")
    _run_concurrent(pro, ck, "daily_basic", dates, fetch_basic_date_df, "daily_basic",
                    limiter=limiter, workers=workers, duck_writer=duck_writer)

    kpl_dates = [d for d in dates if d >= KPL_START]
    print(f"\n[4/8] kpl_list 涨停榜单（{KPL_START} 起，{len(kpl_dates)} 天）")
    _run_concurrent(pro, ck, "kpl_list", kpl_dates, fetch_kpl_df, "kpl_list",
                    limiter=limiter, workers=workers, duck_writer=duck_writer)

    print(f"\n[5/8] 指数日线（{len(INDEX_CODES)} 只）")
    idx_items = [(code, start) for code in INDEX_CODES]
    _run_concurrent(pro, ck, "index_daily", idx_items, fetch_index_daily_df, "index_daily",
                    limiter=limiter, workers=workers, duck_writer=duck_writer)

    print("\n[6/8] 连板天梯")
    _run_concurrent(pro, ck, "limit_step", dates, fetch_limit_step_df, "limit_step",
                    limiter=limiter, workers=workers, duck_writer=duck_writer)

    print(f"\n[7/8] 每日筹码及胜率（按股票逐只，限速 {CYQ_MAX_PER_MIN}/min）")
    codes = _all_codes(ck)
    _run_concurrent(pro, ck, "cyq_perf", codes, fetch_cyq_code_df, "cyq_perf",
                    limiter=RateLimiter(CYQ_MAX_PER_MIN), workers=workers, duck_writer=duck_writer)

    print("\n[8/10] stock_st ST股票日列表")
    _run_concurrent(pro, ck, "stock_st", dates, fetch_stock_st_df, "stock_st",
                    limiter=limiter, workers=workers, duck_writer=duck_writer)

    print("\n[9/10] moneyflow 个股资金流向（按日期）")
    _run_concurrent(pro, ck, "moneyflow", dates, fetch_moneyflow_df, "moneyflow",
                    limiter=limiter, workers=workers, duck_writer=duck_writer)

    print("\n[10/11] limit_cpt_list 涨停最强板块（按日期）")
    _run_concurrent(pro, ck, "limit_cpt_list", dates, fetch_limit_cpt_list_df, "limit_cpt_list",
                    limiter=limiter, workers=workers, duck_writer=duck_writer)

    lld_dates = [d for d in dates if d >= KPL_START]
    print(f"\n[11/12] limit_list_d 涨跌停/炸板（{KPL_START} 起，{len(lld_dates)} 天）")
    _run_concurrent(pro, ck, "limit_list_d", lld_dates, fetch_limit_list_d_df, "limit_list_d",
                    limiter=limiter, workers=workers, duck_writer=duck_writer)

    auc_dates = [d for d in dates if d >= TABLE_START["stk_auction_o"]]
    print(f"\n[11b] 开盘集合竞价 stk_auction_o（{TABLE_START['stk_auction_o']} 起，{len(auc_dates)} 天）")
    _run_concurrent(pro, ck, "stk_auction_o", auc_dates, fetch_stk_auction_o_df, "stk_auction_o",
                    limiter=limiter, workers=workers, duck_writer=duck_writer)
    print(f"\n[11c] 收盘集合竞价 stk_auction_c（{TABLE_START['stk_auction_c']} 起，{len(auc_dates)} 天）")
    _run_concurrent(pro, ck, "stk_auction_c", auc_dates, fetch_stk_auction_c_df, "stk_auction_c",
                    limiter=limiter, workers=workers, duck_writer=duck_writer)

    sf_dates = [d for d in dates if d >= TABLE_START["stk_factor_pro"]]
    print(f"\n[11d] stk_factor_pro 技术面因子（1进2 用，{TABLE_START['stk_factor']} 起，{len(sf_dates)} 天）")
    _run_concurrent(pro, ck, "stk_factor_pro", sf_dates, fetch_stk_factor_df, "stk_factor_pro",
                    limiter=limiter, workers=workers, duck_writer=duck_writer)

    print(f"\n[12/13] ths_index + ths_member（同花顺概念体系，静态成员关系）")
    fetch_ths_concepts(pro, ck, limiter, workers, duck_writer)

    miss_top10 = _top10_missing_periods(ck)
    print(f"\n[13/13] top10_floatholders 十大流通股东（按报告期，{len(miss_top10)} 期）")
    if miss_top10:
        _run_concurrent(pro, ck, "top10_floatholders", miss_top10,
                        fetch_top10_holders_period_df, "top10_floatholders",
                        limiter=limiter, workers=workers, duck_writer=duck_writer)

    print()
    fetch_and_write_st(pro, ck, limiter, duck_writer)
    fetch_and_write_cb_basic(pro, ck, limiter, duck_writer)

    print("\n全量拉取完成。")

# ── 增量更新 ─────────────────────────────────────────────────────────────────

def run_update(pro, ck: Client, date_arg: str | None, workers: int, duck_writer=None) -> None:
    end     = datetime.today().strftime("%Y%m%d")
    limiter = RateLimiter(MAX_REQUESTS_PER_MIN)

    if date_arg:
        miss_daily = miss_adj = miss_basic = miss_kpl = miss_stock_st = miss_limit_step = [date_arg]
        miss_moneyflow = miss_cpt = miss_lld = [date_arg]
        miss_auc_o = miss_auc_c = [date_arg]
        miss_sf    = [date_arg]
        cyq_start  = date_arg
        idx_start  = date_arg
        print(f"指定日期更新：{date_arg}")
    else:
        print("获取交易日历...", end=" ", flush=True)
        all_dates = _trading_dates(pro, limiter, DEFAULT_START, end)
        print(f"{len(all_dates)} 个交易日")

        def _floor(table):
            start = TABLE_START.get(table)
            return all_dates if not start else [d for d in all_dates if d >= start]

        miss_daily      = _missing_dates(ck, "daily",       all_dates)
        miss_adj        = _missing_dates(ck, "adj_factor",  all_dates)
        miss_basic      = _missing_dates(ck, "daily_basic", all_dates)
        miss_kpl        = _missing_dates(ck, "kpl_list",    _floor("kpl_list"))
        miss_stock_st   = _missing_dates(ck, "stock_st",    _floor("stock_st"))
        miss_limit_step = _missing_dates(ck, "limit_step",  _floor("limit_step"))
        miss_moneyflow  = _missing_dates(ck, "moneyflow",   all_dates)
        miss_cpt        = _missing_dates(ck, "limit_cpt_list", _floor("limit_cpt_list"))
        miss_lld        = _missing_dates(ck, "limit_list_d",   _floor("limit_list_d"))
        miss_auc_o      = _missing_dates(ck, "stk_auction_o",  _floor("stk_auction_o"))
        miss_auc_c      = _missing_dates(ck, "stk_auction_c",  _floor("stk_auction_c"))
        miss_sf         = _missing_dates(ck, "stk_factor_pro",     _floor("stk_factor_pro"))
        cyq_start       = _cyq_update_start(ck)
        idx_start       = _index_daily_update_start(ck)

        print(f"\n缺失日期：daily={len(miss_daily)}  adj={len(miss_adj)}  "
              f"basic={len(miss_basic)}  kpl={len(miss_kpl)}  "
              f"stock_st={len(miss_stock_st)}  limit_step={len(miss_limit_step)}  "
              f"moneyflow={len(miss_moneyflow)}  limit_cpt_list={len(miss_cpt)}  "
              f"limit_list_d={len(miss_lld)}  "
              f"auction_o={len(miss_auc_o)}  auction_c={len(miss_auc_c)}  "
              f"stk_factor={len(miss_sf)}  "
              f"cyq_perf_from={cyq_start}  index_daily_from={idx_start}\n")

    if not any([miss_daily, miss_adj, miss_basic, miss_kpl, miss_stock_st,
                miss_limit_step, miss_moneyflow, miss_cpt, miss_lld,
                miss_auc_o, miss_auc_c, miss_sf]):
        print("所有表数据已是最新，无需更新。")
        return

    if miss_daily:
        _run_concurrent(pro, ck, "daily", miss_daily, fetch_daily_df, "daily",
                        limiter=limiter, workers=workers, duck_writer=duck_writer)
    if miss_adj:
        _run_concurrent(pro, ck, "adj_factor", miss_adj, fetch_adj_date_df, "adj_factor",
                        limiter=limiter, workers=workers, duck_writer=duck_writer)
    if miss_basic:
        _run_concurrent(pro, ck, "daily_basic", miss_basic, fetch_basic_date_df, "daily_basic",
                        limiter=limiter, workers=workers, duck_writer=duck_writer)
    if miss_kpl:
        _run_concurrent(pro, ck, "kpl_list", miss_kpl, fetch_kpl_df, "kpl_list",
                        limiter=limiter, workers=workers, duck_writer=duck_writer)
    if miss_stock_st:
        _run_concurrent(pro, ck, "stock_st", miss_stock_st, fetch_stock_st_df, "stock_st",
                        limiter=limiter, workers=workers, duck_writer=duck_writer)
    if miss_limit_step:
        _run_concurrent(pro, ck, "limit_step", miss_limit_step, fetch_limit_step_df, "limit_step",
                        limiter=limiter, workers=workers, duck_writer=duck_writer)
    if miss_moneyflow:
        _run_concurrent(pro, ck, "moneyflow", miss_moneyflow, fetch_moneyflow_df, "moneyflow",
                        limiter=limiter, workers=workers, duck_writer=duck_writer)
    if miss_cpt:
        _run_concurrent(pro, ck, "limit_cpt_list", miss_cpt, fetch_limit_cpt_list_df,
                        "limit_cpt_list", limiter=limiter, workers=workers, duck_writer=duck_writer)
    if miss_lld:
        _run_concurrent(pro, ck, "limit_list_d", miss_lld, fetch_limit_list_d_df,
                        "limit_list_d", limiter=limiter, workers=workers, duck_writer=duck_writer)
    if miss_auc_o:
        _run_concurrent(pro, ck, "stk_auction_o", miss_auc_o, fetch_stk_auction_o_df,
                        "stk_auction_o", limiter=limiter, workers=workers, duck_writer=duck_writer)
    if miss_auc_c:
        _run_concurrent(pro, ck, "stk_auction_c", miss_auc_c, fetch_stk_auction_c_df,
                        "stk_auction_c", limiter=limiter, workers=workers, duck_writer=duck_writer)
    if miss_sf:
        _run_concurrent(pro, ck, "stk_factor_pro", miss_sf, fetch_stk_factor_df,
                        "stk_factor_pro", limiter=limiter, workers=workers, duck_writer=duck_writer)

    # index_daily 按指数代码拉，增量从最新日期起
    idx_items = [(code, idx_start) for code in INDEX_CODES]
    _run_concurrent(pro, ck, "index_daily", idx_items, fetch_index_daily_df, "index_daily",
                    limiter=limiter, workers=workers, duck_writer=duck_writer)

    # cyq_perf 接口官方只在 18:00-19:00 更新当日数据，且必须按股票拉（5837 次调用/全量）。
    # 为避免每次 --update 都跑这个慢步骤、且在收盘前拿到的还是昨天数据，限定 20:00 后才更新。
    # 用 --date 指定单日时不受时间限制（人工覆盖）。
    now = datetime.now()
    if date_arg or now.hour >= 20:
        codes = _all_codes(ck)
        items = [(code, cyq_start) for code in codes]
        print(f"[cyq_perf] 独立限速 {CYQ_MAX_PER_MIN}/min（避免频率报错重试）")
        _run_concurrent(pro, ck, "cyq_perf", items, fetch_cyq_code_range_df, "cyq_perf",
                        limiter=RateLimiter(CYQ_MAX_PER_MIN), workers=workers, duck_writer=duck_writer)
    else:
        print(f"\n[cyq_perf] 当前 {now:%H:%M}，接口 18-19 点才更新当日数据，"
              f"已跳过；20:00 后再 --update 即可拉到最新筹码。")

    # trade_cal / st / cb_basic 是参考型，每次更新都全量重拉（数据量小，一次请求搞定）
    fetch_and_write_trade_cal(pro, ck, limiter, DEFAULT_START, duck_writer)
    fetch_and_write_st(pro, ck, limiter, duck_writer)
    fetch_and_write_cb_basic(pro, ck, limiter, duck_writer)

    miss_top10 = _top10_missing_periods(ck)
    if miss_top10:
        print(f"\n[top10_floatholders] 缺失 {len(miss_top10)} 期，按报告期增量拉取")
        _run_concurrent(pro, ck, "top10_floatholders", miss_top10,
                        fetch_top10_holders_period_df, "top10_floatholders",
                        limiter=limiter, workers=workers, duck_writer=duck_writer)

    # ths 概念体系：成员关系静态，第一次自动建立，已有则跳过
    fetch_ths_concepts(pro, ck, limiter, workers, duck_writer)

    print("\n增量更新完成。")

# ── 入口 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    global CLOUD_ENABLED, CYQ_MAX_PER_MIN
    parser = argparse.ArgumentParser(description="Tushare A股数据缓存工具 (ClickHouse)")
    group  = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--full",   action="store_true", help="全量拉取")
    group.add_argument("--update", action="store_true", help="增量更新")
    parser.add_argument("--start",   default=DEFAULT_START, help="全量拉取起始日 YYYYMMDD")
    parser.add_argument("--date",    default=None,          help="增量更新指定单日 YYYYMMDD")
    parser.add_argument("--workers", type=int, default=WORKERS, help=f"并发数（默认 {WORKERS}）")
    parser.add_argument("--cyq-rate", type=int, default=CYQ_MAX_PER_MIN,
                        help=f"cyq_perf 独立每分钟限速（默认 {CYQ_MAX_PER_MIN}，频率报错多则调小）")
    parser.add_argument("--no-cloud", action="store_true",
                        help="跳过云端 ClickHouse，只更新本地 DuckDB（网络不稳时用）")
    args = parser.parse_args()

    _install_sigint_handler()

    import tushare as ts
    token = _get_token()
    ts.set_token(token)
    pro = ts.pro_api()

    CYQ_MAX_PER_MIN = args.cyq_rate
    if args.no_cloud:
        if not DUCKDB_PATH:
            raise RuntimeError("--no-cloud 需要本地 DuckDB：请在 .pyenv.local 设置 LOCAL_DUCKDB_PATH")
        CLOUD_ENABLED = False
        duck_writer = init_duckdb()
        if duck_writer is None:
            raise RuntimeError("--no-cloud 模式下本地 DuckDB 初始化失败。")
        print(f"[--no-cloud] 跳过云端 ClickHouse，只更新本地 DuckDB: {DUCKDB_PATH}")
        ck = _DuckReadAdapter(DUCKDB_PATH)
    else:
        print(f"连接 ClickHouse {CH_HOST}:{CH_PORT}/{CH_DATABASE}")
        ck = init_db()
        duck_writer = init_duckdb()
        if duck_writer is not None:
            print(f"本地 DuckDB 已就绪: {DUCKDB_PATH}")

    ok = False
    try:
        if args.full:
            run_full(pro, ck, args.start, args.workers, duck_writer)
        else:
            run_update(pro, ck, args.date, args.workers, duck_writer)
        ok = True
    except KeyboardInterrupt:
        print("\n用户中断，已保存的部分数据保留在数据库中。下次 --update 会自动补齐缺口。")
    finally:
        ck.disconnect()
        if duck_writer is not None:
            written = duck_writer.stop()
            print(f"DuckDB 写入完成，共 {written} 行。")

    if ok and duck_writer is not None:
        print()
        rebuild_market_state(DUCKDB_PATH)


if __name__ == "__main__":
    main()
