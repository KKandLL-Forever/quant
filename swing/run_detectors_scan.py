"""
run_detectors_scan.py — 唐奇安/Supertrend 参数稳健性扫描(第2步补充)

对两个主力指数全周期(2019-今)+ 熊市段(2021-07~2024-09)扫检测器参数,看绩效是否在
参数邻域内稳定(稳健=各格子都不错;脆弱=个别格子独高)。指标重风险调整:收益/最大回撤/夏普/交易次数。
  唐奇安:入场窗 N × 出场窗 exit_n(均带 ADX>20 闸门)
  Supertrend:ATR周期 × 倍数(均带 ADX>20 闸门)

环境：.venv312。用法：python swing/run_detectors_scan.py
依赖：复用 run_detectors 的取数/指标/回测。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.expanduser("~/AI/quart/first10"))

import numpy as np
import pandas as pd

import run_detectors as D

CODES = {"000688.SH": "科创50", "399673.SZ": "创业板50"}
WINDOWS = [("全周期", "2019-01-01", None), ("熊市段", "2021-07-01", "2024-09-30")]
DON_N = [20, 40, 55, 80]
DON_EXIT = [10, 20]
ST_PERIOD = [7, 10, 14]
ST_MULT = [2.0, 3.0, 4.0]
ADX_THR = 20


def _bh(c):
    nav = np.cumprod(1 + c.pct_change().fillna(0).values)
    dd = ((nav - np.maximum.accumulate(nav)) / np.maximum.accumulate(nav)).min()
    return nav[-1] - 1, dd


def main():
    for code, name in CODES.items():
        for wname, start, end in WINDOWS:
            df = D._load(code, start, end)
            c, h, l = df["close"], df["high"], df["low"]
            gate = (D._adx(h, l, c) > ADX_THR).astype(int)
            bh, bhdd = _bh(c)
            print(f"\n===== {name}({code}) {wname} {df['trade_date'].iloc[0].date()}~{df['trade_date'].iloc[-1].date()}"
                  f" | 买入持有 {bh*100:+.0f}% 回撤{bhdd*100:.0f}% =====")

            print("  [唐奇安+ADX]  格=收益%/回撤%/夏普/交易")
            print(f"    {'N\\exit':>8}" + "".join(f"{f'exit={e}':>20}" for e in DON_EXIT))
            for N in DON_N:
                cells = []
                for E in DON_EXIT:
                    pos = D._donchian_pos(c, h, l, N, E)
                    pp = pd.Series((pos.values.astype(int) & gate.values).astype(float), index=c.index)
                    _, (tot, ann, mdd, sh, tr, expo) = D._bt(c, pp, None)
                    cells.append(f"{tot*100:>5.0f}/{mdd*100:>4.0f}/{sh:>4.2f}/{tr:>2}")
                print(f"    {N:>8}" + "".join(f"{x:>20}" for x in cells))

            print("  [Supertrend+ADX]  格=收益%/回撤%/夏普/交易")
            print(f"    {'周期\\倍数':>8}" + "".join(f"{f'mult={m}':>20}" for m in ST_MULT))
            for P in ST_PERIOD:
                cells = []
                for M in ST_MULT:
                    pos = D._supertrend(h, l, c, P, M)
                    pp = pd.Series((pos.values.astype(int) & gate.values).astype(float), index=c.index)
                    _, (tot, ann, mdd, sh, tr, expo) = D._bt(c, pp, None)
                    cells.append(f"{tot*100:>5.0f}/{mdd*100:>4.0f}/{sh:>4.2f}/{tr:>2}")
                print(f"    {P:>8}" + "".join(f"{x:>20}" for x in cells))


if __name__ == "__main__":
    main()
