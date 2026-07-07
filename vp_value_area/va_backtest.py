"""
va_backtest.py — 锚定VP + 接受确认 的「真·可交易」回测(ML主升池,基准中证800),含扣费/延迟入场/净值/稳健性。

与 va_breakout_study.py(事件研究,均值)的区别——这里做成能实盘的口径,不再自欺:
  - 信号日 t 收盘首次上穿 VAH;
  - 接受确认:t+2 收盘仍在 VAH 之上(实盘 t+2 收盘才能确认,不含未来);
  - 入场:t+3 开盘(确认后次日开盘,彻底避开 t→t+2 那段吃不到的涨幅);
  - 出场:入场后持有 HOLD 个交易日,收盘卖出;
  - 扣费:买卖单边合计 --cost(默认 0.15% 往返:佣金+印花+滑点);
  - 超额 = 个股净收益 − 中证800 同期涨幅。

产出:
  1) 头牌配置(锚定VP+接受,HOLD=10)的等权日频组合净值曲线 vs 中证800 + 年化/夏普/回撤;
  2) 配置网格(方法×是否接受确认)的逐笔月度收益矩阵 → 复用 swing/model_robustness 的 PBO(CSCV)+ Deflated Sharpe,
     判「在这些配置里挑出最优」是否过拟合。

用法:python vp_value_area/va_backtest.py [--start 20240101] [--hold 10] [--cost 0.0015]
"""

import argparse
import os
import sys

import numpy as np
import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "swing"))
sys.path.insert(0, os.path.join(_ROOT, "xiaoxifu"))
import duckdb
from cache_tushare import DUCKDB_PATH
from va_breakout_study import load_bench, vah_series_fixed, vah_series_anchored, anchors
from model_robustness import pbo_cscv, deflated_sharpe
from rsrs import load_index, rsrs_signal

ACCEPT_LAG = 2


def hs300_defensive(start):
    """沪深300「MA30 且 MA60 同时走坏」→ 防御(空仓)布尔序列(复用 xiaoxifu/regime_combo 口径,走 tushare)。"""
    import tushare as ts
    from db_loader import _ENV
    pro = ts.pro_api(_ENV["TUSHARE_TOKEN"])
    parts = []
    for y in range(int(start[:4]) - 1, 2027):
        d = pro.index_daily(ts_code="000300.SH", start_date=f"{y}0101", end_date=f"{y}1231", fields="trade_date,close")
        if d is not None and len(d):
            parts.append(d)
    ix = pd.concat(parts)
    ix = ix.set_index(pd.to_datetime(ix["trade_date"]))["close"].sort_index()
    ma30, ma60 = ix.rolling(30).mean(), ix.rolling(60).mean()
    healthy30 = (ix > ma30) & (ma30 > ma30.shift(5))
    healthy60 = (ix > ma60) & (ma60 > ma60.shift(5))
    return ((~healthy30) & (~healthy60)).fillna(False)


def ml_pool(con):
    """ML主升池:最新交易日流通市值 top800 非北交所 + 前20热股(circ_mv≥200亿)。"""
    sel = con.execute("SELECT MAX(trade_date) FROM daily_basic").fetchone()[0]
    codes = [r[0] for r in con.execute(
        "SELECT ts_code FROM daily_basic WHERE trade_date=? AND ts_code NOT LIKE '%.BJ' ORDER BY circ_mv DESC LIMIT 800",
        [sel]).fetchall()]
    try:
        hot = [r[0] for r in con.execute(
            "SELECT ts_code FROM ths_hot WHERE data_type='热股' AND trade_date=? ORDER BY rank LIMIT 20",
            [sel.strftime("%Y%m%d")]).fetchall()]
        mv = dict(con.execute("SELECT ts_code,circ_mv FROM daily_basic WHERE trade_date=?", [sel]).fetchall())
        codes = list(dict.fromkeys(codes + [c for c in hot if not c.endswith(".BJ") and (mv.get(c) or 0) >= 2_000_000]))
    except Exception:
        pass
    return codes


def gen_trades(op, hi, lo, cl, vo, anch, gidx, method, need_accept, hold, cost, bench_g):
    """按某方法生成一只股票的可交易记录:t+3开盘进、持有hold收盘出、扣费、减基准同期。"""
    n = len(cl)
    vah = vah_series_fixed(hi, lo, vo, method) if isinstance(method, int) else vah_series_anchored(hi, lo, vo, anch)
    out = []
    for t in range(1, n):
        if np.isnan(vah[t]) or np.isnan(vah[t - 1]):
            continue
        if not (cl[t] > vah[t] and cl[t - 1] <= vah[t - 1]):
            continue
        if need_accept and not (t + ACCEPT_LAG < n and cl[t + ACCEPT_LAG] > vah[t]):
            continue
        e = t + 3
        x = e + hold
        if x >= n or op[e] <= 0:
            continue
        gross = cl[x] / op[e] - 1
        bret = bench_g[gidx[x]] / bench_g[gidx[e]] - 1
        net_ex = gross - cost - bret
        out.append({"entry_local": e, "exit_local": x, "entry_g": gidx[e], "month": None,
                    "gross": gross - cost, "net_ex": net_ex})
    return out


def trade_rets(op, cl, gidx, e, hold, cost):
    """一笔交易的逐持仓日(全局日, 日收益):入场日 open→close 扣往返费,之后 close→close。"""
    days_g, rets = [], []
    for k in range(hold):
        loc = e + k
        r = (cl[loc] / op[loc] - 1) if k == 0 else (cl[loc] / cl[loc - 1] - 1)
        if k == 0:
            r -= cost
        days_g.append(int(gidx[loc]))
        rets.append(float(r))
    return days_g, rets


def curve_gated(head_trades, ndays, defensive):
    """按某牛熊门(defensive 布尔数组,None=不门控)构等权日频净值:入场日在防御期的交易整笔剔除。"""
    contrib = [[] for _ in range(ndays)]
    for tr in head_trades:
        if defensive is not None and defensive[tr["entry_g"]]:
            continue
        for g, r in zip(tr["days_g"], tr["rets"]):
            contrib[g].append(r)
    return curve_from_contrib(contrib, ndays)


def curve_from_contrib(contrib, ndays):
    """每日等权=当日所有在持仓票的均值收益,无持仓记0。"""
    port = np.zeros(ndays)
    for g in range(ndays):
        if contrib[g]:
            port[g] = float(np.mean(contrib[g]))
    return port


def stats(daily):
    """日收益序列 → 年化/夏普/最大回撤/累计。"""
    eq = np.cumprod(1 + daily)
    tot = eq[-1] - 1
    ann = (1 + tot) ** (252 / len(daily)) - 1 if len(daily) else 0
    sr = daily.mean() / (daily.std() + 1e-12) * np.sqrt(252)
    peak = np.maximum.accumulate(eq)
    mdd = ((eq - peak) / peak).min()
    return tot, ann, sr, mdd, eq


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20240101")
    ap.add_argument("--hold", type=int, default=10)
    ap.add_argument("--cost", type=float, default=0.0015)
    ap.add_argument("--breadth-thr", type=float, default=0.5, help="ML池宽度门控阈值:平滑后站上MA20占比 < 此值→防御空仓")
    args = ap.parse_args()

    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    codes = ml_pool(con)
    start = f"{args.start[:4]}-{args.start[4:6]}-{args.start[6:8]}"
    bench = load_bench(con, "000906.SH", args.start)
    bench.index = pd.to_datetime(bench.index)
    cal = list(bench.index)
    gpos = {d: i for i, d in enumerate(cal)}
    bench_g = bench.values
    ndays = len(cal)

    grid = {"锚定VP+接受": ("anch", True), "锚定VP裸": ("anch", False),
            "固定30+接受": (30, True), "固定20+接受": (20, True), "固定30裸": (30, False)}
    defser = hs300_defensive(args.start).reindex(cal, method="ffill").fillna(False)
    defensive_g = defser.values.astype(bool)

    trades = {k: [] for k in grid}
    head_trades = []
    br_above = np.zeros(ndays)
    br_tot = np.zeros(ndays)

    print(f"ML池 {len(codes)} 只 | 起 {start} | 持有 {args.hold}日 | 费 {args.cost*100:.2f}% | 基准 中证800", flush=True)
    for i, code in enumerate(codes, 1):
        d = con.execute("SELECT trade_date,open,high,low,close,vol FROM daily WHERE ts_code=? AND trade_date>=? ORDER BY trade_date",
                        [code, start]).df()
        if len(d) < 40:
            continue
        d["trade_date"] = pd.to_datetime(d["trade_date"])
        d = d[d["trade_date"].isin(gpos)]
        if len(d) < 40:
            continue
        gidx = np.array([gpos[t] for t in d["trade_date"]])
        op, hi, lo, cl, vo = (d["open"].values, d["high"].values, d["low"].values, d["close"].values, d["vol"].values)
        anch = anchors(hi, lo, cl, vo)
        ma20 = pd.Series(cl).rolling(20).mean().values
        for loc in range(len(cl)):
            if not np.isnan(ma20[loc]):
                g = gidx[loc]
                br_tot[g] += 1
                if cl[loc] > ma20[loc]:
                    br_above[g] += 1
        for name, (method, acc) in grid.items():
            tr = gen_trades(op, hi, lo, cl, vo, anch, gidx, method, acc, args.hold, args.cost, bench_g)
            for r in tr:
                r["month"] = str(cal[r["entry_g"]])[:7]
            trades[name].extend(tr)
            if name == "锚定VP+接受":
                for r in tr:
                    r["days_g"], r["rets"] = trade_rets(op, cl, gidx, r["entry_local"], args.hold, args.cost)
                    head_trades.append(r)
        if i % 100 == 0:
            print(f"  [{i}/{len(codes)}]", flush=True)

    # 三个牛熊门:MA30&MA60(沪深300)/ RSRS(中证800)/ ML池宽度(站上MA20占比)
    breadth = np.where(br_tot > 0, br_above / np.maximum(br_tot, 1), np.nan)
    breadth_s = pd.Series(breadth).ffill().rolling(3, min_periods=1).mean().values
    def_breadth = breadth_s < args.breadth_thr
    ix800 = load_index("000906.SH", str(int(args.start[:4]) - 3) + "0101", str(cal[-1].date()).replace("-", ""))
    rsrs_hold = rsrs_signal(ix800).reindex(cal, method="ffill").fillna(0).values
    def_rsrs = rsrs_hold < 0.5
    gates = {"裸VP(不门控)": None, "MA30&MA60门控": defensive_g, "RSRS门控": def_rsrs, "ML池宽度门控": def_breadth}

    bench_ret = np.zeros(ndays)
    bench_ret[1:] = bench_g[1:] / bench_g[:-1] - 1
    bt, ba, bs, bm, beq = stats(bench_ret)

    curves, S = {}, {}
    for gname, dfarr in gates.items():
        c = curve_gated(head_trades, ndays, dfarr)
        curves[gname] = c
        S[gname] = stats(c)

    print("\n=== 头牌 锚定VP+接受 · 三种牛熊门 PK (t+3开盘进/持有%d日/扣%.2f%%) ===" % (args.hold, args.cost * 100))
    print(f"  笔数 {len(head_trades)} | 逐笔净超额均值 {np.mean([t['net_ex'] for t in head_trades])*100:.2f}% | 胜率 {np.mean([t['net_ex']>0 for t in head_trades])*100:.1f}%")
    print(f"  防御天数占比: MA {defensive_g.mean()*100:.0f}%  RSRS {def_rsrs.mean()*100:.0f}%  宽度 {np.nanmean(def_breadth)*100:.0f}%")
    print(f"  {'口径':<14}{'累计%':>8}{'年化%':>8}{'夏普':>7}{'回撤%':>8}{'信息比':>8}")
    for gname in gates:
        tot, ann, sr, mdd, _ = S[gname]
        ir = (curves[gname] - bench_ret).mean() / ((curves[gname] - bench_ret).std() + 1e-12) * np.sqrt(252)
        print(f"  {gname:<14}{tot*100:8.1f}{ann*100:8.1f}{sr:7.2f}{mdd*100:8.1f}{ir:8.2f}")
    print(f"  {'中证800':<14}{bt*100:8.1f}{ba*100:8.1f}{bs:7.2f}{bm*100:8.1f}")

    # 配置网格逐笔 → 月度矩阵 → PBO/DSR
    months = sorted({r["month"] for k in grid for r in trades[k]})
    rows, names = [], []
    for name in grid:
        df = pd.DataFrame(trades[name])
        series = df.groupby("month")["net_ex"].mean().reindex(months) if len(df) else pd.Series(index=months, dtype=float)
        names.append(name)
        rows.append(series.values)
    M = pd.DataFrame(rows, index=names, columns=months).astype(float).fillna(0.0)
    print("\n=== 配置网格 各月逐笔净超额均值(用于 PBO/DSR)===")
    print((M.mean(axis=1) * 100).round(2).to_string())
    pbo, _ = pbo_cscv(M, S=10)
    dsr = deflated_sharpe(M)
    print(f"\nPBO(选优过拟合概率) = {pbo}   ", "→ >0.5 说明挑最优本质是过拟合" if pbo and pbo > 0.5 else "→ ≤0.5,选优不像纯运气")
    print(f"Deflated Sharpe(扣多重检验后最优是否显著) = {dsr}")

    # 画净值
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
        try:
            font_manager.fontManager.addfont("/System/Library/Fonts/PingFang.ttc")
        except Exception:
            pass
        plt.rcParams["font.sans-serif"] = ["PingFang SC", "Arial Unicode MS"]
        plt.rcParams["axes.unicode_minus"] = False
        fig, ax = plt.subplots(figsize=(13, 6))
        colmap = {"裸VP(不门控)": "#e0a458", "MA30&MA60门控": "#7a6f5d", "RSRS门控": "#2077b4", "ML池宽度门控": "#c0392b"}
        for gname in gates:
            tot, ann, sr, mdd, eqc = S[gname]
            ax.plot(cal, eqc, color=colmap[gname], lw=1.9 if "宽度" in gname else 1.4,
                    label=f"{gname}(年化{ann*100:.1f}% 夏普{sr:.2f} 回撤{mdd*100:.1f}%)")
        ax.plot(cal, beq, color="#aaa", lw=1.2, ls="--", label=f"中证800(年化{ba*100:.1f}%)")
        ax.axhline(1, color="#ccc", lw=0.8)
        ax.legend(fontsize=9.5, loc="upper left")
        ax.set_title(f"VP策略 · 三种牛熊门 PK · ML主升池 · 持有{args.hold}日/扣{args.cost*100:.2f}%", fontsize=13, weight="bold")
        ax.grid(alpha=0.25)
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "va_backtest_equity.png")
        plt.savefig(out, dpi=130, bbox_inches="tight")
        print(f"\n净值图已存 {out}")
    except Exception as e:
        print(f"[warn] 画图失败 {e}")


if __name__ == "__main__":
    main()
