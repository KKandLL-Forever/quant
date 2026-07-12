"""中国版宏观 regime 择时模型(复现 arXiv:2108.05801 方法论,换成 tushare 中国宏观数据)。

方法(照论文):月度宏观序列 → 标准化 → PCA(留够90%方差) → k-means 分2个regime →
用上证月度收益标定哪簇是「危机」 → 训 LDA 分类器 → 预测。训练 ≤2022-12,测试 2023-01 至今。
产出:测试段每月 regime + 切换点位,并存本地 HTML(regime阴影叠上证,按项目规则不托管)。

用法:python research/cn_regime.py
数据:tushare 宏观(cn_cpi/cn_ppi/cn_pmi/cn_m/sf_month/shibor)+ 本地 DuckDB 上证指数。需 sklearn。

结论(见运行输出):这是「照葫芦画瓢」的中国宏观regime探针,重在看它把2023+哪些月标成危机、
与市场下跌是否对得上;宏观月频信号滞后、样本少(月度~10年),当研究参考,勿直接实盘。
"""
import os
import numpy as np
import pandas as pd
import duckdb
import tushare as ts
import cache_tushare as ct
from sklearn.preprocessing import StandardScaler
from sklearn.decomposition import PCA
from sklearn.cluster import KMeans
from sklearn.discriminant_analysis import LinearDiscriminantAnalysis
from cache_tushare import DUCKDB_PATH

TRAIN_END = "202212"
_pro = ts.pro_api((os.environ.get("TUSHARE_TOKEN") or ct._ENV.get("TUSHARE_TOKEN", "")).strip())


def macro_panel() -> pd.DataFrame:
    """抓 tushare 宏观拼成月度面板(index=YYYYMM 字符串),列为各宏观特征。"""
    cpi = _pro.cn_cpi()[["month", "nt_yoy", "nt_mom"]].rename(columns={"nt_yoy": "cpi_yoy", "nt_mom": "cpi_mom"})
    ppi = _pro.cn_ppi()[["month", "ppi_yoy"]]
    m = _pro.cn_m()[["month", "m1_yoy", "m2_yoy"]]
    sf = _pro.sf_month()[["month", "inc_month"]].rename(columns={"inc_month": "sf_inc"})
    pmi = _pro.cn_pmi()[["MONTH", "PMI010000", "PMI020100"]].rename(
        columns={"MONTH": "month", "PMI010000": "pmi_mfg", "PMI020100": "pmi_nmfg"})

    sh = _pro.shibor(start_date="20100101", end_date="20260731")[["date", "1y"]]
    sh["month"] = sh["date"].str[:6]
    sh = sh.groupby("month", as_index=False)["1y"].mean().rename(columns={"1y": "shibor1y"})

    df = cpi
    for x in (ppi, m, sf, pmi, sh):
        df = df.merge(x, on="month", how="outer")
    df = df.sort_values("month").set_index("month")
    df["m1_m2"] = df["m1_yoy"] - df["m2_yoy"]
    df["sf_yoy"] = df["sf_inc"].pct_change(12) * 100
    df["shibor_chg"] = df["shibor1y"].diff()
    df = df.drop(columns=["sf_inc"])
    df = df.apply(pd.to_numeric, errors="coerce")
    df = df[df.index >= "201001"].dropna()
    return df


def index_monthly() -> pd.Series:
    """上证综指月度收益%(index=YYYYMM),用于标定危机簇 + 画图。"""
    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    d = con.execute("SELECT trade_date, close FROM index_daily WHERE ts_code='000001.SH' ORDER BY trade_date").df()
    con.close()
    d["month"] = d["trade_date"].astype(str).str[:7].str.replace("-", "")
    mc = d.groupby("month")["close"].last()
    return mc.pct_change() * 100


def main():
    X = macro_panel()
    ret = index_monthly()
    feats = X.columns.tolist()
    print(f"宏观面板 {X.shape[0]} 个月({X.index.min()}~{X.index.max()}),{len(feats)} 特征: {feats}")

    tr = X[X.index <= TRAIN_END]
    te = X[X.index > TRAIN_END]
    sc = StandardScaler().fit(tr)
    pca = PCA(n_components=0.90, svd_solver="full").fit(sc.transform(tr))
    Ztr, Zte = pca.transform(sc.transform(tr)), pca.transform(sc.transform(te))
    print(f"PCA: {pca.n_components_} 个主成分留住 {pca.explained_variance_ratio_.sum()*100:.0f}% 方差")

    km = KMeans(n_clusters=2, n_init=50, random_state=0).fit(Ztr)
    lab_tr = km.labels_
    r_tr = ret.reindex(tr.index).to_numpy()
    avg0 = np.nanmean(r_tr[lab_tr == 0]); avg1 = np.nanmean(r_tr[lab_tr == 1])
    crisis = 0 if avg0 < avg1 else 1
    print(f"训练段:簇0 月均上证 {avg0:+.2f}% / 簇1 {avg1:+.2f}% → 危机簇=簇{crisis}")

    y_tr = (lab_tr == crisis).astype(int)   # 1=危机
    clf = LinearDiscriminantAnalysis().fit(Ztr, y_tr)
    pred_te = clf.predict(Zte)

    out = pd.DataFrame({"regime": np.where(pred_te == 1, "危机", "非危机"),
                        "上证当月%": ret.reindex(te.index).round(1).values}, index=te.index)
    print(f"\n=== 测试段 2023-01 至今:每月 regime 与上证当月涨跌 ===")
    print(out.to_string())

    sw = out[out["regime"] != out["regime"].shift(1)]
    print(f"\n切换点位(共 {len(sw)} 次):")
    for mo, row in sw.iterrows():
        print(f"  {mo[:4]}-{mo[4:]}  → {row['regime']}")

    crisis_m = out[out["regime"] == "危机"]
    print(f"\n测试段判为危机的月份 {len(crisis_m)}/{len(out)},其上证月均 {crisis_m['上证当月%'].mean():+.2f}% "
          f"vs 非危机 {out[out['regime']=='非危机']['上证当月%'].mean():+.2f}%")

    out.to_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "cn_regime_test.csv"), encoding="utf-8-sig")


if __name__ == "__main__":
    main()
