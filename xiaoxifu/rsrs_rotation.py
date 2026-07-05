"""
ETF 轮动 + RSRS 大盘择时开关(复现 量化君「RSRS加持ETF轮动,年化91.6%」思路)。

轮动照旧:主题窗口池 + 效率/双动量,每K日选最强动量ETF(见 theme_rotation)。
RSRS开关:对沪深300算标准分RSRS,**仅当RSRS看多(信号=1,大盘氛围好)才持有轮动选出的ETF,看空则空仓**。
对照 有/无 RSRS 开关,看择时增强多少。数据走 tushare,不碰本地库。

用法:python xiaoxifu/rsrs_rotation.py [--start 2015-01-01 --k 5]
"""
import argparse
import pandas as pd
import engine
from dual_momentum import POOL as PLAIN, load_fund_full, slope_score, eff_score
from theme_rotation import build_active, kfreq_holdings, THEMES
from rsrs import load_index, rsrs_signal


def _perf(pct):
    p = engine.perf(pct)
    return p


def run(px, active, gate, ns, ne, ws, we, k):
    """轮动持仓(可选被RSRS开关gate置空),返回 perf + 换手。gate=None 则不择时。"""
    rets = px.pct_change(fill_method=None)
    hold = kfreq_holdings(slope_score(px, ns), eff_score(px, ne), active, ws, we, k)
    if gate is not None:
        g = gate.reindex(hold.index).fillna(0)
        hold = hold.where(g.values > 0, None)
    w = pd.DataFrame(0.0, index=px.index, columns=px.columns)
    for d, c in hold.items():
        if c is not None:
            w.loc[d, c] = 1.0
    hs_ = hold.fillna("CASH")
    sw = int((hs_ != hs_.shift(1)).sum() - 1)
    p = engine.perf(engine.net_returns(w, rets, engine.COMM_ETF, engine.STAMP_ETF))
    p["开平仓"] = sw * 2
    p["持仓占比"] = round((hold.notna().sum() / len(hold)) * 100, 1)
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2015-01-01")
    ap.add_argument("--end", default=pd.Timestamp.today().strftime("%Y-%m-%d"))
    ap.add_argument("--ns", type=int, default=20)
    ap.add_argument("--ne", type=int, default=20)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--rn", type=int, default=18, help="RSRS斜率窗口N")
    ap.add_argument("--rm", type=int, default=600, help="RSRS标准分观察期M")
    args = ap.parse_args()

    ashare = ["510300.SH", "588000.SH", "159682.SZ"] + [c for c in THEMES]   # 纯A股股票ETF(RSRS对口)
    codes = list(dict.fromkeys(list(PLAIN) + list(THEMES) + ashare))
    print(f"拉取 {len(codes)} 只 ETF + 沪深300 ...")
    px = load_fund_full(codes, args.start, args.end)
    active = build_active(px)
    px_a = px[[c for c in ashare if c in px.columns]]
    active_a = build_active(px_a)
    hs = load_index("000300.SH", args.start, args.end)
    gate = rsrs_signal(hs, args.rn, args.rm).reindex(px.index).ffill().fillna(0)
    gate_a = gate.reindex(px_a.index).ffill().fillna(0)

    rows = {}
    rows["跨资产池 效率K5 无RSRS"] = run(px, active, None, args.ns, args.ne, 0, 1, args.k)
    rows["跨资产池 效率K5 +RSRS"] = run(px, active, gate, args.ns, args.ne, 0, 1, args.k)
    rows["纯A股池 效率K5 无RSRS"] = run(px_a, active_a, None, args.ns, args.ne, 0, 1, args.k)
    rows["纯A股池 效率K5 +RSRS"] = run(px_a, active_a, gate_a, args.ns, args.ne, 0, 1, args.k)
    rows["朴素池等权"] = engine.perf(px[[c for c in PLAIN if c in px.columns]].pct_change(fill_method=None).mean(axis=1))

    print(f"\nETF轮动 + RSRS大盘开关  K={args.k} RSRS(N{args.rn},M{args.rm})  {args.start}~{args.end}")
    print(pd.DataFrame(rows).T.to_string())


if __name__ == "__main__":
    main()
