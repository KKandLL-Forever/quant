"""peer_finder.py — 个股「同行业可比公司 + 行业/竞争定性」发现与缓存(供基本面 LLM 分析)。

同行身份与行业定性都很稳、几乎一次确定长期不变,值得一次性抽好存库、以后直取。
- 同行(peers):以申万三级同业为骨架(可靠、完整),再并入研报点名的可比公司 —— 机构对"谁算同行"的判断
  比申万三级准(会跨三级把储能系统/变压器/电网设备纳进来),故名单要研报的。
- 估值:**只用研报的名单,不用研报的 PE**。研报估值表常以图片嵌入 PDF(无文本层)、且是发布日快照;
  改由 peer_snapshot 自算前瞻PE = 总市值 ÷ 券商一致预测净利(report_rc,同 ta_analyze 口径),
  本股与同行同口径同日期,任意同行集都成立,还能覆盖当期亏损(TTM PE 为空)的公司。TTM PE/PB 仅作辅助。
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
PEER_VER = "2026-07-25b"   # 提版=作废旧缓存:①旧版PDF正文截断致研报同行名单抽不全 ②val_table列改存rp_codes
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
    """打开(必要时建)独立同行缓存库,返回可读写连接。

    val_table 列为历史遗留名,现存"研报点名的同行代码列表"(rp_codes);另有 fwd_np 表缓存券商预测净利。"""
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
            "report_date": r[4], "rp_codes": _as_list(r[6]), "cached": True}


def _as_list(raw):
    """缓存列反序列化成 list;旧版本该列存的是 val_table dict,一律当空。"""
    try:
        v = json.loads(raw or "[]")
    except Exception:
        return []
    return v if isinstance(v, list) else []


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
    """DeepSeek 从研报正文抽 行业空间 + 竞争地位 + 可比公司名单(temp 0,只输出 JSON)。

    只要名单不要研报里的 PE:机构对"谁算同行"的判断比申万三级准(会跨三级把储能系统/变压器/电网设备
    纳进来),但它的估值表常是图片、且是发布日快照;PE 一律由 peer_snapshot 用 report_rc 自算。"""
    import ta_analyze
    ta_analyze._load_keys()
    schema = ('{"industry_space":"行业空间/市场规模一句话或空","competitive":"竞争地位一句话或空",'
              '"peers":[{"name":"可比公司/竞争对手名","code":"6位A股代码或空"}]}')
    rules = ("要求:①peers 只填研报明确点名为可比公司/竞争对手的同行(最多10家),优先取正文『我们选取以下N家"
             "公司作为可比公司』那一句列出的;②境外公司(如美光/三星)code 留空;③不确定就留空,不要编造。")
    prompt = (f"下面是关于公司「{name}」的券商研报正文。只抽客观信息,输出 JSON:\n"
              + schema + "\n" + rules + "\n\n研报正文:\n" + report_text)
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
        for ann, _title, url in ranked[:3]:
            text = _pdf_text(_download_pdf(url))
            if len(text) < 300:
                continue
            i2, c2, raw = _extract_llm(nm, _extract_input(text))
            ispace, comp = ispace or i2, comp or c2
            rp_peers = rp_peers or _resolve_codes(mcon, raw)
            if not src:
                src, rdate = url, str(ann)
            if rp_peers and ispace and comp:     # 名单+定性都拿到就停,否则继续翻下一篇
                src, rdate = url, str(ann)
                break
        seen = {p["code"] for p in sw}
        rp_only = [p for p in rp_peers if p["code"] not in seen and p["code"] != ts]
        peers = sw + rp_only
        source = ("研报+申万" if src else "申万三级同业")
    finally:
        mcon.close()
    rec = {"peers": peers, "rp_codes": [p["code"] for p in rp_peers], "industry_space": ispace,
           "competitive": comp, "source": source, "report_date": rdate, "cached": False}
    con = _cache_conn()
    try:
        con.execute("INSERT OR REPLACE INTO stock_peers (ts_code,name,peers,industry_space,competitive,val_table,source,report_date,model,ver,fetched_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    [ts, nm, json.dumps(peers, ensure_ascii=False), ispace, comp,
                     json.dumps(rec["rp_codes"], ensure_ascii=False),
                     source, rdate, "deepseek-v4-flash", PEER_VER, datetime.now()])
    finally:
        con.close()
    return rec


def _fwd_np_many(codes, year: int, ttl_days: int = 20) -> dict:
    """批量取各股 year 年券商一致预测归母净利(亿)与覆盖券商数,返回 {ts_code:(np亿, n家)}。

    口径同 ta_analyze:只取近半年、每券商每年最新一份,取中位。预测本身月度级才变,故按 ttl_days
    缓存在 peer_cache.duckdb(不写 7GB 主库);PE 每次用当日总市值现算,不缓存。"""
    con = _cache_conn()
    try:
        con.execute("""CREATE TABLE IF NOT EXISTS fwd_np (
            ts_code VARCHAR, year INTEGER, np_yi DOUBLE, n_org INTEGER, fetched_at TIMESTAMP,
            PRIMARY KEY (ts_code, year))""")
        rows = con.execute("SELECT ts_code,np_yi,n_org,fetched_at FROM fwd_np WHERE year=?", [year]).fetchall()
        fresh = {r[0]: (r[1], r[2]) for r in rows
                 if r[3] and (datetime.now() - r[3]).days < ttl_days}
        miss = [c for c in codes if c not in fresh]
        if miss:
            import pandas as pd
            import ta_analyze
            ta_analyze._load_keys()
            import tushare as tsapi
            pro = tsapi.pro_api(os.environ.get("TUSHARE_TOKEN", ""))
            got = []
            for c in miss:
                np_yi, n = None, 0
                try:
                    rc = pro.report_rc(ts_code=c, start_date=f"{year-1}0101", end_date=f"{year+2}1231")
                    rc = rc[rc["quarter"].astype(str) == f"{year}Q4"].copy()
                    rc["rd"] = pd.to_datetime(rc["report_date"])
                    rc = rc[rc["rd"] >= rc["rd"].max() - pd.Timedelta(days=180)]
                    rc = rc.sort_values("rd").groupby("org_name", as_index=False).tail(1)
                    if rc["np"].notna().any():
                        np_yi, n = float(rc["np"].median()) / 1e4, int(len(rc))
                except Exception:
                    pass
                got.append((c, year, np_yi, n, datetime.now()))
                fresh[c] = (np_yi, n)
            con.executemany("INSERT OR REPLACE INTO fwd_np VALUES (?,?,?,?,?)", got)
    finally:
        con.close()
    return {c: v for c, v in fresh.items() if v[0]}


def peer_snapshot(code: str, force: bool = False) -> dict:
    """同行 + 相对估值 + 行业定性:主口径=自算前瞻PE(总市值÷券商一致预测净利),TTM PE/PB 作辅助。"""
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
        vals = {r[0]: (r[1], r[2], r[3]) for r in mcon.execute(
            f"SELECT ts_code,pe_ttm,pb,total_mv FROM daily_basic WHERE trade_date=? AND ts_code IN ({ph})",
            [mx, *codes]).fetchall()}
    finally:
        mcon.close()
    yr = datetime.now().year
    fnp = _fwd_np_many(codes, yr)
    fpe = {}
    for c in codes:
        v, np_ = vals.get(c), fnp.get(c)
        if v and v[2] and np_ and np_[0] > 0:
            fpe[c] = v[2] / 1e4 / np_[0]
    import statistics as st
    rp = set(info.get("rp_codes") or [])
    cmp_peers = [p for p in peers if p["code"] in rp] or peers      # 有研报点名就只用研报那组
    ref_peers = [p for p in peers if p not in cmp_peers]
    grp = "研报点名" if cmp_peers is not peers else "申万三级"
    pf = [fpe[p["code"]] for p in cmp_peers if p["code"] in fpe]
    pes = [vals[p["code"]][0] for p in cmp_peers if vals.get(p["code"]) and vals[p["code"]][0] and vals[p["code"]][0] > 0]
    pbs = [vals[p["code"]][1] for p in cmp_peers if vals.get(p["code"]) and vals[p["code"]][1] and vals[p["code"]][1] > 0]
    fpe_med = round(st.median(pf), 1) if pf else None
    pe_med = round(st.median(pes), 1) if pes else None
    pb_med = round(st.median(pbs), 2) if pbs else None
    self_fpe = round(fpe[ts], 1) if ts in fpe else None
    self_pe, self_pb = (vals.get(ts) or (None, None, None))[:2]
    def _line(p):
        v, f, np_ = vals.get(p["code"]), fpe.get(p["code"]), fnp.get(p["code"])
        s = (f"- {p['name']}({p['code']}) 前瞻PE {round(f,1)}(预测净利{round(np_[0],1)}亿/{np_[1]}家券商)"
             if f and np_ else f"- {p['name']}({p['code']}) 前瞻PE —(无券商覆盖)")
        return s + f" | TTM PE {round(v[0],1) if v and v[0] else '—'} / PB {round(v[1],2) if v and v[1] else '—'}"

    cmp = "高于" if (self_fpe and fpe_med and self_fpe > fpe_med) else "低于" if (self_fpe and fpe_med) else "—"
    head = (f"同业可比:估值对比只用**{grp}的 {len(cmp_peers)} 家**({info['source']};其中{len(pf)}家有券商覆盖)"
            + ("" if grp == "研报点名" else ";研报未点名或抽取失败,退回申万三级") + "\n"
            f"**前瞻PE({yr}E,主口径:总市值÷券商一致预测净利,与本股同口径同日期)**:"
            f"同业中位 {fpe_med};本股 {self_fpe or '—'}({cmp}同业中位)\n"
            f"辅助(TTM,口径不同勿与前瞻混用):同业PE中位 {pe_med} / PB中位 {pb_med};"
            f"本股 PE {round(self_pe,1) if self_pe else '—'} / PB {round(self_pb,2) if self_pb else '—'}")
    body = "\n".join(_line(p) for p in cmp_peers)
    if ref_peers:
        body += (f"\n〔以下{len(ref_peers)}家为申万三级同业,**未计入上面的中位**,仅供参照〕\n"
                 + "\n".join(_line(p) for p in ref_peers))
    extra = ((f"\n行业空间:{info['industry_space']}" if info.get("industry_space") else "") +
             (f"\n竞争地位:{info['competitive']}" if info.get("competitive") else ""))
    return {**info, "peer_fpe_med": fpe_med, "self_fpe": self_fpe, "fwd_year": yr, "cmp_group": grp,
            "cmp_codes": [p["code"] for p in cmp_peers],
            "peer_pe_med": pe_med, "peer_pb_med": pb_med, "self_pe": self_pe, "self_pb": self_pb,
            "text": head + "\n" + body + extra}


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
