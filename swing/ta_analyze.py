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
                "quick_think_llm": "deepseek-v4-pro", "backend_url": "https://api.deepseek.com",
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
