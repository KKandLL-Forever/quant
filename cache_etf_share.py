"""cache_etf_share.py — 把「ETF 份额」页用到的宽基 ETF 的份额/规模历史(tushare etf_share_size)缓存到本地库。

独立小库 etf_cache.duckdb(不写 7GB 主库,避免并发写锁),表 etf_share:
  ts_code / trade_date / total_share(万份)/ total_size(万元)。
webapp 的 /api/etfshare 直接读它,不再每次实时打 tushare。

用法:
  python cache_etf_share.py            增量(每只从库内最新日之后补到今天)
  python cache_etf_share.py --full     全量重拉(2015 起,分段绕 2000 行上限)
ETF 列表与前端 EtfSharePage.tsx 的 ETF_LIST 保持一致(有增删两边一起改)。
依赖:tushare(token 读 .pyenv.local)/ duckdb。
"""
import os
import sys
import datetime as dt

_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)
import duckdb
import pandas as pd
import tushare as ts

ETF_DB = os.path.join(_ROOT, "etf_cache.duckdb")
FULL_START = "20150101"
# 与前端 EtfSharePage.tsx ETF_LIST 同步
ETFS = ["588080.SH", "588000.SH", "510050.SH", "512100.SH", "560010.SH",
        "159845.SZ", "159915.SZ", "510300.SH", "510500.SH", "159919.SZ"]


def _pro():
    """读 .pyenv.local 的 TUSHARE_TOKEN 建 pro。"""
    tok = os.environ.get("TUSHARE_TOKEN", "")
    pe = os.path.join(_ROOT, ".pyenv.local")
    if not tok and os.path.exists(pe):
        for line in open(pe, encoding="utf-8"):
            if line.strip().startswith("TUSHARE_TOKEN") and "=" in line:
                tok = line.split("=", 1)[1].strip().strip('"').strip("'")
    return ts.pro_api(tok)


def _conn():
    """打开(必要时建)etf_cache.duckdb。"""
    con = duckdb.connect(ETF_DB)
    con.execute("""CREATE TABLE IF NOT EXISTS etf_share (
        ts_code VARCHAR, trade_date VARCHAR, total_share DOUBLE, total_size DOUBLE,
        PRIMARY KEY (ts_code, trade_date))""")
    return con


def _fetch(pro, code, start, end):
    """分段拉 etf_share_size(绕 2000 行上限),返回去重排序的 DataFrame。"""
    parts = []
    s = pd.Timestamp(start)
    while s <= pd.Timestamp(end):
        e = min(s + pd.DateOffset(years=3) - pd.Timedelta(days=1), pd.Timestamp(end))
        try:
            d = pro.etf_share_size(ts_code=code, start_date=s.strftime("%Y%m%d"), end_date=e.strftime("%Y%m%d"))
            if d is not None and len(d):
                parts.append(d[["ts_code", "trade_date", "total_share", "total_size"]])
        except Exception:
            pass
        s = e + pd.Timedelta(days=1)
    if not parts:
        return None
    return pd.concat(parts).drop_duplicates(["ts_code", "trade_date"]).sort_values("trade_date")


def run(full: bool = False):
    """把 ETFS 列表逐只增量/全量拉进 etf_cache.duckdb。"""
    sys.stdout.reconfigure(encoding="utf-8")
    pro = _pro()
    con = _conn()
    end = dt.date.today().strftime("%Y%m%d")
    for code in ETFS:
        if full:
            start = FULL_START
        else:
            mx = con.execute("SELECT max(trade_date) FROM etf_share WHERE ts_code=?", [code]).fetchone()[0]
            start = (pd.Timestamp(mx) + pd.Timedelta(days=1)).strftime("%Y%m%d") if mx else FULL_START
        if start > end:
            print(f"{code}: 已最新"); continue
        df = _fetch(pro, code, start, end)
        if df is None or df.empty:
            print(f"{code}: 无新增({start}~{end})"); continue
        con.register("_new", df)
        con.execute("INSERT OR REPLACE INTO etf_share SELECT ts_code,trade_date,total_share,total_size FROM _new")
        con.unregister("_new")
        rng = con.execute("SELECT min(trade_date),max(trade_date),count(*) FROM etf_share WHERE ts_code=?", [code]).fetchone()
        print(f"{code}: +{len(df)} 行 → 库内 {rng[0]}~{rng[1]} 共{rng[2]}")
    con.close()
    print("done →", ETF_DB)


if __name__ == "__main__":
    run(full="--full" in sys.argv)
