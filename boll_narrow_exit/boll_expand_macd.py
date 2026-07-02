"""
BOLL 缩口→扩张 + MACD 金叉 择时研究(中证1000)。

信号(震荡尾部的向上扩张):个股 BOLL 通道先「缩口」(带宽处于过去120日低分位=震荡盘整),
随后当日带宽「扩大」,且 MACD 近3日内金叉(DIF 上穿 DEA、当前在上方),同时收盘站上中轨(向上扩张)。
在该点买入,统计其后 5/10/20 日平均涨幅,与全样本无条件均值对比看有无超额;并检验 ATR 是否有过滤价值。

标的池:中证1000(000852.SH)当前成分(tushare index_weight;有幸存者偏差,研究性口径)。
指标:本地 DuckDB stk_factor_pro(后复权 BOLL/MACD/ATR);前瞻收益用 daily 后复权收盘。
用法:python boll_narrow_exit/boll_expand_macd.py [--start 2021-01-01 --squeeze-q 0.25 --cross-win 3]
"""
import os
_ROOT = os.path.dirname(os.path.abspath(__file__))
while _ROOT != "/" and not os.path.exists(os.path.join(_ROOT, "cache_tushare.py")):
    _ROOT = os.path.dirname(_ROOT)
import sys
sys.path.insert(0, _ROOT)
import argparse
import numpy as np
import pandas as pd
import duckdb
import cache_tushare as ct


def members_1000():
    """取中证1000最新成分代码列表(tushare index_weight)。"""
    import tushare as ts
    pro = ts.pro_api(ct._get_token())
    w = pro.index_weight(index_code="000852.SH", start_date="20260101", end_date="20261231")
    w = w[w["trade_date"] == w["trade_date"].max()]
    return sorted(w["con_code"].unique().tolist())


def load(codes, start):
    """读成分股的后复权收盘 + BOLL/MACD/ATR 指标,返回按 (ts_code,trade_date) 排序的宽表。"""
    con = duckdb.connect(ct.DUCKDB_PATH, read_only=True)
    df = con.execute(
        """SELECT f.ts_code, f.trade_date td, d.close*a.adj_factor AS adjc,
                  f.macd_dif_hfq dif, f.macd_dea_hfq dea,
                  f.boll_upper_hfq bu, f.boll_mid_hfq bm, f.boll_lower_hfq bl, f.atr_hfq atr
           FROM stk_factor_pro f
           JOIN daily d ON d.ts_code=f.ts_code AND d.trade_date=f.trade_date
           JOIN adj_factor a ON a.ts_code=f.ts_code AND a.trade_date=f.trade_date
           WHERE f.ts_code IN (SELECT UNNEST(?)) AND f.trade_date>=?
           ORDER BY f.ts_code, f.trade_date""",
        [list(codes), start],
    ).fetch_df()
    con.close()
    return df


def build_signals(df, squeeze_q, cross_win):
    """逐股算 BOLL缩口→扩张 + MACD近期金叉 + 站上中轨 的买点,返回带前瞻收益/ATR 的信号表。"""
    out = []
    for ts, g in df.groupby("ts_code", sort=False):
        g = g.reset_index(drop=True)
        if len(g) < 140:
            continue
        bw = (g["bu"] - g["bl"]) / g["bm"]
        narrow = bw <= bw.rolling(120, min_periods=60).quantile(squeeze_q)
        widen = bw > bw.shift(1)
        d = g["dif"] - g["dea"]
        crossed = (d > 0) & pd.concat([d.shift(k) <= 0 for k in range(1, cross_win + 1)], axis=1).any(axis=1)
        up = g["adjc"] > g["bm"]
        sig = narrow.shift(1, fill_value=False) & widen & crossed & up
        adjc = g["adjc"]
        f5 = adjc.shift(-5) / adjc - 1
        f10 = adjc.shift(-10) / adjc - 1
        f20 = adjc.shift(-20) / adjc - 1
        atr_pct = g["atr"] / adjc
        s = pd.DataFrame({"ts_code": ts, "date": g["td"], "f5": f5, "f10": f10, "f20": f20, "atr_pct": atr_pct})[sig]
        out.append(s[s["f10"].notna()])
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def main():
    """跑研究并打印:信号数、前瞻收益、胜率、vs 基准、ATR 分桶与过滤效果。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--squeeze-q", type=float, default=0.25, help="缩口分位阈值(带宽低于过去120日该分位=震荡)")
    ap.add_argument("--cross-win", type=int, default=3, help="MACD 金叉发生在最近几日内")
    args = ap.parse_args()

    codes = members_1000()
    print(f"中证1000 成分 {len(codes)} 只,回看起点 {args.start}")
    df = load(codes, args.start)
    print(f"行情+指标 {len(df):,} 行,{df['ts_code'].nunique()} 只有数据")

    sig = build_signals(df, args.squeeze_q, args.cross_win)
    base = df["adjc"].groupby(df["ts_code"]).transform(lambda x: x.shift(-10) / x - 1).dropna()
    print(f"\n=== 信号(BOLL缩口→扩张 + MACD近{args.cross_win}日金叉 + 站上中轨)===")
    print(f"信号数 {len(sig)}")
    if len(sig):
        for k, lbl in (("f5", "5日"), ("f10", "10日"), ("f20", "20日")):
            v = sig[k].dropna()
            print(f"  {lbl}前瞻:均值 {v.mean()*100:+.2f}%  中位 {v.median()*100:+.2f}%  胜率 {(v>0).mean()*100:.0f}%  (n={len(v)})")
        print(f"  基准(全样本10日均值):{base.mean()*100:+.2f}%  →  信号超额 {(sig['f10'].mean()-base.mean())*100:+.2f}%")

        print("\n=== ATR 是否有过滤价值(按入场 ATR/价 分5桶,看10日均值)===")
        sig2 = sig.copy()
        sig2["atr_bucket"] = pd.qcut(sig2["atr_pct"], 5, labels=["Q1低波", "Q2", "Q3", "Q4", "Q5高波"])
        tb = sig2.groupby("atr_bucket", observed=True)["f10"].agg(["mean", "median", "count"])
        for idx, row in tb.iterrows():
            print(f"  {idx}: 均值 {row['mean']*100:+.2f}%  中位 {row['median']*100:+.2f}%  (n={int(row['count'])})")
        lo = sig2[sig2["atr_pct"] <= sig2["atr_pct"].median()]["f10"]
        print(f"  低ATR过滤(≤中位):10日均值 {lo.mean()*100:+.2f}%  胜率 {(lo>0).mean()*100:.0f}%  (n={len(lo)})")


if __name__ == "__main__":
    main()
