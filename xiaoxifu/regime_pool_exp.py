"""
行情自适应换池 实验(在双动量 ETF 轮动上加两层):
  ①趋势门控(因果):某ETF 需 站上MA200 且 斜率>0 且 效率系数ER≥阈值 才准入选;全池无一合格→退防御(债/黄金,均弱则空仓)。
  ②行业换池(温和先知):在人工标注的"行情中段窗口"(行情开始后~2月 到 结束前~2月)把对应行业ETF并进候选池,只吃中段。

三档对照,拆出两部分贡献:
  Run1 基线      = 朴素池 + 双动量 top1(永远满仓)
  Run2 +门控     = 朴素池 + 趋势门控 + 防御回退(纯因果,无先知)
  Run3 +行业换池 = Run2 且 中段窗口并入行业ETF(含后视镜)
  Run2-Run1 = 防御门控价值(可实盘);Run3-Run2 = 行业换池价值(先知代价)。

数据走 tushare,不碰本地库。用法:python xiaoxifu/regime_pool_exp.py [--start 2015-01-01 --er 0.3]
行情窗口为人工标注(见 EVENTS),中段已内含±2月缩边,不完美,仅估算这套想法的上下限。
"""
import argparse
import numpy as np
import pandas as pd
import engine
from dual_momentum import POOL as PLAIN, BENCH, load_fund_full, slope_score, eff_score, backtest

DEFENSIVE = ["511260.SH", "518880.SH"]

EVENTS = [
    ("512690.SH", "白酒", "2019-07-01", "2020-12-01"),
    ("512760.SH", "芯片", "2019-08-01", "2020-05-01"),
    ("512010.SH", "医药", "2020-01-01", "2021-05-01"),
    ("512660.SH", "军工", "2020-09-01", "2021-06-01"),
    ("515030.SH", "新能源车", "2020-06-01", "2021-09-01"),
    ("515790.SH", "光伏", "2021-02-01", "2022-06-01"),
    ("515220.SH", "煤炭", "2021-06-01", "2023-06-01"),
    ("512980.SH", "传媒", "2023-03-01", "2023-04-15"),
    ("515980.SH", "AI", "2023-03-01", "2023-04-15"),
    ("510880.SH", "红利", "2022-03-01", "2024-06-01"),
]


def _er(px, n):
    """效率系数 ER=|位移|/路程,宽表。"""
    disp = (px - px.shift(n)).abs()
    path = px.diff().abs().rolling(n).sum()
    return disp / path.replace(0, np.nan)


def gated_holdings(px, active, ns, ne, w_slope, w_eff, er_thr, min_hold=5):
    """趋势门控 + 防御回退 + 冷却期(降whipsaw),逐日 top1 持仓 Series(空仓为 None)。

    冷却:持仓未满 min_hold 日不换;但持有的股票型标的一旦跌破MA200(硬止损)立即换。防御标的满冷却期后重评。"""
    sl = slope_score(px, ns)
    ef = eff_score(px, ne)
    er = _er(px, ne)
    ma_ok = px > px.rolling(200).mean()
    r_slope = sl.rank(axis=1, ascending=False)
    r_eff = ef.rank(axis=1, ascending=False)
    comb = w_slope * r_slope + w_eff * r_eff
    ret20 = px / px.shift(20) - 1
    hold, held = None, 0
    out = []
    for d in px.index:
        univ = active.loc[d] & px.loc[d].notna()
        passer = univ & ma_ok.loc[d].fillna(False) & (sl.loc[d] > 0) & (er.loc[d] >= er_thr) & (ef.loc[d] > 0)
        cand = comb.loc[d][passer[passer].index].dropna()
        best = cand.idxmin() if len(cand) else None
        if best is None:
            dfd = ret20.loc[d, DEFENSIVE].dropna(); dfd = dfd[dfd > 0]
            best = dfd.idxmax() if len(dfd) else None
        broke = hold is not None and hold not in DEFENSIVE and not bool(ma_ok.loc[d].get(hold, False))
        if hold is not None and held < min_hold and not broke:
            held += 1
        elif best == hold:
            held += 1
        else:
            hold, held = best, 1
        out.append(hold)
    return pd.Series(out, index=px.index)


def _perf_from_holdings(px, hold):
    """持仓 Series → 扣费日收益 + 换手。"""
    rets = px.pct_change(fill_method=None)
    w = pd.DataFrame(0.0, index=px.index, columns=px.columns)
    for d, c in hold.items():
        if c is not None:
            w.loc[d, c] = 1.0
    switches = int((hold != hold.shift(1)).sum() - 1)
    strat = engine.net_returns(w, rets, engine.COMM_ETF, engine.STAMP_ETF)
    p = engine.perf(strat)
    p["开平仓次数"] = switches * 2
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2015-01-01")
    ap.add_argument("--end", default=pd.Timestamp.today().strftime("%Y-%m-%d"))
    ap.add_argument("--ns", type=int, default=20)
    ap.add_argument("--ne", type=int, default=20)
    ap.add_argument("--er", type=float, default=0.3)
    args = ap.parse_args()

    ev_codes = {c: n for c, n, _, _ in EVENTS}
    all_codes = list(PLAIN.keys()) + [c for c in ev_codes if c not in PLAIN]
    print(f"拉取 {len(all_codes)} 只 ETF ...")
    px = load_fund_full(all_codes, args.start, args.end)
    px_plain = px[[c for c in PLAIN if c in px.columns]]

    active_plain = pd.DataFrame(True, index=px.index, columns=px.columns)
    for c in ev_codes:
        if c in active_plain.columns and c not in PLAIN:
            active_plain[c] = False
    active_regime = active_plain.copy()
    for c, n, s, e in EVENTS:
        if c in active_regime.columns:
            active_regime.loc[(px.index >= s) & (px.index <= e), c] = True

    rows = {}
    strat1, sw1 = backtest(px_plain, args.ns, args.ne, 1.0, 1.0, 0, 0)
    p1 = engine.perf(strat1); p1["开平仓次数"] = sw1 * 2
    rows["Run1 基线(朴素池双动量)"] = p1
    rows["Run2 +门控(纯因果)"] = _perf_from_holdings(px, gated_holdings(px, active_plain, args.ns, args.ne, 1.0, 1.0, args.er))
    rows["Run3 +行业换池(含先知)"] = _perf_from_holdings(px, gated_holdings(px, active_regime, args.ns, args.ne, 1.0, 1.0, args.er))
    bpx = px[BENCH] if BENCH in px.columns else None
    if bpx is not None:
        rows["沪深300"] = engine.perf(bpx.pct_change(fill_method=None))
    rows["朴素池等权"] = engine.perf(px_plain.pct_change(fill_method=None).mean(axis=1))

    df = pd.DataFrame(rows).T
    print(f"\n行情自适应换池实验  ns={args.ns} ne={args.ne} ER≥{args.er}  {args.start}~{args.end}")
    print(df.to_string())


if __name__ == "__main__":
    main()
