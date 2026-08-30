"""
牛熊切换组合 —— SuperMind(同花顺 quant.10jqka.com.cn)版,由 xiaoxifu/regime_combo.py 移植。

策略:沪深300「MA30 与 MA60 同时走坏」→ 切到全天候 ETF 腿避险,否则持龙头股腿进攻。
  · 走坏 = 收盘跌破均线 且 均线较 5 日前下行,两条均线都满足才算熊
  · 龙头腿:26 只各赛道龙头,N=20 风险调整动量,每 5 个交易日调仓,取前 5 归一化
  · 全天候腿:纳指/沪深300/黄金 3 只 ETF,N=20,每日调仓,取前 3 归一化
  · 两腿都只买原始动量为正的标的;都为负则空仓持币
  · 调仓动量只用到「昨收」为止的数据,当日开盘后执行,对应本地回测里的 shift(1)

用法:把整份文件粘进 SuperMind 策略编辑器,回测频率选「日线」,起始资金建议 ≥50 万
     (龙头腿 5 只 + 最小佣金 5 元,资金太小手续费占比会失真)。

与本地回测(regime_combo.py)的已知差异,看回测结果对不上时先查这三条:
  1. 龙头腿「每 5 个交易日」的相位:本地从 2022-01-01 预热锚定,这里从回测首日起算,相位不同会
     导致个别调仓日错开,长期收益接近但不会逐日相同
  2. 本地按目标权重的理论成交价(前复权收盘)结算,这里是平台撮合(开盘价 + 滑点 + 最小佣金)
  3. 停牌/涨跌停会导致下单失败,本地回测没有这层摩擦
"""

import numpy as np
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
ALLW_K, ALLW_L = 1, 3
MA_FAST, MA_SLOW, MA_TREND = 30, 60, 5
EXPOSURE = 0.98
MIN_WEIGHT = 0.01


def init(context):
    """回测初始化:设基准/费率,登记每日调仓任务,初始化两腿权重与调仓计数。"""
    set_benchmark(REGIME_INDEX)
    set_slippage(PriceSlippage(0.002))
    try:
        set_commission(PerShare(type="stock", cost=0.00025, min_trade_cost=5.0))
        set_commission(PerShare(type="fund", cost=0.0002, min_trade_cost=5.0))
    except Exception:
        set_commission(PerShare(type="stock", cost=0.00025, min_trade_cost=5.0))
    context.lead_w = {}
    context.allw_w = {}
    context.day = 0
    context.defensive = False
    run_daily(rebalance, time_rule="after_open", hours=0, minutes=5)


def _asof(context):
    """取当前回测日(用于把「今天」这根还没走完的日线剔掉,防前视)。"""
    for attr in ("now", "current_dt", "today", "current_date"):
        v = getattr(context, attr, None)
        if v is not None:
            try:
                return pd.Timestamp(v).normalize()
            except Exception:
                continue
    return None


def _closes(codes, bar_count, context):
    """取多标的前复权收盘宽表[index=日期, columns=代码],并剔除当日未完成的那根。"""
    raw = history(codes, ["close"], bar_count, "1d", fq="pre")
    cols = {}
    if isinstance(raw, dict):
        for c, df in raw.items():
            if df is not None and len(df):
                cols[c] = pd.Series(df["close"].values, index=pd.to_datetime(df.index))
    elif raw is not None and len(raw):
        cols[codes[0] if isinstance(codes, list) else codes] = pd.Series(
            raw["close"].values, index=pd.to_datetime(raw.index))
    px = pd.DataFrame(cols).sort_index()
    today = _asof(context)
    if today is not None and len(px):
        px = px[px.index < today]
    return px.dropna(how="all")


def _weights(px, top_l):
    """风险调整动量选腿内标的:原始动量为正 → 按 均值/√方差 降序取前 L → 归一化,返回 {代码:权重}。"""
    if px is None or len(px) < N + 2:
        return {}
    rets = px.pct_change(fill_method=None)
    mom = rets.rolling(N).mean().iloc[-1]
    vol = rets.rolling(N).var().iloc[-1]
    adj = mom / np.sqrt(vol)
    pos = mom[(mom > 0) & mom.notna()].index
    if len(pos) == 0:
        return {}
    top = adj[pos].dropna().sort_values(ascending=False).head(top_l)
    if len(top) == 0 or top.sum() <= 0:
        return {}
    ww = top / top.sum()
    return {c: float(v) for c, v in ww.items()}


def _defensive(context):
    """沪深300 是否「MA30 与 MA60 同时走坏」:两条都是 收盘<均线 且 均线较 5 日前下行。"""
    px = _closes([REGIME_INDEX], MA_SLOW + MA_TREND + 10, context)
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
    held = set(context.portfolio.positions.keys())
    for code in held - set(target.keys()):
        order_target_percent(code, 0)
    for code, w in sorted(target.items(), key=lambda kv: kv[1]):
        if w >= MIN_WEIGHT:
            order_target_percent(code, round(w * EXPOSURE, 4))


def rebalance(context, bar_dict):
    """每日开盘后:更新两腿权重(龙头腿每 5 日一次),按沪深300 市况选边,再执行调仓。"""
    context.day += 1
    lead_px = _closes(LEADERS, N + LEAD_K + 10, context)
    allw_px = _closes(ALLWEATHER, N + 10, context)

    if context.day == 1 or (context.day - 1) % LEAD_K == 0:
        context.lead_w = _weights(lead_px, LEAD_L)
    context.allw_w = _weights(allw_px, ALLW_L)

    context.defensive = _defensive(context)
    target = context.allw_w if context.defensive else context.lead_w

    _execute(context, target)
    record(defensive=1 if context.defensive else 0, n_hold=len(target))
    log.info("%s 腿 | 持仓 %d 只 | %s",
             "全天候(避险)" if context.defensive else "龙头(进攻)", len(target),
             ", ".join("%s:%.0f%%" % (c, w * 100) for c, w in
                       sorted(target.items(), key=lambda kv: -kv[1])) or "空仓")
