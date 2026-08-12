"""爆量后持续缩量沉寂 形态筛选（一次性选股脚本，不进前端）。

形态定义（以 --date 为观察日 D0，默认库内最新交易日）：
  · 起爆日 B 在 D0 前 --min-lag … --max-lag 个交易日之间搜索（不再固定 20 日），
    要求 B 日成交量 >= --burst × B 日之前 20 日成交量均线（倍量）；多个候选取放量倍数最大者
  · B 之后到 D0 成交量持续萎缩：区间四等分，四段均量逐段递减，且 B 是区间内最高量（之后没再爆量）
  · 尾部 --tail-days 日成交量 <= --shrink × 起爆日成交量，且低于当日 20 日成交量均线
  · 流动性：D0 成交额与尾部均成交额均 >= --min-amt 万元
  · D0 当日 MACD(12,26,9,后复权价) 已金叉（--cross-within 个交易日内 DIF 上穿 DEA）
    或即将金叉（DIF 仍在 DEA 下方但柱状图连续走高，按当前斜率 --pre-days 日内可上穿）
过滤：科创板(688/689)、创业板(300/301)、北交所(4/8/BJ)、ST/*ST/退市、微盘股（总市值 < --min-mv 亿）

用法：python research/vol_burst_shrink_screen.py [--date 20260803] [--min-lag 20] [--max-lag 60]
      [--burst 1.5] [--shrink 0.5] [--min-mv 50] [--min-amt 5000] [--no-macd] [--no-html]
产出：控制台表格 + research/vol_burst_shrink_YYYYMMDD.csv
      + research/vol_burst_shrink_YYYYMMDD.html（把结果 JSON 注入模板
        research/vol_burst_shrink_template.html 的 __PAYLOAD__ 占位符，本地打开即可）
"""
import argparse
import json
import os
import sys

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cache_tushare import DUCKDB_PATH

LOOKBACK = 20
HISTORY = 160


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--date", default=None)
    p.add_argument("--burst", type=float, default=1.5)
    p.add_argument("--shrink", type=float, default=0.5)
    p.add_argument("--min-mv", type=float, default=50.0)
    p.add_argument("--tail-days", type=int, default=3)
    p.add_argument("--cross-within", type=int, default=2)
    p.add_argument("--pre-days", type=int, default=3)
    p.add_argument("--no-macd", action="store_true")
    p.add_argument("--min-amt", type=float, default=5000.0)
    p.add_argument("--min-lag", type=int, default=20)
    p.add_argument("--max-lag", type=int, default=60)
    p.add_argument("--no-html", action="store_true")
    return p.parse_args()


def save(df_or_text, path, writer):
    """写文件；目标被占用时改用带时间戳的文件名，返回实际路径。"""
    try:
        writer(df_or_text, path)
    except PermissionError:
        stem, ext = os.path.splitext(path)
        path = f"{stem}_{pd.Timestamp.now():%H%M%S}{ext}"
        writer(df_or_text, path)
    return path


def write_html(out: pd.DataFrame, meta: dict, end_date) -> str | None:
    """把结果注入模板 vol_burst_shrink_template.html，产出当日报告页。"""
    here = os.path.dirname(os.path.abspath(__file__))
    tpl_path = os.path.join(here, "vol_burst_shrink_template.html")
    if not os.path.exists(tpl_path):
        print(f"[warn] 模板缺失，跳过 HTML：{tpl_path}")
        return None
    with open(tpl_path, encoding="utf-8") as f:
        tpl = f.read()
    payload = {"meta": meta, "rows": out.to_dict("records")}
    html = tpl.replace("__PAYLOAD__", json.dumps(payload, ensure_ascii=False))
    return save(html, os.path.join(here, f"vol_burst_shrink_{end_date:%Y%m%d}.html"),
                lambda t, p: open(p, "w", encoding="utf-8").write(t))


def macd_state(adj_close: np.ndarray, cross_within: int, pre_days: int):
    """MACD(12,26,9) 状态：返回 (是否命中, 文字状态, DIF-DEA 柱值)；未命中返回 (False, ...)。"""
    s = pd.Series(adj_close)
    dif = (s.ewm(span=12, adjust=False).mean() - s.ewm(span=26, adjust=False).mean())
    dea = dif.ewm(span=9, adjust=False).mean()
    hist = (dif - dea).values
    if len(hist) < 40 or not np.isfinite(hist[-3:]).all():
        return False, "", 0.0
    if hist[-1] > 0:
        for k in range(1, cross_within + 1):
            if len(hist) > k and hist[-1 - k] <= 0:
                return True, f"已金叉{k}日", float(hist[-1])
        return False, "金叉过久", float(hist[-1])
    slope = hist[-1] - hist[-2]
    rising = hist[-1] > hist[-2] > hist[-3]
    if rising and slope > 0 and hist[-1] + pre_days * slope > 0:
        eta = int(np.ceil(-hist[-1] / slope))
        return True, f"即将金叉~{eta}日", float(hist[-1])
    return False, "未金叉", float(hist[-1])


def excluded_board(ts_code: str) -> bool:
    """科创板/创业板/北交所判定。"""
    num, _, ex = ts_code.partition(".")
    return ex == "BJ" or num[:3] in ("300", "301", "688", "689") or num[0] in ("4", "8")


def load_panel(con, end_date, need_days):
    """取截至 end_date 的每只股票最近 need_days 根日线（含 20 日均量所需前置窗口）。"""
    return con.execute(
        """
        WITH ranked AS (
          SELECT d.ts_code, d.trade_date, d.close, d.vol, d.amount,
                 d.close * COALESCE(a.adj_factor, 1) AS adj_close,
                 ROW_NUMBER() OVER (PARTITION BY d.ts_code ORDER BY d.trade_date DESC) rn
          FROM daily d
          LEFT JOIN adj_factor a ON a.ts_code = d.ts_code AND a.trade_date = d.trade_date
          WHERE d.trade_date <= ?
        )
        SELECT ts_code, trade_date, close, vol, amount, adj_close FROM ranked WHERE rn <= ?
        ORDER BY ts_code, trade_date
        """,
        [end_date, need_days],
    ).df()


def shrink_ok(v: np.ndarray, b: int, ma20: np.ndarray, args) -> list[float] | None:
    """判定 B 之后到末日是否「持续萎缩且未再爆量」，通过则返回四段均量。"""
    post = v[b + 1:]
    if len(post) < 8 or post.max() >= v[b]:
        return None
    blocks = [float(c.mean()) for c in np.array_split(post, 4)]
    if not all(blocks[i] > blocks[i + 1] for i in range(3)):
        return None
    n = len(v)
    for t in range(n - args.tail_days, n):
        if v[t] > args.shrink * v[b] or v[t] >= ma20[t]:
            return None
    return blocks


def screen(g: pd.DataFrame, args) -> dict | None:
    """对单只股票的日线序列做形态判定（起爆日在 lag 窗口内搜索），命中返回指标字典。"""
    v = g["vol"].values.astype(float)
    n = len(v)
    if n < args.max_lag + LOOKBACK * 2:
        return None
    ma20 = pd.Series(v).rolling(LOOKBACK).mean().values

    best = None
    for lag in range(args.min_lag, args.max_lag + 1):
        b = n - 1 - lag
        if b < LOOKBACK or v[b] <= 0:
            continue
        ma_prev = v[b - LOOKBACK:b].mean()
        if ma_prev <= 0:
            continue
        ratio = v[b] / ma_prev
        if ratio < args.burst:
            continue
        if best is not None and ratio <= best[1]:
            continue
        blocks = shrink_ok(v, b, ma20, args)
        if blocks is None:
            continue
        best = (b, ratio, blocks, lag)
    if best is None:
        return None
    b, ratio, blocks, lag = best

    amt_wan = g["amount"].values.astype(float) / 10.0
    amt3 = amt_wan[-args.tail_days:].mean()
    if amt3 < args.min_amt or amt_wan[-1] < args.min_amt:
        return None

    macd_txt = ""
    if not args.no_macd:
        ok, macd_txt, _ = macd_state(g["adj_close"].values.astype(float),
                                     args.cross_within, args.pre_days)
        if not ok:
            return None

    tail_mean = v[n - args.tail_days:].mean()
    return {
        "macd": macd_txt,
        "burst_date": str(pd.Timestamp(g["trade_date"].values[b]).date()),
        "lag_days": lag,
        "burst_vol_x_ma20": round(ratio, 2),
        "tail_vol_x_burst": round(tail_mean / v[b], 3),
        "tail_vol_x_ma20": round(tail_mean / ma20[-1], 2),
        "shrink_blocks": [round(x / 1e4, 1) for x in blocks],
        "close": round(float(g["close"].values[-1]), 2),
        "ret_since_burst": round(float(g["close"].values[-1] / g["close"].values[b] - 1) * 100, 1),
        "amt_d0_wan": round(float(amt_wan[-1]), 0),
        "amt_tail_avg_wan": round(float(amt3), 0),
    }


def main():
    args = parse_args()
    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    con.execute("SET threads TO 1")

    end_date = args.date or str(con.execute("SELECT max(trade_date) FROM daily").fetchone()[0])
    end_date = pd.Timestamp(end_date).date()

    meta = con.execute("SELECT ts_code, name, industry, delist_date FROM stock_meta").df()
    st_codes = set(
        con.execute(
            "SELECT DISTINCT ts_code FROM stock_st WHERE trade_date >= ?",
            [pd.Timestamp(end_date) - pd.Timedelta(days=400)],
        ).df()["ts_code"]
    )
    mv = con.execute(
        "SELECT ts_code, total_mv, circ_mv FROM daily_basic WHERE trade_date = ?", [end_date]
    ).df()

    px = load_panel(con, end_date, max(HISTORY, args.max_lag + LOOKBACK * 4))
    con.close()

    name_map = dict(zip(meta["ts_code"], meta["name"]))
    ind_map = dict(zip(meta["ts_code"], meta["industry"]))
    delisted = set(meta.loc[meta["delist_date"].astype(str).str.len() > 0, "ts_code"])
    mv_map = dict(zip(mv["ts_code"], mv["total_mv"]))
    circ_map = dict(zip(mv["ts_code"], mv["circ_mv"]))

    rows = []
    for ts, g in px.groupby("ts_code", sort=False):
        if excluded_board(ts) or ts in delisted or ts in st_codes:
            continue
        nm = name_map.get(ts, "")
        if any(k in nm for k in ("ST", "退")):
            continue
        total_mv = mv_map.get(ts)
        if total_mv is None or total_mv / 1e4 < args.min_mv:
            continue
        hit = screen(g.reset_index(drop=True), args)
        if hit is None:
            continue
        rows.append({
            "ts_code": ts, "name": nm, "industry": ind_map.get(ts, ""),
            "total_mv_yi": round(total_mv / 1e4, 1),
            "circ_mv_yi": round(circ_map.get(ts, np.nan) / 1e4, 1),
            **hit,
        })

    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values("tail_vol_x_burst").reset_index(drop=True)
    csv_path = save(out, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      f"vol_burst_shrink_{end_date:%Y%m%d}.csv"),
                    lambda d, p: d.to_csv(p, index=False, encoding="utf-8-sig"))

    macd_desc = ("不过滤" if args.no_macd
                 else f"金叉{args.cross_within}日内或{args.pre_days}日内将金叉")
    head = (f"观察日 D0={end_date}  起爆日搜索窗口=D0前{args.min_lag}~{args.max_lag}个交易日  "
            f"倍量阈值={args.burst}  尾部{args.tail_days}日缩量阈值<={args.shrink}×起爆量  "
            f"总市值>={args.min_mv}亿  MACD={macd_desc}  "
            f"成交额>={args.min_amt}万(D0 与尾部{args.tail_days}日均)")
    print(head)
    print(f"命中 {len(out)} 只 → {csv_path}")

    if not args.no_html:
        html_meta = {
            "date": f"{end_date:%Y-%m-%d}", "count": int(len(out)),
            "min_lag": args.min_lag, "max_lag": args.max_lag, "burst": args.burst,
            "shrink": args.shrink, "tail_days": args.tail_days, "min_mv": args.min_mv,
            "min_amt": args.min_amt, "macd": macd_desc,
            "generated": f"{pd.Timestamp.now():%Y-%m-%d %H:%M}",
        }
        html_path = write_html(out, html_meta, end_date)
        if html_path:
            print(f"报告页 → {html_path}")
    print()
    if not out.empty:
        show = out[["ts_code", "name", "industry", "total_mv_yi", "macd", "burst_date", "lag_days",
                    "burst_vol_x_ma20", "tail_vol_x_burst", "amt_tail_avg_wan",
                    "ret_since_burst", "close"]]
        print(show.to_string(index=False))


if __name__ == "__main__":
    main()
