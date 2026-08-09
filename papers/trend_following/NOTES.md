# Sepp & Lucic (2026) — The Science and Practice of Trend-Following Systems

- 原文：`sepp_lucic_2026_trend_following.pdf`（arXiv 2607.19497，2026-07-20，46 页）
- 全文文本：`fulltext.txt`
- 官方代码：https://github.com/ArturSepp/TrendFollowingSystems （Python 包 `trendfollowing`，含 84 个期货合约 1959–2026 数据）
- 核对状态：式 2.4–7.5 全部主结果已逐页对照原文 PDF 核实（2026-08-09）

---

## 0. 一句话

把趋势跟踪从"回测调参"变成"由自相关函数 + 漂移闭式算夏普"。在 84 个流动期货上，公式预测夏普 vs 实现夏普 **pooled corr = 0.99、slope = 0.96**——但**这是样本内归因，不是样本外预测**（原文 p.29 明说 out-of-sample 留待将来）。

---

## 1. 三类系统（Definition 3.1）

| 类型 | 信号 | 仓位 | 调仓 |
|---|---|---|---|
| European | EWMA 滤波作用于波动率归一化收益 | 连续，正比信号强度 | 日频 |
| American | 两条 EWMA 交叉 + ATR 缓冲进场 + ATR 跟踪止损 | 二值，建仓时固定 | 事件驱动 |
| TSMOM | M 期 × L 日 的波动率归一化收益符号加总 | 归一化符号和 | 周–月频，推荐 L∈(5,30) |

全部理论围绕 European 展开（连续信号才有闭式解），另两个靠实证映射过去。

---

## 2. 核心构造

波动率估计（2.13）：$\sigma_t^{return}=\sqrt{\mathcal L^{(\nu_\sigma)}(r_t^2)}$，实证用 **span = 33 天**。

归一化收益（2.14）：
$$z_t = r_t/\sigma_{t-1}$$
**归一化在滤波之前**，顺序不可换。

EWMA（2.4–2.5）：$\mathcal L^{(\nu)}(y_t)=(1-\nu)\sum_{m\ge0}\nu^m y_{t-m}$，$\ \nu = 1-\dfrac{2}{\text{span}+1}$

方差保持滤波（2.7）：$\tilde{\mathcal L}^{(\nu)} = \sqrt{\dfrac{1+\nu}{1-\nu}}\,\mathcal L^{(\nu)}$ ——独立输入下输出方差 = 输入方差。

长短滤波（2.9–2.10）：$\widetilde{\mathcal{LS}}^{(\nu_1,\nu_2)} = l_1\mathcal L^{(\nu_1)} - l_2\mathcal L^{(\nu_2)}$
$$l_1=\frac{q}{1-\nu_1},\quad l_2=\frac{q}{1-\nu_2},\quad q=\Big(\tfrac{1}{1-\nu_1^2}+\tfrac{1}{1-\nu_2^2}-\tfrac{2}{1-\nu_1\nu_2}\Big)^{-1/2}$$

信号与仓位（4.1–4.2）：
$$S_t=\tilde{\mathcal L}^{(\nu)}(z_t),\qquad w_t = S_t\cdot\frac{\sigma_{target}}{\sqrt a\,\sigma_t},\qquad a=260$$

日收益（4.3）：$f_t = w_{t-1}r_t = \dfrac{\sigma_{target}}{\sqrt a}S_{t-1}z_t$

> 原文强调：波动率目标**不是风控**，是为了让信号和绩效在标的间可比。

---

## 3. 主结果（全部已核对）

### 3.1 自相关生成函数（5.3）
$$\Phi_\nu=\sum_{m=0}^{\infty}\nu^m\rho(m),\qquad \Psi_\nu=\Phi_\nu-1=\sum_{m=1}^{\infty}\nu^m\rho(m)$$

谱表示（5.4，Herglotz）：
$$2\Phi_\nu-1=\int_{-\pi}^{\pi}\frac{1-\nu^2}{1-2\nu\cos\lambda+\nu^2}\,dF(\lambda)$$
被积核是 **Poisson 核**。→ **趋势 alpha = 低频段的超额谱质量**；跨度设定核的带宽，长跨度只读零频附近（长记忆所在）。

### 3.2 年化预期收益（4.22）
$$\bar F_{1y}=h_{1y}\Big[\Phi_\nu-1\Big]+\Big(\frac{l\sigma_{target}}{\sqrt a}\Big)(\mu^z_{an})^2,\qquad h_{1y}=l\sigma_{target}\sqrt a\,\frac{1-\nu}{\nu}$$
$\mu^z_{an}=\sqrt a\,\mu$ = 标的自身的夏普。**只有两个收益来源：正的加权自相关，和漂移的平方。**

零漂移下盈利的充要条件：$\sum_{m\ge1}\nu^m\rho(m) > 0$。

### 3.3 闭式夏普（5.12）★核心
$$SR=\frac{\sqrt a\,A_\nu+(\mu^z_{an})^2/\sqrt a}{\sqrt{B_\nu+A_\nu^2+\kappa K_\nu+\big((\mu^z_{an})^2/a\big)\big(1+B_\nu+2A_\nu\big)}}$$
$$A_\nu=\frac{1-\nu}{\nu}\Psi_\nu,\qquad B_\nu=\frac{1-\nu}{1+\nu}\big(1+2\Psi_\nu\big)$$
$$K_\nu=\sum_{s\ge1}\psi_s^2 g_{s-1}^2,\qquad g_u=(1-\nu)\sum_{m=0}^{u}\nu^m\psi_{u-m}$$
（$\psi_s$ = Wold 分解的 MA 权重，$\kappa$ = 新息超额峰度）

**SR 不依赖 $\sigma_{target}$，也不依赖滤波载荷 $l$**——公因子 $l\sigma_{target}/\sqrt a$ 在分子分母同时出现。

### 3.4 净夏普（5.13）
$$SR^{net}\approx SR-\frac{2ac}{\sqrt\pi}\cdot\frac{1-\nu}{\sqrt{(1+\nu)\Big(B_\nu+A_\nu^2+\kappa K_\nu+\big((\mu^z_{an})^2/a\big)(1+B_\nu+2A_\nu)\Big)}}$$

换手定义（4.15）：$U_t\equiv\sqrt a\,\sigma_t\,|w_t-w_{t-1}|$
信号换手闭式（4.16）：$\mathbb E[aU_t^{signal}]=\dfrac{2a}{\sqrt\pi}\sigma_{target}\sqrt{1-\nu}$

### 3.5 ★★ c 的单位（本仓库自行推导，论文未显式写）
$c$ 是**波动率归一化成本**，不是券商费率。由 $U_t=\sigma_{ann}|\Delta w|$ 与真实成本 $c_{real}|\Delta w|$ 对齐：
$$\boxed{\,c = c_{real}\,/\,\sigma_{ann}\,}$$
用表 7.2 反验：股指 6bp/18% = 33bp，商品 10bp/25% = 40bp，FX 3bp/9% = 33bp，与正文"realized costs 40–60bp per unit of turnover"量级一致。

**A 股换算**：个股单边 3.6bp（往返万7.2）、$\sigma_{ann}\approx40\%$ → $c\approx 9$bp，**远低于临界 37–41bp**。ETF 单边 1.1bp、$\sigma_{ann}\approx20\%$ → $c\approx 5.5$bp。**成本不是 A 股趋势跟踪的约束**。

### 3.6 三个可直接代入的过程

| 过程 | 结果 | 式 |
|---|---|---|
| 白噪声+漂移 | $SR\approx(\mu^z_{an})^2\sqrt{\text{span}/a}$ | 6.4 |
| 白噪声净 | $SR^{net}\approx(\mu^z_{an})^2\sqrt{\text{span}/a}-2ac\sqrt{\tfrac{2}{\pi\,\text{span}}}$ | 6.5 |
| AR(1) 零漂移 | $A_\nu=\tfrac{(1-\nu)\phi}{1-\nu\phi},\ B_\nu=\tfrac{(1-\nu)(1+\nu\phi)}{(1+\nu)(1-\nu\phi)},\ SR=\tfrac{\sqrt a A_\nu}{\sqrt{B_\nu+A_\nu^2}}$ | 6.9 |
| AR(1) 小 φ 展开 | $SR\approx 2\phi\sqrt{a/\text{span}}$ | 6.11 |
| AR(1) 净 | $SR^{net}\approx 2\sqrt{\tfrac{a}{\text{span}}}\Big(\phi-c\sqrt{\tfrac{2a}{\pi}}\Big)$ | 6.12 |
| AR(1) 临界成本 | $c^*_\infty=\sqrt{\dfrac{\pi}{2a}}\cdot\dfrac{\phi}{1-\phi}$ | 6.13 |
| ARFIMA(0,d,0) | $\Phi_\nu=F(d,1,1-d;\nu)$（超几何） | 6.20 |

$\phi=0.05,\ a=260$ → $c^*_\infty = 40.9$bp；跨度 1 周–2 年间只在 **37–41bp** 变动（近似跨度无关）。

**跨度选择的根本矛盾**（p.22）：漂移贡献 $\propto\sqrt{\text{span}}$（增），短期自相关贡献 $\propto 1/\sqrt{\text{span}}$（减）。**短滤波变现自相关，长滤波变现漂移。** AR(1) 下两项同以 $1/\sqrt{span}$ 衰减 → 净夏普符号跨度无关（knife-edge）；只有长记忆才产生内部最优跨度。

**ARFIMA 三种 regime**（图 6.3，d=0.02，c=20bp）：
- $\phi=+0.05$：alpha 压倒成本，**最快跨度最优**，净 SR 从 0.68 递减
- $\phi=0$（纯长记忆）：驼峰，**内部最优 0.19 @ 1–3 个月跨度**
- $\phi=-0.05$（短期反转）：净 SR 在 **3–6 个月跨度之间才转正**

### 3.7 峰度影响 = 二阶，可忽略
- GARCH(1,1) 原始超额峰度 7 → 归一化后 $z_t$ 超额峰度仅 **0.06**（p.15）
- AR-1 标定 κ=3：SR 相对下降**至多 0.2%**（span 5–250 天）
- 全流程表 6.1：厚尾使 pooled gross SR 下降**至多 0.009**
- 原文定性："kurtosis effect is second order within the pipeline"

**波动率归一化本身就吃掉了肥尾。** 不要把"标的肥尾"当作趋势策略夏普的折价理由。

### 3.8 偏度是结构性的（7.4 / Prop D.2）
白噪声下 T 日聚合收益偏度：T=1 为 0，**T≥2 恒正，驼峰状，峰值在约 0.55×span**，与载荷和波动率目标无关。
- span=100：闭式峰值 **2.35 @ 55 天**；84 个期货实测中位数 **2.33 @ 55 天**
- 四分位区间在所有 horizon 都在零上 → 是横截面性质不是中位数标的的性质
- **不需要自相关、不需要漂移、不需要 alpha**——白噪声下期望收益为零时依然成立
- 解释了"日收益看起来对称、月/季收益右偏"
- LS(250,20) 的季度偏度 1.7，单滤波 2.3——长短滤波把 profile 推向更长 horizon

---

## 4. 实证（84 个流动期货，1959/1997–2026）

**参数选择**：European/American 长短跨度 **250/20**（实测成本 1.7%/年，符合真实 CTA）；TSMOM 最优 **M=L=10**；波动率 EWMA span 33。

**vs SG Trend Index**（1999-12-31 起，扣交易成本 + 2%/20% 费后）：

| | Sharpe | vs Index p 值 |
|---|---|---|
| European | 0.47 | 0.96 |
| American | 0.50 | 0.82 |
| TSMOM | 0.55 | 0.62 |
| SG Trend Index | 0.47 | — |

Ledoit-Wolf 检验**无法拒绝相等**。三系统与 Index 平均相关 **80%**，European↔American **95%**。

**换手**：波动率归一化换手 European/TSMOM **300–400%**，American **125–200%**（离散交易使 American 换手与滚动成本低约 50%）。绝对年化换手接近 2000%，被低波动债券合约主导。
> 注意区分：信号换手代理值（393% 单250日滤波 / 88% LS(250,20)）比全流程实测低 1.6–2.3 倍，不要混用。

**公式验证**（图 7.3 / 7.4）：

| 系统 | corr | slope |
|---|---|---|
| European（全跨度） | 0.99 | 0.96 |
| European（≥42日） | 1.00 | 0.98 |
| American（>1个月） | 0.92 | 0.61 |
| TSMOM（全跨度） | 0.89 | 0.73 |

TSMOM 的 0.73 接近符号滤波的高斯基准 $\sqrt{2/\pi}\approx0.80$（Bussgang 定理）。

**归因**（图 7.4）：自相关通道贡献中位夏普 0.55（5日跨度）→ 0.33（2年跨度）；漂移通道最多加 0.08 且随跨度增长。**趋势收益主要是自相关现象，漂移份额随跨度上升。**

**⚠ 样本内**（p.29 原文）："The comparison is in-sample by design... The out-of-sample application is a subject for future research."
另注：全样本 $z_t$ 的 EWMA 一阶自相关从 1990s 的 **0.04** 降到 2010 后的 **0.01**——趋势 alpha 在衰减。作者建议滚动窗口估参数 + 漂移去偏。

---

## 5. 对本仓库的可执行含义

1. **已实测，见 `research/tf_acf_spectrum.py`（2026-08-09）**。原先的猜测「A 股 φ<0，出路在 60–120 日长记忆跨度」**被证伪**：
   - A 股 φ 在宽基/行业/概念/个股四层**都为正**（中位 0.03–0.06），且两个半样本同号（行业 96.8%）
   - 但 GPH 的 **d 全层不显著**（显著>0 占比 0%–0.8%）——**A 股没有长记忆**
   - 只有 φ 没有 d ⇒ 式 6.11 的 $SR\propto1/\sqrt{span}$ 主导，**最优跨度是最短的，不是 60–120 日**
   - 成本确实不是约束：临界单边 7–17bp vs 实际 1.1–3.6bp
   - 剩余的真问题是**冲击成本**（5 日跨度换手极高，模型未含）和**等权指数的非同步交易虚高**
2. **信号形式的边际价值为零**。三系统 corr 80–95%、Sharpe 差异统计不显著。要提升只能换标的池、加 regime 门控（`xiaoxifu/regime_combo` 方向正确）或降换手（离散化规则本身降 50% 成本）。
3. **持有期 ≈ 0.55×信号跨度** 可最大化右尾偏度。`boll_narrow_exit` 15日持有 ↔ 约 27 日跨度；`swing` hold10 ↔ 约 18 日跨度。
4. **(5.12) 用作归因工具是安全的，用作预筛器必须自己做样本外验证**（滚动窗估 ACF → 预测下期 SR → 对比实现值）。论文没做这一步。
5. **不要用"标的肥尾"解释夏普折价**（见 3.7）。
