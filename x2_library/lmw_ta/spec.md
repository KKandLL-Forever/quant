# lo mamaysky wang 2000 foundations TA

## Technical Chart Pattern Trading Strategy (Foundations of Technical Analysis)

**Type**: technical
**Asset Class**: equity

The strategy exploits the predictive power of 10 classical chart formations (e.g., Head-and-Shoulders, Double Tops/Bottoms, Triangles). When a pattern is completed as determined by a nonparametric kernel regression of daily closing prices and an extremum-based recognition algorithm, a one-day position is taken in the direction implied by the pattern (long for bullish reversals, short for bearish). The aim is to capture statistically significant conditional 1-day normalized returns.

### Data Requirements

- **Source**: CRSP (inferred)
- **Period**: 1962-1996
- **Frequency**: daily
- **Universe**: NYSE/AMEX common stocks, NASDAQ common stocks
- **Filters**: All common stocks on NYSE/AMEX and NASDAQ; sorted into market-cap quintiles at the beginning of each 5-year subperiod; within each quintile-subperiod combination, exactly 10 stocks are randomly selected that have at least 80% non-missing daily prices during that subperiod.
- **Data fields**: Price
- **Lookback**: None bars

### Indicators (11)

| ID | Name | Category | Formula | Scope |
|:---|:-----|:---------|:--------|:------|
| `smoothed_close` | Smoothed Daily Closing Price | technical | Apply nonparametric kernel regression to the daily closing price series. A Gauss… | time_series |
| `hs_top_signal` | Head-and-Shoulders Top Pattern Completion | technical | Identify local extrema from the smoothed close series. A Head-and-Shoulders Top … | time_series |
| `ihs_bottom_signal` | Inverse Head-and-Shoulders Bottom Pattern Completion | technical | Mirror image of HS: three troughs with the middle (head) lowest, two peaks formi… | time_series |
| `btop_signal` | Broadening Top Pattern Completion | technical | Identified by a sequence of alternating peaks and troughs that diverge (broaden)… | time_series |
| `bbot_signal` | Broadening Bottom Pattern Completion | technical | Diverging formation with lower lows and higher highs. Completion on a close abov… | time_series |
| `ttop_signal` | Triangle Top Pattern Completion | technical | A converging price pattern with lower highs and higher lows. Top triangle (beari… | time_series |
| `tbot_signal` | Triangle Bottom Pattern Completion | technical | Converging triangle with lower highs and higher lows. Bottom triangle (bullish) … | time_series |
| `rtop_signal` | Rectangle Top Pattern Completion | technical | A horizontal trading range with parallel support and resistance. Top rectangle (… | time_series |
| `rbot_signal` | Rectangle Bottom Pattern Completion | technical | Horizontal range; bullish completion on a close above resistance. Signal true on… | time_series |
| `dtop_signal` | Double Top Pattern Completion | technical | Two peaks of similar height separated by a trough. Completion on a close below t… | time_series |
| `dbot_signal` | Double Bottom Pattern Completion | technical | Two troughs of similar depth separated by a peak. Completion on a close above th… | time_series |

### Logic Pipeline (3 steps)

step1. **condition** (time_series): Aggregate all bullish reversal pattern completion signals (IHS, BBOT, TBOT, RBOT, DBOT) into a single composite bullish flag. — `bullish_flag = ihs_bottom_signal OR bbot_signal OR tbot_signal OR rbot_signal OR dbot_signal`  
   → output: `bullish_flag` (boolean)
step2. **condition** (time_series): Aggregate all bearish reversal pattern completion signals (HS, BTOP, TTOP, RTOP, DTOP) into a single composite bearish flag. — `bearish_flag = hs_top_signal OR btop_signal OR ttop_signal OR rtop_signal OR dtop_signal`  
   → output: `bearish_flag` (boolean)
step3. **condition** (time_series): Generate final trade signal: long if only bullish patterns triggered, short if only bearish patterns triggered, otherwise hold/neutral. — `IF bullish_flag = True AND bearish_flag = False THEN trade_signal = 'long' ELSE IF bearish_flag = True AND bullish_flag = False THEN trade_signal = 'short' ELSE trade_signal = 'hold'`  
   → output: `trade_signal` (label)

### Execution (1 plans)

**exec_1**: Technical chart pattern trading strategy: daily signal generation from 10 classical patterns aggregated into a single long/short/hold signal, executed at next close with a 1-day holding period and equal-weighted positions across all active signals
- Trigger: signal_driven, daily, delay=1 bar(s)
- Action: `WHEN 'long': LONG; WHEN 'short': SHORT`
- Sizing: equal_weight, exposure=None, long_short

### Risk Management (1 rules)

- No explicit stop-loss, position limits, drawdown constraints, or other risk management rules are mentioned in the paper.
