"""
factor_select.py — 通用截面多因子选股（月频，多空 + 纯多头），第一版（量价+估值因子）

正规截面多因子框架：每月末对全市场打分排序 → 买高分一篮子、（多空版）空低分一篮子 → 月度换仓。
靠「微弱单因子 IC × 大量分散 × 长期重复」赚钱，不赌单只。

股票池：剔 ST、上市<120 交易日、北交所(.BJ)、无估值数据；全市场主板/创业/科创。
因子（已按「值越大预期收益越高」对齐方向；每月截面内 去极值→行业+市值中性化→z 标准化）：
  value   估值：1/PB、1/PE_ttm、1/PS_ttm、股息率 的等权（便宜=好）
  reversal 短期反转：上月收益取负（A股短期反转强）
  lowvol  低波：60日波动率取负
  turnover 低换手：月末换手率取负（高换手→低未来收益）
  momentum 动量：12-1 月动量（过去12月剔最近1月；A股偏弱，单列观察）
中性化：每因子对 ln(流通市值)+行业哑变量回归取残差，去掉规模/行业暴露（否则只是变相押小盘/某行业）。
组合：综合分=各因子 z 等权；按综合分分 10 档，D10=最高分、D1=最低分。
  多空 = D10−D1 月收益（机构做法，能吃短边）；纯多头 = D10−全市场等权（散户可做，只吃长边）。
收益口径：月末收盘 → 次月末收盘（close-to-close，月频研究惯例；1日择时滑点二阶，忽略）。

缺：无 ROE/毛利/净利增速（需另抓 fina_indicator），故暂无质量/成长因子。

产出：quant_select/html_output/factor_select_{start}.html（IC/ICIR、分档单调性、多空/多头净值曲线）
用法：python quant_select/factor_select.py   [--start 20160101]
"""

import os
import sys
import argparse
import webbrowser
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

import duckdb
from scipy.stats import spearmanr
from db_loader import _ENV

DUCK_PATH = _ENV.get("LOCAL_DUCKDB_PATH", "stock_data_tushare.duckdb")

FACTORS = ["value", "quality", "growth", "reversal", "lowvol", "turnover", "momentum"]
FACTOR_CN = {"value": "估值(便宜)", "quality": "质量(高ROE/毛利/低负债)", "growth": "成长(净利/营收同比)",
             "reversal": "短期反转", "lowvol": "低波动", "turnover": "低换手", "momentum": "12-1动量"}
COMPOSITE = ["value", "reversal", "lowvol", "turnover"]   # quality/growth/momentum 单列观察（A股月频近零IC），不进综合分
COST = 0.0015   # 单边交易成本，main() 可由 --cost 覆盖
PPY = 12        # 每年期数（月频12/周频52），main() 按 --freq 设置
FREQ_CN = "月"
FREQ = "month"
BORROW = 0.085  # 融券年化利息（做空持仓成本），main() 可由 --borrow 覆盖


_PANEL_SQL = """
WITH me AS (
    SELECT trade_date FROM (
        SELECT trade_date, ROW_NUMBER() OVER (PARTITION BY {PART}
                                              ORDER BY trade_date DESC) rn
        FROM (SELECT DISTINCT trade_date FROM daily)
    ) WHERE rn = 1
),
fd AS (
    SELECT ts_code, trade_date, close,
        LAG(close,21)  OVER w / NULLIF(LAG(close,252) OVER w,0) - 1 AS momentum,
        close / NULLIF(LAG(close,21) OVER w,0) - 1                  AS ret21,
        stddev_samp(pct_chg) OVER (PARTITION BY ts_code ORDER BY trade_date
                                   ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) AS vol60
    FROM daily
    WINDOW w AS (PARTITION BY ts_code ORDER BY trade_date)
),
fina AS (
    SELECT ts_code, CAST(strptime(ann_date,'%Y%m%d') AS DATE) AS ann_dt,
        roe, grossprofit_margin, debt_to_assets, netprofit_yoy, or_yoy
    FROM fina_indicator WHERE ann_date IS NOT NULL
),
panel AS (
    SELECT fd.ts_code, fd.trade_date, fd.close, fd.momentum, fd.ret21, fd.vol60,
        db.pb, db.pe_ttm, db.ps_ttm, db.dv_ratio, db.circ_mv, db.turnover_rate,
        sm.industry, sm.list_date,
        CASE WHEN st.ts_code IS NULL THEN 0 ELSE 1 END AS is_st,
        f.roe, f.grossprofit_margin, f.debt_to_assets, f.netprofit_yoy, f.or_yoy,
        COALESCE(mg.rqye, 0) AS rqye
    FROM me JOIN fd ON fd.trade_date = me.trade_date
    LEFT JOIN daily_basic db ON db.ts_code=fd.ts_code AND db.trade_date=fd.trade_date
    LEFT JOIN stock_meta  sm ON sm.ts_code=fd.ts_code
    LEFT JOIN stock_st    st ON st.ts_code=fd.ts_code AND st.trade_date=fd.trade_date
    ASOF LEFT JOIN fina   f  ON f.ts_code=fd.ts_code AND fd.trade_date >= f.ann_dt
    LEFT JOIN margin_detail mg ON mg.ts_code=fd.ts_code AND mg.trade_date=fd.trade_date
)
SELECT *,
    LEAD(close,1) OVER (PARTITION BY ts_code ORDER BY trade_date)/NULLIF(close,0)-1 AS fwd_ret
FROM panel
ORDER BY trade_date, ts_code
"""


def _winsor(s, p=0.01):
    lo, hi = s.quantile(p), s.quantile(1 - p)
    return s.clip(lo, hi)


def _neutralize(f, ln_mv, ind):
    """对 ln(市值)+行业哑变量回归取残差，去掉规模/行业暴露。"""
    out = pd.Series(np.nan, index=f.index)
    m = f.notna() & ln_mv.notna() & ind.notna()
    if m.sum() < 30:
        return out
    dum = pd.get_dummies(ind[m], drop_first=True).astype(float)
    X = np.column_stack([np.ones(m.sum()), ln_mv[m].values, dum.values])
    y = _winsor(f[m]).values
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    out[m] = y - X @ beta
    return out


def _z(s):
    sd = s.std()
    return (s - s.mean()) / sd if sd and sd > 0 else s * 0.0


def _build_scores(g):
    """单月截面：构造 5 因子 → 中性化 → z 标准化 → 综合分。返回带因子/综合分/分档的 df。"""
    g = g.copy()
    ln_mv = np.log(g["circ_mv"].where(g["circ_mv"] > 0))
    ind = g["industry"]

    bp = 1.0 / g["pb"].where(g["pb"] > 0)
    ep = 1.0 / g["pe_ttm"].where(g["pe_ttm"] > 0)
    sp = 1.0 / g["ps_ttm"].where(g["ps_ttm"] > 0)
    dp = g["dv_ratio"]
    value_raw = pd.concat([_z(_neutralize(x, ln_mv, ind)) for x in (bp, ep, sp, dp)], axis=1).mean(axis=1)
    quality_raw = pd.concat([_z(_neutralize(x, ln_mv, ind))
                             for x in (g["roe"], g["grossprofit_margin"], -g["debt_to_assets"])], axis=1).mean(axis=1)
    growth_raw = pd.concat([_z(_neutralize(x, ln_mv, ind))
                            for x in (g["netprofit_yoy"], g["or_yoy"])], axis=1).mean(axis=1)

    raw = {
        "value":    value_raw,
        "quality":  quality_raw,
        "growth":   growth_raw,
        "reversal": -g["ret21"],
        "lowvol":   -g["vol60"],
        "turnover": -g["turnover_rate"],
        "momentum": g["momentum"],
    }
    for k in FACTORS:
        g[k] = _z(raw[k]) if k in ("value", "quality", "growth") else _z(_neutralize(raw[k], ln_mv, ind))

    g["composite"] = g[COMPOSITE].mean(axis=1)
    valid = g["composite"].notna() & g["fwd_ret"].notna()
    g = g[valid].copy()
    if len(g) >= 50:
        g["decile"] = pd.qcut(g["composite"].rank(method="first"), 10, labels=False) + 1
    else:
        g["decile"] = np.nan
    return g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20160101")
    ap.add_argument("--cost", type=float, default=0.0015, help="单边交易成本(默认0.15%=佣金+印花+冲击)")
    ap.add_argument("--split", default="20210101", help="样本外切点：此日期前建模、之后样本外")
    ap.add_argument("--freq", default="month", choices=["month", "week"], help="调仓频率")
    ap.add_argument("--borrow", type=float, default=0.085, help="融券年化利息(做空成本)")
    args = ap.parse_args()
    global COST, PPY, FREQ_CN, FREQ, BORROW
    COST = args.cost
    BORROW = args.borrow
    PPY = 12 if args.freq == "month" else 52
    FREQ_CN = "月" if args.freq == "month" else "周"
    FREQ = args.freq
    part = ("year(trade_date), month(trade_date)" if args.freq == "month"
            else "date_trunc('week', trade_date)")

    print(f"=== 拉{FREQ_CN}末面板（{args.freq}）===")
    con = duckdb.connect(DUCK_PATH, read_only=True)
    panel = con.execute(_PANEL_SQL.replace("{PART}", part)).df()
    con.close()
    panel["trade_date"] = panel["trade_date"].astype(str).str.replace("-", "").str[:8]
    panel = panel[panel["trade_date"] >= args.start]

    panel["list_date"] = panel["list_date"].astype(str).str.replace("-", "").str[:8]
    age_ok = (pd.to_datetime(panel["trade_date"], format="%Y%m%d")
              - pd.to_datetime(panel["list_date"], format="%Y%m%d", errors="coerce")).dt.days > 180
    uni = panel[(panel["is_st"] == 0) & age_ok
                & ~panel["ts_code"].str.endswith(".BJ")
                & panel["circ_mv"].notna() & panel["fwd_ret"].notna()].copy()
    print(f"{FREQ_CN}末快照 {uni['trade_date'].nunique()} 个{FREQ_CN} · 样本 {len(uni)} · "
          f"均每月 {len(uni)//max(uni['trade_date'].nunique(),1)} 只")

    print("\n=== 逐月打分 ===")
    scored = pd.concat([_build_scores(uni[uni["trade_date"] == m])
                        for m in sorted(uni["trade_date"].unique())], ignore_index=True)
    months = sorted(scored["trade_date"].unique())

    roundtrip = 2 * COST           # 买卖各一次的成本（每边 COST）
    ic_rows = {f: [] for f in FACTORS + ["composite"]}
    ic_month = []                  # 综合分 IC 对应的月份（用于样本外切分）
    dec_ret = {d: [] for d in range(1, 11)}
    mlist = []                     # 有有效组合的月份
    ls_g, lo_g, ls_n, lo_n = [], [], [], []   # 多空/多头 的 毛/净 月收益
    lsr_g, lsr_n, short_cov = [], [], []       # 可执行多空(只空有券源的D1) 毛/净 + D1可空覆盖率
    prev_top, prev_bot = set(), set()
    turn10s, turn1s = [], []
    borrow = BORROW / PPY                       # 融券利息每期成本
    for m in months:
        g = scored[scored["trade_date"] == m]
        for f in FACTORS + ["composite"]:
            x = g[f].values
            ok = ~np.isnan(x)
            if ok.sum() > 30:
                ic = spearmanr(x[ok], g["fwd_ret"].values[ok]).correlation
                ic_rows[f].append(ic)
                if f == "composite":
                    ic_month.append(m)
        gd = g.dropna(subset=["decile"])
        if gd.empty:
            continue
        dmean = gd.groupby("decile")["fwd_ret"].mean()
        for d in range(1, 11):
            dec_ret[d].append(dmean.get(d, np.nan))
        top = set(gd[gd["decile"] == 10]["ts_code"])
        bot = set(gd[gd["decile"] == 1]["ts_code"])
        t10 = 1 - len(top & prev_top) / max(len(top), 1) if prev_top else 1.0
        t1  = 1 - len(bot & prev_bot) / max(len(bot), 1) if prev_bot else 1.0
        turn10s.append(t10); turn1s.append(t1)
        ls_raw = dmean.get(10, np.nan) - dmean.get(1, np.nan)
        lo_raw = dmean.get(10, np.nan) - gd["fwd_ret"].mean()
        mlist.append(m)
        ls_g.append(ls_raw);  lo_g.append(lo_raw)
        ls_n.append(ls_raw - (t10 + t1) * roundtrip)   # 多空两条腿都有换手成本
        lo_n.append(lo_raw - t10 * roundtrip)          # 多头只有 D10 一条腿（基准视为无成本参照）

        d1 = gd[gd["decile"] == 1]
        shortable = d1[d1["rqye"] > 0]                 # 有融券余额=有券源，才空得了
        cov = len(shortable) / max(len(d1), 1)
        short_cov.append(cov)
        if len(shortable) >= 10:
            lsr_raw = dmean.get(10, np.nan) - shortable["fwd_ret"].mean()
            lsr_g.append(lsr_raw)
            lsr_n.append(lsr_raw - (t10 + t1) * roundtrip - borrow)   # +融券利息
        else:
            lsr_g.append(np.nan); lsr_n.append(np.nan)
        prev_top, prev_bot = top, bot

    mlist = np.array(mlist)
    ls_g, lo_g, ls_n, lo_n = map(np.array, (ls_g, lo_g, ls_n, lo_n))
    lsr_g, lsr_n = map(np.array, (lsr_g, lsr_n))
    ic_month = np.array(ic_month)
    ic_comp = np.array(ic_rows["composite"])

    def _ann(a):
        a = np.asarray([x for x in a if not np.isnan(x)])
        if len(a) == 0:
            return 0, 0, 0, 0
        cum = np.cumprod(1 + a)
        ann = cum[-1] ** (PPY / len(a)) - 1
        sharpe = a.mean() / a.std() * np.sqrt(PPY) if a.std() > 0 else 0
        mdd = ((cum - np.maximum.accumulate(cum)) / np.maximum.accumulate(cum)).min()
        return ann, sharpe, mdd, (a > 0).mean()

    print("\n========== 单因子 IC（月频 Spearman，全样本）==========")
    print(f"{'因子':<14s}{'IC均值':>9s}{'ICIR':>8s}{'t值':>7s}{'月数':>6s}  中文")
    for f in FACTORS + ["composite"]:
        a = np.array(ic_rows[f])
        if len(a) == 0:
            continue
        icir = a.mean() / a.std() if a.std() > 0 else 0
        t = icir * np.sqrt(len(a))
        tag = "★综合分" if f == "composite" else FACTOR_CN.get(f, "")
        print(f"{f:<14s}{a.mean():>+9.4f}{icir:>8.2f}{t:>7.2f}{len(a):>6d}  {tag}")

    print("\n========== 综合分 10 档 月均收益（看单调性，D10 应最高）==========")
    for d in range(1, 11):
        a = np.array([x for x in dec_ret[d] if not np.isnan(x)])
        bar = "█" * max(int((a.mean()*100 + 1) * 3), 0)
        print(f"  D{d:<2d}  {FREQ_CN}均 {a.mean()*100:>+6.2f}%  {bar}")

    def _report(tag, mask_m, mask_ic):
        a_ls_g, a_lo_g = ls_g[mask_m], lo_g[mask_m]
        a_ls_n, a_lo_n = ls_n[mask_m], lo_n[mask_m]
        ic_a = ic_comp[mask_ic]
        icir = ic_a.mean()/ic_a.std() if ic_a.std() > 0 else 0
        print(f"\n----- {tag}（{mask_m.sum()} 个{FREQ_CN}）-----")
        print(f"  综合分 IC {ic_a.mean():+.4f}  ICIR {icir:.2f}")
        for name, gg, nn in [("多空(D10-D1全部·理想)", a_ls_g, a_ls_n),
                             ("可执行多空(空D1有券源部分)", lsr_g[mask_m], lsr_n[mask_m]),
                             ("纯多头超额(D10-市场)", a_lo_g, a_lo_n)]:
            ag, sg, mg, wg = _ann(gg)
            an, sn, mn_, wn = _ann(nn)
            print(f"  {name:<24s} 毛年化 {ag*100:+6.1f}% / 净年化 {an*100:+6.1f}%  "
                  f"净夏普 {sn:.2f}  净回撤 {mn_*100:.1f}%  净{FREQ_CN}胜率 {wn*100:.0f}%")

    cut = args.split
    print(f"\n========== 组合表现（每边成本 {COST*100:.2f}%，样本外切点 {cut}）==========")
    _report("全样本", np.ones(len(mlist), bool), np.ones(len(ic_comp), bool))
    _report(f"建模期 <{cut}", mlist < cut, ic_month < cut)
    _report(f"样本外 ≥{cut}", mlist >= cut, ic_month >= cut)
    print(f"\n  D10 {FREQ_CN}均换手 {np.mean(turn10s)*100:.0f}%  ·  D1 {np.mean(turn1s)*100:.0f}%"
          f"  ·  D1 平均可融券覆盖率 {np.mean(short_cov)*100:.0f}%  ·  融券利息 {BORROW*100:.1f}%/年")

    ann_ls, sh_ls, mdd_ls, win_ls = _ann(ls_n)
    ann_lo, sh_lo, mdd_lo, win_lo = _ann(lo_n)
    _render_html(args.start, months, ic_rows, dec_ret, ls_n, lo_n,
                 (ann_ls, sh_ls, mdd_ls, win_ls), (ann_lo, sh_lo, mdd_lo, win_lo),
                 np.mean(turn10s), COST)
    _export_holdings(scored)


def _export_holdings(scored):
    """导出最新一次调仓的 D10 持仓（就是策略当下会买的一篮子），附名称+因子分，存 CSV 并打印前 30。"""
    last = scored["trade_date"].max()
    hold = scored[(scored["trade_date"] == last) & (scored["decile"] == 10)].copy()
    hold = hold.sort_values("composite", ascending=False).reset_index(drop=True)
    con = duckdb.connect(DUCK_PATH, read_only=True)
    nm = con.execute("SELECT ts_code, name FROM stock_meta").df().set_index("ts_code")["name"].to_dict()
    con.close()
    hold["name"] = hold["ts_code"].map(nm)
    cols = ["ts_code", "name", "composite"] + COMPOSITE
    out = hold[cols].copy()
    out.insert(0, "rank", range(1, len(out) + 1))
    path = os.path.join(os.path.dirname(__file__), "html_output", f"holdings_D10_{last}.csv")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    out.round(3).to_csv(path, index=False, encoding="utf-8-sig")
    print(f"\n========== 最新调仓日 {last} 的 D10 持仓（共 {len(out)} 只，等权买入，下{FREQ_CN}末换仓）==========")
    print(f"  按综合分从高到低，前 30 只（完整名单见 {path}）：")
    print(f"  {'#':>3s} {'代码':<11s}{'名称':<9s}{'综合分':>7s}  " + "".join(f"{FACTOR_CN[c][:4]:>6s}" for c in COMPOSITE))
    for r in out.head(30).itertuples(index=False):
        fz = "".join(f"{getattr(r, c):>+6.2f}" for c in COMPOSITE)
        print(f"  {r.rank:>3d} {r.ts_code:<11s}{(r.name or ''):<9s}{r.composite:>+7.2f}  {fz}")


def _render_html(start, months, ic_rows, dec_ret, ls, lo, ksl, klo, turn, cost):
    """渲染 IC 表 + 分档表 + 多空/多头【扣成本净值】曲线 的单页 HTML。"""
    def cum(series):
        a = [x for x in series if not np.isnan(x)]
        c, out = 1.0, [1.0]
        for x in a:
            c *= (1 + x); out.append(c)
        return out

    def spark(curve, color="#c00"):
        if len(curve) < 2:
            return ""
        w, h, pad = 760, 200, 8
        mn, mx = min(curve), max(curve); rng = (mx - mn) or 1
        pts = " ".join(f"{pad+(w-2*pad)*i/(len(curve)-1):.1f},{pad+(h-2*pad)*(1-(v-mn)/rng):.1f}"
                       for i, v in enumerate(curve))
        base = pad + (h-2*pad)*(1-(1-mn)/rng)
        return (f"<svg width=100% height={h} viewBox='0 0 {w} {h}' preserveAspectRatio=none "
                f"style='background:#fff;border:1px solid #e3e6ea;border-radius:8px'>"
                f"<line x1={pad} x2={w-pad} y1={base:.1f} y2={base:.1f} stroke=#ccc stroke-dasharray='4 4'/>"
                f"<polyline points='{pts}' fill=none stroke='{color}' stroke-width=1.8/></svg>")

    ic_tbl = ""
    for f in FACTORS + ["composite"]:
        a = np.array(ic_rows[f])
        if len(a) == 0:
            continue
        icir = a.mean()/a.std() if a.std() > 0 else 0
        nm = "★ 综合分" if f == "composite" else FACTOR_CN.get(f, f)
        c = "#c00" if a.mean() >= 0 else "#080"
        ic_tbl += (f"<tr><td class=l>{nm}</td><td style='color:{c};font-weight:700'>{a.mean():+.4f}</td>"
                   f"<td>{icir:.2f}</td><td>{icir*np.sqrt(len(a)):.2f}</td><td>{len(a)}</td></tr>")

    dec_tbl = ""
    for d in range(1, 11):
        a = np.array([x for x in dec_ret[d] if not np.isnan(x)])
        mv = a.mean()*100
        w = max(mv*8 + 60, 2)
        dec_tbl += (f"<tr><td class=l>D{d}{'（最高分）' if d==10 else '（最低分）' if d==1 else ''}</td>"
                    f"<td style='color:{'#c00' if mv>=0 else '#080'};font-weight:700'>{mv:+.2f}%</td>"
                    f"<td class=l><div style='background:#f3a;height:12px;width:{w:.0f}px;"
                    f"background:{'#f6a' if mv>=0 else '#8c8'}'></div></td></tr>")

    def kpi(k):
        return (f"<div class=box><div class=lbl>{k[0]}</div>"
                f"<div class=val style='color:{k[2]}'>{k[1]}</div></div>")
    ann_ls, sh_ls, mdd_ls, win_ls = ksl
    ann_lo, sh_lo, mdd_lo, win_lo = klo
    kc = "#c00" if ann_ls >= 0 else "#080"
    lc = "#c00" if ann_lo >= 0 else "#080"
    kpis = "".join(kpi(k) for k in [
        ["多空年化", f"{ann_ls*100:+.1f}%", kc], ["多空夏普", f"{sh_ls:.2f}", kc],
        ["多空最大回撤", f"{mdd_ls*100:.1f}%", "#080"],
        ["多头净年化超额", f"{ann_lo*100:+.1f}%", lc], ["多头净夏普", f"{sh_lo:.2f}", lc],
        ["D10月换手", f"{turn*100:.0f}%", "#222"]])

    html = f"""<!doctype html><html lang=zh><head><meta charset=utf-8><title>多因子选股 月频回测</title><style>
body{{font-family:-apple-system,"Microsoft YaHei",sans-serif;margin:24px auto;max-width:1000px;color:#222;padding:0 16px;background:#f4f6f8}}
h1{{font-size:20px;margin:0 0 6px}}.meta{{color:#666;font-size:13px;margin-bottom:14px}}
.kpi{{display:flex;gap:12px;flex-wrap:wrap;margin:0 0 16px}}
.box{{flex:1;min-width:120px;background:#fff;border:1px solid #e8ebee;border-radius:10px;padding:12px 15px}}
.lbl{{font-size:12px;color:#999;margin-bottom:6px}}.val{{font-size:20px;font-weight:800}}
.sec{{font-size:15px;font-weight:700;margin:20px 0 10px;padding-left:10px;border-left:4px solid #c00}}
table.sum{{border-collapse:collapse;font-size:13px;background:#fff;width:100%}}
table.sum th,table.sum td{{border:1px solid #e3e6ea;padding:6px 12px;text-align:right}}
table.sum th{{background:#f6f8fa;color:#666}}table.sum td.l,table.sum th.l{{text-align:left}}</style></head><body>
<h1>通用截面多因子选股 · 月频回测（多空 + 纯多头）</h1>
<div class=meta>区间 {start}~ · {len(months)} 个{FREQ_CN} · {FREQ_CN}末换仓 · 行业+市值中性化 · close-to-close · 每边成本 {cost*100:.2f}% · 净值已扣成本 · 生成 {datetime.now():%Y-%m-%d %H:%M}</div>
<div class=kpi>{kpis}</div>
<div class=sec>单因子 IC（IC均值=预测力方向与强度，ICIR=稳定性，|t|>2 显著）</div>
<table class=sum><thead><tr><th class=l>因子</th><th>IC均值</th><th>ICIR</th><th>t值</th><th>月数</th></tr></thead><tbody>{ic_tbl}</tbody></table>
<div class=sec>综合分 10 档 月均收益（D1→D10 应单调上升）</div>
<table class=sum><tbody>{dec_tbl}</tbody></table>
<div class=sec>多空组合(D10−D1) 扣成本累计净值</div>{spark(cum(ls))}
<div class=sec>纯多头超额(D10−全市场) 扣成本累计净值</div>{spark(cum(lo), "#08a")}
</body></html>"""
    out = os.path.join(os.path.dirname(__file__), "html_output", f"factor_select_{FREQ}_{start}.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nHTML 已生成：{out}")
    try:
        webbrowser.open("file://" + os.path.abspath(out))
    except Exception:
        pass


if __name__ == "__main__":
    main()
