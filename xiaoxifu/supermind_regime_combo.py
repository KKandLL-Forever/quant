"""
牛熊切换组合 —— SuperMind(同花顺 quant.10jqka.com.cn)版,由 xiaoxifu/regime_combo.py 移植。

策略:沪深300「MA30 与 MA60 同时走坏」→ 切到全天候 ETF 腿避险,否则持龙头股腿进攻。
  · 走坏 = 收盘跌破均线 且 均线较 5 日前下行,两条均线都满足才算熊
  · 龙头腿:26 只各赛道龙头,N=20 风险调整动量(均值/√方差),每 5 个交易日调仓,取前 5 归一化
  · 全天候腿:纳指/沪深300/黄金 3 只 ETF,N=20,每日调仓,取前 3 归一化
  · 两腿都只买原始动量为正的标的;全为负则空仓持币

用法:整份粘进 SuperMind 策略编辑器,回测频率选「日线」,起始资金建议 ≥50 万
     (龙头腿满仓 5 只、每笔最低佣金 5 元,资金太小手续费占比会失真)。

写法上避开了平台沙箱的三个坑:
  · 禁用 getattr(会报 InputRejected: Cannot getattr),所以不做任何动态属性访问
  · 只用 handle_bar,日线回测每日 9:31 调用一次,不依赖 run_daily
  · 不 import numpy,开方用 ** 0.5

防前视:日线 history() 只返回到前一交易日,不含当日这根;市价单成交价是当日开盘价+滑点,
即「昨收算信号、今开执行」,对应本地回测里的 shift(1)。

与本地回测(regime_combo.py)的已知差异,结果对不上时先查这四条:
  1. 龙头腿「每 5 个交易日」的相位:本地从 2022-01-01 预热锚定,这里从回测首日起算,
     相位不同会让个别调仓日错开,长期收益接近但不会逐日相同
  2. 本地按前复权收盘的理论价结算,这里是开盘价撮合 + 滑点 + 每笔最低佣金
  3. 平台默认卖出印花税千分之一,而现行实际是万五,故这里的成本偏保守(结果偏低)
  4. 停牌/涨跌停会让下单失败,本地回测没有这层摩擦
"""

import pandas as pd

LEADERS = [
    "600111.SH", "002460.SZ", "601899.SH", "600988.SH", "002230.SZ", "002709.SZ",
    "002594.SZ", "002653.SZ", "601939.SH", "600667.SH", "601606.SH", "000021.SZ",
    "000657.SZ", "600584.SH", "605366.SH", "600176.SH", "603938.SH", "603650.SH",
    "601336.SH", "000568.SZ", "601288.SH", "601319.SH", "600030.SH", "600938.SH",
    "600900.SH", "600536.SH",
]
ALLWEATHER = ["513100.SH", "510300.SH", "518880.SH"]
REGIME_INDEX = "000300.SH"

N = 20
LEAD_K, LEAD_L = 5, 5
ALLW_L = 3
MA_FAST, MA_SLOW, MA_TREND = 30, 60, 5
EXPOSURE = 0.98
MIN_WEIGHT = 0.01


def init(context):
    """回测初始化:设基准/滑点/佣金,初始化两腿权重与调仓计数。"""
    set_benchmark(REGIME_INDEX)
    set_slippage(PriceSlippage(0.002))
    set_commission(PerShare(type="stock", cost=0.00025, min_trade_cost=5.0))
    context.lead_w = {}
    context.allw_w = {}
    context.day = 0
    context.defensive = False


def _closes(codes, bar_count):
    """取多标的前复权收盘宽表[index=日期, columns=代码];日线下 history 已不含当日这根。"""
    raw = history(codes, ["close"], bar_count, "1d", False, fq="pre", is_panel=0)
    cols = {}
    if isinstance(raw, dict):
        for code in raw:
            df = raw[code]
            if df is not None and len(df):
                cols[code] = pd.Series(df["close"].values, index=pd.to_datetime(df.index))
    elif raw is not None and len(raw):
        cols[codes[0]] = pd.Series(raw["close"].values, index=pd.to_datetime(raw.index))
    if not cols:
        return pd.DataFrame()
    return pd.DataFrame(cols).sort_index().dropna(how="all")


def _weights(px, top_l):
    """风险调整动量选腿内标的:原始动量为正 → 按 均值/√方差 降序取前 L → 归一化,返回 {代码:权重}。"""
    if px is None or len(px) < N + 2:
        return {}
    rets = px.pct_change(fill_method=None)
    mom = rets.rolling(N).mean().iloc[-1]
    vol = rets.rolling(N).var().iloc[-1]
    adj = mom / (vol ** 0.5)
    pos = mom[(mom > 0) & mom.notna()].index
    if len(pos) == 0:
        return {}
    top = adj[pos].dropna().sort_values(ascending=False).head(top_l)
    if len(top) == 0 or top.sum() <= 0:
        return {}
    ww = top / top.sum()
    out = {}
    for code in ww.index:
        out[code] = float(ww[code])
    return out


def _defensive(context):
    """沪深300 是否「MA30 与 MA60 同时走坏」:两条都是 收盘<均线 且 均线较 5 日前下行。"""
    px = _closes([REGIME_INDEX], MA_SLOW + MA_TREND + 10)
    if REGIME_INDEX not in px.columns or len(px) < MA_SLOW + MA_TREND + 1:
        return context.defensive
    s = px[REGIME_INDEX]
    bad = []
    for win in (MA_FAST, MA_SLOW):
        ma = s.rolling(win).mean()
        healthy = (s.iloc[-1] > ma.iloc[-1]) and (ma.iloc[-1] > ma.iloc[-1 - MA_TREND])
        bad.append(not healthy)
    return bool(bad[0] and bad[1])


def _execute(context, target):
    """把持仓调到目标权重:先清掉不在目标里的,再按权重买入,权重合计乘 EXPOSURE 留出费用。"""
    for code in list(context.portfolio.positions.keys()):
        if code not in target:
            order_target_percent(code, 0)
    for code in sorted(target, key=lambda c: target[c]):
        if target[code] >= MIN_WEIGHT:
            order_target_percent(code, round(target[code] * EXPOSURE, 4))


def handle_bar(context, bar_dict):
    """每个交易日:更新两腿权重(龙头腿每 5 日一次),按沪深300 市况选边,再执行调仓。"""
    context.day += 1
    if context.day == 1 or (context.day - 1) % LEAD_K == 0:
        context.lead_w = _weights(_closes(LEADERS, N + LEAD_K + 10), LEAD_L)
    context.allw_w = _weights(_closes(ALLWEATHER, N + 10), ALLW_L)

    context.defensive = _defensive(context)
    target = context.allw_w if context.defensive else context.lead_w
    _execute(context, target)

    record(defensive=1 if context.defensive else 0, n_hold=len(target))
    detail = ", ".join(["%s:%.0f%%" % (c, target[c] * 100)
                        for c in sorted(target, key=lambda c: -target[c])]) or "空仓"
    leg = "全天候(避险)" if context.defensive else "龙头(进攻)"
    log.info("%s 腿 | 持仓 %d 只 | %s" % (leg, len(target), detail))
