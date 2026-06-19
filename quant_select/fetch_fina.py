"""
fetch_fina.py — 抓 fina_indicator（财务指标）入库，供多因子选股的质量/成长因子用。

用 fina_indicator_vip 按报告期批量拉（一次一个季度全市场），2014~至今 ~50 个报告期。
字段：roe、毛利率、净利率、资产负债率、净利同比、营收同比、扣非净利同比 + ann_date(公告日,PIT关键)。
去重：同 (ts_code, end_date) 保留 ann_date 最新的一条（取最新更正/披露）。
写入 duckdb 表 fina_indicator。带 pickle 缓存，可重复跑只补缺失报告期。

用法：python quant_select/fetch_fina.py
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
RAW_CACHE = os.path.join(CACHE_DIR, "fina_indicator_raw.pkl")

FIELDS = ("ts_code,ann_date,end_date,roe,grossprofit_margin,netprofit_margin,"
          "debt_to_assets,netprofit_yoy,or_yoy,dt_netprofit_yoy")


def _periods(start_year=2014):
    """生成 start_year 至今的所有季报报告期 YYYYMMDD。"""
    out = []
    from datetime import date
    y0, now = start_year, date.today()
    for y in range(y0, now.year + 1):
        for mmdd in ("0331", "0630", "0930", "1231"):
            p = f"{y}{mmdd}"
            if p <= now.strftime("%Y%m%d"):
                out.append(p)
    return out


def main():
    ts.set_token(_ENV["TUSHARE_TOKEN"])
    pro = ts.pro_api()

    cache = {}
    if os.path.exists(RAW_CACHE):
        with open(RAW_CACHE, "rb") as f:
            cache = pickle.load(f)
    periods = _periods()
    missing = [p for p in periods if p not in cache]
    print(f"报告期 {len(periods)} 个，缓存命中 {len(periods)-len(missing)}，待抓 {len(missing)}")

    for i, p in enumerate(missing, 1):
        for attempt in range(4):
            try:
                cache[p] = pro.fina_indicator_vip(period=p, fields=FIELDS)
                break
            except Exception as e:
                if attempt == 3:
                    print(f"  {p} 抓取失败: {str(e)[:80]}")
                    cache[p] = pd.DataFrame()
                time.sleep(2 * (attempt + 1))
        if i % 10 == 0:
            print(f"  进度 {i}/{len(missing)}")
        time.sleep(0.4)
    with open(RAW_CACHE, "wb") as f:
        pickle.dump(cache, f)

    allp = [df for df in cache.values() if df is not None and len(df)]
    raw = pd.concat(allp, ignore_index=True)
    raw = raw.dropna(subset=["ts_code", "end_date", "ann_date"])
    print(f"\n合并 {len(raw)} 行，去重前...")

    con = duckdb.connect(DUCK_PATH)
    con.register("raw_fina", raw)
    con.execute("""
        CREATE OR REPLACE TABLE fina_indicator AS
        SELECT * FROM raw_fina
        QUALIFY ROW_NUMBER() OVER (PARTITION BY ts_code, end_date ORDER BY ann_date DESC) = 1
    """)
    n = con.execute("SELECT COUNT(*) FROM fina_indicator").fetchone()[0]
    rng = con.execute("SELECT MIN(end_date), MAX(end_date) FROM fina_indicator").fetchone()
    con.close()
    print(f"已写入 duckdb 表 fina_indicator：{n} 行（报告期 {rng[0]} ~ {rng[1]}）")


if __name__ == "__main__":
    main()
