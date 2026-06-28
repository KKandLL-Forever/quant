"""ta_bridge.py — 把 TradingAgents-CN 的 A股数据接口桥接到本地 DuckDB(cache_tushare)。

TA-CN 自带的 A股数据层(akshare/tushare/baostock)在本环境全线失效(版本漂移+缺方法),
导致分析师拿不到正确股票名/价格、甚至对错公司分析。本模块 monkeypatch 两个统一接口,
让"市场(技术)分析师 + 股票资料"直接吃本地 DuckDB,且按 end_date 做 PIT 截断(无未来函数)。

用法:在构建 TradingAgentsGraph 之前 `import ta_bridge; ta_bridge.apply()`。
覆盖:get_china_stock_info_unified(股票资料)、get_china_stock_data_unified(日线行情报告)。
情绪/新闻无自有数据源,不在此桥接范围。
依赖:DuckDB(daily/adj_factor/stock_meta/sw_member);复用 cache_tushare.DUCKDB_PATH。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.expanduser("~/AI/quart"))

import duckdb
import pandas as pd

from cache_tushare import DUCKDB_PATH


def _norm(ticker: str) -> str:
    """6位代码补全为 DuckDB 的 ts_code(.SZ/.SH/.BJ)。"""
    t = str(ticker).strip().upper()
    if "." in t:
        return t
    if t.startswith(("0", "3")):
        return t + ".SZ"
    if t.startswith("6"):
        return t + ".SH"
    return t + ".BJ"


def _con():
    return duckdb.connect(DUCKDB_PATH, read_only=True)


def get_china_stock_info_unified(ticker, *a, **k):
    """从 stock_meta + sw_member 返回股票资料文本(真名/地区/行业)。"""
    ts = _norm(ticker)
    con = _con()
    row = con.execute("SELECT name,area,industry,list_date FROM stock_meta WHERE ts_code=?", [ts]).fetchone()
    sw = con.execute("SELECT l1_name,l2_name,l3_name FROM sw_member WHERE ts_code=?", [ts]).fetchone()
    con.close()
    if not row:
        return f"股票代码: {ts}\n股票名称: 未知\n数据来源: local_duckdb(未找到)"
    name, area, industry, list_date = row
    swtxt = "/".join([x for x in (sw or []) if x]) if sw else "未知"
    return (f"股票代码: {ts}\n股票名称: {name}\n所属地区: {area or '未知'}\n"
            f"所属行业: {industry or '未知'}\n申万行业: {swtxt}\n上市日期: {list_date or '未知'}\n数据来源: local_duckdb")


def get_china_stock_data_unified(ticker, start_date=None, end_date=None, *a, **k):
    """从 DuckDB daily 返回 PIT 日线行情报告(≤end_date),含近30日明细 + MA/涨跌/量能摘要。"""
    ts = _norm(ticker)
    con = _con()
    name = (con.execute("SELECT name FROM stock_meta WHERE ts_code=?", [ts]).fetchone() or [ts])[0]
    end = str(end_date) if end_date else con.execute("SELECT MAX(trade_date) FROM daily").fetchone()[0]
    g = con.execute("""SELECT trade_date, open, high, low, close, vol, pct_chg FROM daily
        WHERE ts_code=? AND trade_date<=? ORDER BY trade_date""", [ts, str(end)]).fetch_df()
    con.close()
    if g.empty:
        return f"{name}({ts}) 无本地行情数据(≤{end})"
    g["trade_date"] = pd.to_datetime(g["trade_date"])
    ca = g["close"]
    ma = {p: ca.rolling(p).mean().iloc[-1] for p in (5, 10, 20, 60)}
    last = g.iloc[-1]
    chg = lambda n: (ca.iloc[-1] / ca.iloc[-1 - n] - 1) * 100 if len(ca) > n else float("nan")
    rec = g.tail(30)[["trade_date", "open", "high", "low", "close", "pct_chg", "vol"]].copy()
    rec["trade_date"] = rec["trade_date"].dt.date
    tbl = rec.to_string(index=False)
    pos = "上方" if last["close"] > ma[20] else "下方"
    return (f"# {name}({ts}) 行情报告(截止 {str(end)[:10]},PIT)\n\n"
            f"最新收盘: {last['close']:.2f}  当日涨跌: {last['pct_chg']:+.2f}%\n"
            f"区间涨跌: 5日 {chg(5):+.1f}% | 20日 {chg(20):+.1f}% | 60日 {chg(60):+.1f}%\n"
            f"均线(原始价): MA5 {ma[5]:.2f} MA10 {ma[10]:.2f} MA20 {ma[20]:.2f} MA60 {ma[60]:.2f}  "
            f"(收盘在MA20{pos})\n\n## 近30个交易日明细\n{tbl}\n")


def apply():
    """对 tradingagents.dataflows.interface 打补丁,使上述两接口走本地 DuckDB。"""
    from tradingagents.dataflows import interface
    interface.get_china_stock_info_unified = get_china_stock_info_unified
    interface.get_china_stock_data_unified = get_china_stock_data_unified
    return True
