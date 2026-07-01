"""
牛熊切换组合(市况分层):沪深300「MA30 且 MA60 同时走坏」→ 持全天候避险,否则持龙头。

择时开关经 scratchpad 横评为最优(夏普3.08/卡玛6.5~7.1,回撤较纯龙头减半且不牺牲收益,详见 README)。
信号滞后1天防前视。返回组合/纯龙头/纯全天候三条净值 + 绩效 + 切换记录。
"""
import argparse
import numpy as np
import pandas as pd
import duckdb
import engine
import leader_momentum as lm
import allweather as aw
import cache_tushare as ct

STRAT_NAME, LEAD_NAME, ALLW_NAME = "牛熊切换组合", "纯龙头", "纯全天候"
WARM = "2022-01-01"


def _daily(universe, loader, n, k, l, warm, end, commission, stamp):
    """复算某策略每日净收益(扣手续费)+ 每日权重宽表,返回 (ret, w)。"""
    px = loader(universe.keys(), warm, end)
    rets = px.pct_change(fill_method=None)
    mom, adj = engine.calc_momentum(rets, n)
    w, _ = engine.build_weights(mom, adj, n, k, l)
    return engine.net_returns(w, rets, commission, stamp), w


def _holdings(w, universe, d):
    """取权重表 w 在日期 d 的非零持仓,返回 picks 列表[{code,name,weight}]。"""
    row = w.loc[d]
    row = row[row > 0].sort_values(ascending=False)
    return [{"code": c, "name": universe.get(c, c), "weight": round(float(v), 4)} for c, v in row.items()]


def _hs300_regime(warm, end):
    """沪深300 走 tushare,返回 defensive 布尔 Series(MA30 且 MA60 同时走坏,即收盘<均线且均线下行)。"""
    import tushare as ts
    pro = ts.pro_api(ct._get_token())
    ix = pro.index_daily(ts_code="000300.SH", start_date=warm.replace("-", ""), end_date=end.replace("-", ""),
                         fields="trade_date,close")
    ix = ix.set_index(pd.to_datetime(ix["trade_date"]))["close"].sort_index()
    ma30, ma60 = ix.rolling(30).mean(), ix.rolling(60).mean()
    healthy30 = (ix > ma30) & (ma30 > ma30.shift(5))
    healthy60 = (ix > ma60) & (ma60 > ma60.shift(5))
    return ((~healthy30) & (~healthy60)).fillna(False)


def _perf_row(name, r):
    """把 engine.perf 结果包成 summary 行。"""
    p = engine.perf(r)
    return {"策略": name, **{k: v for k, v in p.items()}}


def to_payload(start="2024-01-01", end=None, **_):
    """给前后端用:跑牛熊切换组合并组装 JSON(净值/绩效/切换记录)。"""
    end = end or pd.Timestamp.today().strftime("%Y-%m-%d")
    lead, lead_w = _daily(lm.STOCKS, engine.load_stock_qfq, 20, 5, 5, WARM, end, engine.COMM_STOCK, engine.STAMP_STOCK)
    allw, allw_w = _daily(aw.ETFS, engine.load_fund_qfq, 20, 1, 3, WARM, end, engine.COMM_ETF, engine.STAMP_ETF)
    defensive = _hs300_regime(WARM, end)
    idx = lead.index.intersection(allw.index)
    lead, allw = lead.reindex(idx), allw.reindex(idx)
    lead_w, allw_w = lead_w.reindex(idx).ffill(), allw_w.reindex(idx).ffill()
    defensive = defensive.reindex(idx).ffill().fillna(False)
    applied = defensive.shift(1).fillna(False).astype(bool)
    combo = pd.Series(np.where(applied, allw, lead), index=idx)
    switch = applied.ne(applied.shift(1)).fillna(False)
    combo = combo - switch * (engine.COMM_STOCK + engine.COMM_ETF + engine.STAMP_STOCK)

    m = idx >= pd.Timestamp(start)
    rdf = pd.DataFrame({STRAT_NAME: combo[m], LEAD_NAME: lead[m], ALLW_NAME: allw[m]})
    growth = (1 + rdf.fillna(0)).cumprod()
    cum = growth - 1
    equity = [{"date": str(pd.Timestamp(d).date()),
               **{c: round(float(cum.loc[d, c]), 4) for c in cum.columns}} for d in cum.index]
    summary = [_perf_row(STRAT_NAME, combo[m]), _perf_row(LEAD_NAME, lead[m]), _perf_row(ALLW_NAME, allw[m])]

    ap = applied[m]
    switches = []
    prev = None
    for d, dv in ap.items():
        state = "全天候(避险)" if dv else "龙头(进攻)"
        if state != prev:
            picks = _holdings(allw_w, aw.ETFS, d) if dv else _holdings(lead_w, lm.STOCKS, d)
            switches.append({"date": str(pd.Timestamp(d).date()), "state": state, "picks": picks})
            prev = state
    return {
        "ok": True,
        "params": {"N": 20, "K": 0, "L": 0, "start": start, "end": end},
        "cols": [STRAT_NAME, LEAD_NAME, ALLW_NAME],
        "summary": summary,
        "equity": equity,
        "rebalances": list(reversed(switches)),
    }


def main():
    """CLI:打印三条绩效。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default=pd.Timestamp.today().strftime("%Y-%m-%d"))
    args = ap.parse_args()
    p = to_payload(args.start, args.end)
    print(f"\n{STRAT_NAME}  {args.start}~{args.end}  切换次数 {len(p['rebalances'])}")
    print(pd.DataFrame(p["summary"]).to_string(index=False))


if __name__ == "__main__":
    main()
