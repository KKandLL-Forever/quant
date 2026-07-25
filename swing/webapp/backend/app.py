"""app.py — ML信号 + LLM分析 的后端(FastAPI)。

前端"训练模型"按钮 → POST /api/train → 后端子进程跑 run_ml_signals_2026.py --json → 回结构化数据;
前端某行点"LLM分析" → POST /api/analyze → 后端跑 ta_analyze(技术+消息面)→ 回报告 + 分析师层判断。

环境：.venv312。用法:cd swing/webapp/backend && uvicorn app:app --reload --port 8000
依赖:fastapi/uvicorn;复用 swing/run_ml_signals_2026.py(--json)、swing/ta_analyze.py。
"""

import json
import os
import re
_ROOT = os.path.dirname(os.path.abspath(__file__))
while _ROOT != "/" and not os.path.exists(os.path.join(_ROOT, "cache_tushare.py")):
    _ROOT = os.path.dirname(_ROOT)
import subprocess
import sys
import tempfile

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

SWING = os.path.join(_ROOT, "swing")


PY = sys.executable   # 子进程用跑 uvicorn 的同一解释器,保证依赖环境一致(跨平台)
POOL_FILE = os.path.join(_ROOT, "xiaoxifu", "leader_pool.json")   # 自定义龙头股票池持久化(非数据库)
sys.path.insert(0, SWING)
sys.path.insert(0, _ROOT)

app = FastAPI(title="ML信号 + LLM分析")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class TrainReq(BaseModel):
    mode: str = "quick"
    tier: int = 5
    start: str = "20250101"
    end: str | None = None
    n: int = 800            # 股票池大小(按流通市值取前 N)
    train: bool = False     # 重新训练模型(--train)
    refresh: bool = False   # 不重训,但绕过缓存重新打分(拿最新行情)


class AnalyzeReq(BaseModel):
    code: str
    date: str
    force: bool = False
    rid: str = ""            # 前端生成的请求id,用于取消


class CancelReq(BaseModel):
    rid: str


ANALYZE_PROCS = {}           # rid -> Popen,供取消


CACHE_DIR = os.path.join(os.path.dirname(__file__), ".analyze_cache")
os.makedirs(CACHE_DIR, exist_ok=True)


def _read_cache(path, **kw):
    """读 JSON 缓存;文件缺失/损坏/旧 gbk 编码等一律返回 None(当作未命中,重算),避免 500。"""
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f, **kw)
    except Exception:
        return None


@app.post("/api/train")
def train(req: TrainReq):
    """跑 ML 信号管线,返回 {signals, banner, cal, latest, ntrade, ...}。train=True 重训模型。"""
    ck = os.path.join(CACHE_DIR, f"train_{req.mode}_{req.tier}_n{req.n}_{req.start}_{req.end or 'now'}.json")
    if not req.train and not req.refresh:
        r = _read_cache(ck, parse_constant=lambda *_: None)
        if r is not None:
            r["cached"] = True
            return r
    out = tempfile.NamedTemporaryFile(suffix=".json", delete=False).name
    cmd = [PY, "run_ml_signals_2026.py", "--mode", req.mode, "--tier", str(req.tier),
           "--n", str(req.n), "--start", req.start, "--json", out]
    if req.end:
        cmd += ["--end", req.end]
    if req.train:
        cmd += ["--train"]
    try:
        p = subprocess.run(cmd, cwd=SWING, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=1800)
        if not os.path.exists(out) or os.path.getsize(out) == 0:
            return {"ok": False, "error": "ML 未产出数据。stderr:\n" + (p.stderr or p.stdout or "")[-2000:]}
        with open(out, encoding="utf-8") as f:
            payload = json.load(f, parse_constant=lambda *_: None)
        os.unlink(out)
        payload["ok"] = True
        payload["cached"] = False
        with open(ck, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        return payload
    except Exception as e:
        import traceback
        return {"ok": False, "error": traceback.format_exc()[-2000:]}


RID_OUT = {}                 # rid -> 子进程输出临时文件路径(供进度/流式读取)


class ProgReq(BaseModel):
    rid: str


def _cf(code, date):
    return os.path.join(CACHE_DIR, f"{code.split('.')[0]}_{date}.json")


@app.get("/api/analyze/cached_dates")
def analyze_cached_dates(code: str = ""):
    """返回某股票已有 LLM 分析缓存的日期列表(供弹窗日期选择器标注)。"""
    import glob
    pre = (code or "").split(".")[0]
    if not pre:
        return {"ok": True, "dates": []}
    dates = []
    for p in glob.glob(os.path.join(CACHE_DIR, f"{pre}_*.json")):
        base = os.path.basename(p)[:-5]
        d = base[len(pre) + 1:]
        if len(d) == 10 and d[4] == "-":
            dates.append(d)
    return {"ok": True, "dates": sorted(dates)}


_CACHE_INDEX_PAT = re.compile(r"^([^_]+)_(\d{4}-\d{2}-\d{2})\.json$")
_CACHE_INDEX_ITEMS = {}       # basename -> (mtime, item);增量解析,只重读变动文件
_CACHE_INDEX_NAMES = {}       # ts_code -> name;查过的名称常驻,新代码才查库


@app.get("/api/analyze/cache_index")
def analyze_cache_index():
    """列全部已缓存 LLM 分析(供主页左侧缓存侧栏):扫 {code}_{date}.json,正则天然排除 biz_/train_/*.progress。增量解析(按 mtime 只重读变动文件)+名称库查询结果常驻缓存,文件多也不重复读盘/查库。"""
    seen = set()
    for e in os.scandir(CACHE_DIR):
        m = _CACHE_INDEX_PAT.match(e.name)
        if not m:
            continue
        seen.add(e.name)
        cached = _CACHE_INDEX_ITEMS.get(e.name)
        mtime = e.stat().st_mtime
        if cached and cached[0] == mtime:
            continue
        r = _read_cache(e.path)
        if not isinstance(r, dict):
            _CACHE_INDEX_ITEMS.pop(e.name, None)
            continue
        v = r.get("verdict")
        _CACHE_INDEX_ITEMS[e.name] = (mtime, {
            "ts_code": r.get("code") or m.group(1), "code": m.group(1), "name": None,
            "date": m.group(2), "action": v.get("action") if isinstance(v, dict) else None})
    for name in list(_CACHE_INDEX_ITEMS):
        if name not in seen:
            del _CACHE_INDEX_ITEMS[name]

    items = [it for _, it in _CACHE_INDEX_ITEMS.values()]
    miss = {it["ts_code"] for it in items if it["ts_code"] not in _CACHE_INDEX_NAMES}
    if miss:
        import duckdb
        from cache_tushare import DUCKDB_PATH
        con = duckdb.connect(DUCKDB_PATH, read_only=True)
        try:
            rows = con.execute(f"SELECT ts_code, name FROM stock_meta WHERE ts_code IN ({','.join('?' * len(miss))})",
                               list(miss)).fetchall()
        finally:
            con.close()
        for c, n in rows:
            _CACHE_INDEX_NAMES[c] = n
    return {"ok": True, "count": len(items),
            "items": [{**it, "name": _CACHE_INDEX_NAMES.get(it["ts_code"]) or it["code"]} for it in items]}


@app.get("/api/trade_cal")
def trade_cal():
    """返回全部已过交易日(YYYY-MM-DD,升序)+ 最新交易日,供日期选择器禁非交易日、限上限。"""
    import duckdb
    import datetime as dt
    from cache_tushare import DUCKDB_PATH
    today = dt.date.today().strftime("%Y%m%d")
    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    try:
        rows = con.execute(
            "SELECT cal_date FROM trade_cal WHERE exchange='SSE' AND is_open=1 "
            "AND cal_date <= ? ORDER BY cal_date", [today]).fetchall()
    finally:
        con.close()
    dates = [f"{d[0][:4]}-{d[0][4:6]}-{d[0][6:8]}" for d in rows if d[0] and len(d[0]) == 8]
    return {"ok": True, "dates": dates, "latest": dates[-1] if dates else None}


@app.post("/api/analyze/start")
def analyze_start(req: AnalyzeReq):
    """启动分析子进程(只产技术面/消息面报告),立即返回 rid;命中缓存则直接回结果。"""
    cf = _cf(req.code, req.date)
    if not req.force:
        r = _read_cache(cf)
        if r is not None:
            r["cached"] = True
            return r
    out = tempfile.NamedTemporaryFile(suffix=".json", delete=False).name
    open(out + ".progress", "w").close()
    proc = subprocess.Popen([PY, "ta_analyze_job.py", req.code, req.date, out], cwd=SWING)
    ANALYZE_PROCS[req.rid] = proc
    RID_OUT[req.rid] = out
    return {"ok": True, "cached": False, "rid": req.rid}


@app.post("/api/analyze/progress")
def analyze_progress(req: ProgReq):
    """轮询:返回当前阶段;报告好了带回 market_report/news_report。"""
    out = RID_OUT.get(req.rid)
    if not out:
        return {"ok": False, "error": "无此任务"}
    prog = _read_cache(out + ".progress") or {}
    proc = ANALYZE_PROCS.get(req.rid)
    if not prog.get("done") and proc and proc.poll() is not None and proc.returncode != 0:
        return {"ok": False, "error": "分析已取消或失败", "cancelled": proc.returncode < 0}
    return {"ok": True, **prog}


@app.get("/api/analyze/stream")
def analyze_stream(rid: str, code: str, date: str):
    """SSE:报告就绪后,逐字流式输出 公司分析 + 买卖判断;结束写缓存。"""
    def gen():
        out = RID_OUT.get(rid)
        prog = _read_cache((out + ".progress") if out else "") or {}
        if not prog.get("done"):
            yield f"data: {json.dumps({'t': 'error', 'msg': '报告未就绪'})}\n\n"
            return
        import ta_analyze
        ta_analyze._load_keys()
        state = {"market_report": prog.get("market_report", ""), "news_report": prog.get("news_report", "")}
        bf = os.path.join(CACHE_DIR, f"biz_{code.split('.')[0]}_{date}.json")
        business = _read_cache(bf)
        if not isinstance(business, dict) or business.get("_ver") != ta_analyze.BIZ_VER:  # 旧版本口径→重算
            business = None
        if business is None:
            for ev in ta_analyze.business_stream(code, date):
                if "delta" in ev:
                    yield f"data: {json.dumps({'t': 'biz', 'd': ev['delta']}, ensure_ascii=False)}\n\n"
                else:
                    business = ev["final"]
                    with open(bf, "w", encoding="utf-8") as f:
                        json.dump(business, f, ensure_ascii=False)
        yield f"data: {json.dumps({'t': 'biz_done', 'v': business}, ensure_ascii=False)}\n\n"
        verdict = None
        for ev in ta_analyze.verdict_stream(state, business):
            if "delta" in ev:
                yield f"data: {json.dumps({'t': 'ver', 'd': ev['delta']}, ensure_ascii=False)}\n\n"
            else:
                verdict = ev["final"]
        res = {"ok": True, "code": code, "date": date, "market_report": state["market_report"],
               "news_report": state["news_report"], "dialogue": prog.get("dialogue", []),
               "verdict": verdict, "business": business, "risk_decision": prog.get("risk_decision", ""), "cached": False}
        with open(_cf(code, date), "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False)
        for p in (out, out + ".progress"):
            try:
                os.unlink(p)
            except Exception:
                pass
        ANALYZE_PROCS.pop(rid, None)
        RID_OUT.pop(rid, None)
        yield f"data: {json.dumps({'t': 'done', 'verdict': verdict, 'business': business}, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/analyze_cancel")
def analyze_cancel(req: CancelReq):
    """关闭弹窗时调用:kill 掉该请求对应的分析子进程。"""
    p = ANALYZE_PROCS.pop(req.rid, None)
    if p and p.poll() is None:
        p.kill()
        return {"ok": True, "killed": True}
    return {"ok": True, "killed": False}


class KlineReq(BaseModel):
    code: str
    date: str
    win: int = 125


@app.post("/api/kline")
def kline(req: KlineReq):
    """取该股突破日前后约 win*2 根后复权日K + czsc 笔/中枢 + M3 多腿买卖点,供前端画缠论形态。"""
    try:
        import duckdb
        import numpy as np
        import pandas as pd
        from czsc import CZSC, RawBar, Freq, ZS
        import czsc.signals as CS
        from cache_tushare import DUCKDB_PATH
        import ta_bridge
        ts = ta_bridge._norm(req.code)
        con = duckdb.connect(DUCKDB_PATH, read_only=True)
        g = con.execute("""SELECT d.trade_date td, d.open*a.adj_factor o, d.high*a.adj_factor h,
            d.low*a.adj_factor l, d.close*a.adj_factor c, d.vol v
            FROM daily d JOIN adj_factor a ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date
            WHERE d.ts_code=? ORDER BY d.trade_date""", [ts]).fetch_df()
        laf = con.execute("SELECT adj_factor FROM adj_factor WHERE ts_code=? ORDER BY trade_date DESC LIMIT 1", [ts]).fetchone()
        con.close()
        if g.empty:
            return {"ok": False, "error": "无行情"}
        laf = float(laf[0]) if laf else 1.0
        for col in ("o", "h", "l", "c"):
            g[col] = g[col] / laf
        g["td"] = pd.to_datetime(g["td"])
        bo = pd.Timestamp(req.date)
        idx = int((g["td"] - bo).abs().values.argmin())
        g = g.iloc[max(0, idx - req.win): idx + req.win].reset_index(drop=True)
        bo_local = int((g["td"] - bo).abs().values.argmin())
        cc = g["c"].to_numpy()
        ma60 = pd.Series(cc).rolling(60).mean().to_numpy()
        d = lambda t: str(g["td"].iloc[t].date())
        bars = [RawBar(symbol=ts, id=i, dt=r.td, freq=Freq.D, open=r.o, close=r.c,
                       high=r.h, low=r.l, vol=r.v, amount=0.0) for i, r in g.iterrows()]
        c = CZSC(bars[:1])
        sellset = set([0] if _czsc_sell_now(CS, c) else [])
        buyset = set([0] if _czsc_buy_now(CS, c) else [])
        for i in range(1, len(bars)):
            c.update(bars[i])
            if _czsc_sell_now(CS, c):
                sellset.add(i)
            if _czsc_buy_now(CS, c):
                buyset.add(i)
        legs_buy = [bo_local]; sells = []; en = bo_local; t = bo_local + 1
        while t < len(cc):
            if (ma60[t] == ma60[t] and cc[t] < ma60[t]) or cc[t] <= cc[en] * 0.85:
                sells.append((t, "止")); break
            if t in sellset:
                sells.append((t, "缠"))
                re = None
                for t2 in range(t + 1, len(cc)):
                    if ma60[t2] == ma60[t2] and cc[t2] < ma60[t2]:
                        break
                    if t2 in buyset:
                        re = t2; break
                if re is None:
                    break
                en = re; legs_buy.append(re); t = re + 1; continue
            t += 1
        marks = [{"date": d(b), "kind": "buy", "label": "买" if b == bo_local else "补"} for b in legs_buy]
        marks += [{"date": d(si), "kind": "sell", "label": lb} for si, lb in sells]
        bis = [[str(c.bi_list[0].fx_a.dt.date()), round(float(c.bi_list[0].fx_a.fx), 2)]] if c.bi_list else []
        for b in c.bi_list:
            bis.append([str(b.fx_b.dt.date()), round(float(b.fx_b.fx), 2)])
        zs = []
        i = 0
        while i + 2 < len(c.bi_list):
            if not ZS(bis=c.bi_list[i:i + 3]).is_valid():
                i += 1
                continue
            j = i + 3
            while j < len(c.bi_list) and ZS(bis=c.bi_list[i:j + 1]).is_valid():
                j += 1
            z = ZS(bis=c.bi_list[i:j])
            zs.append({"sdt": str(z.sdt.date()), "edt": str(z.edt.date()),
                       "zg": round(float(z.zg), 2), "zd": round(float(z.zd), 2)})
            i = j
        ohlc = [[str(r.td.date()), round(r.o, 2), round(r.h, 2), round(r.l, 2), round(r.c, 2), round(float(r.v), 1)] for r in g.itertuples()]
        return {"ok": True, "code": ts, "bo": str(bo.date()), "ohlc": ohlc, "bis": bis, "zs": zs, "marks": marks}
    except Exception:
        import traceback
        return {"ok": False, "error": traceback.format_exc()[-1500:]}


class AdviseReq(BaseModel):
    code: str
    buy_date: str


def _czsc_sell_now(CS, c):
    """返回触发的缠论卖点规则名(缠论一卖 / MACD顶背驰),都没触发返回 None。"""
    for fn, tag in (("cxt_first_sell_V221126", "缠论一卖"), ("tas_macd_bc_V221201", "MACD顶背驰")):
        f = getattr(CS, fn, None)
        if not f:
            continue
        try:
            out = f(c, di=1)
        except Exception:
            try:
                out = f(c)
            except Exception:
                continue
        for v in out.values():
            tk = str(v).split("_")
            if fn == "cxt_first_sell_V221126" and tk[0] != "其他":
                return tag
            if fn == "tas_macd_bc_V221201" and tk[0] == "背驰" and "红" in str(v):
                return tag
    return None


def _czsc_buy_now(CS, c):
    """缠论买点(一买/二买/三买)任一成立返回 True。"""
    for fn in ("cxt_first_buy_V221126", "cxt_second_bs_V230320", "cxt_third_buy_V230228"):
        f = getattr(CS, fn, None)
        if not f:
            continue
        try:
            out = f(c, di=1)
        except Exception:
            try:
                out = f(c)
            except Exception:
                continue
        for v in out.values():
            s = str(v)
            if fn == "cxt_second_bs_V230320":
                if "买" in s:
                    return True
            elif s.split("_")[0] != "其他":
                return True
    return False


INDEX_ALIAS = {"1A0001": "000001.SH", "SH000001": "000001.SH", "上证指数": "000001.SH",
               "1B0001": "399001.SZ", "深证成指": "399001.SZ"}


def _norm_code(raw):
    """归一化代码:通达信指数别名→tushare;带后缀原样;ETF 5开头→.SH、1开头→.SZ;否则按个股规则。"""
    import ta_bridge
    s = raw.strip().upper()
    if s in INDEX_ALIAS:
        return INDEX_ALIAS[s]
    if "." in s:
        return s
    if s[:1] == "5":
        return s + ".SH"
    if s[:1] == "1":
        return s + ".SZ"
    return ta_bridge._norm(s)


def _is_index(ts):
    """判断是否指数:上证 000/999开头.SH、深证 399开头.SZ。"""
    return (ts.endswith(".SH") and ts[:3] in ("000", "999")) or (ts.endswith(".SZ") and ts.startswith("399"))


def _index_ohlc(ts):
    """指数:tushare 在线拉 index_daily(无需复权),返回(g[td,o,h,l,c,v], 1.0, 名称)。"""
    import os
    import pandas as pd
    import tushare as tsl
    tok = os.environ.get("TUSHARE_TOKEN", "")
    pe = os.path.join(_ROOT, ".pyenv.local")
    if not tok and os.path.exists(pe):
        for line in open(pe, encoding="utf-8"):
            if line.strip().startswith("TUSHARE_TOKEN") and "=" in line:
                tok = line.split("=", 1)[1].strip().strip('"').strip("'")
    pro = tsl.pro_api(tok)
    d = pro.index_daily(ts_code=ts, start_date="20180101", fields="trade_date,open,high,low,close,vol")
    if d is None or d.empty:
        return None, 1.0, ""
    d = d.sort_values("trade_date")
    g = pd.DataFrame({"td": d["trade_date"], "o": d["open"], "h": d["high"],
                      "l": d["low"], "c": d["close"], "v": d["vol"]})
    nm = ts
    try:
        ib = pro.index_basic(ts_code=ts, fields="ts_code,name")
        if ib is not None and not ib.empty:
            nm = ib["name"].iloc[0]
    except Exception:
        pass
    return g, 1.0, nm


def _fund_ohlc(ts):
    """ETF/基金:tushare 在线拉 fund_daily + fund_adj,返回(g[td,o,h,l,c,v 后复权], 最新adj, 名称)。"""
    import os
    import pandas as pd
    import tushare as tsl
    tok = os.environ.get("TUSHARE_TOKEN", "")
    pe = os.path.join(_ROOT, ".pyenv.local")
    if not tok and os.path.exists(pe):
        for line in open(pe, encoding="utf-8"):
            if line.strip().startswith("TUSHARE_TOKEN") and "=" in line:
                tok = line.split("=", 1)[1].strip().strip('"').strip("'")
    pro = tsl.pro_api(tok)
    d = pro.fund_daily(ts_code=ts, start_date="20180101", fields="trade_date,open,high,low,close,vol")
    if d is None or d.empty:
        return None, 1.0, ""
    adj = pro.fund_adj(ts_code=ts, start_date="20180101", fields="trade_date,adj_factor")
    d = d.merge(adj, on="trade_date", how="left").sort_values("trade_date")
    d["adj_factor"] = d["adj_factor"].ffill().bfill().fillna(1.0)
    laf = float(d["adj_factor"].iloc[-1])
    g = pd.DataFrame({"td": d["trade_date"], "o": d["open"] * d["adj_factor"], "h": d["high"] * d["adj_factor"],
                      "l": d["low"] * d["adj_factor"], "c": d["close"] * d["adj_factor"], "v": d["vol"]})
    nm = ""
    try:
        fb = pro.fund_basic(ts_code=ts, fields="ts_code,name")
        if fb is not None and not fb.empty:
            nm = fb["name"].iloc[0]
    except Exception:
        pass
    return g, laf, nm


@app.post("/api/advise")
def advise(req: AdviseReq):
    """对单只个股给定买入日,按缠论 route1(一卖/MACD顶背驰 + 跌破MA60 + 15%止损)判断是否已离场/明日是否该卖、卖出价位;附 K线+笔+中枢。"""
    try:
        import duckdb
        import numpy as np
        import pandas as pd
        from czsc import CZSC, RawBar, Freq, ZS
        import czsc.signals as CS
        from cache_tushare import DUCKDB_PATH
        ts = _norm_code(req.code)
        con = duckdb.connect(DUCKDB_PATH, read_only=True)
        g = con.execute("""SELECT d.trade_date td, d.open*a.adj_factor o, d.high*a.adj_factor h,
            d.low*a.adj_factor l, d.close*a.adj_factor c, d.vol v
            FROM daily d JOIN adj_factor a ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date
            WHERE d.ts_code=? ORDER BY d.trade_date""", [ts]).fetch_df()
        name = con.execute("SELECT name FROM stock_meta WHERE ts_code=?", [ts]).fetchone()
        laf = con.execute("SELECT adj_factor FROM adj_factor WHERE ts_code=? ORDER BY trade_date DESC LIMIT 1", [ts]).fetchone()
        con.close()
        if g.empty:
            if _is_index(ts):
                g, laf, nm = _index_ohlc(ts)          # 指数
            else:
                g, laf, nm = _fund_ohlc(ts)           # ETF/基金
            if g is None or g.empty:
                return {"ok": False, "error": f"无行情:{ts}(个股/ETF/指数均未取到)"}
            name = (nm,)
        else:
            laf = float(laf[0]) if laf else 1.0
        for col in ("o", "h", "l", "c"):
            g[col] = g[col] / laf
        g["td"] = pd.to_datetime(g["td"])
        buy = pd.Timestamp(req.buy_date)
        bi_global = int((g["td"] - buy).abs().values.argmin())
        if g["td"].iloc[bi_global] < buy:
            return {"ok": False, "error": "买入日晚于最新交易日"}
        cc = g["c"].to_numpy()
        ma60 = pd.Series(cc).rolling(60).mean().to_numpy()
        w0 = max(0, bi_global - 250)
        w = g.iloc[w0:].reset_index(drop=True)
        bars = [RawBar(symbol=ts, id=i, dt=r.td, freq=Freq.D, open=r.o, close=r.c,
                       high=r.h, low=r.l, vol=r.v, amount=0.0) for i, r in w.iterrows()]
        c = CZSC(bars[:1])
        selltag = {}; buyset = set()
        t0 = _czsc_sell_now(CS, c)
        if t0:
            selltag[w0] = t0
        if _czsc_buy_now(CS, c):
            buyset.add(w0)
        for i in range(1, len(bars)):
            c.update(bars[i])
            tg = _czsc_sell_now(CS, c)
            if tg:
                selltag[w0 + i] = tg
            if _czsc_buy_now(CS, c):
                buyset.add(w0 + i)
        sellset = set(selltag)
        entry = float(cc[bi_global])
        latest = len(cc) - 1
        ma_l = float(ma60[latest]) if ma60[latest] == ma60[latest] else None
        d = lambda t: str(g["td"].iloc[t].date())

        def _broke(t, en):
            return (ma60[t] == ma60[t] and cc[t] < ma60[t]) or cc[t] <= cc[en] * 0.85

        # M3 回放:缠论卖点止盈→回调缠论买点回补→跌破MA60/入场价85%终止,复利
        legs_buy = [bi_global]; legs_sell = []
        mult = 1.0; en = bi_global; legs = 1; t = bi_global + 1
        state = None; info = {}
        while t <= latest:
            if _broke(t, en):
                mult *= cc[t] / cc[en]
                state = "ended"
                info = {"exit_date": d(t), "exit_price": round(float(cc[t]), 2),
                        "reason": "跌破60日线" if (ma60[t] == ma60[t] and cc[t] < ma60[t]) else "触发15%止损"}
                legs_sell.append(t); break
            if t in sellset:
                mult *= cc[t] / cc[en]; legs_sell.append(t)
                re = None; broke_wait = None
                for t2 in range(t + 1, latest + 1):
                    if ma60[t2] == ma60[t2] and cc[t2] < ma60[t2]:
                        broke_wait = t2; break
                    if t2 in buyset:
                        re = t2; break
                if re is None:
                    if broke_wait is not None:
                        state = "ended"
                        info = {"exit_date": d(t), "exit_price": round(float(cc[t]), 2),
                                "reason": selltag.get(t, "缠论卖点") + "止盈后跌破60日线作罢"}
                    else:
                        state = "waiting"
                        info = {"sold_date": d(t), "sold_price": round(float(cc[t]), 2),
                                "sell_rule": selltag.get(t), "buy_today": latest in buyset}
                    break
                en = re; legs += 1; legs_buy.append(re); t = re + 1; continue
            t += 1
        if state is None:
            state = "holding"

        if state == "ended":
            advice = {"state": "ended", "legs": legs, "ret_pct": round(float(mult - 1) * 100, 1), **info}
        elif state == "waiting":
            advice = {"state": "waiting", "legs": legs,
                      "realized_pct": round(float(mult - 1) * 100, 1),
                      "ma60": None if ma_l is None else round(ma_l, 2),
                      "latest_close": round(float(cc[latest]), 2), "latest_date": d(latest), **info}
        else:
            float_ret = cc[latest] / cc[en]
            stop = cc[latest] * 0.85
            trig = max([x for x in (ma_l, stop) if x is not None])
            advice = {"state": "holding", "legs": legs,
                      "leg_entry_date": d(en), "leg_entry_price": round(float(cc[en]), 2),
                      "czsc_sell_today": latest in sellset, "sell_rule": selltag.get(latest),
                      "ma60": None if ma_l is None else round(ma_l, 2), "stop": round(stop, 2),
                      "trigger": round(trig, 2), "latest_close": round(float(cc[latest]), 2),
                      "latest_date": d(latest),
                      "total_ret_pct": round(float(mult * float_ret - 1) * 100, 1)}
        top_fxs = [x for x in getattr(c, "fx_list", []) if "顶" in str(x.mark)]
        if top_fxs:
            tf = top_fxs[-1]
            advice["sell_top"] = round(float(tf.fx), 2)
            advice["sell_confirm"] = round(float(tf.low), 2)
            advice["sell_top_date"] = str(tf.dt.date())
        bis = [[str(c.bi_list[0].fx_a.dt.date()), round(float(c.bi_list[0].fx_a.fx), 2)]] if c.bi_list else []
        for b in c.bi_list:
            bis.append([str(b.fx_b.dt.date()), round(float(b.fx_b.fx), 2)])
        zs = []
        i = 0
        while i + 2 < len(c.bi_list):
            if not ZS(bis=c.bi_list[i:i + 3]).is_valid():
                i += 1; continue
            j = i + 3
            while j < len(c.bi_list) and ZS(bis=c.bi_list[i:j + 1]).is_valid():
                j += 1
            z = ZS(bis=c.bi_list[i:j])
            zs.append({"sdt": str(z.sdt.date()), "edt": str(z.edt.date()),
                       "zg": round(float(z.zg), 2), "zd": round(float(z.zd), 2)})
            i = j
        ohlc = [[str(r.td.date()), round(r.o, 2), round(r.h, 2), round(r.l, 2), round(r.c, 2), round(float(r.v), 1)] for r in w.itertuples()]
        buy_td = str(g["td"].iloc[bi_global].date())
        marks = [{"date": d(b), "kind": "buy", "label": "买" if b == bi_global else "补"} for b in legs_buy]
        for sidx in legs_sell:
            marks.append({"date": d(sidx), "kind": "sell", "label": "缠"})
        return {"ok": True, "code": ts, "name": name[0] if name else "", "bo": buy_td,
                "entry": round(entry, 2), "ohlc": ohlc, "bis": bis, "zs": zs, "marks": marks, "advice": advice}
    except Exception:
        import traceback
        return {"ok": False, "error": traceback.format_exc()[-1500:]}


class LimitupReq(BaseModel):
    start: str = "20250101"
    end: str | None = None


@app.post("/api/limitup")
def limitup(req: LimitupReq):
    """涨停统计:从 DuckDB limit_list_d(limit_type=U)按区间取,按交易日分组返回 LimitStock 列表。"""
    try:
        import duckdb
        from cache_tushare import DUCKDB_PATH
        def _d(s):
            return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if s and len(s) == 8 and s.isdigit() else s
        con = duckdb.connect(DUCKDB_PATH, read_only=True)
        args = [_d(req.start)]
        endq = ""
        if req.end:
            endq = " AND trade_date<=?"; args.append(_d(req.end))
        rows = con.execute(f"""SELECT trade_date,ts_code,name,industry,close,pct_chg,amount,turnover_ratio,
            open_times,limit_times,fd_amount,up_stat,first_time,last_time
            FROM limit_list_d WHERE limit_type='U' AND trade_date>=?{endq} ORDER BY trade_date""", args).fetchall()
        con.close()
        by_date = {}
        for r in rows:
            d = str(r[0])
            by_date.setdefault(d, []).append({
                "tsCode": r[1], "name": r[2], "industry": r[3] or "", "close": float(r[4] or 0),
                "pctChg": float(r[5] or 0), "amount": float(r[6] or 0), "turnoverRatio": float(r[7] or 0),
                "openTimes": int(r[8] or 0), "limitTimes": max(1, int(r[9] or 1)), "fdAmount": float(r[10] or 0),
                "upStat": str(r[11] or ""), "firstTime": str(r[12] or ""), "lastTime": str(r[13] or ""),
                "limitType": "U", "luDesc": "",
            })
        return {"ok": True, "dates": sorted(by_date), "byDate": by_date}
    except Exception:
        import traceback
        return {"ok": False, "error": traceback.format_exc()[-1500:]}


class BoardCalReq(BaseModel):
    start: str = "20200101"


@app.post("/api/boardcal")
def boardcal(req: BoardCalReq):
    """连板日历:每交易日的最高板/次高板 + 6板及以上龙头(name/board)。从 limit_list_d 全量算。"""
    try:
        import duckdb
        from cache_tushare import DUCKDB_PATH
        s = req.start
        sd = f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else s
        con = duckdb.connect(DUCKDB_PATH, read_only=True)
        rows = con.execute("""SELECT trade_date, ts_code, name, limit_times, fd_amount
            FROM limit_list_d WHERE limit_type='U' AND trade_date>=? ORDER BY trade_date""", [sd]).fetchall()
        con.close()
        byd = {}
        for td, ts, nm, lt, fd in rows:
            byd.setdefault(str(td), []).append((ts, nm, int(lt or 1), float(fd or 0)))
        days = []
        for d in sorted(byd):
            ss = byd[d]
            boards = [x[2] for x in ss]
            mx = max(boards)
            below = [b for b in boards if b < mx]
            second = max(below) if below else 0
            dragons = sorted([x for x in ss if x[2] >= 6], key=lambda x: (-x[2], -x[3]))
            days.append({
                "date": d.replace("-", ""), "maxBoard": mx, "secondBoard": second,
                "dragons": [{"tsCode": x[0], "name": x[1], "board": x[2]} for x in dragons],
            })
        return {"ok": True, "days": days}
    except Exception:
        import traceback
        return {"ok": False, "error": traceback.format_exc()[-1500:]}


class EtfShareReq(BaseModel):
    codes: list[str]
    start: str
    end: str | None = None


@app.post("/api/etfshare")
def etfshare(req: EtfShareReq):
    """ETF 份额时序:读主库 etf_share(total_share 万份→亿份),按代码返回。数据由 cache_tushare 维护。"""
    try:
        import datetime as dt
        import duckdb
        from cache_tushare import DUCKDB_PATH
        def _d(s):
            return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if s and len(s) == 8 and s.isdigit() else s
        end = req.end or dt.date.today().strftime("%Y%m%d")
        con = duckdb.connect(DUCKDB_PATH, read_only=True)
        series = {}
        try:
            if con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name='etf_share'").fetchone()[0] == 0:
                return {"ok": False, "error": "主库无 etf_share 表,请先运行 cache_tushare 更新"}
            for c in req.codes:
                rows = con.execute("""SELECT trade_date, total_share FROM etf_share
                    WHERE ts_code=? AND trade_date>=? AND trade_date<=? ORDER BY trade_date""",
                    [c, _d(req.start), _d(end)]).fetchall()
                series[c] = [{"trade_date": str(td).replace("-", ""), "fdShare": round(float(tsh) / 10000, 2)} for td, tsh in rows if tsh is not None]
        finally:
            con.close()
        return {"ok": True, "series": series}
    except Exception:
        import traceback
        return {"ok": False, "error": traceback.format_exc()[-1500:]}


class BullTopReq(BaseModel):
    start: str = "20150101"


@app.post("/api/bulltop")
def bulltop(req: BullTopReq):
    """牛市逃顶:全市场整体法PE(剔金融)+历史分位、换手率(+MA5)、股东净减持(月)、融资余额。"""
    try:
        import os
        import datetime as dt
        import duckdb
        import numpy as np
        import pandas as pd
        from cache_tushare import DUCKDB_PATH
        s = req.start
        sd = f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else s
        con = duckdb.connect(DUCKDB_PATH, read_only=True)
        val = con.execute("""SELECT db.trade_date td,
            SUM(CASE WHEN f.ts_code IS NULL THEN db.total_mv ELSE 0 END) tmv,
            SUM(CASE WHEN f.ts_code IS NULL AND db.pe_ttm>0 THEN db.total_mv/db.pe_ttm ELSE 0 END) earn,
            SUM(db.circ_mv) cmv, SUM(db.turnover_rate*db.circ_mv) tnum
            FROM daily_basic db
            LEFT JOIN (SELECT DISTINCT ts_code FROM sw_member WHERE l1_name IN ('银行','非银金融','石油石化')) f USING(ts_code)
            WHERE db.trade_date>=? AND db.total_mv IS NOT NULL
            GROUP BY db.trade_date ORDER BY db.trade_date""", [sd]).fetch_df()
        hld = con.execute("""SELECT ann_date, in_de, change_vol, avg_price FROM stk_holdertrade
            WHERE ann_date>=? AND avg_price IS NOT NULL AND change_vol IS NOT NULL""", [s]).fetch_df()
        idxrows = con.execute("""SELECT trade_date, close FROM index_daily
            WHERE ts_code='000001.SH' AND trade_date>=? ORDER BY trade_date""", [sd]).fetchall()
        con.close()
        index = [{"date": str(td).replace("-", ""), "close": round(float(c), 1)} for td, c in idxrows]

        val = val[val["earn"] > 0].copy()
        val["pe"] = val["tmv"] / val["earn"]
        val["turnover"] = val["tnum"] / val["cmv"] / 100
        pe = val["pe"].to_numpy()
        order = np.sort(pe)
        val["pct"] = np.searchsorted(order, pe, side="right") / len(pe)
        val["ma5"] = val["turnover"].rolling(5).mean()
        valuation = [{"date": str(r.td)[:10].replace("-", ""), "peTtm": round(float(r.pe), 2), "pct": round(float(r.pct), 3),
                      "circMv": round(float(r.cmv) / 1e4, 1), "totalMv": round(float(r.tmv) / 1e4, 1),
                      "amountFull": round(float(r.tnum) / 1e6, 1)}   # 全市场成交额近似(亿)
                     for r in val.itertuples()]
        turnover = [{"date": str(r.td)[:10].replace("-", ""), "turnover": round(float(r.turnover) * 100, 3),
                     "ma5": None if r.ma5 != r.ma5 else round(float(r.ma5) * 100, 3)} for r in val.itertuples()]

        hld["month"] = hld["ann_date"].astype(str).str[:6]
        hld["amt"] = hld["change_vol"] * hld["avg_price"] * hld["in_de"].map(lambda x: 1 if x == "DE" else -1)
        hm = hld.groupby("month")["amt"].sum()
        holder = [{"month": m, "netReduce": round(float(v) / 1e8, 2)} for m, v in hm.items()]

        margin = []
        try:
            import tushare as tsl
            tok = os.environ.get("TUSHARE_TOKEN", "")
            pe_env = os.path.join(_ROOT, ".pyenv.local")
            if not tok and os.path.exists(pe_env):
                for line in open(pe_env, encoding="utf-8"):
                    if line.strip().startswith("TUSHARE_TOKEN") and "=" in line:
                        tok = line.split("=", 1)[1].strip().strip('"').strip("'")
            pro = tsl.pro_api(tok)
            end = dt.date.today().strftime("%Y%m%d")
            parts = []
            for y in range(int(s[:4]), int(end[:4]) + 1):
                df = pro.margin(start_date=f"{y}0101", end_date=f"{y}1231")
                if df is not None and not df.empty:
                    parts.append(df)
            if parts:
                m = pd.concat([p.dropna(axis=1, how="all") for p in parts])
                m["two"] = m["rzye"].fillna(0) + m["rqye"].fillna(0)   # 两融=融资余额+融券余额
                g = m.groupby("trade_date").agg(two=("two", "sum"), rzmre=("rzmre", "sum")).sort_index()
                margin = [{"date": str(idx), "rzye": round(float(r.two) / 1e8, 1), "rzmre": round(float(r.rzmre) / 1e8, 1)}
                          for idx, r in g.iterrows()]
        except Exception:
            margin = []
        return {"ok": True, "valuation": valuation, "turnover": turnover, "holder": holder, "margin": margin, "index": index}
    except Exception:
        import traceback
        return {"ok": False, "error": traceback.format_exc()[-1500:]}


class XiaoxifuReq(BaseModel):
    strategy: str = "leader"     # leader | allweather | industry | regime
    N: int = 20
    K: int = 5
    L: int = 5
    start: str = "2026-01-01"
    end: str | None = None
    codes: list[str] | None = None    # 龙头自定义股票池;None=默认22只


@app.post("/api/xiaoxifu")
def xiaoxifu(req: XiaoxifuReq):
    """小西西弗动量轮动策略复现(龙头/全天候/行业):跑对应模块,返回调仓动作 + 累计收益曲线 + 绩效。"""
    import importlib
    import traceback
    mods = {"leader": "leader_momentum", "allweather": "allweather", "industry": "industry", "regime": "regime_combo"}
    try:
        sys.path.insert(0, os.path.join(_ROOT, "xiaoxifu"))
        m = importlib.import_module(mods.get(req.strategy, "leader_momentum"))
        end = req.end or __import__("datetime").date.today().strftime("%Y-%m-%d")
        kw = dict(n=req.N, start=req.start, end=end)
        if req.strategy != "allweather":
            kw.update(k=req.K, l=req.L)
        if req.strategy in ("leader", "regime") and req.codes:
            kw["codes"] = req.codes
        return m.to_payload(**kw)
    except Exception:
        return {"ok": False, "error": traceback.format_exc()[-1500:]}


@app.get("/api/leader_pool")
def leader_pool():
    """龙头股票池选择器数据:默认池(带 md 行业)+ 全市场个股(带 tushare 行业,供搜索添加)。"""
    import traceback
    try:
        import duckdb
        from cache_tushare import DUCKDB_PATH
        sys.path.insert(0, os.path.join(_ROOT, "xiaoxifu"))
        import leader_momentum as lm
        con = duckdb.connect(DUCKDB_PATH, read_only=True)
        rows = con.execute("""SELECT ts_code, name, industry FROM stock_meta
            WHERE ts_code NOT LIKE '%.BJ' AND (delist_date IS NULL OR delist_date='') ORDER BY ts_code""").fetchall()
        con.close()
        universe = [{"code": c, "name": n, "industry": ind or "其他"} for c, n, ind in rows]
        default = [{"code": c, "name": n, "industry": ind} for c, n, ind in lm.DEFAULT_POOL]
        saved = default
        if os.path.exists(POOL_FILE):
            saved = json.load(open(POOL_FILE, encoding="utf-8"))
        return {"ok": True, "default": default, "universe": universe, "saved": saved}
    except Exception:
        return {"ok": False, "error": traceback.format_exc()[-1500:]}


class PoolSaveReq(BaseModel):
    pool: list[dict]


@app.post("/api/leader_pool_save")
def leader_pool_save(req: PoolSaveReq):
    """把自定义龙头股票池落地到后端文件(非数据库),供跨浏览器/重启后仍可用。"""
    try:
        json.dump(req.pool, open(POOL_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        return {"ok": True, "n": len(req.pool)}
    except Exception:
        import traceback
        return {"ok": False, "error": traceback.format_exc()[-1500:]}


class BollReq(BaseModel):
    pool: str = "ml"
    up: str = "mid"


def _attach_main_concepts(signals):
    """给每个信号股附 main_concepts:该股所属同花顺概念中,当天处于「领先区」主线候选的概念名(回测:领先区为尾部增强,改善区无效故不返回)。返回概念数据日期。"""
    if not signals:
        return None
    import duckdb, cache_tushare as ct
    codes = [s["code"] for s in signals]
    con = duckdb.connect(ct.DUCKDB_PATH, read_only=True)
    try:
        if con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name='concept_signals'").fetchone()[0] == 0:
            return None
        cdate = con.execute("SELECT max(trade_date) FROM concept_signals").fetchone()[0]
        ph = ",".join(["?"] * len(codes))
        rows = con.execute(
            f"""SELECT m.con_code AS stock, cs.name
                FROM ths_member m JOIN concept_signals cs ON cs.ts_code = m.ts_code
                WHERE cs.trade_date = (SELECT max(trade_date) FROM concept_signals)
                  AND cs.main AND cs.quadrant='领先' AND cs.bench='000852.SH' AND cs.up='ma20'
                  AND m.con_code IN ({ph})""", codes).fetchall()
    finally:
        con.close()
    mp = {}
    for stock, name in rows:
        mp.setdefault(stock, []).append(name)
    for s in signals:
        s["main_concepts"] = mp.get(s["code"], [])
    return f"{cdate[:4]}-{cdate[4:6]}-{cdate[6:]}" if cdate else None


@app.post("/api/boll")
def boll(req: BollReq):
    """BOLL缩口扩张+MACD金叉 当日信号(大盘池):返回最新交易日信号 + 第二次标记 + 大盘状态。"""
    import traceback
    try:
        sys.path.insert(0, os.path.join(_ROOT, "boll_narrow_exit"))
        import boll_expand_macd as bem
        r = bem.latest_signals(req.pool, req.up)
        r["concept_date"] = _attach_main_concepts(r.get("signals", []))
        healthy = r.get("mkt_bad") is False
        r["signals"].sort(key=lambda s: (
            not (healthy and s.get("is_rep30") and len(s.get("main_concepts") or [])),  # 重点置顶
            not s.get("is_rep30"), -s.get("vol_ratio", 0)))
        return {"ok": True, **r}
    except Exception:
        return {"ok": False, "error": traceback.format_exc()[-1500:]}


@app.post("/api/boll_history")
def boll_history(req: BollReq):
    """BOLL 综合口径历史信号(全条件)+ 未来10/20日涨幅、15日持有收益。"""
    import traceback
    try:
        sys.path.insert(0, os.path.join(_ROOT, "boll_narrow_exit"))
        import boll_expand_macd as bem
        return {"ok": True, **bem.signal_history(req.pool)}
    except Exception:
        return {"ok": False, "error": traceback.format_exc()[-1500:]}


class ConceptReq(BaseModel):
    bench: str = "000852.SH"
    up: str = "ma20"


@app.post("/api/concept")
def concept(req: ConceptReq):
    """概念轮动:扩散指标 + RRG 四象限组合,返回最新交易日全概念 payload。"""
    import traceback
    try:
        sys.path.insert(0, os.path.join(_ROOT, "concept_rotation"))
        import rrg
        return {"ok": True, **rrg.to_payload(bench=req.bench, up=req.up)}
    except Exception:
        return {"ok": False, "error": traceback.format_exc()[-1500:]}


@app.get("/api/stock_search")
def stock_search(q: str = ""):
    """按代码或名称模糊搜股票(供全站LLM分析悬浮框),返回 [{code,name}]。"""
    q = (q or "").strip()
    if not q:
        return {"ok": True, "items": []}
    import duckdb
    import cache_tushare as ct
    con = duckdb.connect(ct.DUCKDB_PATH, read_only=True)
    try:
        rows = con.execute(
            "SELECT ts_code, name FROM stock_meta WHERE (delist_date IS NULL OR delist_date='') "
            "AND (ts_code LIKE ? OR name LIKE ?) ORDER BY ts_code LIMIT 20",
            [f"%{q}%", f"%{q}%"]).fetchall()
    finally:
        con.close()
    return {"ok": True, "items": [{"code": c, "name": n} for c, n in rows]}


@app.get("/api/peer_status")
def peer_status(code: str = ""):
    """查 peer_cache 是否已有该股同业估值缓存 + 是否含研报可比公司前瞻估值表(供分析弹窗标题tag)。"""
    ts = _norm_code(code)
    p = os.path.join(_ROOT, "peer_cache.duckdb")
    if not ts or not os.path.exists(p):
        return {"ok": True, "cached": False}
    try:
        import duckdb
        con = duckdb.connect(p, read_only=True)
        r = con.execute("SELECT val_table, source, report_date, peers FROM stock_peers WHERE ts_code=?", [ts]).fetchone()
        con.close()
    except Exception:
        return {"ok": True, "cached": False}
    if not r:
        return {"ok": True, "cached": False}
    rp = json.loads(r[0]) if r[0] else []
    peers = json.loads(r[3] or "[]")
    n_rp = len(rp) if isinstance(rp, list) else 0
    return {"ok": True, "cached": True, "source": r[1], "report_date": r[2],
            "n_peers": len(peers), "n_report_peers": n_rp}


@app.get("/api/stock_info")
def stock_info(code: str = ""):
    """个股悬浮卡数据:现价/涨跌/估值(PE/PB/市值/股息)/申万行业/最相关前2概念。"""
    ts = _norm_code(code)
    if not ts:
        return {"ok": False}
    import duckdb
    import cache_tushare as ct
    con = duckdb.connect(ct.DUCKDB_PATH, read_only=True)
    try:
        meta = con.execute("SELECT name FROM stock_meta WHERE ts_code=?", [ts]).fetchone()
        px = con.execute("SELECT close, pct_chg FROM daily WHERE ts_code=? ORDER BY trade_date DESC LIMIT 1", [ts]).fetchone()
        val = con.execute("SELECT pe_ttm, pb, total_mv, dv_ttm FROM daily_basic WHERE ts_code=? ORDER BY trade_date DESC LIMIT 1", [ts]).fetchone()
        sw = con.execute("SELECT l1_name, l2_name, l3_name FROM sw_member WHERE ts_code=? ORDER BY is_new DESC LIMIT 1", [ts]).fetchone()
        cons = con.execute(
            "SELECT i.name FROM ths_member m JOIN ths_index i ON m.ts_code=i.ts_code "
            "WHERE m.con_code=? AND i.count>0 ORDER BY i.count ASC LIMIT 2", [ts]).fetchall()
    finally:
        con.close()
    _r = lambda x, n=2: (round(float(x), n) if x is not None else None)
    return {"ok": True, "code": ts, "name": (meta[0] if meta else ts),
            "price": _r(px[0]) if px else None, "pct_chg": _r(px[1]) if px else None,
            "pe_ttm": _r(val[0], 1) if val else None, "pb": _r(val[1]) if val else None,
            "mv_yi": _r(val[2] / 1e4, 0) if (val and val[2]) else None, "dv": _r(val[3]) if val else None,
            "sw": ("/".join(x for x in sw if x) if sw else None),
            "concepts": [c[0] for c in cons]}


LIANBAN_CACHE = os.path.join(os.path.dirname(__file__), ".lianban_cache")
os.makedirs(LIANBAN_CACHE, exist_ok=True)
_PY312 = os.path.join(_ROOT, ".venv312", "bin", "python")
_FIRST10 = os.path.join(_ROOT, "first10")


def _lianban_py():
    """连板脚本需 xgboost,优先用 .venv312;没有则回退当前解释器。"""
    return _PY312 if os.path.exists(_PY312) else sys.executable


@app.get("/api/lianban/score")
def lianban_score(date: str = "", refresh: bool = False):
    """2进4(到4板)每日打分:按日期缓存,refresh=true 才重跑 ml_score_2lb_v6(约10~20秒)。"""
    key = date or "latest"
    cf = os.path.join(LIANBAN_CACHE, f"score_{key}.json")
    if not refresh and os.path.exists(cf):
        with open(cf, encoding="utf-8") as f:
            return {**json.load(f), "cached": True}
    tmp = os.path.join(LIANBAN_CACHE, f"_tmp_{key}.json")
    cmd = [_lianban_py(), os.path.join(_FIRST10, "ml_score_2lb_v6.py"), "--json-out", tmp]
    if date:
        cmd += ["--date", date]
    try:
        r = subprocess.run(cmd, cwd=_FIRST10, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=300)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "打分超时(>5分钟)"}
    if not os.path.exists(tmp):
        return {"ok": False, "error": "打分失败", "log": (r.stderr or r.stdout or "")[-1500:]}
    with open(tmp, encoding="utf-8") as f:
        data = json.load(f)
    os.remove(tmp)
    with open(cf, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return {**data, "cached": False}


@app.get("/api/lianban/history")
def lianban_history(start: str = "20250101", tier: int = 10, refresh: bool = False):
    """历史 Top档 2板信号 + 最终高度/智能止盈实收/T+7(v6评估版,无穿越);按 start+tier 缓存,较慢(分钟级)。"""
    cf = os.path.join(LIANBAN_CACHE, f"hist_{start}_{tier}.json")
    if not refresh and os.path.exists(cf):
        with open(cf, encoding="utf-8") as f:
            return {**json.load(f), "cached": True}
    tmp = os.path.join(LIANBAN_CACHE, f"_tmph_{start}_{tier}.json")
    cmd = [_lianban_py(), os.path.join(_FIRST10, "screen_2lb_top1_t7.py"), "--json-out", tmp,
           "--start", start, "--tier", str(tier), "--model-version", "v6"]
    try:
        r = subprocess.run(cmd, cwd=_FIRST10, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=600)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "历史扫描超时(>10分钟)"}
    if not os.path.exists(tmp):
        return {"ok": False, "error": "历史扫描失败", "log": (r.stderr or r.stdout or "")[-1500:]}
    with open(tmp, encoding="utf-8") as f:
        data = json.load(f)
    os.remove(tmp)
    with open(cf, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return {**data, "cached": False}


@app.post("/api/lianban/retrain")
def lianban_retrain():
    """重训 2进4 实盘部署模型:缺 v6 评估模型时先自动跑 ml_train_2lb_v6(评估),再跑部署;返回训练指标+日志尾。"""
    py = _lianban_py()
    v6_eval = os.path.join(_FIRST10, "model", "xgb_2lb_v6.pkl")
    log = ""
    eval_ran = False
    if not os.path.exists(v6_eval):
        eval_ran = True
        try:
            re = subprocess.run([py, os.path.join(_FIRST10, "ml_train_2lb_v6.py")],
                                cwd=_FIRST10, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=2400)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "评估模型训练超时(>40分钟)"}
        log += "[评估 ml_train_2lb_v6]\n" + ((re.stdout or "") + (re.stderr or ""))[-1200:] + "\n\n"
        if re.returncode != 0 or not os.path.exists(v6_eval):
            return {"ok": False, "error": "评估模型训练失败", "log": log}
    metrics_tmp = os.path.join(LIANBAN_CACHE, "_retrain_metrics.json")
    if os.path.exists(metrics_tmp):
        os.remove(metrics_tmp)
    try:
        r = subprocess.run([py, os.path.join(_FIRST10, "2lb_model_v6_deploy.py"), "--metrics-out", metrics_tmp],
                           cwd=_FIRST10, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "部署训练超时(>30分钟)", "log": log}
    log += "[部署 2lb_model_v6_deploy]\n" + ((r.stdout or "") + (r.stderr or ""))[-1500:]
    if r.returncode != 0:
        return {"ok": False, "error": "部署训练失败", "log": log}
    metrics = None
    if os.path.exists(metrics_tmp):
        with open(metrics_tmp, encoding="utf-8") as f:
            metrics = json.load(f)
        os.remove(metrics_tmp)
    return {"ok": True, "eval_ran": eval_ran, "metrics": metrics, "log": log[-2500:]}


@app.get("/api/health")
def health():
    return {"ok": True}
