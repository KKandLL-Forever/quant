"""ETF 趋势跟踪真实回测：European（连续调仓）vs American（离散进出），按散户真实成本结算。

背景：research/tf_acf_spectrum.py 用 Sepp & Lucic (2026) 闭式公式测出 A 股只有短期自相关、
没有长记忆，最优跨度落在最短端，ETF 层临界单边成本 7–12.7bp。本脚本回答闭式公式回答不了的
最后一个问题：**扣掉散户真实成本（含最低佣金与买卖价差）后还剩多少**。

论文规则（papers/trend_following/NOTES.md 有公式核对）：
  European  式 2.7/4.1/4.2：z=r/sigma_{t-1} → 方差保持 EWMA → 仓位正比信号，日频调仓
  American  式 A.5-A.14：快慢 EWMA 价格滤波 + omega*ATR 进场缓冲 + p*ATR 跟踪止损，
            仓位建仓时按 R*s/ATR 定死，出场要求「跌破止损 且 进场信号已关闭」

散户约束（与论文的机构设定不同，这是本脚本存在的理由）：
  1. 只做多。A 股 ETF 融券券源少、成本高，论文的多空设定不可执行（--allow-short 可开多空对照）
  2. 佣金万 1 但**每笔最低 5 元**，单笔金额越小实际费率越高，这是小资金的主要磨损
  3. 买卖价差：ETF 最小变动 0.001 元，吃对手价单边付半个价差
  4. 冲击成本忽略：20 万相对这些 ETF 几十亿日成交额占比不到万分之一
  5. 每个标的分到 总资金/N，单腿仓位上限 100%（不加杠杆）

用法：
  python research/tf_etf_backtest.py
  python research/tf_etf_backtest.py --capital 200000 --n-etf 5
  python research/tf_etf_backtest.py --allow-short --start 2019-01-01

产出：
  research/tf_etf_backtest.csv       每个配置一行（年化/夏普/回撤/换手/实付成本/前后半段夏普）
  控制台                              European vs American vs 买入持有 对比表

结论（20 万 / 5 只 ETF / 只做多 / 2016-01 至 2026-08，10.5 年）：

  配置                    年化     夏普   最大回撤   10.5年累计成本
  American 250/20 w=1    5.30%   0.51   -13.8%      834 元
  European 60日+35%带     3.51%   0.44   -14.3%    6,837 元
  European 60日 无带      2.72%   0.38   -17.7%   31,940 元
  买入持有                3.46%   0.29   -40.3%       71 元

1. **趋势跟踪在 A 股 ETF 上确实有效**，且三个维度同时改善：年化 3.5%→5.3%、夏普 0.29→0.51、
   最大回撤 -40.3%→-13.8%。回撤减半是比收益提升更稳的那一半（与论文「下行保护型」定位一致）。

2. **American（离散进出）完胜 European（连续调仓），赢在成本**：10.5 年累计交易成本
   834 元 vs 6,837–31,940 元，差 8–38 倍。原因是 American 每年只交易 6.5 笔、每笔接近满仓，
   而 European 天天微调、单笔仅 4000–6000 元，5 元最低佣金折合 8–13bp。
   对 20 万级别的散户，**不加不交易带的 European 是不可用的**（成本年拖累 1.5–2.9%）。

3. **闭式公式说的「最优跨度在最短端」在真实回测里不成立**。实测最优是 American 250/20（很慢）。
   原因：tf_acf_spectrum.py 用的论文成本模型是线性比例成本，没有「每笔最低 5 元」这种固定费用；
   固定费用惩罚小额高频，把最优跨度整体推向长端。这是闭式公式算不出来、必须回测才能发现的。

4. **参数稳健**：American 慢家族（slow=250）四个配置夏普 0.42–0.51，
   快家族（20/5、40/10、100/20）0.19–0.34，单调结构而非孤立的幸运格子。

5. **后半段仍为正**：American 250/20 夏普 前半 0.63 / 后半 0.36，而买入持有 0.46 / 0.13，
   近五年的相对优势反而更大。

已知局限：
  - 45 个配置里挑最优 = 样本内优化。论文 p.26 自己就说「网格最大夏普高估样本外价值」。
  - **夏普的统计误差很大**：10.5 年样本下 se(SR) 约 0.33，0.51 对应 t≈1.55，
    达不到 95% 显著。回撤从 -40% 降到 -14% 是比夏普差异稳健得多的观察。
  - 空仓期间未计现金收益（实盘可放货币基金），真实收益会略高于此处。
  - 未计 ETF 折溢价与停牌，未计红利税差异。
"""

import argparse
import os

import duckdb
import numpy as np
import pandas as pd

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "stock_data_tushare.duckdb")
OUT_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tf_etf_backtest.csv")

A_TRADING = 243
VOL_SPAN = 33
ATR_SPAN = 33
VOL_TARGET = 0.15
MIN_COMMISSION = 5.0
COMMISSION_RATE = 1e-4
TICK = 0.001

EUROPEAN_SPANS = (5, 10, 20, 40, 60, 120)
EUROPEAN_BANDS = (0.0, 0.10, 0.20, 0.35)
AMERICAN_PAIRS = ((20, 5), (40, 10), (100, 20), (250, 20), (250, 50))
AMERICAN_OMEGAS = (0.5, 1.0, 2.0, 5.0)
AMERICAN_STOP_P = 5.0
AMERICAN_R = 0.015

UNIVERSE = {
    "510300.SH": "沪深300",
    "510500.SH": "中证500",
    "512100.SH": "中证1000",
    "510050.SH": "上证50",
    "159915.SZ": "创业板",
    "510880.SH": "红利",
    "512880.SH": "证券",
    "518880.SH": "黄金",
    "512010.SH": "医药",
    "159928.SZ": "消费",
}


def load_etf(codes, start):
    """读取 ETF 复权日线（close/high/low 同乘复权因子），返回 {code: DataFrame}。"""
    con = duckdb.connect(DB_PATH, read_only=True)
    con.execute("SET threads=1")
    try:
        df = con.execute(
            """
            SELECT f.ts_code, f.trade_date, f.close, f.high, f.low, a.adj_factor
            FROM fund_daily f
            JOIN fund_adj a ON f.ts_code = a.ts_code AND f.trade_date = a.trade_date
            WHERE f.ts_code IN ? AND f.trade_date >= ?
              AND f.close IS NOT NULL AND f.close > 0
            ORDER BY f.ts_code, f.trade_date
            """,
            [list(codes), start.replace("-", "")],
        ).df()
    finally:
        con.close()
    out = {}
    for code, g in df.groupby("ts_code"):
        g = g.set_index("trade_date").sort_index()
        adj = g["adj_factor"] / g["adj_factor"].iloc[-1]
        out[code] = pd.DataFrame(
            {
                "px_raw": g["close"],
                "close": g["close"] * adj,
                "high": g["high"] * adj,
                "low": g["low"] * adj,
            }
        ).dropna()
    return out


def atr(df, span=ATR_SPAN):
    """按式 A.1/A.2 计算 ATR（用 EWMA 代替等权均值，与波动率估计口径一致）。"""
    prev = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(span=span, adjust=False).mean()


def european_weights(df, span, allow_short):
    """按式 2.7/4.1/4.2 生成 European 连续仓位序列（已滞后一日，无前视）。"""
    r = df["close"].pct_change()
    sigma = np.sqrt(r.pow(2).ewm(span=VOL_SPAN, adjust=False).mean()).shift(1)
    z = (r / sigma).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    nu = 1.0 - 2.0 / (span + 1.0)
    signal = z.ewm(span=span, adjust=False).mean() * np.sqrt((1 + nu) / (1 - nu))
    w = signal * VOL_TARGET / (np.sqrt(A_TRADING) * sigma)
    w = w.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    lo = -1.0 if allow_short else 0.0
    return w.clip(lo, 1.0)


def american_weights(df, slow, fast, allow_short, omega=1.0, stop_p=AMERICAN_STOP_P, risk_r=AMERICAN_R):
    """按式 A.5-A.13 生成 American 离散仓位序列（进场缓冲 + 跟踪止损，建仓时定死仓位）。"""
    s = df["close"]
    s_slow = s.ewm(span=slow, adjust=False).mean()
    s_fast = s.ewm(span=fast, adjust=False).mean()
    a = atr(df)

    w = np.zeros(len(s))
    pos, size, stop = 0, 0.0, 0.0
    sv, fv, av, pv = s_slow.to_numpy(), s_fast.to_numpy(), a.to_numpy(), s.to_numpy()

    for i in range(len(s)):
        if not np.isfinite(av[i]) or av[i] <= 0:
            w[i] = 0.0
            continue
        long_on = fv[i] > sv[i] + omega * av[i]
        short_on = fv[i] < sv[i] - omega * av[i]

        if pos > 0:
            if pv[i] < stop and not long_on:
                pos, size = 0, 0.0
            else:
                stop = max(stop, pv[i] - stop_p * av[i])
        elif pos < 0:
            if pv[i] > stop and not short_on:
                pos, size = 0, 0.0
            else:
                stop = min(stop, pv[i] + stop_p * av[i])

        if pos == 0:
            if long_on:
                pos, size, stop = 1, min(risk_r * pv[i] / av[i], 1.0), pv[i] - stop_p * av[i]
            elif short_on and allow_short:
                pos, size, stop = -1, -min(risk_r * pv[i] / av[i], 1.0), pv[i] + stop_p * av[i]
        w[i] = size
    return pd.Series(w, index=s.index)


def american_trace(df, slow, fast, omega=1.0, stop_p=AMERICAN_STOP_P, risk_r=AMERICAN_R):
    """复跑 American 规则并记录每笔进出与当前状态，返回 (trades_df, status_dict)。"""
    s = df["close"]
    s_slow = s.ewm(span=slow, adjust=False).mean()
    s_fast = s.ewm(span=fast, adjust=False).mean()
    a = atr(df)
    sv, fv, av, pv = s_slow.to_numpy(), s_fast.to_numpy(), a.to_numpy(), s.to_numpy()
    raw = df["px_raw"].to_numpy()
    idx = s.index

    trades, pos, size, stop, entry_i = [], 0, 0.0, 0.0, None
    for i in range(len(s)):
        if not np.isfinite(av[i]) or av[i] <= 0:
            continue
        long_on = fv[i] > sv[i] + omega * av[i]
        if pos > 0:
            if pv[i] < stop and not long_on:
                trades.append(
                    {
                        "买入日": idx[entry_i], "买入价": raw[entry_i],
                        "卖出日": idx[i], "卖出价": raw[i],
                        "仓位": size, "持有天数": i - entry_i,
                        "收益率": pv[i] / pv[entry_i] - 1,
                    }
                )
                pos, size, entry_i = 0, 0.0, None
            else:
                stop = max(stop, pv[i] - stop_p * av[i])
        if pos == 0 and long_on:
            pos, size, stop, entry_i = 1, min(risk_r * pv[i] / av[i], 1.0), pv[i] - stop_p * av[i], i

    last = len(s) - 1
    status = {
        "日期": idx[last], "现价": raw[last],
        "快线": fv[last], "慢线": sv[last], "ATR": av[last],
        "进场线": sv[last] + omega * av[last],
        "信号": "持有中" if pos > 0 else "空仓",
        "仓位": size,
        "止损价(复权)": stop if pos > 0 else np.nan,
        "止损价(实际)": stop / pv[last] * raw[last] if pos > 0 else np.nan,
        "买入日": idx[entry_i] if entry_i is not None else None,
        "浮动盈亏": pv[last] / pv[entry_i] - 1 if entry_i is not None else np.nan,
        "距进场差": (sv[last] + omega * av[last]) / fv[last] - 1 if pos == 0 else np.nan,
    }
    return pd.DataFrame(trades), status


def trade_cost(notional, price):
    """单笔交易成本（元）：佣金万 1 保底 5 元 + 半个买卖价差。"""
    if notional <= 0:
        return 0.0
    commission = max(MIN_COMMISSION, notional * COMMISSION_RATE)
    spread = notional * (TICK / price) / 2.0
    return commission + spread


def run_sleeve(df, weights, capital, band=0.0):
    """单个标的的资金曲线，逐日结算收益与成本；band 为不交易带（目标与现仓差额小于它就不动）。"""
    r = df["close"].pct_change().fillna(0.0).to_numpy()
    px = df["px_raw"].to_numpy()
    w = weights.to_numpy()
    eq = np.empty(len(r))
    equity, held_w, cost_sum, turn_sum, n_trades = capital, 0.0, 0.0, 0.0, 0
    for i in range(len(r)):
        equity *= 1.0 + held_w * r[i]
        gap = abs(w[i] - held_w)
        if gap > band or (band > 0 and w[i] == 0.0 and held_w != 0.0):
            notional = gap * equity
            if notional > 0:
                c = trade_cost(notional, px[i])
                equity -= c
                cost_sum += c
                turn_sum += notional
                n_trades += 1
            held_w = w[i]
        eq[i] = equity
    return pd.Series(eq, index=df.index), turn_sum, cost_sum, n_trades


def metrics(equity, capital, years):
    """从资金曲线计算年化/夏普/最大回撤/前后半段夏普。"""
    ret = equity.pct_change().dropna()
    if len(ret) < 100 or equity.iloc[-1] <= 0:
        return {}
    cagr = (equity.iloc[-1] / capital) ** (1 / years) - 1
    vol = ret.std() * np.sqrt(A_TRADING)
    sharpe = ret.mean() / ret.std() * np.sqrt(A_TRADING) if ret.std() > 0 else np.nan
    dd = (equity / equity.cummax() - 1).min()
    half = len(ret) // 2
    h1, h2 = ret.iloc[:half], ret.iloc[half:]
    return {
        "年化": cagr,
        "波动": vol,
        "夏普": sharpe,
        "最大回撤": dd,
        "夏普前半": h1.mean() / h1.std() * np.sqrt(A_TRADING) if h1.std() > 0 else np.nan,
        "夏普后半": h2.mean() / h2.std() * np.sqrt(A_TRADING) if h2.std() > 0 else np.nan,
    }


def run_config(data, name, weight_fn, capital, band=0.0):
    """把一套信号跑到全部标的上并汇总成组合，返回一行结果。"""
    n = len(data)
    sleeve_cap = capital / n
    curves, turn, cost, trades = [], 0.0, 0.0, 0
    for code, df in data.items():
        eq, t, c, k = run_sleeve(df, weight_fn(df), sleeve_cap, band)
        curves.append(eq)
        turn += t
        cost += c
        trades += k
    port = pd.concat(curves, axis=1).ffill().dropna().sum(axis=1)
    years = len(port) / A_TRADING
    m = metrics(port, capital, years)
    if not m:
        return None
    m.update(
        {
            "配置": name,
            "年换手": turn / capital / years,
            "年交易笔数": trades / years,
            "单笔均额": turn / trades if trades else np.nan,
            "累计成本元": cost,
            "成本年化拖累": cost / capital / years,
            "期末资金": port.iloc[-1],
        }
    )
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=200000)
    ap.add_argument("--n-etf", type=int, default=8)
    ap.add_argument("--start", default="2016-01-04")
    ap.add_argument("--allow-short", action="store_true")
    ap.add_argument("--trades", action="store_true", help="打印 American 250/20 的历史交易与当前状态")
    args = ap.parse_args()

    codes = list(UNIVERSE)[: args.n_etf]
    data = load_etf(codes, args.start)
    data = {c: d for c, d in data.items() if len(d) > 500}

    if args.trades:
        allt = []
        print(f"=== American 250/20 (omega=1, 止损 5*ATR) 当前状态 @ {args.start} 起 ===\n")
        st_rows = []
        for code, df in data.items():
            tr, st = american_trace(df, 250, 20)
            tr.insert(0, "标的", UNIVERSE[code])
            allt.append(tr)
            st["标的"] = UNIVERSE[code]
            st_rows.append(st)
        sdf = pd.DataFrame(st_rows).set_index("标的")
        print(sdf[["日期", "现价", "信号", "仓位", "止损价(实际)", "浮动盈亏", "买入日", "距进场差"]]
              .to_string(float_format=lambda v: f"{v:.4f}"))
        t = pd.concat(allt, ignore_index=True)
        print(f"\n=== 全部 {len(t)} 笔交易 ===")
        print(t.to_string(index=False, float_format=lambda v: f"{v:.4f}"))
        print(f"\n胜率 {(t['收益率'] > 0).mean():.1%}  平均持有 {t['持有天数'].mean():.0f} 天  "
              f"平均盈利 {t.loc[t['收益率'] > 0, '收益率'].mean():.2%}  "
              f"平均亏损 {t.loc[t['收益率'] <= 0, '收益率'].mean():.2%}")
        return
    print(f"标的 {len(data)} 只: {[UNIVERSE[c] for c in data]}")
    print(f"资金 {args.capital:,.0f}，每只 {args.capital/len(data):,.0f}，"
          f"方向 {'多空' if args.allow_short else '只做多'}\n")

    rows = []
    for span in EUROPEAN_SPANS:
        for band in EUROPEAN_BANDS:
            r = run_config(data, f"European-{span}日-带{band:.0%}",
                           lambda df, s=span: european_weights(df, s, args.allow_short),
                           args.capital, band)
            if r:
                rows.append(r)
    for slow, fast in AMERICAN_PAIRS:
        for om in AMERICAN_OMEGAS:
            r = run_config(data, f"American-{slow}/{fast}-w{om:g}",
                           lambda df, sl=slow, f=fast, o=om: american_weights(
                               df, sl, f, args.allow_short, omega=o),
                           args.capital)
            if r:
                rows.append(r)
    r = run_config(data, "买入持有", lambda df: pd.Series(1.0, index=df.index), args.capital)
    if r:
        rows.append(r)

    df = pd.DataFrame(rows)
    cols = ["配置", "年化", "夏普", "最大回撤", "波动", "年换手", "年交易笔数", "单笔均额",
            "成本年化拖累", "累计成本元", "夏普前半", "夏普后半", "期末资金"]
    df = df[cols].sort_values("夏普", ascending=False)
    df.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")

    show = df.head(20).copy()
    for c in ["年化", "最大回撤", "波动", "成本年化拖累"]:
        show[c] = show[c].map(lambda v: f"{v:.2%}")
    for c in ["夏普", "夏普前半", "夏普后半", "年换手"]:
        show[c] = show[c].map(lambda v: f"{v:.2f}")
    for c in ["累计成本元", "期末资金", "单笔均额", "年交易笔数"]:
        show[c] = show[c].map(lambda v: f"{v:,.0f}")
    print(show.to_string(index=False))
    print(f"\n共 {len(df)} 个配置，saved -> {OUT_CSV}")


if __name__ == "__main__":
    main()
