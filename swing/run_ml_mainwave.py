"""
run_ml_mainwave.py — ML 提升 N/W 突破的主升浪命中率(walk-forward 样本外)

对 N字/W型突破事件,用多维特征预测"突破后60日内最高涨幅≥50%(走出主升浪)",
LightGBM 逐年 walk-forward(训过去、测当年),看能否把候选里的命中率从~16%往上推。
特征:形态类型/突破力度/位置/基底宽/均线乖离/波动/ADX/动量 + 筹码(winner_rate/成本集中度)
       + 资金流(20日大单净额占比) + 估值(PE/PB/市值)。

⚠️ 命中率提升以"样本外 top档命中率 vs 全体命中率(lift)"衡量;lift≈1 即无效,果断判死。
环境：.venv312。用法：python swing/run_ml_mainwave.py --thr 0.09 --n 800
依赖：DuckDB(daily/daily_basic/adj_factor/cyq_perf/moneyflow);lightgbm;复用 run_patterns。
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.expanduser("~/AI/quart/first10"))

import duckdb
import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.metrics import roc_auc_score

from cache_tushare import DUCKDB_PATH
from run_patterns import _detect

THR, MW_GAIN, MW_DAYS = 0.09, 0.50, 60
FEATS = ["ptype", "brk", "pos1y", "basew", "dma20", "dma60", "atrp", "adx", "ret20", "ret60",
         "volr", "winrate", "cyqconc", "mfnet20", "pe", "pb", "lnmv",
         "maxvr60", "dsince", "vdry", "pbdepth",
         "hotrank", "hotbest10", "hotdays10"]


def _adx(h, l, c, n=14):
    up = h.diff(); dn = -l.diff()
    plus = np.where((up > dn) & (up > 0), up, 0.0); minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    pc = c.shift(1); tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / n, adjust=False).mean()
    pdi = 100 * pd.Series(plus, index=h.index).ewm(alpha=1 / n, adjust=False).mean() / atr
    mdi = 100 * pd.Series(minus, index=h.index).ewm(alpha=1 / n, adjust=False).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean(), atr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--thr", type=float, default=0.09)
    ap.add_argument("--n", type=int, default=800)
    args = ap.parse_args()

    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    sel = con.execute("SELECT MAX(trade_date) FROM daily_basic").fetchone()[0]
    liquid = [r[0] for r in con.execute("""SELECT ts_code FROM daily_basic WHERE trade_date=? AND ts_code NOT LIKE '%.BJ'
        ORDER BY circ_mv DESC LIMIT ?""", [sel, args.n]).fetchall()]
    px = con.execute("""SELECT d.ts_code,d.trade_date,d.high*a.adj_factor h,d.low*a.adj_factor l,
        d.close*a.adj_factor c,d.vol v FROM daily d JOIN adj_factor a ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date
        WHERE d.trade_date>=? AND d.ts_code IN (SELECT UNNEST(?))""", ["2021-01-01", liquid]).fetch_df()
    db = con.execute("""SELECT ts_code,trade_date,pe_ttm,pb,circ_mv FROM daily_basic
        WHERE trade_date>=? AND ts_code IN (SELECT UNNEST(?))""", ["2021-01-01", liquid]).fetch_df()
    cyq = con.execute("""SELECT ts_code,trade_date,winner_rate,cost_15pct,cost_50pct,cost_85pct FROM cyq_perf
        WHERE trade_date>=? AND ts_code IN (SELECT UNNEST(?))""", ["2021-01-01", liquid]).fetch_df()
    mf = con.execute("""SELECT ts_code,trade_date,
        buy_lg_amount+buy_elg_amount-sell_lg_amount-sell_elg_amount AS net_lg,
        buy_sm_amount+buy_md_amount+buy_lg_amount+buy_elg_amount+sell_sm_amount+sell_md_amount+sell_lg_amount+sell_elg_amount AS tot
        FROM moneyflow WHERE trade_date>=? AND ts_code IN (SELECT UNNEST(?))""", ["2021-01-01", liquid]).fetch_df()
    hot = con.execute("SELECT ts_code, trade_date td, rank FROM ths_hot WHERE data_type='热股'").fetch_df()
    con.close()
    hot["rank"] = pd.to_numeric(hot["rank"], errors="coerce")
    hotmap = {(r.ts_code, r.td): r.rank for r in hot.itertuples()}

    for d in (px, db, cyq, mf):
        d["trade_date"] = pd.to_datetime(d["trade_date"])
    db = db.set_index(["ts_code", "trade_date"])
    cyq["cyqconc"] = (cyq["cost_85pct"] - cyq["cost_15pct"]) / cyq["cost_50pct"]
    cyq = cyq.set_index(["ts_code", "trade_date"])
    mf = mf.set_index(["ts_code", "trade_date"])

    rows = []
    for ts, g in px.groupby("ts_code"):
        g = g.sort_values("trade_date").reset_index(drop=True)
        c, h, l, v = g["c"], g["h"], g["l"], g["v"]
        cc = c.to_numpy(); gd = g["trade_date"].to_numpy()
        if len(cc) < 180:
            continue
        ma20, ma60 = c.rolling(20).mean(), c.rolling(60).mean()
        adx, atr = _adx(h, l, c)
        pos1y = (c - c.rolling(250).min()) / (c.rolling(250).max() - c.rolling(250).min())
        basew = (h.rolling(40).max().shift(1) - l.rolling(40).min().shift(1)) / l.rolling(40).min().shift(1)
        vma = v.rolling(20).mean()
        maxvr60 = (v / vma).rolling(60).max().shift(1)
        vv = v.to_numpy(); hh = h.to_numpy(); ll = l.to_numpy()
        hr = pd.Series([hotmap.get((ts, pd.Timestamp(x).strftime("%Y%m%d")), np.nan) for x in gd], index=c.index)
        hotbest10 = hr.rolling(10, min_periods=1).min()
        hotdays10 = hr.notna().rolling(10, min_periods=1).sum()
        ret20 = c / c.shift(20) - 1; ret60 = c / c.shift(60) - 1
        try:
            mfg = mf.loc[ts].reindex(g["trade_date"])
            mf_net20 = (mfg["net_lg"].rolling(20).sum() / mfg["tot"].rolling(20).sum().abs()).values
        except KeyError:
            mf_net20 = np.full(len(cc), np.nan)
        evs = _detect(cc, args.thr, 30)
        for typ, bo, pvs in evs:
            d = pd.Timestamp(gd[bo])
            if d.year < 2022 or bo + MW_DAYS >= len(cc):
                continue
            label = int(cc[bo + 1:bo + MW_DAYS + 1].max() / cc[bo] - 1 >= MW_GAIN)
            lo_i = max(0, bo - 60)
            if bo - lo_i >= 5:
                sp = lo_i + int(np.argmax(vv[lo_i:bo]))
                spv = vv[sp]; sph = hh[sp]
                dsince = bo - sp
                vdry = vv[sp + 1:bo + 1].mean() / spv if spv > 0 and bo > sp else np.nan
                pbdepth = (sph - ll[sp + 1:bo + 1].min()) / sph if bo > sp and sph > 0 else np.nan
            else:
                dsince = vdry = pbdepth = np.nan
            try:
                dbr = db.loc[(ts, d)]; cyr = cyq.loc[(ts, d)]
            except KeyError:
                dbr = cyr = None
            rows.append({
                "year": d.year, "label": label,
                "ptype": 0 if typ == "N字型" else 1,
                "brk": cc[bo] / cc[pvs[1] if typ == "N字型" else pvs[1]] - 1,
                "pos1y": pos1y.iloc[bo], "basew": basew.iloc[bo],
                "dma20": cc[bo] / ma20.iloc[bo] - 1, "dma60": cc[bo] / ma60.iloc[bo] - 1,
                "atrp": atr.iloc[bo] / cc[bo], "adx": adx.iloc[bo],
                "ret20": ret20.iloc[bo], "ret60": ret60.iloc[bo],
                "volr": v.iloc[bo] / vma.iloc[bo] if vma.iloc[bo] else np.nan,
                "winrate": cyr["winner_rate"] if cyr is not None else np.nan,
                "cyqconc": cyr["cyqconc"] if cyr is not None else np.nan,
                "mfnet20": mf_net20[bo],
                "pe": dbr["pe_ttm"] if dbr is not None else np.nan,
                "pb": dbr["pb"] if dbr is not None else np.nan,
                "lnmv": np.log(dbr["circ_mv"]) if dbr is not None and dbr["circ_mv"] > 0 else np.nan,
                "maxvr60": maxvr60.iloc[bo], "dsince": dsince, "vdry": vdry, "pbdepth": pbdepth,
                "hotrank": hr.iloc[bo], "hotbest10": hotbest10.iloc[bo], "hotdays10": hotdays10.iloc[bo],
            })
    df = pd.DataFrame(rows)
    print(f"事件总数 {len(df)} | 整体命中率 {df['label'].mean()*100:.0f}%")

    preds, labs = [], []
    imp = np.zeros(len(FEATS))
    for Y in [2023, 2024, 2025, 2026]:
        tr = df[df["year"] < Y]; te = df[df["year"] == Y]
        if len(te) < 30 or tr["label"].sum() < 20:
            continue
        m = lgb.LGBMClassifier(n_estimators=200, learning_rate=0.03, num_leaves=31,
                               min_child_samples=40, subsample=0.8, colsample_bytree=0.8, verbosity=-1)
        m.fit(tr[FEATS], tr["label"])
        p = m.predict_proba(te[FEATS])[:, 1]
        preds.extend(p); labs.extend(te["label"].values); imp += m.feature_importances_
        top = te.assign(p=p).sort_values("p", ascending=False)
        q = max(1, len(top) // 4)
        print(f"  测{Y}: 事件{len(te)} 命中率{te['label'].mean()*100:.0f}% | top25%命中率{top['label'].head(q).mean()*100:.0f}% | AUC {roc_auc_score(te['label'],p):.3f}")

    preds, labs = np.array(preds), np.array(labs)
    base = labs.mean()
    order = np.argsort(-preds); q = len(preds) // 4
    topq = labs[order[:q]].mean()
    print(f"\n样本外汇总: AUC {roc_auc_score(labs,preds):.3f} | 全体命中率 {base*100:.0f}% | "
          f"ML top25%命中率 {topq*100:.0f}% (lift ×{topq/base:.2f})")
    print("\n特征重要度(全部,降序):")
    for rank, i in enumerate(np.argsort(-imp), 1):
        print(f"  {rank:>2}. {FEATS[i]:<10}{imp[i]:.0f}")


if __name__ == "__main__":
    main()
