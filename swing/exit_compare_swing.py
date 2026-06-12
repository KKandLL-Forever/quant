"""
exit_compare_swing.py — 波段策略「出场规则」对比实验

固定选股层（当前 xgb_swing_v2 模型选 Top10%，测试集 2024-01-01 起），
对同一批选票拉出买入后 60 个交易日的后复权价格路径，并行模拟多种出场规则，
对比各自的真实收益分布与分年表现，找出能压低中位亏损、保住右尾赢家的出场。

进场统一：信号次日 T+1 开盘买（后复权 aopen）。
出场规则：
  ma10_break  收盘跌破 MA10 → 次日开盘卖（当前基线）
  ma5_break   收盘跌破 MA5  → 次日开盘卖
  bracket     固定止损 -5% / 止盈 +15%（盘中触及，止损优先），按触发价成交
  trail8      持有期最高收盘回落 8% → 次日开盘卖
  hold5       持有 5 个交易日，第 6 日开盘卖
  hold10      持有 10 个交易日，第 11 日开盘卖
均以 60 日为上限，未触发则末日开盘卖。

用法：python swing/exit_compare_swing.py [--tier 10]
依赖：swing/model/xgb_swing_v2.pkl、swing/model/feature_matrix_swing_v2.csv、daily、adj_factor
"""

import argparse
import os
import pickle
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import duckdb as _duckdb
from db_loader import _ENV

_DUCKDB_PATH = _ENV.get("LOCAL_DUCKDB_PATH", "stock_data_tushare.duckdb")
_MODEL = os.path.join(os.path.dirname(__file__), "model", "xgb_swing_v2.pkl")
_FEAT = os.path.join(os.path.dirname(__file__), "model", "feature_matrix_swing_v2.csv")

TEST_START = 20240101
EXIT_WINDOW = 60
STOP, TAKE = -0.05, 0.15
TRAIL = 0.08
BREADTH_THR = 0.50

_BREADTH_SQL = """
WITH b AS (
  SELECT trade_date,
         AVG(CASE WHEN close >= ma20 THEN 1.0 ELSE 0.0 END) AS breadth
  FROM (
    SELECT ts_code, trade_date, close,
           AVG(close) OVER (PARTITION BY ts_code ORDER BY trade_date
                            ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS ma20
    FROM daily WHERE ts_code NOT LIKE '%.BJ'
  )
  GROUP BY trade_date
)
SELECT strftime(trade_date, '%Y%m%d') AS d, breadth,
       AVG(breadth) OVER (ORDER BY trade_date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) AS breadth_ma5
FROM b
"""

_PATH_SQL = """
WITH dall AS (
  SELECT d.ts_code, d.trade_date,
    d.open * af.adj_factor AS aopen,
    d.high * af.adj_factor AS ahigh,
    d.low  * af.adj_factor AS alow,
    d.close * af.adj_factor AS aclose,
    ROW_NUMBER() OVER w AS seq,
    AVG(d.close * af.adj_factor) OVER w5  AS ma5,
    AVG(d.close * af.adj_factor) OVER w10 AS ma10
  FROM daily d JOIN adj_factor af USING (ts_code, trade_date)
  WINDOW
    w   AS (PARTITION BY d.ts_code ORDER BY d.trade_date),
    w5  AS (PARTITION BY d.ts_code ORDER BY d.trade_date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW),
    w10 AS (PARTITION BY d.ts_code ORDER BY d.trade_date ROWS BETWEEN 9 PRECEDING AND CURRENT ROW)
),
sd AS (
  SELECT p.ts_code, p.sig_date, d.seq AS sig_seq
  FROM picks p JOIN dall d ON d.ts_code = p.ts_code AND d.trade_date = p.sig_date
)
SELECT
  sd.ts_code, strftime(sd.sig_date, '%Y%m%d') AS sig_date,
  f.seq - sd.sig_seq AS off,
  f.aopen, f.ahigh, f.alow, f.aclose, f.ma5, f.ma10
FROM sd JOIN dall f ON f.ts_code = sd.ts_code
                   AND f.seq BETWEEN sd.sig_seq + 1 AND sd.sig_seq + {win}
ORDER BY sd.ts_code, sd.sig_date, off
"""


def _exit_ret(g: pd.DataFrame, rule: str):
    """对单笔的买入后路径 g（按 off 升序），按 rule 返回 (ret, hold_days)。"""
    buy = g["aopen"].iloc[0]
    if not buy or buy <= 0:
        return np.nan, np.nan
    aclose = g["aclose"].to_numpy()
    aopen = g["aopen"].to_numpy()
    ahigh = g["ahigh"].to_numpy()
    alow = g["alow"].to_numpy()
    ma5 = g["ma5"].to_numpy()
    ma10 = g["ma10"].to_numpy()
    n = len(g)

    def sell_next_open(i):
        return aopen[i + 1] if i + 1 < n else aclose[i]

    if rule in ("ma10_break", "ma5_break"):
        line = ma10 if rule == "ma10_break" else ma5
        for i in range(n):
            if aclose[i] < line[i]:
                return sell_next_open(i) / buy - 1.0, i + 1
        return aclose[-1] / buy - 1.0, n
    if rule == "bracket":
        for i in range(n):
            if alow[i] / buy - 1.0 <= STOP:
                return STOP, i + 1
            if ahigh[i] / buy - 1.0 >= TAKE:
                return TAKE, i + 1
        return aclose[-1] / buy - 1.0, n
    if rule == "trail8":
        peak = buy
        for i in range(n):
            peak = max(peak, aclose[i])
            if aclose[i] <= peak * (1 - TRAIL):
                return sell_next_open(i) / buy - 1.0, i + 1
        return aclose[-1] / buy - 1.0, n
    if rule.startswith("hold"):
        k = int(rule[4:])
        i = min(k - 1, n - 1)
        return sell_next_open(i) / buy - 1.0, i + 1
    raise ValueError(rule)


RULES = ["ma10_break", "ma5_break", "bracket", "trail8", "hold5", "hold10"]


def run(tier_pct: int):
    """选 Top tier% 选票 → 拉路径 → 各出场规则对比 + 分年。"""
    d = pickle.load(open(_MODEL, "rb"))
    model, fc, tiers = d["model"], d["feature_cols"], d["tiers"]
    thr = next(t["proba"] for t in tiers if int(t["q"] * 100) == tier_pct)

    df = pd.read_csv(_FEAT, dtype={"ts_code": str})
    df = df[df["trade_date"] >= TEST_START].copy()
    df["proba"] = model.predict_proba(df[fc])[:, 1]
    picks = df[df["proba"] >= thr][["ts_code", "trade_date"]].copy()
    picks["sig_date"] = pd.to_datetime(picks["trade_date"].astype(str), format="%Y%m%d")
    print(f"Top{tier_pct}% 选票 {len(picks)} 笔（proba≥{thr:.4f}，测试集 2024+）\n")

    con = _duckdb.connect(_DUCKDB_PATH, read_only=True)
    try:
        con.register("picks", picks[["ts_code", "sig_date"]])
        path = con.execute(_PATH_SQL.format(win=EXIT_WINDOW)).df()
        breadth = con.execute(_BREADTH_SQL).df()
    finally:
        con.close()

    bmap = dict(zip(breadth["d"], breadth["breadth"]))
    bmap5 = dict(zip(breadth["d"], breadth["breadth_ma5"]))
    path["yr"] = path["sig_date"].str[:4]
    out = {r: [] for r in RULES}
    holds = {r: [] for r in RULES}
    yr_list, gate_list, bval_list, brise_list, ym_list = [], [], [], [], []
    for (tc, sd), g in path.groupby(["ts_code", "sig_date"], sort=False):
        g = g.sort_values("off")
        yr_list.append(sd[:4])
        ym_list.append(sd[:6])
        bv = bmap.get(sd, 0.0)
        bval_list.append(bv)
        brise_list.append(bv > bmap5.get(sd, 1.0))
        gate_list.append(bv >= BREADTH_THR)
        for r in RULES:
            ret, hd = _exit_ret(g, r)
            out[r].append(ret)
            holds[r].append(hd)

    yr_arr = np.array(yr_list)
    gate_arr = np.array(gate_list)
    bval_arr = np.array(bval_list)
    brise_arr = np.array(brise_list)
    ym_arr = np.array(ym_list)

    print(f"{'出场规则':<12s}{'均值':>9s}{'中位':>9s}{'胜率':>8s}{'p10':>8s}{'p90':>8s}{'hold':>7s}")
    for r in RULES:
        a = np.array(out[r], dtype=float)
        a = a[~np.isnan(a)]
        h = np.array(holds[r], dtype=float)
        h = h[~np.isnan(h)]
        print(f"{r:<12s}{a.mean()*100:>8.2f}%{np.median(a)*100:>8.2f}%"
              f"{(a>0).mean()*100:>7.1f}%{np.percentile(a,10)*100:>7.1f}%"
              f"{np.percentile(a,90)*100:>7.1f}%{h.mean():>7.1f}")

    print("\n分年 ret 均值:")
    print(f"{'出场规则':<12s}" + "".join(f"{y:>9s}" for y in ["2024", "2025", "2026"]))
    for r in RULES:
        a = np.array(out[r], dtype=float)
        cells = ""
        for y in ["2024", "2025", "2026"]:
            sub = a[yr_arr == y]
            sub = sub[~np.isnan(sub)]
            cells += f"{sub.mean()*100:>8.2f}%" if len(sub) else f"{'-':>9s}"
        print(f"{r:<12s}{cells}")

    keep = gate_arr.mean()
    print(f"\n=== 广度闸门（全A站上MA20占比≥{BREADTH_THR:.0%}）放行 {keep*100:.1f}% 的选票 ===")
    for r in ["hold10", "trail8"]:
        a = np.array(out[r], dtype=float)
        print(f"\n[{r}] 加闸 vs 不加闸:")
        print(f"{'':<8s}{'样本':>7s}{'均值':>9s}{'中位':>9s}{'胜率':>8s}"
              + "".join(f"{y:>9s}" for y in ["2024", "2025", "2026"]))
        for tag, mask in [("不加闸", np.ones_like(gate_arr, bool)), ("加闸", gate_arr)]:
            v = a[mask]
            v = v[~np.isnan(v)]
            cells = ""
            for y in ["2024", "2025", "2026"]:
                sub = a[mask & (yr_arr == y)]
                sub = sub[~np.isnan(sub)]
                cells += f"{sub.mean()*100:>8.2f}%" if len(sub) else f"{'-':>9s}"
            print(f"{tag:<8s}{len(v):>7d}{v.mean()*100:>8.2f}%{np.median(v)*100:>8.2f}%"
                  f"{(v>0).mean()*100:>7.1f}%{cells}")

    h = np.array(out["hold10"], dtype=float)
    is24 = yr_arr == "2024"
    print("\n=== 2024 深挖（hold10）===")
    print("按月:")
    print(f"{'月份':<8s}{'样本':>6s}{'ret均值':>9s}{'胜率':>8s}{'广度均值':>9s}")
    for m in sorted(set(ym_arr[is24])):
        mask = is24 & (ym_arr == m)
        v = h[mask]; v = v[~np.isnan(v)]
        if len(v):
            print(f"{m:<8s}{len(v):>6d}{v.mean()*100:>8.2f}%{(v>0).mean()*100:>7.1f}%"
                  f"{bval_arr[mask].mean()*100:>8.1f}%")
    print("\n按广度档(2024):")
    buckets = [(0, .4), (.4, .5), (.5, .6), (.6, .7), (.7, 1.01)]
    print(f"{'广度区间':<10s}{'样本':>6s}{'ret均值':>9s}{'胜率':>8s}")
    for lo, hi in buckets:
        mask = is24 & (bval_arr >= lo) & (bval_arr < hi)
        v = h[mask]; v = v[~np.isnan(v)]
        if len(v):
            print(f"{int(lo*100)}-{int(hi*100):<7d}{len(v):>6d}{v.mean()*100:>8.2f}%{(v>0).mean()*100:>7.1f}%")

    print("\n=== 阈值扫描（各年 hold10 ret均值 / 样本数）===")
    print(f"{'闸门条件':<18s}" + "".join(f"{y:>14s}" for y in ["2024", "2025", "2026"]))
    conds = [("广度≥50%", bval_arr >= .5), ("广度≥60%", bval_arr >= .6),
             ("广度≥70%", bval_arr >= .7),
             ("广度≥50%且上升", (bval_arr >= .5) & brise_arr),
             ("广度≥60%且上升", (bval_arr >= .6) & brise_arr)]
    for name, cmask in conds:
        cells = ""
        for y in ["2024", "2025", "2026"]:
            mask = cmask & (yr_arr == y)
            v = h[mask]; v = v[~np.isnan(v)]
            cells += f"{v.mean()*100:>7.2f}%(n{len(v)})" if len(v) else f"{'-':>14s}"
        print(f"{name:<18s}{cells}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", type=int, default=10)
    run(ap.parse_args().tier)


if __name__ == "__main__":
    main()
