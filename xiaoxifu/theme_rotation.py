"""
朴素池 + 大主题ETF 一起轮动(主题ETF自上市即入池,照旧斜率/效率/双动量,无门槛无窗口)。

候选池 = 朴素7-ETF池 + 5大主题(白酒/新能源车/消费/AI/AI硬件);主题ETF上市后有足够数据即参与排名(动量需~N日预热)。
打分照旧:斜率动量=ln价斜率×R²、效率动量=区间涨跌幅×ER、双动量=两者排名等权。
每 K 日调仓一次(K=1日频/5/10),期间持有不动,选综合排名第一的1只,永远满仓。

对比:三种动量 × K=1/5/10,加"只朴素池"基线与等权。数据走 tushare,不碰本地库。
用法:python xiaoxifu/theme_rotation.py [--start 2015-01-01]
主题ETF为事后已知赢家、且多在主题启动后上市(universe自带后视镜),结果为温和先知估算。
"""
import argparse
import numpy as np
import pandas as pd
import engine
from dual_momentum import POOL as PLAIN, load_fund_full, slope_score, eff_score
from theme_trend import THEMES

THEME_WINDOWS = [
    ("512690.SH", "白酒", "2019-05-06", "2020-12-01"),
    ("159928.SZ", "消费", "2019-01-01", "2020-12-01"),
    ("515030.SH", "新能源车", "2020-03-04", "2021-09-01"),
    ("512760.SH", "AI硬件", "2019-06-12", "2020-05-01"),
    ("512760.SH", "AI硬件", "2023-04-01", "2023-09-01"),
    ("512760.SH", "AI硬件", "2024-09-01", "2026-05-01"),
    ("515980.SH", "AI", "2023-01-01", "2023-04-15"),
    ("515980.SH", "AI", "2024-09-01", "2026-05-01"),
]


def build_active(px):
    """候选池成员资格 bool 宽表:朴素池常驻;主题ETF仅在各自窗口内(上市/行情起→顶前2月)。"""
    active = pd.DataFrame(True, index=px.index, columns=px.columns)
    for c in THEMES:
        if c in active.columns and c not in PLAIN:
            active[c] = False
    for c, n, s, e in THEME_WINDOWS:
        if c in active.columns:
            active.loc[(px.index >= s) & (px.index <= e), c] = True
    return active


def kfreq_holdings(sc_slope, sc_eff, active, w_slope, w_eff, k):
    """每 K 日在当日候选池内重选综合排名第一的1只,期间持有不动;返回逐日持仓 Series。"""
    comb = w_slope * sc_slope.rank(axis=1, ascending=False) + w_eff * sc_eff.rank(axis=1, ascending=False)
    hold, out = None, []
    for i, d in enumerate(comb.index):
        if i % k == 0:
            row = comb.loc[d][active.loc[d]].dropna()
            if len(row):
                hold = row.idxmin()
        out.append(hold)
    return pd.Series(out, index=comb.index)


def run(px, active, ns, ne, w_slope, w_eff, k):
    """跑一次:返回 perf dict(含开平仓)。"""
    rets = px.pct_change(fill_method=None)
    hold = kfreq_holdings(slope_score(px, ns), eff_score(px, ne), active, w_slope, w_eff, k)
    w = pd.DataFrame(0.0, index=px.index, columns=px.columns)
    for d, c in hold.items():
        if c is not None:
            w.loc[d, c] = 1.0
    sw = int((hold != hold.shift(1)).sum() - 1)
    p = engine.perf(engine.net_returns(w, rets, engine.COMM_ETF, engine.STAMP_ETF))
    p["开平仓"] = sw * 2
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2015-01-01")
    ap.add_argument("--end", default=pd.Timestamp.today().strftime("%Y-%m-%d"))
    ap.add_argument("--ns", type=int, default=20)
    ap.add_argument("--ne", type=int, default=20)
    args = ap.parse_args()

    codes = list(PLAIN) + [c for c in THEMES if c not in PLAIN]
    print(f"拉取 {len(codes)} 只 ETF(朴素{len(PLAIN)}+主题{len(THEMES)}) ...")
    px = load_fund_full(codes, args.start, args.end)
    px_plain = px[[c for c in PLAIN if c in px.columns]]
    active = build_active(px)
    active_plain = pd.DataFrame(True, index=px_plain.index, columns=px_plain.columns)

    weights = {"斜率": (1.0, 0.0), "效率": (0.0, 1.0), "双动量": (1.0, 1.0)}
    rows = {}
    for mname, (ws, we) in weights.items():
        for k in (1, 5, 10):
            rows[f"{mname} K={k}"] = run(px, active, args.ns, args.ne, ws, we, k)
    rows["双动量K=1(仅朴素池)"] = run(px_plain, active_plain, args.ns, args.ne, 1.0, 1.0, 1)
    rows["朴素池等权"] = engine.perf(px_plain.pct_change(fill_method=None).mean(axis=1))

    print(f"\n主题并入轮动网格  ns={args.ns} ne={args.ne}  {args.start}~{args.end}")
    print(pd.DataFrame(rows).T.to_string())


if __name__ == "__main__":
    main()
