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
    try:
        ind = con.execute("""SELECT macd_dif_hfq,macd_dea_hfq,macd_hfq,kdj_k_hfq,kdj_d_hfq,kdj_hfq,
            rsi_hfq_6,rsi_hfq_12,rsi_hfq_24,boll_upper_hfq,boll_mid_hfq,boll_lower_hfq,
            bias1_hfq,bias2_hfq,bias3_hfq,cci_hfq,wr_hfq,dmi_pdi_hfq,dmi_mdi_hfq,dmi_adx_hfq
            FROM stk_factor_pro WHERE ts_code=? AND trade_date<=? ORDER BY trade_date DESC LIMIT 1""",
            [ts, str(end)]).fetchone()
    except Exception:
        ind = None
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
    if ind:
        (dif, dea, mac, kk, kd, kj, r6, r12, r24, bu, bm, bl, b1, b2, b3, cci, wr, pdi, mdi, adx) = ind
        ib = (f"\n## 技术指标(真实值,tushare,后复权)\n"
              f"MACD: DIF {dif:.2f} DEA {dea:.2f} 柱 {mac:+.2f}({'红柱/多头' if mac > 0 else '绿柱/空头'})\n"
              f"KDJ: K {kk:.1f} D {kd:.1f} J {kj:.1f}  | RSI: 6日 {r6:.1f} 12日 {r12:.1f} 24日 {r24:.1f}\n"
              f"BOLL(后复权): 上 {bu:.1f} 中 {bm:.1f} 下 {bl:.1f}  | BIAS: 6 {b1:+.1f}% 12 {b2:+.1f}% 24 {b3:+.1f}%\n"
              f"CCI {cci:.0f} | WR {wr:.1f} | DMI: +DI {pdi:.1f} -DI {mdi:.1f} ADX {adx:.1f}({'趋势强' if adx > 25 else '弱/盘整'})\n"
              f"(注:BOLL为后复权值,勿与上方原始价直接比;看 MACD金叉死叉/KDJ/RSI超买超卖/DMI 这些信号)\n")
    else:
        ib = "\n(技术指标 stk_factor_pro 无数据)\n"
    return (f"# {name}({ts}) 行情报告(截止 {str(end)[:10]},PIT)\n\n"
            f"最新收盘: {last['close']:.2f}  当日涨跌: {last['pct_chg']:+.2f}%\n"
            f"区间涨跌: 5日 {chg(5):+.1f}% | 20日 {chg(20):+.1f}% | 60日 {chg(60):+.1f}%\n"
            f"均线(原始价): MA5 {ma[5]:.2f} MA10 {ma[10]:.2f} MA20 {ma[20]:.2f} MA60 {ma[60]:.2f}  "
            f"(收盘在MA20{pos})\n{ib}\n## 近30个交易日明细\n{tbl}\n")


PIT_END = None
"""回测用 PIT 截断日(YYYY-MM-DD);设了之后新闻工具只取≤该日的公告/研报/新闻,避免未来函数。实盘留 None。"""


def _make_news_tool(toolkit=None):
    """构造统一新闻工具(普通函数,与原版一致:可直接调用也可 bind_tools),底层走 a-stock-data(东财新闻+研报+巨潮公告)。"""
    import astock_news

    def get_stock_news_unified(stock_code: str, max_news: int = 10, model_info: str = "") -> str:
        """获取个股新闻+研报+公告(东财/巨潮),返回带日期摘要;新闻/公告仅近7天,研报放长窗;PIT_END 设了则按该日截断。"""
        import datetime as _dt
        code = str(stock_code).split(".")[0]
        end = PIT_END
        ref = end or _dt.date.today().isoformat()
        floor7 = (_dt.date.fromisoformat(ref) - _dt.timedelta(days=7)).isoformat()
        try:
            news = [n for n in astock_news.stock_news(code)
                    if floor7 <= n["time"][:10] <= ref][:max_news]
        except Exception:
            news = []
        try:
            reps = [r for r in astock_news.stock_reports(code, end=end or "2030-01-01") if not end or r["date"] <= end][:8]
        except Exception:
            reps = []
        try:
            anns = [a for a in astock_news.stock_announcements(code, end=end)
                    if a["date"] >= floor7][:12]
        except Exception:
            anns = []
        at = "\n".join(f"- {a['date']} {a['title']}" for a in anns) or "(近7天无公告)"
        nt = "\n".join(f"- {n['time'][:16]} {n['source']}: {n['title']}" for n in news) or "(近7天无新闻)"
        rt = "\n".join(f"- {r['date']} {r['org']} 评级{r['rating']}: {r['title']}" for r in reps) or "(无研报)"
        return (f"# {code} 消息面\n\n## 官方公告(巨潮,近7天,利空/利好催化最关键)\n{at}\n\n"
                f"## 个股新闻(东财,近7天)\n{nt}\n\n## 研报评级(东财,近期)\n{rt}\n")

    get_stock_news_unified.name = "get_stock_news_unified"
    get_stock_news_unified.description = "获取个股近期新闻与研报(东财),返回带日期的中文摘要,用于消息面分析"
    return get_stock_news_unified


def apply():
    """patch TA-CN:行情/资料走本地 DuckDB,新闻工具走 a-stock-data(东财)。"""
    from tradingagents.dataflows import interface
    interface.get_china_stock_info_unified = get_china_stock_info_unified
    interface.get_china_stock_data_unified = get_china_stock_data_unified
    import tradingagents.tools.unified_news_tool as unt
    unt.create_unified_news_tool = _make_news_tool
    from tradingagents.agents.analysts import news_analyst
    news_analyst.create_unified_news_tool = _make_news_tool
    # WHY: news 分析师靠 "DeepSeek" in 类名 触发"预抓新闻直喂LLM"(避开 deepseek 不可靠的 tool-loop),
    # 但 TA-CN 把 deepseek 包装成 NormalizedChatOpenAI(不含该字样)→ 预抓不触发 → 报告空。改名强制触发。
    from tradingagents.llm_clients.openai_client import NormalizedChatOpenAI
    NormalizedChatOpenAI.__name__ = "DeepSeekNormalizedChatOpenAI"
    return True
