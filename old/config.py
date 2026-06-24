"""全局配置"""

# === 资金 ===
TOTAL_CAPITAL = 48000  # 总资金（元）

# === 选股 ===
PRICE_MIN = 5.0   # 最低股价
PRICE_MAX = 10.0  # 最高股价

# === 网格 ===
GRID_STEP = 0.5       # 网格间距（元）
GRID_THRESHOLD = 0.10 # 网格触发阈值：收盘价距网格线±0.1元内才算触发

# === BOLL动态箱体 ===
BOLL_PERIOD = 20       # 布林带周期（天）
BOLL_STD = 2.0         # 布林带标准差倍数
BOLL_PRICE_LINES = 5   # 基准网格线数（ATR中等时）
GRID_LINES_MIN = 4     # 最少网格线数（ATR大时）
GRID_LINES_MAX = 8     # 最多网格线数（ATR小时）
BOLL_RANGE_DAYS = 20   # 建网格时取前N天BOLL的最高上轨/最低下轨
BOX_TOUCH_PCT = 0.03   # 触碰判定：距离上/下沿 ±3% 以内
BOLL_BW_MIN = 0.10     # BOLL带宽率下限：低于10%不建仓（缩量横盘）

# === 止损后反转建仓 ===
REVERSAL_LOOKBACK = 5      # 观察最近N天
REVERSAL_UP_DAYS = 4       # 至少M天放量阳线


# === 建仓保护（防止下跌途中建仓） ===
VOLUME_MA_PERIOD = 20       # 成交量均线周期
VOLUME_SURGE_RATIO = 2.0    # 放量倍数阈值（当日量 ≥ 均量 × 2）
PRICE_MA_PERIODS = [5, 10, 20]  # 均线周期

# === ATR ===
ATR_PERIOD = 14

# === 止损止盈 ===
STOP_LOSS_PCT = 0.10   # 跌破建仓价10%止损
TAKE_PROFIT_PCT = 0.20 # 涨破箱顶20%止盈

# === 手续费 ===
COMMISSION_RATE = 0.00025  # 佣金万2.5（买卖双向）
COMMISSION_MIN = 5.0       # 最低佣金5元
STAMP_TAX_RATE = 0.001     # 印花税千1（仅卖出）

# === 回测 ===
BACKTEST_DAYS = 500  # 最近2年（约500个交易日）

# === 数据路径 ===
DATA_DIR = r"D:\Programs\KLineTrainer\data\a_market_offline"
