"""
rmi_p2.py — 残差互信息因子 P2：中性化检验（给因子下最终判决）

P1 已证明 mean_ΔI 独立选股不显著、且强 regime 依赖，疑似只是「科技成长 vs 价值大盘」
的行业/风格暴露。P2 做判定性实验：横截面对 行业 + 市值 + beta 中性化后再算 Rank IC。
  - 中性化后 IC 塌到 ~0  → 确认是行业/风格赌注，无增量信息；
  - 中性化后 IC 仍存活    → 有「相关系数看不见」的残余结构，可作正交补充/风控暴露。

三档对比，逐层剥离看是谁吃掉了信号：
  raw            原始因子 IC
  size+beta      仅对 log(circ_mv)+beta 中性
  full           再加行业哑变量中性

中性化 = 因子对设计矩阵做横截面 OLS 取残差（numpy lstsq，最小范数解，容许行业哑变量秩亏）。
beta 由 300 天窗口收益对等权市场回归得到；市值取调仓日 circ_mv；行业取 stock_meta。

用法：python first10/rmi_p2.py --start 202001 --top 200
产出：控制台三档 IC 统计对比 + rmi_p2_ic_compare.png（三档累计 IC 曲线）
依赖：本地 DuckDB；scipy/matplotlib；复用 rmi_p0/p1 组件。
"""

import argparse

import duckdb
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from cache_tushare import DUCKDB_PATH
from rmi_p0 import WINDOW, _universe, _returns_matrix, _gaussian_mi_curve, compute
from rmi_p1 import _month_end_dates, _adj_close_on


def _factor_and_aux(con, asof_dash, top, bins, curve, rng):
    """返回 DataFrame[factor, logsize, beta, industry]（index=ts_code），数据不足返回空。"""
    uni = _universe(con, asof_dash, top)
    meta = {r[0]: r[2] for r in uni}
    codes_all = [r[0] for r in uni]
    if len(codes_all) < 10:
        return pd.DataFrame()
    ret, codes = _returns_matrix(con, codes_all, asof_dash)
    _, _, mean_dI = compute(ret, bins, rng, curve=curve)

    mkt = ret.mean(axis=1)
    var_m = mkt.var()
    beta = np.array([np.cov(ret[:, k], mkt)[0, 1] / var_m if var_m > 0 else np.nan
                     for k in range(len(codes))])

    cm = dict(con.execute(
        "SELECT ts_code, circ_mv FROM daily_basic WHERE trade_date = ? AND ts_code IN ({})".format(
            ",".join(["?"] * len(codes))), [asof_dash] + list(codes)).fetchall())
    logsize = np.array([np.log(cm[c]) if cm.get(c, 0) > 0 else np.nan for c in codes])

    df = pd.DataFrame({"factor": mean_dI, "logsize": logsize, "beta": beta,
                       "industry": [meta.get(c, "") for c in codes]}, index=codes)
    return df.dropna(subset=["factor", "logsize", "beta"])


def _neutralize(y, X):
    """y 对设计矩阵 X 做 OLS 取残差（最小范数解，容许秩亏）。"""
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    return y - X @ coef


def _residual_factor(df, use_industry):
    """对 df.factor 做横截面中性化，返回残差因子 Series。"""
    n = len(df)
    cols = [np.ones(n), df["logsize"].values, df["beta"].values]
    if use_industry:
        dummies = pd.get_dummies(df["industry"], drop_first=True).values.astype(float)
        if dummies.size:
            cols.append(dummies)
    X = np.column_stack([c if c.ndim == 1 else c for c in cols[:3]] +
                        ([cols[3]] if len(cols) > 3 else []))
    res = _neutralize(df["factor"].values, X)
    return pd.Series(res, index=df.index)


def _ic_series(con, rebal, top, bins, curve, rng):
    """滚动算三档因子的每期 Rank IC，返回 DataFrame[raw, size+beta, full]。"""
    recs = {"raw": [], "size+beta": [], "full": []}
    dates = []
    for k in range(len(rebal) - 1):
        d0, d1 = rebal[k], rebal[k + 1]
        df = _factor_and_aux(con, d0, top, bins, curve, rng)
        if df.empty or len(df) < 15:
            continue
        p0 = _adj_close_on(con, list(df.index), d0)
        p1 = _adj_close_on(con, list(df.index), d1)
        fwd = {c: p1[c] / p0[c] - 1 for c in df.index if c in p0 and c in p1 and p0[c] > 0}
        common = [c for c in df.index if c in fwd]
        if len(common) < 15:
            continue
        sub = df.loc[common]
        r = pd.Series({c: fwd[c] for c in common})
        f_raw = sub["factor"]
        f_sb = _residual_factor(sub, use_industry=False)
        f_full = _residual_factor(sub, use_industry=True)
        recs["raw"].append(spearmanr(f_raw, r).correlation)
        recs["size+beta"].append(spearmanr(f_sb, r).correlation)
        recs["full"].append(spearmanr(f_full, r).correlation)
        dates.append(d1)
        if (k + 1) % 12 == 0 or k == len(rebal) - 2:
            print(f"  [{k+1}/{len(rebal)-1}] {d1}  "
                  f"raw IC={np.nanmean(recs['raw']):+.3f}  full IC={np.nanmean(recs['full']):+.3f}")
    return pd.DataFrame(recs, index=pd.to_datetime(dates))


def _summary(ic):
    """打印各档 IC 统计。"""
    print("\n— 三档 Rank IC 对比 —")
    print(f"  {'档':<12}{'IC均值':>10}{'ICIR':>9}{'年化ICIR':>10}{'胜率':>8}{'t值':>8}")
    for col in ic.columns:
        s = ic[col].dropna()
        icir = s.mean() / s.std()
        print(f"  {col:<12}{s.mean():>+10.4f}{icir:>+9.3f}{icir*np.sqrt(12):>+10.3f}"
              f"{(s > 0).mean():>7.1%}{s.mean()/s.std()*np.sqrt(len(s)):>+8.2f}")


def _plot(ic, out):
    """三档累计 IC 曲线。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["PingFang SC", "Arial Unicode MS", "Heiti SC"]
    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["axes.unicode_minus"] = False
    plt.figure(figsize=(14, 7))
    for col in ic.columns:
        plt.plot(ic.index, ic[col].cumsum(), label=col, lw=1.8)
    plt.axhline(0, color="gray", ls="--", lw=0.8)
    plt.title("残差互信息因子 P2 — 三档累计 Rank IC（中性化前后对比）")
    plt.legend()
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(out, dpi=120, bbox_inches="tight")
    print(f"\n累计 IC 对比图已存：{out}")


def main():
    """P2 主流程：滚动算三档因子 IC → 统计对比 → 出图。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="202001", help="起始月 YYYYMM")
    ap.add_argument("--top", type=int, default=200, help="每期池子大小")
    ap.add_argument("--bins", type=int, default=7, help="等频分箱数")
    args = ap.parse_args()

    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    rng = np.random.default_rng(42)
    curve = _gaussian_mi_curve(WINDOW, args.bins, rng)
    rebal = _month_end_dates(con, args.start)
    print(f"=== RMI P2 中性化检验 | {rebal[0]}→{rebal[-1]} | 调仓 {len(rebal)} 期 | top={args.top} ===")

    ic = _ic_series(con, rebal, args.top, args.bins, curve, rng)
    con.close()
    _summary(ic)
    _plot(ic, "rmi_p2_ic_compare.png")


if __name__ == "__main__":
    main()
