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
    import ta_analyze
    ta_analyze._load_keys()
    state, risk_decision = ta_analyze.analyze(code, date)
    verdict = ta_analyze.analyst_verdict(state)
    res = {"market_report": state.get("market_report") or "",
           "news_report": state.get("news_report") or "",
           "verdict": verdict, "risk_decision": risk_decision}
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False)


if __name__ == "__main__":
    main()
