"""
龙头动量轮动策略(复现 小西西弗/MatrixSpk,原文年化超110%)。

22 只各赛道龙头股为池,风险调整动量选股,每 K 天调仓取前 L,归一化配权,权重滞后1天。
基准科创50ETF(588000),对照龙头等权组合。个股走本地 DuckDB 前复权,基准走 tushare。
用法:python xiaoxifu/leader_momentum.py [--N 20 --K 5 --L 5 --start 2024-01-01]
"""
import argparse
import pandas as pd
import engine

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
BENCH_CODE, BENCH_NAME, STRAT_NAME = "588000.SH", "科创50ETF", "龙头动量轮动策略"


def to_payload(n=20, k=5, l=5, start="2024-01-01", end=None):
    """给前后端用:跑龙头策略回测并组装 JSON。"""
    end = end or pd.Timestamp.today().strftime("%Y-%m-%d")
    return engine.to_payload(STOCKS, engine.load_stock_qfq, BENCH_CODE, BENCH_NAME, STRAT_NAME, n, k, l, start, end)


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
