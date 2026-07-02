"""
rmi_p0.py — 残差互信息因子 P0：单时点跑通 + MST 可视化

实现《残差互信息因子-方法论.md》的第 1-8 步，在单个 as-of 日期上：
  价格→对数收益率→300天窗口→两两 ρ 与经验互信息 I_emp→残差互信息 ΔI→
  个股因子 mean_ΔI→建 MST 网络图（按行业上色，验证同板块是否自然成簇）。

与文档的关键差异（P1 校准点提前用上）：
  ΔI 的高斯基准不取理论闭式 I_G=−½ln(1−ρ²)，而是用「同 ρ 的高斯 copula 代理样本、
  过同一套等频分箱估一遍 MI」当基准（_gaussian_mi_curve）。这样 I_emp 与基准的分箱正偏
  同源、相减抵消，剩下的 ΔI 才是真·非线性残差，而非估计偏差伪影。
  等频分箱对单调变换不变，故高斯 copula 的 MI 只依赖 (ρ, n, bins)，可预模拟成 ρ→MI 曲线后插值。

股票池：as-of 日按 circ_mv 取 top N 高流动性票，剔 ST、次新（上市不足 LIST_MIN_DAYS）、
        窗口内交易日不足、北交所。

用法：
  python first10/rmi_p0.py                         # 默认 as-of=最新交易日, N=100
  python first10/rmi_p0.py --asof 20260101 --top 150 --bins 7

产出：
  控制台打印 mean_ΔI 排名 + ΔI 与 |ρ| 的相关性（检验是否真解耦）
  rmi_mst_<asof>.png  MST 网络图（行业上色）
依赖：本地 DuckDB（daily/adj_factor/daily_basic/stock_st/stock_meta），scipy/networkx/matplotlib。
"""

import argparse
import os
_ROOT = os.path.dirname(os.path.abspath(__file__))
while _ROOT != "/" and not os.path.exists(os.path.join(_ROOT, "cache_tushare.py")):
    _ROOT = os.path.dirname(_ROOT)
import sys

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, _ROOT)
from cache_tushare import DUCKDB_PATH

WINDOW = 300
LIST_MIN_DAYS = 400
SIM_PATHS = 300
RHO_GRID = np.linspace(0.0, 0.985, 40)


def _universe(con, asof, top):
    """as-of 日取 top 只高流动性票（剔 ST/次新/北交所/窗口数据不足），返回 [(ts_code, name, industry)]。"""
    rows = con.execute(
        """
        WITH win AS (
            SELECT trade_date FROM (SELECT DISTINCT trade_date FROM daily WHERE trade_date <= ?)
            ORDER BY trade_date DESC LIMIT ?
        ),
        full_codes AS (
            SELECT ts_code FROM daily WHERE trade_date IN (SELECT trade_date FROM win)
            GROUP BY ts_code HAVING COUNT(*) = ?
        ),
        st_codes AS (SELECT DISTINCT ts_code FROM stock_st WHERE trade_date = ?),
        liq AS (
            SELECT ts_code, circ_mv FROM daily_basic WHERE trade_date = ?
        )
        SELECT m.ts_code, m.name, m.industry
        FROM liq l
        JOIN stock_meta m ON m.ts_code = l.ts_code
        JOIN full_codes f ON f.ts_code = l.ts_code
        WHERE l.ts_code NOT IN (SELECT ts_code FROM st_codes)
          AND m.ts_code NOT LIKE '%.BJ'
          AND m.list_date <> '' AND CAST(m.list_date AS BIGINT) <= ?
        ORDER BY l.circ_mv DESC
        LIMIT ?
        """,
        [asof, WINDOW, WINDOW, asof, asof,
         int((pd.Timestamp(asof) - pd.Timedelta(days=LIST_MIN_DAYS)).strftime("%Y%m%d")), top],
    ).fetchall()
    return rows


def _returns_matrix(con, codes, asof):
    """取窗口内前复权收盘价，返回对数收益率矩阵 (T × N) 与对齐后的 code 列表。"""
    df = con.execute(
        """
        SELECT d.ts_code, d.trade_date, d.close * a.adj_factor AS adj_close
        FROM daily d JOIN adj_factor a
          ON a.ts_code = d.ts_code AND a.trade_date = d.trade_date
        WHERE d.ts_code IN ({}) AND d.trade_date <= ?
        """.format(",".join(["?"] * len(codes))),
        list(codes) + [asof],
    ).fetch_df()
    px = df.pivot(index="trade_date", columns="ts_code", values="adj_close").sort_index()
    px = px.tail(WINDOW + 1)
    px = px.dropna(axis=1)
    ret = np.log(px / px.shift(1)).iloc[1:]
    return ret.values, list(px.columns)


def _equal_freq_codes(x, bins):
    """把一维序列按等频分箱，返回每个样本的箱号 0..bins-1。"""
    edges = np.quantile(x, np.linspace(0, 1, bins + 1))
    edges[0], edges[-1] = -np.inf, np.inf
    return np.clip(np.searchsorted(edges, x, side="right") - 1, 0, bins - 1)


def _mi_from_codes(cx, cy, bins):
    """由两路箱号计算经验互信息（nats）。"""
    joint = np.bincount(cx * bins + cy, minlength=bins * bins).reshape(bins, bins).astype(float)
    joint /= joint.sum()
    px = joint.sum(1, keepdims=True)
    py = joint.sum(0, keepdims=True)
    nz = joint > 0
    return float(np.sum(joint[nz] * np.log(joint[nz] / (px @ py)[nz])))


def _gaussian_mi_curve(n, bins, rng):
    """预模拟高斯 copula 在各 ρ 下、同 n 同等频分箱的期望 MI，返回 (rho_grid, mi)。"""
    out = []
    for rho in RHO_GRID:
        cov = np.array([[1.0, rho], [rho, 1.0]])
        L = np.linalg.cholesky(cov)
        acc = 0.0
        for _ in range(SIM_PATHS):
            z = rng.standard_normal((n, 2)) @ L.T
            acc += _mi_from_codes(_equal_freq_codes(z[:, 0], bins),
                                  _equal_freq_codes(z[:, 1], bins), bins)
        out.append(acc / SIM_PATHS)
    return RHO_GRID, np.array(out)


def compute(ret, bins, rng, curve=None):
    """核心：返回 (rho 矩阵, ΔI 矩阵, mean_ΔI 向量)。curve=(grid_x,grid_y) 可传入预算好的高斯基准。"""
    n, N = ret.shape
    rho = np.corrcoef(ret, rowvar=False)
    codes = np.column_stack([_equal_freq_codes(ret[:, k], bins) for k in range(N)])
    grid_x, grid_y = curve if curve is not None else _gaussian_mi_curve(n, bins, rng)
    dI = np.zeros((N, N))
    for i in range(N):
        for j in range(i + 1, N):
            mi = _mi_from_codes(codes[:, i], codes[:, j], bins)
            base = float(np.interp(abs(rho[i, j]), grid_x, grid_y))
            dI[i, j] = dI[j, i] = mi - base
    mean_dI = dI.sum(1) / (N - 1)
    return rho, dI, mean_dI


def plot_mst(rho, codes, meta, asof):
    """用 d=√(2(1−ρ)) 建距离矩阵，求 MST，按行业上色画图，存 PNG。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import networkx as nx

    plt.rcParams["font.sans-serif"] = ["PingFang SC", "Arial Unicode MS", "Heiti SC", "STHeiti"]
    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["axes.unicode_minus"] = False

    N = len(codes)
    d = np.sqrt(2 * (1 - rho))
    G = nx.Graph()
    for i in range(N):
        for j in range(i + 1, N):
            G.add_edge(i, j, weight=d[i, j])
    mst = nx.minimum_spanning_tree(G)
    inds = [meta[c][1] or "其它" for c in codes]
    uniq = sorted(set(inds))
    cmap = plt.cm.tab20(np.linspace(0, 1, len(uniq)))
    color = [cmap[uniq.index(x)] for x in inds]
    pos = nx.spring_layout(mst, seed=7, k=1.5 / np.sqrt(N))
    plt.figure(figsize=(16, 16))
    nx.draw_networkx_edges(mst, pos, alpha=0.3)
    nx.draw_networkx_nodes(mst, pos, node_color=color, node_size=300)
    nx.draw_networkx_labels(mst, pos, {i: meta[codes[i]][0] for i in range(N)}, font_size=7)
    plt.title(f"残差互信息因子 P0 — MST 网络 (as-of {asof}, N={N})")
    plt.axis("off")
    out = f"rmi_mst_{asof}.png"
    plt.savefig(out, dpi=120, bbox_inches="tight")
    print(f"\nMST 网络图已存：{out}")


def main():
    """P0 主流程：选池→收益率→ΔI→mean_ΔI 排名 + 解耦检验 + MST 图。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", default=None, help="as-of 日 YYYYMMDD（默认最新交易日）")
    ap.add_argument("--top", type=int, default=100, help="池子大小（按 circ_mv top N）")
    ap.add_argument("--bins", type=int, default=7, help="等频分箱数")
    args = ap.parse_args()

    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    asof = args.asof or con.execute("SELECT strftime(MAX(trade_date), '%Y%m%d') FROM daily").fetchone()[0]
    asof_dash = f"{asof[:4]}-{asof[4:6]}-{asof[6:8]}"

    uni = _universe(con, asof_dash, args.top)
    meta = {r[0]: (r[1], r[2]) for r in uni}
    codes_all = [r[0] for r in uni]
    print(f"=== RMI P0 | as-of {asof_dash} | 候选 {len(codes_all)} 只 | bins={args.bins} | 窗口 {WINDOW}d ===")

    ret, codes = _returns_matrix(con, codes_all, asof_dash)
    con.close()
    print(f"对齐后有效股票 {len(codes)} 只，收益率窗口 {ret.shape[0]} 天")

    rng = np.random.default_rng(42)
    rho, dI, mean_dI = compute(ret, args.bins, rng)

    iu = np.triu_indices(len(codes), 1)
    flat_dI, flat_absrho = dI[iu], np.abs(rho[iu])
    decouple = np.corrcoef(flat_dI, flat_absrho)[0, 1]

    res = pd.DataFrame({
        "ts_code": codes,
        "name": [meta[c][0] for c in codes],
        "industry": [meta[c][1] for c in codes],
        "mean_dI": mean_dI,
    }).sort_values("mean_dI", ascending=False)

    pd.set_option("display.unicode.east_asian_width", True)
    print("\n— mean_ΔI 最高 15 只（非线性共动最强）—")
    print(res.head(15).to_string(index=False))
    print("\n— mean_ΔI 最低 10 只 —")
    print(res.tail(10).to_string(index=False))
    print(f"\nΔI 与 |ρ| 的相关性 = {decouple:+.3f}  "
          f"（越接近 0 越说明非线性信号已与线性相关解耦；>0.5 说明仍泄漏）")
    print(f"ΔI 负值占比 = {(flat_dI < 0).mean():.1%}  （去偏到位的话应在 ~50% 附近上下波动）")

    plot_mst(rho, codes, meta, asof)


if __name__ == "__main__":
    main()
