"""ETF 趋势跟踪（American 式离散进出）信号：给 webapp「ETF趋势」页提供当日状态与历史交易。

策略来源：Sepp & Lucic (2026) The Science and Practice of Trend-Following Systems，式 A.5-A.13。
公式核对见 papers/trend_following/NOTES.md；A 股适用性研究见 research/tf_acf_spectrum.py；
参数选择与成本回测见 research/tf_etf_backtest.py。

为什么用 American（离散）而不是 European（连续调仓）：
  A 股散户佣金每笔最低 5 元。European 天天微调、单笔仅数千元，最低佣金折合 8-13bp，
  10.5 年累计成本吃掉本金 16%。American 每年只交易约 1.3 笔/只、每笔接近满仓，
  同期累计成本仅数百元。回测：American 250/20 年化 5.30%/夏普 0.51/最大回撤 -13.8%，
  买入持有 3.46%/0.29/-40.3%。

规则（每只 ETF 独立执行，只做多）：
  慢线 = 250 日 EWMA 收盘价，快线 = 20 日 EWMA 收盘价，ATR = 33 日 EWMA 真实波幅
  买入  空仓且 快线 > 慢线 + 1*ATR
  仓位  0.015 * 现价 / ATR，上限 100%，建仓时定死不再调整
  止损  建仓日 = 收盘 - 5*ATR；此后每日 max(昨日止损, 收盘 - 5*ATR)，只升不降
  卖出  收盘 < 昨日止损 **且** 快线 <= 慢线 + 1*ATR（两个条件必须同时成立）
        单看止损会在趋势未变时被震出、次日信号仍亮又要买回，白付两趟手续费（论文脚注 10）

标的（5 只，各分 1/5 资金，默认总资金 25 万）：
  510300 沪深300 / 588000 科创50 / 159915 创业板 / 510500 中证500 / 513100 纳指
  513100 是 QDII，与 A 股相关性低、分散效果最好，但有折溢价与额度限购：
  信号按二级市场收盘价算，实盘遇到高溢价或暂停申购时需自行判断是否执行。

用法：
  python etf_trend/trend_signal.py        打印当前状态与历史交易
  后端 /api/etftrend 调 to_payload()

命名注意：文件名不能叫 signal.py——后端用 sys.path.insert 加载本目录，会遮蔽 Python 标准库的
signal 模块，uvicorn/subprocess 都依赖它。
"""

import os

import duckdb
import numpy as np
import pandas as pd

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "stock_data_tushare.duckdb")

SLOW_SPAN = 250
FAST_SPAN = 20
ATR_SPAN = 33
OMEGA = 1.0
STOP_P = 5.0
RISK_R = 0.015
A_TRADING = 243
START = "20160104"

MIN_COMMISSION = 5.0
COMMISSION_RATE = 1e-4
TICK = 0.001

UNIVERSE = {
    "510300.SH": "沪深300ETF",
    "588000.SH": "科创50ETF",
    "159915.SZ": "创业板ETF",
    "510500.SH": "中证500ETF",
    "513100.SH": "纳指ETF",
}

DEFAULT_CAPITAL = 250000.0


def load_etf(codes, start=START):
    """读取 ETF 复权日线（close/high/low 同乘复权因子）,返回 {code: DataFrame}。"""
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
            [list(codes), start],
        ).df()
    finally:
        con.close()
    out = {}
    for code, g in df.groupby("ts_code"):
        g = g.set_index("trade_date").sort_index()
        adj = g["adj_factor"] / g["adj_factor"].iloc[-1]
        out[code] = pd.DataFrame(
            {"px": g["close"], "close": g["close"] * adj, "high": g["high"] * adj, "low": g["low"] * adj}
        ).dropna()
    return out


def atr(df, span=ATR_SPAN):
    """按式 A.1/A.2 计算真实波幅的 EWMA。"""
    prev = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(span=span, adjust=False).mean()


def run_rules(df):
    """按式 A.5-A.13 逐日跑规则,返回 (weights, trades, status)。"""
    s = df["close"]
    sv = s.ewm(span=SLOW_SPAN, adjust=False).mean().to_numpy()
    fv = s.ewm(span=FAST_SPAN, adjust=False).mean().to_numpy()
    av = atr(df).to_numpy()
    pv, raw, idx = s.to_numpy(), df["px"].to_numpy(), s.index

    w = np.zeros(len(s))
    trades, pos, size, stop, ei = [], 0, 0.0, 0.0, None
    for i in range(len(s)):
        if not np.isfinite(av[i]) or av[i] <= 0:
            continue
        long_on = fv[i] > sv[i] + OMEGA * av[i]
        if pos > 0:
            if pv[i] < stop and not long_on:
                trades.append({
                    "entry_date": idx[ei], "entry_px": round(float(raw[ei]), 4),
                    "exit_date": idx[i], "exit_px": round(float(raw[i]), 4),
                    "size": round(float(size), 4), "hold_days": int(i - ei),
                    "ret": round(float(pv[i] / pv[ei] - 1), 4),
                })
                pos, size, ei = 0, 0.0, None
            else:
                stop = max(stop, pv[i] - STOP_P * av[i])
        if pos == 0 and long_on:
            pos, size, stop, ei = 1, min(RISK_R * pv[i] / av[i], 1.0), pv[i] - STOP_P * av[i], i
        w[i] = size

    n = len(s) - 1
    entry_line = sv[n] + OMEGA * av[n]
    held = pos > 0
    status = {
        "date": str(idx[n]), "price": round(float(raw[n]), 4),
        "fast": round(float(fv[n]), 4), "slow": round(float(sv[n]), 4),
        "entry_line": round(float(entry_line), 4),
        "atr": round(float(av[n]), 4), "atr_pct": round(float(av[n] / pv[n] * 100), 2),
        "held": held, "size": round(float(size), 4) if held else 0.0,
        "stop": round(float(stop / pv[n] * raw[n]), 4) if held else None,
        "stop_gap": round(float(pv[n] / stop - 1) * 100, 2) if held else None,
        "entry_date": str(idx[ei]) if ei is not None else None,
        "entry_px": round(float(raw[ei]), 4) if ei is not None else None,
        "hold_days": int(n - ei) if ei is not None else None,
        "pnl": round(float(pv[n] / pv[ei] - 1) * 100, 2) if ei is not None else None,
        "to_entry": round(float(entry_line / fv[n] - 1) * 100, 2) if not held else None,
        "signal_on": bool(fv[n] > entry_line),
    }
    status["action"] = _action(status)
    return pd.Series(w, index=idx), trades, status


def _action(st):
    """把当前状态翻译成今日动作提示。"""
    if not st["held"]:
        return "买入" if st["signal_on"] else "观望"
    if st["stop_gap"] is not None and st["stop_gap"] < 0:
        return "卖出" if not st["signal_on"] else "预警(已破止损,信号仍在)"
    return "持有"


def _trade_cost(notional, price):
    """单笔成本:佣金万 1 保底 5 元 + 半个买卖价差。"""
    if notional <= 0:
        return 0.0
    return max(MIN_COMMISSION, notional * COMMISSION_RATE) + notional * (TICK / price) / 2.0


def _equity(df, weights, capital):
    """按真实成本逐日结算单腿资金曲线,返回 (curve, 累计成本)。"""
    r = df["close"].pct_change().fillna(0.0).to_numpy()
    px, w = df["px"].to_numpy(), weights.to_numpy()
    eq, equity, prev, cost = np.empty(len(r)), capital, 0.0, 0.0
    for i in range(len(r)):
        equity *= 1.0 + prev * r[i]
        if w[i] != prev:
            c = _trade_cost(abs(w[i] - prev) * equity, px[i])
            equity -= c
            cost += c
            prev = w[i]
        eq[i] = equity
    return pd.Series(eq, index=df.index), cost


def _metrics(curve, capital):
    """年化/夏普/最大回撤。"""
    ret = curve.pct_change().dropna()
    years = len(curve) / A_TRADING
    if len(ret) < 100 or curve.iloc[-1] <= 0:
        return {}
    return {
        "cagr": round(float((curve.iloc[-1] / capital) ** (1 / years) - 1) * 100, 2),
        "sharpe": round(float(ret.mean() / ret.std() * np.sqrt(A_TRADING)), 2) if ret.std() > 0 else None,
        "mdd": round(float((curve / curve.cummax() - 1).min()) * 100, 2),
        "final": round(float(curve.iloc[-1]), 0),
    }


def to_payload(capital=DEFAULT_CAPITAL, codes=None):
    """webapp 用:当前状态 + 历史交易 + 组合净值对比,返回可直接 JSON 化的 dict。"""
    codes = list(codes or UNIVERSE)
    data = load_etf(codes)
    sleeve = capital / max(len(data), 1)

    items, all_trades, curves, bh_curves, cost_sum = [], [], [], [], 0.0
    for code, df in data.items():
        name = UNIVERSE.get(code, code)
        w, trades, st = run_rules(df)
        items.append({"code": code, "name": name, **st})
        for t in trades:
            all_trades.append({"code": code, "name": name, **t})
        eq, c = _equity(df, w, sleeve)
        curves.append(eq)
        cost_sum += c
        bh_curves.append(_equity(df, pd.Series(1.0, index=df.index), sleeve)[0])

    def _join(cs):
        """未上市期间该腿计为现金,避免整条组合曲线被最晚上市的标的截断。"""
        m = pd.concat(cs, axis=1).sort_index()
        return m.ffill().fillna(sleeve).sum(axis=1)

    port, bh = _join(curves), _join(bh_curves)
    step = max(len(port) // 400, 1)
    equity = [
        {"date": str(d), "strat": round(float(a), 0), "bh": round(float(b), 0)}
        for d, a, b in zip(port.index[::step], port.to_numpy()[::step], bh.reindex(port.index).to_numpy()[::step])
    ]

    closed = [t for t in all_trades if t["ret"] is not None]
    wins = [t["ret"] for t in closed if t["ret"] > 0]
    losses = [t["ret"] for t in closed if t["ret"] <= 0]
    stats = {
        "n_trades": len(closed),
        "winrate": round(len(wins) / len(closed) * 100, 1) if closed else None,
        "avg_win": round(float(np.mean(wins)) * 100, 2) if wins else None,
        "avg_loss": round(float(np.mean(losses)) * 100, 2) if losses else None,
        "avg_hold": round(float(np.mean([t["hold_days"] for t in closed])), 0) if closed else None,
    }

    all_trades.sort(key=lambda t: t["entry_date"], reverse=True)
    items.sort(key=lambda x: ({"卖出": 0, "买入": 1, "预警(已破止损,信号仍在)": 2, "持有": 3, "观望": 4}
                              .get(x["action"], 9), x["code"]))
    return {
        "date": max((i["date"] for i in items), default=""),
        "capital": capital,
        "sleeve": round(sleeve, 0),
        "params": {"slow": SLOW_SPAN, "fast": FAST_SPAN, "omega": OMEGA,
                   "stop_p": STOP_P, "atr_span": ATR_SPAN, "risk_r": RISK_R},
        "items": items,
        "trades": all_trades,
        "stats": stats,
        "equity": equity,
        "perf": {"strat": _metrics(port, capital), "bh": _metrics(bh, capital),
                 "cost": round(float(cost_sum), 0), "years": round(len(port) / A_TRADING, 1)},
    }


def main():
    """命令行自检:打印当前状态、组合表现与历史交易。"""
    p = to_payload()
    print(f"=== ETF 趋势跟踪 American {SLOW_SPAN}/{FAST_SPAN} @ {p['date']} ===\n")
    print(pd.DataFrame(p["items"])[
        ["name", "price", "action", "size", "stop", "stop_gap", "pnl", "entry_date", "to_entry", "atr_pct"]
    ].to_string(index=False))
    print(f"\n组合 {p['perf']['strat']}\n持有 {p['perf']['bh']}\n统计 {p['stats']}")
    print(f"\n=== 历史交易 {len(p['trades'])} 笔 ===")
    print(pd.DataFrame(p["trades"])[
        ["name", "entry_date", "entry_px", "exit_date", "exit_px", "size", "hold_days", "ret"]
    ].to_string(index=False))


if __name__ == "__main__":
    main()
