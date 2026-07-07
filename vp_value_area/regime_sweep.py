"""
regime_sweep.py — 扫参:给 regime.py 的三态识别找「躲熊干净 + 不乱切」的参数组。

评估方式(只判识别器好坏,先不挂 VP 策略):
  用识别结果做最朴素的「熊则空仓、否则满仓持有 ML池指数」择时,和买入持有对比。
  好的识别器 = 回撤砍得多(躲熊)+ 保住大部分收益 + 切换次数少(不乱切)。
  无未来函数:t 日标签(用截至 t 的数据)决定 t+1 日是否持有。

扫描网格:freq{日线/周线} × 回看W × 趋势阈rw × 急跌回撤dd。
产出:控制台按「择时后回撤」排序的对比表(每行:配置/牛熊震荡占比/切换数/买入持有年化·回撤/择时年化·回撤/收益留存)。
用法:python vp_value_area/regime_sweep.py [--start 20220101]
"""

import argparse
import itertools
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import duckdb
from cache_tushare import DUCKDB_PATH
from regime import ml_pool_ew_index, classify, to_weekly


def ann_mdd(daily_ret):
    """日收益序列 → (年化, 最大回撤)。"""
    eq = np.cumprod(1 + daily_ret)
    ann = eq[-1] ** (252 / len(daily_ret)) - 1
    peak = np.maximum.accumulate(eq)
    mdd = ((eq - peak) / peak).min()
    return ann, mdd


def eval_config(pool, freq, w, rw, dd, min_run):
    """某配置 → 识别 + 「熊空仓」择时 vs 买入持有 的指标行。"""
    if freq == "周":
        wk = to_weekly(pool)
        lab_w = classify(wk, w, rw, dd, min_run)
        lab = pd.Series(lab_w, index=wk.index).reindex(pool.index, method="ffill").fillna(0).astype(int).values
    else:
        lab = classify(pool, w, rw, dd, min_run)
    ret = pool.pct_change().fillna(0).values
    pos = np.where(lab != -1, 1.0, 0.0)
    pos = np.roll(pos, 1)
    pos[0] = 0.0
    timed = ret * pos
    bh_ann, bh_mdd = ann_mdd(ret)
    tm_ann, tm_mdd = ann_mdd(timed)
    flips = int((np.diff(lab) != 0).sum())
    n = len(lab)
    return {
        "配置": f"{freq}W{w} rw{int(rw*100)} dd{int(dd*100)}",
        "牛%": round((lab == 1).mean() * 100),
        "熊%": round((lab == -1).mean() * 100),
        "震%": round((lab == 0).mean() * 100),
        "切换": flips,
        "持有年化%": round(bh_ann * 100, 1),
        "持有回撤%": round(bh_mdd * 100, 1),
        "择时年化%": round(tm_ann * 100, 1),
        "择时回撤%": round(tm_mdd * 100, 1),
        "留存%": round(tm_ann / bh_ann * 100) if bh_ann > 0 else None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20220101")
    args = ap.parse_args()

    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    pool = ml_pool_ew_index(con, args.start)
    bh_ann, bh_mdd = ann_mdd(pool.pct_change().fillna(0).values)
    print(f"ML主升池等权指数 · {args.start}起 · 买入持有 年化{bh_ann*100:.1f}% 回撤{bh_mdd*100:.1f}%\n", flush=True)

    rows = []
    for w, rw, dd in itertools.product([40, 60, 90], [0.08, 0.10, 0.12], [0.08, 0.10, 0.12]):
        rows.append(eval_config(pool, "日", w, rw, dd, 5))
    for w, rw, dd in itertools.product([8, 12, 16], [0.08, 0.10, 0.12], [0.08, 0.10, 0.12]):
        rows.append(eval_config(pool, "周", w, rw, dd, 3))

    df = pd.DataFrame(rows)
    pd.set_option("display.unicode.east_asian_width", True)
    pd.set_option("display.max_rows", None)
    print("=== 按「择时后回撤」从小到大(躲熊越干净越靠前)===")
    print(df.sort_values("择时回撤%", ascending=False).to_string(index=False))
    print("\n=== 综合优选:择时回撤 < 持有回撤×0.6 且 留存 ≥ 80% 且 切换 ≤ 20 ===")
    good = df[(df["择时回撤%"] > bh_mdd * 100 * 0.6) & (df["留存%"] >= 80) & (df["切换"] <= 20)]
    print(good.sort_values(["切换", "择时回撤%"], ascending=[True, False]).to_string(index=False) if len(good) else "  (无配置同时满足,放宽条件再看)")


if __name__ == "__main__":
    main()
