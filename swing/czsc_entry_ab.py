"""czsc_entry_ab.py — 入场 A/B:在 ML(long)选出的票上,比"我们W/N入场" vs "czsc买点入场",出场都用 czsc route1。

池子 = run_ml_signals_2026 --mode long 选出的股票(及我们的W/N入场日)。
对每只票:A=按W/N信号日入场;B=按czsc买点(一买/二买/三买)入场。两者出场口径相同:
缠论卖点(一卖/MACD顶背驰)或 跌破MA60 或 跌破入场价85%(route1)。
均为持仓中不重复入场的串行交易,全程后复权,扣双边费;纯本地、无 LLM。

环境：.venv312。用法:
  python swing/run_ml_signals_2026.py --mode long --tier 5 --start 20240101 --json /tmp/long_ab.json
  python swing/czsc_entry_ab.py /tmp/long_ab.json
依赖：czsc, DuckDB, run_ml_signals_2026(COST)。
"""
import json
import os
_ROOT = os.path.dirname(os.path.abspath(__file__))
while _ROOT != "/" and not os.path.exists(os.path.join(_ROOT, "cache_tushare.py")):
    _ROOT = os.path.dirname(_ROOT)
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, _ROOT)

import duckdb
import numpy as np
import pandas as pd
import czsc.signals as CS
from czsc import CZSC, RawBar, Freq
from cache_tushare import DUCKDB_PATH
from run_ml_signals_2026 import COST

START = "2024-01-01"


def _sell(c):
    """缠论卖点(一卖/MACD顶背驰)。"""
    for fn in ("cxt_first_sell_V221126", "tas_macd_bc_V221201"):
        f = getattr(CS, fn, None)
        if not f:
            continue
        try:
            out = f(c, di=1)
        except Exception:
            try:
                out = f(c)
            except Exception:
                continue
        for v in out.values():
            tk = str(v).split("_")
            if fn == "cxt_first_sell_V221126" and tk[0] != "其他":
                return True
            if fn == "tas_macd_bc_V221201" and tk[0] == "背驰" and "红" in str(v):
                return True
    return False


def _buy(c):
    """缠论买点(一买/二买/三买):任一成立即买。"""
    for fn in ("cxt_first_buy_V221126", "cxt_second_bs_V230320", "cxt_third_buy_V230228"):
        f = getattr(CS, fn, None)
        if not f:
            continue
        try:
            out = f(c, di=1)
        except Exception:
            try:
                out = f(c)
            except Exception:
                continue
        for v in out.values():
            s = str(v); tk = s.split("_")
            if fn == "cxt_first_buy_V221126" and tk[0] != "其他":
                return True
            if fn == "cxt_third_buy_V230228" and tk[0] != "其他":
                return True
            if fn == "cxt_second_bs_V230320" and "买" in s:
                return True
    return False


def _buy1(c):
    """只看缠论一买。"""
    f = getattr(CS, "cxt_first_buy_V221126", None)
    if not f:
        return False
    try:
        out = f(c, di=1)
    except Exception:
        try:
            out = f(c)
        except Exception:
            return False
    return any(str(v).split("_")[0] != "其他" for v in out.values())


def _signals(bars):
    """单遍增量更新,返回(买点idx集合, 一买idx集合, 卖点idx集合)。"""
    buy, buy1, sell = set(), set(), set()
    c = CZSC(bars[:1])
    if _buy(c):
        buy.add(0)
    if _buy1(c):
        buy1.add(0)
    if _sell(c):
        sell.add(0)
    for i in range(1, len(bars)):
        c.update(bars[i])
        if _buy(c):
            buy.add(i)
        if _buy1(c):
            buy1.add(i)
        if _sell(c):
            sell.add(i)
    return buy, buy1, sell


def _weekly_up(bars):
    """周线增量更新,返回 [(周线trade_date(Timestamp), 是否最后一笔向上)],因果(每周收盘后判定)。"""
    out = []
    c = CZSC(bars[:1])
    for i in range(len(bars)):
        if i:
            c.update(bars[i])
        up = bool(c.bi_list) and "向上" in str(c.bi_list[-1].direction)
        out.append((bars[i].dt, up))
    return out


def _is_up_asof(weekly_states, d):
    """日线日期 d 处,取最近一个 <=d 的已收盘周线状态判断周线是否向上。"""
    up = False
    for wdt, u in weekly_states:
        if wdt <= d:
            up = u
        else:
            break
    return up


def _trades(entries, sellset, cc, ma60):
    """串行回测:持仓中不重复入场;出场=缠论卖点 或 跌破MA60 或 跌破入场价85%。返回[(ret, hold)]。"""
    out, held = [], -1
    for bo in sorted(entries):
        if bo <= held or bo >= len(cc) - 1:
            continue
        ex = next((t for t in range(bo + 1, len(cc))
                   if t in sellset or (ma60[t] == ma60[t] and cc[t] < ma60[t]) or cc[t] <= cc[bo] * 0.85), None)
        if ex is None:
            out.append((cc[-1] / cc[bo] - 1 - 2 * COST, len(cc) - 1 - bo)); held = len(cc) - 1
        else:
            out.append((cc[ex] / cc[bo] - 1 - 2 * COST, ex - bo)); held = ex
    return out


def _ride_hold(bo, sellset, cc, ma60):
    """M2:忽略缠论卖点,只在跌破MA60或跌破入场价85%离场。返回(ret, hold, legs=1)。"""
    for t in range(bo + 1, len(cc)):
        if (ma60[t] == ma60[t] and cc[t] < ma60[t]) or cc[t] <= cc[bo] * 0.85:
            return cc[t] / cc[bo] - 1 - 2 * COST, t - bo, 1
    return cc[-1] / cc[bo] - 1 - 2 * COST, len(cc) - 1 - bo, 1


def _ride_roundtrip(bo, buyset, sellset, cc, ma60):
    """M3:缠论卖点止盈离场→回调出现缠论买点再买回→跌破MA60/止损终止,复利。返回(ret, hold, legs)。"""
    mult = 1.0; en = bo; legs = 1; t = bo + 1; first = bo
    while t < len(cc):
        broke = (ma60[t] == ma60[t] and cc[t] < ma60[t]) or cc[t] <= cc[en] * 0.85
        if broke:
            mult *= (cc[t] / cc[en]) * (1 - 2 * COST)
            return mult - 1, t - first, legs
        if t in sellset:
            mult *= (cc[t] / cc[en]) * (1 - 2 * COST)
            reentry = None
            for t2 in range(t + 1, len(cc)):
                if ma60[t2] == ma60[t2] and cc[t2] < ma60[t2]:
                    break
                if t2 in buyset:
                    reentry = t2; break
            if reentry is None:
                return mult - 1, t - first, legs
            en = reentry; legs += 1; t = reentry + 1; continue
        t += 1
    mult *= (cc[-1] / cc[en]) * (1 - 2 * COST)
    return mult - 1, len(cc) - 1 - first, legs


def main():
    jf = sys.argv[1] if len(sys.argv) > 1 else "/tmp/long_ab.json"
    sig = json.load(open(jf))["signals"]
    pool = {}
    for s in sig:
        pool.setdefault(s["ts"], []).append(s["date"])

    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    M1, M2, M3 = [], [], []
    for ts, dates in pool.items():
        g = con.execute("""SELECT d.trade_date td, d.open*a.adj_factor o, d.high*a.adj_factor h,
            d.low*a.adj_factor l, d.close*a.adj_factor c, d.vol v
            FROM daily d JOIN adj_factor a ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date
            WHERE d.ts_code=? AND d.trade_date>='2023-01-01' ORDER BY d.trade_date""", [ts]).fetch_df()
        if len(g) < 120:
            continue
        gw = con.execute("""SELECT w.trade_date td, w.open*a.adj_factor o, w.high*a.adj_factor h,
            w.low*a.adj_factor l, w.close*a.adj_factor c, w.vol v
            FROM weekly w JOIN adj_factor a ON a.ts_code=w.ts_code AND a.trade_date=w.trade_date
            WHERE w.ts_code=? AND w.trade_date>='2022-06-01' ORDER BY w.trade_date""", [ts]).fetch_df()
        g["td"] = pd.to_datetime(g["td"])
        cc = g["c"].to_numpy()
        ma60 = pd.Series(cc).rolling(60).mean().to_numpy()
        bars = [RawBar(symbol=ts, id=i, dt=r.td, freq=Freq.D, open=r.o, close=r.c,
                       high=r.h, low=r.l, vol=r.v, amount=0.0) for i, r in g.iterrows()]
        try:
            buyset, buy1set, sellset = _signals(bars)
            wstates = []
            if len(gw) >= 20:
                gw["td"] = pd.to_datetime(gw["td"])
                wbars = [RawBar(symbol=ts, id=i, dt=r.td, freq=Freq.W, open=r.o, close=r.c,
                                high=r.h, low=r.l, vol=r.v, amount=0.0) for i, r in gw.iterrows()]
                wstates = _weekly_up(wbars)
        except Exception:
            continue
        idx = {pd.Timestamp(d): i for i, d in enumerate(g["td"])}
        our = {idx[pd.Timestamp(d)] for d in dates if pd.Timestamp(d) in idx}
        for bo in sorted(our):
            if bo >= len(cc) - 1:
                continue
            ex = next((t for t in range(bo + 1, len(cc))
                       if t in sellset or (ma60[t] == ma60[t] and cc[t] < ma60[t]) or cc[t] <= cc[bo] * 0.85), None)
            if ex is None:
                M1.append((cc[-1] / cc[bo] - 1 - 2 * COST, len(cc) - 1 - bo, 1))
            else:
                M1.append((cc[ex] / cc[bo] - 1 - 2 * COST, ex - bo, 1))
            M2.append(_ride_hold(bo, sellset, cc, ma60))
            M3.append(_ride_roundtrip(bo, buyset, sellset, cc, ma60))
    con.close()

    def rep(name, t):
        if not t:
            print(f"  {name:20} 无交易"); return
        r = np.array([x[0] for x in t]); h = np.array([x[1] for x in t]); lg = np.array([x[2] for x in t])
        print(f"  {name:20} 笔数{len(t):4d}  平均{r.mean()*100:+6.1f}%  中位{np.median(r)*100:+6.1f}%  "
              f"胜率{(r>0).mean()*100:3.0f}%  平均持仓{h.mean():4.0f}天  最差{r.min()*100:+5.0f}%  平均腿数{lg.mean():.1f}")

    print(f"\n池子={len(pool)}只(ML long top5%),全用我们的 W/N 入场,只比持仓管理方式")
    rep("M1 route1(现状,缠卖即走)", M1)
    rep("M2 让利润奔跑(忽略缠卖)", M2)
    rep("M3 波段滚动(卖了回调再买回)", M3)


if __name__ == "__main__":
    main()
