"""
ETF 双动量轮动(复现 量化君「斜率动量 + 效率动量」)。

两套趋势打分:
  斜率动量  = ln收盘价 n_slope 日线性回归斜率 × 决定系数R²   (RSRS/光大金工式)
  效率动量  = 区间涨跌幅(带符号) × 效率系数ER               ER=|位移|/路程 ∈[0,1]
合成:池内各 ETF 分别按两动量降序排名(1=最优),按权重加总成综合排名,选综合排名第一的 1 只,每日调仓。
冷却期:min_hold 个交易日内不卖;但若持仓综合排名跌破 force_rank 则强制卖(0=不启用)。
成本:场内 ETF 佣金双边 0.02%、免印花。数据走 tushare fund_daily+fund_adj 前复权,不碰本地 DuckDB。

用法:python xiaoxifu/dual_momentum.py [--start 2015-01-01] [--ns 20 --ne 20] [--min-hold 0 --force-rank 0]
候选池为临时全天候池(见 POOL),原文 CODE_LIST 未公开,结果对池高度敏感。
"""
import argparse
import numpy as np
import pandas as pd
import engine
import cache_tushare as ct


def load_fund_full(codes, start, end):
    """分年拉 tushare fund_daily+fund_adj 前复权(破单次2000行上限),返回宽表。"""
    import tushare as ts
    pro = ts.pro_api(ct._get_token())
    yrs = range(int(start[:4]), int(end[:4]) + 1)
    cols = {}
    for c in codes:
        ds, as_ = [], []
        for y in yrs:
            s, e = f"{y}0101", f"{y}1231"
            d = pro.fund_daily(ts_code=c, start_date=s, end_date=e, fields="trade_date,close")
            a = pro.fund_adj(ts_code=c, start_date=s, end_date=e, fields="trade_date,adj_factor")
            if len(d):
                ds.append(d)
            if len(a):
                as_.append(a)
        if not ds or not as_:
            continue
        d, a = pd.concat(ds), pd.concat(as_)
        m = d.merge(a, on="trade_date")
        m["dt"] = pd.to_datetime(m["trade_date"])
        m = m.drop_duplicates("dt").sort_values("dt").set_index("dt")
        cols[c] = m["close"] * m["adj_factor"] / m["adj_factor"].iloc[-1]
    return pd.DataFrame(cols).sort_index()

POOL = {
    "510300.SH": "沪深300", "510500.SH": "中证500", "159915.SZ": "创业板",
    "513100.SH": "纳指", "513500.SH": "标普500", "518880.SH": "黄金",
    "511260.SH": "十年国债", "162411.SZ": "华宝油气", "513050.SH": "中概互联",
    "511990.SH": "华宝添益",
}
BENCH = "510300.SH"


def _slope_r2(y):
    """一段 ln价 序列的 线性回归斜率×R²(NaN 时返 nan)。"""
    if np.isnan(y).any():
        return np.nan
    x = np.arange(len(y), dtype=float)
    sl, ic = np.polyfit(x, y, 1)
    yhat = sl * x + ic
    ss_res = ((y - yhat) ** 2).sum()
    ss_tot = ((y - y.mean()) ** 2).sum()
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return sl * r2


def slope_score(px, n):
    """宽表各 ETF 的 斜率动量 = ln价 n 日斜率×R²。"""
    return np.log(px).rolling(n).apply(_slope_r2, raw=True)


def eff_score(px, n):
    """宽表各 ETF 的 效率动量 = n 日区间涨跌幅 × 效率系数(位移/路程)。"""
    disp = px - px.shift(n)
    ret = px / px.shift(n) - 1
    path = px.diff().abs().rolling(n).sum()
    er = disp.abs() / path.replace(0, np.nan)
    return ret * er


def build_holdings(sc_slope, sc_eff, w_slope, w_eff, min_hold, force_rank):
    """按综合排名逐日决定持仓(top1)+冷却期规则,返回 持仓代码 Series(index=日期,空仓为 None)。"""
    r_slope = sc_slope.rank(axis=1, ascending=False)
    r_eff = sc_eff.rank(axis=1, ascending=False)
    comb = w_slope * r_slope + w_eff * r_eff
    dates = comb.index
    hold, held = None, 0
    out = []
    for d in dates:
        row = comb.loc[d].dropna()
        if row.empty:
            out.append(hold); held += (hold is not None); continue
        target = row.idxmin()
        if hold is None or hold not in row.index:
            hold, held = target, 1
        elif hold == target:
            held += 1
        else:
            cur_rank = row.rank(ascending=True).get(hold, np.inf)
            if held >= min_hold or (force_rank > 0 and cur_rank > force_rank):
                hold, held = target, 1
            else:
                held += 1
        out.append(hold)
    return pd.Series(out, index=dates)


def backtest(px, ns, ne, w_slope, w_eff, min_hold=0, force_rank=0):
    """跑一次回测,返回 (日收益 Series, 换手次数)。"""
    rets = px.pct_change(fill_method=None)
    hold = build_holdings(slope_score(px, ns), eff_score(px, ne), w_slope, w_eff, min_hold, force_rank)
    w = pd.DataFrame(0.0, index=px.index, columns=px.columns)
    for d, c in hold.items():
        if c is not None:
            w.loc[d, c] = 1.0
    switches = int((hold != hold.shift(1)).sum() - 1)
    strat = engine.net_returns(w, rets, engine.COMM_ETF, engine.STAMP_ETF)
    return strat, switches


def main():
    """CLI:分别跑 斜率/效率/双动量/双动量+冷却,与沪深300、等权对照。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2015-01-01")
    ap.add_argument("--end", default=pd.Timestamp.today().strftime("%Y-%m-%d"))
    ap.add_argument("--ns", type=int, default=20, help="斜率动量窗口")
    ap.add_argument("--ne", type=int, default=20, help="效率动量窗口")
    ap.add_argument("--min-hold", type=int, default=0)
    ap.add_argument("--force-rank", type=int, default=0)
    args = ap.parse_args()

    print(f"拉取 {len(POOL)} 只 ETF ({args.start}~{args.end}) ...")
    px = load_fund_full(list(POOL.keys()), args.start, args.end)
    print("到齐:", ", ".join(f"{POOL[c]}({px[c].notna().sum()}d)" for c in px.columns))

    configs = [
        ("斜率动量", 1.0, 0.0, 0, 0),
        ("效率动量", 0.0, 1.0, 0, 0),
        ("双动量(等权)", 1.0, 1.0, 0, 0),
        (f"双动量+冷却(h{args.min_hold or 3},r{args.force_rank or 3})", 1.0, 1.0, args.min_hold or 3, args.force_rank or 3),
    ]
    rows = {}
    for name, ws, we, mh, fr in configs:
        strat, sw = backtest(px, args.ns, args.ne, ws, we, mh, fr)
        p = engine.perf(strat)
        p["开平仓次数"] = sw * 2
        rows[name] = p
    rows["沪深300"] = engine.perf(px[BENCH].pct_change(fill_method=None)) if BENCH in px.columns else {}
    rows["等权池"] = engine.perf(px.pct_change(fill_method=None).mean(axis=1))

    df = pd.DataFrame(rows).T
    print(f"\n双动量 ETF 轮动  斜率N={args.ns} 效率N={args.ne} 每日调仓  {args.start}~{args.end}")
    print(df.to_string())


if __name__ == "__main__":
    main()
