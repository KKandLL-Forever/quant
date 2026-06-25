"""
rmi_neighbors.py — 给定一只股票，找出与它关系最近的其他股票（三种口径）

复用残差互信息流程的两两关系，只取「目标股 vs 池内每一只」这一行做排序：
  1. 线性相关 ρ 最高      —— 走势最同步
  2. 总依赖 I_emp 最高     —— 线性+非线性合计绑最紧
  3. 暗线（ΔI 高 + |ρ| 低）—— 相关系数看不见、却有非线性联动的

池子：as-of 日 top N 流动性股 + 强制并入目标股（即使其不在 top N）。
ρ/MI 在同一 300 天窗口、同一等频分箱下计算；ΔI 用同 ρ 高斯 copula 同套分箱估的基准相减（去偏）。

用法：
  python first10/rmi_neighbors.py 600519.SH
  python first10/rmi_neighbors.py 300750.SZ --asof 20260101 --pool 400 --topk 15
依赖：本地 DuckDB；复用 rmi_p0 组件。
"""

import argparse
import os
import sys

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.expanduser("~/AI/quart/first10"))
from cache_tushare import DUCKDB_PATH
from rmi_p0 import (WINDOW, LIST_MIN_DAYS, _universe, _returns_matrix,
                    _equal_freq_codes, _mi_from_codes, _gaussian_mi_curve)


def _pool_with_target(con, target, asof_dash, pool):
    """top N 流动性池并强制并入目标股，返回 {ts_code: (name, industry)}。"""
    rows = _universe(con, asof_dash, pool)
    meta = {r[0]: (r[1], r[2]) for r in rows}
    if target not in meta:
        t = con.execute("SELECT ts_code, name, industry FROM stock_meta WHERE ts_code = ?",
                        [target]).fetchone()
        if t is None:
            raise SystemExit(f"未找到股票 {target}（stock_meta 无此代码）")
        meta[t[0]] = (t[1], t[2])
    return meta


def main():
    """主流程：算目标股对池内每只的 ρ/I_emp/ΔI，输出三种口径 top-K。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="目标股 ts_code，如 600519.SH")
    ap.add_argument("--asof", default=None, help="as-of 日 YYYYMMDD（默认最新交易日）")
    ap.add_argument("--pool", type=int, default=300, help="对比池大小（top N 流动性）")
    ap.add_argument("--bins", type=int, default=7, help="等频分箱数")
    ap.add_argument("--topk", type=int, default=15, help="每种口径列出前 K 只")
    args = ap.parse_args()

    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    asof = args.asof or con.execute("SELECT strftime(MAX(trade_date), '%Y%m%d') FROM daily").fetchone()[0]
    asof_dash = f"{asof[:4]}-{asof[4:6]}-{asof[6:8]}"

    meta = _pool_with_target(con, args.target, asof_dash, args.pool)
    ret, codes = _returns_matrix(con, list(meta.keys()), asof_dash)
    con.close()
    if args.target not in codes:
        raise SystemExit(f"{args.target} 在窗口内交易日不足 {WINDOW} 天，无法计算")

    print(f"=== {args.target} {meta[args.target][0]}（{meta[args.target][1]}） "
          f"| as-of {asof_dash} | 池 {len(codes)} 只 | 窗口 {WINDOW}d ===")

    rng = np.random.default_rng(42)
    grid_x, grid_y = _gaussian_mi_curve(ret.shape[0], args.bins, rng)
    ti = codes.index(args.target)
    code_bins = np.column_stack([_equal_freq_codes(ret[:, k], args.bins) for k in range(len(codes))])
    rho = np.corrcoef(ret, rowvar=False)[ti]

    rows = []
    for j in range(len(codes)):
        if j == ti:
            continue
        mi = _mi_from_codes(code_bins[:, ti], code_bins[:, j], args.bins)
        base = float(np.interp(abs(rho[j]), grid_x, grid_y))
        rows.append((codes[j], meta[codes[j]][0], meta[codes[j]][1], rho[j], mi, mi - base))
    df = pd.DataFrame(rows, columns=["ts_code", "name", "industry", "rho", "I_emp", "dI"])

    pd.set_option("display.unicode.east_asian_width", True)
    k = args.topk

    print(f"\n— ① 线性相关最高（ρ）top {k} —")
    print(df.sort_values("rho", ascending=False).head(k).to_string(index=False))

    print(f"\n— ② 总依赖最高（I_emp，线性+非线性）top {k} —")
    print(df.sort_values("I_emp", ascending=False).head(k).to_string(index=False))

    rho_lo = df["rho"].abs().quantile(0.5)
    hidden = df[df["rho"].abs() <= rho_lo].sort_values("dI", ascending=False).head(k)
    print(f"\n— ③ 暗线（|ρ|≤{rho_lo:.2f} 中位，ΔI 最高：相关系数看不见的非线性联动）top {k} —")
    print(hidden.to_string(index=False))


if __name__ == "__main__":
    main()
