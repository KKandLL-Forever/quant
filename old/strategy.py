"""网格交易策略 + 回测引擎（BOLL动态箱体 + 建仓保护）"""

import math
import pandas as pd
import config


def check_entry_safe(df: pd.DataFrame, idx: int) -> tuple[bool, str]:
    """
    检查当前是否安全建仓。

    返回 (is_safe, reason)
    - 条件1：放量下跌后持续下跌中 → 不安全
    - 条件2：MA5 < MA10 < MA20 空头排列 → 不安全
    任一条件触发即禁止建仓。
    """
    if idx < 2:
        return True, ""

    row = df.iloc[idx]

    # --- 条件2：均线空头排列 ---
    ma5 = row.get("ma5")
    ma10 = row.get("ma10")
    ma20 = row.get("ma20")
    if (ma5 is not None and ma10 is not None and ma20 is not None and
            not (math.isnan(ma5) or math.isnan(ma10) or math.isnan(ma20))):
        if ma5 < ma10 < ma20:
            return False, "均线空头排列"

    # --- 条件1：放量下跌后连续下跌 ---
    if row["close"] >= df.iloc[idx - 1]["close"]:
        return True, ""

    consecutive_fall_start = idx
    for j in range(idx, 0, -1):
        if df.iloc[j]["close"] < df.iloc[j - 1]["close"]:
            consecutive_fall_start = j
        else:
            break

    fall_days = idx - consecutive_fall_start + 1
    if fall_days < 2:
        return True, ""

    check_start = max(0, consecutive_fall_start - 1)
    check_end = min(len(df), consecutive_fall_start + 2)
    for j in range(check_start, check_end):
        r = df.iloc[j]
        v_ma = r.get("vol_ma")
        if (v_ma is not None and not math.isnan(v_ma) and v_ma > 0 and
                r["volume"] >= v_ma * config.VOLUME_SURGE_RATIO and
                r["close"] < r["open"]):
            return False, f"放量下跌中(连跌{fall_days}天)"

    return True, ""


def check_volume_reversal(df: pd.DataFrame, idx: int) -> tuple[bool, str]:
    """
    检查止损后是否出现放量反转信号。

    条件（全部满足才触发）：
    1. 最近5天中至少4天：放量阳线（volume > vol_ma 且 close > open）
    2. MA5拐头向上（当天MA5 > 前一天MA5）
    3. 收盘价站上MA5
    """
    lookback = config.REVERSAL_LOOKBACK
    required = config.REVERSAL_UP_DAYS

    if idx < lookback:
        return False, ""

    row = df.iloc[idx]

    # 条件3：收盘价站上MA5
    ma5 = row.get("ma5")
    if ma5 is None or math.isnan(ma5) or row["close"] <= ma5:
        return False, "价格未站上MA5"

    # 条件2：MA5拐头向上
    prev_ma5 = df.iloc[idx - 1].get("ma5")
    if prev_ma5 is None or math.isnan(prev_ma5) or ma5 <= prev_ma5:
        return False, "MA5未拐头"

    # 条件1：最近5天中至少4天放量阳线
    up_days = 0
    for j in range(idx - lookback + 1, idx + 1):
        r = df.iloc[j]
        vol_ma = r.get("vol_ma")
        if (vol_ma is not None and not math.isnan(vol_ma) and vol_ma > 0
                and r["volume"] > vol_ma and r["close"] > r["open"]):
            up_days += 1

    if up_days < required:
        return False, f"放量阳线不足({up_days}/{required})"

    return True, f"放量反转({up_days}天)"


def calc_commission(amount: float, is_sell: bool) -> float:
    """计算交易手续费"""
    commission = max(amount * config.COMMISSION_RATE, config.COMMISSION_MIN)
    if is_sell:
        commission += amount * config.STAMP_TAX_RATE
    return round(commission, 2)


def _calc_grid_lines(atr: float, box_range: float) -> int:
    """根据ATR与箱体宽度的比例，动态计算网格线数。"""
    if box_range <= 0 or atr <= 0:
        return config.BOLL_PRICE_LINES
    # ATR占箱体的比例越大 → 波动剧烈 → 线越少
    ratio = atr / box_range
    # ratio ~0.15 为基准(5线)，越大线越少，越小线越多
    base = config.BOLL_PRICE_LINES
    lines = round(base * 0.15 / max(ratio, 0.01))
    return max(config.GRID_LINES_MIN, min(config.GRID_LINES_MAX, lines))


def _build_boll_grid(boll_lower: float, boll_upper: float, n: int = None) -> list[float]:
    """根据BOLL上下轨生成几何网格（等比间距）"""
    if n is None:
        n = config.BOLL_PRICE_LINES
    ratio = (boll_upper / boll_lower) ** (1 / (n - 1))
    return [round(boll_lower * ratio ** i, 2) for i in range(n)]


def _find_grid_index(price: float, grid_levels: list[float], current_grid: int | None = None) -> int | None:
    """找到价格应该触发的网格索引。"""
    if price <= grid_levels[0]:
        return 0
    if price >= grid_levels[-1]:
        return len(grid_levels) - 1

    if current_grid is None:
        best_i = 0
        best_dist = abs(price - grid_levels[0])
        for i, level in enumerate(grid_levels):
            dist = abs(price - level)
            if dist < best_dist:
                best_dist = dist
                best_i = i
        return best_i

    # 用动态阈值：格距的30%
    if len(grid_levels) >= 2:
        grid_step = grid_levels[1] - grid_levels[0]
        threshold = grid_step * 0.3
    else:
        threshold = config.GRID_THRESHOLD

    if current_grid < len(grid_levels) - 1:
        next_level = grid_levels[current_grid + 1]
        if price >= next_level - threshold:
            new_grid = current_grid + 1
            while new_grid + 1 < len(grid_levels) and price >= grid_levels[new_grid + 1] - threshold:
                new_grid += 1
            return new_grid

    if current_grid > 0:
        prev_level = grid_levels[current_grid - 1]
        if price <= prev_level + threshold:
            new_grid = current_grid - 1
            while new_grid - 1 >= 0 and price <= grid_levels[new_grid - 1] + threshold:
                new_grid -= 1
            return new_grid

    return current_grid


def backtest_boll(df: pd.DataFrame, capital: float, manual_boxes: list[dict] = None) -> dict:
    """
    BOLL动态箱体网格交易回测。

    建仓时根据当天BOLL上下轨确定箱体范围，固定5条价格线。
    止损/止盈后等待下次机会，用新的BOLL值重建网格。
    """
    # 跳过BOLL未计算完成的前段
    start_idx = 0
    for i in range(len(df)):
        if not math.isnan(df.iloc[i].get("boll_lower", float("nan"))):
            start_idx = i
            break

    trade_data = df.iloc[start_idx:].copy()
    if trade_data.empty:
        return _empty_result(capital)

    cash = capital
    holding = 0
    entry_price = None
    current_grid = None
    trades = []
    is_positioned = False
    shares_per_grid = None
    grid_levels = None
    box_low = None
    box_high = None

    peak_value = capital
    max_drawdown = 0.0
    pending_signal = None
    last_exit_stop_loss = False

    # 解析手动箱体的日期范围
    _manual_boxes_parsed = None
    if manual_boxes:
        _manual_boxes_parsed = []
        for mb in manual_boxes:
            _manual_boxes_parsed.append({
                "start": pd.Timestamp(mb["start"]),
                "end": pd.Timestamp(mb["end"]),
                "box_low": mb["box_low"],
                "box_high": mb["box_high"],
            })

    indices = list(trade_data.index)

    for idx_pos, idx in enumerate(indices):
        row = trade_data.loc[idx]
        date = row["date"]
        close = row["close"]
        low = row["low"]
        high = row["high"]
        open_price = row["open"]

        if math.isnan(close) or math.isnan(open_price):
            continue

        boll_lower = row.get("boll_lower")
        boll_upper = row.get("boll_upper")
        has_boll = (boll_lower is not None and boll_upper is not None and
                    not math.isnan(boll_lower) and not math.isnan(boll_upper))

        # === 执行前一天收盘产生的待执行信号 ===
        if pending_signal is not None and is_positioned:
            sig = pending_signal
            pending_signal = None
            exec_price = open_price

            if sig["action"] == "sell" and holding > 0:
                sell_shares = shares_per_grid * sig["grids"]
                sell_shares = min(sell_shares, holding)
                if sell_shares > 0:
                    amount = sell_shares * exec_price
                    fee = calc_commission(amount, is_sell=True)
                    cash += amount - fee
                    holding -= sell_shares
                    trades.append({
                        "date": date,
                        "action": f"网格卖出({sig['grids']}格)",
                        "price": exec_price,
                        "shares": -sell_shares,
                        "fee": fee,
                        "cash_after": round(cash, 2),
                        "holding_after": holding,
                    })

            elif sig["action"] == "buy":
                buy_shares = shares_per_grid * sig["grids"]
                cost = buy_shares * exec_price
                fee = calc_commission(cost, is_sell=False)
                if cost + fee <= cash:
                    cash -= cost + fee
                    holding += buy_shares
                    trades.append({
                        "date": date,
                        "action": f"网格买入({sig['grids']}格)",
                        "price": exec_price,
                        "shares": buy_shares,
                        "fee": fee,
                        "cash_after": round(cash, 2),
                        "holding_after": holding,
                    })

        # === 还没建仓 ===
        if not is_positioned:
            should_enter = False
            enter_action = "建仓"
            manual_box_low = None
            manual_box_high = None

            if _manual_boxes_parsed:
                # 手动箱体模式：在指定日期范围的第一天建仓
                for mb in _manual_boxes_parsed:
                    if mb.get("_used"):
                        continue
                    if mb["start"] <= date <= mb["end"]:
                        should_enter = True
                        enter_action = f"手动建仓({mb['start'].strftime('%m%d')}~{mb['end'].strftime('%m%d')})"
                        manual_box_low = mb["box_low"]
                        manual_box_high = mb["box_high"]
                        mb["_used"] = True
                        break
            else:
                # 自动模式：BOLL触发
                if not has_boll:
                    continue

                boll_mid = row.get("boll_mid")
                if boll_mid and not math.isnan(boll_mid) and boll_mid > 0:
                    bw = (boll_upper - boll_lower) / boll_mid
                    if bw < config.BOLL_BW_MIN:
                        continue

                # 路径1：价格触碰BOLL下轨
                if low <= boll_lower * (1 + config.BOX_TOUCH_PCT):
                    is_safe, reject_reason = check_entry_safe(df, idx)
                    if is_safe:
                        should_enter = True
                        enter_action = "建仓"

                # 路径2：止损后放量反转信号
                if not should_enter and last_exit_stop_loss:
                    is_reversal, rev_reason = check_volume_reversal(df, idx)
                    if is_reversal:
                        is_safe, reject_reason = check_entry_safe(df, idx)
                        if is_safe:
                            should_enter = True
                            enter_action = f"反转建仓({rev_reason})"

            if not should_enter:
                continue

            # 确定箱体范围
            if manual_box_low is not None:
                box_low = manual_box_low
                box_high = manual_box_high
            else:
                box_low = boll_lower
                box_high = boll_upper

            # ATR动态网格线数
            atr = row.get("atr", float("nan"))
            if not math.isnan(atr):
                price_lines = _calc_grid_lines(atr, box_high - box_low)
            else:
                price_lines = config.BOLL_PRICE_LINES
            grid_levels = _build_boll_grid(box_low, box_high, price_lines)

            if idx_pos + 1 < len(indices):
                next_row = trade_data.loc[indices[idx_pos + 1]]
                buy_price = next_row["open"]
            else:
                buy_price = close

            # 全仓建仓
            lot_unit = price_lines * 100
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
            is_positioned = True
            last_exit_stop_loss = False
            shares_per_grid = total_shares // price_lines

            current_grid = _find_grid_index(buy_price, grid_levels)
            if current_grid is None:
                current_grid = 0

            exec_date = next_row["date"] if idx_pos + 1 < len(indices) else date
            grid_step = grid_levels[1] - grid_levels[0]
            trades.append({
                "date": exec_date,
                "action": enter_action,
                "price": buy_price,
                "shares": total_shares,
                "fee": fee,
                "cash_after": round(cash, 2),
                "holding_after": holding,
                "boll_lower": round(box_low, 2),
                "boll_upper": round(box_high, 2),
                "grid_step": round(grid_step, 2),
                "grid_lines": price_lines,
            })
            continue

        # === 已建仓：检查止损 ===
        if low <= entry_price * (1 - config.STOP_LOSS_PCT):
            sell_price = entry_price * (1 - config.STOP_LOSS_PCT)
            sell_price = max(sell_price, low)
            amount = holding * sell_price
            fee = calc_commission(amount, is_sell=True)
            cash += amount - fee
            trades.append({
                "date": date,
                "action": "止损清仓",
                "price": sell_price,
                "shares": -holding,
                "fee": fee,
                "cash_after": round(cash, 2),
                "holding_after": 0,
            })
            holding = 0
            is_positioned = False
            entry_price = None
            current_grid = None
            grid_levels = None
            pending_signal = None
            last_exit_stop_loss = True
            continue

        # === 已建仓：检查止盈（价格突破BOLL上轨） ===
        if box_high is not None and high >= box_high * (1 + config.TAKE_PROFIT_PCT):
            sell_price = box_high * (1 + config.TAKE_PROFIT_PCT)
            sell_price = min(sell_price, high)
            amount = holding * sell_price
            fee = calc_commission(amount, is_sell=True)
            cash += amount - fee
            trades.append({
                "date": date,
                "action": "止盈清仓",
                "price": sell_price,
                "shares": -holding,
                "fee": fee,
                "cash_after": round(cash, 2),
                "holding_after": 0,
            })
            holding = 0
            is_positioned = False
            entry_price = None
            current_grid = None
            grid_levels = None
            pending_signal = None
            last_exit_stop_loss = False
            continue

        # === 收盘后判断网格信号 ===
        new_grid = _find_grid_index(close, grid_levels, current_grid)

        if new_grid is not None and new_grid != current_grid:
            grids_moved = new_grid - current_grid

            if grids_moved > 0:
                pending_signal = {"action": "sell", "grids": grids_moved}
            elif grids_moved < 0:
                pending_signal = {"action": "buy", "grids": -grids_moved}

            current_grid = new_grid
        else:
            pending_signal = None

        # === 更新最大回撤 ===
        total_value = cash + holding * close
        if total_value > peak_value:
            peak_value = total_value
        dd = (peak_value - total_value) / peak_value
        if dd > max_drawdown:
            max_drawdown = dd

    # === 回测结束 ===
    last_close = df.iloc[-1]["close"]
    final_value = cash + holding * last_close
    profit = final_value - capital
    profit_pct = profit / capital * 100

    return {
        "capital": capital,
        "final_value": round(final_value, 2),
        "profit": round(profit, 2),
        "profit_pct": round(profit_pct, 2),
        "max_drawdown_pct": round(max_drawdown * 100, 2),
        "total_trades": len(trades),
        "trades": trades,
        "remaining_shares": holding,
        "remaining_cash": round(cash, 2),
    }


def _empty_result(capital: float) -> dict:
    return {
        "capital": capital,
        "final_value": capital,
        "profit": 0,
        "profit_pct": 0,
        "max_drawdown_pct": 0,
        "total_trades": 0,
        "trades": [],
        "remaining_shares": 0,
        "remaining_cash": capital,
    }
