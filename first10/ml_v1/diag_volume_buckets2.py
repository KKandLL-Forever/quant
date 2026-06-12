import sys; sys.stdout.reconfigure(encoding="utf-8")
import duckdb
con = duckdb.connect("stock_data_tushare.duckdb", read_only=True)
df = con.execute("""
WITH d AS (
    SELECT ts_code, trade_date, high, low, pct_chg, vol,
           LAG(pct_chg) OVER w AS prev_pct,
           AVG(vol) OVER (PARTITION BY ts_code ORDER BY trade_date
                          ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING) AS vol_ma5,
           LEAD(pct_chg) OVER w AS next_pct
    FROM daily
    WINDOW w AS (PARTITION BY ts_code ORDER BY trade_date)
),
sig AS (
    SELECT *, vol/NULLIF(vol_ma5,0) AS vr
    FROM d
    WHERE pct_chg >= 9.8 AND prev_pct < 9.8 AND high > low AND vol_ma5 > 0
      AND next_pct IS NOT NULL
      AND ts_code NOT LIKE '688%' AND ts_code NOT LIKE '300%' AND ts_code NOT LIKE '%.BJ'
      AND trade_date >= '2018-01-01'
)
SELECT
    CASE
      WHEN vr < 0.7 THEN 'A_严缩量 <0.7'
      WHEN vr < 1.4 THEN 'B_非爆量 0.7-1.4'
      WHEN vr < 2.0 THEN 'C_温放 1.4-2'
      ELSE             'D_爆量 >=2'
    END AS bucket,
    COUNT(*) AS n,
    AVG(CASE WHEN next_pct >= 9.8 THEN 1.0 ELSE 0.0 END) AS pos_rate_2lb,
    AVG(next_pct) AS avg_next_pct
FROM sig GROUP BY bucket ORDER BY bucket
""").df()
df["pos_rate_2lb"] = df["pos_rate_2lb"].apply(lambda x: f"{x:.1%}")
df["avg_next_pct"] = df["avg_next_pct"].apply(lambda x: f"{x:+.2f}%")
print(df.to_string(index=False))
