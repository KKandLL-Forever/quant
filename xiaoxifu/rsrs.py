"""
RSRS 阻力支撑相对强度指标 + 沪深300择时(复现光大金工研报 / 量化君系列)。

指标:N日 `最高价 = α + β×最低价` 的 OLS 斜率β(向量化:β=Cov(low,high)/Var(low),R²=Corr²)。
四版本择时(信号当日出、次日生效,带迟滞):
  斜率策略      β>1.0买 / β<0.8卖
  标准分策略    z=(β-rollmean_M)/rollstd_M(M=600),z>0.7买 / z<-0.7卖
  优化标准分    R²×z,同阈值±0.7
  右偏标准分    β×R²×z(N=16,M=300),同阈值±0.7
标的:沪深300指数,数据走 tushare index_daily(分年拉,避开本地库锁)。
用法:python xiaoxifu/rsrs.py [--start 2005-01-01]

供 ETF 轮动做大盘择时开关用:rsrs_signal() 返回逐日持仓开关(1可持/0空仓)。
"""
import argparse
import os
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cache_tushare as ct


def load_index(code, start, end):
    """分年拉 tushare index_daily OHLC,返回 DataFrame[open/high/low/close/pct]。"""
    import tushare as ts
    pro = ts.pro_api(ct._get_token())
    parts = []
    for y in range(int(start[:4]), int(end[:4]) + 1):
        d = pro.index_daily(ts_code=code, start_date=f"{y}0101", end_date=f"{y}1231",
                            fields="trade_date,open,high,low,close")
        if len(d):
            parts.append(d)
    df = pd.concat(parts).drop_duplicates("trade_date")
    df["dt"] = pd.to_datetime(df["trade_date"])
    df = df.sort_values("dt").set_index("dt")[["open", "high", "low", "close"]]
    df["pct"] = df["close"].pct_change()
    return df.dropna()


def rsrs_beta_r2(df, n):
    """向量化 N日滚动 OLS(high~low)的 斜率β 与 决定系数R²。"""
    x, y = df["low"], df["high"]
    mx, my = x.rolling(n).mean(), y.rolling(n).mean()
    cov = (x * y).rolling(n).mean() - mx * my
    vx = (x * x).rolling(n).mean() - mx * mx
    vy = (y * y).rolling(n).mean() - my * my
    beta = cov / vx
    r2 = (cov * cov) / (vx * vy)
    return beta, r2


def _signal_series(ind, buy, sell):
    """指标序列 + 买卖阈值(迟滞)→ 逐日持仓开关(信号次日生效)。"""
    pos, out = 0, []
    for v in ind:
        out.append(pos)
        if pos == 0 and v > buy:
            pos = 1
        elif pos == 1 and v < sell:
            pos = 0
    return pd.Series(out, index=ind.index)


def _perf(pct, pos):
    """持仓开关(信号已在序列内次日生效)→ 年化/回撤/夏普。"""
    r = (pct * pos).dropna()
    cum = (1 + r).cumprod()
    ann = cum.iloc[-1] ** (250 / len(r)) - 1
    vol = r.std() * np.sqrt(250)
    mdd = (cum / cum.cummax() - 1).min()
    return dict(年化收益=round(ann * 100, 2), 最大回撤=round(abs(mdd) * 100, 2),
                夏普=round(ann / vol, 3) if vol else None, 持仓占比=round(pos.mean() * 100, 1))


def rsrs_signal(df, n=18, m=600, buy=0.7, sell=-0.7):
    """标准分 RSRS 大盘开关:逐日持仓开关 Series(1可持/0空仓),供 ETF 轮动择时。"""
    beta, _ = rsrs_beta_r2(df, n)
    z = (beta - beta.rolling(m, min_periods=20).mean()) / beta.rolling(m, min_periods=20).std()
    return _signal_series(z.dropna(), buy, sell).reindex(df.index).ffill().fillna(0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2005-01-01")
    ap.add_argument("--end", default=pd.Timestamp.today().strftime("%Y-%m-%d"))
    ap.add_argument("--code", default="000300.SH")
    args = ap.parse_args()
    print(f"拉取 {args.code} {args.start}~{args.end} ...")
    df = load_index(args.code, args.start, args.end)

    rows = {}
    beta18, r2_18 = rsrs_beta_r2(df, 18)
    z18 = (beta18 - beta18.rolling(600, min_periods=20).mean()) / beta18.rolling(600, min_periods=20).std()
    rows["斜率策略"] = _perf(df["pct"], _signal_series(beta18.dropna(), 1.0, 0.8))
    rows["标准分"] = _perf(df["pct"], _signal_series(z18.dropna(), 0.7, -0.7))
    rows["优化标准分(×R²)"] = _perf(df["pct"], _signal_series((r2_18 * z18).dropna(), 0.7, -0.7))
    beta16, r2_16 = rsrs_beta_r2(df, 16)
    z16 = (beta16 - beta16.rolling(300, min_periods=20).mean()) / beta16.rolling(300, min_periods=20).std()
    rows["右偏标准分(β×R²×z,N16M300)"] = _perf(df["pct"], _signal_series((beta16 * r2_16 * z16).dropna(), 0.7, -0.7))
    rows["买入持有"] = _perf(df["pct"], pd.Series(1, index=df.index))

    print(f"\nRSRS 沪深300择时复现  {args.start}~{args.end}")
    print(pd.DataFrame(rows).T.to_string())


if __name__ == "__main__":
    main()
