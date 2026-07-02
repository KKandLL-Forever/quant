"""
run_2lb_cne6.py — 用 CNE6 风格维度给 2lb(连板)做风险归因/风格画像

问题:2lb 首板→二板策略到底在赌哪些风格?连板股多是小盘题材股,几乎不在沪深300,
故不能用 cne6_factors(HS300池),改在**全市场**上看首板信号股的 CNE6 风格分位。

做法:
  ① 从 daily 的连板高度识别首板信号(pct_chg>=9.8 且昨日非涨停;剔创业/科创/北交/ST,与2lb口径一致);
  ② 对全市场每个交易日,算各风格维度的横截面分位(daily_basic:总市值/换手/量比/PB/PE);
  ③ 取首板信号股在信号日的平均分位 → 风格画像(50%=市场中位)。
告诉你:2lb 选的票在风格上有多极端(小盘?高换手?),从而判断其收益里有多少其实是风格暴露。

环境：.venv312。用法：python qlib_workflow/momentum/run_2lb_cne6.py
依赖：DuckDB(daily/daily_basic/stock_st)。
"""

import os
_ROOT = os.path.dirname(os.path.abspath(__file__))
while _ROOT != "/" and not os.path.exists(os.path.join(_ROOT, "cache_tushare.py")):
    _ROOT = os.path.dirname(_ROOT)
import sys

sys.path.insert(0, _ROOT)

import duckdb
import numpy as np
import pandas as pd

from cache_tushare import DUCKDB_PATH

START, END = "2020-01-01", "2026-05-30"
STYLES = {"total_mv": "市值(小=小盘)", "turnover_rate": "换手率", "volume_ratio": "量比",
          "pb": "PB", "pe_ttm": "PE_ttm"}


def main():
    con = duckdb.connect(DUCKDB_PATH, read_only=True)

    print("识别首板信号(全市场,剔创业/科创/北交/ST)...")
    d = con.execute("""
        SELECT ts_code, trade_date, pct_chg FROM daily
        WHERE trade_date BETWEEN ? AND ?
          AND ts_code NOT LIKE '30%' AND ts_code NOT LIKE '688%' AND ts_code NOT LIKE '%.BJ'
        ORDER BY ts_code, trade_date
    """, [START, END]).fetch_df()
    st = con.execute("SELECT DISTINCT ts_code, trade_date FROM stock_st").fetch_df()
    st_set = set(zip(st["ts_code"], st["trade_date"].astype(str)))

    d["up"] = d["pct_chg"] >= 9.8
    d["prev_up"] = d.groupby("ts_code")["up"].shift(1).fillna(False)
    sig = d[d["up"] & ~d["prev_up"]][["ts_code", "trade_date"]].copy()  # 首板=今涨停昨非涨停
    sig["key"] = list(zip(sig["ts_code"], sig["trade_date"].astype(str)))
    sig = sig[~sig["key"].isin(st_set)]
    print(f"首板信号 {len(sig)} 个 ({START}~{END})")

    db = con.execute("""
        SELECT ts_code, trade_date, total_mv, turnover_rate, volume_ratio, pb, pe_ttm
        FROM daily_basic WHERE trade_date BETWEEN ? AND ?
    """, [START, END]).fetch_df()
    con.close()

    for col in STYLES:
        db[col + "_p"] = db.groupby("trade_date")[col].rank(pct=True)
    db["key"] = list(zip(db["ts_code"], db["trade_date"].astype(str)))
    pmap = db.set_index("key")

    sig = sig.join(pmap[[c + "_p" for c in STYLES]], on="key")
    print("\n— 2lb 首板信号股的全市场风格分位(50%=市场中位)—")
    for col, name in STYLES.items():
        v = sig[col + "_p"].mean()
        bar = "极低" if v < 0.2 else "偏低" if v < 0.4 else "中性" if v < 0.6 else "偏高" if v < 0.8 else "极高"
        print(f"  {name:<14}{v*100:>5.1f}%   {bar}")


if __name__ == "__main__":
    main()
