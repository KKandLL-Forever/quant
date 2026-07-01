"""
全天候动量轮动 ETF 策略(复现 小西西弗/MatrixSpk,原文年化47.4%)。

3 只跨资产 ETF(纳指/沪深300/黄金),风险调整动量,每日调仓(K=1),正动量全取归一化配权,权重滞后1天。
基准沪深300ETF(510300),对照三 ETF 等权组合。全部走 tushare fund_daily+fund_adj 前复权。
用法:python xiaoxifu/allweather.py [--N 20 --start 2024-01-01]
"""
import argparse
import pandas as pd
import engine

ETFS = {"513100.SH": "纳指ETF", "510300.SH": "沪深300ETF", "518880.SH": "黄金ETF"}
BENCH_CODE, BENCH_NAME, STRAT_NAME = "510300.SH", "沪深300ETF基准", "全天候动量轮动策略"


def to_payload(n=20, k=1, l=3, start="2024-01-01", end=None):
    """给前后端用:跑全天候策略回测并组装 JSON(每日调仓)。"""
    end = end or pd.Timestamp.today().strftime("%Y-%m-%d")
    return engine.to_payload(ETFS, engine.load_fund_qfq, BENCH_CODE, BENCH_NAME, STRAT_NAME, n, k, l, start, end,
                             commission=engine.COMM_ETF, stamp=engine.STAMP_ETF)


def main():
    """CLI:跑回测并打印绩效。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--N", type=int, default=20)
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default=pd.Timestamp.today().strftime("%Y-%m-%d"))
    args = ap.parse_args()
    p = to_payload(args.N, 1, 3, args.start, args.end)
    print(f"\n{STRAT_NAME}  N={args.N} 每日调仓  {args.start}~{args.end}")
    print(pd.DataFrame(p["summary"]).to_string(index=False))


if __name__ == "__main__":
    main()
