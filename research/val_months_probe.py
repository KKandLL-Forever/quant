"""swing 主升模型 VAL_MONTHS(验证窗口长度)敏感性探针——只评估、不改线上默认。

背景:部署拟合(run_ml_signals_2026.py:696)里 val = start 往前 VAL_MONTHS 个月,
训练集因此止于 start 前约 (VAL_MONTHS+EMBARGO) 时间。VAL_MONTHS=12 会砍掉近一年训练数据、
让模型偏旧。本探针在多个历史 as-of 起始日上,对 VAL_MONTHS∈{12,3,2} 各训一个部署模型,
在其后 3 个月(有标签)上比 OOS test-AUC / top20% lift / 训练样本量 / 训练数据新鲜度 / 过拟合gap,
回答"缩短验证窗口值不值"。

用法:python research/val_months_probe.py   (需 .venv312 有 lightgbm;复用 swing/.evcache 面板)
产出:stdout 打印对比表。结论写在运行输出,不落库、不改任何线上参数。
"""
import os
import sys
import glob
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "swing"))
import run_ml_signals_2026 as R

AS_OF = ["2024-01-01", "2024-07-01", "2025-01-01", "2025-07-01", "2026-01-01"]
VAL_LIST = [12, 3, 2]
TEST_DAYS = 90
TIER = 20
SEED = 42


def _panel() -> pd.DataFrame:
    """载入最新 evcache 事件面板并按 long 模式(k=0.09)重算 label。"""
    R.MW_HURDLE_K = 0.09
    pkls = sorted(glob.glob(os.path.join(os.path.dirname(R.__file__), ".evcache", "*.pkl")))
    if not pkls:
        raise SystemExit("无 evcache 面板,请先在前端/命令行跑一次训练生成缓存")
    df = pd.read_pickle(pkls[-1])
    print(f"面板:{os.path.basename(pkls[-1])}  {len(df)} 行")
    df = R._label_by_mode(df)
    df["date"] = pd.to_datetime(df["date"])
    return df[df["label"] >= 0].copy()


def _one(lab: pd.DataFrame, start: pd.Timestamp, vm: int):
    """单个 (as-of, VAL_MONTHS) 的部署拟合 + 其后 TEST_DAYS 标签段的 OOS 指标。"""
    val_cut = start - pd.DateOffset(months=vm)
    trf = lab[lab["date"] < val_cut - pd.Timedelta(days=R.EMBARGO_DAYS)]
    vaf = lab[(lab["date"] >= val_cut) & (lab["date"] < start)]
    tef = lab[(lab["date"] >= start) & (lab["date"] < start + pd.Timedelta(days=TEST_DAYS))]
    if len(trf) < 500 or len(tef) < 50:
        return None
    m, bi, tr_auc, va_auc = R._fit_lgb(trf, vaf, SEED)
    sc = m.predict_proba(tef[R.FEATS])[:, 1]
    te_auc = R.roc_auc_score(tef["label"], sc)
    base = tef["label"].mean()
    kf = max(1, int(len(tef) * TIER / 100))
    lift = tef.assign(s=sc).nlargest(kf, "s")["label"].mean() / base if base > 0 else np.nan
    stale = (start - trf["date"].max()).days
    return {"n_train": len(trf), "stale_days": stale, "tr_auc": tr_auc, "va_auc": va_auc,
            "gap": None if tr_auc is None else tr_auc - te_auc, "te_auc": te_auc, "lift": lift, "n_test": len(tef)}


def main():
    lab = _panel()
    print(f"标签样本 {len(lab)} 行,区间 {lab['date'].min().date()} ~ {lab['date'].max().date()}\n")
    agg = {vm: [] for vm in VAL_LIST}
    for start_s in AS_OF:
        start = pd.Timestamp(start_s)
        print(f"── as-of {start_s}(测试其后{TEST_DAYS}天)──")
        print(f"  {'VAL_M':>5} {'n_train':>8} {'train止于前':>10} {'trAUC':>6} {'vaAUC':>6} {'teAUC':>6} {'gap':>7} {'lift20':>7}")
        for vm in VAL_LIST:
            r = _one(lab, start, vm)
            if r is None:
                print(f"  {vm:>5}  样本不足,跳过"); continue
            agg[vm].append(r)
            fa = lambda x, n=3: "  n/a" if x is None else f"{x:.{n}f}"
            print(f"  {vm:>5} {r['n_train']:>8} {r['stale_days']:>8}天 {fa(r['tr_auc'],3):>6} {fa(r['va_auc'],3):>6} "
                  f"{r['te_auc']:.3f} {fa(r['gap'],3):>7} {r['lift']:>6.2f}x")
        print()

    print("=== 跨 as-of 均值(缩短验证窗口的净效果)===")
    print(f"  {'VAL_M':>5} {'n_train均':>9} {'train滞后均':>11} {'teAUC均':>8} {'lift20均':>8} {'gap均':>7} {'折数':>4}")
    for vm in VAL_LIST:
        xs = agg[vm]
        if not xs:
            print(f"  {vm:>5}  无有效折"); continue
        mn = np.mean([x["n_train"] for x in xs]); ms = np.mean([x["stale_days"] for x in xs])
        mt = np.mean([x["te_auc"] for x in xs]); ml = np.nanmean([x["lift"] for x in xs])
        mg = np.nanmean([x["gap"] for x in xs if x["gap"] is not None])
        print(f"  {vm:>5} {mn:>9.0f} {ms:>9.0f}天 {mt:>8.4f} {ml:>7.2f}x {mg:>+7.4f} {len(xs):>4}")
    print("\n读法:teAUC均/lift20均 越高越好,gap均 越小越好,n_train均 越大、train滞后均 越小=模型越新鲜。"
          "\n若缩短 VAL_MONTHS 后 teAUC/lift 不降甚至升、且 n_train 明显增、滞后减小 → 值得把线上默认调短。")


if __name__ == "__main__":
    main()
