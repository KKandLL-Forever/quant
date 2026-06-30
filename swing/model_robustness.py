"""model_robustness.py — Phase4 过拟合检验:PBO(CSCV) + Deflated/Probabilistic Sharpe。

回答"我们 A/B 了这么多配置,选出来的最优是不是过拟合/运气"。
- PBO(Bailey & López de Prado 2014, CSCV):配置×月度收益矩阵,组合对称交叉验证,
  统计"样本内最优配置在样本外掉到中位以下"的概率。PBO>0.5 = 选优本质是过拟合。
- Deflated Sharpe(DSR):对最优配置的 Sharpe,扣除"试了 N 个配置"的多重检验运气后是否仍显著。

配置网格 = 出场口径{唐奇安/波段/缠论M3} × score分位cut{top100%/50%/25%};时间切片 = 月。
每格表现 = 当月入场信号在该配置下的平均交易收益。纯本地、依赖现有信号 JSON。

环境：.venv312。用法：
  python swing/run_ml_signals_2026.py --mode long --tier 30 --start 20230101 --json /tmp/rb.json
  python swing/model_robustness.py /tmp/rb.json
"""
import json
import sys
from itertools import combinations

import numpy as np
import pandas as pd
from scipy.stats import norm


def _config_month_matrix(sig):
    """构造 配置×月 的平均收益矩阵 M[config, month] 及配置名。"""
    df = pd.DataFrame(sig)
    df["m"] = df["date"].str[:7]
    months = sorted(df["m"].unique())
    exits = [("唐奇安", "donret"), ("波段", "swret"), ("缠论M3", "czret")]
    cuts = [("top100", 1.0), ("top50", 0.5), ("top25", 0.25)]
    names, rows = [], []
    for enm, col in exits:
        for cnm, frac in cuts:
            thr = df["score"].quantile(1 - frac)
            sel = df[df["score"] >= thr]
            series = sel.groupby("m")[col].mean().reindex(months)
            names.append(f"{enm}/{cnm}")
            rows.append(series.values)
    M = pd.DataFrame(rows, index=names, columns=months).astype(float)
    return M.fillna(0.0)  # 当月无该配置交易→记0(中性)


def pbo_cscv(M, S=10):
    """CSCV 计算 PBO。M=配置×切片收益矩阵。把切片均分 S 组,取一半作IS另一半OOS。"""
    cols = list(M.columns)
    S = min(S, len(cols) - (len(cols) % 2))
    if S < 4:
        return None, 0
    groups = np.array_split(np.arange(len(cols)), S)
    logits = []
    for IS_idx in combinations(range(S), S // 2):
        is_cols = np.concatenate([groups[i] for i in IS_idx])
        oos_cols = np.concatenate([groups[i] for i in range(S) if i not in IS_idx])
        is_perf = M.iloc[:, is_cols].mean(axis=1)
        oos_perf = M.iloc[:, oos_cols].mean(axis=1)
        best = is_perf.values.argmax()
        # 最优配置在 OOS 的相对排名(0~1),0.5=中位
        rank = (oos_perf.rank(pct=True).values)[best]
        rank = min(max(rank, 1e-6), 1 - 1e-6)
        logits.append(np.log(rank / (1 - rank)))
    logits = np.array(logits)
    pbo = float((logits <= 0).mean())  # OOS 排名≤中位 的比例
    return pbo, len(logits)


def deflated_sharpe(M):
    """对最优配置(全样本Sharpe最高)算 PSR 与 Deflated Sharpe。月度收益→年化Sharpe。"""
    sr = M.mean(axis=1) / M.std(axis=1).replace(0, np.nan)  # 月度Sharpe(每配置)
    sr_ann = sr * np.sqrt(12)
    best = sr_ann.idxmax()
    r = M.loc[best].values
    r = r[~np.isnan(r)]
    n = len(r)
    if n < 12 or r.std() == 0:
        return best, None
    sr_m = r.mean() / r.std()                      # 月度Sharpe(非年化,用于PSR公式)
    from scipy.stats import skew, kurtosis
    g3, g4 = skew(r), kurtosis(r, fisher=False)
    N = len(M)                                      # 试验数=配置数
    var_sr = sr_ann.var()
    eg = 0.5772156649
    sr_star = np.sqrt(var_sr / 12) * ((1 - eg) * norm.ppf(1 - 1 / N) + eg * norm.ppf(1 - 1 / (N * np.e)))
    denom = np.sqrt(1 - g3 * sr_m + (g4 - 1) / 4 * sr_m ** 2)
    dsr = norm.cdf((sr_m - sr_star) * np.sqrt(n - 1) / denom)
    psr = norm.cdf(sr_m * np.sqrt(n - 1) / denom)   # vs SR*=0
    return best, {"sr_ann": float(sr_m * np.sqrt(12)), "psr": float(psr), "dsr": float(dsr),
                  "sr_star_ann": float(sr_star * np.sqrt(12)), "N_trials": N, "n_months": n}


def main():
    jf = sys.argv[1] if len(sys.argv) > 1 else "/tmp/rb.json"
    sig = json.load(open(jf))["signals"]
    M = _config_month_matrix(sig)
    print(f"配置网格 {len(M)} 个 × 月度切片 {M.shape[1]} 个(信号 {len(sig)} 条)")
    pbo, ncomb = pbo_cscv(M)
    print(f"\n=== PBO 回测过拟合概率(CSCV {ncomb} 组合)===")
    if pbo is None:
        print("  切片不足,无法计算")
    else:
        verdict = "过拟合严重(选优=运气)" if pbo > 0.5 else "可接受" if pbo > 0.2 else "稳健(选优有真实性)"
        print(f"  PBO = {pbo:.2f}  → {verdict}(>0.5严重 / 0.2~0.5可接受 / <0.2稳健)")
    best, ds = deflated_sharpe(M)
    print(f"\n=== Deflated Sharpe(最优配置={best},试验数N={len(M)})===")
    if ds is None:
        print("  样本不足")
    else:
        print(f"  年化Sharpe={ds['sr_ann']:.2f}  扣多重检验后门槛SR*={ds['sr_star_ann']:.2f}")
        print(f"  PSR(>0显著)={ds['psr']:.2f}  Deflated SR={ds['dsr']:.2f}"
              f"  → {'显著(非过拟合)' if ds['dsr'] > 0.95 else '存疑' if ds['dsr'] > 0.5 else '不显著(疑过拟合)'}")


if __name__ == "__main__":
    main()
