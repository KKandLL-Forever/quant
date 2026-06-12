"""
对比不同量比下，A 股首板（非一字板）的次日连板率。
结论指向是否应该改信号定义（缩量 vs 爆量）。
"""
import os, sys
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import duckdb as _duckdb
from db_loader import _ENV

DUCK_PATH = _ENV.get("LOCAL_DUCKDB_PATH", "stock_data_tushare.duckdb")
con = _duckdb.connect(DUCK_PATH, read_only=True)

# 全样本：所有"首板（非一字板）"的次日表现，按 vol_ratio_5 分桶
print("=== 首板（非一字板）次日连板率 vs 5日量比 ===")
print("覆盖：所有 A 股 2018-至今")
print("首板定义：T日 pct_chg≥9.8 且 T-1 pct_chg<9.8（首次涨停）；T日非一字板（high>low）")
print()

df = con.execute("""
    WITH d AS (
        SELECT ts_code, trade_date, open, high, low, close, vol, pct_chg,
               LAG(pct_chg, 1)  OVER w AS prev_pct,
               AVG(vol) OVER (PARTITION BY ts_code ORDER BY trade_date
                              ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING) AS vol_ma5,
               LEAD(pct_chg, 1) OVER w AS next_pct
        FROM daily
        WINDOW w AS (PARTITION BY ts_code ORDER BY trade_date)
    ),
    sig AS (
        SELECT *,
               vol / NULLIF(vol_ma5, 0) AS vol_ratio_5
        FROM d
        WHERE pct_chg >= 9.8
          AND prev_pct IS NOT NULL AND prev_pct < 9.8     -- 首板
          AND high > low                                    -- 非一字板
          AND vol_ma5 > 0
          AND next_pct IS NOT NULL                          -- 有次日数据
          AND ts_code NOT LIKE '688%' AND ts_code NOT LIKE '300%' AND ts_code NOT LIKE '%.BJ'
          AND trade_date >= '2018-01-01'
    )
    SELECT
        CASE
            WHEN vol_ratio_5 < 0.7              THEN '01_严缩量 <0.7x'
            WHEN vol_ratio_5 < 1.0              THEN '02_轻缩量 0.7-1x'
            WHEN vol_ratio_5 < 1.5              THEN '03_平量   1-1.5x'
            WHEN vol_ratio_5 < 2.0              THEN '04_温和放量 1.5-2x'
            WHEN vol_ratio_5 < 3.0              THEN '05_爆量    2-3x'
            WHEN vol_ratio_5 < 5.0              THEN '06_巨量    3-5x'
            ELSE                                     '07_天量   >5x'
        END AS bucket,
        COUNT(*)                                AS n_signals,
        AVG(CASE WHEN next_pct >= 9.8 THEN 1.0 ELSE 0.0 END)        AS pos_rate_2lb,
        AVG(next_pct)                           AS avg_next_pct,
        AVG(CASE WHEN next_pct >= 0 THEN 1.0 ELSE 0.0 END)          AS pos_rate_t1
    FROM sig
    GROUP BY bucket
    ORDER BY bucket
""").df()

df["pos_rate_2lb"] = df["pos_rate_2lb"].apply(lambda x: f"{x:.1%}")
df["avg_next_pct"] = df["avg_next_pct"].apply(lambda x: f"{x:+.2f}%")
df["pos_rate_t1"]  = df["pos_rate_t1"].apply(lambda x: f"{x:.1%}")
print(df.to_string(index=False))

print("\n字段说明:")
print("  n_signals     该桶信号数")
print("  pos_rate_2lb  次日 ≥9.8% 涨停（2连板率）← 你的 label")
print("  avg_next_pct  次日平均涨幅")
print("  pos_rate_t1   次日 ≥0% 收红概率")
con.close()
