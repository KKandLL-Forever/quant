"""
大主题月线趋势策略(按用户规则重做,砍掉日频whipsaw):
  候选=5个大主题ETF(白酒/新能源车/消费/AI/AI硬件半导体);每个 ETF **上市当月即买**(前3月宽限,不等MA5),
  月收盘 **跌破5月均线** 才离场。若无主题在5月线之上→熊市,躲 国债/黄金(取月动量较强者)。
  多个主题同时活着:优先最新上市的那个(上市即买),否则取3月动量最强。月频调仓,极低换手。

只留大行情(白酒/新能源/消费/AI/AI硬件),不含煤炭/军工/医药/宽基。数据走 tushare,不碰本地库。
用法:python xiaoxifu/theme_trend.py [--start 2015-01-01]
主题ETF大多主题启动后才上市(universe自带后视镜),此为"温和先知上限"估算,非可照搬实盘。
"""
import argparse
import numpy as np
import pandas as pd
import engine
from dual_momentum import load_fund_full

THEMES = {
    "512690.SH": "白酒", "515030.SH": "新能源车", "159928.SZ": "消费",
    "515980.SH": "AI", "512760.SH": "AI硬件(芯片)",
}
DEFENSIVE = {"511260.SH": "十年国债", "518880.SH": "黄金"}
NAME = {**THEMES, **DEFENSIVE}


def build_monthly_holdings(m):
    """月收盘宽表 → 逐月持仓代码 Series。上市≤3月宽限即买,破5月线离场,无主题活着则躲债金。"""
    ma5 = m.rolling(5).mean()
    bars = m.notna().cumsum()
    mom3 = m.pct_change(3)
    alive = (m > ma5) | ((bars <= 3) & m.notna())
    out = []
    for d in m.index:
        th = [c for c in THEMES if c in m.columns and alive.loc[d, c]]
        if th:
            fresh = [c for c in th if bars.loc[d, c] <= 3]
            if fresh:
                pick = min(fresh, key=lambda c: bars.loc[d, c])
            else:
                pick = max(th, key=lambda c: (mom3.loc[d, c] if pd.notna(mom3.loc[d, c]) else -9))
        else:
            dfd = {c: mom3.loc[d, c] for c in DEFENSIVE if c in m.columns and pd.notna(m.loc[d, c])}
            pick = max(dfd, key=dfd.get) if dfd else None
        out.append(pick)
    return pd.Series(out, index=m.index)


def perf_m(r):
    """月收益 Series → 年化/波动/回撤/夏普(按12期年化)。"""
    r = r.dropna()
    if len(r) < 2:
        return {}
    cum = (1 + r).cumprod()
    ann = cum.iloc[-1] ** (12 / len(r)) - 1
    vol = r.std() * np.sqrt(12)
    mdd = (cum / cum.cummax() - 1).min()
    return dict(年化收益=round(ann * 100, 2), 年化波动率=round(vol * 100, 2), 最大回撤=round(abs(mdd) * 100, 2),
                夏普比率=round(ann / vol, 3) if vol else None, 卡玛比率=round(ann / abs(mdd), 3) if mdd else None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2015-01-01")
    ap.add_argument("--end", default=pd.Timestamp.today().strftime("%Y-%m-%d"))
    ap.add_argument("--show", action="store_true", help="打印逐月持仓")
    args = ap.parse_args()

    codes = list(THEMES) + list(DEFENSIVE)
    print(f"拉取 {len(codes)} 只 ETF ...")
    px = load_fund_full(codes, args.start, args.end)
    m = px.resample("ME").last()
    mret = m.pct_change(fill_method=None)
    hold = build_monthly_holdings(m)

    w = pd.DataFrame(0.0, index=m.index, columns=m.columns)
    for d, c in hold.items():
        if c is not None:
            w.loc[d, c] = 1.0
    switches = int((hold != hold.shift(1)).sum() - 1)
    strat = (w.shift(1) * mret).sum(axis=1) - engine.COMM_ETF * (w - w.shift(1)).abs().sum(axis=1).shift(1).fillna(0)

    rows = {"大主题月线": {**perf_m(strat), "换手次数": switches}}
    rows["主题等权买入持有"] = perf_m(m[list(THEMES)].pct_change(fill_method=None).mean(axis=1))
    rows["黄金买入持有"] = perf_m(m["518880.SH"].pct_change(fill_method=None)) if "518880.SH" in m.columns else {}
    print(f"\n大主题月线趋势  破5月线离场  {args.start}~{args.end}")
    print(pd.DataFrame(rows).T.to_string())

    if args.show:
        print("\n逐月持仓:")
        prev = None
        for d, c in hold.items():
            if c != prev:
                print(f"  {d.date()}  {NAME.get(c, '空仓') if c else '空仓'}")
                prev = c


if __name__ == "__main__":
    main()
