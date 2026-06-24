"""
rmi_p1.py — 残差互信息因子 P1：月频滚动 + Rank IC/ICIR + 5 分层多空回测

在 rmi_p0 单时点逻辑之上做横截面有效性检验：
  1. 取 2020-01 起每个月末交易日为调仓点；
  2. 每个调仓点：选 top N 流动性池 → 300 天窗口算 mean_ΔI 因子（复用 rmi_p0.compute）；
  3. 前向收益：持有到下一调仓点的前复权区间收益；
  4. 每期 Rank IC = spearman(因子, 下期收益)，汇总 IC 均值/IC标准差/ICIR/胜率/t值；
  5. 按因子分 5 层等权，算各层下期收益与多空组合(Q5−Q1)净值、年化、夏普、最大回撤。

高斯基准曲线只依赖 (窗口长度, 箱数)，全程预算一次复用（_gaussian_mi_curve），是省时关键。
因子方向（高 mean_ΔI 该做多还是做空）由 IC 符号决定，不预设。

用法：
  python first10/rmi_p1.py                          # top=200, bins=7, 2020-01→最新
  python first10/rmi_p1.py --top 150 --start 202101 --bins 7

产出：
  控制台：IC 统计 + 各层年化 + 多空组合指标
  rmi_p1_backtest.png  上图=5层多空净值曲线，下图=每期 Rank IC 柱状
依赖：本地 DuckDB；scipy/matplotlib；复用 rmi_p0 的组件。
"""

import argparse

import duckdb
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from cache_tushare import DUCKDB_PATH
from rmi_p0 import WINDOW, _universe, _returns_matrix, _gaussian_mi_curve, compute


def _month_end_dates(con, start_ym):
    """返回 start_ym(YYYYMM) 起每个自然月的最后一个交易日 [YYYY-MM-DD,...]。"""
    start_dash = f"{start_ym[:4]}-{start_ym[4:6]}-01"
    rows = con.execute(
        "SELECT MAX(trade_date) FROM daily WHERE trade_date >= ? "
        "GROUP BY strftime(trade_date, '%Y-%m') ORDER BY 1",
        [start_dash],
    ).fetchall()
    return [r[0].strftime("%Y-%m-%d") for r in rows]


def _adj_close_on(con, codes, date_dash):
    """某交易日各股前复权收盘价 dict{ts_code: price}。"""
    rows = con.execute(
        """
        SELECT d.ts_code, d.close * a.adj_factor
        FROM daily d JOIN adj_factor a
          ON a.ts_code = d.ts_code AND a.trade_date = d.trade_date
        WHERE d.trade_date = ? AND d.ts_code IN ({})
        """.format(",".join(["?"] * len(codes))),
        [date_dash] + list(codes),
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def _factor_on(con, asof_dash, top, bins, curve, rng):
    """单调仓点的 mean_ΔI 因子 Series(index=ts_code)。"""
    uni = _universe(con, asof_dash, top)
    codes_all = [r[0] for r in uni]
    if len(codes_all) < 10:
        return pd.Series(dtype=float)
    ret, codes = _returns_matrix(con, codes_all, asof_dash)
    _, _, mean_dI = compute(ret, bins, rng, curve=curve)
    return pd.Series(mean_dI, index=codes)


def _metrics(ls):
    """多空月度收益序列 → (年化, 夏普, 最大回撤)。"""
    nav = (1 + ls).cumprod()
    ann = ls.mean() * 12
    sharpe = ls.mean() / ls.std() * np.sqrt(12) if ls.std() > 0 else np.nan
    mdd = (nav / nav.cummax() - 1).min()
    return ann, sharpe, mdd


def _plot(nav_layers, ic_series, ls_nav, out):
    """上：各层+多空净值；下：每期 Rank IC 柱状。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["PingFang SC", "Arial Unicode MS", "Heiti SC"]
    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["axes.unicode_minus"] = False

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 11), height_ratios=[2, 1])
    for col in nav_layers.columns:
        ax1.plot(nav_layers.index, nav_layers[col], label=f"Q{col+1}")
    ax1.plot(ls_nav.index, ls_nav.values, label="多空(Q5−Q1)", color="black", lw=2.2)
    ax1.axhline(1, color="gray", ls="--", lw=0.8)
    ax1.set_title("残差互信息因子 P1 — 5 分层 + 多空净值")
    ax1.legend(ncol=3)
    ax1.grid(alpha=0.3)
    colors = ["#c0392b" if v < 0 else "#27ae60" for v in ic_series.values]
    ax2.bar(range(len(ic_series)), ic_series.values, color=colors)
    ax2.axhline(ic_series.mean(), color="blue", ls="--", lw=1, label=f"IC均值={ic_series.mean():+.3f}")
    ax2.set_title("每期 Rank IC")
    ax2.legend()
    ax2.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(out, dpi=120, bbox_inches="tight")
    print(f"\n回测图已存：{out}")


def main():
    """P1 主流程：滚动算因子+前向收益 → IC 统计 → 分层回测 → 出图。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="202001", help="起始月 YYYYMM（默认 202001）")
    ap.add_argument("--top", type=int, default=200, help="每期池子大小（按 circ_mv top N）")
    ap.add_argument("--bins", type=int, default=7, help="等频分箱数")
    ap.add_argument("--layers", type=int, default=5, help="分层数")
    args = ap.parse_args()

    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    rng = np.random.default_rng(42)
    curve = _gaussian_mi_curve(WINDOW, args.bins, rng)
    rebal = _month_end_dates(con, args.start)
    print(f"=== RMI P1 | {rebal[0]}→{rebal[-1]} | 调仓 {len(rebal)} 期 | top={args.top} | bins={args.bins} ===")

    ic_list, dates_list = [], []
    layer_ret = {q: [] for q in range(args.layers)}
    for k in range(len(rebal) - 1):
        d0, d1 = rebal[k], rebal[k + 1]
        fac = _factor_on(con, d0, args.top, args.bins, curve, rng)
        if fac.empty:
            continue
        p0 = _adj_close_on(con, list(fac.index), d0)
        p1 = _adj_close_on(con, list(fac.index), d1)
        fwd = {c: p1[c] / p0[c] - 1 for c in fac.index if c in p0 and c in p1 and p0[c] > 0}
        common = [c for c in fac.index if c in fwd]
        if len(common) < args.layers * 3:
            continue
        f = fac.loc[common]
        r = pd.Series({c: fwd[c] for c in common})
        ic_list.append(spearmanr(f.values, r.values).correlation)
        dates_list.append(d1)
        q = pd.qcut(f.rank(method="first"), args.layers, labels=False)
        for grp in range(args.layers):
            layer_ret[grp].append(r[q[q == grp].index].mean())
        if (k + 1) % 12 == 0 or k == len(rebal) - 2:
            print(f"  [{k+1}/{len(rebal)-1}] {d1}  累计 IC 均值={np.nanmean(ic_list):+.3f}")
    con.close()

    ic = pd.Series(ic_list, index=pd.to_datetime(dates_list))
    icir = ic.mean() / ic.std()
    tstat = ic.mean() / ic.std() * np.sqrt(len(ic))
    print("\n— Rank IC 统计 —")
    print(f"  期数={len(ic)}  IC均值={ic.mean():+.4f}  IC标准差={ic.std():.4f}  "
          f"ICIR={icir:+.3f}  年化ICIR={icir*np.sqrt(12):+.3f}")
    print(f"  IC>0 胜率={(ic > 0).mean():.1%}  t值={tstat:+.2f}")

    nav_layers = pd.DataFrame(
        {q: (1 + pd.Series(layer_ret[q], index=ic.index)).cumprod() for q in range(args.layers)})
    layer_ann = {q: pd.Series(layer_ret[q]).mean() * 12 for q in range(args.layers)}
    print("\n— 各层年化收益（Q1=因子最低 … Q{}=最高）—".format(args.layers))
    for q in range(args.layers):
        print(f"  Q{q+1}: {layer_ann[q]:+.2%}")

    top_grp, bot_grp = args.layers - 1, 0
    ls = pd.Series(layer_ret[top_grp], index=ic.index) - pd.Series(layer_ret[bot_grp], index=ic.index)
    ann, sharpe, mdd = _metrics(ls)
    ls_nav = (1 + ls).cumprod()
    print(f"\n— 多空组合 (Q{args.layers}−Q1) —")
    print(f"  年化={ann:+.2%}  夏普={sharpe:+.2f}  最大回撤={mdd:.2%}  末期净值={ls_nav.iloc[-1]:.3f}")

    _plot(nav_layers, ic, ls_nav, "rmi_p1_backtest.png")


if __name__ == "__main__":
    main()
