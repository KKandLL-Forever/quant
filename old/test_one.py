"""单只股票详细交易历史（BOLL动态箱体）"""

import sys
import os
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(__file__))

import config
from data_loader import prepare_data
from strategy import backtest_boll


# === 手动箱体配置 ===
# None = 自动BOLL策略
# 填了列表 = 手动指定每段时间的箱体，进入该时间范围的第一天自动建仓
#
# 用法示例（每行一个交易区间）:
# MANUAL_BOXES = [
#     {"start": "2025-09-04", "end": "2026-02-06", "box_low": 8, "box_high": 10.5},
#                  开始日期            结束日期          箱体下界         箱体上界
# ]
# MANUAL_BOXES = None
MANUAL_BOXES = [
    {"start": "2025-10-20", "end": "2026-01-16", "box_low": 8.6, "box_high": 9.7}
]

def run_detail(code: str):
    filepath = os.path.join(config.DATA_DIR, f"{code}.csv")
    df = prepare_data(filepath)
    if df is None:
        print("异常股票，跳过")
        return

    name = df.iloc[0]["name"]

    lines = []

    def log(text=""):
        lines.append(text)
        print(text)

    log(f"交易历史明细  {code} {name}")
    log(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"数据范围: {df.iloc[0]['date'].strftime('%Y-%m-%d')} ~ {df.iloc[-1]['date'].strftime('%Y-%m-%d')}")
    mode = "手动箱体" if MANUAL_BOXES else "BOLL动态箱体"
    log(f"策略: {mode}  周期={config.BOLL_PERIOD}  标准差={config.BOLL_STD}  "
        f"价格线={config.BOLL_PRICE_LINES}  资金={config.TOTAL_CAPITAL}")

    if MANUAL_BOXES:
        log(f"手动箱体配置:")
        for mb in MANUAL_BOXES:
            log(f"  {mb['start']} ~ {mb['end']}  箱体: {mb['box_low']:.2f} ~ {mb['box_high']:.2f}")

    result = backtest_boll(df, config.TOTAL_CAPITAL, manual_boxes=MANUAL_BOXES)

    log(f"\n初始资金: {result['capital']:.2f}")

    if not result["trades"]:
        log("无交易记录")
    else:
        log(f"\n  {'日期':<12} {'操作':<16} {'成交价':>8} {'股数':>8} {'手续费':>8} "
            f"{'现金余额':>10} {'持仓':>6} {'持仓市值':>10} {'总资产':>10}")
        log(f"  {'-'*96}")

        for t in result["trades"]:
            date_str = t["date"].strftime("%Y-%m-%d") if hasattr(t["date"], "strftime") else str(t["date"])
            day_data = df[df["date"] == t["date"]]
            if not day_data.empty:
                day_close = day_data.iloc[0]["close"]
            else:
                day_close = t["price"]

            holding_value = t["holding_after"] * day_close
            total_asset = t["cash_after"] + holding_value

            # 建仓时显示BOLL信息
            extra = ""
            if "boll_lower" in t:
                extra = f"  [BOLL {t['boll_lower']:.2f}~{t['boll_upper']:.2f} 格距{t['grid_step']:.2f} {t.get('grid_lines','?')}线]"

            log(f"  {date_str:<12} {t['action']:<16} {t['price']:>8.2f} {t['shares']:>+8d} "
                f"{t['fee']:>8.2f} {t['cash_after']:>10.2f} {t['holding_after']:>6d} "
                f"{holding_value:>10.2f} {total_asset:>10.2f}{extra}")

        log(f"  {'-'*96}")

    log(f"\n最终: 现金={result['remaining_cash']:.2f}  "
        f"持仓={result['remaining_shares']}股  "
        f"总市值={result['final_value']:.2f}  "
        f"盈亏={result['profit']:+.2f} ({result['profit_pct']:+.2f}%)  "
        f"最大回撤={result['max_drawdown_pct']:.2f}%")

    # 写入日志
    log_dir = os.path.join(os.path.dirname(__file__), "logs")
    os.makedirs(log_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(log_dir, f"trades_{code}_{timestamp}.log")

    with open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"\n交易历史已保存: {log_path}")


if __name__ == "__main__":
    code = sys.argv[1] if len(sys.argv) > 1 else "000421.SZ"
    run_detail(code)
