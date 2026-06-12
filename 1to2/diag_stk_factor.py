"""首板→2板：stk_factor_pro 技术面因子的预测力诊断（纯探索，不训练）。

目的：在不改模型的前提下，检验 tushare stk_factor_pro 里若干技术指标（后复权 _hfq）
      对「首板个股次日晋级 2板」是否有区分度，决定哪些值得加进特征。

流程：
  1) 取 1进2 信号(首板) + label（复用 ml_train_1to2_v1._get_signals + ml_features_2lb_v3.build_feature_matrix）
  2) 按信号日从 stk_factor_pro 拉候选因子（限定字段、翻页、限速、本地缓存到 cache/）
  3) 按 (ts_code,trade_date) 关联，算每个因子的 IC(Spearman) / 晋级-未晋级组均值 / 单因子 AUC
  4) 排名打印 + 存 CSV

用法：
  python 1to2/diag_stk_factor.py                       # 全样本
  python 1to2/diag_stk_factor.py --start 20230101      # 仅 2023 起（更快）
  python 1to2/diag_stk_factor.py --refetch             # 忽略缓存重新拉取

依赖：first10/ml_features_2lb_v3、quart 根 db_loader（TUSHARE_TOKEN）；需 stk_factor_pro 接口权限。
产出：1to2/diag_stk_factor_{start}_{end}.csv
"""
import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "first10"))
sys.stdout.reconfigure(encoding="utf-8")

import tushare as ts
from scipy.stats import spearmanr
from sklearn.metrics import roc_auc_score
from db_loader import _ENV

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

CANDIDATE_FACTORS = [
    "obv_hfq", "vr_hfq", "emv_hfq", "mfi_hfq",
]

_PAGE = 6000
_MAX_PER_MIN = 300


class _Limiter:
    """简单滑动窗口限速。"""

    def __init__(self, n):
        self.n = n
        self.calls = []

    def acquire(self):
        while True:
            now = time.time()
            self.calls = [t for t in self.calls if t > now - 60]
            if len(self.calls) < self.n:
                self.calls.append(now)
                return
            time.sleep(max(0.05, 60 - (now - self.calls[0])))


def _fetch_one(pro, limiter, trade_date, fields):
    """拉单个交易日全市场的候选因子，翻页取尽，失败重试。"""
    frames, offset = [], 0
    for _ in range(20):
        for attempt in range(4):
            limiter.acquire()
            try:
                df = pro.stk_factor_pro(trade_date=trade_date, fields=fields,
                                        offset=offset, limit=_PAGE)
                break
            except Exception as e:
                if "频率" in str(e) or "每分钟" in str(e):
                    time.sleep(12 * (attempt + 1)); continue
                return pd.DataFrame()
        else:
            return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if df is None or df.empty:
            break
        frames.append(df)
        if len(df) < _PAGE:
            break
        offset += len(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _load_signals_with_label(start, end):
    """取 1进2 信号 + label（次日是否涨停=晋级2板）。"""
    from ml_train_1to2_v1 import _get_signals
    from ml_features_2lb_v3 import build_feature_matrix
    sig = _get_signals()
    sig = sig[(sig["trade_date"] >= start) & (sig["trade_date"] <= end)].reset_index(drop=True)
    feat = build_feature_matrix(sig[["ts_code", "trade_date"]], require_label=True)
    return feat[["ts_code", "trade_date", "label"]].dropna(subset=["label"]).copy()


def _fetch_factors(dates, refetch):
    """按信号日拉候选因子，结果缓存到 cache/。"""
    fields = "ts_code,trade_date," + ",".join(CANDIDATE_FACTORS)
    cache = os.path.join(CACHE_DIR, f"stk_factor_diag_{dates[0]}_{dates[-1]}_{len(dates)}.pkl")
    if os.path.exists(cache) and not refetch:
        print(f"因子缓存命中: {cache}", flush=True)
        return pd.read_pickle(cache)

    pro = ts.pro_api(_ENV["TUSHARE_TOKEN"])
    limiter = _Limiter(_MAX_PER_MIN)
    print(f"从 stk_factor_pro 拉 {len(dates)} 个交易日的因子（限速 {_MAX_PER_MIN}/min）...", flush=True)
    out, done, t0 = [], 0, time.time()
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(_fetch_one, pro, limiter, d, fields): d for d in dates}
        for f in as_completed(futs):
            df = f.result()
            if not df.empty:
                out.append(df)
            done += 1
            if done % 100 == 0:
                print(f"  [{done}/{len(dates)}] {time.time()-t0:.0f}s", flush=True)
    res = pd.concat(out, ignore_index=True) if out else pd.DataFrame()
    res.to_pickle(cache)
    print(f"  已拉 {len(res)} 行，缓存 -> {cache}", flush=True)
    return res


def _diagnose(merged):
    """逐因子算 IC / 组均值 / 单因子 AUC。"""
    y = merged["label"].astype(int).values
    base = y.mean()
    rows = []
    for c in CANDIDATE_FACTORS:
        x = pd.to_numeric(merged[c], errors="coerce")
        m = x.notna().values & np.isfinite(x.values)
        if m.sum() < 500:
            continue
        xv, yv = x.values[m], y[m]
        ic, _ = spearmanr(xv, yv)
        try:
            auc = roc_auc_score(yv, xv)
        except Exception:
            auc = np.nan
        mu1, mu0 = xv[yv == 1].mean(), xv[yv == 0].mean()
        rows.append({
            "factor": c, "n": int(m.sum()),
            "IC": round(ic, 4),
            "AUC": round(auc, 4),
            "AUC_abs": round(abs(auc - 0.5) + 0.5, 4),
            "mean_晋级": round(mu1, 4),
            "mean_未晋级": round(mu0, 4),
            "差异%": round((mu1 - mu0) / (abs(mu0) + 1e-9) * 100, 1),
        })
    df = pd.DataFrame(rows).sort_values("AUC_abs", ascending=False).reset_index(drop=True)
    return df, base


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20200101")
    ap.add_argument("--end", default="20991231")
    ap.add_argument("--refetch", action="store_true")
    args = ap.parse_args()

    print("=== 取 1进2 信号 + label ===", flush=True)
    sig = _load_signals_with_label(args.start, args.end)
    end = sig["trade_date"].max()
    print(f"信号 {len(sig)} 条（{sig['trade_date'].min()} ~ {end}），晋级率 {sig['label'].mean():.2%}", flush=True)

    dates = sorted(sig["trade_date"].unique().tolist())
    fac = _fetch_factors(dates, args.refetch)
    if fac.empty:
        print("未取到因子数据（检查 stk_factor_pro 权限）。"); return

    merged = sig.merge(fac, on=["ts_code", "trade_date"], how="inner")
    print(f"\n关联后样本：{len(merged)} 条（覆盖率 {len(merged)/len(sig):.1%}）", flush=True)

    diag, base = _diagnose(merged)
    print(f"\n基础晋级率 = {base:.2%}\n")
    print("=== 单因子区分力排名（按 |AUC-0.5| 降序）===")
    print(diag.to_string(index=False))

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       f"diag_stk_factor_{args.start}_{end}.csv")
    diag.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\nCSV -> {out}")
    print("\n解读：IC 越大(正/负)区分力越强；AUC 偏离 0.5 越多越有用；"
          "差异% 是晋级组相对未晋级组的因子均值偏移。建议挑 |IC|≳0.03 且方向符合直觉的 3~6 个加入特征。")


if __name__ == "__main__":
    main()
