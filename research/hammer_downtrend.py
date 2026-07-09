"""放量长下影短实体(锤子)信号 → 次日/10日后涨跌统计。对比:趋势档(下跌/上升)× 是否加次日确认。

结论(2022~2026 全A,已否决):
  · 下跌趋势锤子:次日/10日均值为负、胜率<44%——长下影"下方承接"在跌势里是假的,多为接飞刀/派发。
  · 加次日确认(收阳+站上5日线):只买到隔日1天反抽(次日+0.47%/胜50%),10日又掉回负,拿不住。
  · 上升趋势版:10日 -1.37%、跌超3%达53%——放量长下影出现在涨势=冲高回落/见顶,是卖出信号,可反用作减仓提示。
  → 该形态无波段 alpha,期望被单一年份 beta 绑架。留档防重复踩坑。
"""
import duckdb, numpy as np, pandas as pd
from cache_tushare import DUCKDB_PATH

START = "2021-01-01"
STAT_FROM = "2022-01-01"
VOLX = 2.0
BODY_MAX = 0.30
LOWSH_MIN = 0.50
LOWSH_BODY = 2.0
RNG_MIN = 0.02

con = duckdb.connect(DUCKDB_PATH, read_only=True)
df = con.execute("""
  SELECT d.ts_code, d.trade_date, d.open o, d.high h, d.low l, d.close c, d.vol v, a.adj_factor af
  FROM daily d JOIN adj_factor a ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date
  WHERE d.trade_date>=? ORDER BY d.ts_code, d.trade_date""", [START]).df()
con.close()
df["ac"] = df["c"] * df["af"]

rows = []
for ts, g in df.groupby("ts_code", sort=False):
    if len(g) < 80:
        continue
    o, h, l, c = g["o"].values, g["h"].values, g["l"].values, g["c"].values
    v, ac = g["v"].values, g["ac"].values
    dates = g["trade_date"].values
    ma5 = pd.Series(ac).rolling(5).mean().values
    ma20 = pd.Series(ac).rolling(20).mean().values
    ma60 = pd.Series(ac).rolling(60).mean().values
    vma5 = pd.Series(v).rolling(5).mean().shift(1).values
    rng = h - l
    body = np.abs(c - o)
    lowsh = np.minimum(o, c) - l
    n = len(g)
    for t in range(60, n - 12):
        if rng[t] <= 0 or rng[t] / c[t] < RNG_MIN:
            continue
        if not (vma5[t] > 0 and v[t] >= VOLX * vma5[t]):
            continue
        if not (body[t] <= BODY_MAX * rng[t] and lowsh[t] >= LOWSH_MIN * rng[t] and lowsh[t] >= LOWSH_BODY * body[t]):
            continue
        down = ac[t] < ma20[t] and ac[t] > ma60[t]      # 下跌趋势:20下60上
        up = ac[t] > ma20[t] and ac[t] > ma60[t]        # 上升趋势:20上60上
        if not (down or up):
            continue
        trend = "下跌" if down else "上升"
        # 次日确认:收阳 且 站上5日线
        confirm = (c[t + 1] > o[t + 1]) and (ac[t + 1] > ma5[t + 1])
        # 无确认:t 收盘进;有确认:t+1 收盘进
        e0 = t
        f1_0 = ac[t + 1] / ac[t] - 1
        f10_0 = ac[t + 10] / ac[t] - 1
        e1 = t + 1
        f1_1 = ac[t + 2] / ac[t + 1] - 1
        f10_1 = ac[t + 11] / ac[t + 1] - 1
        rows.append((str(pd.Timestamp(dates[t]).date()), ts, trend, confirm,
                     f1_0, f10_0, f1_1, f10_1))

r = pd.DataFrame(rows, columns=["date", "ts", "trend", "confirm", "f1_0", "f10_0", "f1_1", "f10_1"])
r = r[r["date"] >= STAT_FROM]


def stat(label, sub, c1, c10):
    a1 = sub[c1].dropna() * 100
    a10 = sub[c10].dropna() * 100
    if not len(a1):
        print(f"{label}: 无样本")
        return
    print(f"{label}  n={len(sub)}")
    print(f"    次日   均 {a1.mean():+.2f}%  中 {a1.median():+.2f}%  胜 {(a1>0).mean()*100:.1f}%")
    print(f"    10日后 均 {a10.mean():+.2f}%  中 {a10.median():+.2f}%  胜 {(a10>0).mean()*100:.1f}%  涨>3% {(a10>3).mean()*100:.0f}%  跌<-3% {(a10<-3).mean()*100:.0f}%")


print("========= 基线:原始(无确认,信号日收盘进) =========")
stat("[下跌趋势·无确认]", r[r.trend == "下跌"], "f1_0", "f10_0")
stat("[上升趋势·无确认] (B)", r[r.trend == "上升"], "f1_0", "f10_0")

print("\n========= A: 加次日确认(收阳+站上5日线,确认日收盘进) =========")
stat("[下跌趋势·加确认] (A)", r[(r.trend == "下跌") & r.confirm], "f1_1", "f10_1")
stat("[上升趋势·加确认] (A+B)", r[(r.trend == "上升") & r.confirm], "f1_1", "f10_1")

print("\n确认命中率:下跌 {:.0f}%  上升 {:.0f}%".format(
    r[r.trend == "下跌"].confirm.mean() * 100, r[r.trend == "上升"].confirm.mean() * 100))
