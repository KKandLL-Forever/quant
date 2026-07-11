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

商品期货主连验证(--futures,读本地 fut_daily 缓存,2013~2026,42品种全池,单边3bp):
  是「防御型危机alpha」,不是收益引擎;单品种高夏普=选择偏差。
  · 单品种夏普跑赢 buy&hold 的仅 12/42;且赢家清一色短历史单段趋势的新品种——氧化铝(2023起夏普1.47)、
    丁二烯橡胶(1.17)、集运欧线(1.43)、多晶硅(2024起1.38)、碳酸锂(0.59)。全是「碳酸锂型」:上市才1-2年、
    恰好一波单边,样本极小。长历史成熟品种(铜/金/IF/锌/白糖/PTA/豆粕)几乎全输。原图 LC2609 就是这12个幸存者之一。
  · 42品种等权组合(2013~2026):策略 年化2.4%/夏普0.34/回撤-18%  vs  BH多头 2.6%/0.28/-41%。
    组合夏普略胜、但收益几乎一样,赢全在回撤减半。逐年:牛市年年输BH,熊市(2013/14/15/18)靠空头腿赢。
  → 结论:多空反手趋势系统的价值是「能做空→熊市削回撤」的防御属性(危机溢价),不是选股/择时的超额收益;
    单品种漂亮截图=幸存者偏差。想用只能当组合层面的对冲叠加,且需大量品种分散、忍受长期低收益。
  数据源:futures_cache.py 缓存的 fut_daily 主连(--exchanges SHFE,CFFEX,INE,GFEX,DCE,CZCE 抓全池)。留档。
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


def load_fut(con, code: str) -> pd.DataFrame:
    """本地 DuckDB 主力连续日线(由 futures_cache.py 缓存,不复权拼接、换月有跳空);pct 用 close 环比。"""
    d = con.execute(
        "SELECT trade_date, close, vol FROM fut_daily WHERE ts_code=? ORDER BY trade_date", [code]).df()
    if not len(d):
        return pd.DataFrame()
    d["pct"] = d["close"].pct_change().fillna(0.0)
    return d


def _discover_conts(con) -> dict:
    """本地 fut_daily 里所有主连(代码点号前全字母,无月份数字)→ 品种名(取自 fut_meta,去月份数字)。"""
    import re
    codes = [r[0] for r in con.execute(
        "SELECT DISTINCT ts_code FROM fut_daily WHERE regexp_matches(ts_code, '^[A-Za-z]+\\.[A-Za-z]+$') ORDER BY ts_code").fetchall()]
    out = {}
    for c in codes:
        root = c.split(".")[0]
        nm = con.execute("SELECT name FROM fut_meta WHERE fut_code=? LIMIT 1", [root]).fetchone()
        clean = re.sub(r"\d.*$", "", str(nm[0])).strip() if nm and nm[0] else root
        out[c] = clean or root
    return out


def run_futures(n1: int, n2: int, cost: float):
    """本地缓存的全部期货主连:单品种 vs BH + 等权组合。先跑 futures_cache.py 落库。"""
    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    if not con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name='fut_daily'").fetchone()[0]:
        con.close()
        raise SystemExit("本地无 fut_daily 表:请先运行 python futures_cache.py 缓存期货数据")
    universe = _discover_conts(con)
    rets, bhs = {}, {}
    print(f"\n=== 两仪四象 · 期货主连({len(universe)}品种) · N1={n1} N2={n2} · 单边{cost*1e4:.0f}bp ===\n")
    print(f"{'品种':<10}{'起始':>10}{'策略年化':>9}{'BH年化':>9}{'策略夏普':>9}{'BH夏普':>9}{'策略回撤':>9}{'反手':>6}")
    for code, name in sorted(universe.items()):
        df = load_fut(con, code)
        if len(df) < 250:
            continue
        strat, _, _, flips = backtest(df["close"].to_numpy(), df["vol"].to_numpy(),
                                      df["pct"].to_numpy(), n1, n2, cost)
        idx = pd.to_datetime(df["trade_date"])
        rets[name] = pd.Series(strat, index=idx)
        bhs[name] = pd.Series(df["pct"].to_numpy(), index=idx)
        ms, mb = metrics(strat), metrics(df["pct"].to_numpy())
        print(f"{name:<10}{df['trade_date'].iloc[0]:>10}{ms['cagr']*100:>8.1f}%{mb['cagr']*100:>8.1f}%"
              f"{ms['sharpe']:>9.2f}{mb['sharpe']:>9.2f}{ms['mdd']*100:>8.1f}%{flips:>6}")

    con.close()
    wins = sum(1 for nm in rets if metrics(rets[nm].to_numpy())["sharpe"] > metrics(bhs[nm].to_numpy())["sharpe"])
    port = pd.DataFrame(rets).mean(axis=1).dropna()
    portbh = pd.DataFrame(bhs).mean(axis=1).dropna()
    mp, mpb = metrics(port.to_numpy()), metrics(portbh.to_numpy())
    print(f"\n单品种夏普跑赢 buy&hold 的:{wins}/{len(rets)}")
    print(f"等权组合({len(rets)}品种,每日再平衡):")
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
