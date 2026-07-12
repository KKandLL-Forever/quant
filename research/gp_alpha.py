"""遗传规划(GP)因子挖掘 + 反过拟合验证:复刻「表达式树进化挖 alpha」那套,并补上它演示里缺的样本外检验。

对应网传「遗传算法引擎/种小麦」视频:染色体=数学表达式树,叶=行情特征、节点=算子(gplearn),
适应度=Rank IC,精英保留+锦标赛选择+交叉/变异,复杂度惩罚(parsimony)。

本脚本的重点不是「挖到牛因子」,而是量化它有多容易过拟合:
  · 硬 walk-forward:GP 只在训练窗进化,最优公式只在从未见过的测试年算 OOS Rank IC。
  · 收集末代整个种群的 IS→OOS IC 收缩(挑出来的赢家样本外掉多少)。
  · 最优因子做 OOS 分层多空回测(计成本),看年化/夏普是否兑现。

用法:python research/gp_alpha.py [--pop 300 --gens 15 --hold 10 --uni 1000]
数据:本地 DuckDB(daily/daily_basic/adj_factor)。需 gplearn。

结论(实测 2018~2026,每日前800只,未来10日,pop200/gens12,非重叠调仓+15bp成本):
  跑之前预判「样本外崩盘」,实测被打脸、结论更细腻——GP 是「因子重组器」,不是过拟合机器也不是圣杯:
  · 真实数据:IS Rank IC 0.111 → OOS 0.124(没崩、几乎不缩),OOS 分层多空夏普 2022~24 约 1.4~1.6、
    2025 衰减到 0.37。挖出的最优公式 = max(neg(log(max(turn,vratio,mom20))), size),即
    「低换手 + 反转 + 小市值」——全是 A 股已知有真实溢价的因子,GP 只是把它们重组,非无中生有。
  · 打乱标签对照组(--shuffle,每日截面内置换未来收益、摧毁真信号):IS_IC 0.003、OOS_IC≈0。
    → 证明:适应度用「Rank IC 在上千个交易日截面取均值」时,碰运气的公式要同时骗过上千个独立截面,
      概率极低——大面板本身就是天然正则,这个设置下 GP 造不出伪 IS_IC。过拟合风险远比想象小。
  · 但整个种群 OOS_IC≈0:进化出的绝大多数公式无信号,只有被选中的最优公式有——它有效纯因为特征本身有 edge。
  真正的隐患不在过拟合,而在:①挖到的是「小市值+反转」这类溢价,正是本仓库 alpha144 标注的「微盘流动性
    幻觉」——纸面真、实盘因换手/冲击/容量难兑现,15bp 成本对小盘偏乐观;②会衰减(2025);③幸存者偏差有限
    (退市股 336 只中 251 只在 daily 有历史行情,占75%,不完全干净但不严重)。
  → 定性:GP 只跟喂给它的特征一样好——喂真因子→重现已知(且容量受限)的溢价,喂噪声→一无所获;
    它不是"自我进化出圣杯",而是一台高效的"已知因子重组+确认"工具。视频里 +0.062/夏普1.21 若真做了 OOS
    则可信度尚可(数量级对得上),但演示全程无训练/测试划分,无法判断是本脚本这种(真)还是纯样本内(假)。留档。
三段式选股 + 剥beta + 择时(--picks N --test-start,2026-07 深挖,hold=20,top20,前800成交额池):最终定论。
  切分:Train=test_start前全部,Val=前1年选公式,Test=test_start起冻结选股。Test IC 两窗都≈0.10(相对选股力在)。
  · 起点即结论:Test从2025 纯多头扣费净 +58.7%/回撤-18%;Test从2023(含熊)-45.4%/回撤-62%。只看近窗严重高估。
  · 剥beta后 alpha≈0:2025 +58.7% = universe(β)+49.5% + 选股超额仅+4.7%;2023 选股超额 -55%(负!)。
    用中证1000/IM期货对冲口径一致 → 收益几乎全是 beta,「IC正 ≠ 多头赚钱」;便携alpha无正α可搬,退化成加杠杆。
  · 择时控仓(上证MA30&MA60双线走坏=空仓)是唯一真改善:含熊完整周期,中证1000择时 +37.4%/夏普0.58/回撤-18%,
    完胜满仓β(+31.7%/-36%)——回撤砍半、收益略升。但纯牛窗(2025)择时踩空反而拖后腿(保险要交保费)。
    择时对 top20 也把 -45%→-24%,但因本身负α只能少亏;择时纯β完胜择时top20 → 那个GP因子毫无价值。
  → 总定论:这套 GP 的 edge 全在 beta,剥beta后无α;真正值钱的不是选股因子/便携alpha,而是最朴素的
    「大盘健康就满仓指数、走坏就空仓」的 regime 择时——与仓库总纲(edge在regime择时不在技术因子)完全闭环。留档。
用法:python research/gp_alpha.py [--pop 300 --gens 15 --uni 1000]  对照:--shuffle  |  选股:--picks 20 --test-start 2023-01-01
"""
import argparse
import warnings
import duckdb
import numpy as np
import pandas as pd
from cache_tushare import DUCKDB_PATH

warnings.filterwarnings("ignore")

FEATS = ["mom5", "mom20", "rev1", "vol20", "turn", "vratio", "amt", "pe", "pb", "size"]
_DATE = None   # 训练集每行的日期码(供 Rank IC 按日分组)
_RANKY = None  # 训练集每行 未来收益 的按日排名(预算,加速)


def load_panel(start: str, end: str, uni: int, hold: int) -> pd.DataFrame:
    """截面面板:每日取成交额前 uni 的票,算特征(按日横截面z-score)+ 未来hold日收益。"""
    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    df = con.execute(
        """SELECT d.ts_code, d.trade_date td, d.close*a.adj_factor adjc, d.pct_chg pct,
                  d.amount amt, b.turnover_rate turn, b.volume_ratio vratio,
                  b.pe_ttm pe, b.pb pb, b.circ_mv cmv
           FROM daily d JOIN adj_factor a ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date
           LEFT JOIN daily_basic b ON b.ts_code=d.ts_code AND b.trade_date=d.trade_date
           WHERE d.trade_date>=? AND d.trade_date<=? AND d.ts_code NOT LIKE '%.BJ'
           ORDER BY d.ts_code, d.trade_date""", [start, end]).df()
    con.close()

    g = df.groupby("ts_code", sort=False)
    df["mom5"] = g["adjc"].transform(lambda s: s / s.shift(5) - 1)
    df["mom20"] = g["adjc"].transform(lambda s: s / s.shift(20) - 1)
    df["rev1"] = -df["pct"] / 100
    df["vol20"] = g["pct"].transform(lambda s: s.rolling(20).std()) / 100
    df["amt"] = np.log(df["amt"].clip(lower=1))
    df["size"] = np.log(df["cmv"].clip(lower=1))
    df["fwd"] = g["adjc"].transform(lambda s: s.shift(-hold) / s - 1)

    df = df.dropna(subset=FEATS + ["fwd"])
    df["rank_amt"] = df.groupby("td")["amt"].rank(ascending=False)
    df = df[df["rank_amt"] <= uni].copy()

    def _z(s):
        return ((s - s.mean()) / (s.std() + 1e-9)).clip(-4, 4)
    for c in FEATS:
        df[c] = df.groupby("td")[c].transform(_z)
    df["dcode"] = df["td"].astype("category").cat.codes
    return df


def _rank_ic(y, y_pred, w) -> float:
    """gplearn 适应度:按日横截面 Rank IC 均值(用全局 _DATE/_RANKY,max_samples=1 对齐)。"""
    if _DATE is None or len(y_pred) != len(_DATE):
        return 0.0
    s = pd.Series(y_pred)
    rf = s.groupby(_DATE).rank()
    d = pd.DataFrame({"d": _DATE, "rf": rf.to_numpy(), "ry": _RANKY})
    ic = d.groupby("d").apply(
        lambda x: np.corrcoef(x["rf"], x["ry"])[0, 1] if len(x) > 20 else np.nan).mean()
    return 0.0 if not np.isfinite(ic) else float(ic)


def oos_ic(prog, df: pd.DataFrame) -> float:
    """把已训练的 gplearn program 用到 df(测试集),算按日 Rank IC 均值。"""
    x = df[FEATS].to_numpy()
    f = prog.execute(x)
    if not np.all(np.isfinite(f)):
        f = np.nan_to_num(f)
    t = pd.DataFrame({"d": df["dcode"].to_numpy(), "f": f, "r": df["fwd"].to_numpy()})
    ic = t.groupby("d").apply(
        lambda g: g["f"].corr(g["r"], method="spearman") if len(g) > 20 else np.nan).mean()
    return float(ic) if np.isfinite(ic) else np.nan


def ls_backtest(prog, df: pd.DataFrame, hold: int, cost: float = 0.0015) -> dict:
    """OOS 分层:每 hold 日不重叠调仓,买最高分组(等权)对比等权基准,扣双边成本。

    # WHY: 用不重叠周期而非逐日重叠的 hold 日收益,避免自相关把夏普虚抬 ~sqrt(hold) 倍。
    """
    x = df[FEATS].to_numpy()
    f = np.nan_to_num(prog.execute(x))
    t = df[["td", "fwd"]].copy()
    t["f"] = f
    dates = sorted(t["td"].unique())[::hold]
    periods = []
    for d in dates:
        g = t[t["td"] == d]
        if len(g) < 30:
            continue
        q = g["f"].rank(pct=True)
        periods.append((g["fwd"][q >= 0.8].mean() - g["fwd"].mean()) - cost)
    per = pd.Series(periods).dropna()
    if len(per) < 5:
        return {"ann": np.nan, "sharpe": np.nan}
    ppy = 244 / hold
    ann = (1 + per.mean()) ** ppy - 1
    sharpe = per.mean() / (per.std() + 1e-9) * np.sqrt(ppy)
    return {"ann": ann, "sharpe": sharpe}


def run_fold(train: pd.DataFrame, test: pd.DataFrame, pop: int, gens: int, hold: int) -> dict:
    """一折:GP 在 train 进化,best 在 test 算 OOS IC;末代种群 IS vs OOS 收缩。"""
    from gplearn.genetic import SymbolicRegressor
    from gplearn.fitness import make_fitness
    global _DATE, _RANKY
    _DATE = train["dcode"].to_numpy()
    _RANKY = train.groupby("dcode")["fwd"].rank().to_numpy()

    est = SymbolicRegressor(
        population_size=pop, generations=gens, tournament_size=20,
        p_crossover=0.7, p_subtree_mutation=0.1, p_hoist_mutation=0.05, p_point_mutation=0.1,
        function_set=("add", "sub", "mul", "div", "sqrt", "log", "abs", "neg", "inv", "max", "min"),
        metric=make_fitness(function=_rank_ic, greater_is_better=True, wrap=False),
        parsimony_coefficient=0.002, feature_names=FEATS,
        max_samples=1.0, n_jobs=1, random_state=42, verbose=0)
    est.fit(train[FEATS].to_numpy(), train["fwd"].to_numpy())

    best = est._program
    is_ic = best.raw_fitness_
    oo_ic = oos_ic(best, test)
    final = est._programs[-1]
    pairs = [(p.raw_fitness_, oos_ic(p, test)) for p in final if p is not None][:60]
    is_top = np.mean([a for a, _ in pairs])
    oo_top = np.nanmean([b for _, b in pairs])
    bt = ls_backtest(best, test, hold)
    return {"is_ic": is_ic, "oos_ic": oo_ic, "pop_is": is_top, "pop_oos": oo_top,
            "ann": bt["ann"], "sharpe": bt["sharpe"], "formula": str(best)}


def _fit_gp(train: pd.DataFrame, pop: int, gens: int):
    """在 train 上进化,返回 est(供跨全代取候选公式)。"""
    from gplearn.genetic import SymbolicRegressor
    from gplearn.fitness import make_fitness
    global _DATE, _RANKY
    _DATE = train["dcode"].to_numpy()
    _RANKY = train.groupby("dcode")["fwd"].rank().to_numpy()
    est = SymbolicRegressor(
        population_size=pop, generations=gens, tournament_size=20,
        p_crossover=0.7, p_subtree_mutation=0.1, p_hoist_mutation=0.05, p_point_mutation=0.1,
        function_set=("add", "sub", "mul", "div", "sqrt", "log", "abs", "neg", "inv", "max", "min"),
        metric=make_fitness(function=_rank_ic, greater_is_better=True, wrap=False),
        parsimony_coefficient=0.002, feature_names=FEATS,
        max_samples=1.0, n_jobs=1, random_state=42, verbose=0)
    est.fit(train[FEATS].to_numpy(), train["fwd"].to_numpy())
    return est


def run_pick(panel: pd.DataFrame, pop: int, gens: int, hold: int, topn: int,
             test_start: str, out_json: str):
    """三段式:Train 进化 → Val(测试集前1年)选公式 → Test(test_start起)冻结选股,吐 HTML 用 JSON。"""
    import json
    ts = pd.Timestamp(test_start)
    vs = ts - pd.DateOffset(years=1)
    tr = panel[panel["td"] < vs]
    va = panel[(panel["td"] >= vs) & (panel["td"] < ts)]
    te = panel[panel["td"] >= ts]
    print(f"Train {tr['td'].min().date()}~{tr['td'].max().date()} {len(tr):,}行 | "
          f"Val {va['td'].min().date()}~{va['td'].max().date()} {len(va):,}行 | "
          f"Test {te['td'].min().date()}~{te['td'].max().date()} {len(te):,}行\n")

    est = _fit_gp(tr, pop, gens)
    cands = {str(p): p for gen in est._programs for p in gen if p is not None}.values()
    scored = [(oos_ic(p, va), p) for p in cands]
    scored = [(ic, p) for ic, p in scored if np.isfinite(ic)]
    best_val, best = max(scored, key=lambda x: x[0])
    tr_ic, te_ic = oos_ic(best, tr), oos_ic(best, te)
    print(f"选中公式(按验证集IC最高,共{len(scored)}个候选):\n  {best}")
    print(f"Rank IC:  训练 {tr_ic:.3f}   验证 {best_val:.3f}   测试 {te_ic:.3f}\n")

    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    names = dict(con.execute("SELECT ts_code,name FROM stock_meta").fetchall())
    con.close()
    te = te.assign(fval=np.nan_to_num(best.execute(te[FEATS].to_numpy())))
    periods = []
    for d in sorted(te["td"].unique())[::hold]:
        g = te[te["td"] == d].nlargest(topn, "fval")
        picks = [{"name": names.get(r["ts_code"], r["ts_code"]), "code": r["ts_code"],
                  "ret": round(float(r["fwd"]) * 100, 1)}
                 for _, r in g.iterrows() if np.isfinite(r["fwd"])]
        if not picks:
            continue
        rets = [p["ret"] for p in picks]
        periods.append({"date": str(pd.Timestamp(d).date()), "picks": sorted(picks, key=lambda x: x["ret"]),
                        "mean": round(float(np.mean(rets)), 1), "win": round(float(np.mean([x > 0 for x in rets])) * 100),
                        "best": max(rets), "worst": min(rets)})
    meta = {"hold": hold, "topn": topn, "test_start": test_start, "formula": str(best),
            "train_ic": round(tr_ic, 3), "val_ic": round(best_val, 3), "test_ic": round(te_ic, 3),
            "periods": periods}
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    print(f"{len(periods)} 期 → {out_json}")
    print(f"各期均值收益: {[p['mean'] for p in periods]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pop", type=int, default=300)
    ap.add_argument("--gens", type=int, default=15)
    ap.add_argument("--hold", type=int, default=10)
    ap.add_argument("--uni", type=int, default=1000)
    ap.add_argument("--picks", type=int, default=0, help=">0 则跑三段式,输出每期前N只选股 JSON(供 HTML)")
    ap.add_argument("--test-start", default="2025-01-01", help="测试集起始;验证集自动取其前1年,训练集取更早全部")
    ap.add_argument("--shuffle", action="store_true", help="对照组:每日截面内打乱未来收益(摧毁真信号),看GP能否凭空造IS_IC")
    args = ap.parse_args()

    print(f"载入面板(2018~2026,每日前{args.uni}只,未来{args.hold}日)...", flush=True)
    panel = load_panel("2018-01-01", "2026-07-10", args.uni, args.hold)
    if args.shuffle:
        rng = np.random.default_rng(0)
        panel["fwd"] = panel.groupby("td")["fwd"].transform(lambda s: rng.permutation(s.to_numpy()))
        print("【对照组】已按日打乱未来收益——真信号已摧毁,OOS_IC 应≈0")
    print(f"面板 {len(panel):,} 行,{panel['td'].nunique()} 天\n")

    if args.picks:
        import os
        tag = args.test_start.replace("-", "")[:6]
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"gp_data_{tag}.json")
        run_pick(panel, args.pop, args.gens, args.hold, args.picks, args.test_start, out)
        return

    folds = [("2018-01-01", "2021-12-31", "2022"),
             ("2018-01-01", "2022-12-31", "2023"),
             ("2018-01-01", "2023-12-31", "2024"),
             ("2018-01-01", "2024-12-31", "2025")]

    print(f"{'测试年':<8}{'IS_IC':>8}{'OOS_IC':>8}{'群IS':>8}{'群OOS':>8}{'OOS年化':>9}{'OOS夏普':>9}")
    rows = []
    for s, e, ty in folds:
        tr = panel[(panel["td"] >= pd.Timestamp(s)) & (panel["td"] <= pd.Timestamp(e))]
        te = panel[(panel["td"] >= pd.Timestamp(f"{ty}-01-01")) & (panel["td"] <= pd.Timestamp(f"{ty}-12-31"))]
        if len(te) < 500:
            continue
        r = run_fold(tr, te, args.pop, args.gens, args.hold)
        rows.append(r)
        print(f"{ty:<8}{r['is_ic']:>8.3f}{r['oos_ic']:>8.3f}{r['pop_is']:>8.3f}{r['pop_oos']:>8.3f}"
              f"{r['ann']*100:>8.1f}%{r['sharpe']:>9.2f}")

    if rows:
        print(f"\n均值:  IS_IC {np.mean([r['is_ic'] for r in rows]):.3f}  "
              f"OOS_IC {np.nanmean([r['oos_ic'] for r in rows]):.3f}  "
              f"OOS夏普 {np.nanmean([r['sharpe'] for r in rows]):.2f}")
        print(f"IS→OOS Rank IC 收缩率: {1 - np.nanmean([r['oos_ic'] for r in rows]) / (np.mean([r['is_ic'] for r in rows]) + 1e-9):.0%}")
        print("\n各折最优公式:")
        for (s, e, ty), r in zip(folds, rows):
            print(f"  {ty}: {r['formula']}")


if __name__ == "__main__":
    main()
