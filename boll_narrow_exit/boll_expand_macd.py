"""
BOLL 缩口→扩张 + MACD 金叉 择时研究(中证1000)。

信号(震荡尾部的向上扩张):个股 BOLL 通道先「缩口」(带宽处于过去120日低分位=震荡盘整),
随后当日带宽「扩大」,且 MACD 近3日内金叉(DIF 上穿 DEA、当前在上方),同时收盘站上中轨(向上扩张)。
在该点买入,统计其后 5/10/20 日平均涨幅,与全样本无条件均值对比看有无超额;并检验 ATR 是否有过滤价值。

标的池:中证1000(000852.SH)当前成分(tushare index_weight;有幸存者偏差,研究性口径)。
指标:本地 DuckDB stk_factor_pro(后复权 BOLL/MACD/ATR);前瞻收益用 daily 后复权收盘。
用法:python boll_narrow_exit/boll_expand_macd.py [--start 2021-01-01 --squeeze-q 0.25 --cross-win 3]
"""
import os
_ROOT = os.path.dirname(os.path.abspath(__file__))
while _ROOT != "/" and not os.path.exists(os.path.join(_ROOT, "cache_tushare.py")):
    _ROOT = os.path.dirname(_ROOT)
import sys
sys.path.insert(0, _ROOT)
import argparse
import numpy as np
import pandas as pd
import duckdb
import cache_tushare as ct


def members_1000():
    """取中证1000最新成分代码列表(本地 DuckDB csi1000_members 最新快照)。"""
    con = duckdb.connect(ct.DUCKDB_PATH, read_only=True)
    codes = [r[0] for r in con.execute(
        "SELECT con_code FROM csi1000_members WHERE trade_date=(SELECT MAX(trade_date) FROM csi1000_members)").fetchall()]
    con.close()
    return sorted(codes)


def members_2000():
    """取中证2000最新成分代码列表(本地 DuckDB csi2000_members 最新快照)。"""
    con = duckdb.connect(ct.DUCKDB_PATH, read_only=True)
    codes = [r[0] for r in con.execute(
        "SELECT con_code FROM csi2000_members WHERE trade_date=(SELECT MAX(trade_date) FROM csi2000_members)").fetchall()]
    con.close()
    return sorted(codes)


def members_ml(n=800, hot_top=20, hot_mv_floor=2_000_000):
    """ML主升浪同款股池:最新日流通市值前 n(排除北交所)+ 当日 ths_hot 前 hot_top 中流通市值≥200亿。"""
    con = duckdb.connect(ct.DUCKDB_PATH, read_only=True)
    sel = con.execute("SELECT MAX(trade_date) FROM daily_basic").fetchone()[0]
    liquid = [r[0] for r in con.execute(
        "SELECT ts_code FROM daily_basic WHERE trade_date=? AND ts_code NOT LIKE '%.BJ' ORDER BY circ_mv DESC LIMIT ?",
        [sel, n]).fetchall()]
    hot = [r[0] for r in con.execute(
        "SELECT ts_code FROM ths_hot WHERE data_type='热股' AND trade_date=? ORDER BY rank LIMIT ?",
        [sel.strftime("%Y%m%d"), hot_top]).fetchall()]
    if hot:
        mv = dict(con.execute("SELECT ts_code,circ_mv FROM daily_basic WHERE trade_date=?", [sel]).fetchall())
        liquid = list(dict.fromkeys(liquid + [c for c in hot if not c.endswith(".BJ") and (mv.get(c) or 0) >= hot_mv_floor]))
    con.close()
    return sorted(liquid)


def load(codes, start):
    """读成分股的后复权收盘 + BOLL/MACD/ATR 指标,返回按 (ts_code,trade_date) 排序的宽表。"""
    con = duckdb.connect(ct.DUCKDB_PATH, read_only=True)
    df = con.execute(
        """SELECT f.ts_code, f.trade_date td, d.close*a.adj_factor AS adjc, d.close AS close_raw, d.vol AS vol,
                  f.macd_dif_hfq dif, f.macd_dea_hfq dea,
                  f.boll_upper_hfq bu, f.boll_mid_hfq bm, f.boll_lower_hfq bl, f.atr_hfq atr
           FROM stk_factor_pro f
           JOIN daily d ON d.ts_code=f.ts_code AND d.trade_date=f.trade_date
           JOIN adj_factor a ON a.ts_code=f.ts_code AND a.trade_date=f.trade_date
           WHERE f.ts_code IN (SELECT UNNEST(?)) AND f.trade_date>=?
           ORDER BY f.ts_code, f.trade_date""",
        [list(codes), start],
    ).fetch_df()
    con.close()
    return df


def latest_signals(pool="ml", up_mode="mid"):
    """取最新交易日的当日信号(供前端信号页):每条带 量比/ATR/距上次信号天数/是否第二次/原始价 + 当日大盘涨跌与健康。"""
    codes = {"ml": members_ml, "csi2000": members_2000, "csi1000": members_1000}[pool]()
    df = load(codes, (pd.Timestamp.today() - pd.Timedelta(days=400)).strftime("%Y-%m-%d"))
    con = duckdb.connect(ct.DUCKDB_PATH, read_only=True)
    names = dict(con.execute("SELECT ts_code,name FROM stock_meta").fetchall())
    con.close()
    rows = []
    for ts, g in df.groupby("ts_code", sort=False):
        g = g.reset_index(drop=True)
        if len(g) < 140:
            continue
        bw = (g["bu"] - g["bl"]) / g["bm"]
        narrow = bw <= bw.rolling(120, min_periods=60).quantile(0.25)
        widen = bw > bw.shift(1)
        d = g["dif"] - g["dea"]
        crossed = (d > 0) & pd.concat([d.shift(k) <= 0 for k in range(1, 4)], axis=1).any(axis=1)
        up = g["adjc"] > (g["bu"] if up_mode == "upper" else g["bm"])
        sig = narrow.shift(1, fill_value=False) & widen & crossed & up
        sd = g["td"][sig]
        days_since = sd.diff().dt.days
        vr = g["vol"] / g["vol"].rolling(20, min_periods=10).mean()
        f = pd.DataFrame({"ts_code": ts, "date": g["td"], "close": g["close_raw"],
                          "vol_ratio": vr, "atr_pct": g["atr"] / g["adjc"]})[sig].copy()
        f["days_since"] = days_since.reindex(f.index)
        rows.append(f)
    data = pd.concat(rows, ignore_index=True)
    last = data["date"].max()
    mk = hs300_market((pd.Timestamp.today() - pd.Timedelta(days=400)).strftime("%Y-%m-%d"))
    mrow = mk[mk["date"] == last]
    mkt_up = bool(mrow["mkt_up"].iloc[0]) if len(mrow) else None
    mkt_bad = bool(mrow["mkt_bad"].iloc[0]) if len(mrow) else None
    cur = data[data["date"] == last].copy()
    cur["is_rep30"] = (cur["days_since"] <= 30).fillna(False)
    out = [{"code": r.ts_code, "name": names.get(r.ts_code, ""), "price": round(float(r.close), 2),
            "vol_ratio": round(float(r.vol_ratio), 2), "atr_pct": round(float(r.atr_pct) * 100, 1),
            "days_since": None if pd.isna(r.days_since) else int(r.days_since), "is_rep30": bool(r.is_rep30)}
           for r in cur.itertuples()]
    out.sort(key=lambda x: (not x["is_rep30"], -x["vol_ratio"]))
    return {"date": str(last.date()), "mkt_up": mkt_up, "mkt_bad": mkt_bad, "pool": pool, "signals": out}


def hs300_market(start, index_code="000300.SH"):
    """大盘口径(默认沪深300,可传中证2000等):返回 DataFrame[date, mkt_up(当天涨), mkt_bad(MA30与MA60同时走坏)]。"""
    import tushare as ts
    pro = ts.pro_api(ct._get_token())
    ix = pro.index_daily(ts_code=index_code, start_date="20200101",
                         end_date=pd.Timestamp.today().strftime("%Y%m%d"), fields="trade_date,close,pct_chg")
    ix["date"] = pd.to_datetime(ix["trade_date"])
    ix = ix.sort_values("date").reset_index(drop=True)
    c, ma30, ma60 = ix["close"], ix["close"].rolling(30).mean(), ix["close"].rolling(60).mean()
    h30 = (c > ma30) & (ma30 > ma30.shift(5))
    h60 = (c > ma60) & (ma60 > ma60.shift(5))
    ix["mkt_up"] = ix["pct_chg"] > 0
    ix["mkt_bad"] = ((~h30) & (~h60)).fillna(False)
    return ix[["date", "mkt_up", "mkt_bad"]]


def build_signals(df, squeeze_q, cross_win, up_mode="mid", hold=10):
    """逐股算 BOLL缩口→扩张 + MACD近期金叉 + 站上(中轨/上轨) 的买点,返回带前瞻收益/ATR/量能 + T+1进出场 的信号表。"""
    out = []
    for ts, g in df.groupby("ts_code", sort=False):
        g = g.reset_index(drop=True)
        if len(g) < 140:
            continue
        bw = (g["bu"] - g["bl"]) / g["bm"]
        narrow = bw <= bw.rolling(120, min_periods=60).quantile(squeeze_q)
        widen = bw > bw.shift(1)
        d = g["dif"] - g["dea"]
        crossed = (d > 0) & pd.concat([d.shift(k) <= 0 for k in range(1, cross_win + 1)], axis=1).any(axis=1)
        up = g["adjc"] > (g["bu"] if up_mode == "upper" else g["bm"])
        sig = narrow.shift(1, fill_value=False) & widen & crossed & up
        adjc = g["adjc"]
        f5 = adjc.shift(-5) / adjc - 1
        f7 = adjc.shift(-7) / adjc - 1
        f10 = adjc.shift(-10) / adjc - 1
        f20 = adjc.shift(-20) / adjc - 1
        atr_pct = g["atr"] / adjc
        vol_ratio = g["vol"] / g["vol"].rolling(20, min_periods=10).mean()
        entry_p, exit_p = adjc.shift(-1), adjc.shift(-(1 + hold))
        s = pd.DataFrame({"ts_code": ts, "date": g["td"], "f5": f5, "f7": f7, "f10": f10, "f20": f20,
                          "atr_pct": atr_pct, "vol_ratio": vol_ratio,
                          "entry_date": g["td"].shift(-1), "exit_date": g["td"].shift(-(1 + hold)),
                          "ret_gross": exit_p / entry_p - 1})[sig]
        out.append(s[s["f10"].notna()])
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def main():
    """跑研究并打印:信号数、前瞻收益、胜率、vs 基准、ATR 分桶与过滤效果。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--squeeze-q", type=float, default=0.25, help="缩口分位阈值(带宽低于过去120日该分位=震荡)")
    ap.add_argument("--cross-win", type=int, default=3, help="MACD 金叉发生在最近几日内")
    ap.add_argument("--pool", choices=["csi1000", "csi2000", "ml"], default="csi1000", help="股票池:中证1000 / 中证2000 / ML主升浪同款")
    ap.add_argument("--up", choices=["mid", "upper"], default="mid", help="向上确认:站上中轨(mid)或站上上轨(upper)")
    args = ap.parse_args()

    codes = {"ml": members_ml, "csi2000": members_2000, "csi1000": members_1000}[args.pool]()
    print(f"股池={args.pool} {len(codes)} 只,回看起点 {args.start}")
    df = load(codes, args.start)
    print(f"行情+指标 {len(df):,} 行,{df['ts_code'].nunique()} 只有数据")

    sig = build_signals(df, args.squeeze_q, args.cross_win, args.up)
    print(f"向上确认:站上{'上轨' if args.up == 'upper' else '中轨'}")
    base = df["adjc"].groupby(df["ts_code"]).transform(lambda x: x.shift(-10) / x - 1).dropna()
    print(f"\n=== 信号(BOLL缩口→扩张 + MACD近{args.cross_win}日金叉 + 站上中轨)===")
    print(f"信号数 {len(sig)}")
    if len(sig):
        for k, lbl in (("f5", "5日"), ("f10", "10日"), ("f20", "20日")):
            v = sig[k].dropna()
            print(f"  {lbl}前瞻:均值 {v.mean()*100:+.2f}%  中位 {v.median()*100:+.2f}%  胜率 {(v>0).mean()*100:.0f}%  (n={len(v)})")
        print(f"  基准(全样本10日均值):{base.mean()*100:+.2f}%  →  信号超额 {(sig['f10'].mean()-base.mean())*100:+.2f}%")

        print("\n=== ATR 是否有过滤价值(按入场 ATR/价 分5桶,看10日均值)===")
        sig2 = sig.copy()
        sig2["atr_bucket"] = pd.qcut(sig2["atr_pct"], 5, labels=["Q1低波", "Q2", "Q3", "Q4", "Q5高波"])
        tb = sig2.groupby("atr_bucket", observed=True)["f10"].agg(["mean", "median", "count"])
        for idx, row in tb.iterrows():
            print(f"  {idx}: 均值 {row['mean']*100:+.2f}%  中位 {row['median']*100:+.2f}%  (n={int(row['count'])})")
        lo = sig2[sig2["atr_pct"] <= sig2["atr_pct"].median()]["f10"]
        print(f"  低ATR过滤(≤中位):10日均值 {lo.mean()*100:+.2f}%  胜率 {(lo>0).mean()*100:.0f}%  (n={len(lo)})")

        mkt = hs300_market(args.start)
        sm = sig.merge(mkt, on="date", how="left")

        def _row(name, s):
            v = s["f10"].dropna()
            return f"  {name:<16} 10日均值 {v.mean()*100:+.2f}%  中位 {v.median()*100:+.2f}%  胜率 {(v>0).mean()*100:.0f}%  (n={len(v)})"

        print("\n=== 口径1:信号当天 沪深300 涨/跌 ===")
        print(_row("大盘当天涨", sm[sm["mkt_up"] == True]))
        print(_row("大盘当天跌", sm[sm["mkt_up"] == False]))
        print("\n=== 口径2:沪深300 走坏(MA30&MA60同时走坏,同ML)===")
        print(_row("大盘健康", sm[sm["mkt_bad"] == False]))
        print(_row("大盘走坏", sm[sm["mkt_bad"] == True]))

        up = sm[sm["mkt_up"] == True]
        print(f"\n=== 只在大盘上涨日入场:5/7/10 日对比(n={len(up)})===")
        for k, lbl in (("f5", "5日"), ("f7", "7日"), ("f10", "10日")):
            v = up[k].dropna()
            print(f"  {lbl:<4} 均值 {v.mean()*100:+.2f}%  中位 {v.median()*100:+.2f}%  胜率 {(v>0).mean()*100:.0f}%  (n={len(v)})")

        print("\n=== 量能:信号当日 量比(vol/20日均量)分5桶,看10日 ===")
        sig3 = sig.copy()
        sig3["vol_bucket"] = pd.qcut(sig3["vol_ratio"], 5, labels=["Q1缩量", "Q2", "Q3", "Q4", "Q5放量"])
        for idx, r in sig3.groupby("vol_bucket", observed=True)["f10"].agg(["mean", "median", "count"]).iterrows():
            print(f"  {idx}: 均值 {r['mean']*100:+.2f}%  中位 {r['median']*100:+.2f}%  胜率 {'—'}  (n={int(r['count'])})")

        print("\n=== 综合过滤:大盘上涨 + 低ATR(≤中位)+ 放量(量比>1)===")
        atr_med = sig["atr_pct"].median()
        comb = sm[(sm["mkt_up"] == True) & (sm["atr_pct"] <= atr_med) & (sm["vol_ratio"] > 1)]
        for k, lbl in (("f5", "5日"), ("f7", "7日"), ("f10", "10日")):
            v = comb[k].dropna()
            print(f"  {lbl:<4} 均值 {v.mean()*100:+.2f}%  中位 {v.median()*100:+.2f}%  胜率 {(v>0).mean()*100:.0f}%  (n={len(v)})")


if __name__ == "__main__":
    main()
