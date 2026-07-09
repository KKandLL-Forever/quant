"""连续两根 放量(≥2×20日均量)大实体无下影K线(两根都收阳版更严),以第二根为信号 → 统计后续。

结论(2022~2026 全A,已否决):
  · 次日有短线动能(两根都收阳次日+1.30%),但胜率仍<50%,靠右尾。
  · 10日基本白做且是陷阱:中位数深负(-2.15%)、跌超5%(39%)远多于涨超5%(27%)——放量双阳=情绪高点,追进多被套。
  · 正期望几乎全来自 2024(次日+4.41%/胜61%),其余年份10日全负或零。
  → 无波段 alpha,最多 T+1 快进快出的动能玩法。留档。
"""
import duckdb, numpy as np, pandas as pd
from cache_tushare import DUCKDB_PATH

START, STAT_FROM = "2021-01-01", "2022-01-01"
VOLX20 = 2.0        # 量 ≥ 2×20日均量
BODY_MIN = 0.70     # 实体 ≥ 70% 全幅
LOWSH_MAX = 0.10    # 下影 ≤ 10% 全幅
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
    if len(g) < 40:
        continue
    o, h, l, c = g["o"].values, g["h"].values, g["l"].values, g["c"].values
    v, ac = g["v"].values, g["ac"].values
    dates = g["trade_date"].values
    vma20 = pd.Series(v).rolling(20).mean().shift(1).values
    rng, body = h - l, np.abs(c - o)
    lowsh = np.minimum(o, c) - l
    n = len(g)

    def ok(i):
        return (rng[i] > 0 and rng[i] / c[i] >= RNG_MIN and vma20[i] > 0 and v[i] >= VOLX20 * vma20[i]
                and body[i] >= BODY_MIN * rng[i] and lowsh[i] <= LOWSH_MAX * rng[i])

    for t in range(21, n - 11):
        if ok(t) and ok(t - 1):
            both_yang = c[t] > o[t] and c[t - 1] > o[t - 1]
            rows.append((str(pd.Timestamp(dates[t]).date()), ts, both_yang,
                         ac[t + 1] / ac[t] - 1, ac[t + 10] / ac[t] - 1))

r = pd.DataFrame(rows, columns=["date", "ts", "yang", "fwd1", "fwd10"])
r = r[r["date"] >= STAT_FROM]


def stat(name, sub):
    if not len(sub):
        print(f"{name}: 无样本"); return
    a1, a10 = sub["fwd1"] * 100, sub["fwd10"] * 100
    print(f"{name:<18} n={len(sub):<6} 次日 {a1.mean():+.2f}%(胜{(a1>0).mean()*100:.0f}%) | "
          f"10日 {a10.mean():+.2f}%(胜{(a10>0).mean()*100:.0f}% 中{a10.median():+.2f}% 涨>5%{(a10>5).mean()*100:.0f}% 跌<-5%{(a10<-5).mean()*100:.0f}%)")


stat("两根(不限阴阳)", r)
stat("两根都收阳", r[r.yang])
print("\n按年(两根都收阳):")
b = r[r.yang].copy(); b["yr"] = b["date"].str[:4]
for y, gg in b.groupby("yr"):
    print(f"  [{y}] n={len(gg):<5} 次日 {gg['fwd1'].mean()*100:+.2f}%(胜{(gg['fwd1']>0).mean()*100:.0f}%)  "
          f"10日 {gg['fwd10'].mean()*100:+.2f}%(胜{(gg['fwd10']>0).mean()*100:.0f}%)")
