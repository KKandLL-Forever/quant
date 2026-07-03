"""
最优配置稳健性检查(STANDARD_MODEL_WORKFLOW):参数敏感性 + 分年 + 邻域稳健占比。

最优 = ML池 + 第二次(30) + 大盘健康 + MA60上行 + RS跑赢 + 15日 + 30仓。
逐个参数在最优附近扰动,看夏普是否悬崖式依赖某个取值(过拟合)还是邻域普遍稳。
数据只加载一次复用。用法:python boll_narrow_exit/robustness.py
"""
import os
_ROOT = os.path.dirname(os.path.abspath(__file__))
while _ROOT != "/" and not os.path.exists(os.path.join(_ROOT, "cache_tushare.py")):
    _ROOT = os.path.dirname(_ROOT)
import sys
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
import boll_expand_macd as bm
from backtest import _slots, _daily_curve, _perf

FEE = 0.0008


def metrics(df, mkt, retmap, cal_all, squeeze=0.25, up="mid", hold=15, parts=30,
            repeat=30, ma60="up", rs="win"):
    """按给定参数跑一次回测,返回 (年化, 回撤, 夏普, 卡玛, 成交数, port日收益)。"""
    sig = bm.build_signals(df, squeeze, 3, up, hold).merge(mkt, on="date", how="left")
    sig = sig[(sig["mkt_up"] == True) & (sig["vol_ratio"] > 1) & sig["entry_date"].notna()
              & sig["exit_date"].notna() & (sig["mkt_bad"] == False)]
    if repeat:
        sig = sig.sort_values(["ts_code", "date"])
        sig = sig[sig.groupby("ts_code")["date"].diff().dt.days <= repeat]
    if ma60 == "up":
        sig = sig[sig["ma60_up"] == True]
    if rs == "win":
        sig = sig[(sig["mom20"] - sig["hs300_mom20"]) > 0]
    taken = _slots(sig, parts)
    cal = cal_all[cal_all >= taken["entry_date"].min()]
    port = _daily_curve(taken, retmap, cal, parts, FEE)
    ann, vol, mdd, shp, cal_r, tot = _perf(port)
    return ann, mdd, shp, cal_r, len(taken), port


def main():
    print("加载数据(一次)...")
    codes = bm.members_ml()
    df = bm.load(codes, "2021-01-01")
    df["td"] = pd.to_datetime(df["td"])
    df["ret"] = df.groupby("ts_code")["adjc"].pct_change(fill_method=None)
    mkt = bm.hs300_market("2021-01-01")
    retmap = {ts: g.set_index("td")["ret"] for ts, g in df[["ts_code", "td", "ret"]].groupby("ts_code")}
    cal_all = pd.DatetimeIndex(sorted(df["td"].unique()))

    def row(tag, **kw):
        ann, mdd, shp, car, n, port = metrics(df, mkt, retmap, cal_all, **kw)
        print(f"  {tag:<22} 年化{ann*100:>6.1f}%  回撤{mdd*100:>5.1f}%  夏普{shp:>5.2f}  卡玛{car:>5.2f}  (成交{n})")
        return shp, port

    print("\n=== 基线(最优)===")
    base_shp, base_port = row("squeeze.25/hold15/30仓/MA60up/RSwin")
    print("\n=== squeeze 分位敏感 ===")
    for q in (0.15, 0.20, 0.25, 0.30, 0.35):
        row(f"squeeze={q}", squeeze=q)
    print("\n=== 持有期敏感 ===")
    for h in (8, 10, 12, 15, 18, 20):
        row(f"hold={h}", hold=h)
    print("\n=== 份数敏感 ===")
    for p in (15, 20, 25, 30, 40, 50):
        row(f"parts={p}", parts=p)
    print("\n=== 第二次窗口敏感 ===")
    for r in (0, 20, 30, 45, 60):
        row(f"repeat={r}", repeat=r)
    print("\n=== 关键过滤 开/关(消融)===")
    row("去掉MA60up", ma60="any")
    row("去掉RS", rs="any")
    row("去掉两者", ma60="any", rs="any")
    row("站上上轨", up="upper")

    print("\n=== 基线 分年 夏普/收益 ===")
    for y, r in base_port.groupby(base_port.index.year):
        eq = (1 + r).prod() - 1
        sh = r.mean() / r.std() * np.sqrt(252) if r.std() else float("nan")
        print(f"  {y}: 收益 {eq*100:+6.1f}%   夏普 {sh:+.2f}   ({len(r)}日)")


if __name__ == "__main__":
    main()
