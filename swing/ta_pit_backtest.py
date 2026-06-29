"""ta_pit_backtest.py — 小样本 PIT 回测:对若干(股票,决策日),跑 TA-CN 综合分析(技术+消息面,
按决策日 PIT 截断)拿 买/卖/持,再对照决策日之后的真实前向收益,看"分析师卖出建议"准不准。

思路:LLM 分析师无法全量回测(慢/贵),但可小样本验证。每个 case:
  ① 设 PIT 截断=决策日 → 跑 market+news → {action}
  ② 从 DuckDB 取决策日后 N 个交易日的前向收益
  ③ 判定:卖出且后续跌 = 对;持有/买入且后续涨 = 对
诚实边界:技术面/公告/研报 PIT 干净;东财新闻按日期过滤(老日期可能返回空)。

环境：.venv312。用法：
  python swing/ta_pit_backtest.py 300903:2026-03-05 600519:2026-04-01   # 自定 股票:决策日
  python swing/ta_pit_backtest.py --fwd 20 <cases...>                   # 前向窗口(默认20交易日)
依赖：ta_analyze + DuckDB。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.expanduser("~/AI/quart"))

import duckdb
import pandas as pd

from cache_tushare import DUCKDB_PATH
import ta_analyze


def _fwd_return(code, date, n):
    """决策日后 n 个交易日的收盘前向收益(后复权);不足则取到最新。"""
    ts = code if "." in code else (code + ".SZ" if code[0] in "03" else code + ".SH")
    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    g = con.execute("""SELECT d.trade_date, d.close*a.adj_factor c FROM daily d
        JOIN adj_factor a ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date
        WHERE d.ts_code=? AND d.trade_date>=? ORDER BY d.trade_date LIMIT ?""",
        [ts, date, n + 1]).fetch_df()
    con.close()
    if len(g) < 2:
        return None, None
    c0 = g["c"].iloc[0]; cN = g["c"].iloc[-1]
    return (cN / c0 - 1) * 100, str(g["trade_date"].iloc[-1])[:10]


def main():
    ta_analyze._load_keys()
    args = [a for a in sys.argv[1:]]
    fwd = 20
    if "--fwd" in args:
        i = args.index("--fwd"); fwd = int(args[i + 1]); del args[i:i + 2]
    cases = [a.split(":") for a in args] or [["300903", "2026-06-11"]]

    rows = []
    for code, date in cases:
        try:
            state, _decision = ta_analyze.analyze(code, date)
            v = ta_analyze.analyst_verdict(state)
            act = v.get("action", "?"); conf = v.get("confidence", "")
        except Exception as e:
            act, conf = f"ERR:{repr(e)[:40]}", ""
        fr, fd = _fwd_return(code, date, fwd)
        ok = ""
        if fr is not None and act in ("买入", "卖出", "持有"):
            if act == "卖出":
                ok = "✓" if fr < 0 else "✗"
            else:
                ok = "✓" if fr >= 0 else "✗"
        rows.append((code, date, act, conf, fr, fd, ok))
        print(f"  {code} @{date}: {act}(conf {conf}) | 前向{fwd}日 {fr:+.1f}%({fd}) | {ok}" if fr is not None
              else f"  {code} @{date}: {act} | 无前向数据")

    print(f"\n=== 汇总(前向{fwd}交易日)===")
    print(f"{'股票':<10}{'决策日':<12}{'建议':<6}{'前向收益':>9}  判定")
    hit = tot = 0
    for code, date, act, conf, fr, fd, ok in rows:
        frs = f"{fr:+.1f}%" if fr is not None else "—"
        print(f"{code:<10}{date:<12}{act:<6}{frs:>9}  {ok}")
        if ok in ("✓", "✗"):
            tot += 1; hit += (ok == "✓")
    if tot:
        print(f"\n方向命中: {hit}/{tot} = {hit/tot*100:.0f}%")


if __name__ == "__main__":
    main()
