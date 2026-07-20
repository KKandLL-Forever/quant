"""etf_flow_episodes.py — 把宽基ETF「净申购」当区间过程,用(持续时长 × 强度)两维和历史对比来分型。

不看峰值,看整段:识别连续净申购区间(20日滚动净申赎 r20>0 的连续段),每段算
  · 时长 dur(交易日数)
  · 强度 peak_r20(段内最强的20日累计净申赎%AUM,即"数量级")
  · 累计 cum(段内每日净申赎%AUM之和)
再取各自的历史分位,按用户三类分型:
  A 长时长 + 强度非高位  → 长期下降趋势(慢磨托底)
  B 短时长 + 强度历史高位 → 急跌后,反弹预期
  C 短时长 + 强度也不大   → 小幅波动
  (D 长时长 + 强度高位   → 强力持续托底)
并回测每段结束后 上证 60/120 日收益,看分型是否真有预测差异。

数据:主库 etf_share + fund_daily + index_daily 上证。用法:python research/etf_flow_episodes.py。纯本地、只打印。
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
import numpy as np
import pandas as pd
import duckdb
from cache_tushare import DUCKDB_PATH, ETF_SHARE_LIST

MIN_DUR = 10          # 过滤:至少 10 个交易日才算一段(去噪)


def _flow():
    """返回日频 DataFrame:flow_pct(当日净申赎%AUM)/r20/上证收盘。"""
    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    ph = ",".join(["?"] * len(ETF_SHARE_LIST))
    sh = con.execute(f"SELECT ts_code,trade_date,total_share FROM etf_share WHERE ts_code IN ({ph})", ETF_SHARE_LIST).df()
    px = con.execute(f"SELECT ts_code,trade_date,close FROM fund_daily WHERE ts_code IN ({ph})", ETF_SHARE_LIST).df()
    idx = con.execute("SELECT trade_date,close FROM index_daily WHERE ts_code='000001.SH' ORDER BY trade_date").df()
    con.close()
    for d in (sh, px, idx):
        d["trade_date"] = pd.to_datetime(d["trade_date"])
    m = sh.merge(px, on=["ts_code", "trade_date"], how="inner").sort_values(["ts_code", "trade_date"])
    m["aum"] = m["total_share"] * m["close"]
    m["flow"] = m.groupby("ts_code")["total_share"].diff() * m["close"]
    g = m.groupby("trade_date").agg(flow=("flow", "sum"), aum=("aum", "sum")).dropna()
    g["flow_pct"] = g["flow"] / g["aum"] * 100
    g["r20"] = g["flow_pct"].rolling(20).sum()
    g = g.join(idx.set_index("trade_date")["close"].rename("sh")).dropna(subset=["sh"])
    g["fwd60"] = g["sh"].shift(-60) / g["sh"] - 1
    g["fwd120"] = g["sh"].shift(-120) / g["sh"] - 1
    return g


def _episodes(g: pd.DataFrame):
    """按 r20>0 的连续段切出净申购区间,返回每段特征 DataFrame。"""
    creating = (g["r20"] > 0).values
    eps = []
    i = 0
    n = len(g)
    while i < n:
        if not creating[i]:
            i += 1; continue
        j = i
        while j < n and creating[j]:
            j += 1
        seg = g.iloc[i:j]
        if len(seg) >= MIN_DUR:
            eps.append({"start": seg.index[0], "end": seg.index[-1], "dur": len(seg),
                        "peak_r20": float(seg["r20"].max()), "cum": float(seg["flow_pct"].sum()),
                        "fwd60_end": float(g.loc[seg.index[-1], "fwd60"]) if not np.isnan(g.loc[seg.index[-1], "fwd60"]) else np.nan,
                        "fwd120_end": float(g.loc[seg.index[-1], "fwd120"]) if not np.isnan(g.loc[seg.index[-1], "fwd120"]) else np.nan})
        i = j
    return pd.DataFrame(eps)


def _pctl(series, v):
    """v 在 series 里的历史分位(0~1)。"""
    return float((series <= v).mean())


def classify(dur_p, int_p):
    """按(时长分位, 强度分位)分型。"""
    long_dur, short_dur = dur_p >= 0.66, dur_p <= 0.5
    high_int, low_int = int_p >= 0.66, int_p <= 0.5
    if long_dur and high_int:
        return "D 强力持续托底"
    if long_dur and not high_int:
        return "A 长期下降趋势(慢磨)"
    if short_dur and high_int:
        return "B 急跌·反弹预期"
    if short_dur and low_int:
        return "C 小幅波动"
    return "— 中间态"


def main():
    g = _flow()
    ep = _episodes(g)
    ep["dur_pctl"] = ep["dur"].apply(lambda v: _pctl(ep["dur"], v))
    ep["int_pctl"] = ep["peak_r20"].apply(lambda v: _pctl(ep["peak_r20"], v))
    ep["型"] = ep.apply(lambda r: classify(r["dur_pctl"], r["int_pctl"]), axis=1)
    print(f"净申购区间 共 {len(ep)} 段(≥{MIN_DUR}日),区间 {g.index.min().date()}~{g.index.max().date()}")
    print(f"时长分布(交易日): 中位 {ep['dur'].median():.0f} / 90分位 {ep['dur'].quantile(.9):.0f}")
    print(f"强度peak_r20(%AUM)分布: 中位 {ep['peak_r20'].median():.1f} / 90分位 {ep['peak_r20'].quantile(.9):.1f}")

    print("\n=== 各段明细(按开始日) ===")
    show = ep.copy()
    show["起止"] = show["start"].dt.date.astype(str) + "~" + show["end"].dt.date.astype(str)
    show["时长"] = show["dur"].astype(str) + "(" + (show["dur_pctl"]*100).round(0).astype(int).astype(str) + "%)"
    show["强度"] = show["peak_r20"].round(0).astype(int).astype(str) + "(" + (show["int_pctl"]*100).round(0).astype(int).astype(str) + "%)"
    show["后60"] = (show["fwd60_end"]*100).round(1).astype(str) + "%"
    show["后120"] = (show["fwd120_end"]*100).round(1).astype(str) + "%"
    print(show[["起止", "时长", "强度", "型", "后60", "后120"]].to_string(index=False))

    print("\n=== 各型 → 段结束后 上证收益均值 ===")
    agg = ep.groupby("型").agg(n=("dur", "size"), 后60=("fwd60_end", lambda s: f"{np.nanmean(s)*100:+.1f}%"),
                              后120=("fwd120_end", lambda s: f"{np.nanmean(s)*100:+.1f}%"),
                              胜率120=("fwd120_end", lambda s: f"{np.nanmean(np.array(s)>0)*100:.0f}%"))
    print(agg.to_string())

    cur = ep.iloc[-1]
    ongoing = (g["r20"].iloc[-1] > 0)
    print(f"\n当前状态:{'仍在净申购区间' if ongoing else '当前非净申购区间'};最近一段 {cur['start'].date()}~{cur['end'].date()} "
          f"时长{cur['dur']}({cur['dur_pctl']*100:.0f}%) 强度{cur['peak_r20']:.0f}({cur['int_pctl']*100:.0f}%) → {cur['型']}")
    ep.to_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "etf_flow_episodes.csv"), encoding="utf-8-sig")


if __name__ == "__main__":
    main()
