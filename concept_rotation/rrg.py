"""concept_rotation/rrg.py — 概念 RRG 四象限 + 扩散指标组合(复现「做量化的西蒙」框架第二步)。

RRG(相对轮动图)关心两个东西,均相对基准(默认中证1000):
- RS-Ratio(相对强度):概念/基准的相对强弱曲线,JdK 归一到 100 附近。
- RS-Momentum(相对动量):RS-Ratio 的动量,同样归一到 100 附近。
四象限:领先(强且续强) / 改善(弱但转强) / 转弱(强但走弱) / 落后(弱且续弱)。

组合逻辑(视频核心):先按扩散指标选内部扩散最强/上升最快的概念,再用 RRG 只保留
领先区 + 改善区 → 主线候选。to_payload() 产出前端可直接用的结构。

用法:
  python concept_rotation/rrg.py                 # 打印最新交易日 主线候选(扩散高 + 领先/改善)
  python concept_rotation/rrg.py --bench 000001.SH
数据:ths_daily(板块指数,同花顺历史时点成分,无前视偏差)+ index_daily(基准)+ diffusion.py。
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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cache_tushare as c
from diffusion import compute_diffusion

_QUAD = {(True, True): "领先", (True, False): "转弱", (False, False): "落后", (False, True): "改善"}


def _jdk(rs: pd.Series, win: int = 60, mwin: int = 10) -> tuple[pd.Series, pd.Series]:
    """RS 曲线 → (RS-Ratio, RS-Momentum),各自滚动标准化到 100 附近。"""
    rsr = 100 + (rs - rs.rolling(win).mean()) / rs.rolling(win).std()
    mom = rsr - rsr.shift(mwin)
    rsm = 100 + (mom - mom.rolling(win).mean()) / mom.rolling(win).std()
    return rsr, rsm


def compute_rrg(bench: str = "000852.SH", win: int = 60) -> pd.DataFrame:
    """算全市场概念的 RRG 时序,返回 [ts_code,name,trade_date,rs_ratio,rs_momentum,quadrant]。"""
    con = duckdb.connect(c.DUCKDB_PATH, read_only=True)
    cp = con.execute("SELECT t.ts_code, t.trade_date, t.close, i.name FROM ths_daily t "
                     "JOIN ths_index i USING (ts_code)").df()
    bm = con.execute("SELECT strftime(trade_date,'%Y%m%d') AS trade_date, close AS bclose "
                     "FROM index_daily WHERE ts_code = ?", [bench]).df()
    con.close()

    df = cp.merge(bm, on="trade_date").sort_values(["ts_code", "trade_date"])
    df["rs"] = 100 * df["close"] / df["bclose"]
    out = []
    for code, sub in df.groupby("ts_code"):
        sub = sub.copy()
        sub["rs_ratio"], sub["rs_momentum"] = _jdk(sub["rs"], win)
        out.append(sub)
    r = pd.concat(out, ignore_index=True)
    r = r.dropna(subset=["rs_ratio", "rs_momentum"])
    r["quadrant"] = [_QUAD[(a > 100, b > 100)] for a, b in zip(r["rs_ratio"], r["rs_momentum"])]
    return r[["ts_code", "name", "trade_date", "rs_ratio", "rs_momentum", "quadrant"]]


def combine(bench: str = "000852.SH", diff_top: int = 40, up: str = "ma20") -> pd.DataFrame:
    """最新交易日:扩散指标 + RRG 合并,标出主线候选(扩散高 + 领先/改善区)。"""
    g = compute_diffusion(up=up)
    r = compute_rrg(bench=bench)
    gd = g["trade_date"].astype(str).str.replace("-", "").str[:8]
    g = g.assign(td=gd)
    d = g["td"].max()
    gg = g[(g["td"] == d) & g["diffusion"].notna()][["ts_code", "name", "diffusion", "diffusion_raw", "mom20"]]
    rd = r["trade_date"].max()
    rr = r[r["trade_date"] == rd][["ts_code", "rs_ratio", "rs_momentum", "quadrant"]]
    m = gg.merge(rr, on="ts_code", how="inner")
    m["diff_rank"] = m["diffusion"].rank(ascending=False)
    m["主线候选"] = (m["diff_rank"] <= diff_top) & m["quadrant"].isin(["领先", "改善"])
    return m.sort_values(["主线候选", "diffusion"], ascending=[False, False])


def to_payload(bench: str = "000852.SH", diff_top: int = 40, up: str = "ma20") -> dict:
    """前端用:{date, bench, concepts:[{name,code,diffusion,diffusion_raw,mom20,rs_ratio,rs_momentum,quadrant,main}]}。"""
    m = combine(bench, diff_top, up)
    g = compute_diffusion(up=up)
    d = str(g["trade_date"].max())[:10]
    rows = [{"name": r["name"], "code": r.ts_code,
             "diffusion": round(r.diffusion, 4), "diffusion_raw": round(r.diffusion_raw, 4),
             "mom20": round(r.mom20, 4) if pd.notna(r.mom20) else None,
             "rs_ratio": round(r.rs_ratio, 2), "rs_momentum": round(r.rs_momentum, 2),
             "quadrant": r.quadrant, "main": bool(r["主线候选"])}
            for _, r in m.iterrows()]
    return {"date": d, "bench": bench, "concepts": rows}


def _print(bench: str, up: str):
    m = combine(bench, up=up)
    fmt = lambda x: f"{x*100:5.1f}%"
    mains = m[m["主线候选"]]
    print(f"\n=== 主线候选(扩散高 + RRG 领先/改善区,基准 {bench})===")
    print(f"{'概念':<16}{'扩散':>8}{'20日动量':>10}{'RS强度':>9}{'RS动量':>9}{'象限':>6}")
    for _, r in mains.iterrows():
        print(f"{r['name']:<16}{fmt(r.diffusion):>8}{fmt(r.mom20):>11}{r.rs_ratio:>9.1f}{r.rs_momentum:>9.1f}{r.quadrant:>7}")
    print(f"\n(共 {len(mains)} 个主线候选 / 全市场 {len(m)} 个概念)")
    print("四象限分布:", m["quadrant"].value_counts().to_dict())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench", default="000852.SH")
    ap.add_argument("--up", choices=["ma20", "pctup"], default="ma20")
    args = ap.parse_args()
    _print(args.bench, args.up)
