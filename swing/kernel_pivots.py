"""kernel_pivots.py — Lo-Mamaysky-Wang(2000) 核回归平滑枢轴检测(实验,对照固定阈值 ZigZag)

做法:对收盘价做高斯核回归平滑(Nadaraya-Watson),带宽由 LOO-CV 最小化误差选出再 ×0.3
(论文系数,避免过度平滑),在平滑曲线上取局部极值当枢轴。带宽=尺度旋钮,自适应每只股票。

环境：.venv312。用法：python swing/kernel_pivots.py --ts 300903.SZ --start 2025-01-01
产出:终端打印核平滑枢轴 vs 9%ZigZag 枢轴,并存对照图 kernel_pivots_<ts>.png。
依赖：DuckDB(daily/adj_factor/stock_meta);matplotlib;复用 run_patterns。
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.expanduser("~/AI/quart"))

import duckdb
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from cache_tushare import DUCKDB_PATH
from run_patterns import _pivots_typed, _detect


def _nw_smooth(p, h):
    """高斯核回归(Nadaraya-Watson)两侧平滑,带宽 h(单位:交易日)。"""
    n = len(p)
    idx = np.arange(n)
    out = np.empty(n)
    for t in range(n):
        w = np.exp(-0.5 * ((idx - t) / h) ** 2)
        out[t] = (w * p).sum() / w.sum()
    return out


def _cv_bandwidth(p, grid):
    """LOO-CV 选带宽:最小化留一法平方误差。"""
    n = len(p)
    idx = np.arange(n)
    best_h, best_err = grid[0], np.inf
    for h in grid:
        err = 0.0
        for t in range(n):
            w = np.exp(-0.5 * ((idx - t) / h) ** 2)
            w[t] = 0.0
            sw = w.sum()
            if sw <= 0:
                continue
            err += (p[t] - (w * p).sum() / sw) ** 2
        if err < best_err:
            best_err, best_h = err, h
    return best_h


def _smoothed_pivots(c, h):
    """平滑后取局部极值,返回 [(idx, is_low)]。"""
    m = _nw_smooth(c, h)
    piv = []
    for t in range(1, len(m) - 1):
        if m[t] > m[t - 1] and m[t] >= m[t + 1]:
            piv.append((t, False))
        elif m[t] < m[t - 1] and m[t] <= m[t + 1]:
            piv.append((t, True))
    return piv, m


def _detect_kernel(c, h, start_i):
    """核平滑枢轴版 N字/W型突破检测,带确认滞后(lag=⌈3h⌉,消除右边缘 lookahead)。

    平滑两侧核回归用到 p±3h 的数据,故枢轴 p 仅在 t≥p+lag 后才视为"已确认可用",避免未来函数。
    枢轴价用原始收盘(突破=收盘越过颈线),与 run_patterns._detect 同口径。"""
    from run_patterns import W_TOL
    pv, _ = _smoothed_pivots(c, h)
    if len(pv) < 3:
        return []
    lag = int(np.ceil(3 * h))
    events = []; n = len(c); pidx = [p[0] for p in pv]
    for t in range(max(start_i, 30), n):
        prior = [k for k in range(len(pv)) if pidx[k] <= t - lag]
        if len(prior) < 3:
            continue
        a, b, cc = prior[-3], prior[-2], prior[-1]
        if pv[a][1] and (not pv[b][1]) and pv[cc][1]:
            pa, pb, pcc = c[pidx[a]], c[pidx[b]], c[pidx[cc]]
            if pa < pcc < pb and c[t] > pb and c[t - 1] <= pb:
                events.append(("N字型", t, [pidx[a], pidx[b], pidx[cc]])); continue
        if len(prior) >= 4:
            h0, lb, hc, ld = prior[-4], prior[-3], prior[-2], prior[-1]
            if (not pv[h0][1]) and pv[lb][1] and (not pv[hc][1]) and pv[ld][1]:
                pB, pC, pD = c[pidx[lb]], c[pidx[hc]], c[pidx[ld]]
                if abs(pD - pB) / pB < W_TOL and pC > pB and pC > pD and c[t] > pC and c[t - 1] <= pC:
                    events.append(("W型", t, [pidx[lb], pidx[hc], pidx[ld]]))
    return events


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ts", default="300903.SZ")
    ap.add_argument("--start", default="2025-01-01")
    ap.add_argument("--thr", type=float, default=0.09)
    ap.add_argument("--h", type=float, default=None, help="手动指定带宽(默认用 CV×0.3)")
    args = ap.parse_args()

    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    name = con.execute("SELECT name FROM stock_meta WHERE ts_code=?", [args.ts]).fetchone()[0]
    g = con.execute("""SELECT d.trade_date, d.close*a.adj_factor c FROM daily d
        JOIN adj_factor a ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date
        WHERE d.ts_code=? AND d.trade_date>=? ORDER BY d.trade_date""", [args.ts, args.start]).fetch_df()
    con.close()
    g["trade_date"] = pd.to_datetime(g["trade_date"])
    c = g["c"].to_numpy(); gd = g["trade_date"].to_numpy()
    ds = lambda i: str(pd.Timestamp(gd[i]).date())

    if args.h is not None:
        h = args.h
        print(f"{name}({args.ts})  样本{len(c)}天  手动带宽={h:.1f}")
    else:
        h_cv = _cv_bandwidth(c, np.arange(2.0, 25.0, 1.0))
        h = max(2.0, 0.3 * h_cv)
        print(f"{name}({args.ts})  样本{len(c)}天  CV带宽={h_cv:.0f}  实用带宽(×0.3)={h:.1f}")

    kpiv, m = _smoothed_pivots(c, h)
    zpiv = _pivots_typed(c, args.thr)
    print(f"\n核平滑枢轴 {len(kpiv)} 个:", ", ".join(("低" if lo else "高") + ds(i) for i, lo in kpiv))
    print(f"9%ZigZag枢轴 {len(zpiv)} 个:", ", ".join(("低" if lo else "高") + ds(i) for i, lo in zpiv))

    print(f"\n核平滑下 2026 检出形态:")
    for typ, bo, pvs in _detect_with(c, kpiv, 30):
        d = pd.Timestamp(gd[bo])
        if d.year == 2026:
            print(f"  {typ} 突破日={d.date()} pivots={[ds(i) for i in pvs]}")

    fig, ax = plt.subplots(figsize=(15, 6))
    ax.plot(g["trade_date"], c, color="#bbb", lw=0.9, label="收盘(后复权)")
    ax.plot(g["trade_date"], m, color="#2c6", lw=1.6, label=f"核平滑 h={h:.1f}")
    for i, lo in kpiv:
        ax.scatter(gd[i], c[i], c="#c0392b" if not lo else "#2980b9", s=28, zorder=5)
    ax.set_title(f"{name} 核平滑枢轴(红=高 蓝=低)")
    ax.legend(); fig.tight_layout()
    out = os.path.expanduser(f"~/AI/quart/swing/kernel_pivots_{args.ts}.png")
    fig.savefig(out, dpi=110); plt.close(fig)
    print(f"\n图: {out}")


def _detect_with(c, pv, start_i):
    """复用 run_patterns 的 N/W 匹配逻辑,但枢轴由外部传入。"""
    from run_patterns import W_TOL
    if len(pv) < 3:
        return []
    events = []; n = len(c); pidx = [p[0] for p in pv]
    for t in range(max(start_i, 30), n):
        prior = [k for k in range(len(pv)) if pidx[k] < t]
        if len(prior) < 3:
            continue
        a, b, cc = prior[-3], prior[-2], prior[-1]
        if pv[a][1] and (not pv[b][1]) and pv[cc][1]:
            pa, pb, pcc = c[pidx[a]], c[pidx[b]], c[pidx[cc]]
            if pa < pcc < pb and c[t] > pb and c[t - 1] <= pb:
                events.append(("N字型", t, [pidx[a], pidx[b], pidx[cc]])); continue
        if len(prior) >= 4:
            h0, lb, hc, ld = prior[-4], prior[-3], prior[-2], prior[-1]
            if (not pv[h0][1]) and pv[lb][1] and (not pv[hc][1]) and pv[ld][1]:
                pB, pC, pD = c[pidx[lb]], c[pidx[hc]], c[pidx[ld]]
                if abs(pD - pB) / pB < W_TOL and pC > pB and pC > pD and c[t] > pC and c[t - 1] <= pC:
                    events.append(("W型", t, [pidx[lb], pidx[hc], pidx[ld]]))
    return events


if __name__ == "__main__":
    main()
