"""etf_flow_regime.py — 把「宽基ETF净申赎」信号叠加到 xiaoxifu 牛熊切换组合上做择时,测算是否增强。

规则(在牛熊切换组合基础上加仓位开关,均 T+1 执行、点位内扩张分位定阈值防未来函数):
  · 长+强净赎回(r60 跌破自身历史20分位)= 见顶/避险 → 空仓(0)
  · 长+强净申购(r60 升破自身历史80分位)= 抄底 → 买入(重新进场)
  · 两信号之间 → 跟随牛熊切换组合(龙头↔全天候)
r60 = 60日滚动净申赎%AUM,天然只在"长且强"时触发,自动避开"短+强=恐慌底反弹"的坑。

对比:牛熊切换组合(基准)/ +本叠加(投资态跟组合)/ +本叠加且抄底强制龙头进攻 / 上证买入持有。
数据:主库 etf_share+fund_daily+index_daily;xiaoxifu regime_combo 内部件。用法:python research/etf_flow_regime.py。
"""
import os
import sys
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); sys.path.insert(0, os.path.join(_ROOT, "xiaoxifu"))
sys.stdout.reconfigure(encoding="utf-8")
import numpy as np
import pandas as pd
import duckdb
from cache_tushare import DUCKDB_PATH, ETF_SHARE_LIST
import engine
import regime_combo as rc
import leader_momentum as lm
import allweather as aw

WARM = "2015-01-01"
END = pd.Timestamp.today().strftime("%Y-%m-%d")


def flow_r60():
    """主库算 60日滚动净申赎%AUM(index=日期)。"""
    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    ph = ",".join(["?"] * len(ETF_SHARE_LIST))
    sh = con.execute(f"SELECT ts_code,trade_date,total_share FROM etf_share WHERE ts_code IN ({ph})", ETF_SHARE_LIST).df()
    px = con.execute(f"SELECT ts_code,trade_date,close FROM fund_daily WHERE ts_code IN ({ph})", ETF_SHARE_LIST).df()
    con.close()
    for d in (sh, px):
        d["trade_date"] = pd.to_datetime(d["trade_date"])
    m = sh.merge(px, on=["ts_code", "trade_date"]).sort_values(["ts_code", "trade_date"])
    m["aum"] = m["total_share"] * m["close"]
    m["flow"] = m.groupby("ts_code")["total_share"].diff() * m["close"]
    g = m.groupby("trade_date").agg(flow=("flow", "sum"), aum=("aum", "sum")).dropna()
    return (g["flow"] / g["aum"] * 100).rolling(60).sum().dropna()


def flow_state(r60, warm=250, hi=0.80, lo=0.20):
    """点位内扩张分位状态机:r60升破历史hi分位→'in';跌破lo分位→'cash';否则保持。返回状态 Series。"""
    vals = r60.values
    st = np.array(["neutral"] * len(r60), dtype=object)
    cur = "neutral"
    for i in range(len(r60)):
        if i >= warm:
            hist = vals[:i]
            hthr, lthr = np.quantile(hist, hi), np.quantile(hist, lo)
            if vals[i] <= lthr:
                cur = "cash"
            elif vals[i] >= hthr:
                cur = "in"
        st[i] = cur
    return pd.Series(st, index=r60.index)


def main():
    lead, *_ = rc._legs(lm.STOCKS, engine.load_stock_qfq, 20, 5, 5, WARM, END, engine.COMM_STOCK, engine.STAMP_STOCK)
    allw, *_ = rc._legs(aw.ETFS, engine.load_fund_qfq, 20, 1, 3, WARM, END, engine.COMM_ETF, engine.STAMP_ETF)
    defensive = rc._hs300_regime(WARM, END)
    idx = lead.index.intersection(allw.index)
    lead, allw = lead.reindex(idx), allw.reindex(idx)
    defensive = defensive.reindex(idx).ffill().fillna(False).astype(bool)
    applied = defensive.shift(1).fillna(False).astype(bool)
    combo = pd.Series(np.where(applied, allw, lead), index=idx)
    switch = applied.ne(applied.shift(1)).fillna(False)
    combo = combo - switch * (engine.COMM_STOCK + engine.COMM_ETF + engine.STAMP_STOCK)

    r60 = flow_r60()

    sh_idx = duckdb.connect(DUCKDB_PATH, read_only=True).execute(
        "SELECT trade_date,close FROM index_daily WHERE ts_code='000001.SH' ORDER BY trade_date").df()
    sh_idx["trade_date"] = pd.to_datetime(sh_idx["trade_date"])
    shr = sh_idx.set_index("trade_date")["close"].pct_change().reindex(idx)

    print(f"回测区间 {idx.min().date()}~{idx.max().date()} | 交易日 {len(idx)}\n")

    def show(name, r):
        p = engine.perf(r.dropna())
        print(f"{name:26s} 累计 {p['累计收益']:+7.1f}%  年化 {p['年化收益']:+6.1f}%  回撤 {p['最大回撤']:5.1f}%  夏普 {p['夏普比率']}  卡玛 {p['卡玛比率']}")
    show("牛熊切换组合(基准)", combo)
    for lo,hi in [(0.10,0.90),(0.05,0.95),(0.15,0.85)]:
        st=flow_state(r60,hi=hi,lo=lo).reindex(idx).ffill().fillna("neutral").shift(1).fillna("neutral")
        nc=(st=="cash").sum()
        v=pd.Series(np.where(st=="cash",0.0,combo),index=idx)
        show(f"+空仓叠加 lo{int(lo*100)}/hi{int(hi*100)}(空仓{nc/len(idx)*100:.0f}%)", v)
    show("纯龙头", lead)
    show("上证买入持有", shr)
    print("--- ETF流择时【上证】(该信号本该配大盘) ---")
    for lo,hi in [(0.10,0.90),(0.20,0.80)]:
        st=flow_state(r60,hi=hi,lo=lo).reindex(idx).ffill().fillna("neutral").shift(1).fillna("neutral")
        nc=(st=="cash").sum()
        v=pd.Series(np.where(st=="cash",0.0,shr),index=idx)
        show(f"上证+空仓叠加 lo{int(lo*100)}/hi{int(hi*100)}(空仓{nc/len(idx)*100:.0f}%)", v)


if __name__ == "__main__":
    main()
