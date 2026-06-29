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

app = FastAPI(title="ML信号 + LLM分析")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class TrainReq(BaseModel):
    mode: str = "quick"
    tier: int = 5
    start: str = "20250101"
    end: str | None = None
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
    ck = os.path.join(CACHE_DIR, f"train_{req.mode}_{req.tier}_{req.start}_{req.end or 'now'}.json")
    if not req.train and not req.refresh and os.path.exists(ck):
        with open(ck) as f:
            r = json.load(f, parse_constant=lambda *_: None)
        r["cached"] = True
        return r
    out = tempfile.NamedTemporaryFile(suffix=".json", delete=False).name
    cmd = [PY, "run_ml_signals_2026.py", "--mode", req.mode, "--tier", str(req.tier),
           "--start", req.start, "--json", out]
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
        res = {"ok": True, "code": req.code, "date": req.date,
               "market_report": state.get("market_report") or "",
               "news_report": state.get("news_report") or "",
               "verdict": verdict, "risk_decision": risk_decision, "cached": False}
        with open(cf, "w") as f:
            json.dump(res, f, ensure_ascii=False)
        return res
    except Exception as e:
        import traceback
        return {"ok": False, "error": traceback.format_exc()[-2000:]}


@app.get("/api/health")
def health():
    return {"ok": True}
