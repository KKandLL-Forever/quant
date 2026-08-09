"""A 股趋势跟踪可行性谱系：估计各层标的的 φ（短期自相关）与 d（长记忆），代入 Sepp & Lucic (2026) 闭式夏普。

论文：papers/trend_following/sepp_lucic_2026_trend_following.pdf，公式核对见同目录 NOTES.md。

回答的问题：A 股的哪一层（宽基 / 行业 / 概念 / 个股）具有正的趋势性，可行跨度是多少。

方法（严格照 NOTES.md 的式号）：
  1. 波动率归一化 z_t = r_t / sigma_{t-1}，sigma 用 EWMA span=33（式 2.13/2.14），归一化在滤波之前
  2. phi = z_t 的一阶样本自相关；d 用 GPH 对数周期图回归（长记忆强度）
  3. Psi_nu = sum_{m>=1} nu^m rho(m)（式 5.3），A_nu、B_nu（式 5.8）
  4. 毛夏普 SR（式 5.12，取 kappa=0，论文 3.7 节证明峰度是二阶效应）
  5. 净夏普 SR_net（式 5.13），成本 c = c_real / sigma_ann（NOTES.md 3.5 节推导）
     A 股单边成本：个股 3.6bp（往返万7.2），ETF/指数 1.1bp（免印花）

数据层：
  broad    宽基（ths_daily 883xxx 样本股指数 + fund_daily 宽基 ETF）
  industry 申万一级行业等权（sw_member 时点成分 + daily.pct_chg 合成）
  concept  同花顺概念板块（ths_daily 885xxx，历史时点成分，无前视偏差）
  stock    个股（daily.pct_chg，流动性筛选，剔除 ST/退市）

用法：
  python research/tf_acf_spectrum.py                    全部层
  python research/tf_acf_spectrum.py --layers broad,industry
  python research/tf_acf_spectrum.py --start 2019-01-01

产出：
  research/tf_acf_spectrum.csv       每个标的一行（phi/d/最优跨度/毛净夏普）
  控制台                              分层中位数汇总表

结论（2016-01 至 2026-08，4596 个标的）：

1. **A 股没有长记忆。** GPH 的 d 在四层全部不显著：宽基/行业/概念 d 显著>0 占比 0%，个股 0.8%，
   各层中位 d 在 -0.04 ~ +0.06 之间。论文中唯一支撑「中长跨度趋势跟踪」的机制（ARFIMA 长记忆，
   图 6.3 的驼峰型内部最优）在 A 股不存在。

2. **但短期自相关 phi 稳定为正**，且在两个半样本同号：行业 96.8%、概念 95.4%、宽基 91.7%、个股 60.1%。
   中位 phi：行业 0.057(t=2.81)、概念 0.058(t=2.60)、宽基 0.038(t=1.87)、个股 0.030(t=1.35)。

3. 只有 phi、没有 d ⇒ 式 6.11 的 SR ∝ 1/sqrt(span) 成立，**净夏普随跨度单调衰减，最优在最短跨度**。
   这与上一轮的猜测（60-120 日）相反，也与论文 AR-1 理论完全一致。
   各层净夏普中位（跨度 5 / 20 / 60 / 250 日）：
       行业   0.70 / 0.48 / 0.23 / 0.08
       概念   0.64 / 0.41 / 0.16 / -0.06
       宽基   0.47 / 0.30 / 0.18 / 0.19
       个股   0.24 / 0.07 / -0.04 / -0.09

4. **个股在 40 日以上跨度净夏普为负**（临界成本也为负），慢速趋势跟踪在个股上是负期望，
   与 quant_select 的「反转有效」一致。分层排序：行业 > 概念 > 宽基 > 个股。

5. 成本远不是约束。临界单边成本（式 5.13 解 SR_net=0）vs 实际单边：
   行业 16.7bp vs 3.6（4.6x）、概念 15.5 vs 3.6（4.3x）、宽基 ETF 7.1 vs 1.1（6.4x）、个股 11.0 vs 3.6（3.1x）。

已知局限（下一步要处理的）：
  - 成本只含佣金+印花+过户，**不含冲击成本与滑点**。5 日跨度换手极高，冲击成本才是主导项，
    上面的 3-6 倍余量不足以证明可实盘。
  - 行业/概念是等权/合成指数，不可直接交易，且等权组合的 phi 含非同步交易虚高成分
    （Lo-MacKinlay 1990）。综合行业 phi=0.112(t=5.6) 最可疑——小盘杂股集合。
    真正可交易的证据在 ETF 那一行：510300 phi=0.033、510500 phi=0.042，临界 7-9bp vs 实际 1.1bp。
  - 论文脚注 3：对持久信号，日频矩年化会高估 horizon-based 夏普（5 日跨度信号持久性低，影响较小）。
  - 全部样本内。论文自己也未做样本外验证（p.29）。
"""

import argparse
import os

import duckdb
import numpy as np
import pandas as pd

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "stock_data_tushare.duckdb")
OUT_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tf_acf_spectrum.csv")

A_TRADING = 243
VOL_SPAN = 33
MIN_OBS = 1000
MAX_LAG = 780
SPANS = (5, 10, 20, 40, 60, 90, 120, 180, 250, 375)

COST_ONEWAY_BP = {"broad": 1.1, "industry": 3.6, "concept": 3.6, "stock": 3.6}

BROAD_THS = {
    "883300.TI": "沪深300样本股",
    "883301.TI": "上证50样本股",
    "883302.TI": "上证180成份股",
    "883303.TI": "上证380成份股",
    "883304.TI": "中证500成份股",
}
BROAD_ETF = {
    "510300.SH": "沪深300ETF",
    "510500.SH": "中证500ETF",
    "512100.SH": "中证1000ETF",
    "510050.SH": "上证50ETF",
    "159915.SZ": "创业板ETF",
    "588000.SH": "科创50ETF",
    "510880.SH": "红利ETF",
}


def _con():
    """打开只读 DuckDB 连接并固定单线程，保证浮点聚合顺序可复现。"""
    con = duckdb.connect(DB_PATH, read_only=True)
    con.execute("SET threads=1")
    return con


def load_broad(con, start):
    """宽基层：同花顺样本股指数 + 宽基 ETF，返回 {code: (name, ret_series)}。"""
    out = {}
    ths = con.execute(
        """
        SELECT ts_code, trade_date, pct_change/100.0 AS ret
        FROM ths_daily
        WHERE ts_code IN ? AND trade_date >= ?
        ORDER BY ts_code, trade_date
        """,
        [list(BROAD_THS), start.replace("-", "")],
    ).df()
    for code, g in ths.groupby("ts_code"):
        out[code] = (BROAD_THS[code], g.set_index("trade_date")["ret"])

    etf = con.execute(
        """
        SELECT ts_code, trade_date, pct_chg/100.0 AS ret
        FROM fund_daily
        WHERE ts_code IN ? AND trade_date >= ?
        ORDER BY ts_code, trade_date
        """,
        [list(BROAD_ETF), start.replace("-", "")],
    ).df()
    for code, g in etf.groupby("ts_code"):
        out[code] = (BROAD_ETF[code], g.set_index("trade_date")["ret"])
    return out


def load_industry(con, start):
    """行业层：申万一级时点成分等权日收益，返回 {l1_name: (name, ret_series)}。"""
    df = con.execute(
        """
        SELECT m.l1_name AS code, d.trade_date, avg(d.pct_chg)/100.0 AS ret
        FROM daily d
        JOIN sw_member m ON d.ts_code = m.ts_code
        WHERE d.trade_date >= ?
          AND strftime(d.trade_date, '%Y%m%d') >= m.in_date
          AND (m.out_date IS NULL OR strftime(d.trade_date, '%Y%m%d') < m.out_date)
          AND d.pct_chg IS NOT NULL
        GROUP BY 1, 2
        HAVING count(*) >= 5
        ORDER BY 1, 2
        """,
        [start],
    ).df()
    return {code: (code, g.set_index("trade_date")["ret"]) for code, g in df.groupby("code")}


def load_concept(con, start):
    """概念层：同花顺概念板块指数（885xxx），返回 {code: (name, ret_series)}。"""
    df = con.execute(
        """
        SELECT d.ts_code, d.trade_date, d.pct_change/100.0 AS ret, i.name
        FROM ths_daily d
        JOIN ths_index i ON d.ts_code = i.ts_code
        WHERE substr(d.ts_code, 1, 3) = '885' AND d.trade_date >= ?
        ORDER BY d.ts_code, d.trade_date
        """,
        [start.replace("-", "")],
    ).df()
    return {code: (g["name"].iloc[0], g.set_index("trade_date")["ret"]) for code, g in df.groupby("ts_code")}


def load_stock(con, start, min_amount_wan=5000):
    """个股层：流动性达标的非 ST 在市个股日收益，返回 {ts_code: (name, ret_series)}。"""
    df = con.execute(
        """
        WITH liq AS (
            SELECT d.ts_code
            FROM daily d
            JOIN stock_meta s ON d.ts_code = s.ts_code
            WHERE d.trade_date >= ?
              AND (s.delist_date IS NULL OR s.delist_date = '')
              AND s.name NOT LIKE '%ST%'
              AND s.name NOT LIKE '%退%'
            GROUP BY d.ts_code
            HAVING count(*) >= ? AND median(d.amount) >= ?
        )
        SELECT d.ts_code, d.trade_date, d.pct_chg/100.0 AS ret, s.name
        FROM daily d
        JOIN liq ON d.ts_code = liq.ts_code
        JOIN stock_meta s ON d.ts_code = s.ts_code
        WHERE d.trade_date >= ? AND d.pct_chg IS NOT NULL
        ORDER BY d.ts_code, d.trade_date
        """,
        [start, MIN_OBS, min_amount_wan, start],
    ).df()
    return {code: (g["name"].iloc[0], g.set_index("trade_date")["ret"]) for code, g in df.groupby("ts_code")}


def vol_normalize(ret, span=VOL_SPAN):
    """按式 2.13/2.14 计算波动率归一化收益 z_t = r_t / sigma_{t-1}。"""
    r = pd.Series(ret).astype(float).dropna()
    sigma = np.sqrt(r.pow(2).ewm(span=span, adjust=False).mean()).shift(1)
    z = (r / sigma).replace([np.inf, -np.inf], np.nan).dropna()
    return z.iloc[span * 3:]


def sample_acf(z, max_lag=MAX_LAG):
    """返回 z 的样本自相关 rho(1..max_lag)。"""
    x = np.asarray(z, dtype=float)
    x = x - x.mean()
    n = len(x)
    max_lag = min(max_lag, n // 3)
    nfft = 1 << int(np.ceil(np.log2(2 * n)))
    f = np.fft.rfft(x, nfft)
    acov = np.fft.irfft(f * np.conjugate(f), nfft)[: max_lag + 1] / n
    if acov[0] <= 0:
        return np.zeros(max_lag)
    return acov[1:] / acov[0]


def gph_d(z, power=0.5):
    """GPH 对数周期图回归估计分数阶 d，返回 (d, se)。"""
    x = np.asarray(z, dtype=float)
    x = x - x.mean()
    n = len(x)
    m = max(8, int(n ** power))
    per = np.abs(np.fft.rfft(x)) ** 2 / (2 * np.pi * n)
    j = np.arange(1, m + 1)
    lam = 2 * np.pi * j / n
    if m + 1 > len(per):
        return np.nan, np.nan
    y = np.log(per[1 : m + 1])
    xr = np.log(4 * np.sin(lam / 2) ** 2)
    xc = xr - xr.mean()
    sxx = float((xc ** 2).sum())
    if sxx <= 0:
        return np.nan, np.nan
    slope = float((xc * (y - y.mean())).sum() / sxx)
    return -slope, float(np.pi / np.sqrt(6 * sxx))


def psi_nu(acf, nu):
    """按式 5.3 计算 Psi_nu = sum_{m>=1} nu^m rho(m)。"""
    m = np.arange(1, len(acf) + 1)
    w = nu ** m
    return float(np.sum(w * acf))


def closed_form_sharpe(acf, span, mu_z_an, a=A_TRADING, cost_bp=0.0):
    """按式 5.12/5.8 算毛夏普、式 5.13 算净夏普、并解出临界成本，返回 (SR, SR_net, c_be_bp)。"""
    nu = 1.0 - 2.0 / (span + 1.0)
    psi = psi_nu(acf, nu)
    a_nu = (1 - nu) / nu * psi
    b_nu = (1 - nu) / (1 + nu) * (1 + 2 * psi)
    drift2 = mu_z_an ** 2
    denom_sq = b_nu + a_nu ** 2 + (drift2 / a) * (1 + b_nu + 2 * a_nu)
    if denom_sq <= 0:
        return np.nan, np.nan, np.nan
    sr = (np.sqrt(a) * a_nu + drift2 / np.sqrt(a)) / np.sqrt(denom_sq)
    unit_drag = (2 * a / np.sqrt(np.pi)) * (1 - nu) / np.sqrt((1 + nu) * denom_sq)
    c_be_bp = sr / unit_drag * 1e4 if unit_drag > 0 else np.nan
    return float(sr), float(sr - unit_drag * cost_bp * 1e-4), float(c_be_bp)


def analyze_one(name, ret, layer, cost_oneway_bp):
    """对单个标的走完全链路，返回一行结果字典。"""
    r = pd.Series(ret).astype(float).replace([np.inf, -np.inf], np.nan).dropna()
    if len(r) < MIN_OBS:
        return None
    z = vol_normalize(r)
    if len(z) < MIN_OBS:
        return None
    acf = sample_acf(z)
    if len(acf) < 20:
        return None
    zv = np.asarray(z, dtype=float)
    sigma_ann = float(np.std(np.asarray(r, dtype=float), ddof=1)) * np.sqrt(A_TRADING)
    mu_z_an = float(np.sqrt(A_TRADING) * zv.mean() / np.std(zv, ddof=1))
    d, d_se = gph_d(z)
    c_norm_bp = cost_oneway_bp / sigma_ann if sigma_ann > 0 else np.nan

    half = len(z) // 2
    phi_h1 = float(sample_acf(z.iloc[:half], 5)[0]) if half >= MIN_OBS // 2 else np.nan
    phi_h2 = float(sample_acf(z.iloc[half:], 5)[0]) if half >= MIN_OBS // 2 else np.nan
    phi_se = 1.0 / np.sqrt(len(z))

    row = {
        "layer": layer,
        "name": name,
        "n_obs": len(z),
        "sigma_ann": sigma_ann,
        "phi": float(acf[0]),
        "phi_t": float(acf[0]) / phi_se,
        "phi_h1": phi_h1,
        "phi_h2": phi_h2,
        "phi_raw": float(sample_acf(r, 5)[0]),
        "acf_sum_20": float(acf[:20].sum()),
        "d_gph": d,
        "d_se": d_se,
        "d_t": d / d_se if d_se and d_se > 0 else np.nan,
        "mu_z_an": mu_z_an,
        "c_norm_bp": c_norm_bp,
    }
    best_span, best_net, best_be = np.nan, -np.inf, np.nan
    for span in SPANS:
        sr, sr_net, c_be = closed_form_sharpe(acf, span, mu_z_an, cost_bp=c_norm_bp)
        row[f"sr_{span}"] = sr
        row[f"srnet_{span}"] = sr_net
        row[f"cbe_{span}"] = c_be * sigma_ann if not np.isnan(c_be) else np.nan
        if sr_net is not None and not np.isnan(sr_net) and sr_net > best_net:
            best_net, best_span, best_be = sr_net, span, c_be
    row["best_span"] = best_span
    row["best_srnet"] = best_net if best_net > -np.inf else np.nan
    row["cbe_real_bp"] = best_be * sigma_ann if not np.isnan(best_be) else np.nan
    row["cost_headroom"] = row["cbe_real_bp"] / cost_oneway_bp if not np.isnan(best_be) else np.nan
    return row


def summarize(df):
    """打印分层中位数汇总。"""
    cols = ["n_obs", "sigma_ann", "phi", "phi_t", "phi_h1", "phi_h2", "d_gph", "d_t",
            "mu_z_an", "c_norm_bp", "best_span", "best_srnet", "cbe_real_bp", "cost_headroom"]
    print("\n=== 分层中位数 ===")
    g = df.groupby("layer")[cols].median()
    g.insert(0, "n", df.groupby("layer").size())
    print(g.to_string(float_format=lambda v: f"{v:.4f}"))

    print("\n=== 正趋势性占比 ===")
    stat = df.groupby("layer").apply(
        lambda x: pd.Series(
            {
                "phi>0": (x["phi"] > 0).mean(),
                "phi显著>0": (x["phi_t"] > 1.96).mean(),
                "phi两半同号+": ((x["phi_h1"] > 0) & (x["phi_h2"] > 0)).mean(),
                "d>0": (x["d_gph"] > 0).mean(),
                "d显著>0": (x["d_t"] > 1.96).mean(),
                "净夏普>0": (x["best_srnet"] > 0).mean(),
                "净夏普>0.3": (x["best_srnet"] > 0.3).mean(),
            }
        ),
        include_groups=False,
    )
    print(stat.to_string(float_format=lambda v: f"{v:.1%}"))

    print("\n=== 各跨度净夏普中位数 ===")
    net_cols = [f"srnet_{s}" for s in SPANS]
    tbl = df.groupby("layer")[net_cols].median()
    tbl.columns = [str(s) for s in SPANS]
    print(tbl.to_string(float_format=lambda v: f"{v:.3f}"))

    print("\n=== 各跨度临界单边成本中位数 bp（实际单边：ETF 1.1 / 其余 3.6）===")
    be_cols = [f"cbe_{s}" for s in SPANS]
    tbl2 = df.groupby("layer")[be_cols].median()
    tbl2.columns = [str(s) for s in SPANS]
    print(tbl2.to_string(float_format=lambda v: f"{v:.1f}"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2016-01-04")
    ap.add_argument("--layers", default="broad,industry,concept,stock")
    ap.add_argument("--min-amount", type=float, default=5000)
    args = ap.parse_args()

    loaders = {"broad": load_broad, "industry": load_industry, "concept": load_concept, "stock": load_stock}
    con = _con()
    rows = []
    try:
        for layer in args.layers.split(","):
            layer = layer.strip()
            if layer not in loaders:
                continue
            print(f"[{layer}] loading ...", flush=True)
            if layer == "stock":
                series = loaders[layer](con, args.start, args.min_amount)
            else:
                series = loaders[layer](con, args.start)
            print(f"[{layer}] {len(series)} series", flush=True)
            for code, (name, ret) in series.items():
                r = analyze_one(name, ret, layer, COST_ONEWAY_BP[layer])
                if r is not None:
                    r["code"] = code
                    rows.append(r)
            print(f"[{layer}] {sum(1 for r in rows if r['layer'] == layer)} analyzed", flush=True)
    finally:
        con.close()

    df = pd.DataFrame(rows)
    front = ["layer", "code", "name", "n_obs", "phi", "d_gph", "d_t", "mu_z_an", "best_span", "best_srnet"]
    df = df[front + [c for c in df.columns if c not in front]]
    df.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
    print(f"\nsaved -> {OUT_CSV}  ({len(df)} rows)")
    summarize(df)


if __name__ == "__main__":
    main()
