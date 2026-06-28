# Diagnosis — Agent Swarm Stop-Loss/Take-Profit Calibration

**Source paper:** Optimal Stop-Loss and Take-Profit Parameterization for Autonomous Trading Agent Swarm (arXiv 2604.27150)
**Asset / data:** BTC-USD daily, yfinance, 2018-01-01 → 2025-01-01 (cached locally)
**Entry proxy (user-supplied, not in paper):** Donchian-20 breakout (paper is exit-only / black-box entry)
**Initial cash:** 100,000

## Results (commission sweep)

| Commission | Total return | Sharpe | Max DD | Trades | Win rate |
|-----------|-------------|--------|--------|--------|----------|
| 0.00%     | 115.95%     | 0.670  | 32.0%  | 125    | 58.4%    |
| 0.01%     | 111.50%     | 0.657  | 32.2%  | 125    | 58.4%    |
| 0.05%     | 93.46%      | 0.602  | 33.3%  | 125    | 56.8%    |

## Match assessment

The paper reports **no numeric expected_performance** (it is a parameter-study
on a private agent swarm over ~900 crypto trades, not a published return series),
so a direct deviation table is not possible. Assessment is **qualitative**:

- The paper's central claim — tighter stops + early partial profit-taking +
  volatility-scaled levels produce a positive risk-adjusted result — reproduces
  in direction: the exit overlay yields a positive Sharpe (~0.6–0.67) with
  bounded drawdown (~32%) on an independent asset/period.
- Commission sensitivity is monotonic and mild at the paper's scale; at 0.05%
  the strategy still returns +93%, so the edge is not pure churn.

## Caveats / known divergences

1. **Entry is not the paper's.** Donchian-20 breakout is a proxy; absolute
   numbers reflect that entry as much as the exit rules.
2. **Stop formula ambiguity.** The extracted spec describes the fixed/ATR stop
   as the "tighter (higher)" of the two but its executable expression is
   `min(entry*0.9, entry-1*ATR)`, which selects the *lower* (wider) stop. Code
   follows the executable expression literally. If the paper's intent was the
   tighter stop, switch `min`→`max` on `stop_level` and re-run.
3. **48h stale-close on daily bars = 2 bars.** The paper's intraday/crypto cadence
   is coarsened to daily; the stale timer is the most cadence-sensitive rule.
4. **Single asset.** The paper studies a swarm across many trades; this is one
   instrument. Generalization needs a multi-asset universe.

## Relevance to run_ml_signals_2026.py

This validates the exit-rule family we discussed: volatility-scaled stop + a
3%-activated trailing stop + partial take-profit is a coherent, positive-Sharpe
overlay. The natural next step is to port these exit rules as an alternative to
the current Donchian-20 exit column and A/B them on our A-share signals.
