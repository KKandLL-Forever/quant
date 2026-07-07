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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "swing"))
import duckdb
from cache_tushare import DUCKDB_PATH
from va_breakout_study import load_bench, vah_series_fixed, vah_series_anchored, anchors
from model_robustness import pbo_cscv, deflated_sharpe

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


def add_to_contrib(contrib, tr, op, cl, gidx, hold, cost):
    """把一笔头牌交易的每个持仓日收益(入场日open→close,之后close→close;入场日扣往返费)按全局日填进 contrib。"""
    e = tr["entry_local"]
    for k in range(hold):
        loc = e + k
        r = (cl[loc] / op[loc] - 1) if k == 0 else (cl[loc] / cl[loc - 1] - 1)
        if k == 0:
            r -= cost
        contrib[gidx[loc]].append(r)


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
    head_contrib = [[] for _ in range(ndays)]
    head_contrib_gate = [[] for _ in range(ndays)]

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
        for name, (method, acc) in grid.items():
            tr = gen_trades(op, hi, lo, cl, vo, anch, gidx, method, acc, args.hold, args.cost, bench_g)
            for r in tr:
                r["month"] = str(cal[r["entry_g"]])[:7]
            trades[name].extend(tr)
            if name == "锚定VP+接受":
                for r in tr:
                    add_to_contrib(head_contrib, r, op, cl, gidx, args.hold, args.cost)
                    if not defensive_g[r["entry_g"]]:
                        add_to_contrib(head_contrib_gate, r, op, cl, gidx, args.hold, args.cost)
                    head_trades.append(r)
        if i % 100 == 0:
            print(f"  [{i}/{len(codes)}]", flush=True)

    # 头牌净值:裸 vs 牛熊门控
    port = curve_from_contrib(head_contrib, ndays)
    portg = curve_from_contrib(head_contrib_gate, ndays)
    bench_ret = np.zeros(ndays)
    bench_ret[1:] = bench_g[1:] / bench_g[:-1] - 1
    tot, ann, sr, mdd, eq = stats(port)
    gt, ga, gs, gm, geq = stats(portg)
    bt, ba, bs, bm, beq = stats(bench_ret)
    exday = portg - bench_ret
    et, ea, es, em, eeq = stats(exday)
    n_gated = sum(1 for t in head_trades if defensive_g[t["entry_g"]])
    pct_bear = defensive_g.mean() * 100

    print("\n=== 头牌:锚定VP + 接受确认 (t+3开盘进/持有%d日/扣%.2f%%) 等权日频组合 ===" % (args.hold, args.cost * 100))
    print(f"  笔数 {len(head_trades)} | 逐笔净超额均值 {np.mean([t['net_ex'] for t in head_trades])*100:.2f}% | "
          f"胜率 {np.mean([t['net_ex']>0 for t in head_trades])*100:.1f}%")
    print(f"  防御(空仓)天数占比 {pct_bear:.0f}% | 被门控挡掉的信号 {n_gated}/{len(head_trades)}")
    print(f"  裸VP:      累计 {tot*100:6.1f}%  年化 {ann*100:5.1f}%  夏普 {sr:4.2f}  回撤 {mdd*100:5.1f}%")
    print(f"  VP+牛熊门控:累计 {gt*100:6.1f}%  年化 {ga*100:5.1f}%  夏普 {gs:4.2f}  回撤 {gm*100:5.1f}%")
    print(f"  中证800:   累计 {bt*100:6.1f}%  年化 {ba*100:5.1f}%  夏普 {bs:4.2f}  回撤 {bm*100:5.1f}%")
    print(f"  门控后超额:累计 {et*100:6.1f}%  年化 {ea*100:5.1f}%  信息比 {es:4.2f}")

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
        ax.plot(cal, eq, color="#e0a458", lw=1.5, label=f"裸VP(年化{ann*100:.1f}% 夏普{sr:.2f} 回撤{mdd*100:.1f}%)")
        ax.plot(cal, geq, color="#c0392b", lw=1.9, label=f"VP+牛熊门控(年化{ga*100:.1f}% 夏普{gs:.2f} 回撤{gm*100:.1f}%)")
        ax.plot(cal, beq, color="#888", lw=1.4, label=f"中证800(年化{ba*100:.1f}%)")
        for i in range(1, ndays):
            if defensive_g[i]:
                ax.axvspan(cal[i - 1], cal[i], color="#3a7fb5", alpha=0.06, lw=0)
        ax.axhline(1, color="#ccc", lw=0.8)
        ax.legend(fontsize=10, loc="upper left")
        ax.set_title(f"锚定VP+接受确认 · ML主升池 · 蓝底=沪深300门控防御(空仓)期 · 持有{args.hold}日/扣{args.cost*100:.2f}%", fontsize=12.5, weight="bold")
        ax.grid(alpha=0.25)
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "va_backtest_equity.png")
        plt.savefig(out, dpi=130, bbox_inches="tight")
        print(f"\n净值图已存 {out}")
    except Exception as e:
        print(f"[warn] 画图失败 {e}")


if __name__ == "__main__":
    main()
