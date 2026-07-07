"""
cache_skill.py — 用「a-stock-data skill」的非官方源(东财/cninfo)增量更新 研报/公告 元数据入主库

与 cache_tushare.py 的分工：cache_tushare 只管官方 tushare 源；本脚本管 skill 非官方源
(tushare 拉不了公告，需单独权限)。两者互不干扰，数据按表名前缀 skill_ 标记来源。

数据集(--what)：
  reports  研报元数据  ← 东财 reportapi  → 表 skill_research_em
  anns     公告元数据  ← cninfo 巨潮      → 表 skill_anns_cninfo
  all      两者都更新

口径：仅元数据(不抓 PDF 全文)。默认 2020-01-01 起。
  --full 大回填：按股票循环、翻页取尽，每只按 [start,end] 窗口「先删后插」幂等。
  --update 增量(不带 --codes)：走快路径——不带 code「按日期抓全市场」翻页取尽，按窗口整段先删后插。
    成本随窗口页数走(几天增量分钟级)，不再随 5000+ 股票数走(旧法逐股扫全宇宙，几天也要小时级)。

限速：每次 HTTP 请求间隔 ≥ --sleep 秒(默认 1.2)，session 复用，失败指数退避重试，
      防东财/cninfo 封 IP。全量(5000+股×翻页)首跑耗时以小时计，之后 --days 增量分钟级。

用法(--full / --update 二选一，与 cache_tushare 一致)：
  日常增量(自动从库内最新日期续抓到最新交易日)：
    python first10/cache_skill.py --what all --update
  全量/分段回填(各段互不重抓、互不覆盖)：
    python first10/cache_skill.py --what all --full --start 20260101                 # 2026(end=最新)
    python first10/cache_skill.py --what all --full --start 20250101 --end 20251231  # 2025，不动2026
    python first10/cache_skill.py --what all --full --start 20200101 --end 20241231  # 更早，不动后面
  断点续跑(段内按股票，断了重跑跳过已抓完的)：加 --resume
    python first10/cache_skill.py --what all --full --start 20200101 --end 20241231 --resume
  小样验证：python first10/cache_skill.py --what all --full --codes 600519.SH,000001.SZ

依赖：requests；主库 stock_meta(取股票池) + daily(取最新交易日)。
注意：东财/cninfo 为非官方接口，字段/分页/orgId 规则可能变；首次大批量前建议先用 --codes 小样验证。
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

import duckdb as _duckdb
from db_loader import _ENV

DUCK_PATH = _ENV.get("LOCAL_DUCKDB_PATH", "stock_data_tushare.duckdb")
CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

EM_REPORT_URL = "https://reportapi.eastmoney.com/report/list"
EM_PDF_TPL = "https://pdf.dfcfw.com/pdf/H3_{info_code}_1.pdf"
EM_HEADERS = {
    "Referer": "https://data.eastmoney.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

CNINFO_QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_ORGMAP_URL = "http://www.cninfo.com.cn/new/data/szse_stock.json"
CNINFO_DETAIL_TPL = "https://www.cninfo.com.cn/new/disclosure/detail?annoId={anno_id}"
CNINFO_PDF_BASE = "https://static.cninfo.com.cn/"
CNINFO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Content-Type": "application/x-www-form-urlencoded",
    "Referer": "https://www.cninfo.com.cn/new/disclosure",
    "Origin": "https://www.cninfo.com.cn",
}

RATING_STD = [
    ("买入", 5), ("强烈推荐", 5), ("强推", 5),
    ("谨慎增持", 4), ("增持", 4), ("推荐", 4), ("优于大市", 4), ("跑赢行业", 4),
    ("中性", 3), ("持有", 3), ("观望", 3), ("同步大市", 3),
    ("减持", 2), ("跑输行业", 2), ("弱于大市", 2),
    ("卖出", 1), ("回避", 1),
]


class Throttle:
    """保证相邻 HTTP 请求间隔 ≥ min_gap 秒。"""

    def __init__(self, min_gap):
        self.min_gap = min_gap
        self._last = 0.0

    def wait(self):
        """阻塞到距上次请求满 min_gap。"""
        dt = time.time() - self._last
        if dt < self.min_gap:
            time.sleep(self.min_gap - dt)
        self._last = time.time()


def _session(headers):
    """构造带默认头、复用连接的 requests.Session。"""
    s = requests.Session()
    s.headers.update(headers)
    return s


def _req(session, throttle, method, url, retries=4, **kw):
    """限速 + 指数退避重试的请求；返回 response 或抛出最后一次异常。"""
    last = None
    for i in range(retries):
        throttle.wait()
        try:
            r = session.request(method, url, timeout=30, **kw)
            r.raise_for_status()
            return r
        except Exception as e:
            last = e
            time.sleep(min(2 ** i, 10))
    raise last


def _f(x):
    """转 float，失败/空返回 None。"""
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _norm_rating(name):
    """评级文本 → 有序数值(5强买…1卖)，无匹配返回 None。"""
    if not name:
        return None
    for kw, v in RATING_STD:
        if kw in name:
            return v
    return None


def _dash(s):
    """YYYYMMDD/YYYY-MM-DD → YYYY-MM-DD。"""
    return s if "-" in s else f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def _tables_for(what):
    """--what → 涉及的表名列表。"""
    t = []
    if what in ("reports", "all"):
        t.append("skill_research_em")
    if what in ("anns", "all"):
        t.append("skill_anns_cninfo")
    return t


def _update_start(con, what):
    """增量起点：取涉及表中 MAX(ann_date) 的较早者(确保慢的那张表也补齐)，无数据返回 None。"""
    mx = None
    for t in _tables_for(what):
        d = con.execute(f"SELECT strftime(MAX(ann_date), '%Y-%m-%d') FROM {t}").fetchone()[0]
        if d and (mx is None or d < mx):
            mx = d
    return mx


def _date_range(args, con):
    """解析抓取窗口 (start_dash, end_dash)。--update 自动续抓；--full 用 --start/--end。"""
    latest = con.execute("SELECT strftime(MAX(trade_date), '%Y-%m-%d') FROM daily").fetchone()[0]
    end = _dash(args.end) if args.end else latest
    if args.update:
        if args.days is not None:
            start = (datetime.strptime(end, "%Y-%m-%d") - timedelta(days=args.days)).strftime("%Y-%m-%d")
        else:
            mx = _update_start(con, args.what)
            start = ((datetime.strptime(mx, "%Y-%m-%d") - timedelta(days=3)).strftime("%Y-%m-%d")
                     if mx else _dash(args.start))
    else:
        start = _dash(args.start)
    return start, end


def _progress_path(what, start_dash, end_dash):
    """断点续跑进度文件路径(按 what+窗口 区分)。"""
    tag = f"{what}_{start_dash}_{end_dash}".replace("-", "")
    return os.path.join(CACHE_DIR, f"skill_resume_{tag}.txt")


def _universe(con, codes_arg):
    """返回 [(ts_code, code6)]；--codes 指定则只取这些。"""
    if codes_arg:
        ts = [c.strip() for c in codes_arg.split(",") if c.strip()]
        return [(t, t.split(".")[0]) for t in ts]
    rows = con.execute("SELECT ts_code FROM stock_meta ORDER BY ts_code").fetchall()
    return [(r[0], r[0].split(".")[0]) for r in rows]


def _ensure_tables(con):
    """建 skill_* 表(若不存在)。"""
    con.execute("""
        CREATE TABLE IF NOT EXISTS skill_research_em (
            ts_code VARCHAR, ann_date DATE, org VARCHAR, title VARCHAR,
            rating VARCHAR, rating_std INTEGER,
            eps_y0 DOUBLE, eps_y1 DOUBLE, eps_y2 DOUBLE,
            industry VARCHAR, info_code VARCHAR, pdf_url VARCHAR,
            src VARCHAR, fetched_at TIMESTAMP
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS skill_anns_cninfo (
            ts_code VARCHAR, ann_date DATE, title VARCHAR, ann_type VARCHAR,
            anno_id VARCHAR, pdf_url VARCHAR, src VARCHAR, fetched_at TIMESTAMP
        )
    """)


def _upsert(con, table, df, ts_code, start_dash, end_dash):
    """对单只股票按 [start,end] 窗口先删后插，保证幂等(只动该窗口，不碰窗口外数据)。"""
    con.execute(f"DELETE FROM {table} WHERE ts_code = ? AND ann_date >= ? AND ann_date <= ?",
                [ts_code, start_dash, end_dash])
    if len(df):
        con.register("_buf", df)
        con.execute(f"INSERT INTO {table} SELECT * FROM _buf")
        con.unregister("_buf")


def _upsert_window(con, table, df, start_dash, end_dash):
    """全市场快路径:按 [start,end] 窗口整段先删后插(跨全部 ts_code),保证幂等。"""
    con.execute(f"DELETE FROM {table} WHERE ann_date >= ? AND ann_date <= ?", [start_dash, end_dash])
    if len(df):
        con.register("_buf", df)
        con.execute(f"INSERT INTO {table} SELECT * FROM _buf")
        con.unregister("_buf")


def _code_map(con):
    """{6位代码: ts_code}，用于全市场抓取后把 stockCode/secCode 回填成带后缀的 ts_code。"""
    return {r[0].split(".")[0]: r[0] for r in con.execute("SELECT ts_code FROM stock_meta").fetchall()}


def _em_row(d, now):
    """东财研报单条 → 行 dict(ts_code 留空由调用方回填)；无发布日返回 None。"""
    pub = (d.get("publishDate") or "")[:10]
    if not pub:
        return None
    info = d.get("infoCode") or ""
    rating = d.get("emRatingName")
    return {
        "ts_code": None, "ann_date": pub,
        "org": d.get("orgSName"), "title": d.get("title"),
        "rating": rating, "rating_std": _norm_rating(rating),
        "eps_y0": _f(d.get("predictThisYearEps")),
        "eps_y1": _f(d.get("predictNextYearEps")),
        "eps_y2": _f(d.get("predictNextTwoYearEps")),
        "industry": d.get("indvInduName"), "info_code": info,
        "pdf_url": EM_PDF_TPL.format(info_code=info) if info else None,
        "src": "eastmoney", "fetched_at": now,
    }


def fetch_reports(session, throttle, code6, start_dash, end_dash):
    """拉单只股票 [start,end] 的东财研报列表，翻页取尽，返回行 dict 列表。"""
    out = []
    page = 1
    now = datetime.now()
    while True:
        params = {
            "code": code6, "pageNo": page, "pageSize": 100,
            "industryCode": "*", "rating": "*", "ratingChange": "*",
            "beginTime": start_dash, "endTime": end_dash, "qType": "0",
        }
        r = _req(session, throttle, "GET", EM_REPORT_URL, params=params)
        j = r.json()
        data = j.get("data") or []
        for d in data:
            row = _em_row(d, now)
            if row:
                out.append(row)
        total = int(j.get("TotalPage") or 1)
        if page >= total or not data:
            break
        page += 1
    return out


def fetch_reports_all(session, throttle, start_dash, end_dash, code_map):
    """按日期抓全市场东财研报(不带 code)，翻页取尽，用 code_map 回填 ts_code(不在池内的跳过)。"""
    out = []
    page = 1
    now = datetime.now()
    while True:
        params = {
            "pageNo": page, "pageSize": 100,
            "industryCode": "*", "rating": "*", "ratingChange": "*",
            "beginTime": start_dash, "endTime": end_dash, "qType": "0",
        }
        r = _req(session, throttle, "GET", EM_REPORT_URL, params=params)
        j = r.json()
        data = j.get("data") or []
        for d in data:
            ts = code_map.get(d.get("stockCode"))
            if not ts:
                continue
            row = _em_row(d, now)
            if row:
                row["ts_code"] = ts
                out.append(row)
        total = int(j.get("TotalPage") or 1)
        if page >= total or not data:
            break
        page += 1
    return out


def _load_orgmap(session, throttle):
    """拉 cninfo code→orgId 映射(模块级一次)，失败返回空表(回退 gssx0{code})。"""
    try:
        r = _req(session, throttle, "GET", CNINFO_ORGMAP_URL)
        j = r.json()
        lst = j if isinstance(j, list) else j.get("stockList", [])
        return {it["code"]: it["orgId"] for it in lst if it.get("code") and it.get("orgId")}
    except Exception as e:
        print(f"  [warn] cninfo orgId 映射拉取失败({e})，回退 gssx0 格式", flush=True)
        return {}


def _cn_row(a, now):
    """cninfo 公告单条 → 行 dict(ts_code 留空由调用方回填)；无公告时间返回 None。"""
    if not a:
        return None
    ts = a.get("announcementTime")
    if ts is None:
        return None
    d = datetime.fromtimestamp(int(ts) / 1000).strftime("%Y-%m-%d")
    adj = a.get("adjunctUrl")
    return {
        "ts_code": None, "ann_date": d,
        "title": a.get("announcementTitle"),
        "ann_type": a.get("announcementType"),  # WHY: announcementTypeName 官方恒为 null，真分类在 announcementType(编码串)
        "anno_id": str(a.get("announcementId") or ""),
        "pdf_url": (CNINFO_PDF_BASE + adj) if adj else None,
        "src": "cninfo", "fetched_at": now,
    }


def fetch_anns(session, throttle, code6, orgid, start_dash, end_dash):
    """拉单只股票 [start,end] 的 cninfo 公告列表，翻页取尽，返回行 dict 列表。"""
    out = []
    page = 1
    now = datetime.now()
    se = f"{start_dash}~{end_dash}"
    while True:
        data = {
            "stock": f"{code6},{orgid}", "tabName": "fulltext",
            "pageSize": 30, "pageNum": page, "column": "szse",
            "category": "", "plate": "", "seDate": se,
            "searchkey": "", "secid": "", "sortName": "", "sortType": "",
            "isHLtitle": "true",
        }
        r = _req(session, throttle, "POST", CNINFO_QUERY_URL, data=data)
        j = r.json()
        anns = j.get("announcements") or []
        for a in anns:
            row = _cn_row(a, now)
            if row:
                out.append(row)
        if not j.get("hasMore") or not anns:
            break
        page += 1
    return out


def fetch_anns_all(session, throttle, start_dash, end_dash, code_map):
    """按日期抓全市场 cninfo 公告(不带 stock，column=szse 实测含沪深全市场)，翻页取尽，回填 ts_code。"""
    out = []
    page = 1
    now = datetime.now()
    se = f"{start_dash}~{end_dash}"
    while True:
        data = {
            "stock": "", "tabName": "fulltext",
            "pageSize": 30, "pageNum": page, "column": "szse",
            "category": "", "plate": "", "seDate": se,
            "searchkey": "", "secid": "", "sortName": "", "sortType": "",
            "isHLtitle": "true",
        }
        r = _req(session, throttle, "POST", CNINFO_QUERY_URL, data=data)
        j = r.json()
        anns = j.get("announcements") or []
        for a in anns:
            ts = code_map.get(str((a or {}).get("secCode") or ""))
            if not ts:
                continue
            row = _cn_row(a, now)
            if row:
                row["ts_code"] = ts
                out.append(row)
        if not j.get("hasMore") or not anns:
            break
        page += 1
    return out


REPORT_COLS = ["ts_code", "ann_date", "org", "title", "rating", "rating_std",
               "eps_y0", "eps_y1", "eps_y2", "industry", "info_code", "pdf_url",
               "src", "fetched_at"]
ANN_COLS = ["ts_code", "ann_date", "title", "ann_type", "anno_id", "pdf_url",
            "src", "fetched_at"]


def run_update_fast(args, con, start_dash, end_dash):
    """--update 快路径:按日期抓全市场,一次翻页取尽,按窗口整段先删后插(成本 O(窗口页数),不随股票数增长)。"""
    do_reports = args.what in ("reports", "all")
    do_anns = args.what in ("anns", "all")
    code_map = _code_map(con)
    print(f"=== cache_skill：{args.what}/update(快路径·按日期抓全市场) | 窗口 {start_dash}~{end_dash} "
          f"| 池内 {len(code_map)} 只 | 限速 {args.sleep}s ===", flush=True)
    n_rep = n_ann = 0
    if do_reports:
        rows = fetch_reports_all(_session(EM_HEADERS), Throttle(args.sleep), start_dash, end_dash, code_map)
        df = pd.DataFrame(rows, columns=REPORT_COLS)
        _upsert_window(con, "skill_research_em", df, start_dash, end_dash)
        n_rep = len(df)
        print(f"  研报:全市场 {n_rep} 行入库", flush=True)
    if do_anns:
        rows = fetch_anns_all(_session(CNINFO_HEADERS), Throttle(args.sleep), start_dash, end_dash, code_map)
        df = pd.DataFrame(rows, columns=ANN_COLS)
        _upsert_window(con, "skill_anns_cninfo", df, start_dash, end_dash)
        n_ann = len(df)
        print(f"  公告:全市场 {n_ann} 行入库", flush=True)
    print(f"\n完成(快路径)：研报 {n_rep} 行，公告 {n_ann} 行。", flush=True)


def run(args):
    """主流程：建表 → 按股票循环抓取 → 幂等入库 → 打印进度/失败。--update(不带 --codes)走全市场快路径。"""
    con = _duckdb.connect(DUCK_PATH)
    _ensure_tables(con)
    start_dash, end_dash = _date_range(args, con)
    if args.update and not args.codes:
        run_update_fast(args, con, start_dash, end_dash)
        con.close()
        return
    universe = _universe(con, args.codes)
    mode = "update" if args.update else "full"
    print(f"=== cache_skill：{args.what}/{mode} | 窗口 {start_dash}~{end_dash} | 股票 {len(universe)} 只 "
          f"| 限速 {args.sleep}s ===", flush=True)

    prog_path = _progress_path(args.what, start_dash, end_dash)
    done = set()
    if args.resume and os.path.exists(prog_path):
        with open(prog_path, encoding="utf-8-sig") as f:
            done = {ln.strip() for ln in f if ln.strip()}
        print(f"  [resume] 本窗口已完成 {len(done)} 只，将跳过", flush=True)
    prog_f = open(prog_path, "a", encoding="utf-8") if args.resume else None

    do_reports = args.what in ("reports", "all")
    do_anns = args.what in ("anns", "all")

    em_sess = _session(EM_HEADERS) if do_reports else None
    cn_sess = _session(CNINFO_HEADERS) if do_anns else None
    em_thr = Throttle(args.sleep)
    cn_thr = Throttle(args.sleep)
    orgmap = _load_orgmap(cn_sess, cn_thr) if do_anns else {}

    fail = []
    n_rep = n_ann = n_skip = 0
    for i, (ts_code, code6) in enumerate(universe, 1):
        if args.resume and ts_code in done:
            n_skip += 1
            continue
        nf0 = len(fail)
        if do_reports:
            try:
                rows = fetch_reports(em_sess, em_thr, code6, start_dash, end_dash)
                for r in rows:
                    r["ts_code"] = ts_code
                df = pd.DataFrame(rows, columns=REPORT_COLS)
                _upsert(con, "skill_research_em", df, ts_code, start_dash, end_dash)
                n_rep += len(df)
            except Exception as e:
                fail.append(("reports", ts_code, str(e)))
        if do_anns:
            try:
                orgid = orgmap.get(code6, f"gssx0{code6}")
                rows = fetch_anns(cn_sess, cn_thr, code6, orgid, start_dash, end_dash)
                for r in rows:
                    r["ts_code"] = ts_code
                df = pd.DataFrame(rows, columns=ANN_COLS)
                _upsert(con, "skill_anns_cninfo", df, ts_code, start_dash, end_dash)
                n_ann += len(df)
            except Exception as e:
                fail.append(("anns", ts_code, str(e)))
        if prog_f is not None and len(fail) == nf0:
            prog_f.write(ts_code + "\n")
            prog_f.flush()
        if i % 50 == 0 or i == len(universe):
            print(f"  [{i}/{len(universe)}] {ts_code}  研报累计 {n_rep}  公告累计 {n_ann}  "
                  f"跳过 {n_skip}  失败 {len(fail)}", flush=True)

    if prog_f is not None:
        prog_f.close()
    con.close()
    print(f"\n完成：研报 {n_rep} 行，公告 {n_ann} 行，跳过 {n_skip} 只，失败 {len(fail)} 次。")
    if args.resume and not fail and os.path.exists(prog_path):
        os.remove(prog_path)
        print("  [resume] 本窗口全部完成，已清除进度文件。")
    if fail:
        print("失败明细(前20)：")
        for w, ts, e in fail[:20]:
            print(f"  {w} {ts}: {e}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--what", choices=["reports", "anns", "all"], default="all")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--full", action="store_true", help="全量/分段回填(用 --start/--end 指定窗口)")
    group.add_argument("--update", action="store_true", help="增量(自动从库内最新日期续抓到最新交易日)")
    ap.add_argument("--start", default="20200101", help="--full 起始日 YYYYMMDD(默认 2020-01-01)")
    ap.add_argument("--end", default=None, help="结束日 YYYYMMDD(默认=最新交易日；配 --start 只抓某段不动其它年份)")
    ap.add_argument("--days", type=int, default=None, help="配 --update：强制抓最近 N 天")
    ap.add_argument("--codes", default=None, help="只抓指定 ts_code(逗号分隔，用于小样验证)")
    ap.add_argument("--sleep", type=float, default=1.2, help="每次 HTTP 最小间隔秒(默认 1.2)")
    ap.add_argument("--resume", action="store_true", help="段内断点续跑，跳过本窗口已抓完的股票")
    args = ap.parse_args()
    run(args)
