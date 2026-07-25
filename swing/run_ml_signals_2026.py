"""
run_ml_signals_2026.py — 模型 top5% 信号清单(2026-01-01 之后,不管仓位)

模型(LightGBM)用 2021-2025 事件训练,对 2026-01-01 之后的 N/W 突破信号打分,取预测概率
top10% 全部列出(不做仓位管理)。每条标:日期/代码/名称/形态/ML分/至今最大涨幅/状态
(已走出主升浪 / 进行中 / 未达)。产出 HTML 清单。

主升浪判定为时间加权动态门槛:60日内任意一天 t,累计涨幅≥MW_HURDLE_K×√t 即达标
(快涨低门槛、慢涨高门槛,体现持仓时间成本);赢家可提前确认,输家需满60日才判负。

环境：.venv312。用法：python swing/run_ml_signals_2026.py --start 20260101 --tier 5
  --start/--end 信号时间范围(YYYYMMDD);--tier 只显示 ML 评分前百分之几(5=top5%,100=全部)
  档位门槛(top1/3/5/10/20/30)是**绝对分数阈值**,取自模型训练期事件的分数分布并随模型一起存盘
  (score_ref),只有 --train 重训才会变。故刷新数据/新增信号不会改变历史信号的入榜与否——
  旧版按信号区间内相对分位排名会导致老信号被回溯追加进榜(实盘当日看不到),已修。
  --pivot kernel/zigzag 枢轴检测(kernel=核平滑LMW默认,识别更干净更准 / zigzag=固定9%阈值);--h 核带宽默认4
  --mode quick/long 主升浪判定(quick=k0.06默认高胜率小赚 / long=k0.09低胜率大赚)
  --train 训练并存盘(模型/HTML 按 mode 分文件);--eval 跑 walk-forward 诚实评估(不出 HTML)
依赖：DuckDB(daily/daily_basic/adj_factor/cyq_perf/moneyflow);lightgbm;复用 run_patterns。

财务特征(roe/npyoy/fc_pos)口径:同一 ann_date 同一只票可能同时公告多份报告(如年报+一季报),
  一律只取 end_date 最新的那份(SQL QUALIFY 去重)+ mergesort 稳定排序。否则取到哪份由 DuckDB
  物理行序决定(实测 34% 的行落在重复组、53% 取到的不是最新报告、npyoy 中位差 66pct),
  一次增量写库就可能静默改变历史特征值。

关于 --start(训练/打分分界):训练用 date<start 的事件,打分用 >=start 的信号。
  · 回测/评价某段:把 start 设到那段开头(如 20260101,用 2021~2025 训、打分 2026)
  · 日常实盘:把 start 设到很近的日期(如今天),让训练吃满到当下、只打分最新信号
"""

import argparse
import os
_ROOT = os.path.dirname(os.path.abspath(__file__))
while _ROOT != "/" and not os.path.exists(os.path.join(_ROOT, "cache_tushare.py")):
    _ROOT = os.path.dirname(_ROOT)
import sys
import io

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, _ROOT)
if __name__ == "__main__" and getattr(sys.stdout, "encoding", "").lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import duckdb
import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.metrics import roc_auc_score

from cache_tushare import DUCKDB_PATH
from run_patterns import _detect, pending_breakouts
from kernel_pivots import _detect_kernel

THR, MW_GAIN, MW_DAYS = 0.09, 0.50, 60
EMBARGO_DAYS = 90  # ≈MW_DAYS(60交易日)的自然日数;train 末尾此窗内样本的label前瞻窗会探入val/test,须purge防泄露
MW_HURDLE_K = 0.090
DON_EXIT, COST = 20, 0.0006
SW_FIXSTOP, SW_ATRSTOP = 0.10, 1.0
SW_FIXTP, SW_ATRTP, SW_TPFRAC = 0.10, 2.0, 0.50
SW_TRAILACT, SW_TRAILDIST, SW_STALE = 0.03, 0.05, 20
SW_TPFRAC_SWEEP = (0.30, 0.40, 0.50, 0.75)
HOT_TOP, HOT_MV_FLOOR = 20, 2_000_000  # 同花顺热股榜并入池子前20;只滤流通市值<200亿的小盘妖股(circ_mv单位万元),不滤连板
VAL_MONTHS = 12
EVAL_FOLDS = [
    {"train_end": "2020-06-30", "val": ("2020-07-01", "2020-12-31"), "test": ("2021-01-01", "2021-12-31")},
    {"train_end": "2021-06-30", "val": ("2021-07-01", "2021-12-31"), "test": ("2022-01-01", "2022-12-31")},
    {"train_end": "2022-06-30", "val": ("2022-07-01", "2022-12-31"), "test": ("2023-01-01", "2023-12-31")},
    {"train_end": "2023-06-30", "val": ("2023-07-01", "2023-12-31"), "test": ("2024-01-01", "2024-12-31")},
    {"train_end": "2024-06-30", "val": ("2024-07-01", "2024-12-31"), "test": ("2025-01-01", "2025-12-31")},
]
LGB_PARAMS = dict(learning_rate=0.02, num_leaves=15, min_child_samples=100,
                  subsample=0.7, colsample_bytree=0.6, reg_alpha=0.5, reg_lambda=5.0,
                  min_split_gain=0.0, verbosity=-1,
                  deterministic=True, force_row_wise=True, n_jobs=1)   # 逐次可复现:单线程+确定性直方图(否则多线程浮点求和顺序不定,同种子也出不同树)
FEATS_ALL = ["ptype", "brk", "pos1y", "basew", "dma20", "dma60", "atrp", "adx", "ret20", "ret60",
             "volr", "winrate", "cyqconc", "mfnet20", "pe", "pb", "lnmv",
             "rs20", "rs60", "rsturn", "bregnum", "crowd", "idxdist", "lb2rate", "nlb", "upratio",
             "sector_rs", "lianban60", "sector_heat", "roe", "npyoy", "fc_pos"]
FEATS = ["ptype", "brk", "pos1y", "basew", "dma20", "atrp", "ret20", "ret60",
         "winrate", "cyqconc", "mfnet20", "pe", "pb", "lnmv", "rsturn",
         "crowd", "idxdist", "sector_rs", "sector_heat", "lianban60", "npyoy"]
OUT = os.path.join(_ROOT, "swing/ml_signals_2026.html")
FEAT_CN = {
    "ptype": "形态类型", "brk": "突破强度", "pos1y": "一年价格位置", "basew": "底部宽度",
    "dma20": "距20日线", "atrp": "波动率ATR%", "ret20": "20日涨幅", "ret60": "60日涨幅",
    "winrate": "获利盘比例", "cyqconc": "筹码集中度", "mfnet20": "20日主力净额", "pe": "市盈率",
    "pb": "市净率", "lnmv": "对数流通市值", "rsturn": "换手率分位", "crowd": "抱团度",
    "idxdist": "指数距60日高", "sector_rs": "板块相对强度", "sector_heat": "板块热度",
    "lianban60": "60日涨停数", "npyoy": "净利润同比",
}


def _shap_plot(model, X, path):
    """对已训练 LGB 模型出 SHAP beeswarm 图(特征中英双标),保存到 path。"""
    import platform
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    _cjk = {"Windows": ["Microsoft YaHei", "SimHei"],
            "Darwin": ["PingFang SC", "Arial Unicode MS", "Heiti TC", "STHeiti"]}.get(
        platform.system(), ["Noto Sans CJK SC", "WenQuanYi Zen Hei", "Microsoft YaHei", "SimHei"])
    plt.rcParams["font.sans-serif"] = _cjk
    plt.rcParams["axes.unicode_minus"] = False
    import shap
    Xs = X.sample(min(2000, len(X)), random_state=0) if len(X) > 2000 else X
    sv = shap.TreeExplainer(model).shap_values(Xs)
    if isinstance(sv, list):
        sv = sv[1]
    Xd = Xs.rename(columns={c: f"{FEAT_CN.get(c, c)} {c}" for c in Xs.columns})
    shap.summary_plot(sv, Xd, show=False, max_display=len(Xs.columns))
    plt.tight_layout()
    plt.savefig(path, dpi=130, bbox_inches="tight")
    plt.close()
MODEL_PATH = os.path.join(_ROOT, "swing/ml_signals_2026_model.pkl")


def _fetch_idx(pro, code, start):
    """从 tushare 拉指数日线收盘(分年避免8000行上限),返回 DataFrame[trade_date,close]。"""
    import pandas as pd
    s = start.replace("-", "")
    parts = []
    for y in range(int(s[:4]), pd.Timestamp.today().year + 1):
        d = pro.index_daily(ts_code=code, start_date=f"{max(int(s), y*10000+101)}", end_date=f"{y}1231")
        if d is not None and len(d):
            parts.append(d[["trade_date", "close"]])
    if not parts:
        return None
    df = pd.concat(parts, ignore_index=True)
    df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
    return df.sort_values("trade_date").reset_index(drop=True)


def _idx_regime(pro, code, start="2020-09-01"):
    """返回 (regime字典{Timestamp:bool}, 当前状态字符串)。走坏=MA30与MA60同时走坏(收盘<均线且均线下行);健康=至少一条多头(同小西西弗牛熊开关)。"""
    d = _fetch_idx(pro, code, start)
    if d is None or len(d) < 70:
        return {}, "无数据"
    ma30 = d["close"].rolling(30).mean()
    ma60 = d["close"].rolling(60).mean()
    h30 = (d["close"] > ma30) & (ma30 > ma30.shift(5))
    h60 = (d["close"] > ma60) & (ma60 > ma60.shift(5))
    hb = (h30 | h60).fillna(False)
    return dict(zip(d["trade_date"], hb)), ("健康" if hb.iloc[-1] else "走坏")


def _adx(h, l, c, n=14):
    up = h.diff(); dn = -l.diff()
    plus = np.where((up > dn) & (up > 0), up, 0.0); minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    pc = c.shift(1); tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / n, adjust=False).mean()
    pdi = 100 * pd.Series(plus, index=h.index).ewm(alpha=1 / n, adjust=False).mean() / atr
    mdi = 100 * pd.Series(minus, index=h.index).ewm(alpha=1 / n, adjust=False).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean(), atr


def _swing_exit(cc, gd, bo, atr_e, tpfrac):
    """论文出场(硬/ATR止损+跟踪止损+部分止盈+超时)模拟,返回(盈亏,是否持仓中,清仓日,部分止盈日)。"""
    ep = cc[bo]
    stop_lv = min(ep * (1 - SW_FIXSTOP), ep - SW_ATRSTOP * atr_e)
    tp_lv = min(ep * (1 + SW_FIXTP), ep + SW_ATRTP * atr_e)
    tp_done = False; remaining = 1.0; realized = 0.0; tract = False; hsince = 0.0; tp_dt = None
    for t2 in range(bo + 1, len(cc)):
        p = cc[t2]
        if not tract and p >= ep * (1 + SW_TRAILACT):
            tract = True; hsince = p
        if tract:
            hsince = max(hsince, p)
        if p <= stop_lv or (tract and p <= hsince * (1 - SW_TRAILDIST)) or (t2 - bo) >= SW_STALE:
            realized += remaining * (p / ep - 1)
            return realized - 2 * COST, False, pd.Timestamp(gd[t2]), tp_dt
        if not tp_done and p >= tp_lv:
            realized += tpfrac * (p / ep - 1); remaining = 1 - tpfrac; tp_done = True; tp_dt = pd.Timestamp(gd[t2])
    return realized + remaining * (cc[-1] / ep - 1) - 2 * COST, True, None, tp_dt


def _czsc_one(job):
    """单股缠论出场 M3(供并行调用):缠论卖点止盈→回调缠论买点回补,跌破MA60或入场价85%终止,复利。
    job=(ts, gd, cc, hh, ll, vv, dates, sel)。worker 内 import czsc;czsc 缺失或异常返回 []。"""
    ts, gd, cc, hh, ll, vv, dates, sel = job
    try:
        from czsc import CZSC, RawBar, Freq
        import czsc.signals as CS
    except Exception:
        return []

    def _sell(c):
        for fn in ("cxt_first_sell_V221126", "tas_macd_bc_V221201"):
            f = getattr(CS, fn, None)
            if not f:
                continue
            try:
                out = f(c, di=1)
            except Exception:
                try:
                    out = f(c)
                except Exception:
                    continue
            for v in out.values():
                tk = str(v).split("_")
                if fn == "cxt_first_sell_V221126" and tk[0] != "其他":
                    return True
                if fn == "tas_macd_bc_V221201" and tk[0] == "背驰" and "红" in str(v):
                    return True
        return False

    def _buy(c):
        for fn in ("cxt_first_buy_V221126", "cxt_second_bs_V230320", "cxt_third_buy_V230228"):
            f = getattr(CS, fn, None)
            if not f:
                continue
            try:
                out = f(c, di=1)
            except Exception:
                try:
                    out = f(c)
                except Exception:
                    continue
            for v in out.values():
                s = str(v); tk = s.split("_")
                if fn == "cxt_second_bs_V230320":
                    if "买" in s:
                        return True
                elif tk[0] != "其他":
                    return True
        return False

    bars = [RawBar(symbol=ts, id=i, dt=pd.Timestamp(gd[i]), freq=Freq.D, open=cc[i], close=cc[i],
                   high=hh[i], low=ll[i], vol=vv[i], amount=0.0) for i in range(len(cc))]
    try:
        c = CZSC(bars[:1])
        sd = {0} if _sell(c) else set()
        bd = {0} if _buy(c) else set()
        for i in range(1, len(bars)):
            c.update(bars[i])
            if _sell(c):
                sd.add(i)
            if _buy(c):
                bd.add(i)
    except Exception:
        return []
    idxmap = {pd.Timestamp(gd[i]): i for i in range(len(gd))}
    ma60 = pd.Series(cc).rolling(60).mean().to_numpy()

    def _m3(bo):
        mult, en, t = 1.0, bo, bo + 1
        legs = [(bo, "买")]
        while t < len(cc):
            if (ma60[t] == ma60[t] and cc[t] < ma60[t]) or cc[t] <= cc[en] * 0.85:
                legs.append((t, "止"))
                return mult * (cc[t] / cc[en]) * (1 - 2 * COST) - 1, t, False, legs
            if t in sd:
                mult *= (cc[t] / cc[en]) * (1 - 2 * COST)
                legs.append((t, "缠"))
                re = None
                for t2 in range(t + 1, len(cc)):
                    if ma60[t2] == ma60[t2] and cc[t2] < ma60[t2]:
                        break
                    if t2 in bd:
                        re = t2; break
                if re is None:
                    return mult - 1, t, False, legs
                en = re; legs.append((re, "补")); t = re + 1; continue
            t += 1
        return mult * (cc[-1] / cc[en]) * (1 - 2 * COST) - 1, None, True, legs

    def _slr(bo):   # 5%硬止损口径:利润奔跑不封顶,唯一卖出=收盘跌破入场价-5%即清仓;任何情况都不回补,单腿结束
        legs = [(bo, "买")]
        for t in range(bo + 1, len(cc)):
            if cc[t] <= cc[bo] * 0.95:
                legs.append((t, "止5"))
                return (cc[t] / cc[bo]) * (1 - 2 * COST) - 1, t, False, legs
        return (cc[-1] / cc[bo]) * (1 - 2 * COST) - 1, None, True, legs

    def _pack(fn, bo, d):
        ret, exi, opn, legs = fn(bo)
        exd = None if exi is None else pd.Timestamp(gd[exi])
        hold = int(np.busday_count(pd.Timestamp(d).date(), (exd or pd.Timestamp(sel)).date()))
        rebought = any(k == "补" for _, k in legs)
        legdates = [[str(pd.Timestamp(gd[i]).date()), k, round(float(cc[i]), 3)] for i, k in legs]
        return (round(ret * 100, 0), round(ret, 4),
                None if exd is None else str(exd.date()), opn, hold, rebought, legdates)

    res = []
    for d in dates:
        bo = idxmap.get(pd.Timestamp(d))
        if bo is None:
            continue
        res.append(((ts, str(pd.Timestamp(d).date())), _pack(_m3, bo, d) + _pack(_slr, bo, d)))
    return res


def _czsc_exits(px, top, sel, workers=1):
    """对 top 信号算缠论卖点出场:每股切片后并行跑 _czsc_one。
    返回 {(ts,日期str):(盈亏%, raw, 离场日str或None, 是否持仓中, 持有交易日)}。"""
    want = set(top["ts"].unique())
    ent_dates = {ts: set(top[top["ts"] == ts]["date"]) for ts in want}
    jobs = []
    for ts, g in px[px["ts_code"].isin(want)].groupby("ts_code"):
        g = g.sort_values("trade_date").reset_index(drop=True)
        bos = [i for i, d in enumerate(g["trade_date"]) if d in ent_dates[ts]]
        if not bos:
            continue
        g = g.iloc[max(0, min(bos) - 300):].reset_index(drop=True)
        jobs.append((ts, g["trade_date"].to_numpy(), g["c"].to_numpy(), g["h"].to_numpy(),
                     g["l"].to_numpy(), g["v"].to_numpy(),
                     list(top[top["ts"] == ts]["date"]), sel))
    out = {}
    if workers <= 1 or len(jobs) < 4:
        for j in jobs:
            out.update(dict(_czsc_one(j)))
    else:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for r in ex.map(_czsc_one, jobs):
                out.update(dict(r))
    return _tag_states(out)


def _tag_states(out):
    """逐股票按日期序判定 czstate:锚点(无持仓时的信号)给持仓中/持仓中(回补)/已离场,
    锚点持仓未终止期间再来的信号=加仓(仅标签,信号表无资金概念)。返回加了 state 的 dict。"""
    bykey = {}
    for (ts, ds) in out:
        bykey.setdefault(ts, []).append(ds)
    final = {}
    for ts, dss in bykey.items():
        cur_exit = "flat"       # "flat" | None(持仓中) | pd.Timestamp(已离场日)
        anchor_exit = None      # 当前锚点仓位的离场日 str(持仓中则 None)
        for ds in sorted(dss):
            ret_, raw_, exit_, opn_, hold_, rebought_, legs_, *slr_ = out[(ts, ds)]
            d = pd.Timestamp(ds)
            anchor = (cur_exit == "flat") or (isinstance(cur_exit, pd.Timestamp) and d > cur_exit)
            posinfo = None
            if anchor:
                if opn_:
                    state = "持仓中(回补)" if rebought_ else "持仓中"
                    cur_exit, anchor_exit = None, None
                else:
                    state = None   # 已离场,列显示离场日
                    cur_exit, anchor_exit = pd.Timestamp(exit_), exit_
            else:
                state = "加仓"     # 归属当前锚点仓位,带上锚点的离场日/持仓状态
                posinfo = [anchor_exit, anchor_exit is None]
            final[(ts, ds)] = (ret_, raw_, exit_, opn_, hold_, state, legs_, posinfo, *slr_)
    return final


def _fit_lgb(trf, vaf, seed):
    """用 val 早停训练 LGB,返回(模型, best_iter, 训练AUC, valAUC);val 太小则退回固定200轮无早停。"""
    if len(vaf) < 200 or vaf["label"].nunique() < 2:
        m = lgb.LGBMClassifier(n_estimators=200, random_state=seed, **LGB_PARAMS)
        m.fit(trf[FEATS], trf["label"])
        return m, 200, None, None
    m = lgb.LGBMClassifier(n_estimators=2000, random_state=seed, **LGB_PARAMS)
    m.fit(trf[FEATS], trf["label"], eval_set=[(vaf[FEATS], vaf["label"])], eval_metric="auc",
          callbacks=[lgb.early_stopping(80, verbose=False)])
    bi = m.best_iteration_ or 200
    tr_auc = roc_auc_score(trf["label"], m.predict_proba(trf[FEATS])[:, 1])
    va_auc = roc_auc_score(vaf["label"], m.predict_proba(vaf[FEATS])[:, 1])
    return m, bi, tr_auc, va_auc


def _evaluate_wf(df, seed, tier):
    """照 v6 的多折 train/val/test 逐年前推评估,打印各折 AUC + OOS 汇总 + 过拟合体检 + top档 lift。"""
    lab = df[df["label"] >= 0].copy()
    lab["d"] = lab["date"]
    oos, metas, imps = [], [], []
    print(f"\n=== walk-forward 逐折评估({len(FEATS)}特征,train/val/test 逐年前推)===")
    for i, fd in enumerate(EVAL_FOLDS, 1):
        te = pd.Timestamp(fd["train_end"])
        v0, v1 = pd.Timestamp(fd["val"][0]), pd.Timestamp(fd["val"][1])
        t0, t1 = pd.Timestamp(fd["test"][0]), pd.Timestamp(fd["test"][1])
        trf = lab[lab["d"] <= te - pd.Timedelta(days=EMBARGO_DAYS)]
        vaf = lab[(lab["d"] >= v0) & (lab["d"] <= v1)]
        tef = lab[(lab["d"] >= t0) & (lab["d"] <= t1)]
        if len(trf) < 500 or len(tef) < 50:
            print(f"  折{i} 跳过(样本不足):train={len(trf)} val={len(vaf)} test={len(tef)}")
            continue
        m, bi, tr_auc, va_auc = _fit_lgb(trf, vaf, seed)
        sc = m.predict_proba(tef[FEATS])[:, 1]
        te_auc = roc_auc_score(tef["label"], sc)
        base_f = tef["label"].mean()
        kf = max(1, int(len(tef) * tier / 100))
        picks = tef.assign(s=sc).nlargest(kf, "s")
        tophit_f = picks["label"].mean()
        lift_f = tophit_f / base_f if base_f > 0 else np.nan
        win = picks[picks["label"] == 1]
        med_day = win["cross_day"].median(); med_gain = win["gain_at_cross"].median()
        part = tef[["date", "ts", "label", "maxfwd"]].copy(); part["score"] = sc; part["fold"] = i
        oos.append(part)
        metas.append({"fold": i, "tr_auc": tr_auc, "te_auc": te_auc, "lift": lift_f,
                      "base": base_f, "tophit": tophit_f, "med_day": med_day, "med_gain": med_gain})
        imps.append(pd.Series(m.feature_importances_, index=FEATS))
        gap = "" if tr_auc is None else f"  train_AUC={tr_auc:.4f} gap={tr_auc-te_auc:+.4f}"
        print(f"  折{i}: train≤{fd['train_end']}({len(trf)}) test {fd['test'][0][:4]}({len(tef)})  "
              f"best_iter={bi}  test_AUC={te_auc:.4f}  lift={lift_f:.2f}x{gap}")
    if not oos:
        print("  无可用折,评估终止"); return
    oo = pd.concat(oos, ignore_index=True)
    pooled = roc_auc_score(oo["label"], oo["score"])
    mean_auc = np.mean([x["te_auc"] for x in metas])
    mean_lift = np.nanmean([x["lift"] for x in metas])
    mtr = np.mean([x["tr_auc"] for x in metas if x["tr_auc"] is not None]) if any(x["tr_auc"] for x in metas) else None
    print(f"\n=== 样本外汇总({len(oo)} 样本,{len(metas)} 折)===")
    mean_base = np.mean([x["base"] for x in metas]); mean_top = np.mean([x["tophit"] for x in metas])
    print(f"  ★逐折均值: test_AUC={mean_auc:.4f}  基础率={mean_base:.1%}  top{tier}%命中={mean_top:.1%}  "
          f"lift={mean_lift:.2f}x   (pooled AUC={pooled:.4f})")
    mday = np.nanmean([x["med_day"] for x in metas]); mgain = np.nanmean([x["med_gain"] for x in metas])
    print(f"  ★精选赢家:中位达标用时={mday:.0f}个交易日  中位兑现幅度={mgain:.1%}")
    if mtr is not None:
        print(f"  过拟合体检:训练AUC均值 {mtr:.4f} vs 逐折测试 {mean_auc:.4f}  gap={mtr-mean_auc:+.4f}"
              f"(<0.05健康 / 0.05~0.10可接受 / >0.15警惕)")
    from scipy.stats import spearmanr
    icf = [spearmanr(g["score"], g["maxfwd"], nan_policy="omit").correlation for _, g in oo.groupby("fold")]
    ic_pool = spearmanr(oo["score"], oo["maxfwd"], nan_policy="omit").correlation
    print(f"  ★rank-IC(score↔最大涨幅):逐折均值={np.nanmean(icf):+.3f}±{np.nanstd(icf):.3f}  pooled={ic_pool:+.3f}"
          f"(>0.03有效 / >0.05好 / 逐折同号=稳定)")
    oo["q5"] = pd.qcut(oo["score"], 5, labels=False, duplicates="drop")
    qt = oo.groupby("q5").agg(avgfwd=("maxfwd", "mean"), hit=("label", "mean"), n=("label", "size"))
    mono = qt["avgfwd"].is_monotonic_increasing
    print(f"  ★分位单调(score五档,低→高):平均最大涨幅 {' < '.join(f'{v*100:.0f}%' for v in qt['avgfwd'])}"
          f"  {'✓单调' if mono else '✗非单调'}")
    brier = float(((oo["score"] - oo["label"]) ** 2).mean())
    oo["d10"] = pd.qcut(oo["score"], 10, labels=False, duplicates="drop")
    cal = oo.groupby("d10").agg(pred=("score", "mean"), real=("label", "mean"))
    cal_gap = float((cal["pred"] - cal["real"]).abs().max())
    print(f"  ★概率校准:Brier={brier:.3f}(越小越好)  十分位 |预测-实际| 最大偏离={cal_gap:.3f}"
          f"({'校准好' if cal_gap < 0.1 else '偏离偏大' if cal_gap < 0.2 else '校准差'})")
    imp = pd.concat(imps, axis=1).mean(axis=1).sort_values(ascending=False)
    print("  特征重要度(gain均值)Top15:")
    for k2, vv in imp.head(15).items():
        print(f"    {k2:<10}{vv:8.1f}")


def _build_event_rows(px, db, cyq, mf, mkt, regs, BOARD_IDX, args):
    """逐股检测突破事件并算全部特征/出场模拟(mode/tier 无关的重活,可缓存);存 kstar/full 供按模式重算标签。"""
    rows = []
    for ts, g in px.groupby("ts_code"):
        g = g.sort_values("trade_date").reset_index(drop=True)
        c, h, l, v = g["c"], g["h"], g["l"], g["v"]
        cc = c.to_numpy(); gd = g["trade_date"].to_numpy(); craw = g["c_raw"].to_numpy()
        rs20a = g["rs20"].to_numpy(); rs60a = g["rs60"].to_numpy(); rsturna = g["rsturn"].to_numpy()
        srsa = g["sector_rs"].to_numpy(); lb60a = g["lianban60"].to_numpy(); sheata = g["sector_heat"].to_numpy()
        roea = g["roe"].to_numpy(); npyoya = g["npyoy"].to_numpy(); fcposa = g["fc_pos"].to_numpy()
        if len(cc) < 180:
            continue
        ma20, ma60 = c.rolling(20).mean(), c.rolling(60).mean()
        adx, atr = _adx(h, l, c)
        pos1y = (c - c.rolling(250).min()) / (c.rolling(250).max() - c.rolling(250).min())
        basew = (h.rolling(40).max().shift(1) - l.rolling(40).min().shift(1)) / l.rolling(40).min().shift(1)
        vma = v.rolling(20).mean()
        ret20 = c / c.shift(20) - 1; ret60 = c / c.shift(60) - 1
        dlow = l.rolling(DON_EXIT).min().shift(1).to_numpy()
        bd = "科创" if ts.startswith(("688", "689")) else "创业" if ts[:3] in ("300", "301") else "主板"
        breg = regs.get(BOARD_IDX.get(bd, "沪深300"), {})
        try:
            mfg = mf.loc[ts].reindex(g["trade_date"])
            mf_net20 = (mfg["net_lg"].rolling(20).sum() / mfg["tot"].rolling(20).sum().abs()).values
        except KeyError:
            mf_net20 = np.full(len(cc), np.nan)
        dets = _detect_kernel(cc, args.h, 30) if args.pivot == "kernel" else _detect(cc, args.thr, 30)
        for typ, bo, pvs in dets:
            d = pd.Timestamp(gd[bo])
            if d.year < 2020:
                continue
            endi = min(bo + MW_DAYS, len(cc) - 1)
            fwd = cc[bo + 1:endi + 1] / cc[bo] - 1
            maxfwd = fwd.max() if len(fwd) else np.nan
            kstar = float(np.max(fwd / np.sqrt(np.arange(1, len(fwd) + 1)))) if len(fwd) else np.nan
            full = bo + MW_DAYS < len(cc)
            crossed = (fwd >= MW_HURDLE_K * np.sqrt(np.arange(1, len(fwd) + 1))) if len(fwd) else np.array([False])
            cidx = int(np.argmax(crossed)) if crossed.any() else -1
            cross_day = cidx + 1 if crossed.any() else np.nan
            gain_at_cross = float(fwd[cidx]) if crossed.any() else np.nan
            seg = cc[bo:endi + 1]
            peak_off = int(seg.argmax())
            launch = int(seg[:peak_off + 1].argmin()) if peak_off > 0 else None
            ep = cc[bo]; donret = None; donopen = True; donexit = None
            for t2 in range(bo + 1, len(cc)):
                if not np.isnan(dlow[t2]) and cc[t2] < dlow[t2]:
                    donret = cc[t2] / ep - 1 - 2 * COST; donopen = False; donexit = pd.Timestamp(gd[t2]); break
            if donret is None:
                donret = cc[-1] / ep - 1 - 2 * COST
            atr_e = atr.iloc[bo]
            swret, swopen, swexit, swtp = _swing_exit(cc, gd, bo, atr_e, SW_TPFRAC)
            swsweep = {fr: _swing_exit(cc, gd, bo, atr_e, fr)[0] for fr in SW_TPFRAC_SWEEP}
            try:
                dbr = db.loc[(ts, d)]; cyr = cyq.loc[(ts, d)]
            except KeyError:
                dbr = cyr = None
            mr = mkt.loc[d] if d in mkt.index else None
            rows.append({
                "date": d, "ts": ts, "year": d.year, "maxfwd": maxfwd, "kstar": kstar, "full": full,
                "cross_day": cross_day, "gain_at_cross": gain_at_cross,
                "launch": launch, "typ": typ,
                "donret": donret, "donopen": donopen, "donexit": donexit,
                "swret": swret, "swopen": swopen, "swexit": swexit, "swtp": swtp,
                **{f"sw{int(fr*100)}": swsweep[fr] for fr in SW_TPFRAC_SWEEP},
                "price": craw[bo],
                "ptype": 0 if typ == "N字型" else 1, "brk": cc[bo] / cc[pvs[1]] - 1,
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
                "rs20": rs20a[bo], "rs60": rs60a[bo], "rsturn": rsturna[bo],
                "bregnum": 1.0 if breg.get(d, False) else 0.0,
                "crowd": mr["crowd"] if mr is not None else np.nan,
                "idxdist": mr["idxdist"] if mr is not None else np.nan,
                "lb2rate": mr["lb2rate"] if mr is not None else np.nan,
                "nlb": mr["nlb"] if mr is not None else np.nan,
                "upratio": mr["upratio"] if mr is not None else np.nan,
                "sector_rs": srsa[bo], "lianban60": lb60a[bo], "sector_heat": sheata[bo],
                "roe": roea[bo], "npyoy": npyoya[bo], "fc_pos": fcposa[bo],
            })
    return rows


def _label_by_mode(df):
    """按当前 MW_HURDLE_K 从 kstar/full 重算 label(主升浪命中)/done/cross_day/gain_at_cross,使缓存 df 与模式无关。"""
    hit = df["kstar"].fillna(-9) >= MW_HURDLE_K
    df["label"] = np.where(hit, 1, np.where(df["full"], 0, -1)).astype(int)
    df["done"] = (hit | df["full"]).astype(bool)
    return df


def _score_ref(m, tr):
    """训练期事件的模型分数(升序 float32),存进模型 pkl 作为冻结档位门槛的参考分布。"""
    if not len(tr):
        return np.array([0.0], dtype="float32")
    return np.sort(m.predict_proba(tr[FEATS])[:, 1]).astype("float32")


def _build_events_parallel(px, db, cyq, mf, mkt, regs, BOARD_IDX, args, workers):
    """按股票分块并行跑 _build_event_rows(macOS spawn:每块只 pickle 该块的数据);workers<=1 退串行。"""
    codes = list(px["ts_code"].unique())
    if workers <= 1 or len(codes) < 100:
        return _build_event_rows(px, db, cyq, mf, mkt, regs, BOARD_IDX, args)
    from concurrent.futures import ProcessPoolExecutor
    chunks = [list(x) for x in np.array_split(codes, workers) if len(x)]
    rows = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = []
        for ch in chunks:
            chs = set(ch)
            futs.append(ex.submit(_build_event_rows,
                                  px[px["ts_code"].isin(chs)],
                                  db[db.index.get_level_values(0).isin(chs)],
                                  cyq[cyq.index.get_level_values(0).isin(chs)],
                                  mf[mf.index.get_level_values(0).isin(chs)],
                                  mkt, regs, BOARD_IDX, args))
        for f in futs:
            rows.extend(f.result())
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--thr", type=float, default=0.09)
    ap.add_argument("--n", type=int, default=800)
    ap.add_argument("--start", default="20250101", help="信号起始日 YYYYMMDD")
    ap.add_argument("--end", default=None, help="信号结束日 YYYYMMDD,默认到最新")
    ap.add_argument("--tier", type=int, default=5, help="按模型训练期分数分布的前百分之几筛信号(如 5=top5%%、100=全部);阈值冻结在模型里,不随信号区间变化")
    ap.add_argument("--train", action="store_true", help="重新训练并存盘到 MODEL_PATH;不加则优先加载已存盘模型")
    ap.add_argument("--seed", type=int, default=42, help="训练随机种子(保证可复现)")
    ap.add_argument("--eval", action="store_true", help="跑 walk-forward 多折评估(诚实 OOS),不出 HTML")
    ap.add_argument("--pivot", choices=["zigzag", "kernel"], default="kernel",
                    help="枢轴检测:kernel=核平滑(LMW,带因果滞后,默认)、zigzag=固定%%阈值")
    ap.add_argument("--h", type=float, default=4.0, help="核平滑带宽(仅 --pivot kernel,默认4)")
    ap.add_argument("--json", default=None, help="把结构化数据(信号/横幅/日历)输出到该 JSON 文件(给前后端用)")
    ap.add_argument("--workers", type=int, default=min(8, (os.cpu_count() or 2)), help="特征提取并行进程数(默认~CPU数)")
    ap.add_argument("--mode", choices=["quick", "long"], default="quick",
                    help="主升浪判定模式:quick=高胜率小赚(k=0.06,默认)、long=低胜率大赚(k=0.09)")
    args = ap.parse_args()
    global MW_HURDLE_K, MODEL_PATH, OUT
    MW_HURDLE_K = {"quick": 0.06, "long": 0.09}[args.mode]
    MODEL_PATH = os.path.join(_ROOT, f"swing/ml_signals_2026_model_{args.mode}_{args.pivot}.pkl")
    OUT = os.path.join(_ROOT, f"swing/ml_signals_2026_{args.mode}_{args.pivot}.html")
    start_ts = pd.Timestamp(args.start)
    end_ts = pd.Timestamp(args.end) if args.end else None

    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    con.execute("SET threads TO 1")   # WHY: 多线程浮点聚合顺序不固定致特征不可复现,单线程保证逐次一致
    sel = con.execute("SELECT MAX(trade_date) FROM daily_basic").fetchone()[0]
    uni_date = con.execute("SELECT MAX(trade_date) FROM daily_basic WHERE trade_date<=?",
                           [start_ts.strftime("%Y-%m-%d")]).fetchone()[0] or sel   # 股票池按 start 时点选(非最新日),使固定start的历史信号可复现、去前视池子偏差
    uni_str = uni_date.strftime("%Y%m%d")
    liquid = [r[0] for r in con.execute("""SELECT ts_code FROM daily_basic WHERE trade_date=? AND ts_code NOT LIKE '%.BJ'
        ORDER BY circ_mv DESC LIMIT ?""", [uni_date, args.n]).fetchall()]
    hot_codes = [r[0] for r in con.execute("""SELECT ts_code FROM ths_hot
        WHERE data_type='热股' AND trade_date=? ORDER BY rank LIMIT ?""", [uni_str, HOT_TOP]).fetchall()]
    if hot_codes:
        mvmap = dict(con.execute("SELECT ts_code,circ_mv FROM daily_basic WHERE trade_date=?", [uni_date]).fetchall())
        hot = [c for c in hot_codes if not c.endswith(".BJ") and (mvmap.get(c) or 0) >= HOT_MV_FLOOR]
        liquid = list(dict.fromkeys(liquid + hot))
    names = dict(con.execute("SELECT ts_code,name FROM stock_meta").fetchall())
    px = con.execute("""SELECT d.ts_code,d.trade_date,d.high*a.adj_factor h,d.low*a.adj_factor l,
        d.close*a.adj_factor c,d.close c_raw,d.vol v FROM daily d JOIN adj_factor a ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date
        WHERE d.trade_date>=? AND d.ts_code IN (SELECT UNNEST(?))""", ["2019-01-01", liquid]).fetch_df()
    db = con.execute("""SELECT ts_code,trade_date,pe_ttm,pb,circ_mv,turnover_rate FROM daily_basic
        WHERE trade_date>=? AND ts_code IN (SELECT UNNEST(?))""", ["2019-01-01", liquid]).fetch_df()
    cyq = con.execute("""SELECT ts_code,trade_date,winner_rate,cost_15pct,cost_50pct,cost_85pct FROM cyq_perf
        WHERE trade_date>=? AND ts_code IN (SELECT UNNEST(?))""", ["2019-01-01", liquid]).fetch_df()
    mf = con.execute("""SELECT ts_code,trade_date,
        buy_lg_amount+buy_elg_amount-sell_lg_amount-sell_elg_amount AS net_lg,
        buy_sm_amount+buy_md_amount+buy_lg_amount+buy_elg_amount+sell_sm_amount+sell_md_amount+sell_lg_amount+sell_elg_amount AS tot
        FROM moneyflow WHERE trade_date>=? AND ts_code IN (SELECT UNNEST(?))""", ["2019-01-01", liquid]).fetch_df()
    cr = con.execute("SELECT trade_date, market_crowding_di FROM market_state WHERE market_crowding_di IS NOT NULL ORDER BY trade_date").fetch_df()
    ms = con.execute("""SELECT trade_date, market_idx_dist_h60 AS idxdist, market_2lb_rate_ma5 AS lb2rate,
        market_max_lianban AS nlb, market_crowding_di AS crowd FROM market_state ORDER BY trade_date""").fetch_df()
    breadth = con.execute("""SELECT trade_date, AVG(CASE WHEN pct_chg>0 THEN 1.0 ELSE 0.0 END) AS upratio
        FROM daily WHERE trade_date>=? GROUP BY trade_date""", ["2019-01-01"]).fetch_df()
    sw = dict(con.execute("SELECT ts_code, l1_name FROM sw_member").fetchall())
    lu = con.execute("SELECT ts_code, trade_date FROM limit_list_d WHERE limit_type='U' AND trade_date>=?",
                     ["2019-01-01"]).fetch_df()
    fi = con.execute("""SELECT ts_code, ann_date, roe, netprofit_yoy FROM fina_indicator
        WHERE ann_date>='20200101' AND roe IS NOT NULL
        QUALIFY row_number() OVER (PARTITION BY ts_code, ann_date ORDER BY end_date DESC) = 1""").fetch_df()
    fc = con.execute("""SELECT ts_code, ann_date, type, p_change_min FROM forecast
        WHERE ann_date>='20200101'
        QUALIFY row_number() OVER (PARTITION BY ts_code, ann_date ORDER BY end_date DESC) = 1""").fetch_df()
    con.close()

    env = os.path.join(_ROOT, ".pyenv.local")
    if os.path.exists(env):
        for line in open(env, encoding="utf-8"):
            if line.strip().startswith("TUSHARE_TOKEN") and "=" in line:
                os.environ["TUSHARE_TOKEN"] = line.split("=", 1)[1].strip().strip('"').strip("'")
    import tushare as ts
    ts.set_token(os.environ["TUSHARE_TOKEN"]); pro = ts.pro_api()
    INDEXES = {"沪深300": "000300.SH", "科创综指": "000680.SH", "创业板指": "399006.SZ", "中证1000": "000852.SH"}
    BOARD_IDX = {"主板": "沪深300", "科创": "科创综指", "创业": "创业板指"}
    regs, curs = {}, {}
    for nm, code in INDEXES.items():
        regs[nm], curs[nm] = _idx_regime(pro, code)
    cur_cr = float(cr["market_crowding_di"].iloc[-1]) if len(cr) else float("nan")
    cr_pct = float((cr["market_crowding_di"] <= cur_cr).mean()) if len(cr) else float("nan")
    cr_label = "高(风险大)" if cr_pct > 0.7 else "低(风险小)" if cr_pct < 0.3 else "中"

    for d in (px, db, cyq, mf, ms, breadth):
        d["trade_date"] = pd.to_datetime(d["trade_date"])
    px = px.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    px["xret20"] = px.groupby("ts_code")["c"].transform(lambda s: s / s.shift(20) - 1)
    px["xret60"] = px.groupby("ts_code")["c"].transform(lambda s: s / s.shift(60) - 1)
    px["rs20"] = px.groupby("trade_date")["xret20"].rank(pct=True)
    px["rs60"] = px.groupby("trade_date")["xret60"].rank(pct=True)
    turn = db[["ts_code", "trade_date", "turnover_rate"]].copy()
    turn["rsturn"] = turn.groupby("trade_date")["turnover_rate"].rank(pct=True)
    px = px.merge(turn[["ts_code", "trade_date", "rsturn"]], on=["ts_code", "trade_date"], how="left")
    px["l1"] = px["ts_code"].map(sw)
    sec = px.groupby(["l1", "trade_date"])["xret20"].mean().reset_index(name="sret20")
    sec["sector_rs"] = sec.groupby("trade_date")["sret20"].rank(pct=True)
    px = px.merge(sec[["l1", "trade_date", "sector_rs"]], on=["l1", "trade_date"], how="left")
    lu["trade_date"] = pd.to_datetime(lu["trade_date"]); lu["isU"] = 1.0
    lu["l1"] = lu["ts_code"].map(sw)
    px = px.merge(lu[["ts_code", "trade_date", "isU"]], on=["ts_code", "trade_date"], how="left")
    px["isU"] = px["isU"].fillna(0.0)
    px["lianban60"] = px.groupby("ts_code")["isU"].transform(lambda s: s.rolling(60, min_periods=1).sum())
    alldates = pd.Index(sorted(px["trade_date"].unique()))
    heat = lu.groupby(["l1", "trade_date"]).size().reset_index(name="uc")
    hp = heat.pivot(index="trade_date", columns="l1", values="uc").reindex(alldates).fillna(0.0)
    hr = hp.rolling(5, min_periods=1).sum().rank(axis=1, pct=True)
    sheat = hr.reset_index().melt(id_vars="index", var_name="l1", value_name="sector_heat").rename(columns={"index": "trade_date"})
    px = px.merge(sheat, on=["l1", "trade_date"], how="left")
    px["trade_date"] = px["trade_date"].astype("datetime64[ns]")
    fi["ann_date"] = pd.to_datetime(fi["ann_date"]).astype("datetime64[ns]")
    fi = fi.sort_values(["ann_date", "ts_code"], kind="mergesort")
    px = px.sort_values("trade_date")
    px = pd.merge_asof(px, fi.rename(columns={"netprofit_yoy": "npyoy"}), left_on="trade_date",
                       right_on="ann_date", by="ts_code", direction="backward")
    fc["ann_date"] = pd.to_datetime(fc["ann_date"]).astype("datetime64[ns]")
    fc["fc_good"] = (fc["type"].isin(["预增", "略增", "扭亏", "续盈"]) | (fc["p_change_min"] > 0)).astype(float)
    fc = fc.sort_values(["ann_date", "ts_code"], kind="mergesort")
    px = pd.merge_asof(px, fc[["ts_code", "ann_date", "fc_good"]].rename(columns={"ann_date": "fc_ann"}),
                       left_on="trade_date", right_on="fc_ann", by="ts_code", direction="backward")
    px["fc_pos"] = np.where((px["fc_good"] == 1.0) & ((px["trade_date"] - px["fc_ann"]).dt.days <= 90), 1.0, 0.0)
    px = px.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    mkt = ms.merge(breadth, on="trade_date", how="left").set_index("trade_date")
    db = db.set_index(["ts_code", "trade_date"])
    cyq["cyqconc"] = (cyq["cost_85pct"] - cyq["cost_15pct"]) / cyq["cost_50pct"]
    cyq = cyq.set_index(["ts_code", "trade_date"]); mf = mf.set_index(["ts_code", "trade_date"])

    import pickle
    _evdir = os.path.join(_ROOT, "swing/.evcache")
    _evkey = os.path.join(_evdir, f"{args.n}_{args.pivot}_{args.h}_hot{HOT_TOP}_don2_u{uni_str}_{sel}.pkl")
    if (not args.eval) and os.path.exists(_evkey):
        df = pd.read_pickle(_evkey)
    else:
        df = pd.DataFrame(_build_events_parallel(px, db, cyq, mf, mkt, regs, BOARD_IDX, args, args.workers))
        if not args.eval:
            os.makedirs(_evdir, exist_ok=True); df.to_pickle(_evkey)
    df = _label_by_mode(df)
    if args.eval:
        _evaluate_wf(df, args.seed, args.tier)
        return
    tr = df[(df["date"] < start_ts) & (df["label"] >= 0)]
    te = df[df["date"] >= start_ts].copy()
    if end_ts is not None:
        te = te[te["date"] <= end_ts].copy()
    import pickle
    saved = None
    if not args.train and os.path.exists(MODEL_PATH):
        with open(MODEL_PATH, "rb") as f:
            saved = pickle.load(f)
    if saved is not None:
        m = saved["model"]
        train_metrics = {"loaded": True, "train_cutoff": saved["train_cutoff"], "n_train": saved["n_train"],
                         "best_iter": saved.get("best_iter")}
        print(f"[模型] 加载已存盘模型(训练截止 {saved['train_cutoff']}, 训练样本 {saved['n_train']}, "
              f"seed {saved['seed']})")
        if saved["train_cutoff"] != args.start:
            print(f"  ⚠️ 该模型训练截止={saved['train_cutoff']} ≠ 当前 --start={args.start};"
                  f"若信号区间早于训练截止会有未来信息泄露,建议 --train 重训")
    else:
        val_cut = start_ts - pd.DateOffset(months=VAL_MONTHS)
        trf = tr[tr["date"] < val_cut - pd.Timedelta(days=EMBARGO_DAYS)]; vaf = tr[tr["date"] >= val_cut]
        if len(trf) < 500:
            trf, vaf = tr, tr.iloc[0:0]
        m, bi, tr_auc, va_auc = _fit_lgb(trf, vaf, args.seed)
        train_metrics = {"loaded": False, "best_iter": bi, "n_train": len(trf), "n_val": len(vaf),
                         "tr_auc": round(float(tr_auc), 4) if tr_auc is not None else None,
                         "va_auc": round(float(va_auc), 4) if va_auc is not None else None,
                         "gap": round(float(tr_auc - va_auc), 4) if (tr_auc is not None and va_auc is not None) else None}
        if va_auc is not None:
            print(f"[模型] val早停:训练{len(trf)}/val{len(vaf)}  best_iter={bi}  "
                  f"训练AUC={tr_auc:.4f} valAUC={va_auc:.4f} gap={tr_auc-va_auc:+.4f}")
        else:
            print(f"[模型] val样本不足,退回固定{bi}轮无早停(训练{len(trf)})")
        if args.train:
            with open(MODEL_PATH, "wb") as f:
                pickle.dump({"model": m, "train_cutoff": args.start, "n_train": len(trf),
                             "seed": args.seed, "feats": FEATS, "best_iter": bi,
                             "score_ref": _score_ref(m, trf)}, f)
            print(f"[模型] 已训练并存盘 → {MODEL_PATH}(训练截止 {args.start}, 训练样本 {len(trf)})")
            try:
                shap_path = MODEL_PATH.replace(".pkl", "_shap.png")
                _shap_plot(m, trf[FEATS], shap_path)
                print(f"[模型] SHAP 特征影响图 → {shap_path}")
            except Exception as e:
                print(f"[模型] SHAP 出图失败(跳过):{e}")
        else:
            print(f"[模型] 无存盘模型,本次现训现用(未存盘);加 --train 可存盘复用")
    ref = saved.get("score_ref") if saved is not None else None
    if ref is None:
        ref = _score_ref(m, tr)
        if saved is not None:
            print(f"  ⚠️ 该存盘模型无冻结门槛(旧版pkl),本次按训练期({args.start}前)分数分布现算;建议 --train 重训固化")
    te["score"] = m.predict_proba(te[FEATS])[:, 1]
    cut = lambda t: float(np.quantile(ref, 1 - t / 100))
    s = te["score"].values
    te["tier"] = np.where(s >= cut(1), "top1", np.where(s >= cut(3), "top3",
                 np.where(s >= cut(5), "top5", np.where(s >= cut(10), "top10",
                 np.where(s >= cut(20), "top20", np.where(s >= cut(30), "top30", "其他"))))))
    top = te[s >= cut(args.tier)].sort_values("score", ascending=False)
    print(f"[门槛] 冻结阈值取自模型训练期分数分布:top{args.tier}% ≥ {cut(args.tier):.4f}"
          f"(参考样本{len(ref)}条);阈值只随重训变化,历史信号不会被回溯追加/挤出")

    done = top[top["done"]]
    hit = (done["label"] == 1).mean() if len(done) else float("nan")
    print(f"信号 {args.start}~{args.end or '今'} 共{len(te)}条 | 过top{args.tier}%门槛 {len(top)}条 | "
          f"已满60日{len(done)}条 走出主升浪 {hit*100:.0f}%")

    def _hold(opencol, exitcol):
        days = []
        for _, r in top.iterrows():
            if r[opencol] or pd.isna(r[exitcol]):
                continue
            days.append(np.busday_count(r["date"].date(), r[exitcol].date()))
        return np.mean(days) if days else float("nan")

    print(f"\n{'出场方式':<16}{'平均盈亏':>9}{'中位盈亏':>9}{'胜率':>7}{'仍持仓':>7}{'平均持有日':>10}")
    v = top["donret"].astype(float)
    print(f"{'唐奇安20':<16}{v.mean()*100:>8.1f}%{v.median()*100:>8.1f}%{(v>0).mean()*100:>6.0f}%"
          f"{int(top['donopen'].sum()):>7d}{_hold('donopen', 'donexit'):>9.0f}天")
    print(f"  ── 波段止盈止损,部分止盈比例扫描(超时{SW_STALE}日,其余规则不变)──")
    for fr in SW_TPFRAC_SWEEP:
        v = top[f"sw{int(fr*100)}"].astype(float)
        print(f"  {'+10%平'+str(int(fr*100))+'%':<14}{v.mean()*100:>8.1f}%{v.median()*100:>8.1f}%"
              f"{(v>0).mean()*100:>6.0f}%{'—':>7}{'—':>9}")

    import json
    czx = _czsc_exits(px, top, sel, args.workers)
    lastc = px.groupby("ts_code")["c"].last().to_dict()
    data = []
    for _, r in top.iterrows():
        st = ("已走出主升浪" if r["label"] == 1 else "未达") if r["done"] else "进行中"
        code = r["ts"][:3]
        board = "科创" if r["ts"].startswith(("688", "689")) else "创业" if code in ("300", "301") else "主板"
        cz = czx.get((r["ts"], str(r["date"].date())), (None, None, None, True, None, None, [], None, None, None, None, True, None, None, []))
        data.append({
            "date": str(r["date"].date()), "ts": r["ts"], "name": names.get(r["ts"], ""),
            "board": board, "mkt": "健康" if regs.get(BOARD_IDX.get(board, "沪深300"), {}).get(r["date"], False) else "走坏",
            "tier": r["tier"], "typ": r["typ"], "score": round(float(r["score"]), 3),
            "price": round(float(r["price"]), 2),
            "maxfwd": None if pd.isna(r["maxfwd"]) else round(float(r["maxfwd"]) * 100, 0),
            "donret": None if pd.isna(r["donret"]) else round(float(r["donret"]) * 100, 0),
            "donr": None if pd.isna(r["donret"]) else round(float(r["donret"]), 4),
            "swr": None if pd.isna(r["swret"]) else round(float(r["swret"]), 4),
            "donopen": bool(r["donopen"]),
            "donexit": None if pd.isna(r["donexit"]) else str(r["donexit"].date()),
            "swret": None if pd.isna(r["swret"]) else round(float(r["swret"]) * 100, 0),
            "swopen": bool(r["swopen"]),
            "swexit": None if pd.isna(r["swexit"]) else str(r["swexit"].date()),
            "swtp": None if pd.isna(r["swtp"]) else str(r["swtp"].date()),
            "czret": cz[0], "czr": cz[1], "czexit": cz[2], "czopen": cz[3], "czhold": cz[4],
            "czstate": cz[5], "czlegs": cz[6], "czposinfo": cz[7],
            "slrret": cz[8], "slrr": cz[9], "slrexit": cz[10], "slropen": cz[11], "slrhold": cz[12], "slrlegs": cz[14],
            "czcur": round(float(lastc.get(r["ts"], r["price"])), 3),
            "launch": None if pd.isna(r["launch"]) else int(r["launch"]),
            "hold": int(np.busday_count(r["date"].date(),
                     (r["donexit"] if not pd.isna(r["donexit"]) else pd.Timestamp(sel)).date())),
            "swhold": int(np.busday_count(r["date"].date(),
                     (r["swexit"] if not pd.isna(r["swexit"]) else pd.Timestamp(sel)).date())),
            "status": st,
        })
    djson = json.dumps(data, ensure_ascii=False)
    latest_td = str(sel)
    _cal = px["trade_date"].drop_duplicates()
    _end = end_ts or pd.Timestamp(sel)
    ntrade = int(((_cal >= start_ts) & (_cal <= _end)).sum())
    cal_js = [str(pd.Timestamp(d).date()) for d in sorted(_cal) if start_ts <= d <= _end]
    latest_px = px["trade_date"].max()
    ml_fc = []
    for ts, g in px.groupby("ts_code"):
        g = g.sort_values("trade_date")
        if g["trade_date"].iloc[-1] != latest_px or len(g) < 60:
            continue
        cc = g["c"].to_numpy(); craw = g["c_raw"].to_numpy()
        ratio = float(craw[-1]) / float(cc[-1])
        pends = pending_breakouts(cc, args.thr)
        if not pends:
            continue
        typ = "/".join(t for t, _, _ in pends)   # 同股 N/W 都成立则合并
        _, brk, _pv = min(pends, key=lambda p: p[1])   # 取最近的突破价(现价固定,brk 越小越近)
        ml_fc.append({"code": ts, "name": names.get(ts, ""), "typ": typ,
                      "price": round(float(craw[-1]), 2), "trig": round(float(brk) * ratio, 2),
                      "dist": round((float(brk) / float(cc[-1]) - 1) * 100, 1),
                      "pct": round((float(cc[-1]) / float(cc[-2]) - 1) * 100, 1)})
    ml_fc.sort(key=lambda x: x["dist"])
    if args.json:
        payload = {"mode": args.mode, "tier": args.tier, "score_cut": round(cut(args.tier), 4),
                   "start": args.start, "end": args.end or "",
                   "pivot": args.pivot, "latest": latest_td, "ntrade": ntrade, "cal": cal_js,
                   "signals": data, "ml_forecast": ml_fc, "metrics": train_metrics,
                   "banner": {"indices": {nm: curs[nm] for nm in INDEXES},
                              "crowd": {"value": None if cur_cr != cur_cr else round(cur_cr, 3),
                                        "pct": None if cr_pct != cr_pct else round(cr_pct, 2), "label": cr_label}}}
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        print(f"JSON:{args.json}")
    html = f"""<!doctype html><html lang=zh><head><meta charset=utf-8><title>{args.mode}·top{args.tier}%·{args.start}~{args.end or '今'}·{args.pivot}</title>
<link rel=stylesheet href="https://unpkg.com/antd@4.24.15/dist/antd.min.css">
<style>body{{font-family:-apple-system,"PingFang SC",sans-serif;max-width:1850px;margin:22px auto;padding:0 18px;color:#222}}
.today{{background:#eef6ff;border:1px solid #b6d4fe;border-radius:8px;padding:10px 14px;margin:10px 0}}
.today .h{{font-weight:700;margin-bottom:6px}} .today .it{{display:inline-block;margin:3px 10px 3px 0;font-size:13px}}
h1{{font-size:20px}} .pos{{color:#c0392b}} .neg{{color:#27ae60}}
.note{{background:#fff8e1;border:1px solid #ffe082;border-radius:6px;padding:10px;font-size:13px;color:#6d4c41;margin:10px 0}}</style>
</head><body>
<h1>ML 主升浪信号清单 — {args.start} ~ {args.end or '今'}(top{args.tier}%)</h1>
<p style="font-size:15px;margin-bottom:4px">当前大盘状态:&nbsp;
{''.join(f'<b>{nm}</b> <span style="color:{chr(35)+("c0392b" if curs[nm]=="健康" else "27ae60")}">{curs[nm]}</span>&nbsp;&nbsp;' for nm in INDEXES)}
&nbsp;|&nbsp; 抱团度风险 <b>{cur_cr:.4f}</b> <span style="color:{'#c0392b' if cr_pct>0.7 else '#27ae60' if cr_pct<0.3 else '#888'}">{cr_label}</span>(历史分位 {cr_pct*100:.0f}%)</p>
<p style="font-size:12px;color:#999;margin-top:0">健康/走坏=同小西西弗牛熊开关:走坏=MA30与MA60同时走坏(收盘&lt;均线且均线下行),至少一条多头即健康(走坏时突破成功率显著下降)。抱团度=残差互信息系统性风险因子,越高=资金越抱团/系统性风险越大。</p>
<p style="font-size:12px;color:#999;margin-top:0"><b>出场口径</b>(均从突破日入场、扣双边费):
<b>唐奇安</b>=持有至跌破唐奇安20日下轨(过去20日最低)即离场,否则一直持有(让利润奔跑);
<b>波段止盈止损</b>=四开关任一触发:①硬止损 跌破 入场×0.9 与 入场−1×ATR 取更低;②涨到 入场×1.1 与 入场+2×ATR 取更低 时平50%(部分止盈);③涨过+3%激活、从最高点回落5%的跟踪止损;④持满20日超时平仓。</p>
<p>模型用 {args.start} 之前数据训练,打分该区间信号 | 共 {len(te)} 条,过 top{args.tier}% 冻结门槛(score≥{cut(args.tier):.4f},取自训练期分数分布,只随重训变化){len(top)} 条 |
已满60日的 {len(done)} 条中走出主升浪(≥50%) {hit*100:.0f}% | 档位列: top5/top10/top20/top30</p>
<div class=note><b>⚠️</b> "至今最大涨幅"=突破日到现在(或满60日)的最高浮盈;"持仓时间"=唐奇安出场口径下从突破日到离场日的交易日数(仍持仓算到最新交易日);
"进行中"=该出场口径下尚未离场(仍持仓)的条数。点表头可排序。模型严格用2026前数据训练,无未来函数。</div>
<div id=root></div>
<script src="https://unpkg.com/react@17/umd/react.production.min.js"></script>
<script src="https://unpkg.com/react-dom@17/umd/react-dom.production.min.js"></script>
<script src="https://unpkg.com/moment@2.29.4/min/moment.min.js"></script>
<script src="https://unpkg.com/antd@4.24.15/dist/antd.min.js"></script>
<style>.cards{{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0}}
.card{{flex:1;min-width:150px;background:#fafafa;border:1px solid #eee;border-radius:8px;padding:12px}}
.card .v{{font-size:22px;font-weight:700}} .card .l{{font-size:13px;color:#444;margin-top:2px}}
.card .calc{{font-size:11px;color:#999;margin-top:4px;line-height:1.4}}</style>
<script>
var DATA={djson};
var LATEST="{latest_td}";
var NTRADE={ntrade};
var CAL={json.dumps(cal_js)};
var e=React.createElement;
function portfolio(rows,exk,retk){{
 var init=150000,nslot=4,buys={{}};
 rows.forEach(function(r){{ if(r[retk]==null) return; var ex=r[exk]||LATEST;
   (buys[r.date]=buys[r.date]||[]).push({{ts:r.ts,ex:ex,ret:r[retk],sc:r.score}}); }});
 var cash=init,op=[],curve=[];
 for(var i=0;i<CAL.length;i++){{ var d=CAL[i],keep=[];
   for(var j=0;j<op.length;j++){{ if(op[j].ex<=d) cash+=op[j].amt*(1+op[j].ret); else keep.push(op[j]); }}
   op=keep;
   var bs=(buys[d]||[]).slice().sort(function(a,b){{return b.sc-a.sc;}});
   for(var k=0;k<bs.length;k++){{ if(op.length>=nslot) break;
     if(op.some(function(p){{return p.ts===bs[k].ts;}})) continue;
     var lk=0;op.forEach(function(p){{lk+=p.amt;}}); var unit=(cash+lk)/nslot;
     if(cash+1e-6>=unit&&unit>0){{ cash-=unit; op.push({{ts:bs[k].ts,ex:bs[k].ex,ret:bs[k].ret,amt:unit}}); }} }}
   var lk2=0;op.forEach(function(p){{lk2+=p.amt;}}); curve.push([d,Math.round(cash+lk2)]); }}
 return curve;
}}
function svgChart(title,series,init){{
 var W=1760,H=260,pad=52;
 var vals=series.map(function(p){{return p[1];}});
 var mn=Math.min.apply(null,vals),mx=Math.max.apply(null,vals);
 if(mn===mx){{mn=mn*0.99;mx=mx*1.01;}}
 var n=series.length;
 var X=function(i){{return pad+i*(W-2*pad)/Math.max(1,n-1);}};
 var Y=function(v){{return H-pad-(v-mn)*(H-2*pad)/(mx-mn);}};
 var d='';for(var i=0;i<n;i++){{d+=(i?'L':'M')+X(i).toFixed(1)+','+Y(series[i][1]).toFixed(1);}}
 var by=Y(init);
 var last=series[n-1][1],ret=((last/init-1)*100).toFixed(1),up=last>=init;
 var col=up?'#c0392b':'#27ae60';
 var lbl='<text x="'+pad+'" y="20" font-size="14" font-weight="700">'+title+'  期末 '+Math.round(last).toLocaleString()+'  ('+(up?'+':'')+ret+'%)</text>';
 var ax='<line x1="'+pad+'" y1="'+by+'" x2="'+(W-pad)+'" y2="'+by+'" stroke="#bbb" stroke-dasharray="4 4"/>';
 ax+='<text x="'+(W-pad+2)+'" y="'+(by+4)+'" font-size="11" fill="#999">本金'+(init/10000)+'万</text>';
 ax+='<text x="6" y="'+(Y(mx)+4)+'" font-size="11" fill="#999">'+Math.round(mx).toLocaleString()+'</text>';
 ax+='<text x="6" y="'+(Y(mn)+4)+'" font-size="11" fill="#999">'+Math.round(mn).toLocaleString()+'</text>';
 var months=[];
 for(var ii=0;ii<n;ii++){{ if(ii===0||series[ii][0].slice(0,7)!==series[ii-1][0].slice(0,7)) months.push(ii); }}
 var step=Math.ceil(months.length/12);
 for(var mi=0;mi<months.length;mi+=step){{ var gi=months[mi]; var gx=X(gi);
   ax+='<line x1="'+gx+'" y1="'+(H-pad)+'" x2="'+gx+'" y2="'+(H-pad+4)+'" stroke="#ccc"/>';
   ax+='<text x="'+gx+'" y="'+(H-8)+'" font-size="10" fill="#999" text-anchor="middle">'+series[gi][0].slice(0,7)+'</text>'; }}
 return '<svg width="'+W+'" height="'+H+'" style="max-width:100%;border:1px solid #eee;border-radius:8px;background:#fff">'+lbl+ax+'<path d="'+d+'" fill="none" stroke="'+col+'" stroke-width="1.8"/></svg>';
}}
var cols=[
 {{title:'突破日',dataIndex:'date',defaultSortOrder:'descend',sorter:function(a,b){{return a.date<b.date?-1:1;}}}},
 {{title:'板块',dataIndex:'board',filters:[{{text:'主板',value:'主板'}},{{text:'科创',value:'科创'}},{{text:'创业',value:'创业'}}],onFilter:function(v,r){{return r.board===v;}}}},
 {{title:'突破日板块大盘',dataIndex:'mkt',filters:[{{text:'健康',value:'健康'}},{{text:'走坏',value:'走坏'}}],onFilter:function(v,r){{return r.mkt===v;}},
   render:function(v){{return e('span',{{className:v==='健康'?'pos':'neg'}},v);}}}},
 {{title:'档位/ML分',dataIndex:'score',defaultSortOrder:'descend',sorter:function(a,b){{return a.score-b.score;}},
   filters:[{{text:'top1',value:'top1'}},{{text:'top3',value:'top3'}},{{text:'top5',value:'top5'}},{{text:'top10',value:'top10'}},{{text:'top20',value:'top20'}},{{text:'top30',value:'top30'}}],onFilter:function(v,r){{return r.tier===v;}},
   render:function(v,r){{return e('span',null,e('b',null,r.tier),' '+v);}}}},
 {{title:'代码',dataIndex:'ts'}},
 {{title:'名称',dataIndex:'name'}},
 {{title:'价格',dataIndex:'price',sorter:function(a,b){{return a.price-b.price;}},render:function(v){{return v+'元';}}}},
 {{title:'形态',dataIndex:'typ',filters:[{{text:'N字型',value:'N字型'}},{{text:'W型',value:'W型'}}],onFilter:function(v,r){{return r.typ===v;}}}},
 {{title:'至今最大涨幅',dataIndex:'maxfwd',sorter:function(a,b){{return (a.maxfwd||-999)-(b.maxfwd||-999);}},
   render:function(v){{return v==null?'—':e('span',{{className:v>=0?'pos':'neg'}},(v>=0?'+':'')+v+'%');}}}},
 {{title:'唐奇安止损盈亏',dataIndex:'donret',sorter:function(a,b){{return (a.donret==null?-999:a.donret)-(b.donret==null?-999:b.donret);}},
   render:function(v,r){{return v==null?'—':e('span',{{className:v>=0?'pos':'neg'}},(v>=0?'+':'')+v+'%'+(r.donopen?'(持仓中)':''));}}}},
 {{title:'离场日',dataIndex:'donexit',sorter:function(a,b){{return (a.donexit||'')<(b.donexit||'')?-1:1;}},render:function(v,r){{return (v||'持仓中')+'('+r.hold+'天)';}}}},
 {{title:'波段止盈止损盈亏',dataIndex:'swret',sorter:function(a,b){{return (a.swret==null?-999:a.swret)-(b.swret==null?-999:b.swret);}},
   render:function(v,r){{return v==null?'—':e('span',{{className:v>=0?'pos':'neg'}},(v>=0?'+':'')+v+'%'+(r.swopen?'(持仓中)':''));}}}},
 {{title:'波段离场日',dataIndex:'swexit',sorter:function(a,b){{return (a.swexit||'')<(b.swexit||'')?-1:1;}},render:function(v,r){{return (v||'持仓中')+'('+r.swhold+'天)';}}}},
 {{title:'缠论M3盈亏',dataIndex:'czret',sorter:function(a,b){{return (a.czret==null?-999:a.czret)-(b.czret==null?-999:b.czret);}},
   render:function(v,r){{return v==null?'—':e('span',{{className:v>=0?'pos':'neg'}},(v>=0?'+':'')+v+'%'+(r.czopen?'(持仓中)':''));}}}},
 {{title:'缠论M3终止日',dataIndex:'czexit',sorter:function(a,b){{return (a.czexit||'')<(b.czexit||'')?-1:1;}},render:function(v,r){{return r.czret==null?'—':(v||'持仓中')+(r.czhold!=null?'('+r.czhold+'天)':'');}}}}
];
function card(v,l,calc){{return e('div',{{className:'card'}},e('div',{{className:'v'}},v),e('div',{{className:'l'}},l),e('div',{{className:'calc'}},calc));}}
function App(){{
 var s=React.useState(true),showKc=s[0],setKc=s[1];
 var s2=React.useState(true),showCy=s2[0],setCy=s2[1];
 var s3=React.useState(false),only50=s3[0],set50=s3[1];
 var rows=DATA.filter(function(r){{return (showKc||r.board!=='科创')&&(showCy||r.board!=='创业')&&(!only50||r.price<=50);}});
 var done=rows.filter(function(r){{return r.status!=='进行中';}});
 var hit=done.filter(function(r){{return r.status==='已走出主升浪';}});
 var donw=rows.filter(function(r){{return r.donret!=null;}});
 var donwin=donw.length?(donw.filter(function(r){{return r.donret>0;}}).length/donw.length*100).toFixed(0)+'%':'—';
 var sww=rows.filter(function(r){{return r.swret!=null;}});
 var swwin=sww.length?(sww.filter(function(r){{return r.swret>0;}}).length/sww.length*100).toFixed(0)+'%':'—';
 var fwds=rows.filter(function(r){{return r.maxfwd!=null;}}).map(function(r){{return r.maxfwd;}});
 var avgfwd=fwds.length?(fwds.reduce(function(a,b){{return a+b;}},0)/fwds.length).toFixed(1)+'%':'—';
 var avg=function(a){{return a.length?(a.reduce(function(x,y){{return x+y;}},0)/a.length).toFixed(1)+'%':'—';}};
 var dons=rows.filter(function(r){{return r.donret!=null;}}).map(function(r){{return r.donret;}});
 var avgdon=avg(dons); var avgdondd=avg(dons.filter(function(v){{return v<0;}}));
 var sws=rows.filter(function(r){{return r.swret!=null;}}).map(function(r){{return r.swret;}});
 var avgsw=avg(sws); var avgswdd=avg(sws.filter(function(v){{return v<0;}}));
 var hd=rows.filter(function(r){{return r.hold!=null;}}).map(function(r){{return r.hold;}});
 var avghold=hd.length?(hd.reduce(function(a,b){{return a+b;}},0)/hd.length).toFixed(0)+'天':'—';
 var swhd=rows.filter(function(r){{return r.swhold!=null;}}).map(function(r){{return r.swhold;}});
 var avgswhold=swhd.length?(swhd.reduce(function(a,b){{return a+b;}},0)/swhd.length).toFixed(0)+'天':'—';
 var donon=rows.filter(function(r){{return r.donopen;}}).length;
 var swon=rows.filter(function(r){{return r.swopen;}}).length;
 var perday=NTRADE?(rows.length/NTRADE).toFixed(2):'—';
 var today=LATEST;
 var todays=rows.filter(function(r){{return r.date===today;}}).sort(function(a,b){{return b.score-a.score;}});
 var sells=rows.filter(function(r){{return r.donexit===today||r.swexit===today||r.swtp===today;}});
 var todayEl=e('div',{{className:'today'}},
   e('div',{{className:'h'}},'最新交易日('+(today||'-')+') · 买入 '+todays.length+' 条 / 需卖出 '+sells.length+' 条'),
   e('div',null, e('b',null,'买入:'), ' ',
     todays.length? todays.map(function(r){{return e('span',{{className:'it',key:'b'+r.ts}},
       e('b',null,r.name+'('+r.ts+')'),' ',r.tier,' ML'+r.score,' ',
       e('span',{{className:r.mkt==='健康'?'pos':'neg'}},'['+r.board+r.mkt+']'));}})
       : e('span',{{style:{{color:'#999'}}}},'无')),
   e('div',{{style:{{marginTop:6}}}}, e('b',null,'需卖出:'), ' ',
     sells.length? sells.map(function(r){{var t=[];if(r.donexit===today)t.push('唐奇安清仓');if(r.swexit===today)t.push('波段清仓');if(r.swtp===today)t.push('波段部分止盈(卖50%)');
       return e('span',{{className:'it',key:'s'+r.ts}},
       e('b',null,r.name+'('+r.ts+')'),' ',e('span',{{className:'neg'}},t.join('/')));}})
       : e('span',{{style:{{color:'#999'}}}},'无')));
 return e('div',null,
  todayEl,
  e('div',{{style:{{margin:'8px 0'}}}},
    e(antd.Checkbox,{{checked:showKc,onChange:function(ev){{setKc(ev.target.checked);}}}},'显示科创板(688/689)'),
    e('span',{{style:{{marginLeft:16}}}}),
    e(antd.Checkbox,{{checked:showCy,onChange:function(ev){{setCy(ev.target.checked);}}}},'显示创业板(300/301)'),
    e('span',{{style:{{marginLeft:16}}}}),
    e(antd.Checkbox,{{checked:only50,onChange:function(ev){{set50(ev.target.checked);}}}},'只看 ≤50元/股')),
  e('div',{{className:'cards'}},
    card(rows.length,'信号数','当前筛选下的信号条数'),
    card(perday,'平均每日信号','信号数 ÷ 区间总交易日数('+rows.length+'/'+NTRADE+')'),
    e('div',{{className:'card'}},
      e('div',{{style:{{display:'flex',gap:14}}}},
        e('div',null,e('div',{{className:'v'}},donwin),e('div',{{className:'l'}},'唐奇安胜率')),
        e('div',null,e('div',{{className:'v'}},swwin),e('div',{{className:'l'}},'波段止盈止损胜率'))),
      e('div',{{className:'calc'}},'各自出场口径下盈亏>0 的占比(含持仓中按现价);唐奇安='+donw.length+'条,波段='+sww.length+'条')),
    card(avgfwd,'平均最大涨幅','所有信号突破后至今(或满60日)最高浮盈的均值;非实际买卖收益'),
    e('div',{{className:'card'}},
      e('div',{{style:{{display:'flex',gap:14}}}},
        e('div',null,e('div',{{className:'v pos'}},avgdon),e('div',{{className:'l'}},'平均盈亏')),
        e('div',null,e('div',{{className:'v neg'}},avgdondd),e('div',{{className:'l'}},'平均回撤'))),
      e('div',{{className:'calc'}},'唐奇安出场:平均盈亏=所有信号均值(红);平均回撤=亏损信号的平均亏幅(绿)。扣双边费,持仓中按现价')),
    e('div',{{className:'card'}},
      e('div',{{style:{{display:'flex',gap:14}}}},
        e('div',null,e('div',{{className:'v pos'}},avgsw),e('div',{{className:'l'}},'平均盈亏')),
        e('div',null,e('div',{{className:'v neg'}},avgswdd),e('div',{{className:'l'}},'平均回撤'))),
      e('div',{{className:'calc'}},'波段止盈止损出场:平均盈亏=所有信号均值(红);平均回撤=亏损信号的平均亏幅(绿)。扣双边费,持仓中按现价')),
    e('div',{{className:'card'}},
      e('div',{{style:{{display:'flex',gap:14}}}},
        e('div',null,e('div',{{className:'v'}},avghold),e('div',{{className:'l'}},'唐奇安持仓')),
        e('div',null,e('div',{{className:'v'}},avgswhold),e('div',{{className:'l'}},'波段持仓'))),
      e('div',{{className:'calc'}},'各自出场口径下从突破日到离场日的交易日均值(仍持仓算到最新交易日)')),
    e('div',{{className:'card'}},
      e('div',{{style:{{display:'flex',gap:14}}}},
        e('div',null,e('div',{{className:'v'}},donon),e('div',{{className:'l'}},'唐奇安进行中')),
        e('div',null,e('div',{{className:'v'}},swon),e('div',{{className:'l'}},'波段进行中'))),
      e('div',{{className:'calc'}},'各自出场口径下尚未离场(仍持仓)的条数'))),
  e('div',{{style:{{margin:'10px 0 4px',fontSize:13,color:'#666'}}}},
    '组合回测:15万本金分4份,出现信号占一份买入(每天最多4只、无空位则放弃),按对应出场法平仓,费率已计'),
  e('div',{{style:{{marginBottom:10}}}},e('div',{{dangerouslySetInnerHTML:{{__html:svgChart('唐奇安出场',portfolio(rows,'donexit','donr'),150000)}}}})),
  e('div',{{style:{{marginBottom:14}}}},e('div',{{dangerouslySetInnerHTML:{{__html:svgChart('波段止盈止损出场',portfolio(rows,'swexit','swr'),150000)}}}})),
  e(antd.Table,{{columns:cols,dataSource:rows,rowKey:function(r){{return r.ts+r.date;}},size:'small',pagination:{{pageSize:30}}}})
 );
}}
ReactDOM.render(e(App),document.getElementById('root'));
</script>
</body></html>"""
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"报告:{OUT}")


if __name__ == "__main__":
    main()
