"""concept_rotation/diffusion.py — 概念「扩散指标」原型(复现「做量化的西蒙」RRG+扩散框架第一步)。

扩散指标定义(按视频):一个概念内部,处于上涨状态的成分股,其自由流通市值占整个概念
自由流通市值的比例;再用 MA20 平滑去噪。比单纯看概念涨幅更能反映板块内部真实赚钱效应。
- 「上涨状态」默认口径:个股后复权收盘站上自身 MA20(可切 pctup=当日上涨)。
- 数据全部本地:daily(收盘)+ adj_factor(后复权)+ daily_basic(circ_mv 自由流通市值)
  + ths_member(同花顺概念→成分股,静态映射,注意历史成分前视偏差)。
- 概念指数价格(RRG 相对强弱要用)本文件不涉及,留待下一步拉 ths_daily 缓存。

用法:
  python concept_rotation/diffusion.py            # 打印最新交易日扩散榜 top20 + 扩散上升榜
  python concept_rotation/diffusion.py --top 30 --up pctup
产出:每个概念的 diffusion(MA20 平滑值)、diffusion_raw(当日原始占比)、mom20(20日扩散动量)。
依赖:db_loader/cache_tushare 的 DUCKDB_PATH。
"""
import argparse
import os
import sys

import duckdb
import pandas as pd

_ROOT = os.path.dirname(os.path.abspath(__file__))
while _ROOT != "/" and not os.path.exists(os.path.join(_ROOT, "cache_tushare.py")):
    _ROOT = os.path.dirname(_ROOT)
sys.path.insert(0, _ROOT)
import cache_tushare as c


def compute_diffusion(lookback_days: int = 160, up: str = "ma20") -> pd.DataFrame:
    """算全市场概念的扩散指标时序,返回 [ts_code,name,trade_date,diffusion_raw,diffusion,mom20]。"""
    con = duckdb.connect(c.DUCKDB_PATH, read_only=True)
    max_date = con.execute("SELECT max(trade_date) FROM daily_basic").fetchone()[0]
    start = con.execute(
        "SELECT min(trade_date) FROM (SELECT DISTINCT trade_date FROM daily "
        "WHERE trade_date <= ? ORDER BY trade_date DESC LIMIT ?)",
        [max_date, lookback_days]).fetchone()[0]

    px = con.execute(
        "SELECT d.ts_code, d.trade_date, d.close * a.adj_factor AS hfq, d.pct_chg "
        "FROM daily d JOIN adj_factor a USING (ts_code, trade_date) "
        "WHERE d.trade_date >= ?", [start]).df()
    mv = con.execute(
        "SELECT ts_code, trade_date, circ_mv FROM daily_basic WHERE trade_date >= ?",
        [start]).df()
    mem = con.execute("SELECT ts_code AS cpt, con_code AS ts_code FROM ths_member").df()
    idx = con.execute("SELECT ts_code AS cpt, name FROM ths_index").df()
    con.close()

    px = px.sort_values(["ts_code", "trade_date"])
    px["ma20"] = px.groupby("ts_code")["hfq"].transform(lambda s: s.rolling(20).mean())
    if up == "ma20":
        px["upstate"] = (px["hfq"] > px["ma20"]).astype(float)
    else:
        px["upstate"] = (px["pct_chg"] > 0).astype(float)

    df = px.merge(mv, on=["ts_code", "trade_date"]).merge(mem, on="ts_code")
    df["w_up"] = df["circ_mv"] * df["upstate"]
    g = df.groupby(["cpt", "trade_date"]).agg(w_up=("w_up", "sum"),
                                              w_all=("circ_mv", "sum")).reset_index()
    g = g[g["w_all"] > 0]
    g["diffusion_raw"] = g["w_up"] / g["w_all"]
    g = g.sort_values(["cpt", "trade_date"])
    g["diffusion"] = g.groupby("cpt")["diffusion_raw"].transform(lambda s: s.rolling(20).mean())
    g["mom20"] = g.groupby("cpt")["diffusion"].transform(lambda s: s - s.shift(20))
    g = g.merge(idx, on="cpt").rename(columns={"cpt": "ts_code"})
    return g[["ts_code", "name", "trade_date", "diffusion_raw", "diffusion", "mom20"]]


def latest_rank(top: int = 20, up: str = "ma20"):
    """打印最新交易日扩散榜 + 扩散上升榜(二次启动候选)。"""
    g = compute_diffusion(up=up)
    d = g["trade_date"].max()
    cur = g[(g["trade_date"] == d) & g["diffusion"].notna()].copy()
    fmt = lambda x: f"{x*100:5.1f}%"
    print(f"\n=== {d} 概念扩散榜 top{top}(口径:{'站上MA20' if up=='ma20' else '当日上涨'})===")
    print(f"{'概念':<16}{'扩散(MA20)':>10}{'当日原始':>10}{'20日动量':>10}")
    for _, r in cur.sort_values("diffusion", ascending=False).head(top).iterrows():
        print(f"{r['name']:<16}{fmt(r.diffusion):>10}{fmt(r.diffusion_raw):>10}{fmt(r.mom20):>11}")
    print(f"\n=== {d} 扩散上升榜 top{top}(20日动量最大 = 冷门变热点/二次启动)===")
    for _, r in cur[cur["mom20"].notna()].sort_values("mom20", ascending=False).head(top).iterrows():
        print(f"{r['name']:<16}{fmt(r.diffusion):>10}{fmt(r.diffusion_raw):>10}{fmt(r.mom20):>11}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--up", choices=["ma20", "pctup"], default="ma20")
    args = ap.parse_args()
    latest_rank(args.top, args.up)
