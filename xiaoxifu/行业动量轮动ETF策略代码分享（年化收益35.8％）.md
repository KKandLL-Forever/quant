---
title: "行业动量轮动ETF策略代码分享（年化收益35.8％）"
source: "https://mp.weixin.qq.com/s/sXeUAmowGTdgyEzgSLlc2g"
author:
  - "[[MatrixSpk]]"
published:
created: 2026-07-01
description: "干脆开源吧"
tags:
  - "clippings"
---
MatrixSpk 小西西弗的量化之路 *2025年12月24日 17:45*

## 今天分享一段行业动量轮动量化策略的代码。

## 一、策略表现

## 表：行业动量轮动策略主要绩效指标

![图片](https://mmbiz.qpic.cn/sz_mmbiz_png/rEUglkbjrwcU5jnytlugqTwV5C2MwyXHnaSUBbiaam6S3QBxgmV8ia3Jvu33IMcptp39tia0MvAichxydaQD6tZ6fQ/640?wx_fmt=png&from=appmsg&watermark=1&tp=webp&wxfrom=5&wx_lazy=1#imgIndex=0)

图1：行业动量轮动策略与科创ETF业绩对比图

![图片](https://mmbiz.qpic.cn/sz_mmbiz_png/rEUglkbjrwcU5jnytlugqTwV5C2MwyXHNsuqwhEVDiasEx3tTdK5JXU9RQ75ER39v209vibuYZF2qYUwPKZmNMhg/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=2)

## 图2：行业动量轮动与行业ETF等权重策略对比图

## 二、代码整体核心思路

行业 ETF 动量轮动量化策略的核心逻辑是： **利用金融市场的「动量效应」，在不同行业 ETF 之间进行择时和择标的轮动，赚取趋势延续的收益，同时规避弱势行业的下跌风险** ，是一套完整的包含 **数据获取、指标计算、选股调仓、收益核算、绩效回测、结果输出** 的量化回测流程，所有步骤环环相扣，目标是跑赢科创 50ETF 基准和行业等权重持仓。

### （一）策略设计的底层逻辑：为什么要做【行业动量轮动】

做行业动量轮动，而非买入持有单一指数 / 单一行业，核心原因有 3 点，也是这个策略成立的底层根基：

- **市场的「动量效应」客观存在**
	金融市场中存在经典的 **动量效应 (Momentum Effect)** —— 即 **在过去一段时间表现好的资产，在未来短期内会延续其上涨趋势；过去表现差的资产，未来短期内会延续下跌趋势** 。这份代码中用「N 日平均收益率」衡量动量，本质就是捕捉这种趋势惯性。
- **A 股行业轮动特征显著**
	A 股市场不是所有行业同步涨跌，而是 **板块轮动极强** （比如 2024 年人工智能、机器人、半导体轮涨，而部分传统行业走弱），不同行业在不同阶段的景气度、资金偏好差异极大。如果一直持有单一行业 / 宽基指数，会错过强势行业的超额收益，也会承受弱势行业的回撤；而通过动量轮动能 **始终把仓位集中在当下最强势的行业** 。
- **规避单一行业的非系统性风险 + 平滑组合波动**
	单一行业 ETF 的波动率极高（比如半导体、游戏 ETF），而通过 **多行业择优选取 + 权重配比** ，能在保留强势行业收益的同时，分散单一行业的黑天鹅风险，让组合的回撤更小、收益曲线更平滑。
- **ETF 标的的适配性**
	代码选用的是行业 ETF 而非个股，ETF 本身是一篮子股票的组合，能规避个股暴雷风险，同时流动性好、交易成本低，非常适合做轮动调仓的标的。

---

## 三、代码逐段核心思路拆解

代码共 8 个核心部分，逻辑上是 **标准化量化回测流程** ，循序渐进，每一步的目的都非常明确：

### 第一部分：加载软件包

加载量化分析必备的包，分工明确：

- `quantmod`
	核心用于从雅虎财经下载金融行情数据（复权收盘价）；
- `PerformanceAnalytics`
	专业的量化绩效分析包，计算夏普比率、最大回撤、卡玛比率等核心指标；
- `ggplot2/patchwork/cowplot`
	绘图可视化，做累计收益、最大回撤的对比图；
- `dplyr/tidyr`
	数据清洗、整理、分组统计；
- 其余包：解决中文字体、颜色配色、数据导出等辅助需求。

### 第二部分：配置核心参数

这是策略的 **灵魂** ，所有核心规则都在这里定义，且参数可灵活调整，是量化策略「参数优化」的核心入口：

- 运行时间：2024-01-01 至 当前日期；
- 动量参数： `N=20` （用 20 日平均收益率算动量）、 `K=5` （每 5 个交易日调仓 1 次）、 `L=5` （每次选动量前 5 的标的）；
- 标的池：13 个主流行业 ETF（人工智能、半导体、军工、消费等），覆盖成长 + 价值板块，兼顾市场不同风格；
- 基准：科创 50ETF（588000），用于衡量策略是否跑出 **超额收益** 。

### 第三部分：下载和清洗数据

1. 封装了带 **重试机制** 的下载函数，解决雅虎财经偶尔下载失败的问题，最多重试 3 次；
2. 只选用 **复权后收盘价** （Adj Close），这是量化分析的核心数据，复权能剔除除权除息对价格的干扰，真实反映资产收益；
3. 合并所有标的的价格数据，做数据维度校验，避免数据缺失导致后续计算报错。

### 第四部分：计算每日动量数据（策略核心指标层）

这是代码 **最核心的量化逻辑** ，不只是简单算「动量」，而是做了 **动量的优化改良** ，也是策略的亮点：

- 先计算每个标的的 **日算术收益率** ；
- 基础动量：20 日滚动平均收益率 → 衡量标的的「趋势强弱」；
- 波动率：20 日滚动收益率方差 → 衡量标的的「风险高低」；
- **核心优化：调整动量 (Adj\_Momentum) = 动量 / 波动率**
- 为什么这么做？ **普通动量只看收益，不看风险** ：比如 A 标的 20 日收益 5%、波动率 10%，B 标的 20 日收益 4%、波动率 2%，单看收益选 A，但 A 的风险远高于 B，单位风险的收益性价比 B 更高；
	- 调整动量本质是「 **风险调整后的收益** 」，能筛选出「收益高、波动低」的优质标的，避免选到高收益高波动的「垃圾动量」标的，大幅提升选股的质量。

### 第五部分：生成每日权重 + 组合收益率（策略交易规则层）

这是策略的 **交易执行逻辑** ，所有规则都严格贴合实盘交易，也是量化策略的「规则核心」，规则设计非常严谨，关键点如下：

- **调仓日规则**
	从第 20 个交易日开始（需要 20 天数据算动量），每 5 个交易日调仓 1 次，符合参数 `K=5` ；
- **选股规则（核心过滤）**
	只选「 **动量为正** 」的标的 ，只做多 **处于上涨趋势** 的行业， **完全规避下跌趋势的行业** ，这是策略控制回撤的核心规则；
- **标的筛选**
	在正动量标的中，按「调整动量」降序排序，选前 5 名（L=5）；如果正动量标的不足 5 个，就全部选中；
- **权重分配规则**
	**不等权，而是按调整动量归一化加权** ，动量越强的标的，分配的仓位越高，进一步放大强势标的的收益，不是简单的等权持仓；
- **权重生效规则**
	调仓日的权重，生效至下一次调仓日前一天，贴合实盘「调仓后持仓不动」的逻辑；
- **收益率计算**
	权重 **滞后 1 天** 计算 → 核心细节！避免「未来函数」（量化回测的大忌），因为当日的动量数据要收盘后才能计算，次日才能买入，完全贴合实盘交易的时间逻辑，保证回测结果的真实性。

### 第六 - 七部分：计算对比基准收益率

为了客观衡量策略的有效性，代码做了 **双重对比** ，这是量化回测的必备逻辑：

- 对比 1：科创 50ETF（宽基指数基准）→ 看策略是否能跑赢市场平均收益；
- 对比 2：行业 ETF 等权重组合 → 看「动量选股 + 轮动」是否比「无脑分散持仓」更有效；
- 这个对比非常关键：如果策略收益只是和等权重持平，说明动量轮动没有价值；如果大幅跑赢，说明策略的选股和轮动规则有效。

### 第八部分：可视化 + 绩效指标 + 结果保存

- **可视化核心**
	绘制「累计收益率 + 最大回撤」双图组合，这是量化策略最核心的两张图：
- 累计收益：看策略的收益能力、超额收益的持续性；
	- 最大回撤：看策略的风险承受能力，回撤越小，策略越稳健；
- **绩效指标**
	计算 5 个量化核心指标（年化收益、年化波动率、最大回撤、夏普比率、卡玛比率）， **量化而非主观** 评价策略优劣；
- **结果保存**
	将所有核心数据（收益、回撤、权重、选股频率）导出为 CSV，图表导出为高清 PNG，方便后续复盘、参数优化、策略改进。

---

## 三、该行业动量轮动策略的核心优势

结合代码设计 + 动量策略本身的特性，这份策略的优势非常突出，也是该策略能在 A 股市场长期有效的核心原因， **优势按优先级排序** ：

### 优势 1：贴合 A 股市场特征，策略适配性极强

A 股的核心特征就是「 **重趋势、轻价值，行业轮动快** 」，而动量策略的本质就是「顺势而为」，完全契合 A 股的市场风格。相比于价值投资（A 股价值股长期跑输成长股）、反转策略（A 股反转效应弱，趋势延续性强），动量轮动在 A 股的有效性更高。

### 优势 2：风险控制规则完善，回撤可控性强

这份代码在基础动量策略上做了 **多层风控优化** ，这是最核心的亮点，区别于简单的动量策略：

- 只选「正动量」标的： **坚决不碰下跌趋势的行业** ，从根源上规避行业暴跌的风险；
- 风险调整后的动量选股：选「高收益、低波动」的标的，避免选到高波动的垃圾标的；
- 分散持仓：每次选 5 个标的，而非重仓 1-2 个，分散单一行业的非系统性风险；
- 固定调仓频率：避免过度交易，降低交易成本和择时失误的概率。

### 优势 3：标的选择合理，规避个股风险

选用 **行业 ETF** 而非个股作为标的，ETF 的优势：

- 无个股暴雷风险：ETF 是一篮子股票，单一个股的利空不会对 ETF 造成致命影响；
- 流动性好：行业 ETF 的成交额足够大，买卖不会有滑点，适合实盘交易；
- 成本低：ETF 的管理费、交易佣金远低于个股频繁交易，长期复利效应明显。

### 优势 4：策略逻辑简单，可解释性强，易落地

- 策略没有复杂的机器学习模型、没有晦涩的指标，核心就是「动量效应 + 风险调整」，逻辑清晰，容易理解和复盘；
- 所有规则都是 **量化硬规则** ，没有主观判断，完全可以写成程序自动化交易，避免人性的贪婪和恐惧；
- 参数可灵活调整（N/K/L），可以根据市场风格变化做参数优化（比如震荡市调小 N，趋势市调大 N）。

### 优势 5：回测严谨，无未来函数，结果真实可靠

代码中所有的计算都做了「权重滞后 1 天」的处理，所有指标都是基于 **已实现的历史数据** 计算，没有任何未来函数（量化回测的大忌），回测结果能真实反映策略在实盘中的表现，可信度高。

### 优势 6：双重对比验证，策略有效性可量化

同时对比「宽基基准 + 等权重组合」，能客观衡量策略的超额收益来源：是动量选股有效？还是轮动有效？还是单纯的运气？量化指标能给出明确答案。

```perl
# ==================== 第一部分：加载软件包 ====================if (!require("quantmod")) install.packages("quantmod")if (!require("PerformanceAnalytics")) install.packages("PerformanceAnalytics")if (!require("ggplot2")) install.packages("ggplot2")if (!require("dplyr")) install.packages("dplyr")if (!require("tidyr")) install.packages("tidyr")if (!require("scales")) install.packages("scales")if (!require("patchwork")) install.packages("patchwork")if (!require("cowplot")) install.packages("cowplot")if (!require("RColorBrewer")) install.packages("RColorBrewer")if (!require("knitr")) install.packages("knitr")if (!require("showtext")) install.packages("showtext")
library(quantmod) # 金融数据获取与分析library(PerformanceAnalytics) # 投资组合绩效分析library(ggplot2) # 高级绘图library(dplyr) # 数据处理library(tidyr) # 数据整理library(scales) # 图形标度调整library(patchwork) # 图形组合library(cowplot) # 图形组合（更精细的控制）library(RColorBrewer) # 颜色调色板library(knitr) # 报表输出library(showtext) # 字体支持
# 添加中文字体支持font_add("simhei", "simhei.ttf")
showtext_auto()theme_set(theme_bw(base_family = "simhei")) # 使用bw主题
# ==================== 第二部分：配置参数 ====================# 回测时间段backtest_start <- "2024-01-01"backtest_end <- Sys.Date()
# 动量计算参数# 动量计算周期（默认20天）
# 调仓间隔天数（默认5天，表示每5个交易日调仓一次）
# 选择标的数量（默认选择前5名）

# 基准参数benchmark_symbol <- "588000.SS" # 科创50ETFbenchmark_name <- "科创50ETF"
# 转换为日期类型start_date <- as.Date(backtest_start)end_date <- as.Date(backtest_end)
# 定义股票/ETF代码和名称stock_symbols <- c(  "159819.SZ", "588000.SS", "512690.SS", "159813.SZ",  "159526.SZ", "515650.SS", "159869.SZ", "159740.SZ",  "159992.SZ", "159755.SZ", "515290.SS", "512200.SS",  "159766.SZ")
stock_names <- c(  "人工智能ETF", "科创50ETF", "军工ETF", "半导体ETF",  "机器人ETF嘉实", "消费50ETF", "游戏ETF", "恒生科技ETF",  "创新药ETF", "电池ETF", "银行ETF易方达", "房地产ETF",  "旅游ETF")
# 将基准加入股票列表all_symbols <- c(stock_symbols, benchmark_symbol)all_names <- c(stock_names, benchmark_name)
cat("==================== 参数设置 ====================\n")cat(sprintf("动量计算周期 N = %d 天\n", N))cat(sprintf("调仓间隔 K = %d 天\n", K))cat(sprintf("选择标的数量 L = %d 只\n", L))cat(sprintf("回测期间: %s 至 %s\n", backtest_start, as.character(end_date)))cat(sprintf("股票数量: %d 只龙头股票\n", length(stock_symbols)))cat(sprintf("基准: %s (%s)\n", benchmark_name, benchmark_symbol))cat("================================================\n\n")
# ==================== 第三部分：下载和清洗数据 ====================cat("正在从Yahoo Finance获取数据...\n")
# 设置下载重试机制download_data <- function(symbols, from_date, to_date, max_retries = 3) {  price_list <- list()
  for (i in 1:length(symbols)) {    symbol <- symbols[i]    retry_count <- 0    success <- FALSE
    while (retry_count < max_retries && !success) {      tryCatch(        {          symbol_data <- getSymbols(symbol,            from = from_date,            to = to_date,            src = "yahoo",            auto.assign = FALSE,            warnings = FALSE          )
          # 使用调整后收盘价          price_list[[i]] <- Ad(symbol_data)          colnames(price_list[[i]]) <- all_names[i]          success <- TRUE          cat(sprintf("  √ 成功下载: %s (%s)\n", all_names[i], symbol))        },        error = function(e) {          retry_count <- retry_count + 1          if (retry_count == max_retries) {            cat(sprintf("  × 下载失败: %s (%s) - %s\n", all_names[i], symbol, e$message))            price_list[[i]] <- NULL          } else {            cat(sprintf("  ! 重试下载: %s (%s) 第%d次\n", all_names[i], symbol, retry_count))# 等待1秒后重试
          }        }      )    }  }
  # 过滤掉NULL值  price_list <- price_list[!sapply(price_list, is.null)]  return(price_list)}
# 下载数据price_list <- download_data(all_symbols, start_date, end_date)
# 检查是否成功下载数据if (length(price_list) == 0) {  stop("未能下载任何数据，请检查网络连接和股票代码")}
# 合并所有价格数据cat("正在合并数据...\n")price_xts <- do.call(merge, price_list)
# 检查数据cat(sprintf("价格数据维度: %d 行 × %d 列\n", nrow(price_xts), ncol(price_xts)))cat(sprintf(  "数据时间范围: %s 至 %s\n",  as.character(index(price_xts)[1]),  as.character(index(price_xts)[nrow(price_xts)])))cat("\n")
# ==================== 第四部分：计算每日动量数据 ====================# 动量计算函数calculate_momentum <- function(price_series, window = N) {  # 计算日收益率  returns <- dailyReturn(price_series, type = "arithmetic")  colnames(returns) <- "Return"
  # 计算N日平均收益率（动量）  momentum <- rollapply(returns,    width = window,    FUN = mean, align = "right", fill = NA  )  colnames(momentum) <- "Momentum"
  # 计算N日收益率方差（波动率）  volatility <- rollapply(returns,    width = window,    FUN = var, align = "right", fill = NA  )  colnames(volatility) <- "Volatility"
  # 计算调整动量（动量/波动率，使用标准差进行标准化）  adj_momentum <- momentum / sqrt(volatility)  colnames(adj_momentum) <- "Adj_Momentum"
  return(list(    returns = returns,    momentum = momentum,    volatility = volatility,    adj_momentum = adj_momentum  ))}
# 为每个股票计算动量指标，以list形式存储cat(sprintf("正在计算%d日动量指标...\n", N))momentum_list <- list()for (stock in colnames(price_xts)) {  momentum_list[[stock]] <- calculate_momentum(price_xts[, stock])}
# 提取所有股票的调整动量和动量，用于后续选股adj_momentum_all <- do.call(merge, lapply(momentum_list, function(x) x$adj_momentum))momentum_all <- do.call(merge, lapply(momentum_list, function(x) x$momentum))colnames(adj_momentum_all) <- colnames(price_xts)colnames(momentum_all) <- colnames(price_xts)
cat(sprintf("动量数据维度: %d 行 × %d 列\n", nrow(adj_momentum_all), ncol(adj_momentum_all)))cat("\n")
# ==================== 第五部分：生成每日权重和组合收益率 ====================# 生成每日权重的函数generate_daily_weights <- function(adj_momentum_df, momentum_df,                                   rebalance_freq = K, select_count = L) {  # 初始化权重数据框  weights_df <- as.data.frame(adj_momentum_df)# 所有值初始化为0

  # 获取所有交易日日期  all_dates <- index(adj_momentum_df)
  # 确定调仓日：从第N个交易日开始，每隔K个交易日调仓一次  if (length(all_dates) < N) {    stop(sprintf("数据不足，需要至少%d个交易日的数据，当前只有%d个", N, length(all_dates)))  }
  # 确定所有可能的调仓日  start_idx <- N # 从第N天开始（因为需要N天数据计算动量）  rebalance_indices <- seq(start_idx, length(all_dates), by = rebalance_freq)
  cat(sprintf("总交易日数: %d\n", length(all_dates)))  cat(sprintf("调仓日数量: %d\n", length(rebalance_indices)))  cat(sprintf("首次调仓日: %s\n", as.character(all_dates[start_idx])))  cat(sprintf("最后调仓日: %s\n", as.character(all_dates[rebalance_indices[length(rebalance_indices)]])))  cat(sprintf("每期选择标的数量: %d\n", select_count))
  # 对每个调仓日计算权重  for (rebalance_idx in rebalance_indices) {    # 获取当日的调整动量值和动量值    current_adj_momentum <- as.numeric(adj_momentum_df[rebalance_idx, ])    current_momentum <- as.numeric(momentum_df[rebalance_idx, ])
    # 筛选动量为正数的标的（排除基准）    # 假设基准是最后一列    selectable_idx <- 1:(ncol(adj_momentum_df) - 1) # 排除最后一个（基准）    selectable_momentum <- current_momentum[selectable_idx]    positive_momentum_idx <- selectable_idx[which(selectable_momentum > 0 & !is.na(selectable_momentum))]
    if (length(positive_momentum_idx) > 0) {      # 获取正动量标的的调整动量值      positive_adj_momentum <- current_adj_momentum[positive_momentum_idx]
      # 如果正动量标的超过select_count个，选择调整动量排名前select_count的      if (length(positive_momentum_idx) > select_count) {        adj_momentum_rank <- order(positive_adj_momentum, decreasing = TRUE)        top_idx <- adj_momentum_rank[1:select_count]        selected_idx <- positive_momentum_idx[top_idx]        selected_adj_momentum <- positive_adj_momentum[top_idx]      } else {        # 如果不超过select_count个，全部选择        selected_idx <- positive_momentum_idx        selected_adj_momentum <- positive_adj_momentum      }
      # 按调整动量大小归一化计算权重      normalized_weights <- selected_adj_momentum / sum(selected_adj_momentum)
      # 确定该权重生效的日期范围：从当前调仓日到下一个调仓日前一天      if (rebalance_idx == rebalance_indices[length(rebalance_indices)]) {        end_idx <- length(all_dates) # 最后一个调仓日      } else {        next_rebalance_idx <- rebalance_indices[which(rebalance_indices == rebalance_idx) + 1]        end_idx <- next_rebalance_idx - 1      }      end_idx <- min(end_idx, length(all_dates))
      # 应用权重到该调仓周期内的所有交易日      for (i in rebalance_idx:end_idx) {        weights_df[i, selected_idx] <- normalized_weights      }    }  }
  # 转换为时间序列  weights_xts <- xts(weights_df, order.by = all_dates)  colnames(weights_xts) <- colnames(adj_momentum_df)
  return(weights_xts)}
# 生成权重序列cat(sprintf("正在计算每日权重（调仓间隔K=%d天，选择标的L=%d只）...\n", K, L))weights <- generate_daily_weights(adj_momentum_all, momentum_all, K, L)
# 将每日权重以list形式存储 - 只保留权重不为0的记录cat("正在创建每日权重列表（只保留权重大于0的记录）...\n")daily_weights_list <- list()weights_df <- as.data.frame(weights) # 转换为数据框
# 使用整数索引循环，只保存权重大于0的记录for (i in 1:nrow(weights_df)) {  date <- index(weights)[i] # 从原始的xts对象获取日期
  # 获取当日的权重向量  weight_vector <- as.numeric(weights_df[i, ])
  # 找出权重大于0的股票  positive_weights_idx <- which(weight_vector > 0)
  if (length(positive_weights_idx) > 0) {    # 只保存权重大于0的记录    weight_df <- data.frame(      Stock = colnames(weights)[positive_weights_idx],      Weight = weight_vector[positive_weights_idx],      Date = as.Date(date),      stringsAsFactors = FALSE    )
    # 按日期字符串作为列表的键    daily_weights_list[[as.character(date)]] <- weight_df  }}
cat(sprintf("成功创建每日权重列表，包含 %d 个交易日的权重数据\n", length(daily_weights_list)))
# 如果没有权重数据，创建空的列表if (length(daily_weights_list) == 0) {  cat("警告: 没有找到权重大于0的记录\n")}
# 计算投资组合每日收益率（权重滞后一天，按次日开盘价买入）cat("正在计算投资组合每日收益率...\n")returns_all <- do.call(merge, lapply(momentum_list, function(x) x$returns))colnames(returns_all) <- colnames(price_xts)
# 检查returns_all的列名cat("收益率数据列名:\n")print(colnames(returns_all))cat("\n")
# 调整权重的时间索引，确保与收益率对齐# 权重滞后一天

# 检查权重和收益率的日期范围cat(sprintf(  "权重数据日期范围: %s 至 %s\n",  as.character(index(weights)[1]),  as.character(index(weights)[nrow(weights)])))cat(sprintf(  "收益率数据日期范围: %s 至 %s\n",  as.character(index(returns_all)[1]),  as.character(index(returns_all)[nrow(returns_all)])))
# 对齐索引：使用公共日期common_dates <- intersect(index(lagged_weights), index(returns_all))cat(sprintf("公共日期数量: %d\n", length(common_dates)))
if (length(common_dates) == 0) {  stop("权重和收益率数据没有公共日期，无法计算策略收益率")}
# 使用公共日期提取数据lagged_weights_aligned <- lagged_weights[common_dates, ]returns_all_aligned <- returns_all[common_dates, ]
# 处理缺失值lagged_weights_aligned[is.na(lagged_weights_aligned)] <- 0
# 计算策略日收益率（只计算龙头股票的加权收益率，排除基准）# 假设基准是最后一列strategy_stocks_returns <- returns_all_aligned[, -ncol(returns_all_aligned)] # 排除基准strategy_stocks_weights <- lagged_weights_aligned[, -ncol(lagged_weights_aligned)] # 排除基准strategy_returns <- rowSums(strategy_stocks_returns * strategy_stocks_weights, na.rm = TRUE)strategy_returns <- xts(strategy_returns, order.by = index(returns_all_aligned))colnames(strategy_returns) <- "行业动量轮动策略"
cat(sprintf("策略收益率数据长度: %d\n", length(strategy_returns)))cat("策略收益率数据预览:\n")print(head(strategy_returns, 5))cat("\n")
# ==================== 第六部分：计算等权重组合收益率 ====================cat("正在计算等权重组合收益率...\n")# 计算等权重组合（行业ETF等权重）# 排除基准
equal_weights <- matrix(1 / num_stocks, nrow = nrow(returns_all_aligned), ncol = num_stocks)equal_weights <- xts(equal_weights, order.by = index(returns_all_aligned))colnames(equal_weights) <- colnames(returns_all_aligned)[1:num_stocks]
equal_returns <- rowSums(returns_all_aligned[, 1:num_stocks] * equal_weights, na.rm = TRUE)equal_returns <- xts(equal_returns, order.by = index(returns_all_aligned))colnames(equal_returns) <- "等权重组合"
cat("等权重组合收益率数据预览:\n")print(head(equal_returns, 5))cat("\n")
# ==================== 第七部分：计算基准收益率 ====================cat("正在计算基准收益率...\n")
# 检查基准名称是否在收益率数据列名中if (benchmark_name %in% colnames(returns_all_aligned)) {  benchmark_returns <- returns_all_aligned[, benchmark_name]  colnames(benchmark_returns) <- benchmark_name} else {  # 如果基准名称不在列名中，尝试使用最后一列  cat(sprintf("警告: 基准名称 '%s' 不在收益率数据列名中，使用最后一列作为基准\n", benchmark_name))  benchmark_returns <- returns_all_aligned[, ncol(returns_all_aligned)]  colnames(benchmark_returns) <- benchmark_name}
cat("基准收益率数据预览:\n")print(head(benchmark_returns, 5))cat("\n")
# ==================== 第八部分：绘制图表 ====================# 1. 计算累计收益率cat("正在计算累计收益率和最大回撤...\n")calculate_cumulative_returns <- function(returns) {  # 用0填充NA值  returns_filled <- na.fill(returns, 0)1
}
strategy_cumulative <- calculate_cumulative_returns(strategy_returns)benchmark_cumulative <- calculate_cumulative_returns(benchmark_returns)equal_cumulative <- calculate_cumulative_returns(equal_returns)
# 合并累计收益率cumulative_all <- merge(strategy_cumulative, benchmark_cumulative, equal_cumulative)colnames(cumulative_all) <- c("行业动量轮动策略", benchmark_name, "等权重组合")
# 转换为数据框用于ggplotcumulative_df <- data.frame(  Date = index(cumulative_all),  as.data.frame(cumulative_all))
# 2. 计算最大回撤calculate_drawdown <- function(returns) {  returns_filled <- na.fill(returns, 0)  cum_returns <- cumprod(1 + returns_filled)  drawdown <- cum_returns / cummax(cum_returns) - 1  return(drawdown)}
strategy_dd <- calculate_drawdown(strategy_returns)benchmark_dd <- calculate_drawdown(benchmark_returns)equal_dd <- calculate_drawdown(equal_returns)
# 合并回撤数据drawdown_all <- merge(strategy_dd, benchmark_dd, equal_dd)colnames(drawdown_all) <- c("行业动量轮动策略", benchmark_name, "等权重组合")
# 转换为数据框用于ggplotdrawdown_df <- data.frame(  Date = index(drawdown_all),  as.data.frame(drawdown_all))
# 3. 创建组合图函数create_combined_plot <- function(cumulative_data, drawdown_data,                                 strategy1, strategy2,                                 title, colors) {  # 准备累计收益率数据  cumulative_long <- cumulative_data %>%    select(Date, all_of(c(strategy1, strategy2))) %>%    pivot_longer(cols = -Date, names_to = "策略", values_to = "累计收益率")
  # 准备回撤数据  drawdown_long <- drawdown_data %>%    select(Date, all_of(c(strategy1, strategy2))) %>%    pivot_longer(cols = -Date, names_to = "策略", values_to = "回撤")
  # 累计收益率图 - 使用bw主题，删除X轴标题，图例无边框且背景透明  p_cumulative <- ggplot(cumulative_long, aes(x = Date, y = 累计收益率, color = 策略)) +    geom_line(size = 1.2) +    labs(title = title, subtitle = "累计收益率", y = "累计收益率", x = "") +    scale_y_continuous(labels = percent_format()) +    scale_color_manual(values = colors) +    theme_bw(base_family = "simhei") + # 使用bw主题    theme(      legend.position = c(0.02, 0.98),1
      legend.direction = "vertical",      legend.title = element_blank(),      # 图例无边框，背景透明      legend.background = element_blank(), # 透明背景      legend.key = element_blank(), # 图例键无边框      legend.box.background = element_blank(), # 图例框背景透明      legend.box.margin = margin(5, 5, 5, 5),      legend.spacing.y = unit(0.2, "cm"),      legend.text = element_text(size = 10),      plot.title = element_text(hjust = 0.5, size = 14, face = "bold"),      plot.subtitle = element_text(hjust = 0.5, size = 12),      axis.title.x = element_blank(), # 删除X轴标题      axis.text.x = element_blank(),      axis.ticks.x = element_blank()    )
  # 最大回撤图 - 使用bw主题，删除图例  p_drawdown <- ggplot(drawdown_long, aes(x = Date, y = 回撤, fill = 策略)) +    geom_area(position = "identity", alpha = 0.3) +    labs(y = "回撤", x = "日期") +    scale_y_continuous(labels = percent_format()) +    scale_fill_manual(values = colors) +    theme_bw(base_family = "simhei") + # 使用bw主题    theme(      legend.position = "none", # 删除图例      plot.title = element_blank(),      axis.title.x = element_text(size = 12)    )
  # 组合两个图  plot_grid(p_cumulative, p_drawdown,"v"
    rel_heights = c(1, 0.9)  )}
# 4. 绘制组合和基准的对比图cat("正在生成组合和基准的对比图...\n")colors_vs_benchmark <- c(  "行业动量轮动策略" = "#E41A1C", # 红色  "科创50ETF" = "#377EB8" # 蓝色)
p1 <- create_combined_plot(  cumulative_df, drawdown_df,  "行业动量轮动策略", "科创50ETF",  sprintf("行业动量轮动策略 vs 科创50ETF (N=%d, K=%d, L=%d)", N, K, L),  colors_vs_benchmark)
# 5. 绘制组合和等权重组合的对比图cat("正在生成组合和等权重组合的对比图...\n")colors_vs_equal <- c(  "行业动量轮动策略" = "#E41A1C", # 红色  "等权重组合" = "#4DAF4A" # 绿色)
p2 <- create_combined_plot(  cumulative_df, drawdown_df,  "行业动量轮动策略", "等权重组合",  sprintf("行业动量轮动策略 vs 等权重组合 (N=%d, K=%d, L=%d)", N, K, L),  colors_vs_equal)
# 6. 显示图表cat("\n正在显示图表...\n")print(p1)cat("\n\n")print(p2)
# 7. 计算并显示绩效指标汇总 - 计算五个核心指标cat("\n正在计算绩效指标...\n")
calculate_performance <- function(returns, name) {  # 检查收益率数据  if (length(returns) == 0 || all(is.na(returns))) {    return(data.frame(      策略 = name,      年化收益 = NA,      年化波动率 = NA,      最大回撤 = NA,      夏普比率 = NA,      卡玛比率 = NA    ))  }
  # 移除NA值  returns_clean <- na.omit(returns)
  if (length(returns_clean) < 2) {    return(data.frame(      策略 = name,      年化收益 = NA,      年化波动率 = NA,      最大回撤 = NA,      夏普比率 = NA,      卡玛比率 = NA    ))  }
  # 计算年化收益率、年化波动率和夏普比率  perf <- tryCatch(    {      table.AnnualizedReturns(returns_clean)    },    error = function(e) {      cat(sprintf("计算%s绩效指标时出错: %s\n", name, e$message))      return(matrix(NA, nrow = 3, ncol = 1))    }  )
  # 计算最大回撤  max_dd <- tryCatch(    {      maxDrawdown(returns_clean)    },    error = function(e) {      cat(sprintf("计算%s最大回撤时出错: %s\n", name, e$message))      return(NA)    }  )
  # 计算卡玛比率（卡尔马比率）  calmar <- tryCatch(    {      CalmarRatio(returns_clean)    },    error = function(e) {      cat(sprintf("计算%s卡玛比率时出错: %s\n", name, e$message))      return(NA)    }  )
  df <- data.frame(    策略 = name,    年化收益 = if (!all(is.na(perf))) round(perf[1, 1] * 100, 2) else NA,    年化波动率 = if (!all(is.na(perf))) round(perf[2, 1] * 100, 2) else NA,    最大回撤 = if (!is.na(max_dd)) round(abs(max_dd) * 100, 2) else NA, # 取绝对值，以正数显示    夏普比率 = if (!all(is.na(perf))) round(perf[3, 1], 3) else NA,    卡玛比率 = if (!is.na(calmar)) round(calmar, 3) else NA  )
  colnames(df) <- c("策略", "年化收益", "年化波动率", "最大回撤", "夏普比率", "卡玛比率")  row.names(df) <- NULL  return(df)}
# 计算各策略绩效strategy_perf <- calculate_performance(strategy_returns, "行业动量轮动策略")benchmark_perf <- calculate_performance(benchmark_returns, benchmark_name)equal_perf <- calculate_performance(equal_returns, "等权重组合")
# 合并绩效指标 - 使用dplyr的bind_rows处理列名不一致问题performance_summary <- rbind(strategy_perf, benchmark_perf, equal_perf)
cat("\n==================== 绩效指标汇总 ====================\n\n")print(performance_summary)cat("\n")
# ==================== 保存结果到文件 ====================cat("正在保存结果到文件...\n")output_dir <- sprintf("行业动量轮动策略_N%d_K%d_L%d", N, K, L)if (!dir.exists(output_dir)) {  dir.create(output_dir)}
# 保存绩效指标write.csv(performance_summary,  file = paste0(output_dir, "/performance_summary.csv"),  row.names = FALSE, fileEncoding = "UTF-8")
# 保存累计收益率数据write.csv(cumulative_df,  file = paste0(output_dir, "/cumulative_returns.csv"),  row.names = FALSE, fileEncoding = "UTF-8")
# 保存回撤数据write.csv(drawdown_df,  file = paste0(output_dir, "/drawdowns.csv"),  row.names = FALSE, fileEncoding = "UTF-8")
# 保存每日权重列表（将列表转换为数据框保存）- 只保存权重大于0的记录if (length(daily_weights_list) > 0) {  daily_weights_df <- do.call(rbind, daily_weights_list)  write.csv(daily_weights_df,    file = paste0(output_dir, "/daily_weights_nonzero.csv"),    row.names = FALSE, fileEncoding = "UTF-8"  )
  # 保存每日权重汇总（每个交易日权重合计和选中标的数）  daily_summary <- data.frame(    Date = as.Date(names(daily_weights_list)),    权重合计 = sapply(daily_weights_list, function(df) sum(df$Weight)),    选中标的数 = sapply(daily_weights_list, function(df) nrow(df))  )  write.csv(daily_summary,    file = paste0(output_dir, "/daily_summary.csv"),    row.names = FALSE, fileEncoding = "UTF-8"  )
  # 计算每个股票被选中的总天数和平均权重  stock_summary <- daily_weights_df %>%    group_by(Stock) %>%    summarise(      选中天数 = n(),3
      选中比例 = round(n() / length(daily_weights_list) * 100, 1)    ) %>%    arrange(desc(选中天数))
  write.csv(stock_summary,    file = paste0(output_dir, "/stock_selection_summary.csv"),    row.names = FALSE, fileEncoding = "UTF-8"  )
  cat("股票选中频率排名（前10名）:\n")  print(head(stock_summary, 10))  cat("\n")} else {  cat("警告: 没有权重大于0的记录，跳过保存权重文件\n")}
# 保存动量数据（前5个股票作为示例）momentum_samples <- list()5
for (stock in sample_stocks) {  momentum_samples[[stock]] <- data.frame(    Date = index(momentum_list[[stock]]$momentum),    Stock = stock,    Momentum = as.numeric(momentum_list[[stock]]$momentum),    Volatility = as.numeric(momentum_list[[stock]]$volatility),    Adj_Momentum = as.numeric(momentum_list[[stock]]$adj_momentum)  )}momentum_samples_df <- do.call(rbind, momentum_samples)write.csv(momentum_samples_df,  file = paste0(output_dir, "/momentum_samples.csv"),  row.names = FALSE, fileEncoding = "UTF-8")
# 保存图表ggsave(paste0(output_dir, "/momentum_vs_benchmark.png"), p1, width = 10, height = 8, dpi = 300)ggsave(paste0(output_dir, "/momentum_vs_equal_weight.png"), p2, width = 10, height = 8, dpi = 300)
cat("==================== 回测完成 ====================\n")cat("结果已保存到文件夹:", output_dir, "\n")cat("包含文件:\n")cat("1. performance_summary.csv - 绩效指标汇总\n")cat("2. cumulative_returns.csv - 累计收益率数据\n")cat("3. drawdowns.csv - 回撤数据\n")if (length(daily_weights_list) > 0) {  cat("4. daily_weights_nonzero.csv - 每日权重数据（只包含权重大于0的记录）\n")  cat("5. daily_summary.csv - 每日权重汇总\n")  cat("6. stock_selection_summary.csv - 股票选中频率汇总\n")}cat("7. momentum_samples.csv - 动量数据示例（前5个股票）\n")cat("8. momentum_vs_benchmark.png - 组合和基准对比图\n")cat("9. momentum_vs_equal_weight.png - 组合和等权重组合对比图\n")cat("\n")cat("运行时间统计:\n")cat(sprintf("股票数量: %d\n", ncol(price_xts)))cat(sprintf("交易日数量: %d\n", nrow(price_xts)))cat(sprintf("公共日期数量: %d\n", length(common_dates)))if (length(daily_weights_list) > 0) {  cat(sprintf("有权重分配的交易日数: %d\n", length(daily_weights_list)))}cat(sprintf("策略开始日期: %s\n", as.character(index(strategy_returns)[1])))cat(sprintf("策略结束日期: %s\n", as.character(index(strategy_returns)[length(strategy_returns)])))cat("\n")
```

**微信扫一扫赞赏作者**

策略代码 · 目录

作者提示: 个人观点，仅供参考