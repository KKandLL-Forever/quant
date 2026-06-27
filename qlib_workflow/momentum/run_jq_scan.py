"""
run_jq_scan.py — 对市值残差因子的 SVR 做参数稳健性扫描(C × 持仓数 × 调仓周期)

目的:run_jq_residual 单组参数 SVR 年化 25%,需确认这不是撞运气。本脚本固定 SVR,
扫 C∈{0.5,1,2,5}、TOPK∈{5,10,20}、STEP∈{5,10,20},输出每组年化/夏普/超额,
看绩效是否在参数邻域内稳定(稳健=各格子都正且接近;脆弱=个别格子独高)。

复用 run_jq_residual 的数据加载与特征构造;特征按调仓周期算一次复用,只对每个 C 重训 SVR。

环境：.venv312。用法：python qlib_workflow/momentum/run_jq_scan.py
依赖：DuckDB(同 run_jq_residual);sw_member 表。
"""

import os
import sys

sys.path.insert(0, os.path.expanduser("~/AI/quart/first10"))
sys.path.insert(0, os.path.dirname(__file__))

import duckdb
import numpy as np
import pandas as pd
from sklearn.svm import SVR
from sklearn.preprocessing import StandardScaler

from cache_tushare import DUCKDB_PATH
import run_jq_residual as R

C_GRID = [0.5, 1.0, 2.0, 5.0]
TOPK_GRID = [5, 10, 20]
STEP_GRID = [5, 10, 20]
COST = R.COST


def _stats(rets, ppy):
    """年化/夏普/最大回撤。"""
    rets = np.array(rets)
    nav = np.cumprod(1 + rets)
    ann = nav[-1] ** (ppy / len(rets)) - 1
    peak = np.maximum.accumulate(nav)
    mdd = ((nav - peak) / peak).min()
    sharpe = rets.mean() / rets.std() * np.sqrt(ppy) if rets.std() > 0 else 0
    return ann, sharpe, mdd


def main():
    """跑参数网格,打印每个 STEP 一张 C×TOPK 的年化(超额)表。"""
    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    rebal0, db, px, bs, inc, meta, sw, st = R._load(con)
    cal = [pd.Timestamp(r[0]) for r in con.execute(
        "SELECT DISTINCT trade_date FROM daily WHERE trade_date>=? ORDER BY trade_date", [R.START]).fetchall()]
    con.close()

    meta = meta.set_index("ts_code")
    meta["list_date"] = pd.to_datetime(meta["list_date"], format="%Y%m%d", errors="coerce")
    meta["delist_date"] = pd.to_datetime(meta["delist_date"], format="%Y%m%d", errors="coerce")
    st_set = set(zip(st["ts_code"], st["trade_date"]))
    pxw = px.pivot_table(index="trade_date", values="adjclose", columns="ts_code")

    for STEP in STEP_GRID:
        rebal = cal[::STEP]
        ppy = 252 / STEP
        feats, fwds, bench = [], [], []
        for k in range(len(rebal) - 1):
            d, d2 = rebal[k], rebal[k + 1]
            if d not in pxw.index or d2 not in pxw.index:
                continue
            list_ok = R._live_universe(meta, d)
            out = R._features(d, db, bs, inc, R._sw_asof(sw, d), st_set, list_ok)
            if out is None:
                continue
            X, y = out
            fwd = pxw.loc[d2] / pxw.loc[d] - 1.0
            feats.append((StandardScaler().fit_transform(X.values), y.values, X.index))
            fwds.append(fwd)
            bench.append(fwd.mean())
        ba, _, _ = _stats(bench, ppy)

        print(f"\n=== STEP={STEP}日调仓 | {len(feats)}期 | 基准等权全市场 年化{ba*100:.1f}% ===")
        print(f"{'C\\TOPK':>8}" + "".join(f"{f'k={k}':>16}" for k in TOPK_GRID))
        for C in C_GRID:
            resids = []
            for Xs, yv, idx in feats:
                est = SVR(C=C, epsilon=0.1, kernel="rbf").fit(Xs, yv)
                resids.append(pd.Series(yv - est.predict(Xs), index=idx).sort_values())
            cells = []
            for TOPK in TOPK_GRID:
                rets, held = [], set()
                for resid, fwd in zip(resids, fwds):
                    pick = list(resid.index[:TOPK])
                    r = fwd.reindex(pick).dropna()
                    ret = (r.mean() if len(r) else 0.0) - COST * (len(set(pick) - held) / max(TOPK, 1)) * 2
                    held = set(pick); rets.append(ret)
                ann, sh, _ = _stats(rets, ppy)
                cells.append(f"{ann*100:>6.1f}%(+{(ann-ba)*100:>4.1f})sh{sh:.2f}")
            print(f"{C:>8}" + "".join(f"{c:>16}" for c in cells))


if __name__ == "__main__":
    main()
