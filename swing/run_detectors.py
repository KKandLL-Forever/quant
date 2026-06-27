"""
run_detectors.py — 实时趋势检测器同台对比(第2步):方向层 × 量价验证 × ADX闸门

在指数日线上用**只看过去**的指标做多/空仓择时(次日开盘执行,杜绝未来函数),对比:
  方向层:双均线 / 唐奇安通道突破 / Supertrend
  量价验证:放量确认突破、OBV 共振(价量同向才进)
  环境层:ADX>阈值 才放行(过滤震荡假信号)
用第1步 ZigZag 事后分段的"吃中段"理论值当分母,量每个检测器的**捕获率**;另报
交易次数(假信号代理)、胜率、在场时间、净值 vs 买入持有。产出对比 HTML。

环境：.venv312。用法：python swing/run_detectors.py --code 000688.SH --start 2019-01-01
依赖：DuckDB(index_daily)或 tushare 回退;matplotlib。复用 run_segment_zigzag 的 ZigZag/取数。
"""

import argparse
import base64
import io
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.expanduser("~/AI/quart/first10"))

import duckdb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from cache_tushare import DUCKDB_PATH
from run_segment_zigzag import _zigzag, _fetch_index_tushare

OUT = os.path.expanduser("~/AI/quart/swing/detectors_{code}.html")
COST = 0.0006


def _load(code, start, end=None):
    """取指数 OHLCV(库优先,缺则 tushare 回退,补 high/low/vol)。"""
    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    df = con.execute("SELECT trade_date, high, low, close, vol FROM index_daily "
                     "WHERE ts_code=? AND trade_date>=? ORDER BY trade_date", [code, start]).fetch_df()
    con.close()
    if len(df) < 60:
        env = os.path.expanduser("~/AI/quart/.pyenv.local")
        if os.path.exists(env):
            for line in open(env):
                if line.strip().startswith("TUSHARE_TOKEN") and "=" in line:
                    os.environ["TUSHARE_TOKEN"] = line.split("=", 1)[1].strip().strip('"').strip("'")
        import tushare as ts
        ts.set_token(os.environ["TUSHARE_TOKEN"])
        pro = ts.pro_api()
        s = start.replace("-", "")
        parts = []
        for y in range(int(s[:4]), pd.Timestamp.today().year + 1):
            d = pro.index_daily(ts_code=code, start_date=f"{max(int(s), y*10000+101)}", end_date=f"{y}1231")
            if d is not None and len(d):
                parts.append(d[["trade_date", "high", "low", "close", "vol"]])
        df = pd.concat(parts, ignore_index=True)
        df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
        df = df.sort_values("trade_date").reset_index(drop=True)
    else:
        df["trade_date"] = pd.to_datetime(df["trade_date"])
    if end:
        df = df[df["trade_date"] <= pd.Timestamp(end)].reset_index(drop=True)
    return df


def _atr(h, l, c, n=10):
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def _adx(h, l, c, n=14):
    up = h.diff()
    dn = -l.diff()
    plus = np.where((up > dn) & (up > 0), up, 0.0)
    minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    atr = _atr(h, l, c, n)
    pdi = 100 * pd.Series(plus, index=h.index).ewm(alpha=1 / n, adjust=False).mean() / atr
    mdi = 100 * pd.Series(minus, index=h.index).ewm(alpha=1 / n, adjust=False).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean()


def _supertrend(h, l, c, n=10, mult=3.0):
    atr = _atr(h, l, c, n)
    hl2 = (h + l) / 2
    up = hl2 - mult * atr
    dn = hl2 + mult * atr
    fu = up.copy()
    fd = dn.copy()
    dirn = pd.Series(1, index=c.index)
    for i in range(1, len(c)):
        fu.iloc[i] = max(up.iloc[i], fu.iloc[i - 1]) if c.iloc[i - 1] > fu.iloc[i - 1] else up.iloc[i]
        fd.iloc[i] = min(dn.iloc[i], fd.iloc[i - 1]) if c.iloc[i - 1] < fd.iloc[i - 1] else dn.iloc[i]
        if c.iloc[i] > fd.iloc[i - 1]:
            dirn.iloc[i] = 1
        elif c.iloc[i] < fu.iloc[i - 1]:
            dirn.iloc[i] = -1
        else:
            dirn.iloc[i] = dirn.iloc[i - 1]
    return (dirn > 0).astype(int)


def _donchian_pos(c, h, l, n=55, exit_n=20):
    """突破上轨进、跌破下轨出的多/空仓状态机(用前 n 日,不含当日)。"""
    up = h.rolling(n).max().shift(1)
    dn = l.rolling(exit_n).min().shift(1)
    pos = np.zeros(len(c))
    s = 0
    for i in range(len(c)):
        if s == 0 and c.iloc[i] > (up.iloc[i] if pd.notna(up.iloc[i]) else np.inf):
            s = 1
        elif s == 1 and c.iloc[i] < (dn.iloc[i] if pd.notna(dn.iloc[i]) else -np.inf):
            s = 0
        pos[i] = s
    return pd.Series(pos, index=c.index)


def _donchian_volconfirm(c, h, l, v, n=55, exit_n=20, vk=1.5, obv_gate=False):
    """唐奇安突破需放量(vol>vk×20日均量)确认;obv_gate 时还需 OBV 在其均线上方。"""
    up = h.rolling(n).max().shift(1)
    dn = l.rolling(exit_n).min().shift(1)
    vma = v.rolling(20).mean()
    obv = (np.sign(c.diff().fillna(0)) * v).cumsum()
    obvma = obv.rolling(30).mean()
    pos = np.zeros(len(c))
    s = 0
    for i in range(len(c)):
        brk = c.iloc[i] > (up.iloc[i] if pd.notna(up.iloc[i]) else np.inf)
        vol_ok = pd.notna(vma.iloc[i]) and v.iloc[i] > vk * vma.iloc[i]
        obv_ok = (not obv_gate) or (pd.notna(obvma.iloc[i]) and obv.iloc[i] > obvma.iloc[i])
        if s == 0 and brk and vol_ok and obv_ok:
            s = 1
        elif s == 1 and c.iloc[i] < (dn.iloc[i] if pd.notna(dn.iloc[i]) else -np.inf):
            s = 0
        pos[i] = s
    return pd.Series(pos, index=c.index)


def _bt(c, pos, dates):
    """次日执行回测:返回净值、汇总指标、交易次数。"""
    ret = c.pct_change().fillna(0).values
    p = pos.shift(1).fillna(0).values
    trades = int((np.abs(np.diff(np.concatenate([[0], p]))) > 0).sum())
    sr = p * ret - np.abs(np.diff(np.concatenate([[0], p]))) * COST
    nav = np.cumprod(1 + sr)
    ann = nav[-1] ** (252 / len(sr)) - 1
    peak = np.maximum.accumulate(nav)
    mdd = ((nav - peak) / peak).min()
    sh = sr.mean() / sr.std() * np.sqrt(252) if sr.std() > 0 else 0
    expo = p.mean()
    return nav, (nav[-1] - 1, ann, mdd, sh, trades, expo)


def _ceiling(c, thr=0.15, eat=0.6):
    """第1步:吃中段理论净值(分母)。"""
    cl = c.values
    piv = _zigzag(cl, thr)
    margin = (1 - eat) / 2
    nav = 1.0
    for a, b in zip(piv[:-1], piv[1:]):
        if cl[b] >= cl[a]:
            lo, hi = cl[a], cl[b]
            nav *= ((lo + (1 - margin) * (hi - lo)) / (lo + margin * (hi - lo))) * (1 - 2 * COST)
    return nav - 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default="000688.SH")
    ap.add_argument("--start", default="2019-01-01")
    ap.add_argument("--end", default=None, help="结束日 YYYY-MM-DD,默认到最新")
    ap.add_argument("--adx", type=float, default=20, help="ADX 闸门阈值")
    args = ap.parse_args()

    df = _load(args.code, args.start, args.end)
    c, h, l, v = df["close"], df["high"], df["low"], df["vol"]
    dates = df["trade_date"].tolist()
    adx = _adx(h, l, c)
    gate = (adx > args.adx).astype(int)
    ceil = _ceiling(c)
    bh = c.iloc[-1] / c.iloc[0] - 1

    dets = {
        "唐奇安55/20": _donchian_pos(c, h, l),
        "Supertrend": _supertrend(h, l, c),
    }
    extra = {}
    for k, pos in dets.items():
        extra[k + "+ADX"] = (pos.values.astype(int) & gate.values).astype(float)
        extra[k + "+ADX"] = pd.Series(extra[k + "+ADX"], index=c.index)
    dets.update(extra)

    navs, rows = {}, []
    for k, pos in dets.items():
        nav, (tot, ann, mdd, sh, tr, expo) = _bt(c, pos, dates)
        navs[k] = nav
        cap = tot / ceil if ceil > 0 else 0
        rows.append((k, tot, ann, mdd, sh, tr, expo, cap))

    plt.rcParams["font.sans-serif"] = ["Arial Unicode MS", "PingFang SC", "Heiti SC"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, ax = plt.subplots(figsize=(12, 5))
    show = ["唐奇安55/20", "Supertrend"]
    for k in show:
        ax.plot(dates, navs[k], label=k, linewidth=1.5)
    ax.plot(dates, np.cumprod(1 + c.pct_change().fillna(0).values), label="买入持有", color="#999", linestyle="--")
    ax.legend(loc="upper left", fontsize=9)
    ax.set_ylabel("净值")
    ax.grid(alpha=0.3)
    fig.autofmt_xdate()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    img = base64.b64encode(buf.getvalue()).decode()

    rows.sort(key=lambda x: -x[7])
    trows = ""
    for k, tot, ann, mdd, sh, tr, expo, cap in rows:
        trows += (f"<tr><td style='text-align:left'>{k}</td><td>{tot*100:+.0f}%</td><td>{ann*100:.1f}%</td>"
                  f"<td class=neg>{mdd*100:.0f}%</td><td>{sh:.2f}</td><td>{tr}</td><td>{expo*100:.0f}%</td>"
                  f"<td class=pos><b>{cap*100:.0f}%</b></td></tr>")

    html = f"""<!doctype html><html lang=zh><head><meta charset=utf-8><title>检测器对比 {args.code}</title><style>
body{{font-family:-apple-system,"PingFang SC",sans-serif;max-width:1040px;margin:24px auto;padding:0 16px;color:#222}}
h1{{font-size:21px}} h2{{font-size:16px;border-left:4px solid #c0392b;padding-left:8px;margin-top:24px}}
table{{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0}} th,td{{border:1px solid #ddd;padding:5px 8px;text-align:center}}
th{{background:#f7f7f7}} .pos{{color:#c0392b}} .neg{{color:#27ae60}}
.note{{background:#fff8e1;border:1px solid #ffe082;border-radius:6px;padding:10px 14px;font-size:13px;color:#6d4c41}}
</style></head><body>
<h1>实时趋势检测器对比 — {args.code}</h1>
<p>区间 <b>{dates[0].date()}~{dates[-1].date()}</b> | 买入持有 <b>{bh*100:+.0f}%</b> | 吃中段理论上限(分母) <b>{ceil*100:+.0f}%</b> | 次日执行/单边费{COST*1000:.1f}‰</p>
<h2>净值曲线(唐奇安系 + Supertrend vs 买入持有)</h2>
<img src="data:image/png;base64,{img}" style="width:100%">
<h2>全部检测器(按捕获率排序)</h2>
<table><tr><th style='text-align:left'>检测器</th><th>总收益</th><th>年化</th><th>最大回撤</th><th>夏普</th><th>交易次数</th><th>在场时间</th><th>捕获率</th></tr>{trows}</table>
<div class=note><b>捕获率</b>=策略总收益 ÷ 第1步"吃中段60%"理论上限,衡量实时检测器逼近天花板的程度。
<b>交易次数</b>越多=假信号/打脸越频繁。<b>在场时间</b>=持仓占比(低=多在空仓躲跌)。
均为只看过去、次日执行,无未来函数。震荡市检测器靠 +ADX 版过滤。</div>
</body></html>"""
    out = OUT.format(code=args.code.replace(".", "_"))
    with open(out, "w") as f:
        f.write(html)
    print(f"报告:{out}")
    best = rows[0]
    print(f"{args.code} | 买入持有{bh*100:+.0f}% | 天花板{ceil*100:+.0f}% | 最佳:{best[0]} 收益{best[1]*100:+.0f}% 捕获{best[7]*100:.0f}% 交易{best[5]}次")


if __name__ == "__main__":
    main()
