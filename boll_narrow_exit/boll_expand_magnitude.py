"""
验证假设:BOLL 缩口→扩张「这一下的幅度」是否决定成功率。

用 boll_expand_macd 的信号(缩口→扩张+MACD金叉+站上中轨),把每个买点按「扩张幅度」分5档,
看未来10/20日平均涨幅与胜率是否随幅度单调递增。三个幅度口径对比:
  bw_jump  = 带宽单日跳升比(用户猜的)
  thrust   = 站上上轨的半带宽数(突破K线爆发力)
  vol_ratio= 放量倍数(对照)
另测「thrust>0(真站上轨) + 放量」联合过滤的增益。

用法:python boll_narrow_exit/boll_expand_magnitude.py [--pool ml|csi1000|csi2000 --start 2021-01-01]
"""
import os
_ROOT = os.path.dirname(os.path.abspath(__file__))
while _ROOT != "/" and not os.path.exists(os.path.join(_ROOT, "cache_tushare.py")):
    _ROOT = os.path.dirname(_ROOT)
import sys
sys.path.insert(0, _ROOT)
import argparse
import numpy as np
import pandas as pd
import boll_expand_macd as B


def _quantile_table(sig, by, fwd="f10", q=5):
    """按 by 列分 q 档,输出每档 均值涨幅%/胜率%/样本数(前瞻 fwd)。"""
    s = sig[[by, fwd]].dropna()
    s = s[np.isfinite(s[by])]
    if len(s) < q * 20:
        return None
    s["bucket"] = pd.qcut(s[by].rank(method="first"), q, labels=[f"Q{i+1}" for i in range(q)])
    g = s.groupby("bucket", observed=True)[fwd]
    return pd.DataFrame({f"{by}区间中值": s.groupby("bucket", observed=True)[by].median().round(3),
                         f"未来{fwd[1:]}日均涨%": (g.mean() * 100).round(2),
                         "胜率%": (g.apply(lambda x: (x > 0).mean()) * 100).round(1),
                         "样本": g.size()})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default="ml", choices=["ml", "csi1000", "csi2000"])
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--squeeze-q", type=float, default=0.25)
    ap.add_argument("--cross-win", type=int, default=3)
    ap.add_argument("--up", choices=["mid", "upper"], default="mid")
    args = ap.parse_args()

    codes = {"ml": B.members_ml, "csi1000": B.members_1000, "csi2000": B.members_2000}[args.pool]()
    print(f"池 {args.pool} {len(codes)} 只,加载 {args.start} 起 ...")
    df = B.load(codes, args.start)
    sig = B.build_signals(df, args.squeeze_q, args.cross_win, args.up)
    print(f"信号 {len(sig)} 个。全样本基线:未来10日均涨 {sig['f10'].mean()*100:.2f}% 胜率 {(sig['f10']>0).mean()*100:.1f}% | "
          f"未来20日均涨 {sig['f20'].mean()*100:.2f}% 胜率 {(sig['f20']>0).mean()*100:.1f}%\n")

    for by in ["bw_jump", "thrust", "vol_ratio"]:
        for fwd in ["f10", "f20"]:
            t = _quantile_table(sig, by, fwd)
            if t is not None:
                print(f"【按 {by} 分档 · {fwd}】")
                print(t.to_string()); print()

    print("=== 联合过滤对照(未来10日)===")
    base = sig
    combos = {
        "全部信号": base,
        "thrust>0(真站上轨)": base[base["thrust"] > 0],
        "放量 vol>1.5": base[base["vol_ratio"] > 1.5],
        "thrust>0 且 vol>1.5": base[(base["thrust"] > 0) & (base["vol_ratio"] > 1.5)],
        "thrust>0.5 且 vol>2": base[(base["thrust"] > 0.5) & (base["vol_ratio"] > 2)],
    }
    rows = {k: {"样本": len(v), "未来10日均涨%": round(v["f10"].mean() * 100, 2),
                "胜率%": round((v["f10"] > 0).mean() * 100, 1),
                "未来20日均涨%": round(v["f20"].mean() * 100, 2)} for k, v in combos.items() if len(v)}
    print(pd.DataFrame(rows).T.to_string())


if __name__ == "__main__":
    main()
