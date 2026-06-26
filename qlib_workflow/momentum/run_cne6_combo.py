"""
run_cne6_combo.py — CNE6 多因子合成 + 分层回测(IC 加权,样本外验证)

把 cne6_factors 的 16 个风格因子合成一个综合打分:
  ① 每个因子按日横截面 zscore;
  ② 权重 = 样本内(2020-2023)各因子的 Rank IC(自动定方向+强弱,IC加权);
  ③ 综合分 = Σ wᵢ·zᵢ。
然后看综合分在 IS / OOS 的 Rank IC,以及 20日调仓 5 分层的年化与多空(Q5−Q1)。
权重只用 IS 估计、拿 OOS 检验——避免全样本拟合。

环境：.venv312。用法：python qlib_workflow/momentum/run_cne6_combo.py
依赖：cne6_factors(及其 DuckDB 依赖)。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import duckdb
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

import cne6_factors as C

H = 20  # 调仓/持有周期(交易日)


def _daily_ic(fac, fwd, s, e):
    d = pd.DataFrame({"f": fac, "r": fwd}).dropna()
    dt = d.index.get_level_values("datetime")
    d = d[(dt >= pd.Timestamp(s)) & (dt <= pd.Timestamp(e))]
    ics = d.groupby(level="datetime").apply(lambda g: spearmanr(g.f, g.r).correlation if len(g) > 20 else np.nan).dropna()
    return ics.mean(), (ics.mean() / ics.std() if ics.std() else np.nan)


def _layers(comp, close, s, e, n=5):
    """20日调仓 n 分层:返回各层年化 + 多空(Qn−Q1)年化/夏普/回撤。"""
    cw = comp.unstack("instrument").sort_index()
    fh = close.shift(-H) / close - 1
    cal = [d for d in cw.index if pd.Timestamp(s) <= d <= pd.Timestamp(e)]
    rebal = cal[::H]
    grp = {q: [] for q in range(n)}
    for d in rebal:
        row = cw.loc[d].dropna()
        if len(row) < 5 * n:
            continue
        rk = row.rank(pct=True)
        fwd = fh.loc[d]
        for q in range(n):
            lo, hi = q / n, (q + 1) / n + (0.001 if q == n - 1 else 0)
            mem = rk[(rk > lo) & (rk <= hi)].index
            grp[q].append(fwd.reindex(mem).mean())
    ann = {q: np.nanmean(grp[q]) * (244 / H) for q in range(n)}
    ls = pd.Series(grp[n - 1]) - pd.Series(grp[0])
    ls_ann = ls.mean() * (244 / H)
    ls_shp = ls.mean() / ls.std() * np.sqrt(244 / H) if ls.std() else np.nan
    nav = (1 + ls).cumprod()
    mdd = (nav / nav.cummax() - 1).min()
    return ann, ls_ann, ls_shp, mdd


def main():
    con = duckdb.connect(C.DUCKDB_PATH, read_only=True)
    codes = C._universe(con)
    print(f"池 {len(codes)} 只,计算 16 因子并合成...")
    factors, close = C.compute_all(con, codes)
    con.close()

    panel = pd.DataFrame(factors)
    z = panel.groupby(level="datetime").transform(lambda x: (x - x.mean()) / x.std(ddof=0))
    fwdH = (close.shift(-H) / close - 1).stack()           # H日前向收益,与持有周期对齐
    fwdH.index = fwdH.index.set_names(["datetime", "instrument"])

    w = {}
    for name in panel.columns:
        ic, _ = _daily_ic(z[name], fwdH, C.IC_START, "2023-12-31")
        w[name] = ic
    comp = z.mul(pd.Series(w)).sum(axis=1)

    print(f"\n— IC 加权权重(样本内 2020-2023 各因子 {H}日 Rank IC)—")
    for k, v in sorted(w.items(), key=lambda kv: -abs(kv[1])):
        print(f"  {k:<22}{v:+.4f}")

    print(f"\n— 综合分 {H}日 Rank IC —")
    for nm, (s, e) in [("IS 2020-2023", (C.IC_START, "2023-12-31")), ("OOS 2024-2026", (C.OOS_START, C.END))]:
        ic, ir = _daily_ic(comp, fwdH, s, e)
        print(f"  {nm}: IC={ic:+.4f}  IR={ir:+.3f}")

    print("\n— 5 分层回测(20日调仓,Q1=综合分最低 … Q5=最高)—")
    for nm, (s, e) in [("IS 2020-2023", (C.IC_START, "2023-12-31")), ("OOS 2024-2026", (C.OOS_START, C.END))]:
        ann, ls_ann, ls_shp, mdd = _layers(comp, close, s, e)
        print(f"  [{nm}] 各层年化: " + " ".join(f"Q{q+1}={ann[q]:+.1%}" for q in range(5)))
        print(f"             多空(Q5−Q1): 年化={ls_ann:+.1%}  夏普={ls_shp:+.2f}  回撤={mdd:.1%}")


if __name__ == "__main__":
    main()
