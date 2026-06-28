"""cache_tushare 数据正确性测试（纯本地，不连网/不连库）。

用法：
    cd quart
    python test_cache_tushare.py          # 或 python -m unittest test_cache_tushare

守护数据正确性的几条底线（每条对应一类曾发生或潜伏的 bug）：
  1. 翻页：全市场接口经 _fetch_paged 必须 offset 循环取尽，单次 6000 行上限不得静默截断
     （top10 曾每季只到 ~600 只；daily 等按日全市场表已逼近上限）。按日 fetcher 也走同一路径。
  2. 幂等：同主键重复写入 DuckDB（INSERT OR REPLACE）不得使行数翻倍——这是“统计不对”的直接来源。
  3. 完整性：_top10_missing_periods 按已落库个股数判断，被截断的旧季度要识别为需重抓。
  4. 计数：_run_concurrent 总行数 = 实际拉取行数，不依赖云端写入返回值（--no-cloud 也要真实计数）。
  5. 列对齐：_df_to_records 按列名而非位置映射，字段顺序变化时不串位（amount 不会写进 vol）。
  6. schema：_prepare_duck_df 列顺序/类型正确，end_date 保持字符串、hold_amount 为数值。

造假对象（PagedSource/FakeLimiter/FakeCk/FakeDuck/FakeDatetime）模拟 tushare/限速器/ClickHouse/
DuckDBWriter/系统时钟的最小接口。
"""
import types
import os
import sys
import unittest
from datetime import date, datetime

import duckdb
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cache_tushare as C


class FakeLimiter:
    """限速器桩：永远放行。"""

    def acquire(self):
        return True


class PagedSource:
    """单接口桩：按 offset/limit 返回切片，记录每次调用的 (offset, limit)。"""

    def __init__(self, total):
        self.total = total
        self.calls = []

    def __call__(self, offset, limit, **kwargs):
        self.calls.append((offset, limit))
        n = max(0, min(limit, self.total - offset))
        return pd.DataFrame({"ts_code": [f"{offset + i:06d}.SZ" for i in range(n)]})


class PagedSourceFailAfterFirst:
    """单接口桩：首页满页，之后返回 None（模拟拉取失败/中断）。"""

    def __init__(self, page):
        self.page = page
        self.calls = []

    def __call__(self, offset, limit, **kwargs):
        self.calls.append(offset)
        if offset == 0:
            return pd.DataFrame({"ts_code": [str(i) for i in range(self.page)]})
        return None


class FakeCk:
    """ClickHouse 桩：execute 返回预置行。"""

    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql, *args, **kwargs):
        return self.rows


class FakeDuck:
    """DuckDBWriter 桩：累计写入行数。"""

    def __init__(self):
        self.rows = 0

    def put(self, table, df):
        self.rows += len(df)


class FakeDatetime:
    """固定 today() 的 datetime 替身，其余构造委托真实 datetime。"""

    fixed = datetime(2026, 4, 10)

    @classmethod
    def today(cls):
        return cls.fixed


class TestQuarterEndPeriods(unittest.TestCase):
    """_quarter_end_periods 季度末枚举。"""

    def test_full_year(self):
        """整年返回四个季度末。"""
        self.assertEqual(
            C._quarter_end_periods("20200101", "20201231"),
            ["20200331", "20200630", "20200930", "20201231"],
        )

    def test_partial_range_excludes_out_of_bounds(self):
        """只保留落在 [start,end] 的季度末。"""
        self.assertEqual(C._quarter_end_periods("20200401", "20200701"), ["20200630"])


class TestFetchPaged(unittest.TestCase):
    """_fetch_paged 翻页正确性（bug #1 核心）。"""

    def setUp(self):
        self._orig = C._API_PAGE
        C._API_PAGE = 10

    def tearDown(self):
        C._API_PAGE = self._orig

    def test_collects_all_pages_non_multiple(self):
        """总数非整页倍数：拼齐全部行，末页不足即停。"""
        src = PagedSource(total=25)
        df = C._fetch_paged(src, FakeLimiter(), "x", trade_date="d")
        self.assertEqual(len(df), 25)
        self.assertEqual(src.calls, [(0, 10), (10, 10), (20, 10)])

    def test_collects_all_pages_exact_multiple(self):
        """总数恰为整页倍数：需多取一页（返回空）确认取尽，不丢数据。"""
        src = PagedSource(total=20)
        df = C._fetch_paged(src, FakeLimiter(), "x", trade_date="d")
        self.assertEqual(len(df), 20)
        self.assertEqual(src.calls[-1], (20, 10))

    def test_sends_offset_and_limit(self):
        """必须把 offset/limit 透传给接口（否则等于不翻页）。"""
        src = PagedSource(total=5)
        C._fetch_paged(src, FakeLimiter(), "x", trade_date="d")
        self.assertEqual(src.calls, [(0, 10)])

    def test_partial_when_page_fails(self):
        """中途返回 None 时返回已取到的部分，不崩溃（由完整性检查后续补抓）。"""
        src = PagedSourceFailAfterFirst(page=10)
        df = C._fetch_paged(src, FakeLimiter(), "x")
        self.assertEqual(len(df), 10)


class TestByDateFetcherPaginates(unittest.TestCase):
    """按日全市场 fetcher 也必须翻页（bug #1 在 daily/basic/moneyflow 等的延伸）。"""

    def setUp(self):
        self._orig = C._API_PAGE
        C._API_PAGE = 10

    def tearDown(self):
        C._API_PAGE = self._orig

    def test_fetch_daily_paginates(self):
        """fetch_daily_df 单日超过单次上限时翻页取尽。"""
        pro = types.SimpleNamespace(daily=PagedSource(total=23))
        df = C.fetch_daily_df(pro, FakeLimiter(), "20240701")
        self.assertEqual(len(df), 23)
        self.assertEqual(pro.daily.calls, [(0, 10), (10, 10), (20, 10)])


class TestDuckIdempotency(unittest.TestCase):
    """重复写入同主键不得使行数翻倍（bug #2）。"""

    def test_insert_or_replace_dedup(self):
        """同 (ts_code,end_date,holder_name) 写两次：仍 1 行，且取最新值。"""
        con = duckdb.connect(":memory:")
        con.execute(C._DUCK_DDL["top10_floatholders"])

        def ins(amount):
            raw = pd.DataFrame({
                "ts_code": ["1.SZ"], "end_date": ["20240630"], "holder_name": ["A"],
                "hold_amount": [amount], "hold_ratio": [1.0], "hold_float_ratio": [2.0],
                "holder_type": ["自然人"], "ann_date": ["20240801"],
            })
            w = C._prepare_duck_df("top10_floatholders", raw)
            con.register("_w", w)
            con.execute("INSERT OR REPLACE INTO top10_floatholders SELECT * FROM _w")
            con.unregister("_w")

        ins(100.0)
        ins(999.0)
        cnt, latest = con.execute(
            "SELECT COUNT(*), MAX(hold_amount) FROM top10_floatholders"
        ).fetchone()
        con.close()
        self.assertEqual(cnt, 1)
        self.assertEqual(latest, 999.0)


class TestTop10MissingPeriods(unittest.TestCase):
    """_top10_missing_periods 完整性判断（bug #3）。"""

    def setUp(self):
        self._orig_dt = C.datetime
        C.datetime = FakeDatetime

    def tearDown(self):
        C.datetime = self._orig_dt

    def _missing(self, have_rows):
        return C._top10_missing_periods(FakeCk(have_rows))

    def test_truncated_period_flagged(self):
        """已落库但个股数不足阈值的旧季度，必须判为需重抓。"""
        self.assertIn("20200630", self._missing([("20200630", 600)]))

    def test_complete_period_skipped(self):
        """个股数达阈值的季度跳过。"""
        self.assertNotIn("20200331", self._missing([("20200331", 5000)]))

    def test_absent_period_flagged(self):
        """完全没落库的旧季度需抓。"""
        self.assertIn("20191231", self._missing([("20200331", 5000)]))

    def test_recent_period_skipped_even_if_truncated(self):
        """距今不足 35 天的季度即使不全也先跳过（季报尚未普遍披露）。"""
        self.assertNotIn("20260331", self._missing([("20260331", 600)]))


class TestRunConcurrentRowCount(unittest.TestCase):
    """_run_concurrent 行数统计（bug #4）。"""

    def setUp(self):
        self._orig_cloud = C.CLOUD_ENABLED
        C.CLOUD_ENABLED = False

    def tearDown(self):
        C.CLOUD_ENABLED = self._orig_cloud

    def test_counts_rows_without_cloud(self):
        """--no-cloud 下 _bulk_write 返回 0，但总数须等于实际拉取行数。"""
        def fetcher(pro, limiter, item):
            return pd.DataFrame({"ts_code": [item, item]})

        duck = FakeDuck()
        total = C._run_concurrent(
            object(), FakeCk([]), "daily", ["a", "b", "c"], fetcher, "daily",
            limiter=FakeLimiter(), duck_writer=duck,
        )
        self.assertEqual(total, 6)
        self.assertEqual(duck.rows, 6)

    def test_bulk_write_returns_zero_without_cloud(self):
        """记录 _bulk_write 在 --no-cloud 时返回 0（计数不能依赖它）。"""
        df = pd.DataFrame({"ts_code": ["x"], "trade_date": ["20240101"]})
        self.assertEqual(C._bulk_write(FakeCk([]), "daily", df), 0)


class TestDfToRecordsColumnAlignment(unittest.TestCase):
    """_df_to_records 按列名映射，字段顺序变化不串位（bug #5）。"""

    def test_scrambled_columns_map_by_name(self):
        """打乱列顺序 + 多余列：值仍落在正确字段，trade_date 转 date，多余列丢弃。"""
        df = pd.DataFrame({
            "pct_chg": [1.0], "amount": [200.0], "vol": [300.0], "close": [10.0],
            "low": [9.0], "high": [11.0], "open": [9.5],
            "trade_date": ["20240101"], "ts_code": ["x.SZ"], "extra": [999],
        })
        rec = C._df_to_records("daily", df)[0]
        self.assertEqual(list(rec.keys()), C.COLUMNS["daily"])
        self.assertEqual(rec["vol"], 300.0)
        self.assertEqual(rec["amount"], 200.0)
        self.assertEqual(rec["trade_date"], date(2024, 1, 1))


class TestPrepareDuckDf(unittest.TestCase):
    """_prepare_duck_df 列顺序与类型（schema 守护，bug #6）。"""

    def test_top10_schema_and_types(self):
        """列顺序对齐 DDL；end_date 保持字符串；hold_amount 转数值；缺列补齐。"""
        raw = pd.DataFrame({
            "ts_code": ["000001.SZ"], "end_date": ["20240630"], "holder_name": ["张三"],
            "hold_amount": ["12345"], "hold_ratio": [1.5], "hold_float_ratio": [2.5],
            "holder_type": ["自然人"],
        })
        out = C._prepare_duck_df("top10_floatholders", raw)
        self.assertEqual(list(out.columns), C.COLUMNS["top10_floatholders"])
        self.assertEqual(out["end_date"].iloc[0], "20240630")
        self.assertEqual(float(out["hold_amount"].iloc[0]), 12345.0)
        self.assertEqual(out["ann_date"].iloc[0], "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
