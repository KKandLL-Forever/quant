"""
regime.py — 大环境识别:把市场逐日分成 牛 / 熊 / 震荡 三态(先识别,后决定策略挂哪条)。

目的(用户口径):
  - 熊(急跌 or 阴跌):躲开,空仓也没关系;
  - 震荡:吃中间段,头尾去掉无所谓;
  - 牛(趋势):交给别的策略,不在这操心。

判据(全用滞后窗口,无未来函数),对每日:
  - rW = 近 W 日收益(P_t/P_{t-W}-1),定趋势方向与力度;
  - dd = P_t/近 W 日最高 -1(距窗口高点回撤),抓「急跌」比慢均线及时。
  规则(按优先级):
    dd ≤ -DD          → 熊(急跌/深回撤)
    rW ≥ RW           → 牛
    rW ≤ -RW          → 熊(阴跌趋势)
    否则              → 震荡(W日内没净走出方向)
  再按最短持续期 MIN_RUN 做去抖(短碎段并入相邻态),避免一天一变。
  注:rW 对比 W 日前,拐点处滞后——顶部回落初期可能仍记牛(区间涨幅会显负),属已知局限。

标的:默认 ML主升池等权指数(我们实际交易的环境)+ 中证800(大盘参照),两幅图对比。
产出:vp_value_area/regime_map.png(价格 + 三态底色) + 控制台各区间清单(起止/天数/区间涨幅)。
用法:python vp_value_area/regime.py [--start 20220101] [--w 60] [--rw 0.10] [--dd 0.10]
"""

import argparse
import os
import sys

import numpy as np
import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
import duckdb
from cache_tushare import DUCKDB_PATH
from va_breakout_study import load_bench

LABELS = {1: "牛", -1: "熊", 0: "震荡"}
COLORS = {1: "#1f8e5a", -1: "#c0392b", 0: "#b8b0a0"}


def ml_pool_ew_index(con, start):
    """ML主升池(最新日流通市值top800非北交所)等权指数:各股日收益横截面均值累乘,对齐交易日历。"""
    sel = con.execute("SELECT MAX(trade_date) FROM daily_basic").fetchone()[0]
    codes = [r[0] for r in con.execute(
        "SELECT ts_code FROM daily_basic WHERE trade_date=? AND ts_code NOT LIKE '%.BJ' ORDER BY circ_mv DESC LIMIT 800",
        [sel]).fetchall()]
    start_d = f"{start[:4]}-{start[4:6]}-{start[6:8]}"
    df = con.execute(
        "SELECT trade_date, ts_code, close FROM daily WHERE ts_code IN (SELECT UNNEST(?)) AND trade_date>=? ORDER BY trade_date",
        [codes, start_d]).df()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    wide = df.pivot_table(index="trade_date", columns="ts_code", values="close")
    ret = wide.pct_change()
    ew = ret.mean(axis=1)
    idx = (1 + ew.fillna(0)).cumprod()
    return idx


def classify(price, w, rw_thr, dd_bear, min_run):
    """价格序列 → 逐日三态(1牛/-1熊/0震荡):W日净收益定趋势,距W日高点回撤抓急跌;含最短持续期去抖。"""
    p = price.values
    n = len(p)
    lab = np.zeros(n, dtype=int)
    for t in range(w, n):
        rW = p[t] / p[t - w] - 1
        dd = p[t] / p[t - w:t + 1].max() - 1
        if dd <= -dd_bear:
            lab[t] = -1
        elif rW >= rw_thr:
            lab[t] = 1
        elif rW <= -rw_thr:
            lab[t] = -1
        else:
            lab[t] = 0
    return _despeckle(lab, min_run)


def _despeckle(lab, min_run):
    """把长度 < min_run 的碎段并入前一段,消抖。"""
    out = lab.copy()
    i = 0
    n = len(out)
    while i < n:
        j = i
        while j + 1 < n and out[j + 1] == out[i]:
            j += 1
        if j - i + 1 < min_run and i > 0:
            out[i:j + 1] = out[i - 1]
        i = j + 1
    return out


def to_weekly(price):
    """日线收盘 → 周线(周五)收盘。"""
    return price.resample("W-FRI").last().dropna()


def segments(dates, lab, price):
    """连续同态区间 → [(label, 起, 止, 天数, 区间涨幅%)]。"""
    segs = []
    i = 0
    n = len(lab)
    while i < n:
        j = i
        while j + 1 < n and lab[j + 1] == lab[i]:
            j += 1
        ret = (price.values[j] / price.values[i] - 1) * 100
        segs.append((lab[i], dates[i], dates[j], j - i + 1, ret))
        i = j + 1
    return segs


def plot_panel(ax, dates, price, lab, title):
    """在 ax 上画价格 + 三态底色。"""
    ax.plot(dates, price.values, color="#222", lw=1.3)
    n = len(lab)
    i = 0
    while i < n:
        j = i
        while j + 1 < n and lab[j + 1] == lab[i]:
            j += 1
        ax.axvspan(dates[max(i - 1, 0)], dates[j], color=COLORS[lab[i]], alpha=0.16, lw=0)
        i = j + 1
    ax.set_title(title, fontsize=12.5)
    ax.grid(alpha=0.2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20220101")
    ap.add_argument("--w", type=int, default=60)
    ap.add_argument("--rw", type=float, default=0.10)
    ap.add_argument("--dd", type=float, default=0.10)
    ap.add_argument("--min-run", type=int, default=5)
    args = ap.parse_args()

    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    pool = ml_pool_ew_index(con, args.start)
    bench = load_bench(con, "000906.SH", args.start)
    bench.index = pd.to_datetime(bench.index)
    bench = bench.reindex(pool.index).ffill()

    series = {"ML主升池等权指数": pool, "中证800": bench}
    fig = None
    for name, px in series.items():
        lab = classify(px, args.w, args.rw, args.dd, args.min_run)
        assert set(np.unique(lab)) <= {-1, 0, 1}
        segs = segments(list(px.index), lab, px)
        share = {k: (lab == v).mean() * 100 for k, v in [("牛", 1), ("熊", -1), ("震荡", 0)]}
        print(f"\n=== {name} · 区间三态识别(W{args.w} rW±{args.rw*100:.0f}% dd{args.dd*100:.0f}%)===")
        print(f"  天数占比: 牛 {share['牛']:.0f}%  熊 {share['熊']:.0f}%  震荡 {share['震荡']:.0f}%")
        print(f"  {'态':<4}{'起':<12}{'止':<12}{'天数':>5}{'区间涨幅%':>10}")
        for l, s, e, d, r in segs:
            if d < args.min_run and l == 0:
                continue
            print(f"  {LABELS[l]:<4}{str(s.date()):<12}{str(e.date()):<12}{d:>5}{r:>10.1f}")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
        from matplotlib.patches import Patch
        try:
            font_manager.fontManager.addfont("/System/Library/Fonts/PingFang.ttc")
        except Exception:
            pass
        plt.rcParams["font.sans-serif"] = ["PingFang SC", "Arial Unicode MS"]
        plt.rcParams["axes.unicode_minus"] = False
        fig, axes = plt.subplots(2, 1, figsize=(14, 9), sharex=True)
        for ax, (name, px) in zip(axes, series.items()):
            lab = classify(px, args.w, args.rw, args.dd, args.min_run)
            plot_panel(ax, list(px.index), px, lab, name)
        axes[0].legend(handles=[Patch(color=COLORS[k], alpha=0.35, label=LABELS[k]) for k in (1, -1, 0)],
                       loc="upper left", ncol=3, fontsize=10)
        fig.suptitle(f"大环境识别:牛(绿)/熊(红)/震荡(灰) — {args.w}日净收益 rW±{args.rw*100:.0f}% 定趋势 + 距高点回撤{args.dd*100:.0f}%抓急跌", fontsize=13.5, weight="bold")
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "regime_map.png")
        plt.savefig(out, dpi=130, bbox_inches="tight")
        print(f"\n区间图已存 {out}")

        wk = to_weekly(pool)
        lab_w = classify(wk, max(args.w // 5, 10), args.rw, args.dd, max(args.min_run // 5, 3))
        lab_w_d = pd.Series(lab_w, index=wk.index).reindex(pool.index, method="ffill").fillna(0).astype(int).values
        lab_d = classify(pool, args.w, args.rw, args.dd, args.min_run)
        flips_d = int((np.diff(lab_d) != 0).sum())
        flips_w = int((np.diff(lab_w_d) != 0).sum())
        fig2, ax2 = plt.subplots(2, 1, figsize=(14, 9), sharex=True)
        plot_panel(ax2[0], list(pool.index), pool, lab_d, f"日线识别(W{args.w}日) · 切换 {flips_d} 次")
        plot_panel(ax2[1], list(pool.index), pool, lab_w_d, f"周线识别(W{max(args.w//5,10)}周) · 切换 {flips_w} 次")
        ax2[0].legend(handles=[Patch(color=COLORS[k], alpha=0.35, label=LABELS[k]) for k in (1, -1, 0)],
                      loc="upper left", ncol=3, fontsize=10)
        fig2.suptitle("同一 ML主升池指数 · 日线 vs 周线识别:周线更平滑少切换,日线更跟手但碎", fontsize=13.5, weight="bold")
        out2 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "regime_freq.png")
        plt.savefig(out2, dpi=130, bbox_inches="tight")
        print(f"日线vs周线对比图已存 {out2}  (日线切换{flips_d}次 / 周线切换{flips_w}次)")
    except Exception as e:
        print(f"[warn] 画图失败 {e}")


if __name__ == "__main__":
    main()
