"""
龙头动量轮动策略(复现 小西西弗/MatrixSpk 公众号 R 版,原文年化超110%)。

思路:22 只各赛道龙头股为标的池,科创50ETF(588000)为基准,龙头等权组合为辅助对照。
风险调整动量 Adj_Momentum = N日日收益均值 / sqrt(N日日收益方差)。
每 K 个交易日调仓:先取原始动量(N日均值)为正的标的,再按 Adj_Momentum 降序取前 L 只,
按 Adj_Momentum 归一化配权;权重滞后 1 天(T 决策 T+1 执行)算组合日收益。
指标:年化收益/年化波动/最大回撤/夏普/卡玛。

数据:个股后复权收盘取自本地 DuckDB(daily.close*adj_factor);基准 588000 走 tushare fund_daily+fund_adj。
用法:python xiaoxifu/leader_momentum.py [--N 20 --K 5 --L 5 --start 2024-01-01]
产出:xiaoxifu/龙头动量轮动策略_N{N}_K{K}_L{L}/ 下的 csv + png。
"""
import os
import argparse
import numpy as np
import pandas as pd
import duckdb
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cache_tushare as ct

STOCKS = {
    "600111.SH": "北方稀土", "002460.SZ": "赣锋锂业", "601899.SH": "紫金矿业",
    "600988.SH": "赤峰黄金", "002230.SZ": "科大讯飞", "300750.SZ": "宁德时代",
    "002594.SZ": "比亚迪", "603259.SH": "药明康德", "601939.SH": "建设银行",
    "688256.SH": "寒武纪", "601606.SH": "长城军工", "688981.SH": "中芯国际",
    "300502.SZ": "新易盛", "601138.SH": "工业富联", "300308.SZ": "中际旭创",
    "300476.SZ": "胜宏科技", "300394.SZ": "天孚通信", "688041.SH": "海光信息",
    "601336.SH": "新华保险", "600519.SH": "贵州茅台", "601288.SH": "农业银行",
    "601319.SH": "中国人保",
}
BENCH_CODE, BENCH_NAME = "588000.SH", "科创50ETF"
STRAT_NAME, EQUAL_NAME = "龙头动量轮动策略", "等权重组合"
TRADING_DAYS = 252


def load_adj_close(codes, start, end):
    """从本地 DuckDB 读多只个股后复权收盘,返回宽表 DataFrame[index=trade_date, columns=code]。"""
    con = duckdb.connect(ct.DUCKDB_PATH, read_only=True)
    df = con.execute(
        """SELECT d.ts_code, d.trade_date, d.close*a.adj_factor AS adjc
           FROM daily d JOIN adj_factor a
             ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date
           WHERE d.ts_code IN (SELECT UNNEST(?)) AND d.trade_date BETWEEN ? AND ?""",
        [list(codes), start, end],
    ).fetch_df()
    con.close()
    return df.pivot(index="trade_date", columns="ts_code", values="adjc").sort_index()


def load_bench(start, end):
    """走 tushare fund_daily+fund_adj 取基准 588000 后复权收盘,返回 Series[index=Timestamp]。"""
    import tushare as ts
    pro = ts.pro_api(ct._get_token())
    d = pro.fund_daily(ts_code=BENCH_CODE, start_date=start.replace("-", ""),
                       end_date=end.replace("-", ""), fields="trade_date,close")
    a = pro.fund_adj(ts_code=BENCH_CODE, start_date=start.replace("-", ""),
                     end_date=end.replace("-", ""), fields="trade_date,adj_factor")
    m = d.merge(a, on="trade_date")
    m["dt"] = pd.to_datetime(m["trade_date"])
    m = m.sort_values("dt").set_index("dt")
    return (m["close"] * m["adj_factor"]).rename(BENCH_NAME)


def calc_momentum(returns, n):
    """对宽表日收益算 (原始动量=N日均值, 调整动量=N日均值/sqrt(N日方差)),返回 (mom, adj)。"""
    mom = returns.rolling(n).mean()
    vol = returns.rolling(n).var()
    return mom, mom / np.sqrt(vol)


def build_weights(mom, adj, n, k, l):
    """按调仓规则生成每日权重宽表:正原始动量筛选→调整动量前L→归一化,权重生效至下次调仓前。"""
    dates = mom.index
    cols = mom.columns
    w = pd.DataFrame(0.0, index=dates, columns=cols)
    reb = list(range(n - 1, len(dates), k))
    for j, ri in enumerate(reb):
        cm = mom.iloc[ri]
        ca = adj.iloc[ri]
        pos = cm[(cm > 0) & cm.notna()].index
        if len(pos) == 0:
            continue
        pa = ca[pos].dropna()
        top = pa.sort_values(ascending=False).head(l)
        if top.sum() <= 0:
            continue
        ww = top / top.sum()
        end = len(dates) if j == len(reb) - 1 else reb[j + 1]
        w.iloc[ri:end, w.columns.get_indexer(ww.index)] = ww.values
    return w


def perf(returns):
    """年化收益(几何)/年化波动/最大回撤/夏普/卡玛,输入日收益 Series,返回 dict。"""
    r = returns.dropna()
    n = len(r)
    if n < 2:
        return dict(年化收益=np.nan, 年化波动率=np.nan, 最大回撤=np.nan, 夏普比率=np.nan, 卡玛比率=np.nan)
    cum = (1 + r).cumprod()
    ann = cum.iloc[-1] ** (TRADING_DAYS / n) - 1
    vol = r.std() * np.sqrt(TRADING_DAYS)
    mdd = (cum / cum.cummax() - 1).min()
    sharpe = ann / vol if vol else np.nan
    calmar = ann / abs(mdd) if mdd else np.nan
    return dict(年化收益=round(ann * 100, 2), 年化波动率=round(vol * 100, 2),
                最大回撤=round(abs(mdd) * 100, 2), 夏普比率=round(sharpe, 3), 卡玛比率=round(calmar, 3))


def plot(cum, dd, outdir, n, k, l):
    """画 累计收益 + 回撤 两张对比图(策略 vs 基准 / 策略 vs 等权),存 png。失败则跳过。"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
        for f in ("PingFang SC", "Heiti SC", "Arial Unicode MS", "STHeiti"):
            if any(f in x.name for x in font_manager.fontManager.ttflist):
                plt.rcParams["font.sans-serif"] = [f]
                break
        plt.rcParams["axes.unicode_minus"] = False
    except Exception:
        return
    for other, tag in ((BENCH_NAME, "vs_benchmark"), (EQUAL_NAME, "vs_equal_weight")):
        fig, ax = plt.subplots(2, 1, figsize=(10, 8), height_ratios=[1, 0.9], sharex=True)
        for name, color in ((STRAT_NAME, "#E41A1C"), (other, "#377EB8")):
            ax[0].plot(cum.index, cum[name] * 100, label=name, color=color, lw=1.5)
            ax[1].fill_between(dd.index, dd[name] * 100, 0, color=color, alpha=0.3)
        ax[0].set_title(f"{STRAT_NAME} vs {other} (N={n}, K={k}, L={l})")
        ax[0].set_ylabel("累计收益率(%)"); ax[0].legend(frameon=False)
        ax[1].set_ylabel("回撤(%)"); ax[1].set_xlabel("日期")
        fig.tight_layout()
        fig.savefig(os.path.join(outdir, f"momentum_{tag}.png"), dpi=150)
        plt.close(fig)


def main():
    """跑完整回测:加载数据→动量→权重→三组收益→指标→存盘绘图。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--N", type=int, default=20)
    ap.add_argument("--K", type=int, default=5)
    ap.add_argument("--L", type=int, default=5)
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default=pd.Timestamp.today().strftime("%Y-%m-%d"))
    ap.add_argument("--no-bench", action="store_true", help="跳过在线拉基准(离线可用)")
    args = ap.parse_args()

    px = load_adj_close(STOCKS.keys(), args.start, args.end)
    px.index = pd.to_datetime(px.index)
    rets = px.pct_change(fill_method=None)
    mom, adj = calc_momentum(rets, args.N)
    w = build_weights(mom, adj, args.N, args.K, args.L)

    strat = (w.shift(1) * rets).sum(axis=1).rename(STRAT_NAME)
    equal = rets.mean(axis=1).rename(EQUAL_NAME)

    series = {STRAT_NAME: strat, EQUAL_NAME: equal}
    if not args.no_bench:
        try:
            bpx = load_bench(args.start, args.end).reindex(px.index).ffill()
            series[BENCH_NAME] = bpx.pct_change().rename(BENCH_NAME)
        except Exception as e:
            print(f"基准拉取失败,跳过: {e}")

    rdf = pd.DataFrame(series).loc[strat.index]
    cum = (1 + rdf.fillna(0)).cumprod() - 1
    dd = (1 + rdf.fillna(0)).cumprod().div((1 + rdf.fillna(0)).cumprod().cummax()) - 1
    summary = pd.DataFrame({name: perf(rdf[name]) for name in rdf.columns}).T
    summary.index.name = "策略"

    print(f"\n参数 N={args.N} K={args.K} L={args.L}  回测 {args.start}~{args.end}")
    print(f"交易日 {len(px)}  标的 {len(STOCKS)}\n")
    print(summary.to_string())

    outdir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          f"龙头动量轮动策略_N{args.N}_K{args.K}_L{args.L}")
    os.makedirs(outdir, exist_ok=True)
    summary.to_csv(os.path.join(outdir, "performance_summary.csv"), encoding="utf-8-sig")
    cum.to_csv(os.path.join(outdir, "cumulative_returns.csv"), encoding="utf-8-sig")
    dd.to_csv(os.path.join(outdir, "drawdowns.csv"), encoding="utf-8-sig")

    wl = w[w.gt(0).any(axis=1)].stack()
    wl = wl[wl > 0].rename("Weight").reset_index()
    wl.columns = ["Date", "Stock", "Weight"]
    wl["Name"] = wl["Stock"].map(STOCKS)
    wl.to_csv(os.path.join(outdir, "daily_weights_nonzero.csv"), index=False, encoding="utf-8-sig")
    freq = (wl.groupby("Stock").size().rename("选中天数")
            .to_frame().join(pd.Series(STOCKS, name="名称"))
            .sort_values("选中天数", ascending=False))
    freq.to_csv(os.path.join(outdir, "stock_selection_summary.csv"), encoding="utf-8-sig")
    print("\n股票选中频率(前10):"); print(freq.head(10).to_string())

    plot(cum, dd, outdir, args.N, args.K, args.L)
    print(f"\n结果已保存到 {outdir}")


if __name__ == "__main__":
    main()
