# 临时把 webapp 分享到外网

不用租服务器。自己电脑当服务器，用隧道给一个临时 https 域名，关掉即失效。

## 三步

```bash
# 1. 打包前端(改过前端才需要重跑)
cd swing/webapp/frontend && npm run build

# 2. 起后端(会自动托管 dist,一个端口同时供页面和 API)
cd ../backend
set QUART_WEB_PASS=你自己定的口令        # PowerShell: $env:QUART_WEB_PASS="..."
python -m uvicorn app:app --port 18000

# 3. 开隧道,把打印出来的 https 链接发给人
cloudflared tunnel --url http://localhost:18000
```

`cloudflared` 没装：`winget install Cloudflare.cloudflared`。不想装也行，本机已有 ngrok：
`ngrok http 18000`（免费版访客首次会先看到一个警告页，要点 Visit Site）。

**Ctrl+C 关掉隧道，链接立刻失效。** 不用清理别的东西。

## 外网访客看到什么

- 先弹口令页（`backend/login.html`），输对了种 12 小时 cookie，之后正常浏览
- 口令错 8 次锁 10 分钟
- **`QUART_WEB_PASS` 没设 = 外网一律进不来**（默认关着，忘了设不会裸奔）

## 外网被停掉的功能（`backend/web_guard.py`）

| 类型 | 接口 | 原因 |
|---|---|---|
| 烧钱 | `/api/analyze/*` | 每次调用消耗 DeepSeek API 额度 |
| 吃 CPU | `/api/lianban/retrain` | 起子进程重训模型 |
| 改数据 | `/api/mood_temp_save`、`/api/leader_pool_save` | 会改你本地文件 |

另有两处降级为「只读缓存」，不报错但也不起子进程：

- `/api/train`：只返回你已经跑过的参数组合；换成没跑过的组合会提示找站主
- `/api/lianban/score`、`/api/lianban/history`：`refresh=true` 被改写成 `false`

## 内网不受影响

本机（localhost）和局域网（192.168.x.x 之类）访问**完全不变**：不弹口令、功能全开。
判定看 Host 头和 `x-forwarded-for`——隧道必然会带这个头，本机直连不会。

`/api/health` 会回 `external: true/false`，前端要隐藏写操作入口可以据此判断。

## 还没做的

前端没有按 `external` 隐藏按钮，所以访客仍能看到「重新训练」「LLM 分析」这些入口，
点了会弹一句「外网访问下 XX 已停用」。功能上拦住了，只是不够干净。
