"""czsc_exit_ab.py — 出场 A/B:同样的突破入场,对比 唐奇安 / 波段止盈止损 / 缠论卖点 三种出场。

缠论出场=入场后逐日更新 CZSC,首次出现"一卖(顶背驰)或 MACD顶背驰"即平仓。其余两种沿用现有口径。
全程后复权价,收益可比;纯本地、无 LLM。

环境：.venv312。用法：python swing/czsc_exit_ab.py [股票...]   (默认一组)
依赖：czsc, run_ml_signals_2026(_swing_exit/常量), kernel_pivots(_detect_kernel), DuckDB。
"""
import os, sys
_ROOT = os.path.dirname(os.path.abspath(__file__))
while _ROOT != "/" and not os.path.exists(os.path.join(_ROOT, "cache_tushare.py")):
    _ROOT = os.path.dirname(_ROOT)
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, _ROOT)

import duckdb, numpy as np, pandas as pd
import czsc
from czsc import CZSC, RawBar, Freq
import czsc.signals as S
from cache_tushare import DUCKDB_PATH
from run_ml_signals_2026 import _swing_exit, SW_TPFRAC, COST, DON_EXIT, _adx
from kernel_pivots import _detect_kernel

STOCKS = ["300903.SZ", "002281.SZ", "688981.SH", "300750.SZ", "002594.SZ", "300059.SZ"]


def _czsc_sell(c):
    """当前是否出现缠论卖点(一卖 或 MACD顶背驰)。"""
    for fn, tag in (("cxt_first_sell_V221126", "一卖"), ("tas_macd_bc_V221201", "顶背驰")):
        f = getattr(S, fn, None)
        if not f:
            continue
        try:
            out = f(c, di=1)
        except Exception:
            try:
                out = f(c)
            except Exception:
                continue
        for v in out.values():
            tok = str(v).split("_")
            if fn == "cxt_first_sell_V221126" and tok[0] != "其他":
                return tag
            if fn == "tas_macd_bc_V221201" and tok[0] == "背驰" and "红" in str(v):
                return tag
    return None


def _czsc_exit(bars, cc, bo):
    """入场后逐日更新 CZSC,首次缠论卖点平仓;返回(收益, 持有交易日)。"""
    c = CZSC(bars[:bo + 1])
    for t in range(bo + 1, len(bars)):
        c.update(bars[t])
        if _czsc_sell(c):
            return cc[t] / cc[bo] - 1 - 2 * COST, t - bo
    return cc[-1] / cc[bo] - 1 - 2 * COST, len(bars) - 1 - bo


def _don_exit(cc, low, bo):
    """唐奇安20破位出场(纯价格,不含大盘gate),返回(收益, 持有交易日)。"""
    dlow = pd.Series(low).rolling(DON_EXIT).min().shift(1).to_numpy()
    for t in range(bo + 1, len(cc)):
        if not np.isnan(dlow[t]) and cc[t] < dlow[t]:
            return cc[t] / cc[bo] - 1 - 2 * COST, t - bo
    return cc[-1] / cc[bo] - 1 - 2 * COST, len(cc) - 1 - bo


def main():
    codes = sys.argv[1:] or STOCKS
    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    rows = []
    for ts in codes:
        g = con.execute("""SELECT d.trade_date, d.open*a.adj_factor o, d.high*a.adj_factor h,
            d.low*a.adj_factor l, d.close*a.adj_factor c, d.vol v, d.amount amt
            FROM daily d JOIN adj_factor a ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date
            WHERE d.ts_code=? AND d.trade_date>='2023-01-01' ORDER BY d.trade_date""", [ts]).fetch_df()
        if len(g) < 200:
            continue
        g["trade_date"] = pd.to_datetime(g["trade_date"])
        cc = g["c"].to_numpy(); low = g["l"].to_numpy(); gd = g["trade_date"].to_numpy()
        atr = _adx(g["h"], g["l"], g["c"])[1].to_numpy()
        bars = [RawBar(symbol=ts, id=i, dt=g["trade_date"].iloc[i], freq=Freq.D,
                       open=g["o"].iloc[i], close=g["c"].iloc[i], high=g["h"].iloc[i],
                       low=g["l"].iloc[i], vol=g["v"].iloc[i], amount=g["amt"].iloc[i]) for i in range(len(g))]
        ents = [bo for _, bo, _ in _detect_kernel(cc, 4.0, 30)
                if pd.Timestamp(gd[bo]).year in (2024, 2025) and bo < len(cc) - 25][:4]
        for bo in ents:
            dr, dd = _don_exit(cc, low, bo)
            sr, so, _se = _swing_exit(cc, gd, bo, atr[bo], SW_TPFRAC)[:3]
            sd = 0  # 波段持有天数略
            cr, cd = _czsc_exit(bars, cc, bo)
            rows.append((ts, str(gd[bo])[:10][:10], dr, dd, sr, cr, cd))
    con.close()

    df = pd.DataFrame(rows, columns=["ts", "entry", "don", "don_d", "sw", "czsc", "czsc_d"])
    print(f"\n样本数: {len(df)}(入场=核平滑突破,2024-2025)")
    for col, dcol, nm in [("don", "don_d", "唐奇安"), ("sw", None, "波段"), ("czsc", "czsc_d", "缠论卖点")]:
        v = df[col]
        hold = f" 持有{df[dcol].mean():.0f}天" if dcol else ""
        print(f"  {nm:<8} 平均{v.mean()*100:+6.1f}%  中位{v.median()*100:+6.1f}%  胜率{(v>0).mean()*100:3.0f}%{hold}")
    print("\n逐条:")
    for _, r in df.iterrows():
        print(f"  {r.ts} {r.entry}: 唐奇安{r.don*100:+.0f}% 波段{r.sw*100:+.0f}% 缠论{r.czsc*100:+.0f}%(持{r.czsc_d}天)")


if __name__ == "__main__":
    main()
