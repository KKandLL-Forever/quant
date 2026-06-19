"""
ml_2to4_without1zi.py — 实验：剔除「弱一字板」2板信号后再训 2进4(到4板) 模型

复用 ml_train_2lb_v6 的整套口径（智能止盈 label、walk-forward 评估、分位档标定），唯一区别在信号集：
剔除 **2板当日是一字板、且开盘集合竞价额 >= 8千万** 的样本（有量=能买进=弱一字）；
保留非一字板，以及**竞价额 < 8千万的锁死强一字**（没量=锁死=最强、续板意愿最高）。

一字板判定：2板当日 daily 的 high == low（全天单一价）。
竞价额：stk_auction_o.amount（开盘集合竞价成交额，单位 元）；无竞价数据按 0 计（视为锁死，保留）。

产出（tag=2to4_no1zi）：
  model/xgb_2lb_2to4_no1zi.pkl / oos_predictions_2lb_2to4_no1zi.csv / shap_summary_2lb_2to4_no1zi.png

用法：python first10/ml_2to4_without1zi.py
依赖：与 v6 相同（需 stk_auction_o 已入库）。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

import duckdb as _duckdb
from db_loader import _ENV

import ml_train_2lb_v6 as v6

_DUCK_PATH = _ENV.get("LOCAL_DUCKDB_PATH", "stock_data_tushare.duckdb")
AUC_AMT_MIN = 80_000_000  # 8 千万（元）

_SIGNAL_SQL = """
SELECT
    l.ts_code,
    strftime(l.trade_date, '%Y%m%d') AS trade_date,
    (d.high = d.low) AS is_1zi,
    COALESCE(ao.amount, 0) AS auc_amt
FROM limit_list_d l
LEFT JOIN stock_st st
                 ON st.ts_code = l.ts_code AND st.trade_date = l.trade_date
JOIN daily d ON d.ts_code = l.ts_code AND d.trade_date = l.trade_date
LEFT JOIN stk_auction_o ao
                 ON ao.ts_code = l.ts_code AND ao.trade_date = l.trade_date
WHERE l.limit_type   = 'U'
  AND l.limit_times  = 2
  AND l.ts_code NOT LIKE '688%'
  AND l.ts_code NOT LIKE '30%'
  AND l.ts_code NOT LIKE '%.BJ'
  AND st.ts_code IS NULL
ORDER BY l.trade_date, l.ts_code
"""


def _get_signals_filtered():
    """扫 2 板信号，剔除「一字板 且 竞价额 < 8千万」的弱一字；打印各类计数。"""
    con = _duckdb.connect(_DUCK_PATH, read_only=True)
    try:
        df = con.execute(_SIGNAL_SQL).df()
    finally:
        con.close()
    is_1zi = df["is_1zi"].astype(bool)
    weak = is_1zi & (df["auc_amt"] >= AUC_AMT_MIN)
    kept = df[~weak]
    print("=== 剔除有量一字（一字 且 竞价额>=8千万=能买进=弱）===")
    print(f"  全量 2板信号 {len(df)}  |  一字板 {int(is_1zi.sum())}  "
          f"（锁死强一字<8千万 {int((is_1zi & ~weak).sum())} 保留 / 有量弱一字>=8千万 {int(weak.sum())} 剔除）")
    print(f"  → 训练用 {len(kept)} 条")
    return kept[["ts_code", "trade_date"]].reset_index(drop=True)


if __name__ == "__main__":
    v6.train(signal_df=_get_signals_filtered(), tag="2to4_no1zi")
