"""
rmi_network.py — 以某只股票为中心，画其近邻簇的关系网络（ρ 主链 + ΔI 非线性叠加）

围绕 seed 股取与它总依赖(I_emp)最高的 K 只邻居，组成子图后：
  灰色边 = ρ 距离 d=√(2(1−ρ)) 的最小生成树（线性主链/产业链骨架）；
  红色粗边 = 子图内 ΔI 最强的若干对（相关系数看不见的非线性额外绑定，粗细∝ΔI）；
  节点按行业上色，seed 放大 + 黑圈高亮。

用法：
  python first10/rmi_network.py 002463.SZ
  python first10/rmi_network.py 002463.SZ --neighbors 30 --pool 400 --nl-edges 20
产出：rmi_network_<seed>.png
依赖：本地 DuckDB；scipy/networkx/matplotlib；复用 rmi_p0 组件。
"""

import argparse
import os
import sys

import duckdb
import numpy as np

sys.path.insert(0, os.path.expanduser("~/AI/quart/first10"))
from cache_tushare import DUCKDB_PATH
from rmi_p0 import WINDOW, _universe, _returns_matrix, _gaussian_mi_curve, compute
from rmi_neighbors import _pool_with_target


def main():
    """主流程：选簇→算 ρ/ΔI→建 MST 主链+ΔI 叠加边→出图。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("seed", help="中心股 ts_code，如 002463.SZ")
    ap.add_argument("--asof", default=None, help="as-of 日 YYYYMMDD（默认最新交易日）")
    ap.add_argument("--pool", type=int, default=400, help="候选池大小（top N 流动性）")
    ap.add_argument("--neighbors", type=int, default=25, help="纳入网络的近邻数")
    ap.add_argument("--bins", type=int, default=7, help="等频分箱数")
    ap.add_argument("--nl-edges", type=int, default=15, help="叠加多少条最强 ΔI 非线性边")
    args = ap.parse_args()

    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    asof = args.asof or con.execute("SELECT strftime(MAX(trade_date), '%Y%m%d') FROM daily").fetchone()[0]
    asof_dash = f"{asof[:4]}-{asof[4:6]}-{asof[6:8]}"
    meta = _pool_with_target(con, args.seed, asof_dash, args.pool)
    ret, codes = _returns_matrix(con, list(meta.keys()), asof_dash)
    con.close()
    if args.seed not in codes:
        raise SystemExit(f"{args.seed} 窗口内交易日不足 {WINDOW} 天")

    rng = np.random.default_rng(42)
    curve = _gaussian_mi_curve(ret.shape[0], args.bins, rng)
    rho, dI, _ = compute(ret, args.bins, rng, curve=curve)

    si = codes.index(args.seed)
    base_seed = np.interp(np.abs(rho[si]), curve[0], curve[1])
    iemp_seed = dI[si] + base_seed
    order = [j for j in np.argsort(-iemp_seed) if j != si][:args.neighbors]
    idx = [si] + order
    sub_codes = [codes[i] for i in idx]
    sub_rho = rho[np.ix_(idx, idx)]
    sub_dI = dI[np.ix_(idx, idx)]

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import networkx as nx
    plt.rcParams["font.sans-serif"] = ["PingFang SC", "Arial Unicode MS", "Heiti SC"]
    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["axes.unicode_minus"] = False

    m = len(sub_codes)
    d = np.sqrt(2 * (1 - sub_rho))
    G = nx.Graph()
    for a in range(m):
        for b in range(a + 1, m):
            G.add_edge(a, b, weight=d[a, b])
    mst = nx.minimum_spanning_tree(G)

    nl = sorted(((sub_dI[a, b], a, b) for a in range(m) for b in range(a + 1, m)),
                reverse=True)[:args.nl_edges]

    inds = [meta[c][1] or "其它" for c in sub_codes]
    uniq = sorted(set(inds))
    cmap = plt.cm.tab20(np.linspace(0, 1, max(len(uniq), 2)))
    color = [cmap[uniq.index(x)] for x in inds]
    sizes = [1400 if i == 0 else 700 for i in range(m)]
    edgecol = ["black" if i == 0 else "none" for i in range(m)]
    pos = nx.spring_layout(mst, seed=7, k=2.0 / np.sqrt(m))

    plt.figure(figsize=(16, 13))
    nx.draw_networkx_edges(mst, pos, alpha=0.35, width=1.2)
    if nl:
        mx = nl[0][0]
        for w, a, b in nl:
            if w > 0:
                plt.plot([pos[a][0], pos[b][0]], [pos[a][1], pos[b][1]],
                         color="#c0392b", lw=1 + 4 * w / mx, alpha=0.6, zorder=0)
    nx.draw_networkx_nodes(mst, pos, node_color=color, node_size=sizes,
                           edgecolors=edgecol, linewidths=2)
    nx.draw_networkx_labels(mst, pos, {i: meta[sub_codes[i]][0] for i in range(m)}, font_size=8)
    handles = [plt.Line2D([0], [0], color="gray", lw=1.5, label="ρ 主链 (MST)"),
               plt.Line2D([0], [0], color="#c0392b", lw=3, label="ΔI 非线性强连接")]
    plt.legend(handles=handles, loc="upper right", fontsize=11)
    plt.title(f"{meta[args.seed][0]} {args.seed} 近邻网络 — ρ 主链 + ΔI 非线性叠加 "
              f"(as-of {asof_dash}, N={m})")
    plt.axis("off")
    out = f"rmi_network_{args.seed}.png"
    plt.savefig(out, dpi=130, bbox_inches="tight")
    print(f"网络图已存：{out}")


if __name__ == "__main__":
    main()
