"""W型箱体震荡识别 + 支撑阻力位疑似箱体"""

import math
import pandas as pd
import config


def find_boxes(df: pd.DataFrame) -> list[dict]:
    """
    在日线数据中识别所有箱体震荡区间。

    两种模式并行：
    1. 确认箱体：多窗口(60/90/120天)，上下沿各触碰≥2次
    2. 疑似箱体：30天窗口，支撑阻力位各触碰≥1次

    返回列表，每个元素包含 confirmed 字段标记是否已确认。
    """
    all_boxes = []

    # === 模式1：确认箱体（原逻辑） ===
    for window in config.BOX_WINDOWS:
        if len(df) < window:
            continue
        boxes = _scan_with_window(df, window, config.BOX_TOUCH_COUNT, confirmed=True)
        all_boxes.extend(boxes)

    # === 模式2：疑似箱体（支撑阻力位） ===
    sr_window = config.SR_WINDOW
    if len(df) >= sr_window:
        sr_boxes = _scan_with_window(df, sr_window, config.SR_TOUCH_COUNT, confirmed=False)
        all_boxes.extend(sr_boxes)

    # 按起始时间排序，合并去重
    all_boxes.sort(key=lambda b: (b["start_idx"], -b["end_idx"]))
    all_boxes = _merge_overlapping(all_boxes)
    return all_boxes


def _scan_with_window(df: pd.DataFrame, window: int, touch_count: int,
                      confirmed: bool) -> list[dict]:
    """用指定窗口大小扫描箱体"""
    boxes = []
    i = 0

    while i <= len(df) - window:
        segment = df.iloc[i : i + window]

        box = _check_box(segment, i, window, touch_count)
        if box is None:
            i += 1
            continue

        box["confirmed"] = confirmed

        # 尝试向后延伸箱体
        end_idx = i + window
        while end_idx < len(df):
            row = df.iloc[end_idx]
            price_in_range = (box["box_low"] * (1 - config.BOX_TOUCH_PCT) <= row["low"] and
                              row["high"] <= box["box_high"] * (1 + config.BOX_TOUCH_PCT))
            if price_in_range:
                end_idx += 1
            else:
                break

        box["end_idx"] = end_idx - 1
        box["end_date"] = df.iloc[end_idx - 1]["date"]

        # 延伸区间内统计触碰次数，用于疑似→确认判断
        extended_segment = df.iloc[i:end_idx]
        if extended_segment["atr"].notna().any():
            box["avg_atr"] = extended_segment["atr"].mean()

        touch_margin_low = box["box_low"] * config.BOX_TOUCH_PCT
        touch_margin_high = box["box_high"] * config.BOX_TOUCH_PCT
        box["low_touches"] = int((extended_segment["low"] <= box["box_low"] + touch_margin_low).sum())
        box["high_touches"] = int((extended_segment["high"] >= box["box_high"] - touch_margin_high).sum())

        boxes.append(box)

        # 跳过当前箱体区间
        i = end_idx

    return boxes


def _check_box(segment: pd.DataFrame, start_idx: int, window: int,
               touch_count: int) -> dict | None:
    """检查一段数据是否构成箱体"""
    seg_low = segment["low"].min()
    seg_high = segment["high"].max()
    box_range = seg_high - seg_low

    # 振幅检查
    if box_range < config.BOX_MIN_RANGE or box_range > config.BOX_MAX_RANGE:
        return None

    # 价格区间检查
    if seg_low < config.PRICE_MIN or seg_high > config.PRICE_MAX:
        return None

    # 触碰检查
    touch_margin_low = seg_low * config.BOX_TOUCH_PCT
    touch_margin_high = seg_high * config.BOX_TOUCH_PCT

    low_touches = (segment["low"] <= seg_low + touch_margin_low).sum()
    high_touches = (segment["high"] >= seg_high - touch_margin_high).sum()

    if low_touches < touch_count or high_touches < touch_count:
        return None

    # 将上下沿对齐到网格
    box_low = _round_down(seg_low, config.GRID_STEP)
    box_high = _round_up(seg_high, config.GRID_STEP)

    # 构建网格价格线
    grid_levels = []
    level = box_low
    while level <= box_high + 1e-9:
        grid_levels.append(round(level, 2))
        level += config.GRID_STEP

    price_lines = len(grid_levels)
    if price_lines < 3:
        return None

    avg_atr = segment["atr"].mean() if segment["atr"].notna().any() else 0

    return {
        "start_idx": start_idx,
        "end_idx": start_idx + len(segment) - 1,
        "start_date": segment.iloc[0]["date"],
        "end_date": segment.iloc[-1]["date"],
        "box_low": box_low,
        "box_high": box_high,
        "avg_atr": avg_atr,
        "price_lines": price_lines,
        "grid_levels": grid_levels,
        "window": window,
        "low_touches": int(low_touches),
        "high_touches": int(high_touches),
    }


def _round_down(value: float, step: float) -> float:
    """向下取整到step的倍数"""
    return round(int(value / step) * step, 2)


def _round_up(value: float, step: float) -> float:
    """向上取整到step的倍数"""
    return round(math.ceil(value / step) * step, 2)


def _merge_overlapping(boxes: list[dict]) -> list[dict]:
    """
    合并/去重时间重叠的箱体。

    规则：
    1. 时间重叠且价格区间接近 → 合并为一个，优先保留确认箱体
    2. 时间重叠且一个箱体的价格区间被另一个包含 → 丢弃被包含的（小的）
    3. 确认箱体优先级高于疑似箱体，时间重叠时优先保留确认箱体
    """
    if len(boxes) <= 1:
        return boxes

    merged = [boxes[0]]
    for box in boxes[1:]:
        prev = merged[-1]

        # 没有时间重叠，直接加入
        if box["start_idx"] > prev["end_idx"]:
            merged.append(box)
            continue

        # === 时间重叠，决定如何处理 ===
        price_close = (abs(box["box_low"] - prev["box_low"]) < config.GRID_STEP and
                       abs(box["box_high"] - prev["box_high"]) < config.GRID_STEP)

        # box的价格区间被prev包含
        box_inside_prev = (box["box_low"] >= prev["box_low"] - config.GRID_STEP and
                           box["box_high"] <= prev["box_high"] + config.GRID_STEP)

        # prev的价格区间被box包含
        prev_inside_box = (prev["box_low"] >= box["box_low"] - config.GRID_STEP and
                           prev["box_high"] <= box["box_high"] + config.GRID_STEP)

        if price_close or box_inside_prev or prev_inside_box:
            # 决定保留哪个：确认优先，其次保留更长/更宽的
            if prev["confirmed"] and not box["confirmed"]:
                # prev是确认箱体，丢弃疑似box，但延伸时间
                prev["end_idx"] = max(prev["end_idx"], box["end_idx"])
                prev["end_date"] = max(prev["end_date"], box["end_date"])
            elif box["confirmed"] and not prev["confirmed"]:
                # box是确认箱体，替换疑似prev
                box["end_idx"] = max(prev["end_idx"], box["end_idx"])
                box["end_date"] = max(prev["end_date"], box["end_date"])
                merged[-1] = box
            else:
                # 同级别，合并为更大的
                prev["end_idx"] = max(prev["end_idx"], box["end_idx"])
                prev["end_date"] = max(prev["end_date"], box["end_date"])
                prev["box_low"] = min(prev["box_low"], box["box_low"])
                prev["box_high"] = max(prev["box_high"], box["box_high"])
                prev["avg_atr"] = (prev["avg_atr"] + box["avg_atr"]) / 2
                if box["confirmed"]:
                    prev["confirmed"] = True
                prev["low_touches"] = max(prev.get("low_touches", 0), box.get("low_touches", 0))
                prev["high_touches"] = max(prev.get("high_touches", 0), box.get("high_touches", 0))
                # 重建网格线
                prev["grid_levels"] = _build_grid(prev["box_low"], prev["box_high"])
                prev["price_lines"] = len(prev["grid_levels"])
        else:
            merged.append(box)

    return merged


def _build_grid(box_low: float, box_high: float) -> list[float]:
    """根据上下沿构建网格线列表"""
    grid_levels = []
    level = box_low
    while level <= box_high + 1e-9:
        grid_levels.append(round(level, 2))
        level += config.GRID_STEP
    return grid_levels
