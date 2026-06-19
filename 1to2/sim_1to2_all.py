"""
sim_1to2_all.py — 首板→2板晋级模型回测：4 种资金管理策略合并到一页（Tab 切换）

模型（默认 v1）：
  v1  = xgb_1lb_2lb_v1.pkl，34 特征（ml_features_1to2_v1）；单切分原版。
  v2  = xgb_1lb_2lb_v2.pkl，34 特征；walk-forward 评估版（只到 ≤2023），切点跨熊牛标定。
  v2d = xgb_1lb_2lb_v2_deploy.pkl，34 特征；v2 实盘部署版（锁定轮数 + ≤今天全量数据训练）。

⚠️ 回测口径警告：v2d 是用 ≤今天全量数据训练的【实盘部署模型】，回测任何历史区间都存在
   数据穿越（训练时已见过该区间样本），收益会系统性虚高，不能用来评估真实表现。
   看可信历史成绩请用 v2（评估版，只到 ≤2023，2024~ 对它是干净样本外）。
   v2d 只适合在 ml_score_1to2_v2.py 里跑「当日/最新交易日」打分（预测未来、无穿越）。

proba：模型对「该首板个股次日涨停、晋级 2 板」的预测概率，0~1，越高越被看好。
分位档：按训练集 proba 分布取的高分位切点（从 pkl 的 tiers 自动读取）。
        参考值：Top1%≈proba≥0.86、Top5%≈≥0.70、Top10%≈≥0.65、Top20%≈≥0.59。
        分位越高 = 门槛越严、入选越少、单只越精。各策略信号池默认取 Top10%。

四种资金管理策略（同一信号池，仅仓位分配不同），用顶部 Tab 切换：
  1) 单账户·等权1/4   （每仓=总权益/4，最多 4 仓；simulate）
  2) 4账本·按proba排名（1号买当天最高 proba，独立复利，各 1w；simulate_ranked）
  3) 3账户·按分位档   （Top1% 全仓、Top5%/10% 各 1/4，各 1w；_run_tier）
  4) 单账户·动态仓位   （Top1%重仓/Top5%中仓/Top10%轻仓；simulate_dynamic）

本文件自包含：信号/特征/模型解析 + 四套回测引擎全部内联。

口径（智能止盈）：买点=首板次日开盘；卖出=未晋级次日开盘 / 连板断板当日开盘。含「对续板完美预判」
假设、一字板按开盘价成交，故收益是理想上限，非实盘可达。

用法：
  python 1to2/sim_1to2_all.py                       # 默认 v1、2026 起
  python 1to2/sim_1to2_all.py --model-version v2    # walk-forward 评估版（看可信历史成绩）
  python 1to2/sim_1to2_all.py --start 20240101 --end 20241231
  python 1to2/sim_1to2_all.py --capital 30000     # 各策略总资金均为 3w
                                                     # 排名→4账本各7500；分位档→3账户各1w

产出：1to2/html_output/sim_1to2_all_{start}_{end}_{ver}.html（自动打开）
"""

import argparse
import json
import os
import pickle
import sys
import webbrowser
from datetime import datetime

import duckdb as _duckdb
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

import screen_1to2_top1_t7_v1 as S

COMM = 0.0003
COMM_MIN = 5.0
STAMP = 0.0005
TRANSFER = 0.00001
LOT = 100
TIERS = [(1, "Top1%", 1), (5, "Top5%", 4), (10, "Top10%", 4)]

_MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "model")
MODEL_VERSIONS = {
    "v1": ("xgb_1lb_2lb_v1.pkl", "ml_features_1to2_v1"),
    "v2": ("xgb_1lb_2lb_v2.pkl", "ml_features_1to2_v1"),
    "v2d": ("xgb_1lb_2lb_v2_deploy.pkl", "ml_features_1to2_v1"),
}


def _resolve_model(version: str):
    """按版本返回 (model, feat_cols, tiers, build_feature_matrix)；差异仅在 pkl 与特征模块。"""
    if version not in MODEL_VERSIONS:
        raise ValueError(f"未知模型版本 {version}，可选 {list(MODEL_VERSIONS)}")
    pkl_name, feat_mod = MODEL_VERSIONS[version]
    with open(os.path.join(_MODEL_DIR, pkl_name), "rb") as f:
        b = pickle.load(f)
    build_feature_matrix = __import__(feat_mod).build_feature_matrix
    return b["model"], b["feature_cols"], b["tiers"], build_feature_matrix


def _load_trades(start: str, end: str, tier_pct: int,
                 version: str = "v1") -> tuple[pd.DataFrame, float]:
    """取 [start,end] 内、proba≥所选分位档的 2板信号，算出每笔买入/卖出日期与价格、连板、名称。"""
    model, feat_cols, tiers, build_feature_matrix = _resolve_model(version)
    proba_thr = tiers[S.TIER_INDEX[tier_pct]]["proba"]
    from ml_train_1to2_v1 import _get_signals

    sig = _get_signals()
    sig = sig[(sig["trade_date"] >= start) & (sig["trade_date"] <= end)].reset_index(drop=True)
    feat = build_feature_matrix(sig[["ts_code", "trade_date"]], require_label=False)
    feat = feat.dropna(subset=feat_cols, how="all").copy()
    feat["proba"] = model.predict_proba(feat[feat_cols])[:, 1]
    feat = feat[feat["proba"] >= proba_thr].copy()
    picked = feat[["ts_code", "trade_date", "proba"]].copy()

    con = _duckdb.connect(S.DUCK_PATH, read_only=True)
    boards = S._attach_boards(con, picked)
    picked_b = picked[["ts_code", "trade_date"]].merge(
        boards, on=["ts_code", "trade_date"], how="left")
    con.register("sigb", picked_b[["ts_code", "trade_date", "boards"]])
    tr = con.execute("""
        WITH sb AS (
          SELECT ts_code, CAST(strptime(trade_date,'%Y%m%d') AS DATE) d2,
                 trade_date d2_str, boards
          FROM sigb WHERE boards IS NOT NULL
        ),
        fut AS (
          SELECT sb.ts_code, sb.d2_str, sb.boards, d.trade_date td, d.open,
                 ROW_NUMBER() OVER (PARTITION BY sb.ts_code, sb.d2
                                    ORDER BY d.trade_date) - 1 AS off
          FROM sb JOIN daily d ON d.ts_code=sb.ts_code AND d.trade_date>=sb.d2
          QUALIFY off <= 30
        )
        SELECT ts_code, d2_str trade_date, boards,
          MAX(CASE WHEN off=1 THEN strftime(td,'%Y%m%d') END) buy_date,
          MAX(CASE WHEN off=1 THEN open END) buy_open,
          MAX(CASE WHEN boards<=1 AND off=2 THEN strftime(td,'%Y%m%d')
                   WHEN boards>=2 AND off=boards THEN strftime(td,'%Y%m%d') END) sell_date,
          MAX(CASE WHEN boards<=1 AND off=2 THEN open
                   WHEN boards>=2 AND off=boards THEN open END) sell_price
        FROM fut GROUP BY ts_code, d2_str, boards
    """).df()
    names = S._get_names(con, picked["ts_code"].tolist())
    con.close()

    df = tr.merge(picked[["ts_code", "trade_date", "proba"]],
                  on=["ts_code", "trade_date"], how="left")
    df = df[df["buy_open"].notna() & df["sell_price"].notna() & df["sell_date"].notna()
            & df["buy_date"].notna() & (df["buy_open"] > 0)].copy()
    df["name"] = df["ts_code"].map(names).fillna("")
    return df, proba_thr


def simulate(df: pd.DataFrame, capital: float, n_slots: int):
    """单账户·每仓=当前总权益/n_slots·最多 n_slots 仓·智能止盈卖出，事件驱动现金回测。"""
    cash = capital
    positions = []
    ev_dates = sorted(set(df["buy_date"]).union(set(df["sell_date"])))
    trades, equity_curve = [], []
    realized = 0.0
    seq, max_conc = 0, 0

    for day in ev_dates:
        still = []
        for p in positions:
            if p["sell_date"] == day:
                proceeds = p["shares"] * p["sell_price"]
                sell_fee = max(proceeds * COMM, COMM_MIN) + proceeds * (STAMP + TRANSFER)
                cash += proceeds - sell_fee
                pnl = proceeds - sell_fee - p["cost"] - p["buy_fee"]
                realized += pnl
                trades.append({**p, "proceeds": proceeds, "sell_fee": sell_fee,
                               "pnl": pnl, "ret": pnl / (p["cost"] + p["buy_fee"]),
                               "equity_after": capital + realized})
            else:
                still.append(p)
        positions = still

        todays = df[df["buy_date"] == day].sort_values("proba", ascending=False)
        for _, r in todays.iterrows():
            if len(positions) >= n_slots:
                break
            invested = sum(p["cost"] + p["buy_fee"] for p in positions)
            budget = min((cash + invested) / n_slots, cash)
            bp = r["buy_open"]
            shares = int(budget // (bp * LOT)) * LOT
            while shares > 0 and shares * bp * (1 + COMM) > cash:
                shares -= LOT
            if shares <= 0:
                continue
            cost = shares * bp
            buy_fee = max(cost * COMM, COMM_MIN) + cost * TRANSFER
            cash -= cost + buy_fee
            seq += 1
            positions.append({"seq": seq, "ts_code": r["ts_code"], "name": r["name"],
                              "boards": int(r["boards"]), "proba": float(r["proba"]),
                              "buy_date": r["buy_date"], "buy_open": bp, "shares": shares,
                              "cost": cost, "buy_fee": buy_fee,
                              "sell_date": r["sell_date"], "sell_price": r["sell_price"]})
            positions[-1]["cash_after"] = round(cash, 2)
            positions[-1]["hold_after"] = round(
                sum(p["cost"] + p["buy_fee"] for p in positions), 2)

        max_conc = max(max_conc, len(positions))
        invested = sum(p["cost"] + p["buy_fee"] for p in positions)
        equity_curve.append([day, round(cash + invested, 2)])

    final_equity = cash + sum(p["cost"] + p["buy_fee"] for p in positions)
    return trades, equity_curve, cash, positions, final_equity, max_conc


def simulate_ranked(df, per_cap: float, n_books: int):
    """N 个独立复利账本，编号小者吃高 proba；返回各账本曲线/余额/交易。"""
    bal = [per_cap] * n_books
    positions = [None] * n_books
    curves = [[] for _ in range(n_books)]
    ev_dates = sorted(set(df["buy_date"]).union(set(df["sell_date"])))
    trades = []
    realized = [0.0] * n_books
    seq = 0

    for day in ev_dates:
        for k in range(n_books):
            p = positions[k]
            if p and p["sell_date"] == day:
                proceeds = p["shares"] * p["sell_price"]
                sell_fee = max(proceeds * COMM, COMM_MIN) + proceeds * (STAMP + TRANSFER)
                bal[k] = p["idle"] + proceeds - sell_fee
                pnl = proceeds - sell_fee - p["cost"] - p["buy_fee"]
                realized[k] += pnl
                trades.append({**p, "proceeds": proceeds, "sell_fee": sell_fee,
                               "pnl": pnl, "ret": pnl / (p["cost"] + p["buy_fee"]),
                               "book": k, "equity_after": per_cap + realized[k]})
                positions[k] = None

        todays = df[df["buy_date"] == day].sort_values("proba", ascending=False)
        free = [k for k in range(n_books) if positions[k] is None]
        recs = list(todays.itertuples(index=False))
        for k, r in zip(free, recs):
            bp = r.buy_open
            shares = int(bal[k] // (bp * LOT)) * LOT
            while shares > 0 and shares * bp * (1 + COMM) > bal[k]:
                shares -= LOT
            if shares <= 0:
                continue
            cost = shares * bp
            buy_fee = max(cost * COMM, COMM_MIN) + cost * TRANSFER
            seq += 1
            positions[k] = {"seq": seq, "ts_code": r.ts_code, "name": r.name,
                            "boards": int(r.boards), "proba": float(r.proba),
                            "buy_date": r.buy_date, "buy_open": bp, "shares": shares,
                            "cost": cost, "buy_fee": buy_fee, "idle": bal[k] - cost - buy_fee,
                            "sell_date": r.sell_date, "sell_price": r.sell_price}
            bal[k] = 0.0
            positions[k]["cash_after"] = round(
                sum(bal) + sum(p["idle"] for p in positions if p), 2)
            positions[k]["hold_after"] = round(
                sum((p["cost"] + p["buy_fee"]) for p in positions if p), 2)

        for k in range(n_books):
            p = positions[k]
            eq = bal[k] + ((p["idle"] + p["cost"] + p["buy_fee"]) if p else 0.0)
            curves[k].append([day, round(eq, 2)])

    finals = [bal[k] + ((positions[k]["idle"] + positions[k]["cost"] + positions[k]["buy_fee"])
                        if positions[k] else 0.0) for k in range(n_books)]
    return trades, curves, finals


def _run_tier(start, end, tier_pct, per_cap, slots, version="v2"):
    """跑单个分位档账户，返回统计 + 曲线 + 交易。"""
    df, thr = _load_trades(start, end, tier_pct, version)
    trades, curve, cash, positions, final_equity, max_conc = simulate(
        df, per_cap, slots)
    return {"thr": thr, "n_cand": len(df), "trades": trades, "curve": curve,
            "final": final_equity, "max_conc": max_conc, "slots": slots}


def _cutoffs(version: str = "v2"):
    """从模型 tiers 取 Top1%/5%/10% 的 proba 切点（按模型版本）。"""
    _, _, tiers, _ = _resolve_model(version)
    return (tiers[S.TIER_INDEX[1]]["proba"], tiers[S.TIER_INDEX[5]]["proba"],
            tiers[S.TIER_INDEX[10]]["proba"])


def _weight(proba, cut, w):
    """按 proba 落档返回 (权重, 档位标签)。"""
    t1, t5, _t10 = cut
    if proba >= t1:
        return w[0], "Top1%"
    if proba >= t5:
        return w[1], "Top5%"
    return w[2], "Top10%"


def simulate_dynamic(df, capital, cut, w):
    """单账户·动态仓位事件驱动回测。"""
    cash = capital
    positions = []
    ev_dates = sorted(set(df["buy_date"]).union(set(df["sell_date"])))
    trades, equity_curve = [], []
    realized = 0.0
    seq, max_conc = 0, 0

    for day in ev_dates:
        still = []
        for p in positions:
            if p["sell_date"] == day:
                proceeds = p["shares"] * p["sell_price"]
                sell_fee = max(proceeds * COMM, COMM_MIN) + proceeds * (STAMP + TRANSFER)
                cash += proceeds - sell_fee
                pnl = proceeds - sell_fee - p["cost"] - p["buy_fee"]
                realized += pnl
                trades.append({**p, "proceeds": proceeds, "sell_fee": sell_fee,
                               "pnl": pnl, "ret": pnl / (p["cost"] + p["buy_fee"]),
                               "equity_after": capital + realized})
            else:
                still.append(p)
        positions = still

        todays = df[df["buy_date"] == day].sort_values("proba", ascending=False)
        for r in todays.itertuples(index=False):
            invested = sum(p["cost"] + p["buy_fee"] for p in positions)
            equity = cash + invested
            wt, tier = _weight(r.proba, cut, w)
            budget = min(wt * equity, cash)
            bp = r.buy_open
            shares = int(budget // (bp * LOT)) * LOT
            while shares > 0 and shares * bp * (1 + COMM) > cash:
                shares -= LOT
            if shares <= 0:
                continue
            cost = shares * bp
            buy_fee = max(cost * COMM, COMM_MIN) + cost * TRANSFER
            cash -= cost + buy_fee
            seq += 1
            positions.append({"seq": seq, "ts_code": r.ts_code, "name": r.name,
                              "boards": int(r.boards), "proba": float(r.proba),
                              "tier": tier, "weight": wt,
                              "buy_date": r.buy_date, "buy_open": bp, "shares": shares,
                              "cost": cost, "buy_fee": buy_fee,
                              "sell_date": r.sell_date, "sell_price": r.sell_price})
            positions[-1]["cash_after"] = round(cash, 2)
            positions[-1]["hold_after"] = round(
                sum(p["cost"] + p["buy_fee"] for p in positions), 2)

        max_conc = max(max_conc, len(positions))
        invested = sum(p["cost"] + p["buy_fee"] for p in positions)
        equity_curve.append([day, round(cash + invested, 2)])

    final_equity = cash + sum(p["cost"] + p["buy_fee"] for p in positions)
    return trades, equity_curve, cash, positions, final_equity, max_conc


def _shape(trades, with_book=False, with_tier=False):
    """把交易列表整形为前端行（可选附加 账本号 / 档位）。"""
    out = []
    for t in sorted(trades, key=lambda x: (x["buy_date"], x["seq"])):
        row = {"seq": t["seq"], "ts_code": t["ts_code"], "name": t["name"],
               "boards": t["boards"], "proba": round(t["proba"], 3),
               "buy_date": t["buy_date"], "buy_open": round(t["buy_open"], 2),
               "shares": int(t["shares"]), "cost": round(t["cost"], 0),
               "sell_date": t["sell_date"], "sell_price": round(t["sell_price"], 2),
               "proceeds": round(t["proceeds"], 0),
               "fee": round(t["buy_fee"] + t["sell_fee"], 1),
               "pnl": round(t["pnl"], 0), "ret": t["ret"],
               "cash": t.get("cash_after"), "hold": t.get("hold_after")}
        if with_book:
            row["book"] = t["book"] + 1
        if with_tier:
            row["tier"] = t["tier"]
        out.append(row)
    return out


def _kpis_single(capital, final, trades, max_conc):
    """单账户型策略的 KPI 列表。"""
    ret = final / capital - 1
    wins = sum(1 for t in trades if t["pnl"] > 0)
    col = "#c00" if ret >= 0 else "#080"
    return [["期初本金", f"{capital:,.0f}", "#222"],
            ["期末总权益", f"{final:,.0f}", col],
            ["累计收益", f"{ret*100:+.1f}%", col],
            ["已平仓交易", str(len(trades)), "#222"],
            ["胜率", f"{wins/max(len(trades),1)*100:.1f}%", "#222"],
            ["最大同时持仓", str(max_conc), "#222"]]


def _kpis_total(init, final):
    """多账户型策略的合计 KPI 列表。"""
    ret = final / init - 1
    col = "#c00" if ret >= 0 else "#080"
    return [["合计期初", f"{init:,.0f}", "#222"],
            ["合计期末", f"{final:,.0f}", col],
            ["合计收益", f"{ret*100:+.1f}%", col]]


def build_equal(df, capital):
    """策略1：单账户等权1/4。"""
    tr, curve, cash, pos, final, maxc = simulate(df, capital, 4)
    return {"id": "equal", "name": "单账户 · 等权1/4（最多4仓）", "type": "single",
            "capital": capital, "kpis": _kpis_single(capital, final, tr, maxc),
            "equity": curve, "trades": _shape(tr), "n_cand": len(df)}


def build_ranked(df, per_cap):
    """策略2：4账本按 proba 排名，独立复利。"""
    tr, curves, finals = simulate_ranked(df, per_cap, 4)
    books = []
    for k in range(4):
        bt = [t for t in tr if t["book"] == k]
        wins = sum(1 for t in bt if t["pnl"] > 0)
        avgp = sum(t["proba"] for t in bt) / len(bt) if bt else 0.0
        books.append({"book": k + 1, "final": round(finals[k], 2),
                      "ret": finals[k] / per_cap - 1, "n": len(bt),
                      "win": wins / len(bt) if bt else 0.0, "avg_proba": avgp,
                      "curve": curves[k], "trades": _shape(bt)})
    return {"id": "ranked", "name": "4账本 · 按proba排名（总3w平分·独立复利）", "type": "ranked",
            "per_cap": per_cap, "kpis": _kpis_total(per_cap * 4, sum(finals)),
            "books": books, "all_trades": _shape(tr, with_book=True)}


def build_tier(start, end, per_cap, version="v1"):
    """策略3：3账户按分位档（Top1%全仓、Top5/10各1/4）。"""
    accounts, total = [], 0.0
    for tier_pct, label, slots in TIERS:
        res = _run_tier(start, end, tier_pct, per_cap, slots, version)
        total += res["final"]
        tr = res["trades"]
        wins = sum(1 for t in tr if t["pnl"] > 0)
        accounts.append({"label": label, "thr": round(res["thr"], 4),
                         "slots": slots, "n_cand": res["n_cand"],
                         "final": round(res["final"], 2),
                         "ret": res["final"] / per_cap - 1, "n": len(tr),
                         "win": wins / len(tr) if tr else 0.0,
                         "max_conc": res["max_conc"], "curve": res["curve"],
                         "trades": _shape(tr)})
    return {"id": "tier", "name": "3账户 · 按分位档（Top1%全仓/5%/10%各1/4）",
            "type": "tier", "per_cap": per_cap,
            "kpis": _kpis_total(per_cap * len(TIERS), total), "accounts": accounts}


def build_dynamic(df, capital, version="v1"):
    """策略4：单账户动态仓位。"""
    cut = _cutoffs(version)
    w = (0.5, 0.25, 0.125)
    tr, curve, cash, pos, final, maxc = simulate_dynamic(df, capital, cut, w)
    tier_stat = []
    for tier, wt in zip(("Top1%", "Top5%", "Top10%"), w):
        tt = [t for t in tr if t["tier"] == tier]
        tw = sum(1 for t in tt if t["pnl"] > 0)
        tier_stat.append([f"{tier}（仓位{wt*100:.1f}%）", len(tt),
                          f"{tw/len(tt)*100:.1f}%" if tt else "-",
                          round(sum(t['pnl'] for t in tt), 0)])
    return {"id": "dynamic", "name": "单账户 · 动态仓位（按proba落档）", "type": "dynamic",
            "capital": capital, "cut": [round(c, 4) for c in cut], "w": list(w),
            "kpis": _kpis_single(capital, final, tr, maxc),
            "tier_stat": tier_stat, "equity": curve, "trades": _shape(tr, with_tier=True),
            "n_cand": len(df)}


def render_html(strategies, span, start, end, ver, cutoffs) -> str:
    """渲染 4 策略 Tab 切换的单页 HTML（标题区含模型版本 / proba / 分位切点说明）。"""
    payload = {"strategies": strategies, "span": span,
               "now": datetime.now().strftime("%Y-%m-%d %H:%M")}
    pj = json.dumps(payload, ensure_ascii=False)
    pkl = MODEL_VERSIONS[ver][0]
    fnote = {"v1": "34 特征 · 单切分原版",
             "v2": "34 特征 · walk-forward(2022~2026样本外)·切点跨熊牛标定",
             "v2d": "34 特征 · v2部署版(锁定轮数·全量数据)·实盘用，回测有穿越"}.get(ver, ver)
    t1, t5, t10 = cutoffs
    header = (
        '<h1>首板→2板 · 4 策略现金回测对照</h1>'
        f'<div class="modelbar"><span><b>模型 {ver}</b>'
        f'（<code>{pkl}</code> · {fnote}）</span>'
        '<span><b>proba</b>＝模型预测「该首板个股次日涨停、晋级 2 板」的概率'
        '（0~1，越高越强）</span>'
        f'<span><b>分位切点</b>　Top1% ≥ {t1:.4f}　·　Top5% ≥ {t5:.4f}　·　'
        f'Top10% ≥ {t10:.4f}　（本页信号池取 Top10%）</span></div>'
    )
    return r"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>首板→2板 4策略现金回测对照</title>
<style>
body{font-family:-apple-system,"Microsoft YaHei",sans-serif;margin:24px auto;max-width:1500px;
color:#222;padding:0 16px;background:#f4f6f8}
h1{font-size:21px;margin:0 0 6px}.meta{color:#666;font-size:13px;margin-bottom:14px}
.tabs{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px;border-bottom:2px solid #e3e6ea}
.tab{padding:9px 16px;font-size:13px;font-weight:600;cursor:pointer;border:1px solid #e3e6ea;
border-bottom:none;border-radius:8px 8px 0 0;background:#eef1f4;color:#666;margin-bottom:-2px}
.tab.on{background:#fff;color:#c00;border-color:#c00;border-bottom:2px solid #fff}
.kpi{display:flex;gap:14px;flex-wrap:wrap;margin:0 0 16px}
.kpi .box{flex:1;min-width:140px;background:#fff;border:1px solid #e8ebee;border-radius:10px;
padding:14px 18px;box-shadow:0 1px 3px rgba(0,0,0,.04)}
.kpi .lbl{font-size:12px;color:#999;margin-bottom:7px}
.kpi .val{font-size:22px;font-weight:800;font-variant-numeric:tabular-nums}
.sec-title{font-size:15px;font-weight:700;margin:20px 0 10px;padding-left:10px;border-left:4px solid #c00}
.card{background:#fff;border:1px solid #e8ebee;border-radius:10px;padding:14px 16px;
box-shadow:0 1px 3px rgba(0,0,0,.04);margin-bottom:16px}
.card h3{font-size:14px;font-weight:700;margin:0 0 8px}
.grid{display:flex;gap:16px;flex-wrap:wrap;align-items:stretch}.grid>.card{flex:1 1 0;min-width:0}
.kv{font-size:12.5px;width:100%;border-collapse:collapse;margin-top:8px}
.kv td{padding:3px 6px;border-bottom:1px dashed #f0f0f0}.kv td:last-child{text-align:right;font-weight:700}
table.sum{border-collapse:collapse;font-size:13px}
table.sum th,table.sum td{border:1px solid #e3e6ea;padding:7px 16px;text-align:right}
table.sum th{background:#f6f8fa;color:#666;text-align:center}table.sum td:first-child{text-align:left;font-weight:700}
table.t{border-collapse:collapse;width:100%;font-size:12.5px}
table.t th,table.t td{border-bottom:1px solid #eee;padding:5px 8px;text-align:right;white-space:nowrap}
table.t th{background:#fafafa;font-weight:600;position:sticky;top:0}
table.t td.l,table.t th.l{text-align:left}table.t tr:hover{background:#fffaf0}
.scroll{max-height:560px;overflow:auto;border:1px solid #eee;border-radius:8px}
.subt{font-weight:700;font-size:13px;margin:14px 0 6px}
.modelbar{background:#fff;border:1px solid #e8ebee;border-left:4px solid #c00;border-radius:8px;
padding:11px 15px;margin:0 0 16px;font-size:12.5px;color:#444;line-height:1.65;
display:flex;flex-direction:column;gap:3px}
.modelbar b{color:#c00}
.modelbar code{background:#f4f6f8;padding:1px 6px;border-radius:4px;font-size:12px}
</style></head><body>
__HEADER__
<div class="meta" id="meta"></div>
<div class="tabs" id="tabs"></div>
<div id="content"></div>
<script>
var P=__P__;
function ce(t,a,c){var el=document.createElement(t);if(a)for(var k in a){
 if(k==="class")el.className=a[k];else if(k==="style")el.style.cssText=a[k];else el.setAttribute(k,a[k]);}
 if(c!=null){(Array.isArray(c)?c:[c]).forEach(function(x){
  el.appendChild(typeof x==="string"||typeof x==="number"?document.createTextNode(String(x)):x);});}
 return el;}
function pct(x){return (x*100>=0?"+":"")+(x*100).toFixed(2)+"%";}
function colr(x){return x>=0?"#c00":"#080";}
function money(x){return Number(x).toLocaleString("en-US",{maximumFractionDigits:0});}
function spark(curve,base,w,h){w=w||360;h=h||120;
 if(!curve||curve.length<2)return ce("div",{style:"color:#999;font-size:12px;padding:20px 0"},"样本太少");
 var pad=8;var ys=curve.map(function(p){return p[1];});
 var mn=Math.min.apply(null,ys),mx=Math.max.apply(null,ys),rng=(mx-mn)||1;
 function Y(v){return pad+(h-2*pad)*(1-(v-mn)/rng);}
 var pts=curve.map(function(p,i){return (pad+(w-2*pad)*i/(curve.length-1)).toFixed(1)+","+Y(p[1]).toFixed(1);}).join(" ");
 var svg=document.createElementNS("http://www.w3.org/2000/svg","svg");
 svg.setAttribute("width","100%");svg.setAttribute("height",h);
 svg.setAttribute("viewBox","0 0 "+w+" "+h);svg.setAttribute("preserveAspectRatio","none");
 svg.style.cssText="background:#fff;border:1px solid #e3e6ea;border-radius:8px";
 if(base>=mn&&base<=mx){var bl=document.createElementNS(svg.namespaceURI,"line");
  bl.setAttribute("x1",pad);bl.setAttribute("x2",w-pad);bl.setAttribute("y1",Y(base));
  bl.setAttribute("y2",Y(base));bl.setAttribute("stroke","#ccc");
  bl.setAttribute("stroke-dasharray","4 4");svg.appendChild(bl);}
 var pl=document.createElementNS(svg.namespaceURI,"polyline");
 pl.setAttribute("points",pts);pl.setAttribute("fill","none");
 pl.setAttribute("stroke","#c00");pl.setAttribute("stroke-width","1.6");svg.appendChild(pl);
 return svg;}
function tbl(head,rows,cls){
 var thead=ce("tr",null,head.map(function(h){return ce("th",{class:h[1]||""},h[0]);}));
 var body=rows.map(function(r){return ce("tr",null,r.map(function(c){
   return ce("td",{class:c[1]||"",style:c[2]||""},c[0]);}));});
 return ce("table",{class:cls||"t"},[ce("thead",null,thead),ce("tbody",null,body)]);}
function kpiRow(kpis){return ce("div",{class:"kpi"},kpis.map(function(k){
 return ce("div",{class:"box"},[ce("div",{class:"lbl"},k[0]),
   ce("div",{class:"val",style:"color:"+(k[2]||"#222")},k[1])]);}));}
function tradeTable(trades,firstCol){
 var head=[["#"]];if(firstCol)head.push([firstCol[0]]);
 head=head.concat([["代码","l"],["名称","l"],["连板"],["proba"],["买入日"],["买入价"],
  ["股数"],["买入额"],["卖出日"],["卖出价"],["卖出额"],["费用"],["盈亏"],["收益率"],
  ["买入后现金"],["买入后持仓"],["总资产"]]);
 var rows=trades.map(function(t){var r=[[t.seq]];if(firstCol)r.push([t[firstCol[1]]]);
  return r.concat([[t.ts_code,"l"],[t.name,"l"],[t.boards+"板"],[(t.proba*100).toFixed(1)+"%"],
   [t.buy_date],[t.buy_open.toFixed(2)],[t.shares],[money(t.cost)],[t.sell_date],
   [t.sell_price.toFixed(2)],[money(t.proceeds)],[t.fee.toFixed(0)],
   [money(t.pnl),"","color:"+colr(t.pnl)+";font-weight:700"],
   [pct(t.ret),"","color:"+colr(t.ret)+";font-weight:700"],
   [t.cash==null?"-":money(t.cash),"","color:#888"],
   [t.hold==null?"-":money(t.hold),"","color:#888"],
   [(t.cash==null||t.hold==null)?"-":money(t.cash+t.hold),"","font-weight:700"]]);});
 return ce("div",{class:"scroll"},tbl(head,rows));}

function renderSingle(s){var box=ce("div",null);
 box.appendChild(kpiRow(s.kpis));
 if(s.tier_stat){box.appendChild(ce("div",{class:"sec-title"},"分档贡献"));
  box.appendChild(ce("div",{class:"card"},tbl([["分位档（仓位）","l"],["交易数"],["胜率"],["盈亏合计"]],
   s.tier_stat.map(function(r){return [[r[0],"l"],[r[1]],[r[2]],
    [money(r[3]),"","color:"+colr(r[3])+";font-weight:700"]];}),"sum")));}
 box.appendChild(ce("div",{class:"sec-title"},"总权益曲线"));
 box.appendChild(ce("div",{class:"card"},spark(s.equity,s.capital,1400,200)));
 box.appendChild(ce("div",{class:"sec-title"},"交易流水（"+s.trades.length+" 笔）"));
 box.appendChild(tradeTable(s.trades,s.type==="dynamic"?["档位","tier"]:null));
 return box;}

function renderRanked(s){var box=ce("div",null);
 box.appendChild(kpiRow(s.kpis));
 box.appendChild(ce("div",{class:"sec-title"},"各账本表现（1号买最高proba）"));
 box.appendChild(ce("div",{class:"grid"},s.books.map(function(b){
  return ce("div",{class:"card"},[ce("h3",null,b.book+"号账本"+(b.book===1?"（最高proba）":"")),
   spark(b.curve,s.per_cap,360,120),ce("table",{class:"kv"},[
    ce("tr",null,[ce("td",null,"期末"),ce("td",{style:"color:"+colr(b.ret)},money(b.final))]),
    ce("tr",null,[ce("td",null,"收益率"),ce("td",{style:"color:"+colr(b.ret)},pct(b.ret))]),
    ce("tr",null,[ce("td",null,"交易数"),ce("td",null,b.n)]),
    ce("tr",null,[ce("td",null,"胜率"),ce("td",null,(b.win*100).toFixed(1)+"%")]),
    ce("tr",null,[ce("td",null,"平均proba"),ce("td",null,(b.avg_proba*100).toFixed(1)+"%")])])]);})));
 box.appendChild(ce("div",{class:"sec-title"},"总流水（"+s.all_trades.length+" 笔）"));
 box.appendChild(tradeTable(s.all_trades,["账本","book"]));
 box.appendChild(ce("div",{class:"sec-title"},"各账本流水"));
 s.books.forEach(function(b){
  box.appendChild(ce("div",{class:"subt"},b.book+"号账本 · "+b.trades.length+" 笔"));
  box.appendChild(tradeTable(b.trades,null));});
 return box;}

function renderTier(s){var box=ce("div",null);
 box.appendChild(kpiRow(s.kpis));
 box.appendChild(ce("div",{class:"sec-title"},"各分位档账户表现"));
 box.appendChild(ce("div",{class:"grid"},s.accounts.map(function(a){
  return ce("div",{class:"card"},[ce("h3",null,a.label+"（proba≥"+a.thr.toFixed(4)+"，"+
    (a.slots===1?"全仓单票":"每仓1/"+a.slots)+"）"),
   spark(a.curve,s.per_cap,360,120),ce("table",{class:"kv"},[
    ce("tr",null,[ce("td",null,"期末"),ce("td",{style:"color:"+colr(a.ret)},money(a.final))]),
    ce("tr",null,[ce("td",null,"收益率"),ce("td",{style:"color:"+colr(a.ret)},pct(a.ret))]),
    ce("tr",null,[ce("td",null,"候选信号"),ce("td",null,a.n_cand)]),
    ce("tr",null,[ce("td",null,"实际交易"),ce("td",null,a.n)]),
    ce("tr",null,[ce("td",null,"胜率"),ce("td",null,(a.win*100).toFixed(1)+"%")]),
    ce("tr",null,[ce("td",null,"最大持仓"),ce("td",null,a.max_conc+"/"+a.slots)])])]);})));
 box.appendChild(ce("div",{class:"sec-title"},"各账户流水"));
 s.accounts.forEach(function(a){
  box.appendChild(ce("div",{class:"subt"},a.label+" 账户 · "+a.trades.length+" 笔"));
  box.appendChild(tradeTable(a.trades,null));});
 return box;}

function renderStrategy(s){
 if(s.type==="ranked")return renderRanked(s);
 if(s.type==="tier")return renderTier(s);
 return renderSingle(s);}

document.getElementById("meta").textContent="区间 "+P.span+" · 4 策略同口径对照（智能止盈：断板当日开盘卖）· 生成 "+P.now;
var content=document.getElementById("content");
var tabs=document.getElementById("tabs");
P.strategies.forEach(function(s,i){
 var t=ce("div",{class:"tab"+(i===0?" on":"")},s.name);
 t.onclick=function(){
  [].forEach.call(tabs.children,function(x){x.className="tab";});
  t.className="tab on";content.innerHTML="";content.appendChild(renderStrategy(s));};
 tabs.appendChild(t);});
content.appendChild(renderStrategy(P.strategies[0]));
</script></body></html>""".replace("__P__", pj).replace("__HEADER__", header)


def main():
    """跑 4 策略并合成单页 Tab 报告。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=30000.0, help="总起始资金（每个策略一致）")
    ap.add_argument("--start", default="20260101")
    ap.add_argument("--end", default="20991231")
    ap.add_argument("--model-version", default="v1", choices=list(MODEL_VERSIONS),
                    help="模型版本（v1 默认 / v2 评估 / v2d 实盘部署，v2d 回测有穿越）")
    args = ap.parse_args()
    cap = args.capital
    ver = args.model_version

    df, _ = _load_trades(args.start, args.end, 10, ver)
    print(f"区间 {args.start}~{args.end} · 模型 {ver} · Top10% 候选 {len(df)} 笔 · 各策略总资金 {cap:,.0f}")
    strategies = [build_equal(df, cap),
                  build_ranked(df, cap / 4),
                  build_tier(args.start, args.end, cap / len(TIERS), ver),
                  build_dynamic(df, cap, ver)]
    for s in strategies:
        print(f"  · {s['name']}：{s['kpis'][1][0]} {s['kpis'][1][1]}")

    span = f"{df['buy_date'].min()} ~ {df['buy_date'].max()}" if len(df) else "-"
    html = render_html(strategies, span, args.start, args.end, ver, _cutoffs(ver))
    out = os.path.join(os.path.dirname(__file__), "html_output",
                       f"sim_1to2_all_{args.start}_{args.end}_{ver}.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"HTML 已生成：{out}")
    try:
        webbrowser.open("file://" + os.path.abspath(out))
    except Exception:
        pass


if __name__ == "__main__":
    main()
