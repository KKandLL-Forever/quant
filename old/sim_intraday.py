"""模拟盘中人工网格交易 - 用日内高低价模拟实时触发"""

import sys
import os
import math

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(__file__))

import config
from data_loader import prepare_data
from strategy import _build_boll_grid, _calc_grid_lines, calc_commission, check_entry_safe

DAYS = 300


def run(code: str):
    filepath = os.path.join(config.DATA_DIR, f"{code}.csv")
    df = prepare_data(filepath, recent_days=DAYS + config.BOLL_PERIOD + 60)
    if df is None:
        print("数据加载失败")
        return

    # 截取最近DAYS天
    if len(df) > DAYS:
        df = df.iloc[-DAYS:].reset_index(drop=True)

    cash = config.TOTAL_CAPITAL
    holding = 0
    entry_price = None
    grid_levels = None
    shares_per_grid = 0
    is_positioned = False
    trades = []
    peak_value = cash
    max_drawdown = 0.0

    name = df.iloc[0]["name"]
    print(f"盘中模拟网格交易  {code} {name}")
    print(f"数据范围: {df.iloc[0]['date'].strftime('%Y-%m-%d')} ~ {df.iloc[-1]['date'].strftime('%Y-%m-%d')}")
    print(f"初始资金: {cash:.2f}")
    print()
    print(f"  {'日期':<12} {'操作':<20} {'成交价':>8} {'股数':>8} {'手续费':>8} "
          f"{'现金':>10} {'持仓':>6} {'总资产':>10}  备注")
    print(f"  {'-'*100}")

    for i in range(len(df)):
        row = df.iloc[i]
        date = row["date"].strftime("%Y-%m-%d")
        close = row["close"]
        low = row["low"]
        high = row["high"]
        open_price = row["open"]

        if math.isnan(close) or math.isnan(open_price):
            continue

        boll_lower = row.get("boll_lower", float("nan"))
        boll_upper = row.get("boll_upper", float("nan"))
        has_boll = not (math.isnan(boll_lower) or math.isnan(boll_upper))

        # === 未建仓：等BOLL下轨 ===
        if not is_positioned:
            if not has_boll:
                continue

            boll_mid = row.get("boll_mid", float("nan"))
            if not math.isnan(boll_mid) and boll_mid > 0:
                bw = (boll_upper - boll_lower) / boll_mid
                if bw < config.BOLL_BW_MIN:
                    continue

            if low <= boll_lower * (1 + config.BOX_TOUCH_PCT):
                is_safe, reason = check_entry_safe(df, i)
                if not is_safe:
                    continue

                # 建网格
                box_low = boll_lower
                box_high = boll_upper
                atr = row.get("atr", float("nan"))
                if not math.isnan(atr):
                    n_lines = _calc_grid_lines(atr, box_high - box_low)
                else:
                    n_lines = config.BOLL_PRICE_LINES
                grid_levels = _build_boll_grid(box_low, box_high, n_lines)

                # 用当天收盘价建仓（模拟盘中发现信号后买入）
                buy_price = close
                lot_unit = n_lines * 100
                max_shares = int(cash / buy_price)
                lots = max_shares // lot_unit
                if lots == 0:
                    continue
                total_shares = lots * lot_unit

                cost = total_shares * buy_price
                fee = calc_commission(cost, is_sell=False)
                if cost + fee > cash:
                    total_shares -= lot_unit
                    if total_shares <= 0:
                        continue
                    cost = total_shares * buy_price
                    fee = calc_commission(cost, is_sell=False)

                cash -= cost + fee
                holding = total_shares
                entry_price = buy_price
                box_high = boll_upper
                is_positioned = True
                shares_per_grid = total_shares // n_lines

                # 找到当前所在网格
                current_grid = 0
                for gi in range(len(grid_levels)):
                    if buy_price >= grid_levels[gi]:
                        current_grid = gi

                grid_str = " ".join([f"{g:.2f}" for g in grid_levels])
                total_val = cash + holding * close
                print(f"  {date:<12} {'建仓':<20} {buy_price:>8.2f} {'+'+str(total_shares):>8} "
                      f"{fee:>8.2f} {cash:>10.2f} {holding:>6} {total_val:>10.2f}  "
                      f"[{n_lines}线 格:{grid_str}]")
            continue

        if not is_positioned:
            continue

        # === 已建仓 ===

        # 止损检查
        if low <= entry_price * (1 - config.STOP_LOSS_PCT):
            sell_price = entry_price * (1 - config.STOP_LOSS_PCT)
            sell_price = max(sell_price, low)
            amount = holding * sell_price
            fee = calc_commission(amount, is_sell=True)
            cash += amount - fee
            total_val = cash
            print(f"  {date:<12} {'止损清仓':<20} {sell_price:>8.2f} {'-'+str(holding):>8} "
                  f"{fee:>8.2f} {cash:>10.2f} {0:>6} {total_val:>10.2f}")
            holding = 0
            is_positioned = False
            entry_price = None
            box_high = None
            grid_levels = None
            continue

        # 止盈检查（用建仓时的box_high）
        if box_high is not None and high >= box_high * (1 + config.TAKE_PROFIT_PCT):
            sell_price = min(box_high * (1 + config.TAKE_PROFIT_PCT), high)
            amount = holding * sell_price
            fee = calc_commission(amount, is_sell=True)
            cash += amount - fee
            total_val = cash
            print(f"  {date:<12} {'止盈清仓':<20} {sell_price:>8.2f} {'-'+str(holding):>8} "
                  f"{fee:>8.2f} {cash:>10.2f} {0:>6} {total_val:>10.2f}")
            holding = 0
            is_positioned = False
            entry_price = None
            box_high = None
            grid_levels = None
            continue

        # 盘中网格交易：用high/low模拟
        # 判断今天相对昨天的方向
        if i == 0:
            continue
        prev_close = df.iloc[i - 1]["close"]

        # 今天盘中可能先涨后跌，或先跌后涨
        # 简化：如果open > prev_close，认为先涨后跌；反之先跌后涨
        if open_price >= prev_close:
            # 先涨（用high检查卖出），再跌（用low检查买入）
            price_sequence = [("up", high), ("down", low)]
        else:
            # 先跌（用low检查买入），再涨（用high检查卖出）
            price_sequence = [("down", low), ("up", high)]

        day_trades = []
        for direction, price in price_sequence:
            if direction == "up" and holding > 0:
                # 价格上涨触碰网格线 → 卖出
                for gi in range(len(grid_levels) - 1, current_grid, -1):
                    if price >= grid_levels[gi] and holding >= shares_per_grid:
                        sell_shares = shares_per_grid
                        sell_price = grid_levels[gi]
                        amount = sell_shares * sell_price
                        fee = calc_commission(amount, is_sell=True)
                        cash += amount - fee
                        holding -= sell_shares
                        current_grid = gi
                        total_val = cash + holding * close
                        day_trades.append(
                            f"  {date:<12} {'卖@'+f'{sell_price:.2f}':<20} {sell_price:>8.2f} "
                            f"{'-'+str(sell_shares):>8} {fee:>8.2f} {cash:>10.2f} "
                            f"{holding:>6} {total_val:>10.2f}  格{gi}")
                        break  # 一个方向只触发一次

            elif direction == "down":
                # 价格下跌触碰网格线 → 买入
                for gi in range(0, current_grid):
                    if price <= grid_levels[gi] and cash >= shares_per_grid * grid_levels[gi]:
                        buy_shares = shares_per_grid
                        buy_price = grid_levels[gi]
                        cost = buy_shares * buy_price
                        fee = calc_commission(cost, is_sell=False)
                        if cost + fee > cash:
                            continue
                        cash -= cost + fee
                        holding += buy_shares
                        current_grid = gi
                        total_val = cash + holding * close
                        day_trades.append(
                            f"  {date:<12} {'买@'+f'{buy_price:.2f}':<20} {buy_price:>8.2f} "
                            f"{'+'+str(buy_shares):>8} {fee:>8.2f} {cash:>10.2f} "
                            f"{holding:>6} {total_val:>10.2f}  格{gi}")
                        break  # 一个方向只触发一次

        for t in day_trades:
            print(t)
            trades.append(t)

        # 更新回撤
        total_value = cash + holding * close
        if total_value > peak_value:
            peak_value = total_value
        dd = (peak_value - total_value) / peak_value
        if dd > max_drawdown:
            max_drawdown = dd

    # 结算
    last_close = df.iloc[-1]["close"]
    final_value = cash + holding * last_close
    profit = final_value - config.TOTAL_CAPITAL
    profit_pct = profit / config.TOTAL_CAPITAL * 100

    print(f"  {'-'*100}")
    print()
    print(f"最终: 现金={cash:.2f}  持仓={holding}股  总市值={final_value:.2f}")
    print(f"盈亏={profit:+.2f} ({profit_pct:+.2f}%)  最大回撤={max_drawdown*100:.2f}%")
    print(f"盘中交易次数: {len(trades)}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python sim_intraday.py <股票代码>")
        print("示例: python sim_intraday.py 603326.SH")
        sys.exit(1)

    stock_code = sys.argv[1]
    # 自动补后缀
    if "." not in stock_code:
        if stock_code.startswith("6"):
            stock_code += ".SH"
        else:
            stock_code += ".SZ"

    import io
    from datetime import datetime

    # 先打印到屏幕
    run(stock_code)

    # 再捕获一份到文件
    log_dir = os.path.join(os.path.dirname(__file__), "logs")
    os.makedirs(log_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(log_dir, f"sim_intraday_{stock_code}_{timestamp}.log")

    old_stdout = sys.stdout
    sys.stdout = buf = io.StringIO()
    run(stock_code)
    sys.stdout = old_stdout
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(buf.getvalue())
    print(f"\n日志已保存: {log_path}")
