"""peer_finder.py — 个股「同行业可比公司 + 行业/竞争定性」发现与缓存(供基本面 LLM 分析)。

同行身份与行业定性都很稳、几乎一次确定长期不变,值得一次性抽好存库、以后直取。
- 同行(peers):以申万三级同业为骨架(可靠、完整、可实时算 PE/PB),再并入研报中额外提到的可比公司。
- 行业空间 / 竞争地位:读该股研报(skill_research_em 的 pdf_url,优先深度报告)全文,DeepSeek 抽取(temp 0)。
  研报 PDF 主机(东财 pdf.dfcfw.com)有反爬,urllib 直连拿不到 → 优先用 browser-harness(真 Chrome 过 JS 挑战、
  页内 fetch 拿真 PDF),失败退 urllib,再失败仅用申万同业。
结果写独立小库 peer_cache.duckdb(不写 7GB 主库,避免并发写锁),按 ts_code + 版本缓存,命中直取。

用法:python swing/peer_finder.py 601869 [--force]
依赖:duckdb / pymupdf(fitz)/ openai(DeepSeek,复用 ta_analyze key)/ 可选 browser-harness(过反爬)。
"""
import os
import sys
import json
import shutil
import tempfile
import subprocess
import urllib.request
from datetime import datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import duckdb
import cache_tushare as ct

PEER_DB = os.path.join(_ROOT, "peer_cache.duckdb")
PEER_VER = "2026-07-15b"
_DEEP_KWS = ("深度", "首次覆盖", "投资价值", "深度报告")
_MAX_TXT = 16000


def _norm(code: str) -> str:
    """把 600xxx / 600xxx.SH 规整成带交易所后缀的 ts_code。"""
    code = str(code).strip().upper()
    if "." in code:
        return code
    if code.startswith(("60", "68", "51", "58", "11", "5")):
        return code + ".SH"
    if code.startswith(("8", "4", "92")):
        return code + ".BJ"
    return code + ".SZ"


def _ord(d) -> int:
    """把日期(DATE 或字符串)转成可比较整数 YYYYMMDD,用于排序。"""
    s = str(d).replace("-", "")
    return int(s) if s.isdigit() else 0


def _cache_conn():
    """打开(必要时建)独立同行缓存库,返回可读写连接。"""
    con = duckdb.connect(PEER_DB)
    con.execute("""CREATE TABLE IF NOT EXISTS stock_peers (
        ts_code VARCHAR PRIMARY KEY, name VARCHAR, peers VARCHAR,
        industry_space VARCHAR, competitive VARCHAR, source VARCHAR,
        report_date VARCHAR, model VARCHAR, ver VARCHAR, fetched_at TIMESTAMP)""")
    return con


def _read_cache(ts: str):
    """读缓存;版本不符或不存在返回 None。"""
    con = _cache_conn()
    try:
        r = con.execute("SELECT peers,industry_space,competitive,source,report_date,ver FROM stock_peers WHERE ts_code=?", [ts]).fetchone()
    finally:
        con.close()
    if not r or r[5] != PEER_VER:
        return None
    return {"peers": json.loads(r[0] or "[]"), "industry_space": r[1], "competitive": r[2], "source": r[3], "report_date": r[4], "cached": True}


def _sw_peers(mcon, ts: str, limit: int = 10):
    """申万三级同业(按流通市值排序,不含自身),返回 [{code,name}]。"""
    sw = mcon.execute("SELECT l3_name FROM sw_member WHERE ts_code=? ORDER BY is_new DESC LIMIT 1", [ts]).fetchone()
    if not sw or not sw[0]:
        return []
    mx = mcon.execute("SELECT max(trade_date) FROM daily_basic").fetchone()[0]
    rows = mcon.execute("""SELECT db.ts_code, sm.name FROM daily_basic db
        JOIN sw_member sw ON sw.ts_code=db.ts_code JOIN stock_meta sm ON sm.ts_code=db.ts_code
        WHERE sw.l3_name=? AND db.trade_date=? AND db.ts_code<>? AND db.circ_mv IS NOT NULL
        ORDER BY db.circ_mv DESC LIMIT ?""", [sw[0], mx, ts, limit]).fetchall()
    return [{"code": c, "name": n} for c, n in rows]


def _download_pdf(url: str) -> bytes:
    """下载研报 PDF:优先 browser-harness(过东财反爬),失败退 urllib。返回 PDF 字节(失败空)。"""
    data = _via_browser(url)
    if data and data[:4] == b"%PDF":
        return data
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"})
        d = urllib.request.urlopen(req, timeout=30).read()
        return d if d[:4] == b"%PDF" else b""
    except Exception:
        return b""


def _via_browser(url: str) -> bytes:
    """用 browser-harness(真 Chrome)打开 PDF、页内 fetch 拿真字节(绕反爬);无 harness/失败返回空。"""
    exe = shutil.which("browser-harness")
    if not exe:
        return b""
    out = os.path.join(tempfile.gettempdir(), f"peer_pdf_{abs(hash(url))}.pdf")
    js = ("return fetch(location.href,{credentials:'include'})"
          ".then(r=>r.arrayBuffer())"
          ".then(b=>{let u=new Uint8Array(b),s='',i=0;for(;i<u.length;i++)s+=String.fromCharCode(u[i]);return btoa(s)})"
          ".catch(e=>'ERR')")
    driver = "\n".join([
        "import time, base64",
        f"new_tab({url!r}); wait_for_load(); time.sleep(3)",
        f"b64 = js({js!r})",
        f"open({out!r},'wb').write(base64.b64decode(b64)) if (isinstance(b64,str) and b64!='ERR') else None",
        "print('DONE')",
    ])
    try:
        subprocess.run([exe], input=driver, text=True, capture_output=True, timeout=120)
        if os.path.exists(out) and os.path.getsize(out) > 10000:
            data = open(out, "rb").read()
            os.remove(out)
            return data
    except Exception:
        pass
    return b""


def _pdf_text(data: bytes) -> str:
    """从 PDF 字节抽正文(截断);失败空串。"""
    try:
        import fitz
        doc = fitz.open(stream=data, filetype="pdf")
        return ("\n".join(doc[i].get_text() for i in range(doc.page_count)))[:_MAX_TXT]
    except Exception:
        return ""


def _extract_llm(name: str, report_text: str):
    """DeepSeek 从研报正文抽 行业空间 + 竞争地位 + 额外可比公司(temp 0,只输出 JSON)。"""
    import ta_analyze
    ta_analyze._load_keys()
    prompt = (f"下面是关于 A 股公司「{name}」的券商研报正文。只抽客观信息,输出 JSON:\n"
              f'{{"industry_space":"行业空间/市场规模一句话或空","competitive":"该公司在行业中的竞争地位一句话或空",'
              f'"peers":[{{"name":"研报明确提到的可比公司/竞争对手名","code":"6位A股代码或空"}}]}}\n'
              f"要求:peers 只填研报**明确点名**的同行(A股为主,最多8家),没有就空数组;不确定代码留空;不要编造。\n\n研报正文:\n{report_text}")
    try:
        txt = ta_analyze._cli().chat.completions.create(
            model="deepseek-v4-flash", temperature=0, response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}]).choices[0].message.content
        d = json.loads(txt)
        return d.get("industry_space") or "", d.get("competitive") or "", d.get("peers") or []
    except Exception:
        return "", "", []


def _resolve_codes(mcon, peers):
    """把研报同行名称解析成 ts_code(精确名优先,再模糊),解析不到丢弃,去重。"""
    out, seen = [], set()
    for p in peers:
        nm = (p.get("name") or "").strip()
        code = (p.get("code") or "").strip()
        ts = None
        if code:
            ts = _norm(code)
            if not mcon.execute("SELECT 1 FROM stock_meta WHERE ts_code=?", [ts]).fetchone():
                ts = None
        if not ts and nm:
            r = (mcon.execute("SELECT ts_code FROM stock_meta WHERE name=? LIMIT 1", [nm]).fetchone()
                 or mcon.execute("SELECT ts_code FROM stock_meta WHERE name LIKE ? LIMIT 1", [f"%{nm}%"]).fetchone())
            ts = r[0] if r else None
        if ts and ts not in seen:
            seen.add(ts); out.append({"code": ts, "name": nm or ts})
    return out


def ensure_peers(code: str, force: bool = False) -> dict:
    """返回该股同行+行业定性(有缓存直取;否则申万骨架 + 研报补定性/额外同行,落库)。"""
    ts = _norm(code)
    if not force:
        c = _read_cache(ts)
        if c is not None:
            return c
    mcon = duckdb.connect(ct.DUCKDB_PATH, read_only=True)
    try:
        nm = (mcon.execute("SELECT name FROM stock_meta WHERE ts_code=?", [ts]).fetchone() or [ts])[0]
        sw = _sw_peers(mcon, ts)
        reps = mcon.execute("""SELECT ann_date,title,pdf_url FROM skill_research_em
            WHERE ts_code=? AND pdf_url IS NOT NULL ORDER BY ann_date DESC LIMIT 12""", [ts]).fetchall()
        ranked = sorted(reps, key=lambda r: (0 if any(k in (r[1] or "") for k in _DEEP_KWS) else 1, -_ord(r[0])))
        ispace, comp, rp_peers, src, rdate = "", "", [], "", ""
        for ann, _title, url in ranked[:2]:
            text = _pdf_text(_download_pdf(url))
            if len(text) < 300:
                continue
            ispace, comp, raw = _extract_llm(nm, text)
            rp_peers = _resolve_codes(mcon, raw)
            src, rdate = url, str(ann)
            break
        seen = {p["code"] for p in sw}
        peers = sw + [p for p in rp_peers if p["code"] not in seen and p["code"] != ts]
        source = ("研报+申万" if src else "申万三级同业")
    finally:
        mcon.close()
    rec = {"peers": peers, "industry_space": ispace, "competitive": comp, "source": source, "report_date": rdate, "cached": False}
    con = _cache_conn()
    try:
        con.execute("INSERT OR REPLACE INTO stock_peers VALUES (?,?,?,?,?,?,?,?,?,?)",
                    [ts, nm, json.dumps(peers, ensure_ascii=False), ispace, comp, source, rdate,
                     "deepseek-v4-flash", PEER_VER, datetime.now()])
    finally:
        con.close()
    return rec


def peer_snapshot(code: str, force: bool = False) -> dict:
    """同行 + 实时相对估值 + 行业定性:各同行最新 PE/PB、同业中位、本股对比;返回 {text, ...}。"""
    ts = _norm(code)
    info = ensure_peers(ts, force=force)
    peers = info["peers"]
    if not peers:
        return {**info, "text": "同业:未找到可比公司", "peer_pe_med": None, "peer_pb_med": None}
    mcon = duckdb.connect(ct.DUCKDB_PATH, read_only=True)
    try:
        mx = mcon.execute("SELECT max(trade_date) FROM daily_basic").fetchone()[0]
        codes = [p["code"] for p in peers] + [ts]
        ph = ",".join(["?"] * len(codes))
        vals = {r[0]: (r[1], r[2]) for r in mcon.execute(
            f"SELECT ts_code,pe_ttm,pb FROM daily_basic WHERE trade_date=? AND ts_code IN ({ph})", [mx, *codes]).fetchall()}
    finally:
        mcon.close()
    import statistics as st
    pes = [vals[p["code"]][0] for p in peers if vals.get(p["code"]) and vals[p["code"]][0] and vals[p["code"]][0] > 0]
    pbs = [vals[p["code"]][1] for p in peers if vals.get(p["code"]) and vals[p["code"]][1] and vals[p["code"]][1] > 0]
    pe_med = round(st.median(pes), 1) if pes else None
    pb_med = round(st.median(pbs), 2) if pbs else None
    self_pe, self_pb = vals.get(ts, (None, None))
    lines = []
    for p in peers:
        v = vals.get(p["code"])
        pe = round(v[0], 1) if v and v[0] else "—"
        pb = round(v[1], 2) if v and v[1] else "—"
        lines.append(f"- {p['name']}({p['code']}) PE {pe} / PB {pb}")
    cmp = "高于" if (self_pe and pe_med and self_pe > pe_med) else "低于" if (self_pe and pe_med) else "—"
    head = (f"同业可比({info['source']}):共{len(peers)}家;同业PE中位 {pe_med} / PB中位 {pb_med};"
            f"本股 PE {round(self_pe,1) if self_pe else '—'} / PB {round(self_pb,2) if self_pb else '—'}({cmp}同业PE中位)")
    extra = ((f"\n行业空间:{info['industry_space']}" if info.get("industry_space") else "") +
             (f"\n竞争地位:{info['competitive']}" if info.get("competitive") else ""))
    return {**info, "peer_pe_med": pe_med, "peer_pb_med": pb_med, "self_pe": self_pe, "self_pb": self_pb,
            "text": head + "\n" + "\n".join(lines) + extra}


def main():
    """CLI:抽/看某股同行。python swing/peer_finder.py 601869 [--force]"""
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) < 2:
        print("用法: python swing/peer_finder.py <代码> [--force]"); return
    snap = peer_snapshot(sys.argv[1], force="--force" in sys.argv)
    print(f"来源: {snap['source']}  报告日: {snap.get('report_date') or '—'}  缓存: {snap['cached']}")
    print(snap["text"])


if __name__ == "__main__":
    main()
