"""
BOLL缩口扩张+MACD金叉 信号的真实净值回测(事件驱动、扣费)。

规则(诚实时序):信号当日(收盘可知大盘涨跌)→ T+1 收盘入场,持有 hold 个交易日后收盘出场;
过滤:仅大盘上涨日 + 量比>1(均为 PIT 无未来);资金 parts 等份,每信号占 1 份;
份额满则「满仓放弃」,竞争时优先低 ATR(更稳);进出各扣单边费。
产出:年化/年化波动/最大回撤/夏普/卡玛 + 单笔胜率/均值/中位 + 成交率。

用法:python boll_narrow_exit/backtest.py [--pool csi2000 --up mid --parts 10 --hold 10 --fee 0.0008]
"""
import os
_ROOT = os.path.dirname(os.path.abspath(__file__))
while _ROOT != "/" and not os.path.exists(os.path.join(_ROOT, "cache_tushare.py")):
    _ROOT = os.path.dirname(_ROOT)
import sys
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import argparse
import numpy as np
import pandas as pd
import boll_expand_macd as bm

TRADING_DAYS = 252


def _slots(trades, parts):
    """按入场日贪心占用 parts 个仓位(满则放弃),同日竞争按代码定序(中性;低ATR/高量比两种排序实测均更差)。"""
    trades = trades.sort_values(["entry_date", "ts_code"]).reset_index(drop=True)
    free = [pd.Timestamp.min] * parts
    taken = []
    for r in trades.itertuples():
        for i in range(parts):
            if free[i] <= r.entry_date:
                free[i] = r.exit_date
                taken.append(r.Index)
                break
    return trades.loc[taken].reset_index(drop=True)


def _daily_curve(taken, retmap, calendar, parts, fee):
    """按每日持仓累加组合日收益(每仓 1/parts,进出各扣单边费),返回日收益 Series。"""
    port = pd.Series(0.0, index=calendar)
    for r in taken.itertuples():
        s = retmap.get(r.ts_code)
        if s is None:
            continue
        win = s.loc[(s.index > r.entry_date) & (s.index <= r.exit_date)]
        port.loc[win.index] += win.values / parts
        if r.entry_date in port.index:
            port.loc[r.entry_date] -= fee / parts
        if r.exit_date in port.index:
            port.loc[r.exit_date] -= fee / parts
    return port


def _perf(port):
    """年化(几何)/年化波动/最大回撤/夏普/卡玛。"""
    eq = (1 + port).cumprod()
    n = len(port)
    ann = eq.iloc[-1] ** (TRADING_DAYS / n) - 1
    vol = port.std() * np.sqrt(TRADING_DAYS)
    mdd = (eq / eq.cummax() - 1).min()
    return ann, vol, abs(mdd), (ann / vol if vol else np.nan), (ann / abs(mdd) if mdd else np.nan), eq.iloc[-1] - 1


def main():
    """跑真实净值回测并打印绩效。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", choices=["csi1000", "csi2000", "ml"], default="csi2000")
    ap.add_argument("--up", choices=["mid", "upper"], default="mid")
    ap.add_argument("--parts", type=int, default=10, help="资金等份数(最多同时持仓数)")
    ap.add_argument("--hold", type=int, default=10, help="持有交易日")
    ap.add_argument("--fee", type=float, default=0.0008, help="单边费率(佣金+印花+滑点粗估)")
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--healthy-only", action="store_true", help="仅在大盘健康日入场(择时指数 MA30&MA60 未同时走坏)")
    ap.add_argument("--mkt", choices=["hs300", "csi2000"], default="hs300", help="择时用哪个指数(涨跌/健康)")
    ap.add_argument("--repeat-days", type=int, default=0, help=">0 则只买第二次信号(前N天内同股已出过信号,第一次视为洗盘)")
    ap.add_argument("--ma60", choices=["any", "up", "down"], default="any", help="按个股60日均线趋势过滤(up上行/down下行)")
    ap.add_argument("--macd0", choices=["any", "above", "below"], default="any", help="MACD金叉在0轴上方(above)/下方(below)过滤")
    ap.add_argument("--rs", choices=["any", "win", "lose"], default="any", help="相对强弱:win跑赢大盘/lose跑输(20日)")
    args = ap.parse_args()

    codes = {"ml": bm.members_ml, "csi2000": bm.members_2000, "csi1000": bm.members_1000}[args.pool]()
    df = bm.load(codes, args.start)
    df["ret"] = df.groupby("ts_code")["adjc"].pct_change(fill_method=None)
    sig = bm.build_signals(df, 0.25, 3, args.up, args.hold)
    mkt = bm.hs300_market(args.start, "932000.CSI" if args.mkt == "csi2000" else "000300.SH")
    sig = sig.merge(mkt, on="date", how="left")
    sig = sig[(sig["mkt_up"] == True) & (sig["vol_ratio"] > 1) & sig["entry_date"].notna() & sig["exit_date"].notna()]
    if args.healthy_only:
        sig = sig[sig["mkt_bad"] == False]
    if args.repeat_days > 0:
        sig = sig.sort_values(["ts_code", "date"])
        sig = sig[sig.groupby("ts_code")["date"].diff().dt.days <= args.repeat_days]
    if args.ma60 == "up":
        sig = sig[sig["ma60_up"] == True]
    elif args.ma60 == "down":
        sig = sig[sig["ma60_up"] == False]
    if args.macd0 == "above":
        sig = sig[sig["macd_above0"] == True]
    elif args.macd0 == "below":
        sig = sig[sig["macd_above0"] == False]
    if args.rs != "any":
        rs = sig["mom20"] - sig["hs300_mom20"]
        sig = sig[rs > 0] if args.rs == "win" else sig[rs <= 0]

    taken = _slots(sig, args.parts)
    df["td"] = pd.to_datetime(df["td"])
    retmap = {ts: g.set_index("td")["ret"] for ts, g in df[["ts_code", "td", "ret"]].groupby("ts_code")}
    cal = pd.DatetimeIndex(sorted(df["td"].unique()))
    cal = cal[cal >= taken["entry_date"].min()]
    port = _daily_curve(taken, retmap, cal, args.parts, args.fee)

    ann, vol, mdd, shp, cal_r, tot = _perf(port)
    net = taken["ret_gross"] - 2 * args.fee
    print(f"\n股池={args.pool} 站上{'上轨' if args.up=='upper' else '中轨'} 份数={args.parts} 持有={args.hold}日 单边费={args.fee*100:.3f}%")
    print(f"信号 {len(sig)} 条 → 实际成交 {len(taken)} 条(成交率 {len(taken)/max(1,len(sig))*100:.0f}%)")
    print(f"区间 {cal[0].date()} ~ {cal[-1].date()}  交易日 {len(port)}")
    print(f"\n=== 组合净值(扣费)===")
    print(f"  累计收益 {tot*100:+.1f}%   年化 {ann*100:+.1f}%   年化波动 {vol*100:.1f}%")
    print(f"  最大回撤 {mdd*100:.1f}%   夏普 {shp:.2f}   卡玛 {cal_r:.2f}")
    print(f"\n=== 单笔(扣双边费)===")
    print(f"  笔数 {len(net)}  胜率 {(net>0).mean()*100:.0f}%  均值 {net.mean()*100:+.2f}%  中位 {net.median()*100:+.2f}%")
    print(f"\n=== 分年收益(组合净值,扣费)===")
    for y, r in port.groupby(port.index.year):
        eqy = (1 + r).prod() - 1
        ddy = ((1 + r).cumprod() / (1 + r).cumprod().cummax() - 1).min()
        print(f"  {y}: 收益 {eqy*100:+6.1f}%   回撤 {ddy*100:5.1f}%   ({len(r)}日)")


if __name__ == "__main__":
    main()
