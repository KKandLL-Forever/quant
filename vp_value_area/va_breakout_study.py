"""
va_breakout_study.py — 成交量分布(VP)价值区突破 事件研究:验证「突破 VAH 是趋势方向」在 A 股是否成立,并对比窗口长短。

背景(讨论结论):VP 的 VAH/VAL 圈住 70% 成交(价值区=平衡区),跌出价值区 = 打破平衡、试探新价值 = 趋势方向。
但窗口不是参数是「你在玩的游戏」:120 日跨多段行情、分布双峰、内建均值回归,对炒预期的 A 股必然失效。
本脚本用数据判:哪种窗口的「突破 VAH → 后续超额」关系最强,以及「锚定 VP」是否比固定窗口更干净。

方法(无未来函数):
  - 对每只股票每日,用截至当日的窗口算 VAH/VAL(H-L 按量摊到价格档);
  - 信号 = 当日收盘首次上穿 VAH(昨日收盘 ≤ 昨日 VAH,今日收盘 > 今日 VAH);
  - 前瞻超额 = 个股 close[t+N]/close[t]-1 减 中证1000 同期涨幅(N=1/5/10),信号后次日入场口径;
  - 「接受 vs 拒绝」:突破后第2日收盘仍在 VAH 之上=接受(真突破),否则=拒绝(假突破)。

对比 5 种口径:固定 5/10/20/30 日 + 锚定 VP(锚=最近一次「放量突破前高日」,profile 从锚点到当日)。

用法:python vp_value_area/va_breakout_study.py [--n 股票数抽样] [--start 20240101]
产出:控制台汇总表 + vp_value_area/va_breakout_windows.png(各口径 +10日超额 与 真/假突破分离度)。
"""

import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import duckdb
from cache_tushare import DUCKDB_PATH

BENCH = {"csi1000": "000852.SH", "ml": "000906.SH"}
FWD = [1, 5, 10]
ACCEPT_LAG = 2


def load_bench(con, code, start):
    """基准指数收盘序列:先查本地 index_daily,没有则走 tushare index_daily 拉入内存(不写库)。"""
    df = con.execute("SELECT trade_date, close FROM index_daily WHERE ts_code=? AND trade_date>=? ORDER BY trade_date",
                     [code, start]).df()
    if len(df):
        return df.set_index("trade_date")["close"]
    import tushare as ts
    from db_loader import _ENV
    pro = ts.pro_api(_ENV["TUSHARE_TOKEN"])
    parts = []
    for y in range(int(start[:4]), 2027):
        d = pro.index_daily(ts_code=code, start_date=f"{y}0101", end_date=f"{y}1231", fields="trade_date,close")
        if d is not None and len(d):
            parts.append(d)
    alld = pd.concat(parts)
    alld["trade_date"] = pd.to_datetime(alld["trade_date"]).dt.date
    return alld.sort_values("trade_date").set_index("trade_date")["close"]


def value_area(hi, lo, vol, nb=40):
    """给定窗口内 high/low/vol,返回 (VAH, VAL, POC价);量按当日[low,high]均摊到价格档,取70%价值区。"""
    pmin, pmax = float(lo.min()), float(hi.max())
    if pmax <= pmin:
        return pmax, pmin, pmin
    edges = np.linspace(pmin, pmax, nb + 1)
    cen = (edges[:-1] + edges[1:]) / 2
    prof = np.zeros(nb)
    for h, l, v in zip(hi, lo, vol):
        a = min(max(int(np.searchsorted(edges, l)) - 1, 0), nb - 1)
        b = min(max(int(np.searchsorted(edges, h)) - 1, 0), nb - 1)
        prof[a:b + 1] += v / (b - a + 1)
    poc = int(prof.argmax())
    tgt = prof.sum() * 0.7
    lo_j = hi_j = poc
    acc = prof[poc]
    while acc < tgt:
        up = prof[hi_j + 1] if hi_j + 1 < nb else -1
        dn = prof[lo_j - 1] if lo_j - 1 >= 0 else -1
        if up >= dn and hi_j + 1 < nb:
            hi_j += 1
            acc += prof[hi_j]
        elif lo_j - 1 >= 0:
            lo_j -= 1
            acc += prof[lo_j]
        else:
            break
    return cen[hi_j], cen[lo_j], cen[poc]


def vah_series_fixed(hi, lo, vol, w):
    """固定窗口 w 的逐日 VAH 序列(前 w-1 日为 nan)。"""
    n = len(hi)
    out = np.full(n, np.nan)
    for t in range(w - 1, n):
        vah, _, _ = value_area(hi[t - w + 1:t + 1], lo[t - w + 1:t + 1], vol[t - w + 1:t + 1])
        out[t] = vah
    return out


def anchors(hi, lo, close, vol, lb=20, volx=1.5):
    """每日对应的锚点索引:最近一次「收盘破前 lb 日最高 且 放量>volx」的日;无锚为 -1。"""
    n = len(close)
    vma = pd.Series(vol).rolling(lb).mean().values
    hmax = pd.Series(hi).shift(1).rolling(lb).max().values
    is_anchor = (close > hmax) & (vol > volx * vma)
    out = np.full(n, -1, dtype=int)
    cur = -1
    for t in range(n):
        if is_anchor[t]:
            cur = t
        out[t] = cur
    return out


def vah_series_anchored(hi, lo, vol, anch, min_len=5):
    """锚定 VP 的逐日 VAH:profile 从当日锚点到当日;锚段不足 min_len 日为 nan。"""
    n = len(hi)
    out = np.full(n, np.nan)
    for t in range(n):
        a = anch[t]
        if a < 0 or t - a + 1 < min_len:
            continue
        vah, _, _ = value_area(hi[a:t + 1], lo[a:t + 1], vol[a:t + 1])
        out[t] = vah
    return out


def collect_events(close, vah, bench_close):
    """收盘首次上穿 VAH 的事件 → 返回 [(t, fwd_excess dict, accepted)]。"""
    n = len(close)
    ev = []
    for t in range(1, n):
        if np.isnan(vah[t]) or np.isnan(vah[t - 1]):
            continue
        if close[t] > vah[t] and close[t - 1] <= vah[t - 1]:
            rec = {}
            ok = True
            for N in FWD:
                if t + N >= n:
                    ok = False
                    break
                r = close[t + N] / close[t] - 1
                br = bench_close[t + N] / bench_close[t] - 1
                rec[N] = (r - br) * 100
            if not ok:
                continue
            acc = (t + ACCEPT_LAG < n) and (close[t + ACCEPT_LAG] > vah[t])
            ev.append((t, rec, acc))
    return ev


def summarize(name, events):
    """把某口径的事件汇总成一行统计。"""
    if not events:
        return {"口径": name, "信号数": 0}
    row = {"口径": name, "信号数": len(events)}
    for N in FWD:
        v = np.array([e[1][N] for e in events])
        row[f"+{N}日超额%"] = round(v.mean(), 2)
        row[f"+{N}胜率%"] = round((v > 0).mean() * 100, 1)
    acc = np.array([e[1][10] for e in events if e[2]])
    rej = np.array([e[1][10] for e in events if not e[2]])
    row["接受+10%"] = round(acc.mean(), 2) if len(acc) else None
    row["拒绝+10%"] = round(rej.mean(), 2) if len(rej) else None
    row["分离度"] = round(acc.mean() - rej.mean(), 2) if len(acc) and len(rej) else None
    row["接受占比%"] = round(len(acc) / len(events) * 100, 1)
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", choices=["csi1000", "ml"], default="csi1000",
                    help="股池:csi1000=中证1000成分;ml=ML主升池(最新日流通市值 top800 非北交所 + 前20热股)")
    ap.add_argument("--n", type=int, default=None, help="抽样股票数(截断股池)")
    ap.add_argument("--start", default="20240101")
    args = ap.parse_args()

    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    if args.pool == "ml":
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
    else:
        codes = [r[0] for r in con.execute(
            "SELECT DISTINCT con_code FROM csi1000_members WHERE trade_date=(SELECT MAX(trade_date) FROM csi1000_members) ORDER BY con_code"
        ).fetchall()]
    if args.n:
        codes = codes[:args.n]
    start = f"{args.start[:4]}-{args.start[4:6]}-{args.start[6:8]}"
    bench_code = BENCH[args.pool]
    bench = load_bench(con, bench_code, args.start)
    bench.index = pd.to_datetime(bench.index)

    methods = {"固定5日": ("fix", 5), "固定10日": ("fix", 10), "固定20日": ("fix", 20),
               "固定30日": ("fix", 30), "锚定VP": ("anch", None)}
    bag = {m: [] for m in methods}

    print(f"股池 {args.pool} · {len(codes)} 只 | 起 {start} | 基准 {bench_code}", flush=True)
    for i, code in enumerate(codes, 1):
        d = con.execute("SELECT trade_date, high, low, close, vol FROM daily WHERE ts_code=? AND trade_date>=? ORDER BY trade_date",
                        [code, start]).df()
        if len(d) < 40:
            continue
        d["trade_date"] = pd.to_datetime(d["trade_date"])
        d = d[d["trade_date"].isin(bench.index)]
        if len(d) < 40:
            continue
        bc = bench.reindex(d["trade_date"]).values
        hi, lo, cl, vo = d["high"].values, d["low"].values, d["close"].values, d["vol"].values
        anch = anchors(hi, lo, cl, vo)
        for name, (kind, w) in methods.items():
            vah = vah_series_fixed(hi, lo, vo, w) if kind == "fix" else vah_series_anchored(hi, lo, vo, anch)
            bag[name].extend(collect_events(cl, vah, bc))
        if i % 100 == 0:
            print(f"  [{i}/{len(codes)}]", flush=True)

    rows = [summarize(m, bag[m]) for m in methods]
    df = pd.DataFrame(rows)
    pd.set_option("display.unicode.east_asian_width", True)
    bn = {"000852.SH":"中证1000","000906.SH":"中证800"}.get(bench_code, bench_code)
    print(f"\n=== VP 价值区突破 事件研究(超额 vs {bn})===")
    print(df.to_string(index=False))

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
        for f in ["/System/Library/Fonts/PingFang.ttc"]:
            try:
                font_manager.fontManager.addfont(f)
            except Exception:
                pass
        plt.rcParams["font.sans-serif"] = ["PingFang SC", "Arial Unicode MS"]
        plt.rcParams["axes.unicode_minus"] = False
        names = [r["口径"] for r in rows if r["信号数"]]
        f10 = [r.get("+10日超额%", 0) for r in rows if r["信号数"]]
        sep = [r.get("分离度") or 0 for r in rows if r["信号数"]]
        fig, (a1, a2) = plt.subplots(1, 2, figsize=(13, 5))
        a1.bar(names, f10, color=["#c9c2b4", "#e0a458", "#e08a2f", "#b5701a", "#c0392b"][:len(names)])
        a1.axhline(0, color="#888", lw=0.8)
        a1.set_title(f"突破 VAH 后 +10日 平均超额%(vs {bn})")
        a1.grid(alpha=0.25, axis="y")
        a2.bar(names, sep, color="#2077b4")
        a2.axhline(0, color="#888", lw=0.8)
        a2.set_title("真突破(接受) - 假突破(拒绝) 的 +10日超额 分离度%")
        a2.grid(alpha=0.25, axis="y")
        fig.suptitle("VP 价值区突破:窗口越短越有效?锚定 VP 是否更干净?", fontsize=14, weight="bold")
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "va_breakout_windows.png")
        plt.savefig(out, dpi=130, bbox_inches="tight")
        print(f"\n图已存 {out}")
    except Exception as e:
        print(f"[warn] 画图失败 {e}")


if __name__ == "__main__":
    main()
