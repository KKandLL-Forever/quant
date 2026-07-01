"""app.py — ML信号 + LLM分析 的后端(FastAPI)。

前端"训练模型"按钮 → POST /api/train → 后端子进程跑 run_ml_signals_2026.py --json → 回结构化数据;
前端某行点"LLM分析" → POST /api/analyze → 后端跑 ta_analyze(技术+消息面)→ 回报告 + 分析师层判断。

环境：.venv312。用法:cd swing/webapp/backend && uvicorn app:app --reload --port 8000
依赖:fastapi/uvicorn;复用 swing/run_ml_signals_2026.py(--json)、swing/ta_analyze.py。
"""

import json
import os
import subprocess
import sys
import tempfile

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

SWING = os.path.expanduser("~/AI/quart/swing")
PY = os.path.expanduser("~/AI/quart/.venv312/bin/python")
sys.path.insert(0, SWING)
sys.path.insert(0, os.path.expanduser("~/AI/quart"))

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


CACHE_DIR = os.path.join(os.path.dirname(__file__), ".analyze_cache")
os.makedirs(CACHE_DIR, exist_ok=True)


@app.post("/api/train")
def train(req: TrainReq):
    """跑 ML 信号管线,返回 {signals, banner, cal, latest, ntrade, ...}。train=True 重训模型。"""
    ck = os.path.join(CACHE_DIR, f"train_{req.mode}_{req.tier}_n{req.n}_{req.start}_{req.end or 'now'}.json")
    if not req.train and not req.refresh and os.path.exists(ck):
        with open(ck) as f:
            r = json.load(f, parse_constant=lambda *_: None)
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
        p = subprocess.run(cmd, cwd=SWING, capture_output=True, text=True, timeout=1800)
        if not os.path.exists(out) or os.path.getsize(out) == 0:
            return {"ok": False, "error": "ML 未产出数据。stderr:\n" + (p.stderr or p.stdout or "")[-2000:]}
        with open(out) as f:
            payload = json.load(f, parse_constant=lambda *_: None)
        os.unlink(out)
        payload["ok"] = True
        payload["cached"] = False
        with open(ck, "w") as f:
            json.dump(payload, f, ensure_ascii=False)
        return payload
    except Exception as e:
        import traceback
        return {"ok": False, "error": traceback.format_exc()[-2000:]}


@app.post("/api/analyze")
def analyze(req: AnalyzeReq):
    """对单只股票在指定日期跑 技术+消息面 LLM 分析,返回报告 + 分析师层买卖持判断。"""
    cf = os.path.join(CACHE_DIR, f"{req.code.split('.')[0]}_{req.date}.json")
    if not req.force and os.path.exists(cf):
        with open(cf) as f:
            r = json.load(f)
        r["cached"] = True
        return r
    try:
        import ta_analyze
        ta_analyze._load_keys()
        state, risk_decision = ta_analyze.analyze(req.code, req.date)
        verdict = ta_analyze.analyst_verdict(state)
        bf = os.path.join(CACHE_DIR, f"biz_{req.code.split('.')[0]}.json")
        if os.path.exists(bf):
            business = json.load(open(bf))
        else:
            business = ta_analyze.business_profile(req.code)
            json.dump(business, open(bf, "w"), ensure_ascii=False)
        res = {"ok": True, "code": req.code, "date": req.date,
               "market_report": state.get("market_report") or "",
               "news_report": state.get("news_report") or "",
               "verdict": verdict, "business": business, "risk_decision": risk_decision, "cached": False}
        with open(cf, "w") as f:
            json.dump(res, f, ensure_ascii=False)
        return res
    except Exception as e:
        import traceback
        return {"ok": False, "error": traceback.format_exc()[-2000:]}


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
        ohlc = [[str(r.td.date()), round(r.o, 2), round(r.h, 2), round(r.l, 2), round(r.c, 2)] for r in g.itertuples()]
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
    pe = os.path.expanduser("~/AI/quart/.pyenv.local")
    if not tok and os.path.exists(pe):
        for line in open(pe):
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
    pe = os.path.expanduser("~/AI/quart/.pyenv.local")
    if not tok and os.path.exists(pe):
        for line in open(pe):
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
        ohlc = [[str(r.td.date()), round(r.o, 2), round(r.h, 2), round(r.l, 2), round(r.c, 2)] for r in w.itertuples()]
        buy_td = str(g["td"].iloc[bi_global].date())
        marks = [{"date": d(b), "kind": "buy", "label": "买" if b == bi_global else "补"} for b in legs_buy]
        for sidx in legs_sell:
            marks.append({"date": d(sidx), "kind": "sell", "label": "缠"})
        return {"ok": True, "code": ts, "name": name[0] if name else "", "bo": buy_td,
                "entry": round(entry, 2), "ohlc": ohlc, "bis": bis, "zs": zs, "marks": marks, "advice": advice}
    except Exception:
        import traceback
        return {"ok": False, "error": traceback.format_exc()[-1500:]}


@app.get("/api/health")
def health():
    return {"ok": True}
