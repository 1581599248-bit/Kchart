# RYAN 技术面多因子打分模型 v2 — 设计说明书（证据驱动）

定稿日期：2026-08-01
本文档是 scoring.py 的设计依据，记录每个因子存在或被淘汰的理由及文献来源。
模型代码实现接口见 `docs/ARCHITECTURE.md` 第 3 节 scoring.py；榜单烘焙链路见 scripts/bake_top20.py 一节。
（注：回测功能已于 2026-08-03 下线，backtest.py 已删除；§5/§6 中回测执行与撮合相关内容保留作设计记录，恢复回测时再对照实现。）

---

## 1. 设计哲学

打分模型只做一件事：**在可投资域内，用有独立证据支撑、彼此低相关的技术面维度做横截面排序**。

三条铁律：

1. **每个因子必须能引用证据或可证伪的经济逻辑**，"交易圈都说有用"不算证据。
2. **同族指标只留一个代表**——堆叠同源因子 = 隐性给该维度加权 = 过拟合的典型路径。
3. **展示价值与预测价值分离**——MACD/KDJ/WR/BOLL/斐波那契/谐波是优秀的"图解语言"（推背图标注照用），但统计证据不支持它们进入打分公式，就一律零权重。

## 2. 因子淘汰记录（为什么砍掉）

| 淘汰对象 | 理由 | 证据 |
|---|---|---|
| 价格动量组（ROC20/ROC60/动量加速度） | A股月度频率无动量，存在显著短期反转；中国被跨国研究归为"无动量市场" | Chui-Titman-Wei 2010 JF；Liu-Stambaugh-Yuan 2019 JFE；Griffin-Ji-Martin 2003；Pan-Tang-Xu 2013 |
| WR 指标 | WR = %K − 100，与 KDJ %K 是数学等价的线性变换，同放即重复计权 | 构造事实（无需实证） |
| KDJ（打分中） | 与 RSI 同属"区间位置"振荡器族，族内只留代表 RSI6；KDJ 保留为图上信号 | 指标族冗余实证（ICCS 2026 华沙聚类研究）；Chong-Ng 2008 |
| MACD 柱/金叉（打分中） | 由 EMA12/26 完全派生，与趋势组因子高度同源；保留为图上信号与背离检测 | 构造事实；指标族聚类实证 |
| OBV | 无独立顶刊证据；Sullivan-Timmermann-White 1999 将 OBV 规则纳入 7,846 条规则宇宙后整体不显著 | STW 1999 JF |
| 斐波那契回撤位（打分中） | 算法化三市场检验：价格在斐波那契位反弹概率与随机水平无差异 | UPV/RiuNet 三市场实证；Macalester 外汇市场荣誉论文 |
| 谐波形态 Gartley/Bat/Crab/PRZ（打分中） | 无同行评审证据；独立回测显示 Gartley 为谐波中盈利最差之一；比率体系为后人附加无规范回测 | Liberated Stock Trader 2025 独立回测；Gartley 1935 原书无回测 |
| 蜡烛图形态 | 美股与日本市场 1975-2004 系统检验不盈利 | Marshall-Young-Rose 2006 JBF |

## 3. 保留的五个因子组（组间等权 0.20）

### G1 短期反转（证据强度：★★★ A股最稳健异象之一）
- RET5(−)、RET20(−)：近 5/20 日收益取负向。
- 依据：Liu-Stambaugh-Yuan 2019（JFE 134:48-69）记录中国各期限反转、1个月反转显著；账户级数据研究（Jones/Shi/Zhang/Zhang）确认周度、月度强反转；国内王永宏-赵学军 2001、鲁臻-邹恒甫 2007 一致。
- 与周度调仓频率匹配：技术因子半衰期 1-4 周（中信建投因子衰减研究），5日反转恰在周频有效域内。

### G2 换手与量能（证据强度：★★★ 进入中国三因子模型）
- TURN20(−)：20 日均换手率，低换手得分高。
- ABNTURN(−)：当日换手 / 250 日均换手，异常放量得分低。
- 量价配合(+)：5 日"价升量增/价跌量缩"一致度（量价交互，Wang-Chin 2004 PBFJ：A股低量股票动量、高量股票反转）。
- 依据：LSY 2019 将换手率/异常换手率列为中国显著异象并纳入三因子模型；Carpenter-Lu-Whitelaw 2021（RFS）高换手=投机=低未来收益；Mei-Scheinkman-Xiong 2009。
- 弃用 OBV（见淘汰表）。

### G3 趋势质量（证据强度：★★ 文献脉络明确但样本外衰减，故只占一族）
- close > MA250 且 MA250 上行；MA60 的 20 日斜率；ADX14>25 且 PDI>MDI 程度。组内等权。
- 依据：Brock-Lakonishok-LeBaron 1992（JF）MA/TRB 规则谱系；George-Hwang 之外的趋势-信息扩散理论（Hong-Stein 1999）。
- 衰减警示：Sullivan-Timmermann-White 1999、Bajgrowicz-Scaillet 2012（JFE）显示美股 1987 后失效——因此趋势组只占 1/5 权重，且参数用行业标准值不优化。
- 与 G1 的关系：反转与趋势在横截面上天然低相关甚至负相关（一个买超跌、一个买强势），组间等权正好构成对冲式复合，这是模型稳健性的主要来源之一。

### G4 波动与彩票（证据强度：★★☆ 中国证据充分）
- VOL20(−)：20 日日收益标准差（低波异象）。
- MAX5(−)：近 5 日最大单日涨幅（彩票效应，Bali-Cakici-Whitelaw 2011 谱系；A股散户博彩偏好强）。
- DIST250H(+)：close / 250 日最高价（52 周高点效应，George-Hwang 2004 JF）。
- 依据：LSY 2019 中国异象检验含波动类；彩票效应对应 Carpenter-Lu-Whitelaw 2021 的投机交易结论。
- 与 G2 的分工：换手率度量"交易热度"，波动/彩票度量"价格行为热度"，构造数据源不同（量 vs 价），相关预期中等，受 |ρ|>0.6 治理规则监控。

### G5 结构形态（证据强度：★☆ 增量信息有、利润证据弱，给最低族内权重）
- patterns.py 已确认事件：W底/头肩底/圆弧底颈线突破 +1（构筑中 +0.3）；M顶/头肩顶 −1；按确认点距 t 的根数做半衰期 20 根指数衰减；无结构为 0。
- 依据：Lo-Mamaysky-Wang 2000（JF）证实头肩/双底等形态对条件收益分布有统计显著增量信息，但作者明确"不必然转化为交易利润"——因此只给一个组、且只用"已确认突破"事件（右侧确认后才有任何意义）。
- 此组同时是推背图展示与打分之间唯一的桥梁，但打分只取方向与确认状态，不取形态"美观度"类自由参数。

## 4. 横截面处理流水线

每个调仓日 t：
1. 原始因子计算（仅用 ≤t 数据）；
2. median/MAD 稳健 z-score，±3 缩尾（Barra FaCS 标准）；
3. 负向因子取反，统一方向；
4. 组内等权合成 → 再标准化；
5. 五组等权加总 → 横截面百分位映射到 0~100。

权重方案决策记录：采用**组间+组内固定等权**为主模型。依据 DeMiguel-Garlappi-Uppal 2009（RFS）证明 1/N 样本外持续击败 14 种最优权重模型；Reschenhofer 2022 对多特征打分组合同样结论；AQR 旗舰 QMJ 因子即组内 z-score 等权（Frazzini-Kabiller-Pedersen 2018）。滚动 12 个月 ICIR 加权只作为回测报告中的敏感性对照曲线——若两者差异巨大，说明权重选择本身在过拟合，需回炉。

## 5. 防过拟合协议（写死在回测报告里）

1. **参数冻结**：所有参数为行业标准值（MA 20/60/250、ATR/ADX/RSI 14 或 6、半衰期 20 根），不允许网格寻优。任何参数变更 = 一次新试验，计入 N。
2. **DSR（Deflated Sharpe Ratio，Bailey & López de Prado 2014）**：
   `DSR = Φ( (SR̂ − E[max SR])·√(T−1) / √(1 − γ₃·SR̂ + (γ₄−1)/4·SR̂²) )`，
   其中运气门槛 `E[max SR] = σ_SR·[(1−γ)Φ⁻¹(1−1/N) + γ·Φ⁻¹(1−1/(N·e))]`，γ≈0.5772。
   报告口径：DSR > 0.95 判定通过；同时报告 MinBTL ≈ 2·ln(N)/SR²（年）。N 由 results_db `system_meta` 累计记录（含手工调参次数）。
3. **因子显著性阈值**：Harvey-Liu-Zhu 2016（RFS）多重检验修正——新因子 t 值门槛 3.0，不用 2.0。
4. **因子相关治理**：实现后计算五组得分（及组内因子）的截面 Spearman 相关矩阵并取时序均值，|ρ| 持续 >0.6 的因子对合并或删除其一，处置写入报告（Barra 式"簇内合成"，不做 PCA——PCA 成分无经济含义、载荷滚动不稳定，不适合 alpha 合成）。
5. **分年度检验**：回测报告必须给出分年度收益与五因子组各自等权子组合曲线；单一年份或单一组驱动的"alpha"直接判不合格。
6. **样本外纪律**：本模型全部参数在 2026-08-01 一次性冻结，之后若再调参，最近一次完整回测标记为样本内，须留出其后 12 个月作为冻结观察期。

## 6. 可投资域与执行（海通金工标准口径）

- 剔除：ST/*ST、停牌、上市不足 120 交易日、20 日中位成交额 < 5000 万（现由 scripts/bake_top20.py 在榜单日期当日判定：`days_since_listing>=120` 且 `median_amount_cny_20>=5e7`）。
- 执行（回测口径，功能已下线，保留作设计记录）：信号日 t 收盘打分 → t+1 开盘价成交；一字涨停买不进 → 顺延排名下一位；一字跌停卖不出 → 持有至下一可交易日。
- 成本：佣金万2.5双边 + 滑点千1双边 + 卖出印花税万5 ≈ 双边千三。
- 停牌期间持仓按停牌前价冻结，复牌跳空计入。
- 价格口径：信号与收益均用前复权比率（=真实全收益），不使用绝对价格水平类信号，规避前复权前视问题。

## 7. 与推背图展示层的关系

打分模型（预测）与推背图（解释/展示）共享同一套 pivot/结构识别地基，但：
- 推背图可以使用全部经典语言：MACD/KDJ/RSI/WR/BOLL/背离/斐波那契/谐波/波浪/箱体/旗形楔形三角形——它们的任务是"让你看清这根K线上发生了什么"；
- 打分公式只引用 §3 五个组——它们的任务是"排序未来一周谁更可能跑赢"。
- 前端展示打分徽标时并列显示五组得分，让用户看到排名的构成，而不是一个黑箱分数。

## 8. 主要参考文献

1. Brock, Lakonishok, LeBaron (1992), *Simple Technical Trading Rules and the Stochastic Properties of Stock Returns*, JF 47:1731-1764.
2. Sullivan, Timmermann, White (1999), *Data-Snooping, Technical Trading Rule Performance, and the Bootstrap*, JF 54:1647-1691.
3. Lo, Mamaysky, Wang (2000), *Foundations of Technical Analysis*, JF 55:1705-1765.
4. Bajgrowicz, Scaillet (2012), *Technical trading revisited: False discoveries, persistence tests, and transaction costs*, JFE.
5. Liu, Stambaugh, Yuan (2019), *Size and Value in China*, JFE 134:48-69.
6. Chui, Titman, Wei (2010), *Individualism and Momentum around the World*, JF.
7. Carpenter, Lu, Whitelaw (2021), *The Real Value of China's Stock Market*, RFS.
8. Lee, Swaminathan (2000), *Price Momentum and Trading Volume*, JF 55:2017-2069.
9. Wang, Chin (2004), *Profitability of return and volume-based investment strategies in China's stock market*, PBFJ.
10. George, Hwang (2004), *The 52-Week High and Momentum Investing*, JF.
11. Marshall, Young, Rose (2006), *Candlestick Technical Trading Strategies*, JBF 30:2303-2323.
12. DeMiguel, Garlappi, Uppal (2009), *Optimal Versus Naive Diversification: How Inefficient is the 1/N Portfolio Strategy?*, RFS.
13. Frazzini, Kabiller, Pedersen (2018), *Buffett's Alpha*, FAJ.
14. Bailey, López de Prado (2014), *The Deflated Sharpe Ratio*, JPM.
15. Harvey, Liu, Zhu (2016), *…and the Cross-Section of Expected Returns*, RFS.
16. Green, Hand, Zhang (2017), *The Characteristics that Provide Independent Information about Average U.S. Monthly Stock Returns*, RFS.
17. López de Prado (2018), *Advances in Financial Machine Learning*, ch.7 (Purged CV & Embargo).
18. Mei, Scheinkman, Xiong (2009), *Speculative Trading and Stock Prices: Evidence from Chinese A-B Share Premia*.
19. UPV/RiuNet: *Automatic identification and evaluation of Fibonacci retracements: Empirical evidence from three equity markets*（否定性结论）.
20. 华泰金工《因子合成方法实证分析》(2019)；广发金工《考虑换手率限制的多因子Alpha模型》；中信建投《因子衰减在多因子选股中的应用》；海通金工"海量"高频因子系列（可投资域口径）.
