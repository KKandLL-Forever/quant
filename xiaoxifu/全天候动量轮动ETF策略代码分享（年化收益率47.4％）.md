---
title: "全天候动量轮动ETF策略代码分享（年化收益率47.4％）"
source: "https://mp.weixin.qq.com/s/aDVZfjGU4HwszkjGba583g"
author:
  - "[[MatrixSpk]]"
published:
created: 2026-07-01
description: "干脆开源吧"
tags:
  - "clippings"
---
MatrixSpk 小西西弗的量化之路 *2025年12月23日 14:55*

## 全天候动量轮动ETF策略表现

![图片](https://mmbiz.qpic.cn/sz_mmbiz_jpg/rEUglkbjrwdSw7icMI26zz6rD2UcUxicgj4ickta3NTlibicmVGlX6jp94TObqQpNFUGZe3Fmia0ib29zjunoMumiaEdNQ/640?wx_fmt=jpeg&watermark=1&tp=webp&wxfrom=5&wx_lazy=1#imgIndex=0) ![图片](https://mmbiz.qpic.cn/sz_mmbiz_jpg/rEUglkbjrwdSw7icMI26zz6rD2UcUxicgjLM79JyvrkqssPtQjK9V7icEkQUSWKM0GbsS0js8gmNHh2sibBqWxud4Q/640?wx_fmt=jpeg&watermark=1&tp=webp&wxfrom=5&wx_lazy=1#imgIndex=2)

---

## 全天候动量轮动ETF策略设计思路

## 1\. 标的资产选择逻辑：构建跨市场、跨资产类别的配置组合

**沪深300ETF（510300.SS）**

- **代表中国A股核心资产** ：沪深300指数涵盖A股市场市值最大、流动性最好的300只股票，反映中国宏观经济和股票市场整体走势
- **基准作用** ：作为策略绩效对比的基准，衡量策略能否跑赢市场平均水平
- **市值风格暴露** ：主要配置大盘蓝筹股，提供稳定的市场贝塔收益

**纳指ETF（513100.SS）**

- **代表全球科技成长板块** ：跟踪纳斯达克100指数，涵盖苹果、微软、亚马逊等全球科技巨头
- **分散化价值** ：与A股市场的相关性相对较低，提供跨市场配置机会
- **成长因子暴露** ：科技股通常具有更高的成长性和波动性，为动量策略提供潜在的高收益机会

**黄金ETF（518880.SS）**

- **避险资产与通胀对冲工具** ：黄金具有避险属性，通常在市场动荡时期表现较好
- **负相关性资产** ：与股票资产往往呈现负相关或低相关性，有助于降低组合整体波动
- **货币属性** ：反映美元走势和全球流动性状况，提供额外的配置维度

**组合设计理念** ：这三个ETF分别代表国内股票、海外科技股票和大宗商品三类不同资产，具有良好的分散化效果。历史数据显示这三类资产的相关性较低，能够在不同市场环境下相互对冲，为动量轮动策略提供了良好的基础。

### 2\. 动量因子选择与计算逻辑

**理论基础** ：动量效应是金融市场上最稳健的异象之一，由Jegadeesh和Titman（1993）首次系统论证。其核心逻辑是"趋势延续"——过去表现较好的资产在未来短期内继续表现较好的概率较高。

**20日动量窗口设计** ：

- **短期趋势捕捉** ：20个交易日（约1个月）的窗口能够有效捕捉中期趋势，避免长期趋势的滞后性和短期噪声干扰
- **平衡敏感性与稳定性** ：较10日窗口更稳定，较60日窗口更敏感，适合A股的交易节奏
- **实践验证** ：在A股市场中，20-60日的动量效应较为显著

**动量计算方式** ：使用简单算术平均而非几何平均，更敏感地反映近期价格变化。每个交易日收盘后计算过去20日的平均日收益率，作为该ETF的动量得分。

### 3\. 波动率调整的必要性与方法

**问题意识** ：未经调整的动量指标存在严重缺陷——高收益可能源自高波动而非真正的趋势质量。例如，某个ETF可能因为单日暴涨而具有很高的动量值，但这种高波动性的"趋势"往往不可持续且风险较大。

**风险调整动量** ：

- **经济学原理** ：借鉴夏普比率的思想，将收益与风险（波动率）相结合，计算单位风险带来的收益
- **计算方法** ：动量值 ÷ 波动率（标准差），这实质上是短期夏普比率的近似
- **核心优势** ：
1. **公平比较** ：使不同波动特性的资产可以在同一标准下比较
	2. **风险控制** ：自动降低高波动资产的权重，避免组合过度暴露于高风险资产
	3. **稳定性提升** ：筛选出的标的既有上涨潜力又有相对稳定的表现

**波动率计算细节** ：使用20日收益率的方差（波动率平方），取其平方根得到标准差。这种滚动窗口计算能够动态反映资产近期的风险特征。

### 4\. 权重分配机制设计

**正动量筛选** ：

- **顺势而为原则** ：只选择调整动量为正的资产，避免逆势操作
- **空仓机制** ：当所有资产调整动量均为负时，权重合计为0，策略持有现金，这实质上是市场的择时机制

**归一化加权** ：

- **资金充分使用** ：将100%的仓位分配给符合条件的资产，提高资金使用效率
- **动量强度反映** ：权重与调整动量值成正比，趋势越强的资产获得越高权重
- **自动再平衡** ：每日根据新信号调整权重，保持策略与市场状态的同步

**T+1交易制度** ：

- **现实可行性** ：使用T日收盘后计算的数据，在T+1日执行交易，符合A股实际交易规则
- **价格近似** ：用T+1日收盘价近似代替"最高最低平均价"，简化回测同时保持合理性

### 5\. 策略的哲学基础与预期效果

**核心投资哲学** ：

1. **趋势跟踪** ：承认市场趋势的存在并尝试从中获利
2. **风险调整收益** ：不仅追求收益，更关注收益的质量和稳定性
3. **资产分散** ：通过多资产配置降低单一市场风险
4. **系统化执行** ：避免主观情绪干扰，严格遵循量化规则

**预期优势** ：

- **穿越牛熊** ：通过多资产轮动，在不同市场环境下都能找到表现较好的资产
- **风险控制** ：波动率调整和正动量筛选提供双重风险控制
- **适应性** ：权重每日更新，策略能够快速适应市场变化
- **透明可复制** ：完全规则化的策略，结果可验证、过程可复制

**潜在风险与局限** ：

- **动量崩溃风险** ：在市场急剧反转时，动量策略可能遭受较大损失
- **交易成本** ：高频调仓可能产生显著的交易成本（回测中未考虑）
- **参数敏感性** ：20日窗口和正动量阈值等参数需要定期检验和优化
- **流动性假设** ：假设可以按收盘价交易，实际中大宗交易可能影响成交价格

这个策略体现了现代量化投资的核心思想：通过系统化的方法，在控制风险的前提下，捕捉市场的统计规律。它不试图预测市场方向，而是设计一套规则来响应市场变化，力求在不同市场环境下都能获得稳健的风险调整后收益。

---

全天候动量轮动ETF策略代码

### 接下来分享实现上述策略的代码，本部分包括三块内容：代码功能说明、注意事项和代码。

### 代码功能说明

1. **数据获取与处理** ：使用 `quantmod` 包从Yahoo Finance获取三大ETF的历史数据。
2. **指标计算** ：
- 计算20日平均日收益率作为动量
	- 计算20日方差作为波动率
	- 计算调整动量（动量/波动率）
4. **权重分配** ：每个交易日筛选调整动量>0的标的，按调整动量大小归一化分配权重
5. **回测逻辑** ：使用T日计算的权重，在T+1日以平均价（收盘价近似）执行交易
6. **绩效分析** ：计算年化收益率、波动率、夏普比率、最大回撤等关键指标
7. **可视化输出** ：生成三种对比图表

### 注意事项

1. **ETF代码** ：程序中使用的ETF代码是基于常识的常见代码，如果与实际不符，请修改 `etf_symbols` 变量
2. **数据源** ：Yahoo Finance数据有时可能不完整，如果获取失败可以尝试其他数据源
3. **交易成本** ：此回测未考虑交易成本，实际收益会略低
4. **执行价格** ：实际交易中T+1日的买入价格使用最高最低平均价，这里用收盘价近似

在 `R` 软件中运行此代码后，你将得到完整的回测结果，包括绩效指标、每日权重分配和对比图表。所有结果也会自动保存到 `momentum_strategy_backtest` 文件夹中。

R 语言代码

```bash
# 加载必要的R包library(quantmod) # 金融数据获取与分析library(PerformanceAnalytics) # 投资组合绩效分析library(ggplot2) # 高级绘图library(dplyr) # 数据处理library(tidyr) # 数据整理library(scales) # 图形标度调整library(patchwork) # 图形组合library(cowplot) # 图形组合（更精细的控制）
# 1. 数据获取与预处理# 设置回测时间段 - 确保使用正确的日期格式backtest_start <- "2024-01-01"backtest_end <- Sys.Date()
# 转换为日期类型start_date <- as.Date(backtest_start)end_date <- as.Date(backtest_end)
# 定义ETF代码 (基于常识的常见代码，Yahoo Finance格式)# 沪市纳指ETF: 513100.SS# 沪深300ETF: 510300.SS# 黄金ETF: 518880.SSetf_symbols <- c("513100.SS", "510300.SS", "518880.SS")etf_names <- c("纳指ETF", "沪深300ETF", "黄金ETF")
# 从Yahoo Finance获取数据cat("正在从Yahoo Finance获取数据...\n")getSymbols(etf_symbols,  from = start_date,  to = end_date,  src = "yahoo",  auto.assign = TRUE)
# 提取调整后收盘价（已考虑分红配股）prices <- list()for (i in 1:length(etf_symbols)) {  symbol_data <- get(etf_symbols[i])  # 使用调整后收盘价  prices[[i]] <- Ad(symbol_data)}names(prices) <- etf_names
# 合并所有价格数据price_df <- do.call(merge, prices)colnames(price_df) <- etf_names
# 2. 计算指标：动量、波动率、调整动量calculate_metrics <- function(price_series, window = 20) {  # 计算日收益率  returns <- dailyReturn(price_series, type = "arithmetic")  colnames(returns) <- "Return"
  # 计算20日平均收益率（动量）  momentum <- rollapply(returns, width = window, FUN = mean, align = "right", fill = NA)  colnames(momentum) <- "Momentum"
  # 计算20日收益率方差（波动率）  volatility <- rollapply(returns, width = window, FUN = var, align = "right", fill = NA)  colnames(volatility) <- "Volatility"
  # 计算调整动量（动量/波动率）  # 注意：使用标准差进行标准化（方差的平方根）  adj_momentum <- momentum / sqrt(volatility)  colnames(adj_momentum) <- "Adj_Momentum"
  return(list(    returns = returns,    momentum = momentum,    volatility = volatility,    adj_momentum = adj_momentum  ))}
# 为每个ETF计算指标cat("正在计算动量指标...\n")metrics_list <- list()for (etf in etf_names) {  metrics_list[[etf]] <- calculate_metrics(price_df[, etf])}
# 3. 生成交易信号和权重generate_weights <- function(adj_momentum_df) {  # 初始化权重数据框  weights_df <- as.data.frame(adj_momentum_df)  weights_df[] <- 0 # 所有值初始化为0
  # 对每一行（每个交易日）计算权重  for (i in 1:nrow(adj_momentum_df)) {    # 获取当日的调整动量值    current_momentum <- as.numeric(adj_momentum_df[i, ])
    # 筛选调整动量大于0的标的    positive_idx <- which(current_momentum > 0 & !is.na(current_momentum))
    if (length(positive_idx) > 0) {      # 获取正的调整动量值      positive_momentum <- current_momentum[positive_idx]
      # 按调整动量大小归一化计算权重      normalized_weights <- positive_momentum / sum(positive_momentum)
      # 分配权重      weights_df[i, positive_idx] <- normalized_weights    }  }
  # 转换为时间序列  weights_xts <- xts(weights_df, order.by = index(adj_momentum_df))  colnames(weights_xts) <- colnames(adj_momentum_df)
  return(weights_xts)}
# 提取所有ETF的调整动量adj_momentum_all <- do.call(merge, lapply(metrics_list, function(x) x$adj_momentum))colnames(adj_momentum_all) <- etf_names
# 生成权重序列cat("正在计算每日权重...\n")weights <- generate_weights(adj_momentum_all)
# 4. 将权重数据整理到数据框中# 创建每日权重数据框（包含日期）daily_weights_df <- data.frame(Date = index(weights))for (etf in etf_names) {  daily_weights_df[[etf]] <- as.numeric(weights[, etf])}
# 计算每个交易日的权重合计（应为1或0）daily_weights_df$权重合计 <- rowSums(daily_weights_df[, etf_names], na.rm = TRUE)
# 创建每个ETF的单独权重数据框etf_weight_dfs <- list()for (etf in etf_names) {  etf_weight_dfs[[etf]] <- data.frame(    Date = index(weights),    ETF = etf,    Weight = as.numeric(weights[, etf])  )}
# 合并所有ETF权重数据框all_etf_weights_df <- do.call(rbind, etf_weight_dfs)
# 5. 回测计算# 计算每个ETF的日收益率returns_all <- do.call(merge, lapply(metrics_list, function(x) x$returns))colnames(returns_all) <- etf_names
# 调整权重的时间索引，确保与收益率对齐# 使用T日的权重在T+1日交易lagged_weights <- lag(weights, 1) # 权重滞后一天lagged_weights <- lagged_weights[index(returns_all)] # 对齐索引
# 处理缺失值lagged_weights[is.na(lagged_weights)] <- 0
# 计算策略日收益率（按T+1日开盘价计算，这里用平均价近似）strategy_returns <- rowSums(returns_all * lagged_weights, na.rm = TRUE)strategy_returns <- xts(strategy_returns, order.by = index(returns_all))colnames(strategy_returns) <- "动量策略"
# 6. 绩效指标计算cat("正在计算绩效指标...\n")# 策略绩效strategy_perf <- table.AnnualizedReturns(strategy_returns)strategy_dd <- table.DownsideRisk(strategy_returns)max_dd <- maxDrawdown(strategy_returns)strategy_calmar <- CalmarRatio(strategy_returns)strategy_sortino <- SortinoRatio(strategy_returns)strategy_positive_returns <- sum(strategy_returns > 0, na.rm = TRUE)strategy_total_returns <- sum(!is.na(strategy_returns))strategy_win_rate <- strategy_positive_returns / strategy_total_returns
# 基准绩效（沪深300ETF）benchmark_returns <- returns_all[, "沪深300ETF"]colnames(benchmark_returns) <- "沪深300ETF"benchmark_perf <- table.AnnualizedReturns(benchmark_returns)benchmark_dd <- table.DownsideRisk(benchmark_returns)benchmark_max_dd <- maxDrawdown(benchmark_returns)benchmark_calmar <- CalmarRatio(benchmark_returns)benchmark_sortino <- SortinoRatio(benchmark_returns)benchmark_positive_returns <- sum(benchmark_returns > 0, na.rm = TRUE)benchmark_total_returns <- sum(!is.na(benchmark_returns))benchmark_win_rate <- benchmark_positive_returns / benchmark_total_returns
# 等权重组合绩效equal_weights <- matrix(1 / 3, nrow = nrow(returns_all), ncol = ncol(returns_all))equal_weights <- xts(equal_weights, order.by = index(returns_all))colnames(equal_weights) <- etf_namesequal_returns <- rowSums(returns_all * equal_weights, na.rm = TRUE)equal_returns <- xts(equal_returns, order.by = index(returns_all))colnames(equal_returns) <- "等权重组合"equal_perf <- table.AnnualizedReturns(equal_returns)equal_max_dd <- maxDrawdown(equal_returns)equal_calmar <- CalmarRatio(equal_returns)equal_sortino <- SortinoRatio(equal_returns)equal_positive_returns <- sum(equal_returns > 0, na.rm = TRUE)equal_total_returns <- sum(!is.na(equal_returns))equal_win_rate <- equal_positive_returns / equal_total_returns
# 7. 创建绩效指标汇总数据框# 准备回测期间字符串backtest_period <- paste0(  format(as.Date(backtest_start), "%Y-%m-%d"),  " 至 ",  format(end_date, "%Y-%m-%d"))
performance_metrics_df <- data.frame(  策略 = c("动量策略", "沪深300ETF", "等权重组合"),  年化收益率 = c(    round(strategy_perf[1, 1] * 100, 2),    round(benchmark_perf[1, 1] * 100, 2),    round(equal_perf[1, 1] * 100, 2)  ),  年化波动率 = c(    round(strategy_perf[2, 1] * 100, 2),    round(benchmark_perf[2, 1] * 100, 2),    round(equal_perf[2, 1] * 100, 2)  ),  夏普比率 = c(    round(strategy_perf[3, 1], 3),    round(benchmark_perf[3, 1], 3),    round(equal_perf[3, 1], 3)  ),  最大回撤 = c(    round(max_dd * 100, 2),    round(benchmark_max_dd * 100, 2),    round(equal_max_dd * 100, 2)  ),  卡尔马比率 = c(    round(strategy_calmar, 3),    round(benchmark_calmar, 3),    round(equal_calmar, 3)  ),  索提诺比率 = c(    round(strategy_sortino, 3),    round(benchmark_sortino, 3),    round(equal_sortino, 3)  ),  胜率 = c(    round(strategy_win_rate * 100, 2),    round(benchmark_win_rate * 100, 2),    round(equal_win_rate * 100, 2)  ),  正收益天数 = c(    strategy_positive_returns,    benchmark_positive_returns,    equal_positive_returns  ),  总交易天数 = c(    strategy_total_returns,    benchmark_total_returns,    equal_total_returns  ),  回测期间 = c(backtest_period, backtest_period, backtest_period))
# 8. 绩效指标输出cat("==================== 绩效指标汇总数据框 ====================\n\n")print(performance_metrics_df)cat("\n")
# 9. 输出权重数据cat("==================== 每日组合权重数据框 ====================\n\n")cat("每日组合权重数据框（前10行）:\n")print(head(daily_weights_df, 10))cat("\n")
cat("每日组合权重数据框（后10行）:\n")print(tail(daily_weights_df, 10))cat("\n")
cat("组合权重统计信息:\n")cat("总交易日数:", nrow(daily_weights_df), "\n")cat("有权重分配的交易日数:", sum(daily_weights_df$权重合计 > 0), "\n")cat("无权重分配的交易日数:", sum(daily_weights_df$权重合计 == 0), "\n\n")
# 计算每个ETF的权重统计cat("各ETF权重统计:\n")for (etf in etf_names) {  etf_weights <- daily_weights_df[[etf]]  cat(etf, ":\n")  cat("  平均权重:", round(mean(etf_weights) * 100, 2), "%\n")  cat("  最大权重:", round(max(etf_weights) * 100, 2), "%\n")  cat("  最小权重:", round(min(etf_weights) * 100, 2), "%\n")  cat("  正权重天数:", sum(etf_weights > 0), "\n")  cat("  零权重天数:", sum(etf_weights == 0), "\n\n")}
# 10. 输出每个ETF的权重数据框cat("==================== 每个ETF的权重数据框 ====================\n\n")for (etf in etf_names) {  cat(etf, "权重数据框（前5行）:\n")  etf_df <- etf_weight_dfs[[etf]]  print(head(etf_df, 5))  cat("\n")}
# 11. 绘制图表cat("正在生成图表...\n")
# 准备累计收益率数据cumulative_returns <- merge(  cumprod(1 + na.fill(strategy_returns, 0)) - 1,  cumprod(1 + na.fill(benchmark_returns, 0)) - 1,  cumprod(1 + na.fill(equal_returns, 0)) - 1)colnames(cumulative_returns) <- c("动量策略", "沪深300ETF", "等权重组合")
# 转换为数据框用于ggplotcumulative_df <- data.frame(  Date = index(cumulative_returns),  as.data.frame(cumulative_returns))
# 计算最大回撤数据calculate_drawdown <- function(returns) {  cum_returns <- cumprod(1 + na.fill(returns, 0))  drawdown <- cum_returns / cummax(cum_returns) - 1  return(drawdown)}
# 计算各策略的回撤drawdowns <- merge(  calculate_drawdown(strategy_returns),  calculate_drawdown(benchmark_returns),  calculate_drawdown(equal_returns))colnames(drawdowns) <- c("动量策略", "沪深300ETF", "等权重组合")
# 转换为数据框用于ggplotdrawdown_df <- data.frame(  Date = index(drawdowns),  as.data.frame(drawdowns))
# 定义颜色方案colors_strategies <- c(  "动量策略" = "#E41A1C", # 红色  "沪深300ETF" = "#377EB8", # 蓝色  "等权重组合" = "#4DAF4A" # 绿色)
# 设置面积图的透明度area_alpha <- 0.3
# ============================================================# 图表1: 动量策略 vs 沪深300ETF 组合图# ============================================================# 筛选数据vs_benchmark_cumulative <- cumulative_df %>%  select(Date, 动量策略, 沪深300ETF) %>%  pivot_longer(cols = -Date, names_to = "策略", values_to = "累计收益率")
vs_benchmark_drawdown <- drawdown_df %>%  select(Date, 动量策略, 沪深300ETF) %>%  pivot_longer(cols = -Date, names_to = "策略", values_to = "回撤")
# 累计收益率图（隐藏X轴，图例放在左上角，上下排列）p1_cumulative <- ggplot(  vs_benchmark_cumulative,  aes(x = Date, y = 累计收益率, color = 策略)) +  geom_line(size = 1.2) +  labs(    title = "动量策略 vs 沪深300ETF: 累计收益率",    x = "", y = "累计收益率"  ) +  scale_y_continuous(labels = percent_format()) +  scale_color_manual(values = colors_strategies[c("动量策略", "沪深300ETF")]) +  theme_minimal() +  theme(    legend.position = c(0.02, 0.98), # 图例在左上角    legend.justification = c(0, 1), # 对齐到左上角    legend.direction = "vertical", # 图例上下排列    legend.title = element_blank(),    legend.background = element_rect(fill = "white", color = "gray", size = 0.3),    legend.box.margin = margin(5, 5, 5, 5), # 图例内边距    legend.spacing.y = unit(0.2, "cm"), # 图例项之间的垂直间距    legend.text = element_text(size = 10),    plot.title = element_text(hjust = 0.5, size = 14, face = "bold"),    axis.title.x = element_blank(),    axis.text.x = element_blank(),    axis.ticks.x = element_blank()  )
# 最大回撤面积图（隐藏标题和X轴，删除图例）p1_drawdown <- ggplot(  vs_benchmark_drawdown,  aes(x = Date, y = 回撤, fill = 策略)) +  geom_area(position = "identity", alpha = area_alpha) + # 使用面积图  labs(    title = "",    x = "日期", y = "回撤"  ) +  scale_y_continuous(labels = percent_format()) +  scale_fill_manual(values = colors_strategies[c("动量策略", "沪深300ETF")]) +  theme_minimal() +  theme(    legend.position = "none", # 删除图例    plot.title = element_blank(),    axis.title.x = element_text(size = 12)  )
# 使用cowplot组合图表p1_combined <- plot_grid(p1_cumulative, p1_drawdown,  ncol = 1, align = "v",  rel_heights = c(1, 0.9))
# ============================================================# 图表2: 动量策略 vs 等权重组合 组合图# ============================================================# 筛选数据vs_equal_cumulative <- cumulative_df %>%  select(Date, 动量策略, 等权重组合) %>%  pivot_longer(cols = -Date, names_to = "策略", values_to = "累计收益率")
vs_equal_drawdown <- drawdown_df %>%  select(Date, 动量策略, 等权重组合) %>%  pivot_longer(cols = -Date, names_to = "策略", values_to = "回撤")
# 累计收益率图（隐藏X轴，图例放在左上角，上下排列）p2_cumulative <- ggplot(  vs_equal_cumulative,  aes(x = Date, y = 累计收益率, color = 策略)) +  geom_line(size = 1.2) +  labs(    title = "动量策略 vs 等权重组合: 累计收益率",    x = "", y = "累计收益率"  ) +  scale_y_continuous(labels = percent_format()) +  scale_color_manual(values = colors_strategies[c("动量策略", "等权重组合")]) +  theme_minimal() +  theme(    legend.position = c(0.02, 0.98), # 图例在左上角    legend.justification = c(0, 1), # 对齐到左上角    legend.direction = "vertical", # 图例上下排列    legend.title = element_blank(),    legend.background = element_rect(fill = "white", color = "gray", size = 0.3),    legend.box.margin = margin(5, 5, 5, 5), # 图例内边距    legend.spacing.y = unit(0.2, "cm"), # 图例项之间的垂直间距    legend.text = element_text(size = 10),    plot.title = element_text(hjust = 0.5, size = 14, face = "bold"),    axis.title.x = element_blank(),    axis.text.x = element_blank(),    axis.ticks.x = element_blank()  )
# 最大回撤面积图（隐藏标题和X轴，删除图例）p2_drawdown <- ggplot(  vs_equal_drawdown,  aes(x = Date, y = 回撤, fill = 策略)) +  geom_area(position = "identity", alpha = area_alpha) + # 使用面积图  labs(    title = "",    x = "日期", y = "回撤"  ) +  scale_y_continuous(labels = percent_format()) +  scale_fill_manual(values = colors_strategies[c("动量策略", "等权重组合")]) +  theme_minimal() +  theme(    legend.position = "none", # 删除图例    plot.title = element_blank(),    axis.title.x = element_text(size = 12)  )
# 使用cowplot组合图表p2_combined <- plot_grid(p2_cumulative, p2_drawdown,  ncol = 1, align = "v",  rel_heights = c(1, 0.9))
# ============================================================# 图表3: 动量策略 vs 三个单独的ETF (Buy&Hold) 组合图# ============================================================# 计算各ETF的累计收益率etf_cumulative <- do.call(  merge,  lapply(    1:length(etf_names),    function(i) cumprod(1 + na.fill(returns_all[, i], 0)) - 1  ))colnames(etf_cumulative) <- etf_names
# 计算各ETF的回撤etf_drawdowns <- do.call(  merge,  lapply(    1:length(etf_names),    function(i) calculate_drawdown(returns_all[, i])  ))colnames(etf_drawdowns) <- etf_names
# 合并策略和各ETF的数据all_cumulative <- merge(cumulative_returns[, "动量策略"], etf_cumulative)all_drawdowns <- merge(calculate_drawdown(strategy_returns), etf_drawdowns)
# 转换为数据框用于ggplotall_cumulative_df <- data.frame(  Date = index(all_cumulative),  as.data.frame(all_cumulative))all_drawdowns_df <- data.frame(  Date = index(all_drawdowns),  as.data.frame(all_drawdowns))
# 转换为长格式all_cumulative_long <- pivot_longer(all_cumulative_df,  cols = -Date,  names_to = "策略",  values_to = "累计收益率")
all_drawdowns_long <- pivot_longer(all_drawdowns_df,  cols = -Date,  names_to = "策略",  values_to = "回撤")
# 设置颜色方案 - 动量策略和三个ETF（确保每个都有不同颜色）colors_buyhold <- c(  "动量策略" = "#E41A1C", # 红色  "纳指ETF" = "#377EB8", # 蓝色  "沪深300ETF" = "#4DAF4A", # 绿色  "黄金ETF" = "#984EA3" # 紫色)
# 确保策略名称与颜色方案匹配all_cumulative_long$策略 <- factor(all_cumulative_long$策略,  levels = names(colors_buyhold))all_drawdowns_long$策略 <- factor(all_drawdowns_long$策略,  levels = names(colors_buyhold))
# 累计收益率图（隐藏X轴，图例放在左上角，上下排列）p3_cumulative <- ggplot(  all_cumulative_long,  aes(x = Date, y = 累计收益率, color = 策略)) +  geom_line(size = 1.0) +  labs(    title = "动量策略 vs 各ETF买入持有策略: 累计收益率",    x = "", y = "累计收益率"  ) +  scale_y_continuous(labels = percent_format()) +  scale_color_manual(values = colors_buyhold) + # 确保应用颜色方案  theme_minimal() +  theme(    legend.position = c(0.02, 0.98), # 图例在左上角    legend.justification = c(0, 1), # 对齐到左上角    legend.direction = "vertical", # 图例上下排列    legend.title = element_blank(),    legend.background = element_rect(fill = "white", color = "gray", size = 0.3),    legend.box.margin = margin(5, 5, 5, 5), # 图例内边距    legend.spacing.y = unit(0.15, "cm"), # 图例项之间的垂直间距    legend.text = element_text(size = 9),    plot.title = element_text(hjust = 0.5, size = 14, face = "bold"),    axis.title.x = element_blank(),    axis.text.x = element_blank(),    axis.ticks.x = element_blank()  )
# 最大回撤面积图（隐藏标题和X轴，删除图例）p3_drawdown <- ggplot(  all_drawdowns_long,  aes(x = Date, y = 回撤, fill = 策略)) +  geom_area(position = "identity", alpha = area_alpha) + # 使用面积图  labs(    title = "",    x = "日期", y = "回撤"  ) +  scale_y_continuous(labels = percent_format()) +  scale_fill_manual(values = colors_buyhold) + # 确保应用颜色方案  theme_minimal() +  theme(    legend.position = "none", # 删除图例    plot.title = element_blank(),    axis.title.x = element_text(size = 12)  )
# 使用cowplot组合图表p3_combined <- plot_grid(p3_cumulative, p3_drawdown,  ncol = 1, align = "v",  rel_heights = c(1, 0.9))
# ============================================================# 图表4: 权重随时间变化图# ============================================================# 准备权重数据weights_long <- pivot_longer(daily_weights_df,  cols = all_of(etf_names),  names_to = "ETF",  values_to = "Weight")
# 权重图颜色方案colors_weights <- c(  "纳指ETF" = "#E41A1C", # 红色  "沪深300ETF" = "#377EB8", # 蓝色  "黄金ETF" = "#4DAF4A" # 绿色)
p4 <- ggplot(weights_long, aes(x = Date, y = Weight, fill = ETF)) +  geom_area(position = "stack", alpha = 0.7) +  labs(    title = "动量策略: 每日权重分配",    x = "日期", y = "权重"  ) +  scale_y_continuous(labels = percent_format()) +  scale_fill_manual(values = colors_weights) +  theme_minimal() +  theme(    legend.position = "bottom",    plot.title = element_text(hjust = 0.5, size = 14, face = "bold")  )
# 12. 显示图表cat("\n正在显示图表...\n")print(p1_combined)cat("\n\n")print(p2_combined)cat("\n\n")print(p3_combined)cat("\n\n")print(p4)
# 13. 保存结果到文件output_dir <- "momentum_strategy_backtest"if (!dir.exists(output_dir)) {  dir.create(output_dir)}
# 保存绩效指标数据框write.csv(performance_metrics_df,  file = paste0(output_dir, "/performance_metrics.csv"),  row.names = FALSE, fileEncoding = "UTF-8")
# 保存权重数据write.csv(daily_weights_df,  file = paste0(output_dir, "/daily_weights.csv"),  row.names = FALSE, fileEncoding = "UTF-8")
# 保存每个ETF的权重数据for (etf in etf_names) {  write.csv(etf_weight_dfs[[etf]],    file = paste0(output_dir, "/", etf, "_weights.csv"),    row.names = FALSE, fileEncoding = "UTF-8"  )}
# 保存合并的ETF权重数据write.csv(all_etf_weights_df,  file = paste0(output_dir, "/all_etf_weights.csv"),  row.names = FALSE, fileEncoding = "UTF-8")
# 保存累计收益率数据write.csv(cumulative_df,  file = paste0(output_dir, "/cumulative_returns.csv"),  row.names = FALSE, fileEncoding = "UTF-8")
# 保存回撤数据write.csv(drawdown_df,  file = paste0(output_dir, "/drawdowns.csv"),  row.names = FALSE, fileEncoding = "UTF-8")
# 保存Buy&Hold数据write.csv(all_cumulative_df,  file = paste0(output_dir, "/buyhold_cumulative.csv"),  row.names = FALSE, fileEncoding = "UTF-8")
write.csv(all_drawdowns_df,  file = paste0(output_dir, "/buyhold_drawdowns.csv"),  row.names = FALSE, fileEncoding = "UTF-8")
# 保存图表ggsave(paste0(output_dir, "/momentum_vs_benchmark.png"), p1_combined, width = 10, height = 8)ggsave(paste0(output_dir, "/momentum_vs_equal_weight.png"), p2_combined, width = 10, height = 8)ggsave(paste0(output_dir, "/momentum_vs_etfs_buyhold.png"), p3_combined, width = 10, height = 8)ggsave(paste0(output_dir, "/daily_weights_plot.png"), p4, width = 10, height = 6)
cat("\n==================== 回测完成 ====================\n")cat("结果已保存到文件夹:", output_dir, "\n")cat("包含文件:\n")cat("1. performance_metrics.csv - 绩效指标汇总数据框\n")cat("2. daily_weights.csv - 每日权重分配数据框\n")cat("3. [ETF名称]_weights.csv - 每个ETF的权重数据框\n")cat("4. all_etf_weights.csv - 所有ETF合并的权重数据框\n")cat("5. cumulative_returns.csv - 累计收益率数据\n")cat("6. drawdowns.csv - 回撤数据\n")cat("7. buyhold_cumulative.csv - Buy&Hold累计收益率数据\n")cat("8. buyhold_drawdowns.csv - Buy&Hold回撤数据\n")cat("9. momentum_vs_benchmark.png - 动量策略vs沪深300ETF组合图\n")cat("10. momentum_vs_equal_weight.png - 动量策略vs等权重组合对比图\n")cat("11. momentum_vs_etfs_buyhold.png - 动量策略vs各ETF买入持有策略组合图\n")cat("12. daily_weights_plot.png - 每日权重分配图\n")
```

**微信扫一扫赞赏作者**

策略代码 · 目录

作者提示: 个人观点，仅供参考