"""5组 x 8只 中波动(30-40%)股票 批量回测（BOLL动态箱体）"""

import sys
import os
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(__file__))

import config
from data_loader import prepare_data
from strategy import backtest_boll

# 40只中波动(30-40%)、股价5-10元股票，分5组
GROUPS = [
    # 第1组 (~30-32%) 低中波动
    ["601117.SH", "000910.SZ", "601236.SH", "000539.SZ",
     "600507.SH", "002511.SZ", "601800.SH", "002332.SZ"],
    # 第2组 (~32-34%) 中波动
    ["600916.SH", "000686.SZ", "600583.SH", "002939.SZ",
     "600812.SH", "002100.SZ", "000983.SZ", "603707.SH"],
    # 第3组 (~34-36%) 中波动
    ["600048.SH", "600219.SH", "601636.SH", "002064.SZ",
     "601666.SH", "600497.SH", "002234.SZ", "600516.SH"],
    # 第4组 (~36-38%) 中高波动
    ["600637.SH", "600271.SH", "601118.SH", "000528.SZ",
     "002745.SZ", "000600.SZ", "002644.SZ", "600210.SH"],
    # 第5组 (~38-40%) 中高波动
    ["600718.SH", "600278.SH", "000501.SZ", "000011.SZ",
     "600864.SH", "600603.SH", "601949.SH", "600810.SH"],
]


def backtest_stock(code: str) -> dict:
    """对单只股票回测，返回汇总结果"""
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
    """运行全部回测，返回输出文本"""
    lines = []

    def log(text=""):
        lines.append(text)
        print(text)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log(f"回测报告（中波动组）  生成时间: {now}")
    log(f"策略: BOLL动态箱体  周期={config.BOLL_PERIOD}  标准差={config.BOLL_STD}  "
        f"价格线={config.BOLL_PRICE_LINES}  资金={config.TOTAL_CAPITAL}元  "
        f"止损={config.STOP_LOSS_PCT*100:.0f}%  止盈={config.TAKE_PROFIT_PCT*100:.0f}%  "
        f"回测天数={config.BACKTEST_DAYS}")
    log(f"选股条件: 股价5-10元  年化波动率30-40%")

    all_group_results = []
    all_stock_results = []

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
    log(f"  总汇总（5组 x 8只 = 40只，中波动，回测{config.BACKTEST_DAYS}个交易日）")
    log(f"{'='*76}")
    for gr in all_group_results:
        log(f"  第{gr['group']}组: 平均收益 {gr['avg_pct']:+.2f}%  盈利{gr['win']}只 亏损{gr['loss']}只")

    total_avg = sum(g["avg_pct"] for g in all_group_results) / len(all_group_results)
    total_win = sum(g["win"] for g in all_group_results)
    total_loss = sum(g["loss"] for g in all_group_results)
    total_even = 40 - total_win - total_loss
    log(f"  {'-'*40}")
    log(f"  全部平均收益: {total_avg:+.2f}%")
    log(f"  全部胜率: {total_win}/{total_win+total_loss+total_even} = "
        f"{total_win/(total_win+total_loss+total_even)*100:.1f}%")

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
    log_path = os.path.join(log_dir, f"backtest_midvol_{timestamp}.log")

    with open(log_path, "w", encoding="utf-8") as f:
        f.write(output)

    print(f"\n日志已保存: {log_path}")
