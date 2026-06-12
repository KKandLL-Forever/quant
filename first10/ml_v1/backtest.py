"""
backtest.py — 首板信号 → 次日开盘买、次日收盘卖 的简单回测

策略对比：
  A. baseline_all      — 当日所有信号等权
  B. model_topk        — 当日按模型 proba 排序取 Top-K
  C. fd_filter         — fd_amount_ratio ≥ 阈值的信号等权
  D. fd_then_topk      — 先用 fd_amount_ratio 过滤，再 Top-K

测试集：trade_date >= 20240101
"""
import os, sys, pickle
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import duckdb as _duckdb
from db_loader import _ENV

HERE = os.path.dirname(os.path.abspath(__file__))
FEAT_CSV   = os.path.join(HERE, "model", "feature_matrix.csv")
MODEL_PATH = os.path.join(HERE, "model", "xgb_lianban.pkl")
DUCK_PATH  = _ENV.get("LOCAL_DUCKDB_PATH", "stock_data_tushare.duckdb")

TEST_START = "20240101"
FEE_ROUND_TRIP = 0.003     # 0.3% 双边
FD_THRESHOLD   = 0.0092    # fd_amount/float_mv 分位数 60% 阈值（quintile 3 起跳点）
TOP_K_PER_DAY  = 2

# ── 1) 拿模型 + 特征矩阵 ─────────────────────────────────────────
with open(MODEL_PATH, "rb") as f:
    obj = pickle.load(f)
model     = obj["model"]
feat_cols = obj["feature_cols"]

feat = pd.read_csv(FEAT_CSV, dtype={"trade_date": str})
test = feat[feat["trade_date"] >= TEST_START].copy()
test = test.dropna(subset=["label"])
test = test[test["label"].isin([0.0, 1.0])].reset_index(drop=True)

test["proba"] = model.predict_proba(test[feat_cols])[:, 1]
print(f"测试集信号: {len(test)}，覆盖 {test['trade_date'].nunique()} 个交易日")

# ── 2) DuckDB 一次性把每个信号的 T+1, T+2 open/close 拿出来 ─────
con = _duckdb.connect(DUCK_PATH, read_only=True)
con.register("sig", test[["ts_code", "trade_date"]])
con.execute("""
    CREATE OR REPLACE TEMP VIEW sig_typed AS
    SELECT ts_code,
           CAST(strptime(trade_date, '%Y%m%d') AS DATE) AS sig_date,
           trade_date AS trade_date_str
    FROM sig
""")
nxt = con.execute("""
    WITH d AS (
        SELECT ts_code, trade_date, open, close,
               LEAD(open,  1) OVER w AS next_open,
               LEAD(close, 1) OVER w AS next_close,
               LEAD(close, 2) OVER w AS next2_close
        FROM daily
        WINDOW w AS (PARTITION BY ts_code ORDER BY trade_date)
    )
    SELECT s.ts_code, s.trade_date_str AS trade_date,
           d.close       AS sig_close,
           d.next_open   AS next_open,
           d.next_close  AS next_close,
           d.next2_close AS next2_close
    FROM sig_typed s
    JOIN d ON d.ts_code = s.ts_code AND d.trade_date = s.sig_date
""").df()
con.close()

test = test.merge(nxt, on=["ts_code", "trade_date"], how="left")

# ── 3) 计算每条信号 T+1 open→close 收益 ─────────────────────────
test["limit_open"] = test["next_open"] >= test["sig_close"] * 1.097
test["tradeable"]  = (~test["limit_open"]) & test["next_open"].notna() & test["next_close"].notna()

# 三个不同退出规则下的净收益（已扣 0.3% 双边）
test["ret_open2close"]  = (test["next_close"]  / test["next_open"]) - 1 - FEE_ROUND_TRIP   # 当前
test["ret_open2next2"]  = (test["next2_close"] / test["next_open"]) - 1 - FEE_ROUND_TRIP   # 持仓 1.5 天
test["ret_close2next"]  = (test["next_close"]  / test["sig_close"]) - 1 - FEE_ROUND_TRIP   # 假想 T 收盘买入（理想化基准）
test["gap_pct"]         = (test["next_open"]   / test["sig_close"]) - 1                    # T+1 高开幅度

for c in ["ret_open2close", "ret_open2next2", "ret_close2next"]:
    test.loc[~test["tradeable"], c] = np.nan

print(f"  一字板不可买: {test['limit_open'].sum()} ({test['limit_open'].mean():.1%})")
print(f"  可交易信号:   {test['tradeable'].sum()}")

# Gap 分析：label=1 vs label=0 的高开幅度
g1 = test[test["tradeable"] & (test["label"] == 1)]["gap_pct"]
g0 = test[test["tradeable"] & (test["label"] == 0)]["gap_pct"]
print(f"\n=== T+1 高开幅度（gap_pct = next_open/sig_close - 1）===")
print(f"  label=1 (T+1 涨停): mean={g1.mean():+.2%}  median={g1.median():+.2%}")
print(f"  label=0 (T+1 不涨): mean={g0.mean():+.2%}  median={g0.median():+.2%}")
print(f"  → label=1 票普遍高开 {g1.mean()-g0.mean():+.2%} 多")

# ── 4) 策略选股函数 ─────────────────────────────────────────────
def strategy_baseline_all(day_df):
    return day_df

def strategy_model_topk(day_df, k=TOP_K_PER_DAY):
    return day_df.nlargest(k, "proba")

def strategy_fd_filter(day_df, thr=FD_THRESHOLD):
    return day_df[day_df["fd_amount_ratio"] >= thr]

def strategy_fd_then_topk(day_df, thr=FD_THRESHOLD, k=TOP_K_PER_DAY):
    pool = day_df[day_df["fd_amount_ratio"] >= thr]
    return pool.nlargest(k, "proba")

STRATEGIES = {
    "A_baseline_all":  strategy_baseline_all,
    "B_model_top2":    strategy_model_topk,
    "C_fd_filter":     strategy_fd_filter,
    "D_fd_then_top2":  strategy_fd_then_topk,
}

# ── 5) 按日撮合，等权持仓 ────────────────────────────────────────
def run_strategy(name, fn, ret_col="ret_open2close"):
    daily_rets = []
    daily_n    = []
    for date, group in test.groupby("trade_date"):
        sel = fn(group)
        sel = sel[sel["tradeable"]]
        if sel.empty:
            continue
        daily_rets.append(sel[ret_col].mean())
        daily_n.append(len(sel))
    if not daily_rets:
        return None

    rets = np.array(daily_rets)
    n_days = len(rets)
    avg_n  = float(np.mean(daily_n))

    cum = np.cumprod(1 + rets)
    total_ret = cum[-1] - 1
    days_per_year = 250
    n_years = n_days / days_per_year
    cagr = (1 + total_ret) ** (1 / n_years) - 1 if n_years > 0 else float("nan")

    sharpe = (rets.mean() / rets.std()) * np.sqrt(days_per_year) if rets.std() > 0 else float("nan")
    win_rate = (rets > 0).mean()

    # 最大回撤
    peak = np.maximum.accumulate(cum)
    dd = (cum - peak) / peak
    max_dd = dd.min()

    print(f"\n[{name}]")
    print(f"  交易天数={n_days}  平均每日持仓={avg_n:.2f}")
    print(f"  胜率(日)  = {win_rate:.2%}")
    print(f"  日均收益  = {rets.mean():+.4%}")
    print(f"  日波动    = {rets.std():.4%}")
    print(f"  累计收益  = {total_ret:+.2%}")
    print(f"  年化      = {cagr:+.2%}")
    print(f"  夏普      = {sharpe:.2f}")
    print(f"  最大回撤  = {max_dd:.2%}")
    return rets

for ret_col, label in [
    ("ret_open2close", "T+1 open → T+1 close（实战可执行）"),
    ("ret_open2next2", "T+1 open → T+2 close（持仓 1.5 天）"),
    ("ret_close2next", "T close → T+1 close（理想基准，实战买不到）"),
]:
    print("\n" + "=" * 60)
    print(f"退出规则: {label}")
    print("=" * 60)
    for name, fn in STRATEGIES.items():
        run_strategy(name, fn, ret_col=ret_col)
