# research/ — 一次性形态/信号研究脚本(留档,不进前端)

放**探索性、一次性**的统计研究:验证某个 K 线形态/量价信号在 A 股是否有 edge。**结论写在各脚本文件头 docstring**,避免以后重复踩坑。

运行(需 .venv、仓库根在 PYTHONPATH):
```
cd quart && PYTHONPATH=. .venv/bin/python research/<script>.py
```
纯本地读 DuckDB(daily/adj_factor/daily_basic/index_daily/csi2000_members…),部分脚本用 tushare 拉基准指数。

## 已做研究(截至 2026-07)

| 脚本 | 信号 | 结论 |
|---|---|---|
| `hammer_downtrend.py` | 下跌/上升趋势里 放量长下影短实体(锤子) | 否决。跌势接飞刀、涨势见顶;无波段alpha |
| `hammer_volcross.py` | 锤子 → 量能金叉(量MA5上穿MA20)确认 | 弱信号。10日均值转正(+1.07%)但胜率<50%、靠右尾、2026崩;须叠大环境闸 |
| `two_strong_bodies.py` | 连续两根放量大实体阳线 | 否决。次日有短线动能,10日中位深负=情绪顶陷阱;正期望全靠2024 |
| `wyckoff_spring.py` | 威科夫 Spring/二次测试(地量No Supply)+ RVOL1.5-3 需求确认 + 放量突破平台上沿 | 否决。全市场/ML池/中证2000 剥beta后均无正超额;追突破(中证2000)超额中位-3.2%/胜38% |

## 一句话总纲
**纯技术形态(K线/量价/威科夫)在 A 股横截面、剥掉大盘 beta 后,系统性无 alpha,追突破还为负。**
真正的 edge 在 **regime(哪年做)+ 选股模型(哪些票)**——这也是仓库里 swing/first10 那些带模型打分的策略比纯形态强的根本原因。
