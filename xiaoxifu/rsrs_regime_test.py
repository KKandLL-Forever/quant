"""
牛熊切换组合 开关对照:现用「沪深300 MA30&MA60 同时走坏」 vs 「RSRS标准分看空」。

其余不变(龙头腿/全天候腿/切换成本/信号滞后1天),只换避险开关,比绩效。
龙头腿走本地 DuckDB(load_stock_qfq),全天候腿+RSRS走 tushare。
用法:python xiaoxifu/rsrs_regime_test.py [--start 2022-01-01 --rn 18 --rm 600 --rs 0.7]
"""
import argparse
import numpy as np
import pandas as pd
import engine
import leader_momentum as lm
import allweather as aw
import regime_combo as rc
from rsrs import load_index, rsrs_beta_r2, _signal_series


def _combo(lead, allw, defensive, idx):
    """按避险开关拼组合净收益(信号滞后1天+切换成本)。"""
    defensive = defensive.reindex(idx).ffill().fillna(False).astype(bool)
    applied = defensive.shift(1).fillna(False).astype(bool)
    combo = pd.Series(np.where(applied, allw, lead), index=idx)
    switch = applied.ne(applied.shift(1)).fillna(False)
    combo = combo - switch * (engine.COMM_STOCK + engine.COMM_ETF + engine.STAMP_STOCK)
    return combo, int(switch.sum())


def rsrs_defensive(warm, end, n, m, s):
    """RSRS标准分看空(空仓状态)= 避险。返回 defensive 布尔 Series。"""
    df = load_index("000300.SH", warm, end)
    beta, _ = rsrs_beta_r2(df, n)
    z = (beta - beta.rolling(m, min_periods=20).mean()) / beta.rolling(m, min_periods=20).std()
    hold = _signal_series(z.dropna(), s, -s).reindex(df.index).ffill().fillna(0)
    return (hold == 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--end", default=pd.Timestamp.today().strftime("%Y-%m-%d"))
    ap.add_argument("--rn", type=int, default=18)
    ap.add_argument("--rm", type=int, default=600)
    ap.add_argument("--rs", type=float, default=0.7)
    args = ap.parse_args()

    codes = lm.saved_codes() or None
    stocks = lm._names(codes) if codes else lm.STOCKS
    print(f"龙头池 {len(stocks)} 只;RSRS 需 M={args.rm} 预热,大盘从 2019 拉起 ...")
    lead, *_ = rc._legs(stocks, engine.load_stock_qfq, 20, 5, 5, rc.WARM, args.end, engine.COMM_STOCK, engine.STAMP_STOCK)
    allw, *_ = rc._legs(aw.ETFS, engine.load_fund_qfq, 20, 1, 3, rc.WARM, args.end, engine.COMM_ETF, engine.STAMP_ETF)
    idx = lead.index.intersection(allw.index)
    lead, allw = lead.reindex(idx), allw.reindex(idx)

    def_ma = rc._hs300_regime(rc.WARM, args.end)
    def_rsrs = rsrs_defensive("2019-01-01", args.end, args.rn, args.rm, args.rs)
    combo_ma, sw_ma = _combo(lead, allw, def_ma, idx)
    combo_rsrs, sw_rsrs = _combo(lead, allw, def_rsrs, idx)

    m = idx >= pd.Timestamp(args.start)
    rows = {
        f"组合·MA30&60开关(现用) 切{sw_ma}": engine.perf(combo_ma[m]),
        f"组合·RSRS开关 切{sw_rsrs}": engine.perf(combo_rsrs[m]),
        "纯龙头": engine.perf(lead[m]),
        "纯全天候": engine.perf(allw[m]),
    }
    print(f"\n牛熊切换组合 开关对照  {args.start}~{args.end}  RSRS(N{args.rn},M{args.rm},S{args.rs})")
    print(pd.DataFrame(rows).T.to_string())
    print(f"\n避险占比:MA开关 {def_ma.reindex(idx).ffill().fillna(False).mean()*100:.0f}% | "
          f"RSRS开关 {def_rsrs.reindex(idx).ffill().fillna(False).mean()*100:.0f}%")


if __name__ == "__main__":
    main()
