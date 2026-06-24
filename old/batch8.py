"""8组 x 5只中价位股票 批量回测"""

import sys
import os
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(__file__))

import config
from data_loader import prepare_data
from pattern import find_boxes
from strategy import backtest_box

# 40只中价位主板股票分8组（排除创业板300/301/科创板688）
GROUPS = [
    # 第1组
    ["001268.SZ", "600114.SH", "600562.SH", "003017.SZ", "002396.SZ"],
    # 第2组
    ["600699.SH", "603477.SH", "605566.SH", "000021.SZ", "603005.SH"],
    # 第3组
    ["603895.SH", "605058.SH", "603277.SH", "605338.SH", "605003.SH"],
    # 第4组
    ["605080.SH", "002912.SZ", "600990.SH", "000933.SZ", "603192.SH"],
    # 第5组
    ["002270.SZ", "002832.SZ", "001266.SZ", "603931.SH", "600764.SH"],
    # 第6组
    ["605589.SH", "000766.SZ", "000403.SZ", "001319.SZ", "600455.SH"],
    # 第7组
    ["601089.SH", "603298.SH", "603508.SH", "603505.SH", "001211.SZ"],
    # 第8组
    ["002737.SZ", "001207.SZ", "002557.SZ", "605020.SH", "600460.SH"],
]

CAPITAL = 48000


def run_backtest():
    lines = []

    def log(text=""):
        lines.append(text)
        print(text)

    log(f"中价位股票批量回测  8组 x 5只")
    log(f"起始资金: {CAPITAL}  网格间距: {config.GRID_STEP}  价格区间: {config.PRICE_MIN}~{config.PRICE_MAX}")
    log(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"{'='*90}")

    all_results = []
    group_summaries = []

    for g_idx, group in enumerate(GROUPS):
        log(f"\n{'='*90}")
        log(f"第 {g_idx+1} 组")
        log(f"{'='*90}")

        group_profit = 0
        group_count = 0

        for code in group:
            filepath = os.path.join(config.DATA_DIR, f"{code}.csv")
            df = prepare_data(filepath)
            if df is None:
                log(f"  {code}  跳过（异常股票）")
                continue

            name = df.iloc[0]["name"]
            boxes = find_boxes(df)

            if not boxes:
                log(f"  {code} {name:8s}  未识别到箱体")
                all_results.append({"code": code, "name": name, "profit": 0, "profit_pct": 0,
                                    "trades": 0, "max_dd": 0, "boxes": 0})
                continue

            # 计算截止索引避免箱体重叠
            box_end_indices = []
            for i, box in enumerate(boxes):
                if i + 1 < len(boxes):
                    next_box = boxes[i + 1]
                    next_start = next_box["start_idx"] + next_box.get("window", 60)
                    box_end_indices.append(next_start)
                else:
                    box_end_indices.append(None)

            total_profit = 0
            total_trades = 0
            max_dd = 0
            rolling_capital = CAPITAL

            for i, box in enumerate(boxes):
                result = backtest_box(df, box, rolling_capital, end_idx=box_end_indices[i])
                rolling_capital = result['final_value']
                total_profit += result["profit"]
                total_trades += result["total_trades"]
                if result["max_drawdown_pct"] > max_dd:
                    max_dd = result["max_drawdown_pct"]

            profit_pct = total_profit / CAPITAL * 100

            log(f"  {code} {name:8s}  箱体={len(boxes)}  "
                f"盈亏={total_profit:+8.0f} ({profit_pct:+6.1f}%)  "
                f"交易={total_trades:3d}次  最大回撤={max_dd:.1f}%")

            all_results.append({
                "code": code, "name": name,
                "profit": total_profit, "profit_pct": profit_pct,
                "trades": total_trades, "max_dd": max_dd,
                "boxes": len(boxes),
            })

            group_profit += total_profit
            group_count += 1

        if group_count > 0:
            avg_pct = group_profit / CAPITAL / group_count * 100
            group_summaries.append({"group": g_idx + 1, "avg_pct": avg_pct, "total_profit": group_profit})
            log(f"\n  组平均收益: {avg_pct:+.1f}%  组总盈亏: {group_profit:+.0f}")

    # 汇总
    log(f"\n{'='*90}")
    log(f"总汇总")
    log(f"{'='*90}")

    traded = [r for r in all_results if r["trades"] > 0]
    win = [r for r in traded if r["profit"] > 0]
    lose = [r for r in traded if r["profit"] < 0]

    log(f"  总股票数: {len(all_results)}  有交易: {len(traded)}  盈利: {len(win)}  亏损: {len(lose)}")

    if traded:
        avg_pct = sum(r["profit_pct"] for r in traded) / len(traded)
        total_profit = sum(r["profit"] for r in all_results)
        avg_dd = sum(r["max_dd"] for r in traded) / len(traded)
        log(f"  有交易股票平均收益: {avg_pct:+.2f}%")
        log(f"  全部股票总盈亏: {total_profit:+.0f}")
        log(f"  平均最大回撤: {avg_dd:.2f}%")

    # TOP5 / BOTTOM5
    sorted_results = sorted(all_results, key=lambda x: x["profit_pct"], reverse=True)
    log(f"\n  TOP5:")
    for r in sorted_results[:5]:
        log(f"    {r['code']} {r['name']:8s}  {r['profit_pct']:+.1f}%  盈亏={r['profit']:+.0f}")
    log(f"\n  BOTTOM5:")
    for r in sorted_results[-5:]:
        log(f"    {r['code']} {r['name']:8s}  {r['profit_pct']:+.1f}%  盈亏={r['profit']:+.0f}")

    # 各组对比
    log(f"\n  各组平均收益:")
    for gs in group_summaries:
        log(f"    第{gs['group']}组: {gs['avg_pct']:+.1f}%  总盈亏={gs['total_profit']:+.0f}")

    # 写日志
    log_dir = os.path.join(os.path.dirname(__file__), "logs")
    os.makedirs(log_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(log_dir, f"batch8_mid_{timestamp}.log")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n日志已保存: {log_path}")


if __name__ == "__main__":
    run_backtest()
