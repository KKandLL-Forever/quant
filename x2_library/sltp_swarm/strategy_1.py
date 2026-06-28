"""Agent Swarm Stop-Loss/Take-Profit Calibration — executable Backtrader backtest.

Source spec: x2_library/sltp_swarm/spec.json (paper: Optimal Stop-Loss and
Take-Profit Parameterization for Autonomous Trading Agent Swarm, arXiv 2604.27150).

The paper specifies an EXIT-ONLY overlay (fixed 10% stop, ATR-scaled stop/target,
3%-activated 5% trailing stop, 10% partial take-profit closing 75%, 48h stale
close, 2-consecutive-loss circuit breaker shrinking new size to 25%). The paper
treats entry as a black box; per user decision the entry is a Donchian-20
breakout proxy and the asset is BTC-USD daily (yfinance, locally cached).

Run: uv run python strategy_1.py
Outputs: results/metrics.json, results/portfolio_vs_assets.png/.csv,
results/key_pred/*.png/.csv
"""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import json
from pathlib import Path

import numpy as np
import pandas as pd
import backtrader as bt

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
RESULTS_DIR = HERE / "results"
KEY_PRED_DIR = RESULTS_DIR / "key_pred"
for _d in (DATA_DIR, RESULTS_DIR, KEY_PRED_DIR):
    _d.mkdir(parents=True, exist_ok=True)

BASE_SYMBOL = "BTC-USD"
INITIAL_CASH = 100_000.0
COMMISSION_RATES = (0.0, 0.0001, 0.0005)


def fetch_data_cached(symbol: str, start: str, end: str) -> pd.DataFrame:
    """Return OHLCV for symbol, cache-first then yfinance network fetch."""
    cache_path = DATA_DIR / f"{symbol}_{start}_{end}.csv"
    if cache_path.is_file():
        df = pd.read_csv(cache_path, index_col=0, parse_dates=True)
        if not df.empty:
            return df
    import yfinance as yf
    df = yf.download(
        symbol, start=start, end=end,
        auto_adjust=True, multi_level_index=False, progress=False,
    )
    if df is None or df.empty:
        raise ValueError(f"No data returned for {symbol} ({start}..{end})")
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df.to_csv(cache_path)
    return df


def to_feed(df: pd.DataFrame, name: str) -> bt.feeds.PandasData:
    """Build a tradable PandasData feed with finite OHLCV."""
    out = pd.DataFrame(index=pd.to_datetime(df.index).tz_localize(None))
    close = df["Close"].astype(float).ffill()
    out["Close"] = close
    out["Open"] = df["Open"].astype(float) if "Open" in df else close.shift(1).fillna(close.iloc[0])
    out["High"] = df["High"].astype(float) if "High" in df else pd.concat([out["Open"], close], axis=1).max(axis=1)
    out["Low"] = df["Low"].astype(float) if "Low" in df else pd.concat([out["Open"], close], axis=1).min(axis=1)
    out["Volume"] = df["Volume"].astype(float) if "Volume" in df else 1.0
    return bt.feeds.PandasData(dataname=out, name=name)


class MyStrategy(bt.Strategy):
    params = (
        ("atr_period", 14),
        ("don_period", 20),
        ("fixed_stop", 0.10),
        ("atr_stop_mult", 1.0),
        ("fixed_tp", 0.10),
        ("atr_tp_mult", 2.0),
        ("trail_activate", 0.03),
        ("trail_dist", 0.05),
        ("partial_fraction", 0.75),
        ("stale_bars", 2),
        ("cb_loss_streak", 2),
        ("cb_size_factor", 0.25),
    )

    def __init__(self):
        self.atr = bt.indicators.ATR(self.data, period=self.p.atr_period)
        self.don_high = bt.indicators.Highest(self.data.high, period=self.p.don_period)
        self.entry_price = None
        self.entry_bar = None
        self.highest_since_activation = None
        self.trailing_active = False
        self.tp_done = False
        self.consec_losses = 0

    def notify_trade(self, trade):
        if trade.isclosed:
            if trade.pnlcomm < 0:
                self.consec_losses += 1
            else:
                self.consec_losses = 0

    def _reset_trade_state(self):
        self.entry_price = None
        self.entry_bar = None
        self.highest_since_activation = None
        self.trailing_active = False
        self.tp_done = False

    def next(self):
        if len(self) <= self.p.don_period:
            return
        price = float(self.data.close[0])
        pos = self.position

        if not pos:
            if price > float(self.don_high[-1]):
                size_factor = self.p.cb_size_factor if self.consec_losses >= self.p.cb_loss_streak else 1.0
                cash = self.broker.getcash() * size_factor * 0.99
                size = cash / price
                if size > 0:
                    self.buy(size=size)
                    self.entry_price = price
                    self.entry_bar = len(self)
                    self.highest_since_activation = None
                    self.trailing_active = False
                    self.tp_done = False
            return

        entry = self.entry_price
        atr = float(self.atr[0])
        stop_level = min(entry * (1 - self.p.fixed_stop), entry - self.p.atr_stop_mult * atr)
        tp_level = min(entry * (1 + self.p.fixed_tp), entry + self.p.atr_tp_mult * atr)

        if not self.trailing_active and price >= entry * (1 + self.p.trail_activate):
            self.trailing_active = True
            self.highest_since_activation = price
        if self.trailing_active:
            self.highest_since_activation = max(self.highest_since_activation, price)
        trailing_stop = (self.highest_since_activation * (1 - self.p.trail_dist)
                         if self.trailing_active else None)

        stale = (len(self) - self.entry_bar) >= self.p.stale_bars

        if price <= stop_level:
            self.close()
            self._reset_trade_state()
            return
        if self.trailing_active and price <= trailing_stop:
            self.close()
            self._reset_trade_state()
            return
        if stale:
            self.close()
            self._reset_trade_state()
            return
        if not self.tp_done and price >= tp_level:
            self.sell(size=pos.size * self.p.partial_fraction)
            self.tp_done = True


def run_once(feed_df: pd.DataFrame, commission: float):
    """Run one backtest at a given commission. Returns (metrics, value_series)."""
    cerebro = bt.Cerebro()
    cerebro.adddata(to_feed(feed_df, BASE_SYMBOL))
    cerebro.addstrategy(MyStrategy)
    cerebro.broker.setcash(INITIAL_CASH)
    cerebro.broker.setcommission(commission=commission)

    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe",
                        riskfreerate=0.0, annualize=True)
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")
    cerebro.addanalyzer(bt.analyzers.Returns, _name="returns")
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")
    cerebro.addobserver(bt.observers.Value)

    results = cerebro.run()
    strat = results[0]

    _n = len(strat)
    value_series = pd.Series(
        list(strat.observers.value.get(size=_n)),
        index=[bt.num2date(x) for x in strat.data.datetime.get(size=_n)],
    )
    value_series = value_series[np.isfinite(value_series.values)]

    final_value = float(cerebro.broker.getvalue())
    if not np.isfinite(final_value):
        raise ValueError("Non-finite final portfolio value — strategy is broken")

    sharpe = strat.analyzers.sharpe.get_analysis().get("sharperatio")
    dd = strat.analyzers.drawdown.get_analysis()
    trades = strat.analyzers.trades.get_analysis()
    metrics = {
        "commission": commission,
        "final_value": round(final_value, 2),
        "return_value": round(final_value, 2),
        "total_return": round((final_value / INITIAL_CASH - 1) * 100, 2),
        "sharpe_ratio": round(sharpe, 4) if sharpe is not None else None,
        "max_drawdown_pct": round(dd.get("max", {}).get("drawdown", 0.0), 2),
        "num_trades": trades.get("total", {}).get("closed", 0),
        "won_trades": trades.get("won", {}).get("total", 0),
        "lost_trades": trades.get("lost", {}).get("total", 0),
    }
    return metrics, value_series


def plot_portfolio_vs_assets(value_by_commission, asset_prices: dict) -> None:
    """One image: 3 commission portfolio curves + every asset buy-and-hold."""
    fig, ax = plt.subplots(figsize=(12, 7))
    combined = pd.DataFrame()
    for sym, prices in asset_prices.items():
        s = prices.astype(float).ffill()
        bh = INITIAL_CASH * (s / s.iloc[0])
        is_base = sym.upper() == BASE_SYMBOL.upper()
        ax.plot(bh.index, bh.values, label=f"{sym} (B&H)",
                linewidth=2.6 if is_base else 1.0,
                alpha=1.0 if is_base else 0.7)
        combined[f"{sym}_bh"] = bh
    for comm, vseries in value_by_commission.items():
        ax.plot(vseries.index, vseries.values,
                label=f"Portfolio @ {comm*100:.2f}% comm", linewidth=2.6)
        combined[f"portfolio_comm_{comm}"] = vseries
    ax.set_title("Portfolio vs Same-Capital Buy-and-Hold (BTC + portfolio boldface)")
    ax.set_xlabel("Date")
    ax.set_ylabel("Account value")
    ax.legend(loc="best", fontsize=8)
    fig.tight_layout()
    fig.savefig(RESULTS_DIR / "portfolio_vs_assets.png", dpi=120)
    plt.close(fig)
    combined.to_csv(RESULTS_DIR / "portfolio_vs_assets.csv")


def plot_key_factor(name: str, series: pd.Series) -> None:
    """One CSV + PNG per key observable factor, under results/key_pred/."""
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(series.index, series.values, linewidth=1.2)
    ax.set_title(f"Key factor: {name}")
    fig.tight_layout()
    fig.savefig(KEY_PRED_DIR / f"{name}.png", dpi=120)
    plt.close(fig)
    series.to_frame(name).to_csv(KEY_PRED_DIR / f"{name}.csv")


def main() -> None:
    start, end = "2018-01-01", "2025-01-01"
    raw = fetch_data_cached(BASE_SYMBOL, start, end)
    asset_prices = {BASE_SYMBOL: raw["Close"]}

    value_by_commission = {}
    all_metrics = []
    for comm in COMMISSION_RATES:
        metrics, vseries = run_once(raw, comm)
        value_by_commission[comm] = vseries
        all_metrics.append(metrics)

    plot_portfolio_vs_assets(value_by_commission, asset_prices)

    close = raw["Close"].astype(float)
    high = raw["High"].astype(float)
    low = raw["Low"].astype(float)
    tr = pd.concat([high - low,
                    (high - close.shift()).abs(),
                    (low - close.shift()).abs()], axis=1).max(axis=1)
    atr14 = tr.rolling(14).mean()
    don20 = high.rolling(20).max()
    plot_key_factor("ATR_14", atr14.dropna())
    plot_key_factor("Donchian_20_high", don20.dropna())

    (RESULTS_DIR / "metrics.json").write_text(json.dumps(all_metrics, indent=2))
    print(json.dumps(all_metrics, indent=2))


if __name__ == "__main__":
    main()
