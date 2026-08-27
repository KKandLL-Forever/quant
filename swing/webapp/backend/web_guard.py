"""web_guard.py — 外网访问守卫:口令验证 + 高成本接口禁用。

本机 / 局域网访问完全不受影响(不弹口令、功能全开)。经隧道(cloudflared / ngrok)进来的请求判定为外网:
先过口令页(口令读 swing/webapp/.webpass 第一行,第二行是给访客看的提示;也可用环境变量 QUART_WEB_PASS 覆盖;
两处都没有则一律拒绝进入),通过后只开放只读页面 —
烧钱的(LLM 分析)、吃 CPU 的(训练 / 重新打分)、改数据的(保存)接口一律 403;
只读接口里凡是「没缓存就要起子进程」的(/api/train、连板打分),外网只吃现成缓存,算不出就提示找站主。

判定依据:Host 头不是 localhost / 私网地址,或带 x-forwarded-for / cf-connecting-ip(隧道必然会加)。
用法:app.py 里 app.add_middleware(GuardMiddleware)。口令改了不用重启,下一条请求就生效。
"""

import hashlib
import hmac
import ipaddress
import os
import time

from fastapi.responses import HTMLResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

COOKIE = "qt_auth"
COOKIE_MAX_AGE = 12 * 3600
_HERE = os.path.dirname(os.path.abspath(__file__))
LOGIN_HTML = os.path.join(_HERE, "login.html")
PASS_FILE = os.path.join(os.path.dirname(_HERE), ".webpass")

BLOCKED = {
    "/api/analyze/start": "LLM 个股分析",
    "/api/analyze/stream": "LLM 个股分析",
    "/api/analyze_cancel": "LLM 个股分析",
    "/api/lianban/retrain": "连板模型重训",
    "/api/mood_temp_save": "修改情绪温度",
    "/api/leader_pool_save": "修改龙头股票池",
}
NO_REFRESH = {"/api/lianban/score", "/api/lianban/history"}   # 只读但 refresh=true 会重跑脚本
OPEN = {"/api/health", "/api/login"}

MAX_TRIES, TRY_WINDOW = 8, 600
_tries: dict[str, list[float]] = {}


def is_external(request) -> bool:
    """判断这条请求是不是从外网(隧道)进来的;本机与局域网都算内网。"""
    if request.headers.get("x-forwarded-for") or request.headers.get("cf-connecting-ip"):
        return True
    host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]").lower()
    if host in ("", "localhost", "127.0.0.1", "::1"):
        return False
    try:
        return not ipaddress.ip_address(host).is_private
    except ValueError:
        return True


def _secret() -> tuple[str, str]:
    """取(口令, 提示):环境变量 QUART_WEB_PASS 优先,否则读 .webpass(第一行口令、第二行提示)。都没有则口令为空。"""
    pw = os.environ.get("QUART_WEB_PASS", "").strip()
    if pw:
        return pw, os.environ.get("QUART_WEB_HINT", "").strip()
    try:
        with open(PASS_FILE, encoding="utf-8") as f:
            lines = [ln.strip() for ln in f.read().splitlines() if ln.strip()]
        return (lines[0], lines[1] if len(lines) > 1 else "")
    except (OSError, IndexError):
        return "", ""


def _token() -> str:
    """由口令算出 cookie 值;口令未配置时返回空串,表示外网一律不放行。"""
    pw = _secret()[0]
    return hmac.new(pw.encode(), b"quart-web", hashlib.sha256).hexdigest() if pw else ""


def _authed(request) -> bool:
    tok = _token()
    return bool(tok) and hmac.compare_digest(request.cookies.get(COOKIE, ""), tok)


def _client(request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?")


def _rate_limited(ip: str) -> bool:
    """同一来源 10 分钟内最多试 8 次口令,防爆破。"""
    now = time.time()
    hits = [t for t in _tries.get(ip, []) if now - t < TRY_WINDOW]
    hits.append(now)
    _tries[ip] = hits
    return len(hits) > MAX_TRIES


def _login_page(msg: str = "") -> HTMLResponse:
    with open(LOGIN_HTML, encoding="utf-8") as f:
        html = f.read()
    return HTMLResponse(html.replace("{{MSG}}", msg).replace("{{HINT}}", _secret()[1]), status_code=401)


class GuardMiddleware(BaseHTTPMiddleware):
    """外网请求:先验口令,再按名单禁用高成本接口;内网请求原样放行。"""

    async def dispatch(self, request, call_next):
        if not is_external(request):
            return await call_next(request)

        request.state.external = True   # 下游据此只吃缓存,不起打分/训练子进程
        path = request.url.path
        if path == "/api/login" and request.method == "POST":
            return await self._login(request)
        if path in OPEN:
            return await call_next(request)

        if not _authed(request):
            if path.startswith("/api/"):
                return JSONResponse({"ok": False, "error": "未验证:请回首页输入访问口令"}, status_code=401)
            return _login_page("公开访问需要口令" if _token() else "本站未开启外网访问")

        if path in BLOCKED:
            return JSONResponse(
                {"ok": False, "error": f"外网访问下「{BLOCKED[path]}」已停用(这项会消耗 API 额度或占满 CPU),请找站主在本机操作"},
                status_code=403)
        if path in NO_REFRESH and b"refresh=true" in request.scope.get("query_string", b""):
            request.scope["query_string"] = request.scope["query_string"].replace(b"refresh=true", b"refresh=false")
        return await call_next(request)

    async def _login(self, request):
        """校验口令,通过则种 12 小时 cookie。"""
        ip = _client(request)
        if _rate_limited(ip):
            return JSONResponse({"ok": False, "error": "尝试过于频繁,请 10 分钟后再试"}, status_code=429)
        tok = _token()
        if not tok:
            return JSONResponse({"ok": False, "error": "本站未开启外网访问"}, status_code=403)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "请求格式不对"}, status_code=400)
        pw, hint = _secret()
        got = str(body.get("pass", "")).strip()
        # WHY: compare_digest 不接受非 ASCII 的 str,中文口令必须转 bytes 再比
        ok = hmac.compare_digest(got.encode(), pw.encode())
        if hint:      # 顺手认「提示+答案」的完整写法,免得访客照着念全句反而被判错
            ok = ok or hmac.compare_digest(got.encode(), (hint + pw).encode())
        if not ok:
            return JSONResponse({"ok": False, "error": "答错了,再想想"}, status_code=401)
        _tries.pop(ip, None)
        r = JSONResponse({"ok": True})
        https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
        r.set_cookie(COOKIE, tok, max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax", secure=https)
        return r
