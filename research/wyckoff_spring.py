"""威科夫 Spring/二次测试(地量No Supply) + 需求确认(RVOL1.5-3) 两阶段信号 → 后续统计。
测试根:平台整理中触支撑、极窄波幅、地量(近10日量新低且RVOL20<1)。
信号根:测试后 CONFIRM_WIN 日内 RVOL20∈[1.5,3]、收阳、且收盘站上平台上沿(SOS真突破) → 入场。
统计:前向5/10/20/60日收益、超额(减基准指数)、前向20日最大回撤;分年+中位。
池/基准可切:代码里改 pool 取数(全市场/ML池top800/中证2000成分)与 bench 指数。

结论(2022~2026,已否决):
  · 全市场 & ML主升池:绝对收益随周期走高但全是 beta+2024;剥基准后超额≈0、中位负、胜率<50%,且偏防御(熊年超额正、牛年负)。
  · 中证2000 + 放量突破平台上沿(本文件当前口径,CONFIRM_WIN=25):超额反而最负——20日超额中位 -3.19%、胜率仅38%,
    60日绝对中位也翻负。等放量站上平台上沿,鱼身已走完,追突破=接最后一棒,小盘尤甚。
  → 逻辑最扎实的威科夫形态,在A股横截面剥beta后仍无正超额,追突破为负。印证:alpha 在 regime+选股模型,不在K线形态。留档。
"""
import duckdb, numpy as np, pandas as pd
from cache_tushare import DUCKDB_PATH

START, STAT_FROM = "2021-01-01", "2022-01-01"
R = 40            # 平台回看窗口
PLAT_W = 0.25     # 平台振幅上限
TOUCH = 1.01      # 触支撑:低 ≤ 支撑×1.01
NARROW = 0.025    # 极窄波幅:振幅/收盘 ≤ 2.5%
VLOWN = 10        # 地量:近10日量新低
CONFIRM_WIN = 25  # 测试后确认窗口
RVOL_LO, RVOL_HI = 1.5, 3.0

con = duckdb.connect(DUCKDB_PATH, read_only=True)
pool = [r[0] for r in con.execute(
    "SELECT DISTINCT con_code FROM csi2000_members WHERE trade_date=(SELECT MAX(trade_date) FROM csi2000_members)").fetchall()]
df = con.execute("""
  SELECT d.ts_code, d.trade_date, d.open o, d.high h, d.low l, d.close c, d.vol v, a.adj_factor af
  FROM daily d JOIN adj_factor a ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date
  WHERE d.trade_date>=? AND d.ts_code IN (SELECT UNNEST(?)) ORDER BY d.ts_code, d.trade_date""", [START, pool]).df()
con.close()
import tushare as ts
from db_loader import _ENV
pro = ts.pro_api(_ENV["TUSHARE_TOKEN"])
bparts = [pro.index_daily(ts_code="932000.CSI", start_date=f"{y}0101", end_date=f"{y}1231", fields="trade_date,close") for y in range(2021, 2027)]
bench = pd.concat([b for b in bparts if b is not None and len(b)])
bm = {f"{d[:4]}-{d[4:6]}-{d[6:8]}": c for d, c in zip(bench["trade_date"], bench["close"])}
print(f"中证2000池 {len(pool)} 只 · 基准 中证2000 · bench天数 {len(bm)}")
for col in ("o", "h", "l", "c"):
    df["a" + col] = df[col] * df["af"]

rows = []
for ts, g in df.groupby("ts_code", sort=False):
    if len(g) < R + 70:
        continue
    ao, ah, al, ac = g["ao"].values, g["ah"].values, g["al"].values, g["ac"].values
    v = g["v"].values
    dts = [str(pd.Timestamp(d).date()) for d in g["trade_date"].values]
    supp = pd.Series(al).rolling(R).min().shift(1).values
    hmax = pd.Series(ah).rolling(R).max().shift(1).values
    width = (hmax - supp) / supp
    vma20 = pd.Series(v).rolling(20).mean().shift(1).values
    rvol = v / vma20
    vmin10 = pd.Series(v).rolling(VLOWN).min().values
    rng = ah - al
    n = len(g); s = R
    while s < n - 61:
        test = (supp[s] > 0 and width[s] <= PLAT_W and al[s] <= supp[s] * TOUCH
                and rng[s] / ac[s] <= NARROW and vmin10[s] == v[s]
                and vma20[s] > 0 and rvol[s] < 1)
        if not test:
            s += 1; continue
        sig = None
        for t in range(s + 1, min(s + 1 + CONFIRM_WIN, n - 60)):
            if RVOL_LO <= rvol[t] <= RVOL_HI and ac[t] > ao[t] and ac[t] > hmax[s]:   # 放量突破平台上沿(SOS)
                sig = t; break
        if sig is None:
            s += 1; continue
        e = sig
        def fwd(h): return ac[e + h] / ac[e] - 1
        def bfwd(h):
            b0, b1 = bm.get(dts[e]), bm.get(dts[e + h])
            return None if (b0 is None or b1 is None) else b1 / b0 - 1
        mdd20 = float(np.min(ac[e + 1:e + 21]) / ac[e] - 1)
        rows.append((dts[e], ts, fwd(5), fwd(10), fwd(20), fwd(60), bfwd(10), bfwd(20), mdd20))
        s = sig + 1

r = pd.DataFrame(rows, columns=["date", "ts", "f5", "f10", "f20", "f60", "b10", "b20", "mdd20"])
r = r[r["date"] >= STAT_FROM]
r["ex10"] = r["f10"] - r["b10"]
r["ex20"] = r["f20"] - r["b20"]
print(f"威科夫Spring+确认 信号数(≥{STAT_FROM}): {len(r)}")


def stat(name, a):
    a = a.dropna() * 100
    if not len(a): print(f"  {name}: 无"); return
    print(f"  {name:<10} n={len(a):<5} 均 {a.mean():+.2f}%  中 {a.median():+.2f}%  胜 {(a>0).mean()*100:.0f}%")


print("=== 绝对收益 ===")
for h, col in [("5日", "f5"), ("10日", "f10"), ("20日", "f20"), ("60日", "f60")]:
    stat(h, r[col])
print("=== 超额(减中证2000) ===")
stat("10日超额", r["ex10"]); stat("20日超额", r["ex20"])
print(f"=== 前向20日最大回撤 均 {r['mdd20'].mean()*100:.2f}%  中 {r['mdd20'].median()*100:.2f}% ===")
print("=== 分年(20日绝对 / 20日超额) ===")
r["yr"] = r["date"].str[:4]
for y, gg in r.groupby("yr"):
    print(f"  [{y}] n={len(gg):<4} 20日 {gg['f20'].mean()*100:+.2f}%(胜{(gg['f20']>0).mean()*100:.0f}% 中{gg['f20'].median()*100:+.2f}%)  "
          f"超额 {gg['ex20'].mean()*100:+.2f}%")
