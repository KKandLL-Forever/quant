"""ta_analyze.py — 用 TradingAgents-CN(DeepSeek)对单只A股做 技术面+消息面 综合分析,出买/卖/持。

走 TA-CN 的 LangGraph;市场分析师吃本地 DuckDB 价格(PIT),新闻分析师吃 a-stock-data
(巨潮公告+东财新闻+研报)。指定日期则按该日 PIT 截断(无未来函数),可用于复盘历史某天该不该卖。

环境：.venv312。用法：
  python swing/ta_analyze.py 300903 2026-06-26      # 指定股票+决策日
  python swing/ta_analyze.py 300903                 # 决策日默认最新交易日
依赖：tradingagents(已装) + ta_bridge + astock_news;DeepSeek key(读 x2 的 .env)、tushare token。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.expanduser("~/AI/quart"))


def _load_keys():
    env = os.path.expanduser("~/.claude/skills/x2strategy/.env")
    for line in open(env):
        if line.startswith("DEEPSEEK_API_KEY") and "=" in line:
            os.environ["DEEPSEEK_API_KEY"] = line.split("=", 1)[1].strip().strip('"').strip("'")
    pe = os.path.expanduser("~/AI/quart/.pyenv.local")
    if os.path.exists(pe):
        for line in open(pe):
            if line.strip().startswith("TUSHARE_TOKEN") and "=" in line:
                os.environ["TUSHARE_TOKEN"] = line.split("=", 1)[1].strip().strip('"').strip("'")


def analyze(code, date, analysts=("market", "news")):
    """对 code 在 date 跑 TA-CN 综合分析,返回 (state, decision)。"""
    import ta_bridge
    ta_bridge.apply()
    ta_bridge.PIT_END = date
    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    cfg = dict(DEFAULT_CONFIG)
    cfg.update({"llm_provider": "deepseek", "deep_think_llm": "deepseek-v4-pro",
                "quick_think_llm": "deepseek-v4-flash", "backend_url": "https://api.deepseek.com",
                "online_tools": True, "max_debate_rounds": 1})
    ta = TradingAgentsGraph(selected_analysts=list(analysts), debug=False, config=cfg)
    return ta.propagate(str(code).split(".")[0], date, progress_callback=lambda *a, **k: None)


def analyst_verdict(state):
    """选A:基于分析师层(技术面+消息面)报告做趋势感知的买/卖/持聚合,绕开 TA-CN 风险经理的系统性恐高。"""
    import json
    from openai import OpenAI
    mk = (state.get("market_report") or "")[:3500]
    nw = (state.get("news_report") or "")[:3500]
    prompt = f"""你是A股波段交易员,基于下面的技术面与消息面分析,只对"持仓者现在该买入/卖出/持有"给结论。
原则:① 上升趋势未破坏时,不要仅因涨幅大、RSI超买、缩量就判卖出(那会踏空主升浪);
② 只有出现明确利空催化(减持/立案/业绩暴雷/评级下调/重大利空公告)或技术破位(跌破关键均线/趋势线)才判卖出;
③ 趋势完好且无利空 → 持有或买入。

【技术面】
{mk}

【消息面】
{nw}

只输出JSON:{{"action":"买入|卖出|持有","confidence":0~1,"reasoning":"一句话理由"}}"""
    cli = OpenAI(api_key=os.environ["DEEPSEEK_API_KEY"], base_url="https://api.deepseek.com")
    txt = cli.chat.completions.create(model="deepseek-v4-pro", temperature=0.2,
                                      messages=[{"role": "user", "content": prompt}]).choices[0].message.content
    try:
        return json.loads(txt[txt.index("{"):txt.rindex("}") + 1])
    except Exception:
        return {"action": "?", "raw": txt[:200]}


def business_profile(code):
    """公司产业链地位深度分析:主营/上中下游/全球+国内市占率/议价能力(结合毛利率等真实财务)/双向卡脖子。"""
    import json
    import duckdb
    import tushare as ts
    from openai import OpenAI
    import ta_bridge
    from cache_tushare import DUCKDB_PATH
    tscode = ta_bridge._norm(code)
    pro = ts.pro_api(os.environ["TUSHARE_TOKEN"])
    df = pro.stock_company(ts_code=tscode, fields="main_business,business_scope,introduction")
    if df is None or df.empty:
        return {"raw": "无公司资料"}
    r = df.iloc[0]
    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    fin = con.execute("""SELECT grossprofit_margin,netprofit_margin,roe,or_yoy,netprofit_yoy
        FROM fina_indicator WHERE ts_code=? AND grossprofit_margin IS NOT NULL ORDER BY end_date DESC LIMIT 1""", [tscode]).fetchone()
    mv = con.execute("""SELECT total_mv,pe_ttm,pb FROM daily_basic WHERE ts_code=?
        ORDER BY trade_date DESC LIMIT 1""", [tscode]).fetchone()
    swr = con.execute("SELECT l1_name,l2_name,l3_name FROM sw_member WHERE ts_code=?", [tscode]).fetchone()
    con.close()
    fintxt = "无" if not fin else (f"毛利率{fin[0]:.1f}% 净利率{fin[1]:.1f}% ROE{fin[2]:.1f}% "
                                   f"营收同比{fin[3]:+.1f}% 净利同比{fin[4]:+.1f}%")
    mvtxt = "无" if not mv else f"总市值{mv[0]/10000:.0f}亿 PE(TTM){mv[1]} PB{mv[2]}"
    sw = "/".join(x for x in (swr or []) if x) if swr else ""
    info = (f"主营:{r['main_business']}\n简介:{str(r['introduction'])[:600]}\n申万行业:{sw}\n"
            f"财务(最新报告期):{fintxt}\n规模估值:{mvtxt}")
    prompt = f"""你是资深产业链分析师。基于下列公司资料+真实财务,**用数据说话**,深度分析其供应链地位与议价能力,不要泛泛而谈:

{info}

请判断并尽量量化:
1) products: 主营产品/核心业务
2) chain: 上游|中游|下游;chain_desc: 上下游分别是谁
3) market_pos: 全球与国内的市场地位/市占率(尽量给数字或区间,如"国内HDI约X%、全球前N";没把握就说"约/估")
4) pricing: 议价能力——结合毛利率{('('+str(fin[0])+'%)') if fin else ''}与行业对比,说强/中/弱及原因(高毛利=强议价)
5) bottleneck: 卡脖子方向——是"被卡"(依赖海外/国产替代)还是"卡别人"(我方主导/海外依赖我)还是"否";给 被卡|卡别人|否|部分,reason 一句话带依据
6) summary: 一句话总结其在供应链的真实地位

只输出JSON:{{"products":"","chain":"上游|中游|下游","chain_desc":"","market_pos":"","pricing":"","bottleneck":"被卡|卡别人|部分|否","reason":"","summary":""}}"""
    cli = OpenAI(api_key=os.environ["DEEPSEEK_API_KEY"], base_url="https://api.deepseek.com")
    txt = cli.chat.completions.create(model="deepseek-v4-pro", temperature=0.3,
                                      messages=[{"role": "user", "content": prompt}]).choices[0].message.content
    try:
        d = json.loads(txt[txt.index("{"):txt.rindex("}") + 1])
        for x in ("上游", "中游", "下游"):  # 模型有时把整串枚举塞进来,取第一个匹配
            if x in str(d.get("chain", "")):
                d["chain"] = x
                break
        d["fin"] = fintxt
        return d
    except Exception:
        return {"raw": txt[:400]}


def _latest_td():
    import duckdb
    from cache_tushare import DUCKDB_PATH
    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    d = con.execute("SELECT MAX(trade_date) FROM daily").fetchone()[0]
    con.close()
    return str(d)


if __name__ == "__main__":
    _load_keys()
    code = sys.argv[1] if len(sys.argv) > 1 else "300903"
    date = sys.argv[2] if len(sys.argv) > 2 else _latest_td()
    print(f"分析 {code} @ {date}(PIT 截断)...\n")
    state, decision = analyze(code, date)
    for k, label in [("market_report", "技术面"), ("news_report", "消息面")]:
        v = state.get(k)
        print(f"\n========== {label} ==========\n{(v or '(空)')[:2000]}")
    print(f"\n========== 风险经理决策(TA-CN原版,偏恐高) ==========\n{decision}")
    print(f"\n========== 分析师层判断(选A,趋势感知) ==========\n{analyst_verdict(state)}")
