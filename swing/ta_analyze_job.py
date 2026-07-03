"""ta_analyze_job.py — 子进程跑单股 技术+消息面 LLM 分析(多agent),结果写 JSON。

供后端 /api/analyze 以子进程方式调用,便于用户关闭弹窗时 kill 掉、终止在跑的分析(省 DeepSeek)。
只做慢的那部分(analyze + analyst_verdict);公司定位(business)由后端另算(带缓存、快)。

环境：.venv312。用法：python swing/ta_analyze_job.py <code> <date> <out.json>
依赖：ta_analyze(TradingAgents-CN/DeepSeek)。
"""
import json
import os
_ROOT = os.path.dirname(os.path.abspath(__file__))
while _ROOT != "/" and not os.path.exists(os.path.join(_ROOT, "cache_tushare.py")):
    _ROOT = os.path.dirname(_ROOT)
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, _ROOT)


def main():
    code, date, out = sys.argv[1], sys.argv[2], sys.argv[3]
    prog = out + ".progress"

    def _wp(d):
        try:
            with open(prog, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False)
        except Exception:
            pass

    import ta_analyze
    ta_analyze._load_keys()
    _wp({"stage": "多智能体分析中(技术面+消息面,约1-3分钟)…"})

    def cb(*a, **k):
        msg = next((str(x) for x in a if isinstance(x, str) and x.strip()), None)
        _wp({"stage": "分析中:" + msg if msg else "多智能体分析中…"})

    state, risk_decision = ta_analyze.analyze(code, date, progress_callback=cb)
    ids = state.get("investment_debate_state") or {}
    dialogue = []
    for key, role, av in (("bull_history", "看涨研究员", "🐂"), ("bear_history", "看跌研究员", "🐻"),
                          ("judge_decision", "研究经理", "👔")):
        t = (ids.get(key) or "").strip()
        if t:
            dialogue.append({"role": role, "av": av, "text": t})
    tp = (state.get("trader_investment_plan") or "").strip()
    if tp:
        dialogue.append({"role": "交易员", "av": "💼", "text": tp})
    res = {"market_report": state.get("market_report") or "",
           "news_report": state.get("news_report") or "",
           "dialogue": dialogue, "risk_decision": risk_decision}
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False)
    _wp({"stage": "报告完成", "done": True, **res})


if __name__ == "__main__":
    main()
