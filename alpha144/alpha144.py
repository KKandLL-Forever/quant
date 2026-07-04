"""alpha144/alpha144.py — 复现「@B:A 用人话讲因子·第七期」的 Alpha#144 流动性冲击择时策略。

因子(国泰君安191 Alpha144,Amihud 非流动性):
  alpha144 = 过去20个交易日里,只在下跌日(ret<0)累加 |ret|/amount。越大=流动性越差=越易超跌反弹。
策略卡片(视频):
  股票池=中证500成份;选股=alpha144 排名前 top_pct(每 refresh 天刷新);
  入场=收盘突破5日新高→次日开盘买入;持仓=最多 slots 只等权、持满 hold 天退出、不止损;
  风控=中证500 近20日跌幅 < mkt_thr(-3%)时空仓不新开。
产出:年化/最大回撤/夏普/逐年收益/与中证500相关性;--liq-floor 做剔除低成交额稳健性检验。

数据:本地 DuckDB —— daily(OHLC+amount+pct_chg)、csi500_members(月度成分快照)、index_daily(000905.SH)。
用法:python alpha144/alpha144.py [--top-pct 0.10] [--slots 5] [--hold 20] [--refresh 10] [--liq-floor 0]
口径:point-in-time 成分(月度快照前向填充);次日开盘成交、扣双边费;walk-forward 无未来函数。
"""
import argparse
import os
import sys

import duckdb
import numpy as np
import pandas as pd

_ROOT = os.path.dirname(os.path.abspath(__file__))
while _ROOT != "/" and not os.path.exists(os.path.join(_ROOT, "cache_tushare.py")):
    _ROOT = os.path.dirname(_ROOT)
sys.path.insert(0, _ROOT)
import cache_tushare as c

COST = 0.0006   # 双边费近似(单边),买卖各扣一次


_UNIV = {"csi500": ("csi500_members", "000905.SH"), "csi1000": ("csi1000_members", "000852.SH"),
         "csi2000": ("csi2000_members", "399852.SZ")}


def load(start="2020-06-01", universe="csi500"):
    """读指定指数成份股的日线 + 月度成分快照 + 该指数;返回 (px, memb, idx)。"""
    tbl, idxcode = _UNIV[universe]
    con = duckdb.connect(c.DUCKDB_PATH, read_only=True)
    memb = con.execute(f"SELECT con_code, trade_date FROM {tbl}").df()
    memb["trade_date"] = pd.to_datetime(memb["trade_date"])
    codes = sorted(memb["con_code"].unique())
    ph = ",".join(["?"] * len(codes))
    px = con.execute(
        f"""SELECT ts_code, trade_date, open, high, close, amount, pct_chg
            FROM daily WHERE ts_code IN ({ph}) AND trade_date >= ? ORDER BY ts_code, trade_date""",
        codes + [start]).df()
    idx = con.execute("SELECT trade_date, close FROM index_daily WHERE ts_code=? ORDER BY trade_date", [idxcode]).df()
    con.close()
    px["trade_date"] = pd.to_datetime(px["trade_date"])
    idx["trade_date"] = pd.to_datetime(idx["trade_date"])
    return px, memb, idx


def compute(px):
    """逐股算 alpha144(20日下跌日 |ret|/amount 累加)+ 5日新高突破标记。"""
    px = px.sort_values(["ts_code", "trade_date"]).copy()
    r = px["pct_chg"] / 100.0
    impact = (r.abs() / px["amount"]).where(r < 0, 0.0)   # 只在下跌日取 |ret|/amount
    px["alpha144"] = impact.groupby(px["ts_code"]).transform(lambda s: s.rolling(20, min_periods=15).sum())
    hi5 = px.groupby("ts_code")["close"].transform(lambda s: s.shift(1).rolling(5).max())
    px["brk5"] = px["close"] > hi5   # 收盘突破前5日最高收盘
    return px


def _membership(memb, dates, pit=True):
    """成分归属:pit=True 用月度快照前向填充(point-in-time,当时真实成分);
    pit=False 用【最新一期快照】铺满全历史(=用今天的名单看过去,幸存者偏差/未来数据)。"""
    snaps = sorted(memb["trade_date"].unique())
    if not pit:
        latest = set(memb[memb["trade_date"] == snaps[-1]]["con_code"])
        return {d: latest for d in dates}
    bycode = {d: set(memb[memb["trade_date"] == d]["con_code"]) for d in snaps}
    out, cur = {}, set()
    si = 0
    for d in dates:
        while si < len(snaps) and snaps[si] <= d:
            cur = bycode[snaps[si]]; si += 1
        out[d] = cur
    return out


def backtest(px, memb, idx, top_pct=0.10, slots=5, hold=20, refresh=10, mkt_thr=-0.03, liq_floor=0.0, pit=True):
    """事件驱动定槽:每 refresh 天选 alpha144 前 top_pct 候选;候选中当日突破5日新高→次日开盘买入;
    最多 slots 只等权、持满 hold 天开盘卖出、不止损;近20日跌幅<mkt_thr 时不新开。pit=False 用今天名单(未来数据)。"""
    dates = sorted(px["trade_date"].unique())
    di = {d: i for i, d in enumerate(dates)}
    inmemb = _membership(memb, dates, pit)
    idxc = idx.set_index("trade_date")["close"]
    idx_ret20 = (idxc / idxc.shift(20) - 1).to_dict()
    day_groups = {d: g.set_index("ts_code") for d, g in px.groupby("trade_date")}
    openm, closem = {}, {}
    for d, g in day_groups.items():
        i = di[d]
        openm[i] = g["open"].to_dict(); closem[i] = g["close"].to_dict()

    # 1) 事件循环只决定成交(entry_i 开盘买、exit_i=entry_i+hold 开盘卖),受 slots 约束
    open_pos = []   # {code, entry_i, exit_i, entry_px}
    trades = []
    cand = set()
    for i, d in enumerate(dates):
        g = day_groups.get(d)
        open_pos = [p for p in open_pos if p["exit_i"] > i]   # 到期的已在净值段处理,这里剔除
        if i % refresh == 0 and g is not None:
            pool = g[(g.index.isin(inmemb[d])) & g["alpha144"].notna()]
            if liq_floor > 0:
                pool = pool[pool["amount"] >= liq_floor]
            if len(pool):
                thr = pool["alpha144"].quantile(1 - top_pct)
                cand = set(pool.index[pool["alpha144"] >= thr])
        mkt_ok = idx_ret20.get(d, 0) >= mkt_thr
        if g is not None and mkt_ok and i + 1 < len(dates):
            held = {p["code"] for p in open_pos}
            trig = g[(g.index.isin(cand)) & g["brk5"] & (~g.index.isin(held))].sort_values("alpha144", ascending=False)
            for code in trig.index:
                if len(open_pos) >= slots:
                    break
                op = openm.get(i + 1, {}).get(code)
                if op is None or not (op == op) or op <= 0:
                    continue
                p = {"code": code, "entry_i": i + 1, "exit_i": i + 1 + hold, "entry_px": float(op)}
                open_pos.append(p); trades.append(p)

    # 2) 由成交生成每日组合收益:每笔占 1/slots,入场日 open→close、中间 close→close、出场日 prevclose→open,扣双边费
    port = np.zeros(len(dates))
    for p in trades:
        e, x, code, epx = p["entry_i"], p["exit_i"], p["code"], p["entry_px"]
        prev = epx
        for t in range(e, min(x, len(dates) - 1) + 1):
            if t == e:
                cl = closem.get(t, {}).get(code, epx); r = cl / epx - 1 - COST
            elif t == x:
                opx = openm.get(t, {}).get(code, prev); r = opx / prev - 1 - COST
            else:
                cl = closem.get(t, {}).get(code, prev); r = cl / prev - 1
            port[t] += r / slots
            prev = openm.get(t, {}).get(code, prev) if t == x else closem.get(t, {}).get(code, prev)
        p["ret"] = round((openm.get(x, {}).get(code, epx) / epx - 1) * 100 - 2 * COST * 100, 1)
        p["entry"] = str(dates[e].date()); p["exit"] = str(dates[min(x, len(dates) - 1)].date())
    nav = np.cumprod(1 + port)
    curve = pd.DataFrame({"date": [str(d.date()) for d in dates], "nav": nav})
    trades = pd.DataFrame([{k: p[k] for k in ("code", "entry", "exit", "ret")} for p in trades])
    return curve, trades


def metrics(curve, idx):
    """年化/最大回撤/夏普/逐年/与中证500相关性。"""
    nav = curve.set_index("date")["nav"]
    dt = pd.to_datetime(nav.index)
    ret = nav.pct_change().fillna(0)
    yrs = (dt[-1] - dt[0]).days / 365.25
    cagr = nav.iloc[-1] ** (1 / yrs) - 1
    mdd = (nav / nav.cummax() - 1).min()
    sharpe = ret.mean() / (ret.std() + 1e-9) * np.sqrt(252)
    byyear = nav.groupby(dt.year).apply(lambda s: s.iloc[-1] / s.iloc[0] - 1)
    ic = idx.set_index("trade_date")["close"].reindex(dt).pct_change().reset_index(drop=True)
    corr = pd.Series(ret.values).corr(ic)
    return cagr, mdd, sharpe, byyear, corr


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--top-pct", type=float, default=0.10)
    ap.add_argument("--slots", type=int, default=5)
    ap.add_argument("--hold", type=int, default=20)
    ap.add_argument("--refresh", type=int, default=10)
    ap.add_argument("--liq-floor", type=float, default=0.0, help="剔除当日成交额(千元)低于此值的票,做流动性稳健性检验")
    ap.add_argument("--start", default="2020-06-01")
    ap.add_argument("--universe", default="csi500", choices=["csi500", "csi1000", "csi2000"])
    ap.add_argument("--no-pit", action="store_true", help="用今天的成分名单铺满全历史(幸存者偏差/未来数据,演示坑用)")
    args = ap.parse_args()
    px, memb, idx = load(args.start, args.universe)
    px = compute(px)
    curve, trades = backtest(px, memb, idx, args.top_pct, args.slots, args.hold, args.refresh,
                             liq_floor=args.liq_floor, pit=not args.no_pit)
    cagr, mdd, sharpe, byyear, corr = metrics(curve, idx)
    print(f"\n=== Alpha#144 流动性冲击择时(中证500,top{args.top_pct:.0%},{args.slots}只,持{args.hold}日,刷新{args.refresh}日,"
          f"liq_floor={args.liq_floor:g})===")
    print(f"成交 {len(trades)} 笔  胜率 {(trades['ret'] > 0).mean()*100:.0f}%  单笔均值 {trades['ret'].mean():.1f}%")
    print(f"年化 {cagr*100:.1f}%  最大回撤 {mdd*100:.1f}%  夏普 {sharpe:.2f}  与中证500相关 {corr:.3f}")
    print("逐年:", {int(y): f"{v*100:+.0f}%" for y, v in byyear.items()})
