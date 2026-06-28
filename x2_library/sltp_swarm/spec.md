# Optimal Stop-Loss and Take-Profit Parameterization for Autonomous Trading Agent Swarm

## Agent Swarm Stop-Loss/Take-Profit Calibration

**Type**: technical
**Asset Class**: crypto

This paper optimizes exit policies (stop-loss, trailing stop, partial take-profit, stale close, ATR-based levels, circuit breaker) for a swarm of autonomous crypto trading agents. Over 900 historical trades are replayed under alternative parameter sets to evaluate risk-adjusted performance. The study finds that tighter stop-losses (10%), early profit capture (10% partial take-profit, 75% closing), and volatility-scaled levels substantially improve Sharpe ratio compared to the baseline.

### Data Requirements

- **Frequency**: None
- **Data fields**: Price
- **Lookback**: None bars

### Indicators (1)

| ID | Name | Category | Formula | Scope |
|:---|:-----|:---------|:--------|:------|
| `average_true_range` | Average True Range (ATR) | technical | Average True Range over a lookback window (period not specified in the paper). T… | time_series |

### Logic Pipeline (8 steps)

step1. **custom** (time_series): Compute current Average True Range (ATR) value for the asset using the default lookback (assumed 14 periods). — `atr_value = indicator('average_true_range')`  
   → output: `atr_value` (scalar)
step2. **arithmetic** (time_series): Compute the effective stop-loss level: the tighter (higher) of a fixed 10% loss from entry and a volatility-scaled stop (entry - 1.0 * ATR). — `stop_level = min(entry_price * (1 - 0.10), entry_price - 1.0 * atr_value)`  
   → output: `stop_level` (scalar)
step3. **arithmetic** (time_series): Compute the partial take-profit trigger level: the closer (lower) of a fixed 10% gain from entry and a volatility-scaled target (entry + 2.0 * ATR). — `tp_level = min(entry_price * (1 + 0.10), entry_price + 2.0 * atr_value)`  
   → output: `tp_level` (scalar)
step4. **condition** (time_series): Check if the trailing stop activation condition is met (price has risen at least 3% from entry). — `trailing_active = (current_price >= entry_price * (1 + 0.03))`  
   → output: `trailing_active` (boolean)
step5. **custom** (time_series): Track the highest price observed since the trailing stop was activated. If not active, set to None. — `if trailing_active: highest_since_activation = max(highest_since_activation_prev, current_price) else: None`  
   → output: `highest_since_activation` (scalar)
step6. **arithmetic** (time_series): Compute the trailing stop level: 5% below the highest price since activation. Only valid if trailing_active is True. — `if trailing_active: trailing_stop = highest_since_activation * (1 - 0.05) else: None`  
   → output: `trailing_stop` (scalar)
step7. **condition** (time_series): Check all exit conditions: stop-loss hit, take-profit hit, trailing stop hit, or stale duration exceeded (48 hours). — `exit_signal = (current_price <= stop_level) or (current_price >= tp_level) or (trailing_active and current_price <= trailing_stop) or (trade_duration_hours >= 48). Note: take-profit trigger closes 75% of position (partial_fraction = 0.75); other triggers close the full remaining position.`  
   → output: `exit_signal` (boolean)
step8. **condition** (time_series): Generate final trade signal. An external, undisclosed agent swarm provides the buy signal; this pipeline only controls the exit. When an exit condition is true, output 'sell', otherwise 'hold' if a position is open, else 'buy' (assumed provided externally). For practical purposes, we output 'sell' or 'hold'. — `if exit_signal: trade_signal = 'sell' else: trade_signal = 'hold' (assuming position is already open; actual entry is handled by external black-box agents)`  
   → output: `trade_signal` (label)

### Execution (1 plans)

**exec_1**: Exit‑only overlay for autonomous trading agent swarm; monitors open positions and emits full‑close signal when any pre‑calibrated exit rule is hit.
- Trigger: signal_driven, None, delay=1 bar(s)
- Action: `WHEN trade_signal = 'sell': CLOSE ALL LONG (market order at next available price); WHEN trade_signal = 'hold': HOLD existing position; external 'buy' entries are not driven by this signal.`
- Sizing: signal_based, exposure=None, long_only

### Risk Management (7 rules)

- Fixed stop‑loss at 10% decline from entry price (entire position closed)
- Trailing stop activated when price rises ≥3% from entry; it follows highest price since activation with a 5% trailing distance below that peak, closing the position if hit
- Partial take‑profit at 10% gain from entry (75% of position closed, remainder stays subject to other exits)
- Stale‑close after 48 hours (position automatically closed if open >48 h)
- ATR‑based stop multiplier 1.0 (stop distance = 1.0 × ATR from entry, whichever is tighter than fixed 10% stop)
- ATR‑based take‑profit multiplier 2.0 (profit target = 2.0 × ATR from entry, whichever is lower than fixed 10% target)
- Circuit‑breaker: after 2 consecutive losing trades, all subsequent new positions are opened at 25% of normal size; resets after a win
