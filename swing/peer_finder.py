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
PEER_VER = "2026-07-25a"   # 提版=作废旧缓存:旧版 PDF 正文截断致深度报告的可比公司估值表 100% 抽不到
_DEEP_KWS = ("深度", "首次覆盖", "投资价值", "深度报告")
_MAX_TXT = 20000


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
    """打开(必要时建)独立同行缓存库,返回可读写连接;val_table 为研报可比公司前瞻估值表。"""
    con = duckdb.connect(PEER_DB)
    con.execute("""CREATE TABLE IF NOT EXISTS stock_peers (
        ts_code VARCHAR PRIMARY KEY, name VARCHAR, peers VARCHAR,
        industry_space VARCHAR, competitive VARCHAR, val_table VARCHAR, source VARCHAR,
        report_date VARCHAR, model VARCHAR, ver VARCHAR, fetched_at TIMESTAMP)""")
    con.execute("ALTER TABLE stock_peers ADD COLUMN IF NOT EXISTS val_table VARCHAR")
    return con


def _read_cache(ts: str):
    """读缓存;版本不符或不存在返回 None。"""
    con = _cache_conn()
    try:
        r = con.execute("SELECT peers,industry_space,competitive,source,report_date,ver,val_table FROM stock_peers WHERE ts_code=?", [ts]).fetchone()
    finally:
        con.close()
    if not r or r[5] != PEER_VER:
        return None
    return {"peers": json.loads(r[0] or "[]"), "industry_space": r[1], "competitive": r[2], "source": r[3],
            "report_date": r[4], "val_table": json.loads(r[6] or "null"), "cached": True}


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
        subprocess.run([exe], input=driver, text=True, encoding="utf-8", errors="replace",
                       capture_output=True, timeout=120)
        if os.path.exists(out) and os.path.getsize(out) > 10000:
            data = open(out, "rb").read()
            os.remove(out)
            return data
    except Exception:
        pass
    return b""


def _pdf_text(data: bytes) -> str:
    """从 PDF 字节抽全文;失败空串。"""
    try:
        import fitz
        doc = fitz.open(stream=data, filetype="pdf")
        return "\n".join(doc[i].get_text() for i in range(doc.page_count))
    except Exception:
        return ""


def _extract_input(txt: str) -> str:
    """给抽取用的正文:开头(公司/行业/竞争)+「可比公司估值表」那一段,再裁到 LLM 输入预算。

    表几乎总在深度报告末尾几页,故须在**全文**里定位、且取最后一次出现。
    # WHY: 曾在 _pdf_text 里先截断到 20000 再找表,而 49 页研报的表在第 58k 字符,
    # 导致所有深度报告的可比公司估值表 100% 抽不到(只抽到前段顺带提及的"可比公司")。
    """
    base = txt[:9000]
    for kw in ("可比公司估值", "估值比较", "可比公司", "可比"):
        i = txt.rfind(kw)
        if i > 9000:
            base += "\n……\n" + txt[max(0, i - 200):i + 1800]
            break
    return base[:_MAX_TXT]


def _extract_llm(name: str, report_text: str):
    """DeepSeek 从研报正文抽 行业空间 + 竞争地位 + 可比公司 + 可比公司前瞻估值表(temp 0,只输出 JSON)。"""
    import ta_analyze
    ta_analyze._load_keys()
    schema = ('{"industry_space":"行业空间/市场规模一句话或空","competitive":"竞争地位一句话或空",'
              '"peers":[{"name":"可比公司/竞争对手名","code":"6位A股代码或空"}],'
              '"val_table":{"caliber":"口径如『前瞻P/E』或空","year1":"如2026E","year2":"如2027E",'
              '"rows":[{"name":"公司名","code":"6位A股代码或空(境外留空)","pe1":0,"pe2":0}]},'
              '"val_summary":{"caliber":"如动态PE","year1":"如2026E","year2":"如2027E",'
              '"peer_avg1":0,"peer_avg2":0,"self_pe1":0,"self_pe2":0,'
              '"peer_names":["可比公司名"]}}')
    rules = ("要求:①peers 只填研报明确点名的同行(最多10家);"
             "②val_table 只在**能逐行读到每家公司的前瞻PE数字**时填(pe1=近年如2026E、pe2=远年如2027E,"
             "含境外龙头如美光/三星/海力士);读不到逐行数字就整个设为 null;"
             "③val_summary 与 val_table **相互独立、各自判断**:只要正文写了『可比公司X年动态PE均值/中位为A』"
             "或『本股X年对应PE为A』就填。**估值表常以图片嵌入PDF、文字层读不到,但正文往往给了这个结论,"
             "此时 val_summary 是唯一可得的一致口径,务必填**;正文确实没有才设 null;"
             "④不确定/读不到就留空或 null,不要编造。")
    prompt = (f"下面是关于公司「{name}」的券商研报正文(含可能存在的『可比公司估值表』)。只抽客观信息,输出 JSON:\n"
              + schema + "\n" + rules + "\n\n研报正文:\n" + report_text)
    try:
        txt = ta_analyze._cli().chat.completions.create(
            model="deepseek-v4-flash", temperature=0, response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}]).choices[0].message.content
        d = json.loads(txt)
        return (d.get("industry_space") or "", d.get("competitive") or "", d.get("peers") or [],
                _merge_val(d.get("val_table"), d.get("val_summary")))
    except Exception:
        return "", "", [], None


def _merge_val(vt, vs):
    """把逐行估值表与正文汇总结论合成一份;两者都拿不到数字则返回 None(避免把 None 塞进 prompt)。"""
    rows = vt.get("rows") if isinstance(vt, dict) else None
    has_row_pe = bool(rows) and any(r.get("pe1") or r.get("pe2") for r in rows)
    vs = vs if isinstance(vs, dict) else {}
    has_sum = bool(vs.get("peer_avg1") or vs.get("peer_avg2") or vs.get("self_pe1") or vs.get("self_pe2"))
    if not (has_row_pe or has_sum):
        return None
    base = dict(vt) if isinstance(vt, dict) else {}
    if not has_row_pe:
        base["rows"] = [{"name": n} for n in (vs.get("peer_names") or [])] or (rows or [])
    for k in ("caliber", "year1", "year2"):
        base[k] = base.get(k) or vs.get(k)
    for k in ("peer_avg1", "peer_avg2", "self_pe1", "self_pe2"):
        base[k] = vs.get(k)
    base["row_pe_ok"] = has_row_pe
    return base


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
        ispace, comp, rp_peers, val_table, src, rdate = "", "", [], None, "", ""
        for ann, _title, url in ranked[:3]:
            text = _pdf_text(_download_pdf(url))
            if len(text) < 300:
                continue
            i2, c2, raw, vt = _extract_llm(nm, _extract_input(text))
            ispace, comp = ispace or i2, comp or c2
            rp_peers = rp_peers or _resolve_codes(mcon, raw)
            if not src:
                src, rdate = url, str(ann)
            if vt:                       # 找到可比公司估值表就停(最有价值),否则继续翻下一篇
                val_table = vt
                src, rdate = url, str(ann)
                break
        seen = {p["code"] for p in sw}
        peers = sw + [p for p in rp_peers if p["code"] not in seen and p["code"] != ts]
        source = ("研报+申万" if src else "申万三级同业")
    finally:
        mcon.close()
    rec = {"peers": peers, "industry_space": ispace, "competitive": comp, "val_table": val_table,
           "source": source, "report_date": rdate, "cached": False}
    con = _cache_conn()
    try:
        con.execute("INSERT OR REPLACE INTO stock_peers (ts_code,name,peers,industry_space,competitive,val_table,source,report_date,model,ver,fetched_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    [ts, nm, json.dumps(peers, ensure_ascii=False), ispace, comp,
                     json.dumps(val_table, ensure_ascii=False) if val_table else None,
                     source, rdate, "deepseek-v4-flash", PEER_VER, datetime.now()])
    finally:
        con.close()
    return rec


def _valtable_text(ts: str, name: str, vt: dict) -> str:
    """研报『可比公司估值表』(前瞻P/E)→ 文本:本股 vs 同业前瞻PE中位(境内/全部),口径一致、含境外龙头。"""
    import statistics as st
    rows = vt.get("rows") or []
    y1, y2 = vt.get("year1") or "近年", vt.get("year2") or "远年"
    cal = vt.get("caliber") or "前瞻P/E"
    tgt = next((r for r in rows if (r.get("code") and _norm(r["code"]) == ts) or (name and r.get("name") and name in r["name"]) or (r.get("name") and r["name"] in name)), None)
    peers = [r for r in rows if r is not tgt]
    dom = [r for r in peers if r.get("code")]                    # 境内(有A股代码)
    m = lambda rs, k: (round(st.median([r[k] for r in rs if r.get(k)]), 1) if [r for r in rs if r.get(k)] else None)
    lines = [f"- {r.get('name','')}{('('+str(r['code'])+')') if r.get('code') else '(境外)'} {cal} {r.get('pe1','—')}/{r.get('pe2','—')}" for r in rows]
    t1 = (tgt.get("pe1") if tgt else None) or vt.get("self_pe1")
    t2 = (tgt.get("pe2") if tgt else None) or vt.get("self_pe2")
    if not vt.get("row_pe_ok", True):
        # WHY: 估值表常以图片嵌入 PDF、文字层读不到逐行 PE,此时只报正文给出的汇总,不列一串"—"
        return (f"研报可比公司估值({cal} {y1}/{y2},口径一致、优先于自算TTM;原表为图片,仅取正文汇总结论):\n"
                f"本股 {t1 or '—'}/{t2 or '—'};可比公司均值 {vt.get('peer_avg1') or '—'}/{vt.get('peer_avg2') or '—'}\n"
                f"可比公司名单:{'、'.join(r.get('name', '') for r in rows) or '—'}")
    head = (f"研报可比公司估值表({cal} {y1}/{y2},口径一致、优先于自算TTM):\n"
            f"本股 {t1 or '—'}/{t2 or '—'};境内同业中位 {m(dom,'pe1')}/{m(dom,'pe2')};全部(含境外龙头)中位 {m(peers,'pe1')}/{m(peers,'pe2')}")
    return head + "\n" + "\n".join(lines)


def peer_snapshot(code: str, force: bool = False) -> dict:
    """同行 + 相对估值 + 行业定性:优先用研报可比公司估值表(前瞻P/E),没有再回退自算实时PE/PB。"""
    ts = _norm(code)
    info = ensure_peers(ts, force=force)
    peers = info["peers"]
    vt = info.get("val_table")
    if isinstance(vt, dict) and vt.get("rows"):
        nm = ""
        try:
            _c = duckdb.connect(ct.DUCKDB_PATH, read_only=True)
            nm = (_c.execute("SELECT name FROM stock_meta WHERE ts_code=?", [ts]).fetchone() or [""])[0]; _c.close()
        except Exception:
            pass
        vtext = _valtable_text(ts, nm, vt)
        extra = ((f"\n行业空间:{info['industry_space']}" if info.get("industry_space") else "") +
                 (f"\n竞争地位:{info['competitive']}" if info.get("competitive") else ""))
        return {**info, "text": vtext + extra}
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
