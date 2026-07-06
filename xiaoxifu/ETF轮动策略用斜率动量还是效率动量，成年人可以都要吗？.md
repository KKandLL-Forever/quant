---
title: "ETF轮动策略用斜率动量还是效率动量，成年人可以都要吗？"
source: "https://mp.weixin.qq.com/s/i07JWL_jEtnJsBl6RLtNoA"
author:
  - "[[量化君]]"
published:
created: 2026-07-05
description: "成年人真的是都要..."
tags:
  - "clippings"
---
量化君 量化君也 *2025年12月26日 07:07*

![图片](https://mmbiz.qpic.cn/mmbiz_png/icBZQJaAUTJiac8IbeKmzNQ1RicdXbOzLzy9jfu3JK3sCf2tQ5Ku5yzT1Xqibt21ahx3ibmF3lrBOSFZEVOBibl4dMxg/640?wx_fmt=png&tp=webp&wxfrom=5&wx_lazy=1#imgIndex=0)

之前分享了两个ETF轮动策略的思路，一个斜率动量，另一个是效率动量，就有萌新小伙伴问我，是斜率动量好还是效率动量好，我回答说各有各的好。

选策略就跟找老婆是一样的，没有最好的，只有适合自己的，如人饮水冷暖自知。有的萌新小伙伴听完就纠结了，还是不知道选哪一个，我想说的是，成年人嘛，拿不准的话，可以都要的，中国有“重婚罪”，又没有“重策罪”。

![图片](https://mmbiz.qpic.cn/mmbiz_png/icBZQJaAUTJglhtJRiaSOcic7DrrexLsIMgdLicYiatUAZjubgwPrI6ib3Gkeiaa0D3yHJCibibe293ORFq6iacWM20u9Fvw/640?wx_fmt=png&from=appmsg&watermark=1&tp=webp&wxfrom=5&wx_lazy=1#imgIndex=1)

你可以两个策略同时跑，每个策略都分配一定的仓位，也可以把两个策略合二为一，斜率和效率都利用起来，前者称为“双ETF动量轮动策略”，搞起来很简单，直接导入策略同时开跑就行了，后者称为“ETF双动量轮动策略”，需要做源码级别的修改，就稍微麻烦一些。

志不求易者成，事不避难者进，今儿个就搞“ETF双动量轮动策略”，看看效果到底如何，在此之前，先来回顾一下斜率动量和效率动量的思路和至今的表现。

斜率动量是借鉴了光大金工的网红RSRS指标的构建思路，用收盘价序列的斜率来表征，斜率越大，ETF的走势越猛越强。

同时也引入决定系数R2的概念，它是对线性拟合效果好坏的判断指标，取值范围一般在0~1之间，数值越大，表示线性拟合的效果就越好。

因此顺其自然将两者结合起来，“斜率动量 = 斜率 x 决定系数”，作为ETF动量强弱的趋势得分，得分数值越高，就表示ETF动量越强，在轮动策略中每日都选入得分最高的ETF，详细的说明可以看 [《](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247486859&idx=1&sn=0b2e608827dab3d9a45def5c952e537c&scene=21#wechat_redirect) [手把手教你构建与改进ETF轮动策略（十年19倍，附源码）](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247486859&idx=1&sn=0b2e608827dab3d9a45def5c952e537c&scene=21#wechat_redirect) [》](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247486859&idx=1&sn=0b2e608827dab3d9a45def5c952e537c&scene=21#wechat_redirect) 这篇文章，策略回测绩效如下所示。

![图片](https://mmbiz.qpic.cn/mmbiz_png/icBZQJaAUTJglhtJRiaSOcic7DrrexLsIMgbQxVSonNFOGy9wiaXiaRcklalIjdAjOGgfpwKptKenPoEBdzOQzchSicQ/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=2)

效率动量来源于“效率系数”这个概念，它出自于美国交易专家佩里·J·考夫曼发明的自适应均线AMA (Adaptive Moving Average)，AMA其实由两条均线复合而成，一条短期均线，另一条则是长期均线，效率系数的作用就是在市场单边趋势时，让AMA更偏向于短期均线，在震荡时，则偏向于长期均线。

效率系数的计算也不复杂，用中学物理来打比方，效率系数就是等于“位移/路程”，就好比你用高德来导航，与目的地之间的直线距离是20公里，但是你开车过去整个车程就可能是40公里，前者就是位移，后者就是路程，你这趟开车的效率系数就是0.5。如果你遇上的是盘龙古道，那效率系数铁定是低于0.1了。

![图片](https://mmbiz.qpic.cn/mmbiz_jpg/icBZQJaAUTJglhtJRiaSOcic7DrrexLsIMgiaO6wzImsY3UOXwGvibBdpkNgGfUtAj6D401qlKYawXKD34njP1rnyWQ/640?wx_fmt=jpeg&watermark=1#imgIndex=4)

当ETF呈现单边强劲趋势时，ETF价格几乎直线运动，价格位移与途经路程相差无几，效率系数就接近1.0；当ETF处于无序震荡时，价格上下反复，很可能最终回到原点，价格位移变动很小，效率系数就接近0。

因此，另一个ETF趋势的衡量版本就是，“效率动量 = 区间涨跌幅 x 效率系数”，详细说明可见 [《](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247486941&idx=1&sn=f30e448c361fa7e89c6c5e4fab1ed5cc&scene=21#wechat_redirect) [十年多赚2400%，ETF轮动策略都经历了哪些改进》](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247486941&idx=1&sn=f30e448c361fa7e89c6c5e4fab1ed5cc&scene=21#wechat_redirect) 这篇文章，策略回测绩效如下所示。

![图片](https://mmbiz.qpic.cn/mmbiz_png/icBZQJaAUTJglhtJRiaSOcic7DrrexLsIMgOQ9l4kd6p4xIPxK2OyEqspXsESiaecGDNleB4QJlia7xX3asX6HhKlQw/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=5)

从2015年至今的回测中可以看出，斜率动量策略年化收益为28.05%，夏普率为1.0，最大回撤为31.30%，效率动量策略年化收益为39.21%，夏普率为1.39，最大回撤为26.05%。

从收益和回撤上看，效率动量仿佛是完全优于斜率动量的，咱再从交易频率上看，从2015年至今的11年间，斜率动量策略开平仓160次，效率动量策略开平仓347次，可以看出来，效率动量策略的交易频率是斜率动量的2倍还多，斜率策略平均一个月交易1.2次，效率策略则是2.6次。

效率策略的高收益是因其更高的敏感性换来的，但这也会带来一个缺点，就是在震荡行情当中，交易次数会高很多，实盘中被打脸的次数也会更高，这个时候的持仓体验就差很多。因此，斜率动量适合捕捉整体大趋势的交易者，而效率动量更适合喜欢敏感小波段的交易者。

现在，咱通过排名加权的方式将这两个策略合二为一形成一个策略，在实操当中，先给ETF池中的所有ETF分别计算出斜率动量和效率动量的排名，然后按照设定的权重(默认是等权)，将两个排名加总在一起形成综合排名，最后选择综合排名最高的ETF，按照这个思路，再走一遍回测，绩效如下所示。

![图片](https://mmbiz.qpic.cn/mmbiz_png/icBZQJaAUTJglhtJRiaSOcic7DrrexLsIMgMic6EHXxzwZaM5ZPMaQuevfOMwwkQIfLx5v7Ivc5tWvMg8dngVekUxA/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=7)

从中可以看出，这个ETF双动量轮动策略的年化收益是35.49%，夏普率为1.22，最大回撤为27.84%，期间开平仓277次，相较于原来的效率动量策略来说，虽然收益下降了，但是以79.8%(277/347)的交易次数获取到了原来90.5%(35.49%/39.21%)的收益，持仓体验会更好一些。

在群友的建议下，我还在当中加入了一个“最小持仓天数”的设置，也就是说一定要持有那么多个交易日才会卖出，以此来降低交易频率，相配套地，还加入了一个“强制卖出排名阈值”，如果当前的持仓ETF排名跌破了这个阈值，即使还未持有满“最小持仓天数”，也可以被卖出，咱来看一下“最小持仓天数=3”和“强制卖出排名阈值=3”的回测情况。

![图片](https://mmbiz.qpic.cn/mmbiz_png/icBZQJaAUTJglhtJRiaSOcic7DrrexLsIMgZT2tPSzIJ92PScEFFia7oV0iapcbeSyfxRLJ6brOiaxch4XZEt6FfPcHA/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=9)

可以看到，加了冷却期的ETF双动量轮动策略的收益更低了，年化收益为32.47%，夏普率为1.11，最大回撤为26.49%，期间开平仓235次，相较于原来的效率动量策略来说，以67.7%(235/347)的交易次数获取到了原来82.8%(32.47%/39.21%)的收益。

ETF双动量轮动策略回测的情况说完了，如果有想要继续深入探究的小伙伴，下面我给你们说一下实盘参数的设置要点。 ![图片](https://mmbiz.qpic.cn/mmbiz_png/icBZQJaAUTJglhtJRiaSOcic7DrrexLsIMgwUohVtuvVva3taUriaeMYXF1C3BxGAoDRicJhib6zLdfbXic53o9Z0m5ug/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=11)

ACCOUNT\_ID：就是你自己的股票账户的资金账号，回测的时候可以不填或乱填，实盘的时候一定要填自己正确的账号。

ACCOUNT\_TYPE：因为要使用股票账户进行交易，所以该参数固定为“STOCK”。

ACCOUNT\_MODE：就是你打算用多少钱来跑这个策略，可选值为MONEY和RATIO。选MONEY就表示按照ACCOUNT\_MONEY设定的金额，选RATIO就表示使用总账户ACCOUNT\_RATIO那么多比例的资金。

ACCOUNT\_MONEY：策略金额，单位是“元”，根据自己的资金情况设置。

ACCOUNT\_RATIO：占总账户资金的比例，数值在0~1.0之间，0.3表示占30%比例。

STRATEGY\_TRADETIME：策略进行交易的时间，因为该策略是日线策略，一天那么长，所以需要指定一个具体的时间进行下单交易。

ORDER\_TIMEOUT：订单超时时间，默认是60秒，下单后超过60秒没有全部成交就是超时，策略程序会自动检查出超时的委托单，然后撤单重下。

STRATEGY\_PATH：策略相关文件的存储路径，策略程序会在STRATEGY\_PATH这个路径底下再新建一个名为STRATEGY\_NAME的文件夹，策略相关的持仓文件和交易日志文件都会保存在这个文件夹底下，这些文件是做仓位隔离和信息回溯的关键。

STRATEGY\_NAME：策略名称，一旦开启实盘之后，策略名称不要随意修改，不然就无法识别策略持仓文件，如果在盘中修改然后重启策略的话，就识别不了修改之前下的委托单和成交单。特别说明就是，策略名称除了中文和英文之外，不要含有任何特殊字符，不然就无法正确识别券商柜台的委托回报。

CODE\_LIST：ETF候选池的代码列表，你也可以把自己想轮动的ETF加入进去。

SLOPE\_N\_DAYS：斜率动量的计算长度，数值表示交易日天数。

EFFICIENCY\_N\_DAYS：效率动量计算长度，数值表示交易日天数。

SLOPE\_WEIGHT：斜率动量排名的权重，用来生成综合排名。

EFFICIENCY\_WEIGHT：效率动量排名权重，用来生成综合排名。

SELECT\_NUM：每一次选择多少个ETF作为目标持仓，这里默认是1个。

MIN\_HOLD\_DAYS：表示每个ETF最少要持有多少个交易日，设为0表示不限制，一旦不是趋势最优的ETF就可以卖出。

FORCE\_SELL\_RANK：强制卖出排名阈值，与MIN\_HOLD\_DAYS相配合，即使在冷却期内，如果排名跌到这个位置或更差，也会强制卖出，设置为0表示不启用强制卖出，即冷却期内无论排名多差都不卖。

本次ETF双动量轮动策略的回测/实盘源代码，已经分享在『量化达摩院』社群当中，群友请原路径自取，还不会使用QMT进行策略回测和实盘的小伙伴，请参照社群知识库第二章的第3和第4部分进行操作。

![图片](https://mmbiz.qpic.cn/mmbiz_png/icBZQJaAUTJglhtJRiaSOcic7DrrexLsIMgk1r4c2dxm6HjIoaSia14rEmEXgaz1qic0gUzibEE2YUa5zQXqib9hAPC2Q/640?wx_fmt=png&from=appmsg&watermark=1#imgIndex=12)

根据之前的规则约定，带V1字样的是回测版，仅支持回测，主要是代码少，方便看策略逻辑，带V2字样的是回测实盘一体版，同时兼顾实盘和回测，方便对比回测实盘之间的差异，后缀py的是策略源码文件，rzrk是QMT当中的策略备份文件，在QMT中导入后，除了能看到源码外，还会带有策略回测时的各项参数，因此墙裂建议量化萌新通过rzrk导入的方式使用策略，会非常省心省力。

我是量化君，下期见~

---

★

往期回顾

★

\------量化社群------

\------量化策略------

[桥水全天候策略](http://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247485885&idx=1&sn=d687f4296450cae754bd1b801ce7bd34&chksm=c21baa32f56c2324b137bf77796ce740cf9ca6b2c66f4ea4f9c60f1658b170cae66ae45ec5e3&scene=21#wechat_redirect) [风险平价策略](http://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247485904&idx=1&sn=7f9a873f81ea6dcbc6e5b9af2d659fa5&chksm=c21baa5ff56c23495174192875c6ea4a6cdfa52d9db3646fa52765acc68bdfdd7334859b1377&scene=21#wechat_redirect)

[贴水策略](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247484405&idx=1&sn=664567f274c737278867402e0b2277c2&scene=21#wechat_redirect) [概率密度策略](http://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247484675&idx=1&sn=e8a5e701e58ddb2e34db793f0ec59d9c&chksm=c21ba68cf56c2f9ab4d37d956e70250dfd59af25b636681f8fae8d9557e4c639e45c1633429d&scene=21#wechat_redirect) [一致预期](http://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247484960&idx=1&sn=bacb21875c2a4b377a7d35c47d03b21b&chksm=c21ba5aff56c2cb97a55bab6a7630d20e5cf19fb9f6743d9e8c49f186f19d6a5e3ff666c43d7&scene=21#wechat_redirect)

[野路子策略](http://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247485305&idx=1&sn=eb63adecb80b44b8ee57d050495da51c&chksm=c21ba4f6f56c2de037a52ab7b5c74ae31ace34863ed6d9bad20486e583e09d2643e2150d7a3d&scene=21#wechat_redirect) [ETF轮动](http://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247485362&idx=1&sn=28d5dc92d07758cdc922cf81e5cc5e26&chksm=c21ba43df56c2d2b2a2185c28297745c1108163ab6ae2274676f3605eed9bf10f80ad681db31&scene=21#wechat_redirect) [ETF轮动2](http://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247485425&idx=1&sn=d25d93f195a38d64976f6cd0f78bd21d&chksm=c21ba47ef56c2d68436195945c4d1a85887736a36366cf207f19aa9f5cea36ceb936f107a491&scene=21#wechat_redirect)

[菜场大妈&马科维茨](http://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247485558&idx=1&sn=a376f48a64e624ca543f4ad6d947f8f2&chksm=c21babf9f56c22effd22cff1aefd7c02835f96a840b08d65772af4d584320066e89571dbf5b7&scene=21#wechat_redirect) [多赚200%](http://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247485503&idx=1&sn=436532c5379f99d18307c2841b150020&chksm=c21babb0f56c22a6eb876387adf688580acd97e2e4a49b1f2352833f45e22fcbde11509293ad&scene=21#wechat_redirect)

[美债&A股择时](http://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247485683&idx=1&sn=470100d1c72e65d5f9ae4116e480617c&chksm=c21bab7cf56c226a9b8d699c6504d982240a1b65da0b832df2e6723cd266c31d302a3eb5b6a8&scene=21#wechat_redirect) [价比斜率套利](http://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247485960&idx=1&sn=3ac032c71b7aafeb15d9bbb0b84f9faf&chksm=c21ba987f56c20914d85503f959c7b0912b1189b30af329e408375c6b7ed203c706a55e30d54&scene=21#wechat_redirect)

[黄金价格预测](http://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247486032&idx=1&sn=7b0a0f10b252eabec76bfef256db9d8e&chksm=c21ba9dff56c20c92e3a08ee170b20dc411112fa12a8f89f55ae70450000e25614174a7a22bf&scene=21#wechat_redirect) [量化兵器库](http://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247483659&idx=1&sn=4c44a69d92bf5fdcb57ae64f3f7bab01&chksm=c21ba284f56c2b92aadecee7a9b50d507198c87b64c7355d23c178756b1ef4348105d58a2ac8&scene=21#wechat_redirect)

[十年零回撤](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247485946&idx=1&sn=0247059aebc4e851e330dcfef8a71e06&scene=21#wechat_redirect) [红利策略](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247486120&idx=1&sn=b3810b54ef12d899cd8cacdf3f7ea594&scene=21#wechat_redirect) [数字信号](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247486368&idx=1&sn=9a8e7b22717192a59072ef40c5b1ea43&scene=21#wechat_redirect)

\------心得杂谈------

[入门路径](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247486125&idx=1&sn=7363ab72138ab4c76e167507c6e66fe7&scene=21#wechat_redirect) [量化书单](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247483782&idx=1&sn=b80c2ee25c6f9f8fd88b7c1dee9513ef&scene=21#wechat_redirect) [量化神作](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247483706&idx=1&sn=7c45148b63cd2afd102da9da08609073&scene=21#wechat_redirect)

[打开黑箱](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247484482&idx=1&sn=7b98097f0a0a48728aeec452a834f1fc&scene=21#wechat_redirect) [量化手册](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247486076&idx=1&sn=7265e59cd5b9a9716a83b1a4cfb416b4&scene=21#wechat_redirect) [量化攻略](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247486384&idx=1&sn=a1d00fcf058755df0f2b51cda5a2ab5a&scene=21#wechat_redirect)

[个人量化](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247484469&idx=1&sn=ecfdb2b3f3e723fd417c0bddbd957b6b&scene=21#wechat_redirect) [量化误解](http://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247485473&idx=1&sn=4c71320bde8a62db39ee807dc4a206a8&chksm=c21babaef56c22b8846e2a4b1c499cd1ffae011276bd06c8cad172453b9d4d905578da0c71cf&scene=21#wechat_redirect) [高收入背后](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247484426&idx=1&sn=e0d282978280a65b4d3270c050fe71bf&scene=21#wechat_redirect)

[西蒙斯](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247483840&idx=1&sn=8cce9b5875f11d57945a1666f0e03591&scene=21#wechat_redirect) [雪球爆仓](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247485815&idx=1&sn=6378f7c65c16da331af9598b08d53679&scene=21#wechat_redirect) [量化交易邪术](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247486330&idx=1&sn=10e5309bc821819950e43bf7df0cac21&scene=21#wechat_redirect)

[量化网站1](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247485841&idx=1&sn=e94800a827ce4f38c833dcd144ea1806&scene=21#wechat_redirect) [量化网站2](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247485869&idx=1&sn=951f15090c856e7fcda3b660dccc6b8f&scene=21#wechat_redirect) [量化狠人](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247485924&idx=1&sn=9447f05fbba78524455878c999adabd1&scene=21#wechat_redirect)

[未来函数](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247484081&idx=1&sn=6076ced2c2418de2d8e5d77f2162ea07&scene=21#wechat_redirect) [回测&过拟合](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247484577&idx=1&sn=6492bc8164649e3d85d015410c9db8a6&scene=21#wechat_redirect) [回测&实盘](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247484586&idx=1&sn=545202b3c6a10f87e5f03be0f00a2cb2&scene=21#wechat_redirect)

[资金流](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247484049&idx=1&sn=78b94c8055e6822e949180d942b00058&scene=21#wechat_redirect) [吃贴水](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247484388&idx=1&sn=f165f6ca0ab5c0e36fc320e4dbf0e8e0&scene=21#wechat_redirect) [回测提速](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247483736&idx=1&sn=334f2395a881328014c2f2bc1568e89b&scene=21#wechat_redirect) [量价背离](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247483796&idx=1&sn=0f783208f9dd1994a21964b715bdf63e&scene=21#wechat_redirect)

[自学路径](http://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247484712&idx=1&sn=9fdf003c783b4bc053b90736ebbb8435&chksm=c21ba6a7f56c2fb1e87cf876cba0c2620244840c339666072447a0c14516dbeb686ca2e65702&scene=21#wechat_redirect) [文章合辑](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247485751&idx=1&sn=c97da5635ce842634d1c2f68397f21c1&scene=21#wechat_redirect) [151个策略](http://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247484775&idx=1&sn=90a7e0d3786f9dd2d97a7ba95ec69318&chksm=c21ba6e8f56c2ffe64eb9fe0d2f30ba8460d53997401f00b79a91e5981a9ba2ab21a000e4d2b&scene=21#wechat_redirect)

[5年131倍](http://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247485243&idx=1&sn=2c2903bce6d0de4f7480ab30c4139124&chksm=c21ba4b4f56c2da23deaa897576c6617653a7dd1d9186fdec53ba42d813a628eaf7132eddaf3&scene=21#wechat_redirect) [量化编程神器](http://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247485006&idx=1&sn=ec989b1a9f2d74f669509dc7ce561710&chksm=c21ba5c1f56c2cd7e02fba70dab2e8236ddbc6aa05d6cf7ad7fc9205c6fe1f4f7f29461d6f6b&scene=21#wechat_redirect)

[4000因子](http://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247485037&idx=1&sn=dad40d6cdb8482fdf1e6f94690f2494e&chksm=c21ba5e2f56c2cf48767632408133f74e3e23dbd0aac285e1f169d2d7f912516d178b04da22d&scene=21#wechat_redirect) [因子库](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247486429&idx=1&sn=5f2101cfde264f0370b99b4f8b8e7ada&scene=21#wechat_redirect) [量化神集](http://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247485464&idx=1&sn=782255a6c21ac469dc7fb85bde8699a4&chksm=c21bab97f56c2281ce0d25b95fbc689322fecd47952f24ce8e29e35da0f24891bce15dda51fb&scene=21#wechat_redirect)

[量化深坑](http://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247485449&idx=1&sn=e228c79ce83db37b1c4360347439ddf8&chksm=c21bab86f56c22909465f838d3eb66009058cdfbe63f8a64f272d6517328d6ff4f4f4e216b93&scene=21#wechat_redirect) [老胡炒股](http://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247485272&idx=1&sn=11e9d986e865585aa6b82ebdf28ffde0&chksm=c21ba4d7f56c2dc10545527e2fa965adde6559c1ffca60f5c1acff0413cf5616b72e1d56f661&scene=21#wechat_redirect) [私募上班](http://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247485250&idx=1&sn=294038b08fe03a700052aa6e94aea1f2&chksm=c21ba4cdf56c2ddb2831a4da44609bc7c323d03c0897a57022fa937501bf2c18f83ac417490c&scene=21#wechat_redirect)

[十年8万倍](http://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247485624&idx=1&sn=893ca25217be05fca79e7f43ae3410cd&chksm=c21bab37f56c2221c2cc186175080622c2421cc7c1704862e482a60f282900e4c1ff02708855&scene=21#wechat_redirect) [五穷六绝](http://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247486105&idx=1&sn=87db4aa95cd30cf1e2b2b4a0205a2234&chksm=c21ba916f56c200088c4aceda6e62511469cff0d18af97fa48b659fc6ac3862d494f02fd38a6&scene=21#wechat_redirect) [一月之殇](https://mp.weixin.qq.com/s?__biz=MzkyODI5ODcyMA==&mid=2247485826&idx=1&sn=cfeb2dd29ef21c48ab2e96b568b42559&scene=21#wechat_redirect)

*Tip：点击关键字可以直接查看对应文章。*

END

如果对本文有疑惑，或是想聊聊

亦或是围观朋友圈当点赞之交

戳我，让我们一路同行

吃瓜吐槽写代码

![图片](https://mmbiz.qpic.cn/mmbiz_png/icBZQJaAUTJj8BE6ibmq8zu4eEcMT8W6QrIjY8JZcTGn5I8e1mmMXFic1GlLJ5MPJlTPgicu3ZR5uEiceJr4TSyaWIQ/640?wx_fmt=png#imgIndex=13)

添加好友后，私信『 **666** 』

送你一些量化小福利

人工回复慢请见谅~

![图片](https://mmbiz.qpic.cn/mmbiz_png/icBZQJaAUTJjfqsUicmjYPTmCHoOpib1H8oAWmCwic3YXB0rqbCqKLZrl2meoy7FIS4tHRqXgcskDoQkuTssKxQicEQ/640?wx_fmt=png&from=appmsg#imgIndex=14)

风险提示：市场有风险，投资需谨慎。所有策略思路和策略源码仅供参考和学习，不构成投资建议，策略回测仅代表历史收益，不代表未来收益。

量化交易 · 目录