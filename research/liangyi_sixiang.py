"""两仪四象(自适应周期 + 成交量加权动能)趋势跟踪策略复现与评估。

策略(文华/TB 语义,始终在场、多空反手):
  EF   = MAX(HHV(C,N1)-LLV(C,N1), ABS(C-REF(C,N1))) / SUM(ABS(C-REF(C,1)),N1)   # Kaufman效率系数变体
  EFMA = EMA(EF,N1)
  MER  = INTPART(N1 - (EFMA-0.5)*N2)                                             # 趋势强→周期短,震荡→周期长
  LY   = SUM((C-REF(C,1))*VOL, MER)                                             # 动态周期内的量加权动能
  LYSX = EMA(LY,N1)
  LYSX>0 做多 / LYSX<0 做空(T+1 执行,反手计双边成本)

用法: python research/liangyi_sixiang.py [--n1 10 --n2 10] [--cost-bps 2]
标的: 只在可做空的指数上评估(个股不能裸空);默认扫 上证/创业板指/中证1000。

结论(2015-2026,指数级,N1=10/N2=10,单边成本2bp):不稳健、非 alpha,不推荐实盘。
  · 相对 buy&hold 只在 中证1000 赢(年化7.0% vs 2.7%,夏普0.38 vs 0.24);上证(-3.6% vs 1.9%)、
    创业板指(7.3% vs 8.7%)均输 → 3 个标的只 1 个跑赢,不具跨标的稳健性。
  · 中证1000 的超额几乎全来自 3 个空头/趋势大年:2018(+3% vs -37%)、2022(+24% vs -22%)、
    2024(+48% vs +1%);而在震荡牛里被反复打脸:2020(-3% vs +19%)、2021(-17% vs +21%)、
    2025(-2.5% vs +28%)、2026(-3.8% vs +8%)——近两年持续跑输。典型趋势系统:靠熊市/单边行情的
    空头腿吃「危机 alpha」,在区间震荡牛里被 whipsaw 磨死。
  · 始终在场无止损 → 最大回撤 -60%~-66%,不优于甚至差于 buy&hold,风险调整后并无改善。
  · 参数网格夏普 0.0~0.47,好区间集中在 N1≈10 一条带(N1=20 整排塌到 0.05~0.15),边际过拟合。
  · 且「做空指数」只能借道股指期货(升贴水/移仓)或 ETF(难融券),指数级 gross 结果还高估了可实现收益。
  → 与总纲一致:纯技术趋势系统剥 beta 后无系统性 alpha,edge 是熊市空头的风险溢价而非选股/择时能力。

商品期货主连验证(--futures,读本地 fut_daily 缓存,2013~2026,12品种,单边3bp):碳酸锂神图=选择偏差,已证伪。
  · 碳酸锂单品种确实惊艳(策略年化+18% vs BH-11%,夏普0.59)——但那是 2023-2025 碳酸锂单边
    崩盘(50万→7万)一波大空头喂出来的,单品种单段行情,正是原图 LC2609 截图的来源。
  · 换成 12 个流动品种(螺纹/铜/银/橡胶/豆粕/铁矿/棕榈/焦炭/PTA/白糖/甲醇+碳酸锂)等权组合(2013~2026):
    策略年化 1.5%/夏普 0.19/回撤 -38%,仍不及 BH多头 2.1%/0.22;除碳酸锂/铁矿外几乎每个品种都跑输,
    近年(2022 -12%、2024 -13%)被震荡行情反复打脸。早年(2013-2015 熊市)靠空头腿赢,是危机溢价非 alpha。
  · 主连换月跳空本会给趋势系统「送」人为动量(利好方向),即便如此组合仍不敌纯多头 → 更坐实无真实 edge。
  → 单品种截图 = 幸存者/选择偏差的教科书案例:一个跑赢的合约不代表系统有效,多品种长历史组合才算数。
  数据源:futures_cache.py 缓存的 fut_daily 主连(需先跑一次)。留档。
"""
import argparse
import sys
import duckdb
import numpy as np
import pandas as pd
from cache_tushare import DUCKDB_PATH

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")   # WHY: Windows GBK 控制台会把中文表头打成乱码

INDICES = {"000001.SH": "上证指数", "399006.SZ": "创业板指", "000852.SH": "中证1000"}
START = "2015-01-01"


def _ema(x: np.ndarray, n: int) -> np.ndarray:
    """通达信 EMA:alpha=2/(n+1),递归 (adjust=False)。"""
    return pd.Series(x).ewm(span=n, adjust=False).mean().to_numpy()


def signal(close: np.ndarray, vol: np.ndarray, n1: int, n2: int) -> np.ndarray:
    """返回每根K线的多空持仓 sign(LYSX) ∈ {-1,0,1}。"""
    c = pd.Series(close)
    hhv = c.rolling(n1).max().to_numpy()
    llv = c.rolling(n1).min().to_numpy()
    dref = np.abs(close - np.concatenate([[np.nan] * n1, close[:-n1]]))
    num = np.maximum(hhv - llv, dref)
    step = np.abs(np.diff(close, prepend=close[0]))
    den = pd.Series(step).rolling(n1).sum().to_numpy()
    ef = np.divide(num, den, out=np.zeros_like(num), where=(den > 0))
    efma = _ema(ef, n1)

    mer = np.floor(n1 - (efma - 0.5) * n2)
    mer = np.clip(np.nan_to_num(mer, nan=n1), 2, 3 * n1).astype(int)

    pv = np.concatenate([[0.0], np.diff(close)]) * vol
    cs = np.concatenate([[0.0], np.cumsum(pv)])
    idx = np.arange(len(close))
    lo = np.clip(idx - mer + 1, 0, None)
    ly = cs[idx + 1] - cs[lo]

    lysx = _ema(ly, n1)
    return np.sign(lysx)


def backtest(close: np.ndarray, vol: np.ndarray, pct: np.ndarray, n1: int, n2: int, cost: float):
    """多空反手回测。返回净值序列 + 指标。ret 用指数 pct_chg;pos T+1 生效;反手计双边成本。"""
    pos = signal(close, vol, n1, n2)
    held = np.concatenate([[0.0], pos[:-1]])
    turnover = np.abs(np.diff(held, prepend=0.0))
    strat = held * pct - turnover * cost
    flips = int((np.abs(np.diff(pos)) > 0).sum())
    eq = np.cumprod(1 + strat)
    bh = np.cumprod(1 + pct)
    return strat, eq, bh, flips


def metrics(r: np.ndarray) -> dict:
    """年化/夏普/最大回撤/胜率。"""
    r = r[np.isfinite(r)]
    if len(r) < 30:
        return {"cagr": np.nan, "sharpe": np.nan, "mdd": np.nan, "win": np.nan}
    eq = np.cumprod(1 + r)
    yrs = len(r) / 244
    cagr = eq[-1] ** (1 / yrs) - 1
    sharpe = r.mean() / r.std() * np.sqrt(244) if r.std() > 0 else np.nan
    mdd = float((eq / np.maximum.accumulate(eq) - 1).min())
    win = float((r > 0).mean())
    return {"cagr": cagr, "sharpe": sharpe, "mdd": mdd, "win": win}


FUT = {
    "LC.GFE": "碳酸锂", "RB.SHF": "螺纹钢", "CU.SHF": "沪铜", "AG.SHF": "沪银",
    "RU.SHF": "橡胶", "M.DCE": "豆粕", "I.DCE": "铁矿石", "P.DCE": "棕榈油",
    "J.DCE": "焦炭", "TA.ZCE": "PTA", "SR.ZCE": "白糖", "MA.ZCE": "甲醇",
}


def load_fut(con, code: str) -> pd.DataFrame:
    """本地 DuckDB 主力连续日线(由 futures_cache.py 缓存,不复权拼接、换月有跳空);pct 用 close 环比。"""
    d = con.execute(
        "SELECT trade_date, close, vol FROM fut_daily WHERE ts_code=? ORDER BY trade_date", [code]).df()
    if not len(d):
        return pd.DataFrame()
    d["pct"] = d["close"].pct_change().fillna(0.0)
    return d


def run_futures(n1: int, n2: int, cost: float):
    """碳酸锂 + 一篮子流动商品主连:单品种 vs BH + 等权组合。读本地 fut_daily(先跑 futures_cache.py)。"""
    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    if not con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name='fut_daily'").fetchone()[0]:
        con.close()
        raise SystemExit("本地无 fut_daily 表:请先运行 python futures_cache.py 缓存期货数据")
    rets, bhs = {}, {}
    print(f"\n=== 两仪四象 · 商品期货主连 · N1={n1} N2={n2} · 单边{cost*1e4:.0f}bp ===\n")
    print(f"{'品种':<8}{'起始':>10}{'策略年化':>9}{'BH年化':>9}{'策略夏普':>9}{'BH夏普':>9}{'策略回撤':>9}{'反手':>6}")
    for code, name in FUT.items():
        df = load_fut(con, code)
        if len(df) < 200:
            print(f"{name:<8}{'(数据不足)':>10}")
            continue
        strat, _, _, flips = backtest(df["close"].to_numpy(), df["vol"].to_numpy(),
                                      df["pct"].to_numpy(), n1, n2, cost)
        idx = pd.to_datetime(df["trade_date"])
        rets[name] = pd.Series(strat, index=idx)
        bhs[name] = pd.Series(df["pct"].to_numpy(), index=idx)
        ms, mb = metrics(strat), metrics(df["pct"].to_numpy())
        print(f"{name:<8}{df['trade_date'].iloc[0]:>10}{ms['cagr']*100:>8.1f}%{mb['cagr']*100:>8.1f}%"
              f"{ms['sharpe']:>9.2f}{mb['sharpe']:>9.2f}{ms['mdd']*100:>8.1f}%{flips:>6}")

    con.close()
    port = pd.DataFrame(rets).mean(axis=1).dropna()
    portbh = pd.DataFrame(bhs).mean(axis=1).dropna()
    mp, mpb = metrics(port.to_numpy()), metrics(portbh.to_numpy())
    print(f"\n等权组合({len(rets)}品种,每日再平衡):")
    print(f"  策略  年化 {mp['cagr']*100:>6.1f}%  夏普 {mp['sharpe']:>5.2f}  回撤 {mp['mdd']*100:>6.1f}%  胜率 {mp['win']*100:.0f}%")
    print(f"  BH多头 年化 {mpb['cagr']*100:>6.1f}%  夏普 {mpb['sharpe']:>5.2f}  回撤 {mpb['mdd']*100:>6.1f}%")
    print("\n  组合逐年(策略/BH):")
    for y, g in port.groupby(port.index.year):
        b = portbh[portbh.index.year == y]
        print(f"    {y}: {((1+g).prod()-1)*100:>6.1f}% / {((1+b).prod()-1)*100:>6.1f}%")


def load(con, code: str) -> pd.DataFrame:
    df = con.execute(
        "SELECT trade_date, close, vol, pct_chg FROM index_daily WHERE ts_code=? AND trade_date>=? ORDER BY trade_date",
        [code, START]).df()
    df["pct"] = df["pct_chg"] / 100.0
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n1", type=int, default=10)
    ap.add_argument("--n2", type=int, default=10)
    ap.add_argument("--cost-bps", type=float, default=2.0)
    ap.add_argument("--grid", action="store_true", help="扫 N1/N2 网格看稳健性")
    ap.add_argument("--futures", action="store_true", help="改跑商品期货主连(碳酸锂+一篮子)")
    args = ap.parse_args()
    cost = args.cost_bps / 1e4

    if args.futures:
        run_futures(args.n1, args.n2, cost)
        return

    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    data = {code: load(con, code) for code in INDICES}
    con.close()

    print(f"\n=== 两仪四象 复现 · N1={args.n1} N2={args.n2} · 单边成本 {args.cost_bps}bp · {START}~ ===\n")
    print(f"{'指数':<10}{'策略年化':>9}{'BH年化':>9}{'策略夏普':>9}{'BH夏普':>9}{'策略回撤':>9}{'BH回撤':>9}{'胜率':>7}{'反手':>6}")
    for code, name in INDICES.items():
        df = data[code]
        strat, eq, bh, flips = backtest(df["close"].to_numpy(), df["vol"].to_numpy(),
                                        df["pct"].to_numpy(), args.n1, args.n2, cost)
        ms, mb = metrics(strat), metrics(df["pct"].to_numpy())
        print(f"{name:<10}{ms['cagr']*100:>8.1f}%{mb['cagr']*100:>8.1f}%{ms['sharpe']:>9.2f}{mb['sharpe']:>9.2f}"
              f"{ms['mdd']*100:>8.1f}%{mb['mdd']*100:>8.1f}%{ms['win']*100:>6.0f}%{flips:>6}")

    print("\n--- 逐年策略年化(中证1000,判定稳健性/是否靠个别年份)---")
    df = data["000852.SH"]
    strat, _, _, _ = backtest(df["close"].to_numpy(), df["vol"].to_numpy(), df["pct"].to_numpy(), args.n1, args.n2, cost)
    yr = pd.Series(strat, index=pd.to_datetime(df["trade_date"]))
    bh = pd.Series(df["pct"].to_numpy(), index=pd.to_datetime(df["trade_date"]))
    for y, g in yr.groupby(yr.index.year):
        b = bh[bh.index.year == y]
        print(f"  {y}: 策略 {((1+g).prod()-1)*100:>7.1f}%   BH {((1+b).prod()-1)*100:>7.1f}%")

    if args.grid:
        print("\n--- 参数网格(中证1000,策略夏普;看是否只有个别参数好=过拟合)---")
        n1s, n2s = [6, 10, 14, 20], [6, 10, 14, 20]
        hdr = "N1\\N2" + "".join(f"{n2:>8}" for n2 in n2s)
        print(hdr)
        for n1 in n1s:
            row = f"{n1:>5}"
            for n2 in n2s:
                strat, _, _, _ = backtest(df["close"].to_numpy(), df["vol"].to_numpy(),
                                          df["pct"].to_numpy(), n1, n2, cost)
                row += f"{metrics(strat)['sharpe']:>8.2f}"
            print(row)


if __name__ == "__main__":
    main()
