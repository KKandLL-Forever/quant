---
title: "复现网红阻力支撑指标RSRS，手把手教你构建大盘择时策略"
source: "https://mp.weixin.qq.com/s/d8K2GDfc3sJlKoAy_yDoYQ"
author:
  - "[[量化君]]"
published:
created: 2026-07-05
description: "谁用谁知道~"
tags:
  - "clippings"
---
量化君 量化君也 *2023年4月10日 22:20*

![图片](https://mmbiz.qpic.cn/mmbiz_png/icBZQJaAUTJiac8IbeKmzNQ1RicdXbOzLzy9jfu3JK3sCf2tQ5Ku5yzT1Xqibt21ahx3ibmF3lrBOSFZEVOBibl4dMxg/640?wx_fmt=png&tp=webp&wxfrom=5&wx_lazy=1#imgIndex=0)

之前写过一篇利用RSRS指标做ETF轮动的文章，可能是因为回测绩效看起来还不错，其后就有不少小伙伴陆陆续续来询问，想不到还有那么多人关注，于是本期文章就想掰开了揉碎了唠唠RSRS，从数据获取、计算细节一直聊到策略构建，不藏着掖着，每一步都有对应代码。

![图片](https://mmbiz.qpic.cn/mmbiz_png/icBZQJaAUTJjPQgIia2uk5MhskL7u8PnwSmV42icgGnI8jvH9kyVP8cg8HicX1cEsiaZ8Dn6kBZAoNAeamo4j6eRD8g/640?wx_fmt=png&tp=webp&wxfrom=5&wx_lazy=1#imgIndex=1)

我当初关注到RSRS，是因为当时无论是做股票和ETF的，还是做期货CTA或者是大饼的圈子，都有不少人提到它，它被提及的频次仅次于MACD，说是网红指标也毫不为过，好奇心被勾起来了，就去细细研究和向大神们学习呗，于是乎才有了当时那篇ETF轮动的文章。

闲白说完，现在开始入活~~~

**1.RSRS的来源和思想**

**RSRS指标的全称是“阻力支撑相对强度（Resistance Support Relative Strength）”，它诞生于光大证券在2017年劳动节发布的金工研报《基于阻力支撑相对强度的市场择时》** ，这个系列的研报有好几篇，目录放在文末参考资料那里了，想看的小伙伴在本公众号后台回复暗号『 **RSRS** 』便可以保存下载阅读。

![图片](https://mmbiz.qpic.cn/mmbiz_png/icBZQJaAUTJjPQgIia2uk5MhskL7u8PnwSOkjnjZPiaJoXZR9g5vibQU2Z4ImZms0WzXPHf8pTFd5KeEMnPjacuAeQ/640?wx_fmt=png&tp=webp&wxfrom=5&wx_lazy=1#imgIndex=2)

具体的渊源和概念可以参照原版研报，如果只想听个大体思路的话，暂且听我之前的闲话唠一唠。

刚开始做交易的时候，总会听到一些"专家"预测点位，说大盘的阻力位在哪，说某只股票的支撑位在哪，各有各的理由，众说纷纭，但是预测的点位也是"一千个人眼里有一千个哈姆莱特"，不知道谁说的对。

后来慢慢发现，无论是在开发股票策略还是CTA策略，都不知不觉的使用了阻力和支撑的概念，比如说在做趋势策略之时，突破上轨做多，突破下轨做空，这个上下轨其实就类似于阻力线和支撑线，向上突破了阻力线后，广阔天地，大有可为，就开多仓，向下突破支撑线后，失去靠山，一泻千里，则开空仓或平仓。

![图片](https://mmbiz.qpic.cn/mmbiz_png/icBZQJaAUTJjPQgIia2uk5MhskL7u8PnwSBYFKNpZ3KAJicEB5O5rTZoN2X1eCXLsU2WNtoJCCnKC6h1ZMIbrvrqw/640?wx_fmt=png&tp=webp&wxfrom=5&wx_lazy=1#imgIndex=3)

有的时候，阻力线和支撑线并不是分开的两条线，也可以是一条线，这条线既可以是阻力线，也可以是支撑线。

就拿很多萌新入门常用的单均线策略来说，价格上穿20日均线做多，价格下穿20日均线做空，在这里，这根20日均线既是阻力线也是支撑线。价格在均线下方之时，均线便是阻力线，向上突破则做多，反之，价格在均线上方，此时均线则化身为支撑线，当价格失去支撑时则做空或平仓。

那问题来了，怎么找到阻力位和支撑位呢？听网上那些“专家”的预测吗？当然不是啦~

其实我们每天看K线图，“公认”的阻力和支撑就蕴含在里面，那就是K线的最高价和最低价，不要脸地说，这两个价格是经过万千交易者充分交易后的博弈结果，所有的成交价格都包含在了最高价和最低价形成的空间里，在最高价这条阻力线之下，在最低价这条支撑线之上。当然了，光用1天的最高价和最低价当然不行，可以用序列值。

![图片](https://mmbiz.qpic.cn/mmbiz_png/icBZQJaAUTJjPQgIia2uk5MhskL7u8PnwShFCibtYSyPU6mYRLDNdRQwBVoxR2MavdicNiabtgrHic6MtSo82QYz5mkw/640?wx_fmt=png&tp=webp&wxfrom=5&wx_lazy=1#imgIndex=4)

假设我们已经有了相对靠谱的阻力位和支撑位，那应该怎么使用呢？像上下轨突破策略那样使用吗？

可以换一个思路， **这就是RSRS的创新点所在，不直接使用阻力位和支撑位这种绝对阈值方式，改为使用相对强度的方式。**

就好比是，绝对阈值方式就是预测清华北大的学生能否将来年入百万千万，相对强度方式则是预测清华北大的学生收入将来是否超越双非院校的学生，这两者都不是绝对事件，但两者的预测难易程度一目了然，这个比方不是很恰当，是我能想到的最好的了，只是用来说明，让大伙儿更好地体会（惶恐狗头保命状ing）。

![图片](https://mmbiz.qpic.cn/mmbiz_png/icBZQJaAUTJjPQgIia2uk5MhskL7u8PnwSWKiazC7nPm9C3k0vibdY3XX2ac4zHKVZlqn3n55GRUsncWAQvqLnX1Aw/640?wx_fmt=png&tp=webp&wxfrom=5&wx_lazy=1#imgIndex=5)

**2.RSRS斜率指标和策略**

现在说清楚了阻力位和支撑位的代理变量，和指标构建的核心思想，那再来唠唠RSRS的具体计算步骤和细节。

![图片](https://mmbiz.qpic.cn/mmbiz_png/icBZQJaAUTJjPQgIia2uk5MhskL7u8PnwSsZTpdxNUmqadd5OjVQPfHeMjWicDsHaw2KK7icyjdKRM7MrKJe6rDV2A/640?wx_fmt=png&tp=webp&wxfrom=5&wx_lazy=1#imgIndex=6)

**首先，获取N日最高价和最低价的价格序列，然后，对最高价和最低价序列进行最小二乘法(OLS)线性回归，每日滚动进行，其中beta值就是斜率。**

```js
最高价 = alpha + beta×最低价
```

![图片](https://mmbiz.qpic.cn/mmbiz_png/icBZQJaAUTJjPQgIia2uk5MhskL7u8PnwSsWNCE9bprEWvbDIsHIrV7yr7BrXu9ve6EtLbB7efHXUndkjTnNt2TA/640?wx_fmt=png&tp=webp&wxfrom=5&wx_lazy=1#imgIndex=7)

其中斜率值beta表示最高价相对最低价位置变化的程度，也就是说，当最低价变化为1的时候，最高价变动多少。

当斜率值beta很大时，支撑强度大于阻力强度，从图形上看就是，最高价的变动速度比最低价的要快，阻力逐渐减小，上涨空间大。

![图片](https://mmbiz.qpic.cn/mmbiz_png/icBZQJaAUTJjPQgIia2uk5MhskL7u8PnwSprJicKebj6Gmab1hCqchZ716iaia9SPJvXIIsezEX7ljeZcMmpjh8ffUQ/640?wx_fmt=png#imgIndex=8)

当斜率值beta很小时，阻力强度大于支撑强度，从图形上看就是，最高价的变动速度比最低价的要慢，上涨逐渐减缓，势头受阻见顶。

![图片](https://mmbiz.qpic.cn/mmbiz_png/icBZQJaAUTJjPQgIia2uk5MhskL7u8PnwSt3W2vSNahGq7fbDSqu8lElnl5QSntbnjPBjbyHZ2wNm6ZHW8U35UiaQ/640?wx_fmt=png#imgIndex=9)

**最后，这个斜率值beta就会被作为当日的RSRS值，确切来说应该是“RSRS斜率指标值”，因为后文会对指标不断改进，RSRS的含义会更加多样丰富。**

RSRS的计算步骤和流程说完了，光说不练假把式，咱撸起袖子开干吧，从数据获取、指标计算和策略构建全部用代码实现和展示。

**第一步，对照原版研报，获取沪深300指数从2005年至今的开高低收行情数据，这里使用的是股票量化开源库qstock** ，“pip install qstock”安装后，基本的功能无需注册便可以使用，萌新使用起来也非常丝滑。

```kotlin
import qstock as qs
2005
data = qs.get_data(code_list=['HS300'], start='20050101', freq='d')[['open','high','low','close']]# 删除名称列、排序并去除空值data = data.sort_index().fillna(method='ffill').dropna()# 插入日期列data.insert(0, 'date', data.index)# 将日期从datetime格式转换为str格式data['date'] = data['date'].apply(lambda x: x.strftime('%Y-%m-%d'))# 按收盘价计算每日涨幅data['pct'] = data['close'] / data['close'].shift(1) - 1.0data = data.dropna().reset_index(drop=True)
print(data.head(5))print(data.tail(5))
```

![图片](https://mmbiz.qpic.cn/mmbiz_png/icBZQJaAUTJjPQgIia2uk5MhskL7u8PnwSicfk7EzbIFWbcXvZH7pQIHyic9AZAPLHPGlXsV9I0HbInZunp3nxzl5A/640?wx_fmt=png#imgIndex=10)

**第二步，这里的关键是计算每一日的斜率值beta** ，这里先给量化萌新说一个简单具体的例子，懂最小二乘法OLS的小伙伴可跳过。

假设有18个二维的数据点，横轴X轴的坐标是1~18的等差数列，纵轴Y轴的坐标依照y=2\*x\_noise+1生成，x\_noise是在横坐标x的基础上加入了随机数噪声，在这里，X轴数值对应的就是RSRS计算中的最低价，Y轴对应的就是最高价，具体分布如下。

```python
import numpy as npimport pandas as pdimport matplotlib.pyplot as plt#保证随机数生成的一致性

#数据点个数
1
x_noise = x + np.random.randn(N) #加入随机数噪声干扰1
print('x:', x)print('x_noise:', x_noise)print('y:', y)7
plt.scatter(x, y)plt.show()
```

![图片](https://mmbiz.qpic.cn/mmbiz_png/icBZQJaAUTJjPQgIia2uk5MhskL7u8PnwSNkmd9ciaicteTvuoibxVfak4zG7EnO4HGKsfOONxwMP1bfiatOibvFiayVsQ/640?wx_fmt=png#imgIndex=11)

虽然有噪声的干扰，咱都知道它们的底层关系就是一条二维直线y=beta\*x+alpha，其中beta=2是斜率，alpha=1是截距，最小二乘法OLS的作用就是根据已知的坐标数值，计算出斜率和截距。

在这里为了方(tou)便(lan)，咱还是直接从Python免费机器学习库Scikit-learn（简称sklearn）中导入LinearRegression求解，这里要注意的是，训练集必须是二维数组（矩阵）的形式，也就是每个样本对应的是一个向量，即使这个向量只有一个数值，这里使用reshape函数快速将n维向量转换为n x 1维矩阵。从最终结果看出，解出来的斜率为1.907，跟实际值还是非常接近的。

```makefile
from sklearn.linear_model import LinearRegression
lr = LinearRegression().fit(x.reshape(-1, 1), y)y_pred = lr.predict(x.reshape(-1, 1))beta = lr.coef_[0]alpha = lr.intercept_
print('斜率:', beta, '截距:', alpha)plt.figure(figsize=(7,7))plt.scatter(x, y)plt.plot(x, y_pred, color='red')plt.show()
```

![图片](https://mmbiz.qpic.cn/mmbiz_png/icBZQJaAUTJjPQgIia2uk5MhskL7u8PnwSROesSW9dvG5hd1CDHicWHfLIXYADUQkvuBJYgnMqxGezXzq9B8bZibZg/640?wx_fmt=png#imgIndex=12)

解单个序列的斜率值咱搞定了，在沪深300指数的行情数据上， **咱只需要每个交易日滑动(rolling)计算18个交易日最高价vs最低价的斜率就可以了，为什么N=18呢，因为这是原版研报中在2017年定的最优参数，本期文章以复现为主，因此尊重历史客观事实按照原始参数。**

```kotlin
def calculate_beta(df, window=18):    if df.shape[0] < window:        return np.nan    x = df['low'].values    y = df['high'].values    beta = LinearRegression().fit(x.reshape(-1, 1), y).coef_[0]    return beta
N = 18 #计算斜率时的数据点个数data['beta'] = [calculate_beta(df,window=N) for df in data.rolling(N)]
data.tail(20)
```

![图片](https://mmbiz.qpic.cn/mmbiz_png/icBZQJaAUTJjPQgIia2uk5MhskL7u8PnwSBh3RLLXx7JR09bdbtnVQw5ekFD6XMn5N5sMzZC2EqQ3G7kXxYtthMA/640?wx_fmt=png#imgIndex=13)

现在咱们有了历史上每个交易日的beta值，也就是RSRS值， **在这第三步里就可以构建针对大盘沪深300指数的量化择时策略了，这个策略的逻辑非常简单，就是“RSRS值大于1.0的时候，买入持有；RSRS值小于0.8，卖出平仓”，现实当中对应的交易标的可以是300ETF或IF股指期货。**

有的小伙伴可能会好奇，为什么买入阈值是1.0、卖出阈值是0.8呢？原文当中的确定方法是，根据RSRS均值加减一个标准差形成的。

![图片](https://mmbiz.qpic.cn/mmbiz_png/icBZQJaAUTJjPQgIia2uk5MhskL7u8PnwSGT3rjNNcx0psRTxb185qfWjr4mVibrbkWNZcS1EJyicHHveibqLc6diawg/640?wx_fmt=png#imgIndex=14)

重新统计一下目前的数据，统计值和斜率分布如下，发现RSRS均值还是在0.9左右，标准差也还是在0.1左右，故买入阈值仍然可以定为1.0，卖出阈值定为0.8。

```perl
print('均值：%.3f' %data['beta'].mean())print('标准差：%.3f' %data['beta'].std())print('偏度：%.3f' %data['beta'].skew())print('峰度：%.3f' %data['beta'].kurt())
y = list(range(200))8
plt.hist(data['beta'], bins=100)plt.plot(len(y)*[0.8], y, color='green', linestyle=':')plt.plot(len(y)*[1.0], y, color='red', linestyle=':')plt.show()
```

![图片](https://mmbiz.qpic.cn/mmbiz_png/icBZQJaAUTJjPQgIia2uk5MhskL7u8PnwSicdqKJeOeyicJKwhnetepRIXYiadR8MCxwNdzdRPpykr7C5YjvH9JK1Eg/640?wx_fmt=png#imgIndex=15)

买入卖出阈值确定后，RSRS值若大于1.0，买入并持有，RSRS值跌破0.8后，则卖出平仓，为了方(tou)便(lan)尊重原版研报不考虑费率影响，策略源码和回测曲线如下，总体下来比买入并一直持有基准指数要好。

```powershell
buy_thre = 1.0  # 买入阈值sell_thre = 0.8 # 卖出阈值data1 = data.dropna().copy().reset_index(drop=True)
data1['flag'] = 0 # 买卖标记，买入：1，卖出：-1data1['position'] = 0 # 持仓状态，持仓：1，不持仓：0position = 0 for i in range(1, data1.shape[0]-1):    beta = data1.loc[i,'beta']    if (position == 0) and (beta > buy_thre):        # 若之前无持仓，上穿买入阈值则买入        data1.loc[i,'flag'] = 1        data1.loc[i+1,'position'] = 1        position = 1    elif (position == 1) and (beta < sell_thre):         # 若之前有持仓，下穿卖出阈值则卖出        data1.loc[i,'flag'] = -1        data1.loc[i+1,'position'] = 0             position = 0    else:        # 不触发阈值，则保持原有持仓状态        data1.loc[i+1,'position'] = data1.loc[i,'position']     
# RSRS策略的日收益率data1['strategy_pct'] = data1['pct'] * data1['position']
#策略和沪深300的净值data1['strategy'] = (1.0 + data1['strategy_pct']).cumprod()data1['hs300'] = (1.0 + data1['pct']).cumprod()
# 粗略计算年化收益率annual_return = 100 * (pow(data1['strategy'].iloc[-1], 250/data1.shape[0]) - 1.0)print('RSRS斜率量化择时策略的年化收益率：%.2f%%' %annual_return)
#将索引从字符串转换为日期格式，方便展示data1.index = pd.to_datetime(data1['date'])ax = data1[['strategy','hs300']].plot(figsize=(16,8), color=['SteelBlue','Red'],                                      title='RSRS斜率量化指数择时策略净值  by 公众号【量化君也】')plt.show()
```

![图片](https://mmbiz.qpic.cn/mmbiz_png/icBZQJaAUTJjPQgIia2uk5MhskL7u8PnwSLh0c7mI0icbr8rBa76zqIJFmnCfTZrvXWoBth9crnIuELGoYvMYvxWQ/640?wx_fmt=png#imgIndex=16)

**3.RSRS标准分指标和策略**

但由于市场不同时期，斜率的均值（中枢位置）会有比较大的波动，季度均值(蓝线)和年度均值(红线)如下所示，因此使用固定数值作为买入卖出阈值则不太妥当。

![图片](https://mmbiz.qpic.cn/mmbiz_png/icBZQJaAUTJjPQgIia2uk5MhskL7u8PnwSnmYic7xBKrP7V3hQxY2Y9TFpgaKLVicBT1rKRB4PM9tdUPF8v8bJH7lw/640?wx_fmt=png#imgIndex=17)

于是乎， **研报当中提出了将原来的“RSRS斜率”转换为“RSRS标准分”，也就是在每个交易日，以M个交易日为观察期（默认M=600），将RSRS斜率做一个Z-Score标准化（即“（当前值-均值）/标准差”），便可以得到RSRS标准分** ，它能更加灵活地适应市场波动带来的斜率均值的变化。

![图片](https://mmbiz.qpic.cn/mmbiz_png/icBZQJaAUTJjPQgIia2uk5MhskL7u8PnwSue0ibqicTcnib3RzHFc2u87DJAKAURQBLXC8VEib5eIr2J4LdOQkgiaINCQ/640?wx_fmt=png#imgIndex=18)

有了RSRS标准分之后，便可以构建新策略，与之前的RSRS斜率策略类似， **当RSRS标准分大于0.7时，买入并持有，当RSRS标准分小于-0.7时，则卖出平仓** ，策略源码和回测净值曲线如下所示。

```powershell
# 观察周期
buy_thre = 0.7 # 买入阈值sell_thre = -0.7 # 卖出阈值
data2 = data.dropna().copy().reset_index(drop=True)# 计算标准分，如果当前时间长度不够，则使用至少20交易日数据计算data2['std_score'] = (data2['beta'] - data2['beta'].rolling(M, min_periods=20).mean())/data2['beta'].rolling(M, min_periods=20).std()
data2['flag'] = 0 # 买卖标记，买入：1，卖出：-1data2['position'] = 0 # 持仓状态，持仓：1，不持仓：0position = 0 for i in range(1, data2.shape[0]-1):    std_score = data2.loc[i,'std_score']    if (position == 0) and (std_score > buy_thre):        # 若之前无持仓，上穿买入阈值则买入        data2.loc[i,'flag'] = 1        data2.loc[i+1,'position'] = 1        position = 1    elif (position == 1) and (std_score < sell_thre):         # 若之前有持仓，下穿卖出阈值则卖出        data2.loc[i,'flag'] = -1        data2.loc[i+1,'position'] = 0             position = 0    else:        # 不触发阈值，则保持原有持仓状态        data2.loc[i+1,'position'] = data2.loc[i,'position']     
# RSRS策略的日收益率data2['strategy_pct'] = data2['pct'] * data2['position']
#策略和沪深300的净值data2['strategy'] = (1.0 + data2['strategy_pct']).cumprod()data2['hs300'] = (1.0 + data2['pct']).cumprod()
# 粗略计算年化收益率annual_return = 100 * (pow(data2['strategy'].iloc[-1], 250/data2.shape[0]) - 1.0)print('RSRS标准分量化择时策略的年化收益率：%.2f%%' %annual_return)
#将索引从字符串转换为日期格式，方便展示data2.index = pd.to_datetime(data2['date'])ax = data2[['strategy','hs300']].plot(figsize=(16,8), color=['SteelBlue','Red'],                                      title='RSRS标准分量化指数择时策略净值  by 公众号【量化君也】')plt.show()
```

![图片](https://mmbiz.qpic.cn/mmbiz_png/icBZQJaAUTJjPQgIia2uk5MhskL7u8PnwS5Ar8I6vFuIKmf6biamdfhzqCVY47dSxuENsGM5P1zRaeicnCanruDE6Q/640?wx_fmt=png#imgIndex=19)

RSRS标准分策略看起来要比RSRS斜率策略要好，咱把它们和基准画在一张图上进行对比，这种优秀就更明显了。

```php
data_merge = pd.merge(data1[['date','strategy']].rename(columns={'strategy':'RSRS斜率策略'}),                      data2[['strategy','hs300']].rename(columns={'strategy':'RSRS标准分策略'}),                      left_index=True, right_index=True, how='inner')data_merge.index = pd.to_datetime(data_merge['date'])ax = data_merge[['RSRS斜率策略','RSRS标准分策略','hs300']].plot(figsize=(16,8),                 color=['Yellow','SteelBlue','Red'], title='RSRS量化择时策略对比 by 公众号【量化君也】')plt.show()
```

![图片](https://mmbiz.qpic.cn/mmbiz_png/icBZQJaAUTJjPQgIia2uk5MhskL7u8PnwS4KUlLfIsLeibibWUuRjuR3LuQuJU6vwDPfhFvhbQzibYfklQAAEgiadosw/640?wx_fmt=png#imgIndex=20)

咱把研报中的RSRS策略对比图也找出来看看，研报中的数据是截止到2017年4月，当时RSRS斜率策略的累计净值是在10.57，RSRS标准分策略的累计净值是在13.37，无论是走势还是数值，总体上还是比较接近的，算是能复现出个大概了。

![图片](https://mmbiz.qpic.cn/mmbiz_png/icBZQJaAUTJjPQgIia2uk5MhskL7u8PnwSz7LRzYia6KdknicR9kMzuFDFchuRKJhHhocW1muhPT40Q4WYib7dee5YA/640?wx_fmt=png#imgIndex=21)

**4.补充和总结**

需要补充的是，原始研报中可能隐含了两处“未来函数”，第一处是买入卖出阈值的确定，文中是统计了全部数据集的数值（例如斜率值beta）分布再确定阈值的，相当于是用训练集训练模型，然后又让模型预测训练集。

第二处就是买卖时点的确定，当天出信号之后当日收盘价成交，虽然只要当日K线不出现“光头”或“光脚”，可以大概率近似实现，但与实盘情况还是有一定差距，只是回测起来非常方便。原版研报当中没有明说，仅为个人猜测和看法，因为这种方式回测结果与研报最接近。

总体来说整篇研报还是瑕不掩瑜，RSRS指标带有一定的创新性，不少小伙伴看了都觉得有启发，本次重点是在“复现”，于是也遵从了这两处设定。

到这里，基本的RSRS策略就已经复现完毕了，幸好总体结果跟原始研报还是一致的，暂时还没有翻车，希望可以给小伙伴们说清楚一些RSRS指标策略具体的计算细节，也让大伙儿少走一些弯路，节省一些精力。 **如果对你有帮助，可以点个充满鼓励的『赞』告诉我，接着把RSRS后续系列肝完。**

**参考资料**

*光大金工，2017.5，《技术择时系列报告之一：基于阻力支撑相对强度（RSRS）的市场择时》*

*光大金工，2017.6，《技术择时系列报告之二：阻力支撑相对强度（RSRS）择时及行业轮动》*

*光大金工，2017.7，《技术择时系列报告之三：阻力支撑相对强度（RSRS）选股》*

*光大金工，2018.3，《技术择时系列报告之五：基于RSRS策略改进的资产配置研究》*

*光大金工，2019.11，《技术择时系列报告之六：RSRS择时：回顾与改进》*

★

往期回顾

★

\------量化社群------

\------量化策略------

[期货Alpha](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247484095&idx=1&sn=56d3df957c23f8043667b9fa190d1a36&scene=21#wechat_redirect) [跨品种套利](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247484021&idx=1&sn=de75d6fb7b8e30c4e6a6b465ed608791&scene=21#wechat_redirect) [GARP策略](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247484628&idx=1&sn=91adbe6e039e86324b2136f733fb4e72&scene=21#wechat_redirect)

[绩优小市值](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247484219&idx=1&sn=b4b6b583d379ec5920807d580559578a&scene=21#wechat_redirect) [漂亮50](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247483810&idx=1&sn=cbf7c998e8b95f029bd98d16b75a89ce&scene=21#wechat_redirect) [操盘手](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247484010&idx=1&sn=8f425ffec08b044aff03cf8a1f51b16b&scene=21#wechat_redirect) [Rumi](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247484064&idx=1&sn=cfd99a47728f889692845ccb7b0a099d&scene=21#wechat_redirect)

[AI择时](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247484565&idx=1&sn=9fedbb0b8904fac5e4cb6df582e94bf8&scene=21#wechat_redirect) [K线面积法](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247484161&idx=1&sn=85b980eb19f4d016b7f1a42ffa9bf7a5&scene=21#wechat_redirect) [零编程策略](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247484518&idx=1&sn=24270a92ae7e4aada59981a479adf38e&scene=21#wechat_redirect)

[贴水策略](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247484405&idx=1&sn=664567f274c737278867402e0b2277c2&scene=21#wechat_redirect) [概率密度策略](http://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247484675&idx=1&sn=e8a5e701e58ddb2e34db793f0ec59d9c&chksm=c21ba68cf56c2f9ab4d37d956e70250dfd59af25b636681f8fae8d9557e4c639e45c1633429d&scene=21#wechat_redirect) [一致预期](http://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247484960&idx=1&sn=bacb21875c2a4b377a7d35c47d03b21b&chksm=c21ba5aff56c2cb97a55bab6a7630d20e5cf19fb9f6743d9e8c49f186f19d6a5e3ff666c43d7&scene=21#wechat_redirect)

\------心得杂谈------

[年化577倍](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247484340&idx=1&sn=b415703a642e2b3c1a04481af017108f&scene=21#wechat_redirect) [抄底&摸顶](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247484287&idx=1&sn=5de0c792a1d7a56bf8867656d919c07e&scene=21#wechat_redirect) [策略开发](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247483999&idx=1&sn=1c77888217e83b4dab4961bc2b3b8ce5&scene=21#wechat_redirect)

[量化入门](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247484482&idx=1&sn=7b98097f0a0a48728aeec452a834f1fc&scene=21#wechat_redirect) [量化神作](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247483706&idx=1&sn=7c45148b63cd2afd102da9da08609073&scene=21#wechat_redirect) [量化书单](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247483782&idx=1&sn=b80c2ee25c6f9f8fd88b7c1dee9513ef&scene=21#wechat_redirect) [他](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247483840&idx=1&sn=8cce9b5875f11d57945a1666f0e03591&scene=21#wechat_redirect)

[个人量化](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247484469&idx=1&sn=ecfdb2b3f3e723fd417c0bddbd957b6b&scene=21#wechat_redirect) [量化误解](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247484138&idx=1&sn=d254513ad26c1872127bf7287b00d3f3&scene=21#wechat_redirect) [高收入背后](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247484426&idx=1&sn=e0d282978280a65b4d3270c050fe71bf&scene=21#wechat_redirect)

[未来函数](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247484081&idx=1&sn=6076ced2c2418de2d8e5d77f2162ea07&scene=21#wechat_redirect) [回测&过拟合](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247484577&idx=1&sn=6492bc8164649e3d85d015410c9db8a6&scene=21#wechat_redirect) [回测&实盘](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247484586&idx=1&sn=545202b3c6a10f87e5f03be0f00a2cb2&scene=21#wechat_redirect)

[资金流](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247484049&idx=1&sn=78b94c8055e6822e949180d942b00058&scene=21#wechat_redirect) [吃贴水](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247484388&idx=1&sn=f165f6ca0ab5c0e36fc320e4dbf0e8e0&scene=21#wechat_redirect) [回测提速](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247483736&idx=1&sn=334f2395a881328014c2f2bc1568e89b&scene=21#wechat_redirect) [量价背离](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247483796&idx=1&sn=0f783208f9dd1994a21964b715bdf63e&scene=21#wechat_redirect)

[自学路径](http://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247484712&idx=1&sn=9fdf003c783b4bc053b90736ebbb8435&chksm=c21ba6a7f56c2fb1e87cf876cba0c2620244840c339666072447a0c14516dbeb686ca2e65702&scene=21#wechat_redirect) [文章合辑](http://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247484689&idx=1&sn=1e1c6ad82946f9abd0cada5e0092b517&chksm=c21ba69ef56c2f883068c5dbb559d0b61b206b16f32afd908ea12a3cb93d2fbaddea5e1ac3d8&scene=21#wechat_redirect) [151个策略](http://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247484775&idx=1&sn=90a7e0d3786f9dd2d97a7ba95ec69318&chksm=c21ba6e8f56c2ffe64eb9fe0d2f30ba8460d53997401f00b79a91e5981a9ba2ab21a000e4d2b&scene=21#wechat_redirect)

[5年116倍](http://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247484971&idx=1&sn=b1406abf16c51e8f44d02182413bce2b&chksm=c21ba5a4f56c2cb2b43b76b539294677b0028c85d60a3543d502bcd5b869a01a6fbcdce39ac4&scene=21#wechat_redirect) [量化编程神器](http://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247485006&idx=1&sn=ec989b1a9f2d74f669509dc7ce561710&chksm=c21ba5c1f56c2cd7e02fba70dab2e8236ddbc6aa05d6cf7ad7fc9205c6fe1f4f7f29461d6f6b&scene=21#wechat_redirect)

*Tip：点击关键字可以直接查看对应文章。*

END

如果对本文有疑惑，或是想聊聊

亦或是围观朋友圈当点赞之交

点我，让我们一路同行

吃瓜吐槽写代码

![图片](https://mmbiz.qpic.cn/mmbiz_jpg/icBZQJaAUTJgFgp6HLKHEgXSU3szYABVVJrQMf3TE40nico0ADzRKPpP6PSlJCVEu4QWjHQ7tPsyLTqxEibMKWOJg/640?wx_fmt=jpeg#imgIndex=22)

(微信号:iquantman)

添加好友后，私信『666』

送你一些量化小福利

人工回复慢请见谅~

![图片](https://mmbiz.qpic.cn/mmbiz_png/icBZQJaAUTJgqDZXmAly0icYbAldqOjSEgiaibX8ibveeZ6amu1ehamzOwHjOqbFpDygZpqHSeP4reWU3MLiaug8Nwtg/640?wx_fmt=png#imgIndex=23)

量化交易 · 目录