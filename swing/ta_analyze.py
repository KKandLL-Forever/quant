"""ta_analyze.py — 用 TradingAgents-CN(DeepSeek)对单只A股做 技术面+消息面 综合分析,出买/卖/持。

走 TA-CN 的 LangGraph;市场分析师吃本地 DuckDB 价格(PIT),新闻分析师吃 a-stock-data
(巨潮公告+东财新闻+研报)。指定日期则按该日 PIT 截断(无未来函数),可用于复盘历史某天该不该卖。

环境：.venv312。用法：
  python swing/ta_analyze.py 300903 2026-06-26      # 指定股票+决策日
  python swing/ta_analyze.py 300903                 # 决策日默认最新交易日
依赖：tradingagents(已装) + ta_bridge + astock_news;DeepSeek key(读 x2 的 .env)、tushare token。
"""

import os
_ROOT = os.path.dirname(os.path.abspath(__file__))
while _ROOT != "/" and not os.path.exists(os.path.join(_ROOT, "cache_tushare.py")):
    _ROOT = os.path.dirname(_ROOT)
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, _ROOT)


def _load_keys():
    """加载 DEEPSEEK_API_KEY / TUSHARE_TOKEN:优先仓库根 .pyenv.local,回退旧的 x2strategy skill .env(仅本机)。"""
    def _read(path, keys):
        if not path or not os.path.exists(path):
            return
        for line in open(path, encoding="utf-8"):
            s = line.strip()
            for k in keys:
                if s.startswith(k) and "=" in s:
                    os.environ[k] = s.split("=", 1)[1].strip().strip('"').strip("'")

    _read(os.path.join(_ROOT, ".pyenv.local"), ("DEEPSEEK_API_KEY", "TUSHARE_TOKEN"))
    if not os.environ.get("DEEPSEEK_API_KEY"):
        _read(os.path.expanduser("~/.claude/skills/x2strategy/.env"), ("DEEPSEEK_API_KEY",))


def analyze(code, date, analysts=("market", "news"), progress_callback=None):
    """对 code 在 date 跑 TA-CN 综合分析,返回 (state, decision);progress_callback 收阶段进度。"""
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
    return ta.propagate(str(code).split(".")[0], date, progress_callback=progress_callback or (lambda *a, **k: None))


def _cli():
    from openai import OpenAI
    return OpenAI(api_key=os.environ["DEEPSEEK_API_KEY"], base_url="https://api.deepseek.com")


def _verdict_prompt(state):
    """构造分析师层买/卖/持的 prompt(技术面+消息面)。"""
    mk = (state.get("market_report") or "")[:3500]
    nw = (state.get("news_report") or "")[:3500]
    return f"""你是A股波段交易员,基于下面的技术面与消息面分析,只对"持仓者现在该买入/卖出/持有"给结论。
原则:① 上升趋势未破坏时,不要仅因涨幅大、RSI超买、缩量就判卖出(那会踏空主升浪);
② 只有出现明确利空催化(减持/立案/业绩暴雷/评级下调/重大利空公告)或技术破位(跌破关键均线/趋势线)才判卖出;
③ 趋势完好且无利空 → 持有或买入。

【技术面】
{mk}

【消息面】
{nw}

只输出JSON:{{"action":"买入|卖出|持有","confidence":0~1,"reasoning":"一句话理由"}}"""


def _parse_verdict(txt):
    """从模型输出解析买卖持 JSON,失败回退 raw。"""
    import json
    try:
        return json.loads(txt[txt.index("{"):txt.rindex("}") + 1])
    except Exception:
        return {"action": "?", "raw": txt[:200]}


def verdict_stream(state):
    """流式版分析师判断:逐段 yield {"delta":文本};结束 yield {"final":解析后的JSON}。"""
    buf = ""
    for ch in _cli().chat.completions.create(model="deepseek-v4-pro", temperature=0.2, stream=True,
                                             messages=[{"role": "user", "content": _verdict_prompt(state)}]):
        d = ch.choices[0].delta.content or ""
        if d:
            buf += d
            yield {"delta": d}
    yield {"final": _parse_verdict(buf)}


_LIVE_NODES = {
    "Market Analyst": ("技术面分析师", "📊", "market"),
    "News Analyst": ("消息面分析师", "📰", "news"),
    "Bull Researcher": ("看涨研究员", "🐂", "debate"),
    "Bear Researcher": ("看跌研究员", "🐻", "debate"),
    "Research Manager": ("研究经理", "👔", "manager"),
    "Trader": ("交易员", "💼", "trader"),
}


def analyze_live(code, date, on_event, analysts=("market", "news")):
    """自己迭代 LangGraph stream(updates 模式),每个 agent 节点一产出就 on_event(role,av,kind,text);返回 final state。"""
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
    company = str(code).split(".")[0]
    ta.ticker = company
    init = ta.propagator.create_initial_state(company, date)
    args = ta.propagator.get_graph_args(use_progress_callback=True)
    final = dict(init)
    for chunk in ta.graph.stream(init, **args):
        for node, upd in chunk.items():
            if node.startswith("__") or not isinstance(upd, dict):
                continue
            final.update(upd)
            meta = _LIVE_NODES.get(node)
            if not meta:
                continue
            role, av, kind = meta
            ids = upd.get("investment_debate_state") or {}
            txt = {"market": upd.get("market_report"), "news": upd.get("news_report"),
                   "debate": ids.get("current_response"), "manager": ids.get("judge_decision"),
                   "trader": upd.get("trader_investment_plan")}.get(kind)
            if txt and str(txt).strip():
                on_event(role, av, kind, str(txt).strip())
            if node == "Trader":   # 交易员之后是风控团队(前端不展示、verdict另算),提前结束省 DeepSeek(~4次调用)
                return final
    return final


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


def _business_prompt(code):
    """构造公司产业链分析 prompt,返回 (prompt, fintxt);无资料返回 (None, None)。"""
    import duckdb
    import tushare as ts
    import ta_bridge
    from cache_tushare import DUCKDB_PATH
    tscode = ta_bridge._norm(code)
    pro = ts.pro_api(os.environ["TUSHARE_TOKEN"])
    df = pro.stock_company(ts_code=tscode, fields="main_business,business_scope,introduction")
    if df is None or df.empty:
        return None, None
    r = df.iloc[0]
    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    fin = con.execute("""SELECT grossprofit_margin,netprofit_margin,roe,or_yoy,netprofit_yoy
        FROM fina_indicator WHERE ts_code=? AND grossprofit_margin IS NOT NULL ORDER BY end_date DESC LIMIT 1""", [tscode]).fetchone()
    mv = con.execute("""SELECT total_mv,pe_ttm,pb,ps_ttm,dv_ttm,trade_date FROM daily_basic WHERE ts_code=?
        ORDER BY trade_date DESC LIMIT 1""", [tscode]).fetchone()
    swr = con.execute("SELECT l1_name,l2_name,l3_name FROM sw_member WHERE ts_code=?", [tscode]).fetchone()
    yr = (mv[5].year if mv else __import__("datetime").date.today().year)
    ytd = con.execute("""SELECT d.close*a.adj_factor FROM daily d JOIN adj_factor a
        ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date
        WHERE d.ts_code=? AND d.trade_date>=? ORDER BY d.trade_date""", [tscode, f"{yr}-01-01"]).fetchall()
    ytd_pct = (ytd[-1][0] / ytd[0][0] - 1) * 100 if len(ytd) >= 2 else None
    prof = con.execute("""SELECT i.end_date, i.n_income_attr_p/1e8, f.netprofit_yoy FROM income i
        LEFT JOIN fina_indicator f ON f.ts_code=i.ts_code AND f.end_date=i.end_date
        WHERE i.ts_code=? ORDER BY i.end_date DESC LIMIT 6""", [tscode]).fetchall()
    ann = con.execute("""SELECT end_date, n_income_attr_p/1e8 FROM income
        WHERE ts_code=? AND end_date LIKE '%1231' AND n_income_attr_p IS NOT NULL
        ORDER BY end_date DESC LIMIT 4""", [tscode]).fetchall()
    roey = con.execute("""SELECT roe_yearly FROM fina_indicator WHERE ts_code=? AND roe_yearly IS NOT NULL
        ORDER BY end_date DESC LIMIT 1""", [tscode]).fetchone()
    con.close()
    import math
    cagr = None   # 近3年归母净利复合增速
    if len(ann) >= 2 and ann[-1][1] and ann[-1][1] > 0 and ann[0][1] and ann[0][1] > 0:
        cagr = (ann[0][1] / ann[-1][1]) ** (1 / (len(ann) - 1)) - 1
    fwd_np = None   # 券商一致预期·全年归母净利(亿)
    try:
        rc = pro.report_rc(ts_code=tscode, start_date=f"{yr-1}0101", end_date=f"{yr}1231")
        if rc is not None and len(rc):
            rc = rc[rc["quarter"].astype(str).str.endswith("Q4")]
            rc = rc[rc["quarter"].astype(str).str[:4].astype(int) >= yr]
            if len(rc):
                fwd_np = float(rc["np"].median()) / 1e4   # np 万元→亿
    except Exception:
        pass
    fwd_pe = (mv[0] / 1e4 / fwd_np) if (mv and fwd_np and fwd_np > 0) else None   # 市值(亿)/全年预测净利(亿)
    fwd_peg = (fwd_pe / (cagr * 100)) if (fwd_pe and cagr and cagr > 0) else None
    digest = (math.log((mv[1] if mv else 0) / 30) / math.log(1 + cagr)
              if (mv and mv[1] and mv[1] > 30 and cagr and cagr > 0) else None)
    tier = None if fwd_peg is None else (
        "极度低估" if fwd_peg < 0.5 else "低估" if fwd_peg < 1 else "合理" if fwd_peg < 1.5 else "偏贵" if fwd_peg < 2 else "高估")
    pegdict = None if fwd_peg is None else {
        "peg": round(fwd_peg, 2), "cagr": round(cagr * 100, 0), "fwd_pe": round(fwd_pe, 1),
        "fwd_np": round(fwd_np, 2), "tier": tier, "digest": round(digest, 1) if digest else None}
    pegtxt = "无券商预测/增速为负,PEG不适用" if fwd_peg is None else (
        f"3年净利CAGR {cagr*100:.0f}% | 券商全年预测净利 {fwd_np:.2f}亿 | 前瞻PE {fwd_pe:.1f} | "
        f"前瞻PEG {fwd_peg:.2f}({tier};<0.5极低估/0.5-1低估/1-1.5合理/1.5-2偏贵/>2高估)"
        + (f" | 当前PE需 {digest:.1f} 年增长消化到30x" if digest else ""))
    # PB-ROE / RIM(内在价值/现价 V/P):合理PB=(ROE-g)/(r-g),r=10%,g 取保守增长
    roe = (roey[0] / 100.0) if (roey and roey[0] is not None) else None   # 年化ROE(roe_yearly)
    pb = mv[2] if mv else None
    g_l = min(cagr, 0.08) if (cagr and cagr > 0) else 0.03
    just_pb = ((roe - g_l) / (0.10 - g_l)) if (roe is not None and roe > g_l) else None
    vp = (just_pb / pb) if (just_pb and pb and pb > 0) else None
    tooltxt = "；".join(x for x in [
        (f"PB-ROE:合理PB {just_pb:.2f}(用**当前年化ROE {roe*100:.0f}%**,r10%,g{g_l*100:.0f}%——注意:若ROE处周期底部/正拐点上行,此合理PB会被严重低估),现PB {pb:.2f} → RIM内在价值/现价 V/P {vp:.2f}(>1低估/<1高估)"
         if vp else None),
        (f"股息率 {mv[4]:.1f}%" if (mv and mv[4]) else None),
        (f"PS(TTM) {mv[3]:.1f}" if (mv and mv[3]) else None)] if x)
    fintxt = "无" if not fin else (f"毛利率{fin[0]:.1f}% 净利率{fin[1]:.1f}% ROE{fin[2]:.1f}% "
                                   f"营收同比{fin[3]:+.1f}% 净利同比{fin[4]:+.1f}%")
    mvtxt = "无" if not mv else f"总市值{mv[0]/10000:.0f}亿 PE(TTM){mv[1]} PB{mv[2]}"
    ytdtxt = "无" if ytd_pct is None else f"{yr}年初至今涨幅 {ytd_pct:+.1f}%"
    proftxt = " | ".join(f"{p[0]} 归母净利{p[1]:.2f}亿" + (f"(同比{p[2]:+.0f}%)" if p[2] is not None else "")
                         for p in reversed(prof)) or "无"
    sw = "/".join(x for x in (swr or []) if x) if swr else ""
    info = (f"主营:{r['main_business']}\n简介:{str(r['introduction'])[:600]}\n申万行业:{sw}\n"
            f"财务(最新报告期):{fintxt}\n规模估值:{mvtxt}\n股价:{ytdtxt}\n分期归母净利:{proftxt}\n"
            f"前瞻PEG(彼得·林奇口径):{pegtxt}\n估值工具箱:{tooltxt or '数据不足'}")
    prompt = f"""你是资深产业链分析师。基于下列公司资料+真实财务,**用数据说话**,深度分析其供应链地位与议价能力,不要泛泛而谈:

{info}

请判断并尽量量化:
1) products: 主营产品/核心业务
2) chain: 上游|中游|下游;chain_desc: 上下游分别是谁
3) market_pos: 全球与国内的市场地位/市占率(尽量给数字或区间,如"国内HDI约X%、全球前N";没把握就说"约/估")
4) pricing: 议价能力——结合毛利率{('('+str(fin[0])+'%)') if fin else ''}与行业对比,说强/中/弱及原因(高毛利=强议价)
5) bottleneck: 卡脖子方向——是"被卡"(依赖海外/国产替代)还是"卡别人"(我方主导/海外依赖我)还是"否";给 被卡|卡别人|否|部分,reason 一句话带依据
6) summary: 一句话总结其在供应链的真实地位
7) val_type: **先判企业类型**(据行业+财务特征,单选并说明依据一句话):
   消费白马/稳定成长 | 高成长科技医药 | 周期股 | 金融重资产 | 未盈利成长 | 公用高分红
8) val_method: **按类型选最合适的估值法**(别一律用PEG!):
   - 消费白马/稳定成长 → PE历史分位 + 前瞻PEG(增长匹配)
   - 高成长科技医药 → 前瞻PEG;未盈利则用 PS(TTM);讲清增长兑现节奏
   - 周期股 → **PB(底部区)**;**用 PB-ROE 要警惕:若当前ROE处周期底部/利润刚拐点(如近期净利暴增),用底部ROE算的合理PB会假性"高估",这正是周期陷阱——此时应改用正常化/中周期ROE判断,或直接以"利润拐点+PB历史分位"定夺,别被 V/P<1 误导**;忌用PE(顶部PE最低=陷阱)
   - 金融重资产 → **PB-ROE / RIM内在价值(V/P)**(ROE 较稳,V/P 可信度高)
   - 未盈利成长 → **PS(TTM)** + 赛道空间
   - 公用高分红 → **股息率 / DDM**
   说明:选了哪种、为什么、用上面「估值工具箱/前瞻PEG」里的对应数字得出的结论(一句话判断+关键数字)。
   **⚠️ 若 val_method(如RIM V/P)与 valuation(前瞻匹配)结论相反,必须点破原因**(通常=滞后ROE vs 前瞻业绩;周期股底部尤甚)并给一个统一判断,不要把两个相反结论并排丢出。
9) valuation: **股价-业绩 匹配测算**(所有类型都做):对比【年初至今涨幅】与【最近季报/半年报归母净利同比】判断背离;
   若要维持PE不变,涨X%→利润需同比+X%,据此反推下一报告期需要的利润额与单季增速,对照已披露季度看现实性;
   结论区分"业绩已兑现 / 靠预期透支"。给一句话+关键数字(涨幅、季度利润、PE、需要的单季利润)。
10) peg: **前瞻PEG 一句话结论**(基于「前瞻PEG」那行;标注不适用则说明为何——利润为负/无券商覆盖)。

只输出JSON:{{"products":"","chain":"上游|中游|下游","chain_desc":"","market_pos":"","pricing":"","bottleneck":"被卡|卡别人|部分|否","reason":"","summary":"","val_type":"","val_method":"","valuation":"","peg":""}}"""
    return prompt, fintxt, pegdict


BIZ_VER = "2026-07-05b"   # 公司分析 prompt/口径版本;改动即 +1,旧缓存自动失效重算


def _parse_business(txt, fintxt, pegdict=None):
    """解析公司分析 JSON,规整 chain 字段、附财务串 + 确定性 PEG 数据 + 版本号;失败回退 raw。"""
    import json
    try:
        d = json.loads(txt[txt.index("{"):txt.rindex("}") + 1])
        for x in ("上游", "中游", "下游"):
            if x in str(d.get("chain", "")):
                d["chain"] = x
                break
        d["fin"] = fintxt
        d["peg_data"] = pegdict
        d["_ver"] = BIZ_VER
        return d
    except Exception:
        return {"raw": txt[:400], "peg_data": pegdict, "_ver": BIZ_VER}


def business_stream(code):
    """流式版公司分析:逐段 yield {"delta":文本};结束 yield {"final":解析后的JSON}。"""
    prompt, fintxt, pegdict = _business_prompt(code)
    if prompt is None:
        yield {"final": {"raw": "无公司资料"}}
        return
    buf = ""
    for ch in _cli().chat.completions.create(model="deepseek-v4-pro", temperature=0.3, stream=True,
                                             messages=[{"role": "user", "content": prompt}]):
        d = ch.choices[0].delta.content or ""
        if d:
            buf += d
            yield {"delta": d}
    yield {"final": _parse_business(buf, fintxt, pegdict)}


def business_profile(code):
    """公司产业链地位深度分析(非流式,供缓存/回退)。"""
    prompt, fintxt, pegdict = _business_prompt(code)
    if prompt is None:
        return {"raw": "无公司资料"}
    txt = _cli().chat.completions.create(model="deepseek-v4-pro", temperature=0.3,
                                         messages=[{"role": "user", "content": prompt}]).choices[0].message.content
    return _parse_business(txt, fintxt, pegdict)


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
