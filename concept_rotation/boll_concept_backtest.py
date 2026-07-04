"""concept_rotation/boll_concept_backtest.py — BOLL突破信号 × 概念轮动主线(改善区)联动回测。

问题:一个 BOLL 缩口扩张+MACD金叉的买点,当它所属同花顺概念当天正处于「改善区主线候选」
(concept_signals.main 且 quadrant=改善)时,后续收益是否显著更好?

做法:复用 boll_expand_macd.build_signals 生成历史信号(带 f5/f10/f20 前瞻收益)→ 每个信号
按 ths_member 映射到其概念 → 查 concept_signals 当天该概念的象限/主线状态 → 按
「命中改善主线 / 命中领先主线 / 命中任意主线 / 未命中」分组,比前瞻收益均值/中位/胜率。

用法:python concept_rotation/boll_concept_backtest.py [--pool ml] [--start 2021-01-01] [--hold 10]
口径提醒:ths_member 概念成分是静态的(前视偏差);ML 池用当前名单套历史(前视);结论看相对差异。
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
sys.path.insert(0, os.path.join(_ROOT, "boll_narrow_exit"))
import cache_tushare as c
import boll_expand_macd as bem


def _flags(sig: pd.DataFrame) -> pd.DataFrame:
    """给每个信号打概念联动标记:hit_improve/hit_lead/hit_lead_up(领先且扩散动量>0)/hit_main。"""
    con = duckdb.connect(c.DUCKDB_PATH, read_only=True)
    mem = con.execute("SELECT con_code AS stock, ts_code AS cpt FROM ths_member").df()
    cs = con.execute("SELECT trade_date AS td, ts_code AS cpt, main, quadrant, mom20 FROM concept_signals "
                     "WHERE bench='000852.SH' AND up='ma20'").df()
    con.close()
    s = sig[["ts_code", "date"]].copy()
    s["td"] = s["date"].dt.strftime("%Y%m%d")
    s["sid"] = range(len(s))
    j = s.merge(mem, left_on="ts_code", right_on="stock").merge(cs, on=["cpt", "td"])
    j["improve"] = j["main"] & (j["quadrant"] == "改善")
    j["lead"] = j["main"] & (j["quadrant"] == "领先")
    j["lead_up"] = j["lead"] & (j["mom20"] > 0)
    agg = j.groupby("sid").agg(hit_improve=("improve", "max"), hit_lead=("lead", "max"),
                               hit_lead_up=("lead_up", "max"), hit_main=("main", "max")).reset_index()
    out = sig.copy()
    out["sid"] = range(len(out))
    out = out.merge(agg, on="sid", how="left")
    for col in ("hit_improve", "hit_lead", "hit_lead_up", "hit_main"):
        out[col] = out[col].fillna(False).astype(bool)
    return out


def _stat(name: str, s: pd.DataFrame, col: str):
    """打印某组信号在某前瞻收益列上的 n/均值/中位/胜率。"""
    v = s[col].dropna()
    if len(v) == 0:
        print(f"  {name:<22} n={0}")
        return
    print(f"  {name:<22} n={len(v):>5}  均值{v.mean()*100:+6.2f}%  中位{v.median()*100:+6.2f}%  胜率{(v>0).mean()*100:4.0f}%")


def run(pool="ml", start="2021-01-01", hold=10, squeeze_q=0.25, cross_win=3, up_mode="mid"):
    """跑联动回测,f10 上分三组测试:①领先+扩散动量>0 ②大盘健康门控 ③第二次信号叠加主线。"""
    codes = {"ml": bem.members_ml, "csi2000": bem.members_2000, "csi1000": bem.members_1000}[pool]()
    df = bem.load(codes, start)
    sig = bem.build_signals(df, squeeze_q, cross_win, up_mode, hold)
    if sig.empty:
        print("无信号"); return
    sig = _flags(sig)
    sig = sig.sort_values(["ts_code", "date"])
    sig["rep30"] = sig.groupby("ts_code")["date"].diff().dt.days.le(30).fillna(False)
    mk = bem.hs300_market(start)[["date", "mkt_bad"]]
    sig = sig.merge(mk, on="date", how="left")
    sig["healthy"] = sig["mkt_bad"] == False

    print(f"\n=== BOLL × 概念主线 联动回测(池={pool} 起={start} hold={hold} 共 {len(sig)} 信号)===")
    col = "f10"

    print("\n── 基线 ──")
    _stat("全部信号", sig, col)

    print("\n── 测试1:领先区 + 扩散动量>0(要求概念确认走强且广度在扩) ──")
    _stat("命中领先区主线", sig[sig["hit_lead"]], col)
    _stat("领先区 + 扩散动量>0", sig[sig["hit_lead_up"]], col)

    print("\n── 测试2:大盘健康门控(只在 MA30&MA60 未同时走坏时) ──")
    h = sig[sig["healthy"]]
    _stat("健康-全部信号", h, col)
    _stat("健康-命中任意主线", h[h["hit_main"]], col)
    _stat("健康-改善区主线", h[h["hit_improve"]], col)
    _stat("健康-领先区主线", h[h["hit_lead"]], col)
    _stat("健康-领先+扩散动量>0", h[h["hit_lead_up"]], col)
    _stat("不健康-全部信号", sig[~sig["healthy"]], col)

    print("\n── 测试3:第二次信号(30日内再现)叠加主线 ──")
    r = sig[sig["rep30"]]
    _stat("第二次信号-全部", r, col)
    _stat("第二次 + 命中任意主线", r[r["hit_main"]], col)
    _stat("第二次 + 领先区主线", r[r["hit_lead"]], col)
    _stat("第二次 + 领先+扩散动量>0", r[r["hit_lead_up"]], col)

    print("\n── 对照:领先区到底加不加分(控制住 第二次+健康) ──")
    rh = r[r["healthy"]]
    _stat("第二次 + 健康(对照基准)", rh, col)
    _stat("第二次 + 健康 + 领先区", rh[rh["hit_lead"]], col)
    _stat("第二次 + 健康 + 命中任意主线", rh[rh["hit_main"]], col)
    _stat("第二次 + 健康 + 未命中主线", rh[~rh["hit_main"]], col)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default="ml")
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--hold", type=int, default=10)
    args = ap.parse_args()
    run(args.pool, args.start, args.hold)
