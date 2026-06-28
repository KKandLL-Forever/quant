"""astock_news.py — 个股新闻/研报抓取(东财直连HTTP),逻辑取自 a-stock-data(simonlin1212)。

为情绪/新闻面提供数据:对单只A股拉取个股新闻 + 研报(带评级/日期,可按日期PIT截断)。
内置东财限流(间隔≥1s)防封。巨潮公告(需orgid映射)暂未含,后续可补。

环境：.venv312。用法见 __main__;或 import 后调 stock_news(code) / stock_reports(code, begin, end)。
依赖：requests。数据源:东财 search-api(新闻) + reportapi(研报)。
"""

import json
import random
import re
import time
from datetime import datetime

import requests

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
REPORT_API = "https://reportapi.eastmoney.com/report/list"
_SESSION = requests.Session()
_MIN_INTERVAL = 1.0
_last = [0.0]


def _get(url, params=None, headers=None, timeout=20):
    """东财统一请求:节流+复用session+默认UA。"""
    wait = _MIN_INTERVAL - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait + random.uniform(0.1, 0.4))
    try:
        return _SESSION.get(url, params=params, headers=headers or {"User-Agent": UA}, timeout=timeout)
    finally:
        _last[0] = time.time()


def stock_reports(code, begin="2020-01-01", end="2030-01-01", max_pages=3):
    """拉个股研报(机构/评级/EPS/日期),begin~end 可按决策日做 PIT 截断。"""
    out = []
    for page in range(1, max_pages + 1):
        params = {"industryCode": "*", "pageSize": "50", "industry": "*", "rating": "*",
                  "ratingChange": "*", "beginTime": begin, "endTime": end, "pageNo": str(page),
                  "qType": "0", "code": code}
        r = _get(REPORT_API, params=params, headers={"User-Agent": UA, "Referer": "https://data.eastmoney.com/"})
        d = r.json(); rows = d.get("data") or []
        if not rows:
            break
        for a in rows:
            out.append({"date": a.get("publishDate", "")[:10], "org": a.get("orgSName", ""),
                        "title": a.get("title", ""), "rating": a.get("sRatingName", ""),
                        "eps": a.get("predictThisYearEps", "")})
        if page >= (d.get("TotalPage", 1) or 1):
            break
    return out


def stock_news(code, page_size=20):
    """拉个股新闻(东财 JSONP),返回 [{title,content,time,source,url}]。"""
    inner = json.dumps({"uid": "", "keyword": code, "type": ["cmsArticleWebOld"], "client": "web",
                        "clientType": "web", "clientVersion": "curr",
                        "param": {"cmsArticleWebOld": {"searchScope": "default", "sort": "default",
                                  "pageIndex": 1, "pageSize": page_size, "preTag": "", "postTag": ""}}},
                       separators=(',', ':'))
    r = _get("https://search-api-web.eastmoney.com/search/jsonp",
             params={"cb": "jQuery_news", "param": inner},
             headers={"User-Agent": UA, "Referer": "https://so.eastmoney.com/"})
    text = r.text
    d = json.loads(text[text.index("(") + 1:text.rindex(")")])
    out = []
    for a in d.get("result", {}).get("cmsArticleWebOld", []) or []:
        out.append({"title": re.sub(r"<[^>]+>", "", a.get("title", "")),
                    "content": re.sub(r"<[^>]+>", "", a.get("content", ""))[:200],
                    "time": a.get("date", ""), "source": a.get("mediaName", ""), "url": a.get("url", "")})
    return out


def _cninfo_ts_to_date(ts):
    """巨潮 announcementTime 是 Unix 毫秒,转日期字符串。"""
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
    return str(ts)[:10] if ts else ""


_CNINFO_ORGID = {}


def _cninfo_orgid(code):
    """查巨潮真实 orgId(动态映射表,查不到回退老格式)。"""
    global _CNINFO_ORGID
    if not _CNINFO_ORGID:
        try:
            r = requests.get("http://www.cninfo.com.cn/new/data/szse_stock.json",
                             headers={"User-Agent": UA}, timeout=15)
            _CNINFO_ORGID = {s["code"]: s["orgId"] for s in r.json().get("stockList", [])}
        except Exception:
            pass
    org = _CNINFO_ORGID.get(code)
    if org:
        return org
    if code.startswith("6"):
        return f"gssh0{code}"
    if code.startswith(("8", "4")):
        return f"gsbj0{code}"
    return f"gssz0{code}"


def stock_announcements(code, begin=None, end=None, page_size=30):
    """巨潮公告(官方法定披露),end 截断做 PIT。返回 [{date,type,title,url}](按日期降序)。"""
    code = str(code).split(".")[0]
    se = f"{begin or '2000-01-01'}~{end}" if end else ""
    payload = {"stock": f"{code},{_cninfo_orgid(code)}", "tabName": "fulltext",
               "pageSize": str(page_size), "pageNum": "1", "column": "", "category": "",
               "plate": "", "seDate": se, "searchkey": "", "secid": "", "sortName": "time",
               "sortType": "desc", "isHLtitle": "true"}
    headers = {"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded",
               "Referer": "https://www.cninfo.com.cn/new/disclosure",
               "Origin": "https://www.cninfo.com.cn"}
    r = requests.post("https://www.cninfo.com.cn/new/hisAnnouncement/query", data=payload, headers=headers, timeout=20)
    out = []
    for it in r.json().get("announcements") or []:
        out.append({"date": _cninfo_ts_to_date(it.get("announcementTime")),
                    "type": it.get("announcementTypeName", ""),
                    "title": re.sub(r"<[^>]+>", "", it.get("announcementTitle", "")),
                    "url": f"https://www.cninfo.com.cn/new/disclosure/detail?annoId={it.get('announcementId','')}"})
    return out


if __name__ == "__main__":
    import sys
    c = sys.argv[1] if len(sys.argv) > 1 else "300903"
    print(f"=== {c} 公告(≤2026-06-26) ===")
    for a in stock_announcements(c, end="2026-06-26")[:8]:
        print(f"  {a['date']} | {a['type']} | {a['title'][:40]}")
    print(f"=== {c} 研报 ===")
    for r in stock_reports(c, begin="2026-01-01", end="2026-06-30")[:5]:
        print(f"  {r['date']} | {r['org']} | {r['rating']} | {r['title'][:40]}")
    print(f"=== {c} 新闻 ===")
    for n in stock_news(c)[:5]:
        print(f"  {n['time']} | {n['source']} | {n['title'][:40]}")
