"""首板→2板：板块协同因子的预测力诊断（纯探索，全本地数据，不连网）。

思路（验证过的口径，见 002971 案例）：
  - 个股当日题材：kpl_list.theme（顿号分隔）+ lu_desc（涨停原因）→ token 集合
  - 当日板块强度：limit_cpt_list（开盘啦涨停最强板块，up_nums=涨停家数, rank, up_stat=梯队）
  - 子串匹配（"芯片" ↔ "芯片概念"）把个股 token 映射到当日板块，取最强归属

候选因子：
  sect_up_nums  所属题材当日涨停家数（最强归属）  —— 板块协同强度
  sect_rank_inv 最强归属题材 rank 的倒数(1/rank)   —— 越靠前越大
  sect_top3     是否属于当日 Top3 题材 (1/0)
  sect_ladder   最强归属题材连板梯队高度(板数, 解析 up_stat)
  sect_n_hot    个股 token 命中的当日上榜题材数     —— 协同广度

输出：1to2/diag_sector_{start}_{end}.csv（IC / AUC / 组均值）
用法：python 1to2/diag_sector.py [--start 20200101]
"""
import argparse
import os
import re
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

import duckdb as _duckdb
from db_loader import _ENV

DUCK = _ENV.get("LOCAL_DUCKDB_PATH", "stock_data_tushare.duckdb")
FACTORS = ["sect_pct", "sect_pct_max", "sect_up_nums", "sect_rank_inv", "sect_top3", "sect_ladder", "sect_n_hot"]


def _ladder(up_stat):
    """从 '7天5板' 解析出连板梯队高度（板数）。"""
    if not up_stat:
        return np.nan
    m = re.search(r"(\d+)板", str(up_stat))
    return float(m.group(1)) if m else np.nan


def _signals_with_label(con, start, end):
    """1进2 首板信号 + label（次日涨停=晋级2板）。"""
    return con.execute(f"""
        WITH lt AS (
            SELECT ts_code, trade_date FROM limit_list_d
            WHERE limit_type='U' AND limit_times=1
              AND ts_code NOT LIKE '688%' AND ts_code NOT LIKE '30%' AND ts_code NOT LIKE '%.BJ'
        ),
        dd AS (
            SELECT ts_code, trade_date, pct_chg,
                   LEAD(pct_chg) OVER (PARTITION BY ts_code ORDER BY trade_date) AS nxt
            FROM daily
        )
        SELECT strftime(lt.trade_date,'%Y%m%d') AS trade_date, lt.ts_code,
               CASE WHEN dd.nxt >= 9.8 THEN 1 ELSE 0 END AS label
        FROM lt JOIN dd ON dd.ts_code=lt.ts_code AND dd.trade_date=lt.trade_date
        LEFT JOIN stock_st st ON st.ts_code=lt.ts_code AND st.trade_date=lt.trade_date
        WHERE dd.nxt IS NOT NULL AND st.ts_code IS NULL
          AND strftime(lt.trade_date,'%Y%m%d') BETWEEN '{start}' AND '{end}'
    """).df()


def _stock_tokens(con):
    """每个 (trade_date,ts_code) 的题材 token 集合（theme 顿号分隔 + lu_desc）。"""
    df = con.execute("""
        SELECT strftime(trade_date,'%Y%m%d') AS trade_date, ts_code, theme, lu_desc
        FROM kpl_list WHERE tag='涨停'
    """).df()
    out = {}
    for r in df.itertuples(index=False):
        toks = set()
        for part in (str(r.theme or "").split("、") + [str(r.lu_desc or "")]):
            p = part.strip()
            if len(p) >= 2:
                toks.add(p)
        if toks:
            out[(r.trade_date, r.ts_code)] = toks
    return out


def _day_boards(con):
    """每个交易日的板块榜：{date: [(core_name, up_nums, rank, ladder)]}。"""
    df = con.execute("""
        SELECT strftime(trade_date,'%Y%m%d') AS trade_date, name, up_nums, rank, up_stat, pct_chg
        FROM limit_cpt_list
    """).df()
    out = {}
    for r in df.itertuples(index=False):
        core = str(r.name).replace("概念", "").replace("板块", "").strip()
        out.setdefault(r.trade_date, []).append(
            (str(r.name), core, float(r.up_nums or 0), int(r.rank or 999), _ladder(r.up_stat), float(r.pct_chg) if r.pct_chg is not None else np.nan))
    return out


def _match(tokens, boards):
    """个股 token 与当日板块子串匹配，返回最强归属的特征。"""
    hits = []
    for name, core, up, rk, lad, pc in boards:
        ok = any((t in name) or (core and (core in t or t in core)) for t in tokens)
        if ok:
            hits.append((up, rk, lad, pc))
    if not hits:
        return dict(sect_pct=np.nan, sect_pct_max=np.nan, sect_up_nums=0.0,
                    sect_rank_inv=0.0, sect_top3=0.0, sect_ladder=np.nan, sect_n_hot=0.0)
    best = max(hits, key=lambda x: x[0])
    pcs = [h[3] for h in hits if not np.isnan(h[3])]
    return dict(sect_pct=best[3], sect_pct_max=(max(pcs) if pcs else np.nan),
                sect_up_nums=best[0], sect_rank_inv=1.0 / best[1],
                sect_top3=1.0 if best[1] <= 3 else 0.0,
                sect_ladder=best[2], sect_n_hot=float(len(hits)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20200101")
    ap.add_argument("--end", default="20991231")
    args = ap.parse_args()

    con = _duckdb.connect(DUCK, read_only=True)
    print("=== 取信号+label / 题材 / 板块榜 ===", flush=True)
    sig = _signals_with_label(con, args.start, args.end)
    tok = _stock_tokens(con)
    boards = _day_boards(con)
    con.close()
    print(f"信号 {len(sig)} 条，晋级率 {sig['label'].mean():.2%}；"
          f"kpl 题材覆盖日-股 {len(tok)}；板块榜覆盖 {len(boards)} 天", flush=True)

    rows = []
    for r in sig.itertuples(index=False):
        toks = tok.get((r.trade_date, r.ts_code))
        bd = boards.get(r.trade_date)
        feat = (_match(toks, bd) if toks and bd else
                dict(sect_pct=np.nan, sect_pct_max=np.nan, sect_up_nums=0.0, sect_rank_inv=0.0,
                     sect_top3=0.0, sect_ladder=np.nan, sect_n_hot=0.0))
        feat.update(ts_code=r.ts_code, trade_date=r.trade_date, label=r.label,
                    has_kpl=1 if toks else 0)
        rows.append(feat)
    m = pd.DataFrame(rows)
    print(f"kpl 命中率(首板在开盘啦榜单): {m['has_kpl'].mean():.1%}", flush=True)

    from scipy.stats import spearmanr
    from sklearn.metrics import roc_auc_score
    y = m["label"].astype(int).values
    base = y.mean()
    out = []
    for c in FACTORS:
        x = pd.to_numeric(m[c], errors="coerce")
        mask = x.notna().values & np.isfinite(x.values)
        if mask.sum() < 500:
            continue
        xv, yv = x.values[mask], y[mask]
        ic, _ = spearmanr(xv, yv)
        try:
            auc = roc_auc_score(yv, xv)
        except Exception:
            auc = np.nan
        out.append({"factor": c, "n": int(mask.sum()), "IC": round(ic, 4),
                    "AUC": round(auc, 4), "AUC_abs": round(abs(auc - 0.5) + 0.5, 4),
                    "mean_晋级": round(xv[yv == 1].mean(), 3),
                    "mean_未晋级": round(xv[yv == 0].mean(), 3)})
    diag = pd.DataFrame(out).sort_values("AUC_abs", ascending=False).reset_index(drop=True)
    print(f"\n基础晋级率 = {base:.2%}\n")
    print("=== 板块协同因子区分力（按 |AUC-0.5| 降序）===")
    print(diag.to_string(index=False))

    p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     f"diag_sector_{args.start}_{m['trade_date'].max()}.csv")
    diag.to_csv(p, index=False, encoding="utf-8-sig")
    print(f"\nCSV -> {p}")


if __name__ == "__main__":
    main()
