"""
小西西弗动量轮动策略 通用引擎(三个策略共用:龙头/全天候/行业)。

风险调整动量 Adj_Momentum = N日日收益均值 / sqrt(N日日收益方差)。
每 K 天调仓(K=1 即每日):原始动量为正 → 按 Adj_Momentum 降序取前 L → 归一化配权;
权重滞后 1 天(T 决策 T+1 执行)算组合日收益。基准与等权组合为对照。

price loader 由各策略传入(龙头走本地 DuckDB 个股;ETF 策略走 tushare fund_daily+fund_adj)。
"""
import os
import sys
import numpy as np
import pandas as pd
import duckdb

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cache_tushare as ct

EQUAL_NAME = "等权重组合"
TRADING_DAYS = 252


def load_stock_qfq(codes, start, end):
    """本地 DuckDB 读多只个股前复权收盘,返回宽表[index=Timestamp, columns=code]。"""
    con = duckdb.connect(ct.DUCKDB_PATH, read_only=True)
    df = con.execute(
        """WITH laf AS (
             SELECT ts_code, arg_max(adj_factor, trade_date) AS lf FROM adj_factor GROUP BY ts_code)
           SELECT d.ts_code, d.trade_date, d.close*a.adj_factor/laf.lf AS adjc
           FROM daily d JOIN adj_factor a
             ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date
           JOIN laf ON laf.ts_code=d.ts_code
           WHERE d.ts_code IN (SELECT UNNEST(?)) AND d.trade_date BETWEEN ? AND ?""",
        [list(codes), start, end],
    ).fetch_df()
    con.close()
    px = df.pivot(index="trade_date", columns="ts_code", values="adjc").sort_index()
    px.index = pd.to_datetime(px.index)
    return px


def load_fund_qfq(codes, start, end):
    """走 tushare fund_daily+fund_adj 取多只 ETF 前复权收盘,返回宽表[index=Timestamp, columns=code]。"""
    import tushare as ts
    pro = ts.pro_api(ct._get_token())
    s, e = start.replace("-", ""), end.replace("-", "")
    cols = {}
    for c in codes:
        d = pro.fund_daily(ts_code=c, start_date=s, end_date=e, fields="trade_date,close")
        a = pro.fund_adj(ts_code=c, start_date=s, end_date=e, fields="trade_date,adj_factor")
        if d.empty or a.empty:
            continue
        m = d.merge(a, on="trade_date")
        m["dt"] = pd.to_datetime(m["trade_date"])
        m = m.sort_values("dt").set_index("dt")
        cols[c] = m["close"] * m["adj_factor"] / m["adj_factor"].iloc[-1]
    return pd.DataFrame(cols).sort_index()


def calc_momentum(returns, n):
    """对宽表日收益算 (原始动量=N日均值, 调整动量=N日均值/sqrt(N日方差)),返回 (mom, adj)。"""
    mom = returns.rolling(n).mean()
    vol = returns.rolling(n).var()
    return mom, mom / np.sqrt(vol)


def build_weights(mom, adj, n, k, l):
    """按调仓规则生成每日权重宽表 + 调仓动作列表。返回 (w, actions[{date,picks:[{code,weight}]}])。"""
    dates = mom.index
    w = pd.DataFrame(0.0, index=dates, columns=mom.columns)
    reb = list(range(n - 1, len(dates), k))
    actions = []
    for j, ri in enumerate(reb):
        cm, ca = mom.iloc[ri], adj.iloc[ri]
        pos = cm[(cm > 0) & cm.notna()].index
        top = ca[pos].dropna().sort_values(ascending=False).head(l) if len(pos) else pd.Series(dtype=float)
        if len(top) == 0 or top.sum() <= 0:
            actions.append({"date": str(dates[ri].date()), "picks": []})
            continue
        ww = top / top.sum()
        end = len(dates) if j == len(reb) - 1 else reb[j + 1]
        w.iloc[ri:end, w.columns.get_indexer(ww.index)] = ww.values
        actions.append({"date": str(dates[ri].date()),
                        "picks": [{"code": c, "weight": round(float(v), 4)} for c, v in ww.items()]})
    return w, actions


def perf(returns):
    """年化收益(几何)/年化波动/最大回撤/夏普/卡玛,输入日收益 Series,返回 dict。"""
    r = returns.dropna()
    if len(r) < 2:
        return dict(年化收益=None, 年化波动率=None, 最大回撤=None, 夏普比率=None, 卡玛比率=None)
    cum = (1 + r).cumprod()
    ann = cum.iloc[-1] ** (TRADING_DAYS / len(r)) - 1
    vol = r.std() * np.sqrt(TRADING_DAYS)
    mdd = (cum / cum.cummax() - 1).min()
    return dict(年化收益=round(ann * 100, 2), 年化波动率=round(vol * 100, 2),
                最大回撤=round(abs(mdd) * 100, 2),
                夏普比率=round(ann / vol, 3) if vol else None,
                卡玛比率=round(ann / abs(mdd), 3) if mdd else None)


def run(universe, loader, bench_code, bench_name, strat_name, n, k, l, start, end):
    """跑回测,返回 (summary DataFrame, cum 累计收益宽表, dd 回撤宽表, actions)。"""
    px = loader(universe.keys(), start, end)
    rets = px.pct_change(fill_method=None)
    mom, adj = calc_momentum(rets, n)
    w, actions = build_weights(mom, adj, n, k, l)
    strat = (w.shift(1) * rets).sum(axis=1).rename(strat_name)
    equal = rets.mean(axis=1).rename(EQUAL_NAME)
    series = {strat_name: strat, EQUAL_NAME: equal}
    if bench_code in px.columns:
        series[bench_name] = px[bench_code].pct_change(fill_method=None).rename(bench_name)
    else:
        try:
            b = load_fund_qfq([bench_code], start, end)
            series[bench_name] = b[bench_code].reindex(px.index).ffill().pct_change(fill_method=None).rename(bench_name)
        except Exception as ex:
            print(f"基准 {bench_code} 拉取失败,跳过: {ex}")
    rdf = pd.DataFrame(series).loc[strat.index]
    growth = (1 + rdf.fillna(0)).cumprod()
    cum, dd = growth - 1, growth.div(growth.cummax()) - 1
    summary = pd.DataFrame({name: perf(rdf[name]) for name in rdf.columns}).T
    summary.index.name = "策略"
    return summary, cum, dd, actions


def to_payload(universe, loader, bench_code, bench_name, strat_name, n, k, l, start, end):
    """组装前后端 JSON:params/cols/summary/equity/rebalances。"""
    summary, cum, dd, actions = run(universe, loader, bench_code, bench_name, strat_name, n, k, l, start, end)
    equity = [{"date": str(pd.Timestamp(d).date()),
               **{c: round(float(cum.loc[d, c]), 4) for c in cum.columns}} for d in cum.index]
    for a in actions:
        for p in a["picks"]:
            p["name"] = universe.get(p["code"], p["code"])
    return {
        "ok": True,
        "params": {"N": n, "K": k, "L": l, "start": start, "end": end},
        "cols": list(cum.columns),
        "summary": [{"策略": idx, **{c: (None if pd.isna(v) else v) for c, v in row.items()}}
                    for idx, row in summary.iterrows()],
        "equity": equity,
        "rebalances": list(reversed(actions)),
    }
