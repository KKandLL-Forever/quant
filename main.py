"""入口：加载数据 → BOLL动态箱体回测 → 输出结果"""

import os
import sys
import config
from data_loader import prepare_data
from strategy import backtest_boll


def run_single_stock(filepath: str):
    """对单只股票执行完整流程"""
    filename = os.path.basename(filepath)
    print(f"\n{'='*60}")
    print(f"股票文件: {filename}")
    print(f"{'='*60}")

    df = prepare_data(filepath, recent_days=config.BACKTEST_DAYS)
    if df is None:
        print("  跳过：ST/PT/退市等异常股票")
        return
    stock_name = df.iloc[0]["name"] if "name" in df.columns else "未知"
    stock_code = df.iloc[0]["code"] if "code" in df.columns else "未知"
    print(f"股票: {stock_code} {stock_name}")
    print(f"数据范围: {df.iloc[0]['date'].strftime('%Y-%m-%d')} ~ {df.iloc[-1]['date'].strftime('%Y-%m-%d')}")
    print(f"共 {len(df)} 个交易日")
    print(f"策略: BOLL动态箱体  周期={config.BOLL_PERIOD}  标准差={config.BOLL_STD}")

    result = backtest_boll(df, config.TOTAL_CAPITAL)
    _print_result(result)


def _print_result(result: dict):
    """打印回测结果"""
    print(f"\n  初始资金: {result['capital']:.2f}")
    print(f"  最终市值: {result['final_value']:.2f}")
    print(f"  盈亏: {result['profit']:+.2f} ({result['profit_pct']:+.2f}%)")
    print(f"  最大回撤: {result['max_drawdown_pct']:.2f}%")
    print(f"  交易次数: {result['total_trades']}")
    print(f"  剩余持仓: {result['remaining_shares']}股")
    print(f"  剩余现金: {result['remaining_cash']:.2f}")

    if result["trades"]:
        print(f"\n  交易明细:")
        print(f"  {'日期':<12} {'操作':<16} {'价格':>8} {'股数':>8} {'手续费':>8} {'现金余额':>10} {'持仓':>6}")
        for t in result["trades"]:
            date_str = t["date"].strftime("%Y-%m-%d") if hasattr(t["date"], "strftime") else str(t["date"])
            print(f"  {date_str:<12} {t['action']:<16} {t['price']:>8.2f} {t['shares']:>+8d} "
                  f"{t['fee']:>8.2f} {t['cash_after']:>10.2f} {t['holding_after']:>6d}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        filepath = sys.argv[1]
    else:
        filepath = os.path.join(config.DATA_DIR, "000607.SZ.csv")

    if not os.path.exists(filepath):
        print(f"文件不存在: {filepath}")
        sys.exit(1)

    run_single_stock(filepath)
