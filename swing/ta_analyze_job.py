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
    acc = {"stage": "多智能体分析中(约1-3分钟)…", "market_report": "", "news_report": "", "dialogue": []}
    _wp(acc)

    def on_event(role, av, kind, text):
        """每个 agent 产出即写进度文件,前端轮询即实时显示。"""
        if kind == "market":
            acc["market_report"] = text
            acc["stage"] = "消息面分析中…"
        elif kind == "news":
            acc["news_report"] = text
            acc["stage"] = "研究员辩论中…"
        else:
            acc["dialogue"].append({"role": role, "av": av, "text": text})
            acc["stage"] = f"{role}发言完成,继续…"
        _wp(acc)

    state = ta_analyze.analyze_live(code, date, on_event)
    res = {"market_report": acc["market_report"], "news_report": acc["news_report"],
           "dialogue": acc["dialogue"], "risk_decision": state.get("final_trade_decision") or ""}
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False)
    _wp({"stage": "报告完成", "done": True, **res})


if __name__ == "__main__":
    main()
