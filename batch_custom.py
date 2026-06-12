"""自定义股票组 批量回测（BOLL动态箱体）"""

import sys
import os
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(__file__))

import config
from data_loader import prepare_data
from strategy import backtest_boll

# 31只股票分4组
GROUPS = [
    # 第1组 (8只)
    ["000591.SZ", "600666.SH", "000539.SZ", "600982.SH",
     "002479.SZ", "600930.SH", "600310.SH", "002616.SZ"],
    # 第2组 (8只)
    ["000862.SZ", "000601.SZ", "002218.SZ", "000027.SZ",
     "000531.SZ", "600780.SH", "600011.SH", "600173.SH"],
    # 第3组 (8只)
    ["300040.SZ", "600744.SH", "600163.SH", "000791.SZ",
     "002608.SZ", "600396.SH", "601330.SH", "000543.SZ"],
    # 第4组 (7只)
    ["600956.SH", "600821.SH", "600642.SH", "601985.SH",
     "001258.SZ", "000600.SZ", "600032.SH"],
]


def backtest_stock(code: str) -> dict:
    filepath = os.path.join(config.DATA_DIR, f"{code}.csv")
    if not os.path.exists(filepath):
        return {"code": code, "name": "?", "profit": 0, "profit_pct": 0,
                "trades": 0, "max_dd": 0, "error": "文件不存在"}

    df = prepare_data(filepath)
    if df is None:
        return {"code": code, "name": "?", "profit": 0, "profit_pct": 0,
                "trades": 0, "max_dd": 0, "error": "异常股票"}

    name = df.iloc[0]["name"]
    result = backtest_boll(df, config.TOTAL_CAPITAL)

    return {
        "code": code, "name": name,
        "profit": round(result["profit"], 2),
        "profit_pct": round(result["profit_pct"], 2),
        "trades": result["total_trades"],
        "max_dd": round(result["max_drawdown_pct"], 2),
        "error": None,
    }


def run_backtest():
    lines = []

    def log(text=""):
        lines.append(text)
        print(text)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log(f"回测报告（自定义组）  生成时间: {now}")
    log(f"策略: BOLL动态箱体  周期={config.BOLL_PERIOD}  标准差={config.BOLL_STD}  "
        f"价格线={config.BOLL_PRICE_LINES}  资金={config.TOTAL_CAPITAL}元  "
        f"止损={config.STOP_LOSS_PCT*100:.0f}%  止盈={config.TAKE_PROFIT_PCT*100:.0f}%  "
        f"回测天数={config.BACKTEST_DAYS}")

    all_group_results = []
    all_stock_results = []
    total_stocks = sum(len(g) for g in GROUPS)

    for gi, group in enumerate(GROUPS):
        log(f"\n{'='*76}")
        log(f"  第 {gi+1} 组")
        log(f"{'='*76}")
        log(f"  {'代码':<12} {'名称':<10} {'交易':>4} "
            f"{'盈亏':>10} {'收益率':>8} {'最大回撤':>8}  备注")
        log(f"  {'-'*76}")

        group_profits = []
        for code in group:
            r = backtest_stock(code)
            all_stock_results.append(r)
            err = r["error"] or ""
            log(f"  {r['code']:<12} {r['name']:<10} {r['trades']:>4} "
                f"{r['profit']:>+10.2f} {r['profit_pct']:>+7.2f}% {r['max_dd']:>7.2f}%  {err}")
            group_profits.append(r["profit_pct"])

        avg_pct = sum(group_profits) / len(group_profits)
        win = sum(1 for p in group_profits if p > 0)
        loss = sum(1 for p in group_profits if p < 0)
        log(f"  {'-'*76}")
        log(f"  组平均收益: {avg_pct:+.2f}%  |  盈利 {win} 只  亏损 {loss} 只")
        all_group_results.append({"group": gi + 1, "avg_pct": avg_pct, "win": win, "loss": loss})

    # 总汇总
    log(f"\n{'='*76}")
    log(f"  总汇总（{len(GROUPS)}组 共{total_stocks}只，回测{config.BACKTEST_DAYS}个交易日）")
    log(f"{'='*76}")
    for gr in all_group_results:
        log(f"  第{gr['group']}组: 平均收益 {gr['avg_pct']:+.2f}%  盈利{gr['win']}只 亏损{gr['loss']}只")

    total_avg = sum(g["avg_pct"] for g in all_group_results) / len(all_group_results)
    total_win = sum(g["win"] for g in all_group_results)
    total_loss = sum(g["loss"] for g in all_group_results)
    total_even = total_stocks - total_win - total_loss
    log(f"  {'-'*40}")
    log(f"  全部平均收益: {total_avg:+.2f}%")
    log(f"  全部胜率: {total_win}/{total_stocks} = "
        f"{total_win/total_stocks*100:.1f}%")

    # TOP5 / BOTTOM5
    sorted_stocks = sorted(all_stock_results, key=lambda x: x["profit_pct"], reverse=True)
    log(f"\n  收益 TOP 5:")
    for r in sorted_stocks[:5]:
        log(f"    {r['code']} {r['name']:<10} {r['profit_pct']:>+7.2f}%  回撤{r['max_dd']:.2f}%")
    log(f"  收益 BOTTOM 5:")
    for r in sorted_stocks[-5:]:
        log(f"    {r['code']} {r['name']:<10} {r['profit_pct']:>+7.2f}%  回撤{r['max_dd']:.2f}%")

    return "\n".join(lines)


if __name__ == "__main__":
    output = run_backtest()

    log_dir = os.path.join(os.path.dirname(__file__), "logs")
    os.makedirs(log_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(log_dir, f"backtest_custom_{timestamp}.log")

    with open(log_path, "w", encoding="utf-8") as f:
        f.write(output)

    print(f"\n日志已保存: {log_path}")
