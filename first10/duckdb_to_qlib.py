"""
duckdb_to_qlib.py — 把本地 DuckDB 的日线数据转成 qlib bin 格式

qlib 不直接读 DuckDB，它用自有二进制列存(bin)。本脚本读 DuckDB 的 daily+adj_factor，
算成后复权(hfq)OHLCV，写出 qlib 标准目录(calendars / instruments / features)。

只依赖 numpy/pandas/duckdb，不 import qlib，故可跑在与 cache_tushare 相同的主 .venv(3.14)，
方便日更时前后串联：cache_tushare --update  →  duckdb_to_qlib --update。

为什么用后复权而非前复权：后复权的历史值不会因未来分红而变化(hfq[t]=close[t]*adj_factor[t]，
过去的 adj_factor 不随新除权改变)，故 --update 只需追加新日期/重建受影响个股，绝不重写无关历史。
建模用的都是比值特征，后/前复权等价。

字段：open/high/low/close/volume/vwap/factor，对齐 qlib Alpha158 所需。
代码格式：tushare 600000.SH → qlib SH600000(features 目录名小写 sh600000)。

用法(与 cache_tushare 风格一致)：
  python first10/duckdb_to_qlib.py --full              # 全量重建(安全，首次或大改后用)
  python first10/duckdb_to_qlib.py --update            # 增量(追加新交易日+重建受影响个股)
  python first10/duckdb_to_qlib.py --full --out ~/.qlib/qlib_data/duck_cn
之后在 qlib 里 qlib.init(provider_uri=该目录)，handler 的 instruments 用 "all"。
依赖：本地 DuckDB(daily, adj_factor)；numpy/pandas/duckdb。
"""

import argparse
import os
import struct
import sys

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cache_tushare import DUCKDB_PATH

DEFAULT_OUT = os.path.expanduser("~/.qlib/qlib_data/duck_cn")
FIELDS = ["open", "high", "low", "close", "volume", "vwap", "factor"]


def _qlib_symbol(ts_code: str) -> str:
    """600000.SH → SH600000。"""
    code, mkt = ts_code.split(".")
    return f"{mkt}{code}"


def _load(con, codes=None):
    """读 daily+adj_factor，返回算好 hfq OHLCV/vwap/factor 的 DataFrame(已按代码、日期排序)。"""
    where = ""
    params = []
    if codes:
        where = "WHERE d.ts_code IN ({})".format(",".join(["?"] * len(codes)))
        params = list(codes)
    df = con.execute(
        f"""
        SELECT d.ts_code, d.trade_date,
               d.open, d.high, d.low, d.close, d.vol, d.amount,
               COALESCE(a.adj_factor, 1.0) AS adj_factor
        FROM daily d
        LEFT JOIN adj_factor a ON a.ts_code = d.ts_code AND a.trade_date = d.trade_date
        {where}
        ORDER BY d.ts_code, d.trade_date
        """,
        params,
    ).fetch_df()
    f = df["adj_factor"].to_numpy()
    for c in ("open", "high", "low", "close"):
        df[c] = df[c].to_numpy() * f
    vol = df["vol"].to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        vwap_raw = np.where(vol > 0, df["amount"].to_numpy() * 10.0 / vol, np.nan)
    df["vwap"] = vwap_raw * f
    df["volume"] = vol
    df["factor"] = f
    df["symbol"] = df["ts_code"].map(_qlib_symbol)
    return df


def _load_index(con):
    """读 index_daily(指数日线，factor=1)，对齐个股同样的字段，供基准/特征用。"""
    df = con.execute(
        """
        SELECT ts_code, trade_date, open, high, low, close, vol, amount
        FROM index_daily ORDER BY ts_code, trade_date
        """
    ).fetch_df()
    if df.empty:
        return df
    vol = df["vol"].to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        df["vwap"] = np.where(vol > 0, df["amount"].to_numpy() * 10.0 / vol, np.nan)
    df["volume"] = vol
    df["factor"] = 1.0
    df["symbol"] = df["ts_code"].map(_qlib_symbol)
    return df


def _write_calendar(out, cal):
    """写 calendars/day.txt。"""
    p = os.path.join(out, "calendars")
    os.makedirs(p, exist_ok=True)
    with open(os.path.join(p, "day.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(cal) + "\n")


def _write_instruments(out, ranges):
    """写 instruments/all.txt：symbol\\tstart\\tend。ranges={symbol:(start,end)}。"""
    p = os.path.join(out, "instruments")
    os.makedirs(p, exist_ok=True)
    with open(os.path.join(p, "all.txt"), "w", encoding="utf-8") as fh:
        for sym in sorted(ranges):
            s, e = ranges[sym]
            fh.write(f"{sym}\t{s}\t{e}\n")


def _write_symbol_bins(out, symbol, sub, date2i):
    """把单个 symbol 的各字段写成 features/<sym小写>/<field>.day.bin。"""
    idx = sub["trade_date"].map(date2i).to_numpy()
    start, end = int(idx[0]), int(idx[-1])
    length = end - start + 1
    d = os.path.join(out, "features", symbol.lower())
    os.makedirs(d, exist_ok=True)
    rel = idx - start
    for field in FIELDS:
        arr = np.full(length, np.nan, dtype="<f4")
        arr[rel] = sub[field].to_numpy(dtype="<f4")
        with open(os.path.join(d, f"{field}.day.bin"), "wb") as fh:
            fh.write(struct.pack("<f", float(start)))
            arr.tofile(fh)


def _read_calendar(out):
    """读已有 calendars/day.txt，无则返回 []。"""
    p = os.path.join(out, "calendars", "day.txt")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as fh:
        return [ln.strip() for ln in fh if ln.strip()]


def _dump(out, df, cal):
    """按给定全局日历 cal，把 df 内所有 symbol 写成 bin，并刷新 instruments。"""
    date2i = {d: i for i, d in enumerate(cal)}
    ranges = {}
    for symbol, sub in df.groupby("symbol", sort=False):
        sub = sub.sort_values("trade_date")
        _write_symbol_bins(out, symbol, sub, date2i)
        ranges[symbol] = (sub["trade_date"].iloc[0], sub["trade_date"].iloc[-1])
    return ranges


def run_full(out):
    """全量重建：读全部数据，重写日历/instruments/全部 features。"""
    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    df = pd.concat([_load(con), _load_index(con)], ignore_index=True)
    con.close()
    df["trade_date"] = df["trade_date"].astype(str)
    cal = sorted(df["trade_date"].unique())
    print(f"[full] {df['symbol'].nunique()} 只股票，{len(cal)} 个交易日，重建中...")
    ranges = _dump(out, df, cal)
    _write_calendar(out, cal)
    _write_instruments(out, ranges)
    print(f"[full] 完成 → {out}")


def run_update(out):
    """增量：追加新交易日到日历，重建有新数据的个股(后复权历史不变，无关个股保持有效)。"""
    old_cal = _read_calendar(out)
    if not old_cal:
        print("[update] 未发现已有 bin，转为全量。")
        return run_full(out)
    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    last = old_cal[-1]
    new_dates = [r[0].strftime("%Y-%m-%d") for r in con.execute(
        "SELECT DISTINCT trade_date FROM daily WHERE CAST(trade_date AS VARCHAR) > ? ORDER BY 1",
        [last]).fetchall()]
    if not new_dates:
        con.close()
        print(f"[update] 日历已最新({last})，无需更新。")
        return
    affected = [r[0] for r in con.execute(
        "SELECT DISTINCT ts_code FROM daily WHERE CAST(trade_date AS VARCHAR) > ?", [last]).fetchall()]
    df = pd.concat([_load(con, codes=affected), _load_index(con)], ignore_index=True)
    con.close()
    df["trade_date"] = df["trade_date"].astype(str)
    cal = old_cal + new_dates
    print(f"[update] 新增 {len(new_dates)} 个交易日，重建 {len(affected)} 只受影响个股...")
    new_ranges = _dump(out, df, cal)
    _write_calendar(out, cal)

    ranges = {}
    p = os.path.join(out, "instruments", "all.txt")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            for ln in fh:
                parts = ln.strip().split("\t")
                if len(parts) == 3:
                    ranges[parts[0]] = (parts[1], parts[2])
    ranges.update(new_ranges)
    _write_instruments(out, ranges)
    print(f"[update] 完成 → {out}")


def main():
    """入口：--full 全量 / --update 增量。"""
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--full", action="store_true", help="全量重建")
    g.add_argument("--update", action="store_true", help="增量更新")
    ap.add_argument("--out", default=DEFAULT_OUT, help=f"输出目录(默认 {DEFAULT_OUT})")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    if args.full:
        run_full(args.out)
    else:
        run_update(args.out)


if __name__ == "__main__":
    main()
