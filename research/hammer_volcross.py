"""下跌趋势锤子(前置) → 量能金叉(量MA5上穿MA20)当信号日,再按「金叉日收阳/站上20线」收紧,对比后续。

结论(2022~2026 全A,弱信号·不推荐单用):
  · 锤子+量能金叉:10日均值从原始锤子的 -0.26% 抬到 +1.07%(量能重聚有确认价值),但胜率仍<50%、
    中位仍负——正期望全靠右尾,且 2026 崩(-2.86%)。
  · 收紧金叉(收阳/站上20线):均值抬到 +1.5%,但胜率反降到46%、中位更负——不是过滤烂的,是换成更极端的赌法。
  · 每年样本仅几十个,均值噪声大,正期望严重依赖 2022/2024;换退潮年失效。
  → 最多是低胜率靠右尾、看行情脸色的弱信号,须叠大环境闸才可能可用。留档。
"""
import duckdb, numpy as np, pandas as pd
from cache_tushare import DUCKDB_PATH

START, STAT_FROM = "2021-01-01", "2022-01-01"
VOLX, BODY_MAX, LOWSH_MIN, LOWSH_BODY, RNG_MIN, MAXWAIT = 2.0, 0.30, 0.50, 2.0, 0.02, 20

con = duckdb.connect(DUCKDB_PATH, read_only=True)
df = con.execute("""
  SELECT d.ts_code, d.trade_date, d.open o, d.high h, d.low l, d.close c, d.vol v, a.adj_factor af
  FROM daily d JOIN adj_factor a ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date
  WHERE d.trade_date>=? ORDER BY d.ts_code, d.trade_date""", [START]).df()
con.close()
df["ac"] = df["c"] * df["af"]

rows = []
for ts, g in df.groupby("ts_code", sort=False):
    if len(g) < 90:
        continue
    o, h, l, c = g["o"].values, g["h"].values, g["l"].values, g["c"].values
    v, ac = g["v"].values, g["ac"].values
    dates = g["trade_date"].values
    ma20 = pd.Series(ac).rolling(20).mean().values
    ma60 = pd.Series(ac).rolling(60).mean().values
    vp = pd.Series(v).rolling(5).mean().shift(1).values
    vma5 = pd.Series(v).rolling(5).mean().values
    vma20 = pd.Series(v).rolling(20).mean().values
    rng, body = h - l, np.abs(c - o)
    lowsh = np.minimum(o, c) - l
    n = len(g); t = 60
    while t < n - 12:
        hammer = (rng[t] > 0 and rng[t] / c[t] >= RNG_MIN and ac[t] < ma20[t] and ac[t] > ma60[t]
                  and vp[t] > 0 and v[t] >= VOLX * vp[t] and body[t] <= BODY_MAX * rng[t]
                  and lowsh[t] >= LOWSH_MIN * rng[t] and lowsh[t] >= LOWSH_BODY * body[t])
        if not hammer:
            t += 1; continue
        sig = None
        for d in range(t + 1, min(t + 1 + MAXWAIT, n - 11)):
            if vma5[d - 1] <= vma20[d - 1] and vma5[d] > vma20[d]:
                sig = d; break
        if sig is not None:
            yang = c[sig] > o[sig]
            above20 = ac[sig] > ma20[sig]
            rows.append((str(pd.Timestamp(dates[sig]).date()), ts, yang, above20,
                         ac[sig + 1] / ac[sig] - 1, ac[sig + 10] / ac[sig] - 1))
            t = sig + 1
        else:
            t += 1

r = pd.DataFrame(rows, columns=["date", "ts", "yang", "above20", "fwd1", "fwd10"])
r = r[r["date"] >= STAT_FROM]


def stat(name, sub):
    if not len(sub):
        print(f"{name}: 无样本"); return
    a1, a10 = sub["fwd1"] * 100, sub["fwd10"] * 100
    print(f"{name:<26} n={len(sub):<5} 次日 {a1.mean():+.2f}%(胜{(a1>0).mean()*100:.0f}%) | "
          f"10日 {a10.mean():+.2f}%(胜{(a10>0).mean()*100:.0f}% 中{a10.median():+.2f}% 涨>3%{(a10>3).mean()*100:.0f}% 跌<-3%{(a10<-3).mean()*100:.0f}%)")


stat("金叉(基线)", r)
stat("金叉+收阳", r[r.yang])
stat("金叉+站上20线", r[r.above20])
stat("金叉+收阳+站上20线", r[r.yang & r.above20])
print("\n按年(金叉+收阳+站上20线):")
best = r[r.yang & r.above20].copy(); best["yr"] = best["date"].str[:4]
for y, gg in best.groupby("yr"):
    print(f"  [{y}] n={len(gg):<4} 10日 {gg['fwd10'].mean()*100:+.2f}%(胜{(gg['fwd10']>0).mean()*100:.0f}%)")
