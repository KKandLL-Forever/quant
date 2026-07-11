"""商品期货数据增量缓存到本地 DuckDB(与 cache_tushare.py 分离,只共用同一个库文件)。

缓存三张表(tushare 期货接口):
  fut_meta     合约基础信息(fut_basic):ts_code/symbol/name/exchange/fut_code/乘数/交割月/上市退市日
  fut_daily    日线行情(fut_daily):主力连续 + 各月合约 的 OHLC/结算/量/持仓;PK(ts_code,trade_date)
  fut_mapping  主力映射(fut_mapping):主连 → 当日实际主力月合约;PK(cont_code,trade_date)

默认追踪一篮子流动商品(碳酸锂/螺纹/铜/银/橡胶/豆粕/铁矿/棕榈/焦炭/PTA/白糖/甲醇)的主力连续。
用法:
  python futures_cache.py                 # 增量:更新 fut_meta + 各主连日线 + 主力映射(到最新交易日)
  python futures_cache.py --init          # 首次全量:主连长历史按年切片(绕开 fut_daily 单次2000行上限)
  python futures_cache.py --contracts     # 额外缓存各品种全部月合约日线(量大,供主连后复权/展期分析)
  python futures_cache.py --roots LC,RB   # 只更指定品种(root=fut_code,逗号分隔)

依赖:.pyenv.local 里的 TUSHARE_TOKEN(与 cache_tushare 同源);期货接口需相应积分。
fut_daily 单次上限 2000 行,故长区间/主连按年切片拉取。
"""
import argparse
import os
import time
import duckdb
import pandas as pd
import tushare as ts
from cache_tushare import DUCKDB_PATH

ROOTS = {
    "LC": ("GFEX", "GFE", "碳酸锂"), "RB": ("SHF", "SHF", "螺纹钢"),
    "CU": ("SHF", "SHF", "沪铜"), "AG": ("SHF", "SHF", "沪银"),
    "RU": ("SHF", "SHF", "橡胶"), "M": ("DCE", "DCE", "豆粕"),
    "I": ("DCE", "DCE", "铁矿石"), "P": ("DCE", "DCE", "棕榈油"),
    "J": ("DCE", "DCE", "焦炭"), "TA": ("ZCE", "ZCE", "PTA"),
    "SR": ("ZCE", "ZCE", "白糖"), "MA": ("ZCE", "ZCE", "甲醇"),
}
HIST_START = "20130101"
_DAILY_COLS = ["ts_code", "trade_date", "open", "high", "low", "close",
               "settle", "pre_close", "pre_settle", "change1", "vol", "amount", "oi"]

_SCHEMA = {
    "fut_meta": """
        CREATE TABLE IF NOT EXISTS fut_meta (
            ts_code VARCHAR, symbol VARCHAR, exchange VARCHAR, name VARCHAR,
            fut_code VARCHAR, multiplier DOUBLE, trade_unit VARCHAR, per_unit DOUBLE,
            quote_unit VARCHAR, list_date VARCHAR, delist_date VARCHAR,
            d_month VARCHAR, last_ddate VARCHAR,
            PRIMARY KEY (ts_code)
        )""",
    "fut_daily": """
        CREATE TABLE IF NOT EXISTS fut_daily (
            ts_code VARCHAR, trade_date VARCHAR,
            open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, settle DOUBLE,
            pre_close DOUBLE, pre_settle DOUBLE, change1 DOUBLE,
            vol DOUBLE, amount DOUBLE, oi DOUBLE,
            PRIMARY KEY (ts_code, trade_date)
        )""",
    "fut_mapping": """
        CREATE TABLE IF NOT EXISTS fut_mapping (
            cont_code VARCHAR, trade_date VARCHAR, mapping_ts_code VARCHAR,
            PRIMARY KEY (cont_code, trade_date)
        )""",
}


def _token() -> str:
    """从环境变量或 .pyenv.local(与本文件同目录)读 TUSHARE_TOKEN。"""
    tok = os.environ.get("TUSHARE_TOKEN", "").strip()
    if tok:
        return tok
    f = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".pyenv.local")
    if os.path.exists(f):
        for ln in open(f, encoding="utf-8"):
            if ln.strip().startswith("TUSHARE_TOKEN"):
                return ln.split("=", 1)[1].strip()
    return ""


def _retry(fn, **kw):
    """瞬时错误有限重试(网络/限流),参数/权限错误不重试。"""
    for i in range(4):
        try:
            return fn(**kw)
        except Exception as e:
            msg = str(e)
            if any(k in msg for k in ("权限", "积分", "没有访问", "params")):
                raise
            if i == 3:
                raise
            time.sleep(1.5 * (i + 1))


def _today() -> str:
    return pd.Timestamp.today().strftime("%Y%m%d")


def _watermark(con, ts_code: str) -> str:
    r = con.execute("SELECT max(trade_date) FROM fut_daily WHERE ts_code=?", [ts_code]).fetchone()
    return r[0] if r and r[0] else ""


def _write(con, table: str, df: pd.DataFrame, cols: list[str]) -> int:
    if df is None or df.empty:
        return 0
    d = df[[c for c in cols if c in df.columns]].copy()
    con.register("_w", d)
    con.execute(f"INSERT OR REPLACE INTO {table} SELECT {','.join(cols)} FROM _w")
    con.unregister("_w")
    return len(d)


def refresh_meta(pro, con, roots: list[str]) -> None:
    """按品种所在交易所拉 fut_basic,过滤到追踪品种,upsert fut_meta。"""
    exchs = sorted({ROOTS[r][0] for r in roots})
    wanted = set(roots)
    total = 0
    for ex in exchs:
        df = _retry(pro.fut_basic, exchange=ex)
        if df is None or df.empty:
            continue
        df = df[df["fut_code"].isin(wanted)]
        total += _write(con, "fut_meta", df,
                        ["ts_code", "symbol", "exchange", "name", "fut_code", "multiplier",
                         "trade_unit", "per_unit", "quote_unit", "list_date", "delist_date",
                         "d_month", "last_ddate"])
        time.sleep(0.15)
    print(f"fut_meta: upsert {total} 合约")


def _fetch_daily_sliced(pro, ts_code: str, start: str, end: str) -> pd.DataFrame:
    """按自然年切片拉 fut_daily(单次上限2000行),拼齐。"""
    parts = []
    y0, y1 = int(start[:4]), int(end[:4])
    for y in range(y0, y1 + 1):
        s = max(start, f"{y}0101")
        e = min(end, f"{y}1231")
        if s > e:
            continue
        d = _retry(pro.fut_daily, ts_code=ts_code, start_date=s, end_date=e)
        if d is not None and not d.empty:
            parts.append(d[[c for c in _DAILY_COLS if c in d.columns]])
        time.sleep(0.12)
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True).drop_duplicates(["ts_code", "trade_date"])


def update_daily(pro, con, ts_code: str, init: bool) -> int:
    """单个 ts_code(主连或月合约)的日线增量:从水位+1 拉到今天;init 或空表则全史切片。"""
    wm = _watermark(con, ts_code)
    end = _today()
    if init or not wm:
        df = _fetch_daily_sliced(pro, ts_code, HIST_START, end)
    else:
        start = (pd.Timestamp(wm) + pd.Timedelta(days=1)).strftime("%Y%m%d")
        if start > end:
            return 0
        df = _retry(pro.fut_daily, ts_code=ts_code, start_date=start, end_date=end)
    return _write(con, "fut_daily", df, _DAILY_COLS)


def update_mapping(pro, con, cont_code: str) -> int:
    """主连的主力映射(全量小表,幂等 upsert)。"""
    df = _retry(pro.fut_mapping, ts_code=cont_code)
    if df is None or df.empty:
        return 0
    df = df.rename(columns={"ts_code": "cont_code"})
    return _write(con, "fut_mapping", df, ["cont_code", "trade_date", "mapping_ts_code"])


def _month_contracts(con, root: str) -> list[str]:
    rows = con.execute("SELECT ts_code FROM fut_meta WHERE fut_code=? ORDER BY ts_code", [root]).fetchall()
    return [r[0] for r in rows]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", action="store_true", help="首次全量(主连长历史按年切片)")
    ap.add_argument("--contracts", action="store_true", help="额外缓存各品种全部月合约日线")
    ap.add_argument("--roots", default="", help="只更指定品种(fut_code,逗号分隔)")
    args = ap.parse_args()

    tok = _token()
    if not tok:
        raise SystemExit("缺 TUSHARE_TOKEN:请在 .pyenv.local 配置或设为环境变量")
    pro = ts.pro_api(tok)

    roots = [r.strip().upper() for r in args.roots.split(",") if r.strip()] if args.roots else list(ROOTS)
    bad = [r for r in roots if r not in ROOTS]
    if bad:
        raise SystemExit(f"未知品种 {bad},可选:{list(ROOTS)}")

    con = duckdb.connect(DUCKDB_PATH)
    for ddl in _SCHEMA.values():
        con.execute(ddl)

    refresh_meta(pro, con, roots)

    print(f"\n主力连续日线 + 主力映射（{len(roots)} 品种，{'全量' if args.init else '增量'}）：")
    for r in roots:
        _, suf, cn = ROOTS[r]
        cont = f"{r}.{suf}"
        nd = update_daily(pro, con, cont, args.init)
        nm = update_mapping(pro, con, cont)
        print(f"  {cn:<6}{cont:<10} 日线+{nd:<5} 映射={nm}")

    if args.contracts:
        print(f"\n月合约日线（{'全量' if args.init else '增量'}，量大）：")
        for r in roots:
            codes = _month_contracts(con, r)
            tot = sum(update_daily(pro, con, c, args.init) for c in codes)
            print(f"  {ROOTS[r][2]:<6} {len(codes)} 合约 · 新增 {tot} 行")

    con.close()
    print("\n完成。")


if __name__ == "__main__":
    main()
