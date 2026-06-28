"""
fill_stock_meta.py — 一次性补全本地 DuckDB 的 stock_meta 表

背景：cache_tushare.py 只在 --full 时抓 stock_meta，--update 不碰它，
导致只跑过 --update 的本地库 stock_meta 为空，下游（如 cache_skill.py 取股票池）拿不到数据。
本脚本用 tushare stock_basic 拉全市场基础信息（含已退市），写入本地 DuckDB 的 stock_meta。

用法：python first10/fill_stock_meta.py
依赖：tushare、duckdb；token 与库路径复用 cache_tushare.py 的 .pyenv.local 配置。
"""

import os
import sys

import duckdb
import pandas as pd
import tushare as ts

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cache_tushare import DUCKDB_PATH, COLUMNS, _get_token, _DUCK_DDL


def main():
    """拉 stock_basic 全量并幂等写入本地 DuckDB stock_meta。"""
    if not DUCKDB_PATH:
        raise RuntimeError("未配置 LOCAL_DUCKDB_PATH")
    pro = ts.pro_api(_get_token())
    fields = "ts_code,name,area,industry,list_date,delist_date"
    parts = []
    for status in ("L", "D", "P"):
        df = pro.stock_basic(exchange="", list_status=status, fields=fields)
        parts.append(df)
    df = pd.concat(parts, ignore_index=True)
    for c in COLUMNS["stock_meta"]:
        if c not in df.columns:
            df[c] = ""
    df = df[COLUMNS["stock_meta"]].fillna("").astype(str)

    con = duckdb.connect(DUCKDB_PATH)
    con.execute(_DUCK_DDL["stock_meta"])
    con.register("_m", df)
    con.execute("INSERT OR REPLACE INTO stock_meta SELECT * FROM _m")
    con.unregister("_m")
    n = con.execute("SELECT COUNT(*) FROM stock_meta").fetchone()[0]
    con.close()
    print(f"stock_meta 写入完成，当前共 {n} 行。")


if __name__ == "__main__":
    main()
