"""从数据库加载股票数据（支持 ClickHouse 云端 / 本地 DuckDB）

切换数据源：
  注释掉当前启用的 "── 使用 ClickHouse ──" 区域，
  解除注释 "── 使用 DuckDB ──" 区域，反之亦然。
  两个区域暴露完全相同的函数名，上层代码无需修改。
"""

import os

import pandas as pd
from clickhouse_driver import Client

from data_loader import is_abnormal_stock, is_restricted_board

try:
    import duckdb as _duckdb
except ImportError:
    _duckdb = None

# ── .pyenv.local 加载 ───────────────────────────────────────────────────────

PYENV_FILE = os.path.join(os.path.dirname(__file__), ".pyenv.local")


def _load_pyenv() -> dict[str, str]:
    """解析 .pyenv.local 文件，返回 {KEY: VALUE} 字典。"""
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

# ════════════════════════════════════════════════════════════════════════════
# ── 使用 ClickHouse（云端）── 当前启用
#    切换到 DuckDB：注释掉本区域（到下一条分隔线），解除 DuckDB 区域注释
# ════════════════════════════════════════════════════════════════════════════

# 切换云：注释掉一组、启用另一组
# —— 阿里云 ECS
# CH_HOST     = _ENV.get("ALI_CLICKHOUSE_IP",       "47.117.166.2")
# CH_USER     = _ENV.get("ALI_CLICKHOUSE_USERNAME", "default")
# CH_PASSWORD = _ENV.get("ALI_CLICKHOUSE_PASSWORD", "")

# —— AWS EC2（当前启用）
CH_HOST     = _ENV.get("AWS_CLICKHOUSE_IP",       "47.129.255.54")
CH_USER     = _ENV.get("AWS_CLICKHOUSE_USERNAME", "default")
CH_PASSWORD = _ENV.get("AWS_CLICKHOUSE_PASSWORD", "")

CH_PORT     = int(os.environ.get("CH_PORT", "9000"))
CH_DATABASE = os.environ.get("CH_DATABASE", "tushare_stock")


def _make_client() -> Client:
    return Client(host=CH_HOST, port=CH_PORT,
                  user=CH_USER, password=CH_PASSWORD,
                  database=CH_DATABASE)


def load_stock_from_db(ts_code: str) -> pd.DataFrame | None:
    """从 ClickHouse 加载单只股票日线。列：code, name, date, open, high, low, close, volume"""
    if is_restricted_board(ts_code):
        return None
    ck = _make_client()
    try:
        rows = ck.execute(
            """
            SELECT d.ts_code, d.trade_date, d.open, d.high, d.low, d.close, d.vol, m.name
            FROM daily d
            LEFT JOIN stock_meta m ON d.ts_code = m.ts_code
            WHERE d.ts_code = %(code)s
            ORDER BY d.trade_date
            """,
            {"code": ts_code},
        )
    finally:
        ck.disconnect()
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["code", "date", "open", "high", "low", "close", "volume", "name"])
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    if is_abnormal_stock(df):
        return None
    return df


def list_stocks_from_db() -> list[str]:
    """返回 stock_meta 中所有股票的 ts_code 列表。"""
    ck = _make_client()
    try:
        rows = ck.execute("SELECT ts_code FROM stock_meta ORDER BY ts_code")
        return [r[0] for r in rows]
    finally:
        ck.disconnect()


def load_latest_circ_mv() -> dict[str, float]:
    """返回每只股票最新交易日流通市值（万元）。"""
    ck = _make_client()
    try:
        rows = ck.execute(
            """
            SELECT ts_code, argMax(circ_mv, trade_date) AS circ_mv
            FROM daily_basic
            GROUP BY ts_code
            HAVING isFinite(circ_mv) AND circ_mv > 0
            """
        )
        return {r[0]: r[1] for r in rows}
    except Exception:
        return {}
    finally:
        ck.disconnect()

# ════════════════════════════════════════════════════════════════════════════
# ── 使用 DuckDB（本地）── 注释掉上方 ClickHouse 区域后解除以下注释
# ════════════════════════════════════════════════════════════════════════════

# DUCKDB_PATH = _ENV.get("LOCAL_DUCKDB_PATH", "stock_data_tushare.duckdb")
#
#
# def _duck_con():
#     if _duckdb is None:
#         raise RuntimeError("duckdb 未安装，请执行: pip install duckdb")
#     return _duckdb.connect(DUCKDB_PATH, read_only=True)
#
#
# def load_stock_from_db(ts_code: str) -> pd.DataFrame | None:
#     """从本地 DuckDB 加载单只股票日线。"""
#     if is_restricted_board(ts_code):
#         return None
#     con = _duck_con()
#     try:
#         df = con.execute(
#             """
#             SELECT d.ts_code AS code, d.trade_date AS date,
#                    d.open, d.high, d.low, d.close, d.vol AS volume, m.name
#             FROM daily d
#             LEFT JOIN stock_meta m ON d.ts_code = m.ts_code
#             WHERE d.ts_code = ?
#             ORDER BY d.trade_date
#             """,
#             [ts_code],
#         ).df()
#     finally:
#         con.close()
#     if df.empty:
#         return None
#     df["date"] = pd.to_datetime(df["date"])
#     df = df.sort_values("date").reset_index(drop=True)
#     if is_abnormal_stock(df):
#         return None
#     return df
#
#
# def list_stocks_from_db() -> list[str]:
#     """返回 stock_meta 中所有股票的 ts_code 列表。"""
#     con = _duck_con()
#     try:
#         return con.execute("SELECT ts_code FROM stock_meta ORDER BY ts_code").df()["ts_code"].tolist()
#     finally:
#         con.close()
#
#
# def load_latest_circ_mv() -> dict[str, float]:
#     """返回每只股票最新交易日流通市值（万元）。"""
#     con = _duck_con()
#     try:
#         df = con.execute(
#             """
#             SELECT ts_code, circ_mv
#             FROM daily_basic
#             QUALIFY ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY trade_date DESC) = 1
#             WHERE circ_mv IS NOT NULL AND circ_mv > 0
#             """
#         ).df()
#         return dict(zip(df["ts_code"], df["circ_mv"]))
#     except Exception:
#         return {}
#     finally:
#         con.close()
