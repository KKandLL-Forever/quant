"""
fetch_margin.py — 抓 margin_detail（融资融券交易明细）入库，用于判断个股能否融券做空。

在 margin_detail 里出现 = 两融标的（可融资）；rqye(融券余额)/rqyl(融券余量)>0 = 实际有融券/有券源。
按交易日抓（一天全市场标的一次调用）；默认抓 2016 起的所有「月末+周末」交易日（够给月/周频回测标注）。
带 pickle 缓存，可重复跑只补缺失日。写入 duckdb 表 margin_detail。

字段：ts_code, trade_date, rzye(融资余额), rqye(融券余额), rqyl(融券余量), rqmcl(融券卖出量), rzrqye(两融余额)

用法：python quant_select/fetch_margin.py
"""

import os
import sys
import time
import pickle

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

import duckdb
import tushare as ts
from db_loader import _ENV

DUCK_PATH = _ENV.get("LOCAL_DUCKDB_PATH", "stock_data_tushare.duckdb")
CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
os.makedirs(CACHE_DIR, exist_ok=True)
RAW_CACHE = os.path.join(CACHE_DIR, "margin_detail_raw.pkl")
FIELDS = "trade_date,ts_code,rzye,rqye,rqyl,rqmcl,rzrqye"


def _rebalance_dates(con, start="20160101"):
    """月末 + 周末 交易日（去重），覆盖月频/周频回测的所有调仓日。"""
    return con.execute(f"""
        SELECT DISTINCT trade_date FROM (
            SELECT trade_date, ROW_NUMBER() OVER (PARTITION BY year(trade_date), month(trade_date)
                                                  ORDER BY trade_date DESC) rn
            FROM (SELECT DISTINCT trade_date FROM daily WHERE trade_date >= DATE '{start[:4]}-01-01')
        ) WHERE rn=1
        UNION
        SELECT DISTINCT trade_date FROM (
            SELECT trade_date, ROW_NUMBER() OVER (PARTITION BY date_trunc('week', trade_date)
                                                  ORDER BY trade_date DESC) rn
            FROM (SELECT DISTINCT trade_date FROM daily WHERE trade_date >= DATE '{start[:4]}-01-01')
        ) WHERE rn=1
        ORDER BY trade_date
    """).df()["trade_date"].astype(str).str.replace("-", "").str[:8].tolist()


def main():
    ts.set_token(_ENV["TUSHARE_TOKEN"])
    pro = ts.pro_api()
    con = duckdb.connect(DUCK_PATH)
    dates = _rebalance_dates(con)

    cache = {}
    if os.path.exists(RAW_CACHE):
        with open(RAW_CACHE, "rb") as f:
            cache = pickle.load(f)
    missing = [d for d in dates if d not in cache]
    print(f"调仓日 {len(dates)} 个，缓存命中 {len(dates)-len(missing)}，待抓 {len(missing)}")

    for i, d in enumerate(missing, 1):
        for attempt in range(4):
            try:
                cache[d] = pro.margin_detail(trade_date=d, fields=FIELDS)
                break
            except Exception as e:
                if attempt == 3:
                    print(f"  {d} 失败: {str(e)[:70]}")
                    cache[d] = pd.DataFrame()
                time.sleep(2 * (attempt + 1))
        if i % 30 == 0:
            print(f"  进度 {i}/{len(missing)}")
        time.sleep(0.3)
    with open(RAW_CACHE, "wb") as f:
        pickle.dump(cache, f)

    raw = pd.concat([df for df in cache.values() if df is not None and len(df)], ignore_index=True)
    raw = raw.dropna(subset=["ts_code", "trade_date"])
    con.register("raw_m", raw)
    con.execute("""
        CREATE OR REPLACE TABLE margin_detail AS
        SELECT ts_code, CAST(strptime(trade_date,'%Y%m%d') AS DATE) AS trade_date,
               rzye, rqye, rqyl, rqmcl, rzrqye
        FROM raw_m
        QUALIFY ROW_NUMBER() OVER (PARTITION BY ts_code, trade_date ORDER BY rzrqye DESC) = 1
    """)
    n = con.execute("SELECT COUNT(*) FROM margin_detail").fetchone()[0]
    rng = con.execute("SELECT MIN(trade_date), MAX(trade_date) FROM margin_detail").fetchone()
    con.close()
    print(f"已写入 duckdb 表 margin_detail：{n} 行（{rng[0]} ~ {rng[1]}）")


if __name__ == "__main__":
    main()
