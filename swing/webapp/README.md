# ML 信号 + LLM 分析 webapp

前后端项目:前端点"训练模型"→ 后端跑 `run_ml_signals_2026.py` 出信号 → 表格/卡片/组合曲线;
某行点"分析"→ 后端跑 `ta_analyze`(技术面+消息面 LLM)→ 弹窗显示报告 + 分析师层买/卖/持判断。

## 结构
```
webapp/
├── backend/app.py        FastAPI:/api/train(子进程跑ML出JSON) /api/analyze(LLM分析)
└── frontend/             Vite+React+antd:训练按钮、表格、卡片、组合曲线、分析弹窗
```

## 运行(开两个终端)

> 仓库根目录:macOS 是 `~/AI/quart`,Windows 是 `D:\code\quart`。
> Python 环境:macOS 用 venv `.venv312`;Windows 直接用系统 Python `C:\Python312`(依赖用 `pip install --user` 装,无 venv)。

**① 后端**

macOS / Linux:
```bash
cd ~/AI/quart && source .venv312/bin/activate
cd swing/webapp/backend && uvicorn app:app --reload --port 18000
```

Windows(PowerShell):
```powershell
cd D:\code\quart\swing\webapp\backend
python -m uvicorn app:app --reload --port 18000
```

**② 前端**

macOS / Linux:
```bash
cd ~/AI/quart/swing/webapp/frontend && npm run dev
# 打开 http://localhost:5173
```

Windows(PowerShell):
```powershell
cd D:\code\quart\swing\webapp\frontend
npm run dev
# 打开 http://localhost:5173
```

## 用法
- 选 模式(quick/long)/档位/起始日 → 点「训练模型/出信号」(勾"重新训练"才重训模型,否则加载已存盘模型,快)。
- 表格每行「分析」→ 对该股在突破日跑 技术+消息面 LLM(约 1-3 分钟),弹窗给报告 + **分析师层判断**(趋势感知,已绕开 TA-CN 风险经理的恢高偏置)。

## 依赖/注意
- 后端复用 `swing/run_ml_signals_2026.py --json` 和 `swing/ta_analyze.py`;DeepSeek key 读 x2 的 .env、tushare token 读 .pyenv.local。
- `/api/analyze` 每次跑完整多 agent(慢、耗 token),建议只点你真正要决策的票,别全表点。
- 前端 `/api` 经 vite proxy 转到 :18000,所以前端只调 `/api/*`。改后端端口要同步改 `frontend/vite.config.js`。
- 后端端口用 18000 不用 8000:Windows 的 TCP 动态端口范围是 1024–14999,8000 会被别的程序(如 Clash/verge-mihomo)当临时源端口占走,再 bind 就报 `WinError 10013`。
  排查:`netstat -ano | findstr :8000` 找到占用 PID,`netsh int ipv4 show dynamicport tcp` 看动态范围。
