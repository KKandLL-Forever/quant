"""etf_flow_timing.py — 用宽基ETF「净申赎」做择时工具的测算(观察:大额净申购≈国家队托底/市场弱底;大额净赎回≈市场强/见顶)。

解决两个问题:
1) 量级漂移:净申赎金额随时间数量级变化,不能直接比 → 归一化为「净申赎额 / 篮子AUM」(占比,尺度无关);
2) 持续性:信号是数周~数月的持续行为 → 用 20/60日滚动累计净申赎(%AUM),配 z-score/分位定义信号强弱。

数据:主库 etf_share(10只宽基ETF份额)+ fund_daily(价格)+ index_daily 上证综指。
产出:滚动净申赎分档→前瞻上证收益;相关性;历史关键段核对;信号阈值建议。纯本地、只打印。
用法:python research/etf_flow_timing.py
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
import numpy as np
import pandas as pd
import duckdb
from cache_tushare import DUCKDB_PATH, ETF_SHARE_LIST


def main():
    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    ph = ",".join(["?"] * len(ETF_SHARE_LIST))
    sh = con.execute(f"SELECT ts_code,trade_date,total_share FROM etf_share WHERE ts_code IN ({ph})", ETF_SHARE_LIST).df()
    px = con.execute(f"SELECT ts_code,trade_date,close FROM fund_daily WHERE ts_code IN ({ph})", ETF_SHARE_LIST).df()
    idx = con.execute("SELECT trade_date,close FROM index_daily WHERE ts_code='000001.SH' ORDER BY trade_date").df()
    con.close()
    for d in (sh, px, idx):
        d["trade_date"] = pd.to_datetime(d["trade_date"])
    sh_ = sh.merge(px, on=["ts_code", "trade_date"], how="inner")
    sh_["aum"] = sh_["total_share"] * sh_["close"]                       # 万份×元=万元
    sh_ = sh_.sort_values(["ts_code", "trade_date"])
    sh_["flow"] = sh_.groupby("ts_code")["total_share"].diff() * sh_["close"]   # 万元 净申赎额
    g = sh_.groupby("trade_date").agg(flow=("flow", "sum"), aum=("aum", "sum")).dropna()
    g["flow_pct"] = g["flow"] / g["aum"] * 100                           # 当日净申赎占AUM%
    g["r20"] = g["flow_pct"].rolling(20).sum()                          # 20日累计
    g["r60"] = g["flow_pct"].rolling(60).sum()                          # 60日累计
    g["z60"] = (g["r60"] - g["r60"].rolling(250).mean()) / g["r60"].rolling(250).std()   # 60日累计的1年滚动z
    sh_idx = idx.set_index("trade_date")["close"]
    g = g.join(sh_idx.rename("sh")).dropna(subset=["sh"])
    for h in (20, 60, 120):
        g[f"fwd{h}"] = g["sh"].shift(-h) / g["sh"] - 1

    print(f"样本 {len(g)} | 区间 {g.index.min().date()}~{g.index.max().date()} | 篮子AUM最新 {g['aum'].iloc[-1]/1e4:.0f}亿元")
    print(f"日净申赎占AUM% 分布: 均值 {g['flow_pct'].mean():+.3f} 标准差 {g['flow_pct'].std():.3f}")
    print(f"60日累计净申赎%AUM 分布: 5% {g['r60'].quantile(.05):+.1f} / 中位 {g['r60'].median():+.1f} / 95% {g['r60'].quantile(.95):+.1f}")

    print("\n=== 60日累计净申赎(%AUM) 五分档 → 前瞻上证收益均值 ===")
    g["q"] = pd.qcut(g["r60"], 5, labels=["Q1最净赎回", "Q2", "Q3", "Q4", "Q5最净申购"])
    tab = (g.groupby("q", observed=True)[["fwd20", "fwd60", "fwd120"]].mean() * 100).round(1)
    tab["r60区间"] = g.groupby("q", observed=True)["r60"].agg(lambda s: f"{s.min():+.0f}~{s.max():+.0f}")
    tab["n"] = g.groupby("q", observed=True).size()
    print(tab)
    print("\n相关系数(r60 vs 前瞻):", {h: round(g["r60"].corr(g[f"fwd{h}"]), 3) for h in (20, 60, 120)})
    print("相关系数(z60 vs 前瞻):", {h: round(g["z60"].corr(g[f"fwd{h}"]), 3) for h in (20, 60, 120)})

    print("\n=== 极端净申购(r60 前5%分位以上)后 前瞻上证 ===")
    thr_hi = g["r60"].quantile(.95)
    hi = g[g["r60"] >= thr_hi]
    print(f"阈值 r60≥{thr_hi:.1f}% | {len(hi)}天 | 前瞻20/60/120日均值 "
          f"{hi['fwd20'].mean()*100:+.1f}% / {hi['fwd60'].mean()*100:+.1f}% / {hi['fwd120'].mean()*100:+.1f}% "
          f"| 胜率(120日) {(hi['fwd120']>0).mean()*100:.0f}%")
    print("=== 极端净赎回(r60 后5%分位以下)后 前瞻上证 ===")
    thr_lo = g["r60"].quantile(.05)
    lo = g[g["r60"] <= thr_lo]
    print(f"阈值 r60≤{thr_lo:.1f}% | {len(lo)}天 | 前瞻20/60/120日均值 "
          f"{lo['fwd20'].mean()*100:+.1f}% / {lo['fwd60'].mean()*100:+.1f}% / {lo['fwd120'].mean()*100:+.1f}% "
          f"| 胜率(120日) {(lo['fwd120']>0).mean()*100:.0f}%")

    print("\n=== 历史关键段 核对(每段取该段内 r60 极值日) ===")
    for lab, a, b in [("2015股灾底", "2015-08-01", "2015-10-31"), ("2018熊末", "2018-10-01", "2019-01-31"),
                      ("2020疫情底", "2020-02-01", "2020-04-30"), ("2022-04底", "2022-04-01", "2022-05-31"),
                      ("2022-10底", "2022-10-01", "2022-11-30"), ("2024-02汇金", "2024-01-15", "2024-03-15"),
                      ("2024-09反弹", "2024-09-01", "2024-10-15")]:
        seg = g[(g.index >= a) & (g.index <= b)]
        if seg.empty:
            print(f"  {lab}: 无数据"); continue
        d_hi = seg["r60"].idxmax()
        print(f"  {lab}: 段内 r60 峰值 {seg['r60'].max():+.1f}%@{d_hi.date()} (z60 {seg.loc[d_hi,'z60']:+.1f}) → 其后120日上证 {g.loc[d_hi,'fwd120']*100:+.1f}%")

    g.to_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "etf_flow_timing.csv"), encoding="utf-8-sig")


if __name__ == "__main__":
    main()
