---
title: "ETF轮动策略在阻力支撑相对强度RSRS指标加持下起飞（年化91.6%）"
source: "https://mp.weixin.qq.com/s/_8Ogv_VZyqZJbpCzwR3FuQ"
author:
  - "[[量化君]]"
published:
created: 2026-07-05
description: "不预测阻力和支撑的具体价位，只度量它们之间的相对强度~"
tags:
  - "clippings"
---
量化君 量化君也 *2021年12月28日 22:35*

![图片](https://mmbiz.qpic.cn/mmbiz_png/icBZQJaAUTJiac8IbeKmzNQ1RicdXbOzLzy9jfu3JK3sCf2tQ5Ku5yzT1Xqibt21ahx3ibmF3lrBOSFZEVOBibl4dMxg/640?wx_fmt=png&tp=webp&wxfrom=5&wx_lazy=1#imgIndex=0)

之前更新(shui)过一篇文章 [《唠一唠指标之王MACD的另类用法：高低形态短线量化策略》](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247483870&idx=1&sn=8936877f7f597a2bbb9ef8e2ec9887cb&scene=21#wechat_redirect) ，主要是因为这个指标被广泛应用，横跨股票/期货/期权/外汇/数字货币领域，基本上是妇孺皆知了。

这段时间观察量化圈，发现在大小盘择时、ETF轮动等相对类型的策略上，有一个指标跟MACD的使用频次类似、同时还带有一丝创新意味的指标，研究使用的人还真不少，那就是阻力支撑相对强度（ **R** esistance **S** upport **R** elative **S** trength）指标，看到这个名字有些小伙伴觉得很陌生，说它的简称『RSRS』，有的小伙伴就可能想起来了。

它的设计思想诞生于光大证券的金工研报《基于阻力支撑相对强度的市场择时》，这个系列的研报有好几篇，目录放在文末参考资料那里了，具体的计算细节请参照原版研报，如果只想听个大概思想的话，暂且听我闲话唠一唠。

![图片](https://mmbiz.qpic.cn/mmbiz_jpg/icBZQJaAUTJjEib5p5HHlfHduoGdKFAuVeN4KFoHUdKGdpCdtND26iacjtXGbbyPvibSoia2NaicaNibW8I9mKL5rvXXw/640?wx_fmt=jpeg&tp=webp&wxfrom=5&wx_lazy=1#imgIndex=1)

闲聊开始～

刚开始做交易的时候，总会听到一些"专家"预测点位，说大盘的阻力位在哪，说某只股票的支撑位在哪，各有各的理由，众说纷纭，但是预测的点位也是"一千个人眼里有一千个哈姆莱特"，不知道谁说的对。

后来慢慢发现，无论是在开发股票策略还是CTA策略，都不知不觉的使用了阻力和支撑的概念，比如说在做趋势策略之时，突破上轨做多，突破下轨做空，这个上下轨其实就类似于阻力线和支撑线，向上突破了阻力线后，广阔天地，大有可为，就开多仓，向下突破支撑线后，失去靠山，一泻千里，则开空仓或平仓。

![图片](https://mmbiz.qpic.cn/mmbiz_jpg/icBZQJaAUTJjEib5p5HHlfHduoGdKFAuVezsWkVlibaQxFE7zTzib41gpAeESRMMAOzViaHYau5bC9BGgVGGbqAAwlg/640?wx_fmt=jpeg#imgIndex=2)

有的时候，阻力线和支撑线并不是分开的两条线，也可以是一条线，这条线既可以是阻力线，也可以是支撑线。

就拿很多萌新入门常用的单均线策略来说，价格上穿20日均线做多，价格下穿20日均线做空，在这里，这根20日均线既是阻力线也是支撑线。价格在均线下方之时，均线便是阻力线，向上突破则做多，反之，价格在均线上方，此时均线则化身为支撑线，当价格失去支撑时则做空或平仓。

那问题来了，怎么找到阻力位和支撑位呢？听网上那些“专家”的预测吗？当然不是啦~

其实我们每天看K线图，“公认”的阻力和支撑就蕴含在里面，那就是K线的最高价和最低价，不要脸地说，这两个价格是经过万千交易者充分交易后的博弈结果，所有的成交价格都包含在了最高价和最低价形成的空间里，在最高价这条阻力线之下，在最低价这条支撑线之上。当然了，光用1天的最高价和最低价当然不行，可以用序列值。

![图片](https://mmbiz.qpic.cn/mmbiz_png/icBZQJaAUTJjEib5p5HHlfHduoGdKFAuVeO3rkPtcG0xWuibfLWAELfrMgkuofW7TR6f98HwlxS9wfI0p0SZ0mm9A/640?wx_fmt=png#imgIndex=3)

假设我们已经有了相对靠谱的阻力位和支撑位，那应该怎么使用呢？像上下轨突破策略那样使用吗？

可以换一个思路， **这就是RSRS的创新点所在，不直接使用阻力位和支撑位这种绝对阈值方式** **，改为使用相对强度的方式。**

就好比是，绝对阈值方式就是预测清华北大的学生能否将来年入百万千万，相对强度方式则是预测清华北大的学生收入将来是否超越双非院校的学生，这两者都不是绝对事件，但两者的预测难易程度一目了然，这个比方不是很恰当，是我能想到的最好的了，只是用来说明，让大伙儿更好地体会，狗头保命。

![图片](https://mmbiz.qpic.cn/mmbiz_jpg/icBZQJaAUTJjEib5p5HHlfHduoGdKFAuVebaoZ0HQPUdAYxTPwBxulJcCuI0uDYbwcPOmDDQIxBWUFHcSQP2MLpQ/640?wx_fmt=jpeg#imgIndex=4)

现在说清楚了阻力位和支撑位的代理变量，和指标构建的核心思想，那再来唠唠RSRS的具体计算步骤和细节。

首先，像刚才所说的，我们用最高价和最低价作为阻力位和支撑位的代理变量，因此第一步就要获取N日最高价和最低价的数值序列。

然后，对最高价和最低价序列进行线性回归，每日滚动进行。

```js
最高价 = α + β × 最低价
```

其中斜率值β就是最高价相对最低价位置变化的程度，也就是说，当最低价变化为1的时候，最高价变动多少。

当斜率值β很大时，支撑强度大于阻力强度，从图形上看就是，最高价的变动速度比最低价的要快，阻力逐渐减小，上涨空间大。

![图片](https://mmbiz.qpic.cn/mmbiz_png/icBZQJaAUTJjEib5p5HHlfHduoGdKFAuVetoMB1BFtotawZTdf3Yx3hFXSia4ux532dUwxUwHWVZcqy6H4ysYtYmw/640?wx_fmt=png#imgIndex=5)

当斜率值β很小时，阻力强度大于支撑强度，从图形上看就是，最高价的变动速度比最低价的要慢，上涨逐渐减缓，势头受阻见顶。

![图片](https://mmbiz.qpic.cn/mmbiz_png/icBZQJaAUTJjEib5p5HHlfHduoGdKFAuVecTVZZaiapszKC39BwOB5MzIbFZiaE9KEBiccr7AphkYeV5MtZfv7DUibNw/640?wx_fmt=png#imgIndex=6)

但是每个股票/品种/合约，它们的斜率都是不一样的，比如说1.0到底是大还是小，好比说成交额5亿对很多小票来说算大的了，但是对茅台来说可是不够看的，所以不能用绝对数值做考量。

由于RSRS是用来择时的，因此可以用自己的当前值跟自己的历史值做比较，然后做一个Z-Score标准化。也就是说，取M个斜率值β组成一个序列，算出这个序列的均值和标准差，然后标准分就等于（当前值-均值）/标准差，得到了这个标准分，那就是RSRS的指标值了。

RSRS指标值计算出来了，那就是如何使用这个指标呢？

由于RSRS指标本身已经经过标准化处理，数据分布满足均值为0，标准差为1,，最简单最朴素的做法就是，当RSRS大于阈值S时做多，RSRS小于阈值-S时做空。

因此，小结一下，RSRS的计算和使用流程跟把大象塞进冰箱里是一样的，如下：

**第一步：获取N日最高价和最低价数据序列，采用线性回归，计算出斜率β值。**

```js
最高价 = α + β × 最低价
```

**第二步：取最近M日的斜率β值序列，采用Z-Score法计算出RSRS值。**

**第三步：当RSRS大于阈值S时做多，RSRS小于阈值-S时做空。**

**这就是一个基础RSRS策略的构建方法，其中参数N、M、S，在不同的领域数值大小不同，从目前看过的RSRS相关策略来说，N一般取值在10~60，M取值一般在240~720，S值一般取值在0.6~0.8，大伙儿根据自己的领域进行调试。**

![图片](https://mmbiz.qpic.cn/mmbiz_jpg/icBZQJaAUTJjEib5p5HHlfHduoGdKFAuVeHqmmv7nqibOAh4XwibsyFEmoSPRicYkc1oHmbMLA0uLVMBUROo0GicyRNw/640?wx_fmt=jpeg#imgIndex=7)

说说最近看到的比较有趣的RSRS策略，这是一个EFF轮动的策略，本来的策略思想很朴素，就基本上是一个动量策略，在ETF备选池当中选择近期动量最强的ETF持有， **但是大神后来拿RSRS来做一个开关策略（对大盘择时），只有在RSRS出现做多信号的时候（市场氛围良好）** ，才会开仓买入/持有动量最强的ETF，绩效增强不少。

最近几年策略表现不错，年化有91.6%，最大回撤有11.4%，效果看上去不赖，但用的是最原始的RSRS指标，其实光大金工后续还有对RSRS进行改进和优化，在原来的基础上加入了右偏修正和成交额加权钝化，还可以进行更深入的探究和优化。

**适合PC端查看的横版：**

![图片](https://mmbiz.qpic.cn/mmbiz_png/icBZQJaAUTJjEib5p5HHlfHduoGdKFAuVeFW4xWfKickGaoYYvtfoCbPhu7MfrYEUo8M33c1CLxv6P0NWVuLwOpBA/640?wx_fmt=png#imgIndex=8)

**适合手机端的竖版：**

![图片](https://mmbiz.qpic.cn/mmbiz_png/icBZQJaAUTJjEib5p5HHlfHduoGdKFAuVeZ7UdicOuiaKgSibNXykzoRgibN9Pbicb9nxF9LzwTTJ9WwBn2awok4muuyQ/640?wx_fmt=png#imgIndex=9)

**参考资料：**

*光大金工，2017.5，《技术择时系列报告之一：基于阻力支撑相对强度（RSRS）的市场择时》*

*光大金工，2017.6，《技术择时系列报告之二：阻力支撑相对强度（RSRS）择时及行业轮动》*

*光大金工，2017.7，《技术择时系列报告之三：阻力支撑相对强度（RSRS）选股》*

*光大金工，2018.3，《技术择时系列报告之五：基于 RSRS 策略改进的资产配置研究》*

*光大金工，201911，《技术择时系列报告之六：RSRS 择时：回顾与改进》*

*PS：本策略测试源码已经分享到策略群内（目前已分享200+套策略），群友请原路径自取，祝好祝顺！*

END

如果对本文有疑惑，或是想聊聊

亦或是围观朋友圈当点赞之交

点我，让我们一路同行

吃瓜吐槽写代码

![图片](https://mmbiz.qpic.cn/mmbiz_jpg/icBZQJaAUTJjEib5p5HHlfHduoGdKFAuVeRo5icy2dK27Cib0ib2DK49D7eVniaEENY9RRR2jqlUM2UtccicRREfkMv3w/640?wx_fmt=jpeg#imgIndex=10)

(微信号:iquantman)

量化投资 · 目录