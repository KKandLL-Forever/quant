"""news_verdict.py — 个股新闻+研报 → LLM(DeepSeek) 给买/卖/持评价(情绪/消息面卖点辅助)。

拉 astock_news 的个股新闻+研报(研报按 end_date 做 PIT 截断),组装摘要喂 DeepSeek,
输出 {action:买入/卖出/持有, confidence, reasoning}。用于对持仓股做消息面卖点预警/回测辅助。

注意 PIT:研报可按日期截断(干净);个股新闻东财只返回近期(历史回测会泄露),
故历史回测谨慎、近端实盘可用。

环境：.venv312。用法：python swing/news_verdict.py 300903 2026-06-26
依赖：astock_news;DeepSeek key(读 ~/.claude/skills/x2strategy/.env)。
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import astock_news


def _deepseek_key():
    """取 DEEPSEEK_API_KEY:优先仓库根 .pyenv.local,回退旧的 x2strategy skill .env。"""
    root = os.path.dirname(os.path.abspath(__file__))
    while root != "/" and not os.path.exists(os.path.join(root, "cache_tushare.py")):
        root = os.path.dirname(root)
    for path in (os.path.join(root, ".pyenv.local"), os.path.expanduser("~/.claude/skills/x2strategy/.env")):
        if not os.path.exists(path):
            continue
        for line in open(path):
            if line.strip().startswith("DEEPSEEK_API_KEY") and "=" in line:
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("未找到 DEEPSEEK_API_KEY(请在 .pyenv.local 配置)")


def verdict(code, end_date, name=""):
    """对 code 在 end_date 的消息面给买/卖/持评价(研报≤end_date,新闻取近期)。"""
    reps = [r for r in astock_news.stock_reports(code, end=end_date) if r["date"] <= end_date][:8]
    news = [n for n in astock_news.stock_news(code) if n["time"][:10] <= end_date][:12]
    rep_txt = "\n".join(f"- {r['date']} {r['org']} 评级{r['rating']}: {r['title']}" for r in reps) or "(无)"
    news_txt = "\n".join(f"- {n['time'][:10]} {n['source']}: {n['title']}" for n in news) or "(无)"
    prompt = f"""你是A股消息面分析师。基于下列{name}({code})截至 {end_date} 的研报与新闻,
判断当前消息面对持仓者意味着应该「买入/卖出/持有」哪个动作,聚焦是否有利空催化(减持/立案/业绩暴雷/评级下调)或利好。

【研报】
{rep_txt}

【新闻】
{news_txt}

只输出 JSON:{{"action":"买入|卖出|持有","confidence":0~1,"reasoning":"一句话理由"}}"""
    from openai import OpenAI
    cli = OpenAI(api_key=_deepseek_key(), base_url="https://api.deepseek.com")
    r = cli.chat.completions.create(model="deepseek-v4-pro", temperature=0.2,
                                    messages=[{"role": "user", "content": prompt}])
    txt = r.choices[0].message.content
    try:
        return json.loads(txt[txt.index("{"):txt.rindex("}") + 1]), rep_txt, news_txt
    except Exception:
        return {"raw": txt}, rep_txt, news_txt


if __name__ == "__main__":
    code = sys.argv[1] if len(sys.argv) > 1 else "300903"
    end = sys.argv[2] if len(sys.argv) > 2 else "2026-06-26"
    v, rep, news = verdict(code, end)
    print(f"=== {code} 截至 {end} ===\n[研报]\n{rep}\n\n[新闻]\n{news}\n\n[消息面评价]\n{json.dumps(v, ensure_ascii=False, indent=2)}")
