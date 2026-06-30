# 标准模型训练 / 评估流程(金融预测信号模型)

> 本仓库所有「模型相关」工作的准绳。目标:**正确性(无泄露)、稳健性(跨时间/环境不崩)、鲁棒性(对扰动不敏感)**。
> 对象:`run_ml_signals_2026.py` 的 LightGBM(score = P(主升浪))。每次改模型/特征/标签/切分,按本流程逐相走;❌ 项未过不得上线。

---

## Phase 0 — 数据与标签正确性(Correctness)【一票否决】
- [x] **无未来函数**:特征只用 t 及之前;label 只用 t 之后。基本面 merge_asof backward;label = 前向 60 日 kstar。
- [x] **PIT 对齐**:财务用 ann_date、新闻/研报按决策日截断。
- [⚠] **横截面参照系**:rs20/rsturn/sector_rs 等按池内当日 rank → **池子(--n / 热榜)变化会改变同股特征值**,换池必重训,缓存 key 须含池标识。
- [x] **标签可复现**:主升浪 = 60日内 cumret ≥ k√t(quick0.06/long0.09),kstar 口径固定。
- [ ] 样本去重 / 同股同日不重复;缺失值处理一致(训练=推理)。

## Phase 1 — 数据切分(防泄露)【一票否决】
- [x] **时序 walk-forward**:train < val < test 逐年前推(`_evaluate_wf`),禁用随机 KFold。
- [⚠] **Embargo(关键易漏)**:label 有 60 交易日前瞻窗 → train 末尾与 test 之间必须留 **≥60 交易日 embargo**,否则训练样本的 label 窗口探入测试期 = 泄露。**需核查/补上**。
- [x] **val 早停**:val 独立于 test,early stopping 只看 val。

## Phase 2 — 区分力(Discrimination)
- [x] 逐折 + OOS pooled **AUC**。
- [x] **top档 lift / 命中率**(实战只买尖端,必看)。
- [ ] **rank-IC + IC 衰减**:score 与前向收益的秩相关 + 随持有期衰减 → 测全程单调预测力(零依赖,scipy 可加)。
- [ ] **分位单调性**:按 score 分 5/10 档,收益应单调递增。

## Phase 3 — 概率质量(Calibration)
- [ ] **校准曲线 + Brier 分数**:预测概率 0.7 是否真对应 ~70% 命中(sklearn,零安装)。

## Phase 4 — 过拟合检验(Overfitting)【gap>0.15 一票否决】
- [x] **train vs test AUC gap**(<0.05健康 / 0.05~0.10可接受 / >0.15警惕)。
- [ ] **PBO / CSCV**(回测过拟合概率):多配置(quick/long×pivot×tier×池子×出场)选优时,PBO>0.5 说明"最优"是过拟合(mlfinlab/portfoliolab)。
- [ ] **Deflated / Probabilistic Sharpe**:扣除多次试验的运气后,收益是否仍显著。

## Phase 5 — 稳健性 / 鲁棒性(Robustness)
- [x] **跨市场环境**:牛/熊/震荡分桶(已跑熊市)→ 各环境都不应崩。
- [△] **跨时间稳定性**:逐折指标方差小(已有逐折,未量化方差)。
- [△] **参数敏感性**:对 k / h / tier / 阈值微扰结果平滑(A/B 过,未系统化)。
- [ ] **多 seed 方差**:换随机种子 AUC/lift 波动小。
- [ ] **特征鲁棒**:去掉任一 top 特征,性能不崩(probatus/SHAP 消融)。

## Phase 6 — 可解释性(Attribution)
- [x] **SHAP** 特征影响图(`--train` 出 `_shap.png`,中英双标)。
- [ ] 特征重要性随时间漂移监控(某特征突然主导=数据问题预警)。

## Phase 7 — 上线 sign-off 清单
全部通过才可用于实盘信号:
1. ❌ 无任何泄露(Phase 0/1 全绿,含 embargo)。
2. ❌ 逐折 test_AUC 显著 > 0.5 且 top档 lift > 1.5。
3. ❌ 过拟合 gap ≤ 0.15 且(若做了)PBO ≤ 0.5。
4. ❌ 概率校准无系统性偏离。
5. ❌ 跨环境/多 seed 不崩(无某一年/某 seed 暴雷)。

---

## 全流程体检结果(2026-06,quick / kernel)
按本流程跑了一遍,工具落地:`--eval`(rank-IC/分位/校准已内置)+ `model_robustness.py`(PBO/DSR)。

| Phase | 指标 | 结果 | 判定 |
|---|---|---|---|
| 1 泄露 | 补 train↔val embargo 90天后 test_AUC | 0.6880→0.6885 几乎不变 | ✅ 原指标未泄露 |
| 2 区分力 | 逐折 test_AUC / top档 lift | ~0.69 / 1.5–2.0x | ✅ |
| 2 IC | rank-IC 逐折 | +0.187(每折同号,±0.005跨seed) | ✅ 强且稳 |
| 2 单调 | score五档平均涨幅 | 顶档≫底档但中段塌 | △ 极端有效、非严格单调 |
| 3 校准 | Brier / 十分位最大偏离 | 0.177 / 0.101 | △ 概率未校准,只宜排序选top |
| 4 过拟合 | 训练vs测试 gap | +0.09~0.16(2025折偏高) | △ 可接受偏上 |
| 4 PBO | CSCV(9配置×42月) | 0.13 | ✅ 选优非运气 |
| 4 DSR | 最优配置 Deflated Sharpe | 1.00(N=9) | ✅(N偏小,真实更保守) |
| 5 多seed | test_AUC / lift 跨5种子 | ±0.003 / ±0.11 | ✅ 不挑种子 |
| 5 跨环境 | 逐年(2022熊~2025强) | AUC>0.67、lift>1.5 全绿 | ✅ 无环境暴雷 |
| 6 可解释 | SHAP | crowd/市值/指数距高 主导 | ✅ |

**总评:正确性✅(embargo补齐无泄露)、稳健性✅(跨年/seed不崩)、鲁棒性✅(PBO 0.13)。两个待改善:概率校准(Phase3)、中段单调性,均属"锦上添花"而非否决项。**

待补(优先级低):概率校准(isotonic/Platt)、特征消融鲁棒性、参数敏感性系统化、IC多horizon衰减。

## 现状小结(2026-06)
- **已具备**:无未来函数、walk-forward、AUC/lift/命中、过拟合 gap、熊市验证、SHAP。基础扎实。
- **最该补(按优先级)**:
  1. **Embargo ≥60 交易日**(Phase 1)—— 正确性,最高优先,可能存在泄露。
  2. **rank-IC + 校准/Brier**(Phase 2/3)—— 零安装,直接复用 OOS 预测。
  3. **PBO + Deflated Sharpe**(Phase 4)—— 你 A/B 极多,这是防"选出来的最优=过拟合"的核心。
- 推荐库:`alphalens-reloaded`(因子IC)、`mlfinlab`/`portfoliolab`(PBO/DSR/purged-CV)、`probatus`(分类器稳健性)、`sklearn`(校准,已装)。
