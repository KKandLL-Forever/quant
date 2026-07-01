"""
行业动量轮动 ETF 策略(复现 小西西弗/MatrixSpk,原文年化35.8%)。

13 只主流行业 ETF 为池,风险调整动量,每 K 天调仓取前 L,归一化配权,权重滞后1天。
基准科创50ETF(588000),对照行业等权组合。全部走 tushare fund_daily+fund_adj 前复权。
用法:python xiaoxifu/industry.py [--N 20 --K 5 --L 5 --start 2024-01-01]
"""
import argparse
import pandas as pd
import engine

ETFS = {
    "159819.SZ": "人工智能ETF", "588000.SH": "科创50ETF", "512690.SH": "军工ETF",
    "159813.SZ": "半导体ETF", "159526.SZ": "机器人ETF嘉实", "515650.SH": "消费50ETF",
    "159869.SZ": "游戏ETF", "159740.SZ": "恒生科技ETF", "159992.SZ": "创新药ETF",
    "159755.SZ": "电池ETF", "515290.SH": "银行ETF易方达", "512200.SH": "房地产ETF",
    "159766.SZ": "旅游ETF",
}
BENCH_CODE, BENCH_NAME, STRAT_NAME = "588000.SH", "科创50ETF基准", "行业动量轮动策略"


def to_payload(n=20, k=5, l=5, start="2024-01-01", end=None):
    """给前后端用:跑行业策略回测并组装 JSON。"""
    end = end or pd.Timestamp.today().strftime("%Y-%m-%d")
    return engine.to_payload(ETFS, engine.load_fund_qfq, BENCH_CODE, BENCH_NAME, STRAT_NAME, n, k, l, start, end,
                             commission=engine.COMM_ETF, stamp=engine.STAMP_ETF)


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
