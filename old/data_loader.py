"""数据加载与指标计算"""

import pandas as pd
import config


def load_csv(filepath: str) -> pd.DataFrame | None:
    """加载CSV文件，返回按日期正序排列的DataFrame。ST等异常股票返回None。"""
    df = pd.read_csv(filepath, encoding="utf-8")

    col_map = {
        "股票代码": "code",
        "股票名称": "name",
        "交易日": "date",
        "开盘价": "open",
        "最高价": "high",
        "最低价": "low",
        "收盘价": "close",
        "成交量（手）": "volume",
    }
    df = df.rename(columns=col_map)
    df = df[list(col_map.values())].copy()

    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    df = df.sort_values("date").reset_index(drop=True)

    # 过滤异常股票
    if is_abnormal_stock(df):
        return None

    # 过滤创业板(300xxx)和科创板(688xxx)
    code = df.iloc[0]["code"]
    if is_restricted_board(code):
        return None

    return df


# 异常股票名称关键词
_ABNORMAL_KEYWORDS = ("ST", "*ST", "PT", "退市", "退", "S ", "SST", "NST")

# 创业板300xxx、科创板688xxx、北交所(BJ)
_RESTRICTED_PREFIXES  = ("300", "688")
_RESTRICTED_EXCHANGES = ("BJ",)


def is_abnormal_stock(df: pd.DataFrame) -> bool:
    """检查股票是否为ST/PT/退市等异常股票（任意交易日出现过即标记）"""
    names = df["name"].dropna().unique()
    for name in names:
        for kw in _ABNORMAL_KEYWORDS:
            if kw in name:
                return True
    return False


def is_restricted_board(code: str) -> bool:
    """检查是否为创业板、科创板或北交所（代码如 300507.SZ / 688223.SH / 830946.BJ）"""
    parts = code.split(".")
    num      = parts[0]
    exchange = parts[1] if len(parts) > 1 else ""
    if exchange in _RESTRICTED_EXCHANGES:
        return True
    return any(num.startswith(p) for p in _RESTRICTED_PREFIXES)


def calc_atr(df: pd.DataFrame, period: int = config.ATR_PERIOD) -> pd.DataFrame:
    """计算ATR指标"""
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    df = df.copy()
    df["atr"] = tr.rolling(window=period, min_periods=period).mean()
    return df


def calc_ma_and_volume(df: pd.DataFrame) -> pd.DataFrame:
    """计算均线和成交量均线"""
    df = df.copy()
    for p in config.PRICE_MA_PERIODS:
        df[f"ma{p}"] = df["close"].rolling(window=p, min_periods=p).mean()
    df["vol_ma"] = df["volume"].rolling(window=config.VOLUME_MA_PERIOD,
                                        min_periods=config.VOLUME_MA_PERIOD).mean()
    return df


def calc_boll(df: pd.DataFrame) -> pd.DataFrame:
    """计算布林带指标"""
    df = df.copy()
    period = config.BOLL_PERIOD
    df["boll_mid"] = df["close"].rolling(window=period, min_periods=period).mean()
    df["boll_std"] = df["close"].rolling(window=period, min_periods=period).std()
    df["boll_upper"] = df["boll_mid"] + config.BOLL_STD * df["boll_std"]
    df["boll_lower"] = df["boll_mid"] - config.BOLL_STD * df["boll_std"]
    return df


def prepare_data(filepath: str, recent_days: int = config.BACKTEST_DAYS) -> pd.DataFrame | None:
    """加载数据 + 计算ATR/均线/均量/BOLL + 截取最近N个交易日。异常股票返回None。"""
    df = load_csv(filepath)
    if df is None:
        return None
    df = calc_atr(df)
    df = calc_ma_and_volume(df)
    df = calc_boll(df)

    # 多取一些数据用于指标计算的前置数据
    extra = config.BOLL_PERIOD + 60
    if len(df) > recent_days + extra:
        df = df.iloc[-(recent_days + extra):].reset_index(drop=True)

    return df
