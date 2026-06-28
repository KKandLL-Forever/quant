# lo mamaysky wang 2000 foundations TA

> 
> 
> ## 
> 
> 
> 
> 
> 
> ## 
> 
> 
> 
> ## 
> 
> 
> 
> **==> picture [328 x 19] intentionally omitted <==**
> 
> ## 
> 
> 
> 
> **==> picture [345 x 28] intentionally omitted <==**
> 
> **==> picture [305 x 16] intentionally omitted <==**
> 
> **==> picture [299 x 37] intentionally omitted <==**
> 
> 
> 
> ## 
> 
> **==> picture [310 x 21] intentionally omitted <==**
> 
> **==> picture [343 x 20] intentionally omitted <==**
> 
> **==> picture [315 x 15] intentionally omitted <==**
> 
> **==> picture [308 x 19] intentionally omitted <==**
> 
> 
> 
> **==> picture [370 x 36] intentionally omitted <==**
> 
> **==> picture [295 x 16] intentionally omitted <==**
> 
> ## 
> 
> **==> picture [320 x 16] intentionally omitted <==**
> 
> 
> 
> **==> picture [310 x 16] intentionally omitted <==**
> 
> **==> picture [295 x 18] intentionally omitted <==**
> 
> 
> 
> ## 
> 
> ## 
> 
> 
> 
> ## 
> 
> 
> 
> **==> picture [287 x 57] intentionally omitted <==**
> 
> **==> picture [78 x 9] intentionally omitted <==**
> 
> **==> picture [292 x 39] intentionally omitted <==**
> 
> **==> picture [363 x 40] intentionally omitted <==**
> 
> **==> picture [364 x 42] intentionally omitted <==**
> 
> 
> 
> **==> picture [186 x 36] intentionally omitted <==**
> 
> ## 
> 
> 
> 
> 
> 
> **==> picture [278 x 40] intentionally omitted <==**
> 
> 
> 
> ## 
> 
> ## 
> 
> 
> 
> ## 
> 
> ## 
> 
> **==> picture [11 x 9] intentionally omitted <==**
> 
> **==> picture [279 x 15] intentionally omitted <==**
> 
> **==> picture [370 x 19] intentionally omitted <==**
> 
> 
> 
> **==> picture [262 x 23] intentionally omitted <==**
> 
> **==> picture [359 x 28] intentionally omitted <==**
> 
> **==> picture [414 x 24] intentionally omitted <

## Methodology

**Core Trading Idea**  
The strategy is a pattern-based technical trading system that exploits the predictive power of 10 classical chart formations: Head-and-Shoulders (HS), Inverse Head-and-Shoulders (IHS), Broadening Top (BTOP), Broadening Bottom (BBOT), Triangle Top (TTOP), Triangle Bottom (TBOT), Rectangle Top (RTOP), Rectangle Bottom (RBOT), Double Top (DTOP), and Double Bottom (DBOT). The idea is that the completion of these patterns signals an imminent price reversal or continuation, and a one-day position in the direction implied by the pattern can capture statistically significant abnormal returns.

**Signal Generation Process**  
1. **Price Smoothing & Extrema Detection**  
   Daily closing prices of each stock are first smoothed using a nonparametric kernel regression (typically a Gaussian kernel with an optimized bandwidth) to remove high-frequency noise. From the smoothed series, local minima and maxima are identified sequentially.

2. **Pattern Recognition**  
   For each of the 10 patterns, a deterministic rule set based on the number, order, and relative magnitude of the extrema is applied. Examples of such rules (inferred from the technical analysis literature) include:  
   - *HS*: three peaks, the middle one being the highest, with the two troughs roughly at the same level (neckline).  
   - *IHS*: mirror image of HS (three troughs, middle lowest).  
   - *Double Top/Bottom*: two peaks (troughs) of similar height separated by a valley (peak).  
   - *Triangle/Rectangle Tops/Bottoms*: sequences of converging or parallel highs and lows that break out of the formation.  
   Each pattern requires a minimum number of extrema and specific ordering conditions, and sometimes a breakout beyond a trendline or neckline to signal completion.

3. **Signal Execution**  
   When a pattern is detected as completed (usually at the close of the day when the final extremum or neckline violation occurs), a trading signal is generated:  
   - **Long signal** for bullish reversal patterns: IHS, BBOT, TBOT, RBOT, DBOT.  
   - **Short signal** for bearish reversal patterns: HS, BTOP, TTOP, RTOP, DTOP.  
   The entry occurs at the closing price on the signal day, and the position is held for exactly one trading day (overnight), as the analysis focuses on **1-day conditional normalized returns**. No exit rules beyond the fixed holding period are applied; the position is closed at the following day’s close.

**Portfolio Construction & Rebalancing**  
The strategy is applied stock-by-stock; each stock’s pattern signals are treated independently. A practical implementation would either:  
- Construct a separate portfolio for each pattern type (e.g., all stocks that just triggered an IHS), or  
- Combine all signals in a single long/short portfolio where each signal receives an equal dollar position.  
Because the original analysis tests each pattern’s conditional return distribution uniqueness, a realistic trading portfolio would be equal-weighted across 

*(truncated)*

## Data Description

The text is heavily redacted, but visible table titles and a sample description allow extraction of the following data specifications.

### 1. Data Sources
- **Not explicitly stated** in the remaining text. Given the U.S. stock universe, daily frequency, and the time period (1962–1996), the source is almost certainly the **CRSP (Center for Research in Security Prices)** daily stock file, which is standard for such studies. No other databases (Compustat, IBES, etc.) are mentioned.

### 2. Asset Universe
- **All common stocks** listed on **NYSE/AMEX** and **NASDAQ**, with separate samples drawn from each exchange group.
- No industry or sector restrictions are indicated.

### 3. Time Period
- **Full sample:** January 1962 – December 1996 (35 years).
- The analysis is conducted over **seven non-overlapping 5‑year subperiods**:
  1. 1962–1966
  2. 1967–1971
  3. 1972–1976
  4. 1977–1981
  5. 1982–1986
  6. 1987–1991
  7. 1992–1996

### 4. Data Frequency
- **Daily** price data.
- The dependent variable is **1‑day normalized returns**. (Normalization method not specified in the visible text.)

### 5. Selection / Filter Criteria
- Stocks are sorted into **market‑capitalization quintiles** (Smallest, 2nd, 3rd, 4th, Largest) at the beginning of each subperiod or annually (details redacted).
- **Within each size‑quintile and each 5‑year subperiod**, exactly **10 stocks are randomly chosen** that meet the criterion:
  - **At least 80% non‑missing daily prices** during that subperiod.
- **Total per exchange sample:**  
  10 stocks × 5 quintiles × 7 subperiods = **350 stocks** for NYSE/AMEX, and **350 stocks** for NASDAQ.
- No explicit price‑level filter (e.g., >$5) or minimum market‑cap cutoff is mentioned beyond the quintile sorting.

### 6. Fundamental Data Fields
- **None.** The study uses only price data to identify technical chart patterns and compute returns. No accounting variables (Book‑to‑Market, earnings, etc.) are employed.

### 7. Alternative Data
- **None.** The paper focuses exclusively on technical indicators (head‑and‑shoulders, double tops/bottoms, etc.) derived from price series.

### 8. Benchmark
- **No market index** is used as a benchmark. The analysis compares:
  - **Unconditional 1‑day normalized return deciles** (from all stock‑day observations) with the empirical distribution of returns conditioned on a specific technical pattern.
- The null hypothesis is that the conditional returns fall uniformly across the unconditional deciles (10% in each).

## Signal Logic

The provided text consists solely of statistical tables and omitted image placeholders, with no explicit textual descriptions of trading rules, indicator parameters, or entry/exit logic. Consequently, precise extraction as requested is not possible. The paper appears to analyze the conditional distribution of returns following the automatic detection of classical chart patterns (head-and-shoulders, double tops, triangles, etc.) using kernel regression, but the exact pattern definitions, computational algorithms, and trading implementation details are contained in the omitted figures and surrounding prose.

**What can be inferred from the tables:**
- **Patterns studied:** Head-and-shoulders (HS), inverted head-and-shoulders (IHS), broadening top (BTOP), broadening bottom (BBOT), triangle top (TTOP), triangle bottom (TBOT), rectangle top (RTOP), rectangle bottom (RBOT), double top (DTOP), double bottom (DBOT).
- **Data:** Daily prices (Open, High, Low, Close implied) for NYSE/AMEX and NASDAQ stocks, 1962–1996, split into seven five-year subperiods. Stocks are size-quintile sorts.
- **Signal horizon:** Conditional 1-day normalized returns are analyzed (i.e., the return one day after pattern completion). This suggests a very short-term holding period (1 day) for evaluating predictive power.
- **Quantile analysis:** Returns conditional on a pattern are compared to unconditional return deciles. No explicit sorting or ranking of stocks is described beyond pattern occurrence.
- **No explicit thresholds:** The tables contain counts, moments, and goodness-of-fit statistics, but no entry/exit thresholds, stop-losses, or take-profits.

**Conclusion:** The necessary information to extract concrete trading rules is absent from the provided context. To derive rules, one would need the pattern recognition algorithms (likely involving local extrema and distance criteria) and any decision thresholds (e.g., buy when a pattern's predicted return exceeds a certain z-score). Without the omitted pictures and main text, a precise extraction is infeasible.

---
*Full text: 49,639 chars*
