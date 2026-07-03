"""
BOLL缩口扩张+MACD金叉 信号的 ML 排序器(ML主升浪池)。

思路:基础信号已在大盘池有正期望,但小资金只能拿少数仓 → 用 LightGBM 给每个信号打分,
少仓时只取分高的 top-K。目标标签=T+1入场的前瞻收益(15日为主、10日对照);市况当特征让模型自学。
评估(过 STANDARD_MODEL_WORKFLOW):walk-forward 分年(带 embargo)→ OOS rank-IC + 预测分位单调 + top-K 选股增益。

用法:python boll_narrow_exit/ml_rank.py [--label 15 --pool ml]
"""
import os
_ROOT = os.path.dirname(os.path.abspath(__file__))
while _ROOT != "/" and not os.path.exists(os.path.join(_ROOT, "cache_tushare.py")):
    _ROOT = os.path.dirname(_ROOT)
import sys
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import argparse
import numpy as np
import pandas as pd
import duckdb
import lightgbm as lgb
from scipy.stats import spearmanr
import cache_tushare as ct
import boll_expand_macd as bm

FEATS = ["vol_ratio", "atr_pct", "bw", "bw_pct", "hist_n", "dist_mid", "dist_up",
         "mom20", "mom60", "ret5", "days_since", "is_rep30", "ln_mv", "turnover",
         "mkt_up", "mkt_bad", "hs300_mom20"]


def make_dataset(pool, start="2021-01-01"):
    """构建信号级特征+标签数据集(T+1入场的10/15日前瞻收益;特征全 PIT)。"""
    codes = {"ml": bm.members_ml, "csi2000": bm.members_2000, "csi1000": bm.members_1000}[pool]()
    df = bm.load(codes, start)
    con = duckdb.connect(ct.DUCKDB_PATH, read_only=True)
    b = con.execute("""SELECT ts_code, trade_date td, circ_mv, turnover_rate FROM daily_basic
        WHERE ts_code IN (SELECT UNNEST(?)) AND trade_date>=?""", [list(codes), start]).fetch_df()
    con.close()
    b["td"] = pd.to_datetime(b["td"])
    df["td"] = pd.to_datetime(df["td"])
    df = df.merge(b, on=["ts_code", "td"], how="left")

    import tushare as ts
    pro = ts.pro_api(ct._get_token())
    hx = pro.index_daily(ts_code="000300.SH", start_date="20200101",
                         end_date=pd.Timestamp.today().strftime("%Y%m%d"), fields="trade_date,close,pct_chg")
    hx["date"] = pd.to_datetime(hx["trade_date"])
    hx = hx.sort_values("date").reset_index(drop=True)
    hc, h30, h60 = hx["close"], hx["close"].rolling(30).mean(), hx["close"].rolling(60).mean()
    mkt = pd.DataFrame({"date": hx["date"], "mkt_up": (hx["pct_chg"] > 0).astype(float),
                        "mkt_bad": (((hc <= h30) | (h30 <= h30.shift(5))) & ((hc <= h60) | (h60 <= h60.shift(5)))).astype(float),
                        "hs300_mom20": hc / hc.shift(20) - 1})

    rows = []
    for ts, g in df.groupby("ts_code", sort=False):
        g = g.reset_index(drop=True)
        if len(g) < 140:
            continue
        c = g["adjc"]
        bw = (g["bu"] - g["bl"]) / g["bm"]
        narrow = bw <= bw.rolling(120, min_periods=60).quantile(0.25)
        widen = bw > bw.shift(1)
        d = g["dif"] - g["dea"]
        crossed = (d > 0) & pd.concat([d.shift(k) <= 0 for k in range(1, 4)], axis=1).any(axis=1)
        up = c > g["bm"]
        sig = narrow.shift(1, fill_value=False) & widen & crossed & up
        sd = g["td"][sig]
        days_since = sd.diff().dt.days
        f = pd.DataFrame({
            "ts_code": ts, "date": g["td"],
            "vol_ratio": g["vol"] / g["vol"].rolling(20, min_periods=10).mean(),
            "atr_pct": g["atr"] / c, "bw": bw,
            "bw_pct": bw.rolling(120, min_periods=60).rank(pct=True),
            "hist_n": d / c, "dist_mid": c / g["bm"] - 1, "dist_up": c / g["bu"] - 1,
            "mom20": c / c.shift(20) - 1, "mom60": c / c.shift(60) - 1, "ret5": c / c.shift(5) - 1,
            "ln_mv": np.log(g["circ_mv"].clip(lower=1)), "turnover": g["turnover_rate"],
            "y10": c.shift(-11) / c.shift(-1) - 1, "y15": c.shift(-16) / c.shift(-1) - 1,
        })[sig].copy()
        f["days_since"] = days_since.reindex(f.index)
        f["is_rep30"] = (f["days_since"] <= 30).astype(float)
        rows.append(f)
    data = pd.concat(rows, ignore_index=True).merge(mkt, on="date", how="left")
    return data.dropna(subset=["y15", "y10"] + FEATS).reset_index(drop=True)


def walk_forward(data, label, test_years=(2023, 2024, 2025, 2026)):
    """按年 walk-forward:训练=测试年前(留15日embargo),预测该年。返回带 oos_pred 的数据。"""
    data = data.sort_values("date").reset_index(drop=True)
    data["oos_pred"] = np.nan
    for y in test_years:
        te = (data["date"] >= f"{y}-01-01") & (data["date"] <= f"{y}-12-31")
        tr = data["date"] < (pd.Timestamp(f"{y}-01-01") - pd.Timedelta(days=25))
        if tr.sum() < 500 or te.sum() == 0:
            continue
        m = lgb.LGBMRegressor(n_estimators=300, learning_rate=0.03, num_leaves=31,
                              subsample=0.8, colsample_bytree=0.8, min_child_samples=50, verbose=-1)
        m.fit(data.loc[tr, FEATS], data.loc[tr, label])
        data.loc[te, "oos_pred"] = m.predict(data.loc[te, FEATS])
    return data


def evaluate(data, label):
    """打印 OOS rank-IC(逐年+pooled)、预测分位的实际收益单调、top-K vs 全体。"""
    oos = data.dropna(subset=["oos_pred"])
    print(f"\n=== 标签 {label}  OOS 样本 {len(oos)} ===")
    ics = []
    for y, g in oos.groupby(oos["date"].dt.year):
        ic = spearmanr(g["oos_pred"], g[label]).correlation
        ics.append(ic)
        print(f"  {y}: rank-IC {ic:+.3f}  (n={len(g)}  实际均值{g[label].mean()*100:+.2f}%)")
    pooled = spearmanr(oos["oos_pred"], oos[label]).correlation
    print(f"  逐年均值 {np.nanmean(ics):+.3f}   pooled {pooled:+.3f}")
    q = pd.qcut(oos["oos_pred"], 5, labels=["Q1低分", "Q2", "Q3", "Q4", "Q5高分"])
    print("  预测分位 → 实际收益:")
    for i, r in oos.groupby(q, observed=True)[label].agg(["mean", "median", "count"]).iterrows():
        print(f"    {i}: 均值 {r['mean']*100:+.2f}%  中位 {r['median']*100:+.2f}%  胜率 {(oos[q==i][label]>0).mean()*100:.0f}%  (n={int(r['count'])})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default="ml", choices=["ml", "csi2000", "csi1000"])
    ap.add_argument("--start", default="2021-01-01")
    args = ap.parse_args()
    print("构建数据集...")
    data = make_dataset(args.pool, args.start)
    print(f"信号样本 {len(data)}  特征 {len(FEATS)}")
    for label in ("y15", "y10"):
        d = walk_forward(data.copy(), label)
        evaluate(d, label)


if __name__ == "__main__":
    main()
