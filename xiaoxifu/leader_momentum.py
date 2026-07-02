"""
龙头动量轮动策略(复现 小西西弗/MatrixSpk,原文年化超110%)。

22 只各赛道龙头股为池,风险调整动量选股,每 K 天调仓取前 L,归一化配权,权重滞后1天。
基准科创50ETF(588000),对照龙头等权组合。个股走本地 DuckDB 前复权,基准走 tushare。
用法:python xiaoxifu/leader_momentum.py [--N 20 --K 5 --L 5 --start 2024-01-01]
"""
import argparse
import os
import json
import pandas as pd
import duckdb
import engine
import cache_tushare as ct

POOL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "leader_pool.json")

DEFAULT_POOL = [
    ("600111.SH", "北方稀土", "稀土"), ("002460.SZ", "赣锋锂业", "锂"), ("601899.SH", "紫金矿业", "有色"),
    ("600988.SH", "赤峰黄金", "黄金"), ("002230.SZ", "科大讯飞", "AI"), ("300750.SZ", "宁德时代", "电池"),
    ("002594.SZ", "比亚迪", "新能车"), ("603259.SH", "药明康德", "医药"), ("601939.SH", "建设银行", "银行"),
    ("688256.SH", "寒武纪", "芯片"), ("601606.SH", "长城军工", "军工"), ("688981.SH", "中芯国际", "芯片"),
    ("300502.SZ", "新易盛", "光模块"), ("601138.SH", "工业富联", "代工"), ("300308.SZ", "中际旭创", "光模块"),
    ("300476.SZ", "胜宏科技", "PCB"), ("300394.SZ", "天孚通信", "光器件"), ("688041.SH", "海光信息", "芯片"),
    ("601336.SH", "新华保险", "保险"), ("600519.SH", "贵州茅台", "白酒"), ("601288.SH", "农业银行", "银行"),
    ("601319.SH", "中国人保", "保险"),
]
STOCKS = {c: n for c, n, _ in DEFAULT_POOL}
BENCH_CODE, BENCH_NAME, STRAT_NAME = "588000.SH", "科创50ETF", "龙头动量轮动策略"


def _names(codes):
    """从 DuckDB stock_meta 取 {code:name},缺名回退用代码。"""
    con = duckdb.connect(ct.DUCKDB_PATH, read_only=True)
    m = dict(con.execute("SELECT ts_code,name FROM stock_meta WHERE ts_code IN (SELECT UNNEST(?))", [list(codes)]).fetchall())
    con.close()
    return {c: m.get(c, STOCKS.get(c, c)) for c in codes}


def saved_codes():
    """读后端持久化的自定义池代码;无文件返回默认 22 只代码。"""
    if os.path.exists(POOL_FILE):
        try:
            return [r["code"] for r in json.load(open(POOL_FILE, encoding="utf-8")) if r.get("code")]
        except Exception:
            pass
    return list(STOCKS)


def to_payload(n=20, k=5, l=5, start="2024-01-01", end=None, codes=None):
    """给前后端用:跑龙头策略回测并组装 JSON。codes 传了用之,否则用后端保存的池(无则默认 22 只)。"""
    end = end or pd.Timestamp.today().strftime("%Y-%m-%d")
    codes = [c for c in codes if c] if codes else saved_codes()
    universe = _names(codes) if codes else STOCKS
    if not universe:
        universe = STOCKS
    return engine.to_payload(universe, engine.load_stock_qfq, BENCH_CODE, BENCH_NAME, STRAT_NAME, n, k, l, start, end)


def main():
    """CLI:跑回测并打印绩效。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--N", type=int, default=20)
    ap.add_argument("--K", type=int, default=5)
    ap.add_argument("--L", type=int, default=5)
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default=pd.Timestamp.today().strftime("%Y-%m-%d"))
    args = ap.parse_args()
    p = to_payload(args.N, args.K, args.L, args.start, args.end)
    print(f"\n{STRAT_NAME}  N={args.N} K={args.K} L={args.L}  {args.start}~{args.end}")
    print(pd.DataFrame(p["summary"]).to_string(index=False))


if __name__ == "__main__":
    main()
