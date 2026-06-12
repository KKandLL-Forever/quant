"""快速筛选符合条件的股票"""

import sys
import os
import glob
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(__file__))

import config
from data_loader import _ABNORMAL_KEYWORDS, _RESTRICTED_PREFIXES


def quick_scan(data_dir: str, limit: int = 40) -> list[str]:
    """快速扫描，只读必要字段，筛选5-10元区间的正常股票"""
    files = glob.glob(os.path.join(data_dir, "*.csv"))
    candidates = []

    for f in files:
        try:
            # 只读前3列+价格列，速度快
            df = pd.read_csv(f, encoding="utf-8", usecols=[0, 1, 2, 4, 5, 6])
            df.columns = ["code", "name", "date", "high", "low", "close"]

            # 过滤异常股票
            name = df.iloc[0]["name"]
            if any(kw in name for kw in _ABNORMAL_KEYWORDS):
                continue

            # 过滤创业板、科创板
            code = df.iloc[0]["code"]
            num = str(code).split(".")[0]
            if any(num.startswith(p) for p in _RESTRICTED_PREFIXES):
                continue

            df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
            df = df.sort_values("date")
            recent = df.tail(500)

            if len(recent) < 400:
                continue

            avg = recent["close"].mean()
            lo = recent["low"].min()
            hi = recent["high"].max()
            std = recent["close"].std()

            # 价格区间5-10，且有足够的震荡
            if 5 <= avg <= 10 and lo >= 4.5 and hi <= 11 and std >= 0.5:
                candidates.append({
                    "file": os.path.basename(f),
                    "code": df.iloc[-1]["code"],
                    "name": df.iloc[-1]["name"],
                    "avg": round(avg, 2),
                    "low": round(lo, 2),
                    "high": round(hi, 2),
                    "std": round(std, 2),
                })
        except Exception:
            continue

    # 按波动率排序，取top N
    candidates.sort(key=lambda x: x["std"], reverse=True)
    return candidates[:limit]


if __name__ == "__main__":
    results = quick_scan(config.DATA_DIR, limit=40)
    print(f"筛选出 {len(results)} 只股票:\n")
    for i, r in enumerate(results):
        print(f"  {i+1:2d}. {r['code']} {r['name']:10s} "
              f"均价={r['avg']:.2f} 区间={r['low']:.2f}~{r['high']:.2f} 波动={r['std']:.2f}")
