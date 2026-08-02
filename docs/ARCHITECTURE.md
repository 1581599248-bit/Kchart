# RYAN K线推背图 — 系统架构规范

版本：v1.0（2026-08-01）
本文件是全系统唯一开发依据。所有模块必须遵守本规范中的接口签名、数据口径和参数标准。

---

## 0. 总目标

桌面端本地网站「RYAN K线推背图」：
- 分页1「指数看板」：宽基指数（上证指数/深证成指/创业板指/科创50/沪深300/中证500/中证1000/中证2000）单列展示 + 下方 TOP20 标的瀑布流（两列）。
- 分页2「搜索」：全市场指数与个股检索（带索引/自动补全），点击任意标的进入完整K线看板。
- 分页3「回测」：周度轮换 TOP10 策略 10 年回测与量化指标。
- 每个标的卡片：K线图（小时/日/周/月切换、指标、画线工具）+ 图上推背图标注 + 图下精简文字分析（现况/走势/目标位/止盈止损）。

## 1. 技术栈与目录

- 后端：Python 3.12 + FastAPI + uvicorn + DuckDB + pandas + numpy（venv 在 `.venv/`）。
- 前端：原生 HTML/JS SPA，`frontend/vendor/lightweight-charts.standalone.production.js`（v4.2.3，已本地化，禁止 CDN）。
- 无前端构建步骤；后端 `main.py` 直接静态托管 `frontend/`。
- 数据库：**只读外接**权威库；一切派生数据写入 `data/results.duckdb`（独立研究结果库）。

```
RYAN技术面K线模型/
├─ 启动看板.bat              # 一键入口：启动后端并打开浏览器
├─ README.md
├─ .gitignore               # 忽略 .venv/ data/*.duckdb __pycache__
├─ docs/ARCHITECTURE.md      # 本文件
├─ backend/
│  ├─ requirements.txt
│  └─ app/
│     ├─ config.py  db.py  resample.py
│     ├─ indicators.py  pivots.py  divergence.py
│     ├─ patterns.py  fibonacci.py  harmonics.py
│     ├─ analysis.py  scoring.py  backtest.py
│     ├─ results_db.py  main.py
├─ frontend/
│  ├─ index.html  css/app.css
│  ├─ vendor/lightweight-charts.standalone.production.js
│  └─ js/ api.js  chart.js  drawing.js  analysis_view.js  app.js
├─ data/                    # results.duckdb、打分缓存（git 忽略）
└─ scripts/  precompute_scores.py  run_backtest.py
```

## 2. 数据口径（硬约束）

权威库路径（junction）：`C:/Users/Administrator/Desktop/完整A股量化模型 数据库/RYAN重要全市场K线数据库.duckdb`
路径可用环境变量 `RYAN_AUTH_DB` 覆盖。**所有连接必须 `read_only=True`**。禁止向权威库写任何数据、禁止物化复制全表。

- 个股日线（前复权）：`daily_bars_qfq` 视图的 `open/high/low/close` 即前复权价（`*_raw` 为原始价），含 `vol`（手）`amount`（千元）`turnover_rate`。
- 个股合格池/资格过滤：`research_daily_bars_strict`（逐日过滤非沪深/停牌/ST/上市不足60日/低流动性），与 `daily_bars_qfq` 按 `ts_code+trade_date` JOIN 使用。**打分与回测的股票池必须来自该视图对应日期的记录**。
- 60分钟线：`research_hourly_bars_strict`（`trade_time`, `open/high/low/close` 已是前复权口径则直接用；若该视图为原始价则乘 `qfq_ratio`，以实现时实际查看视图定义为准），时间粒度 10:30/11:30/14:00/15:00。
- 指数日线：`index_daily_bars`（无复权概念，直接用）。
- 指数名称：`index_master`（`ts_code,name,market,index_type`）。
- 个股名称：`security_master_history WHERE is_current`（`ts_code,security_name,market,board`）。
- 交易日历：`trading_calendar WHERE exchange='SSE'`（`cal_date,is_open`）。
- 周线/月线：后端由日线聚合（详见 resample.py 规范），不使用外部源。
- 涨跌停：`security_trading_status` 的 `hit_limit_up/hit_limit_down, limit_up_price, limit_down_price`。
- API（teajoin / tushare代理）仅作为**在线补数**的可选后备，token 从环境变量 `TS_TOKEN` 读取，禁止硬编码进代码与 git。当前默认数据源 = 权威库。

## 3. 后端模块规范

### config.py
- `AUTH_DB_PATH`（env `RYAN_AUTH_DB` 覆盖）、`RESULTS_DB_PATH = data/results.duckdb`、`HOST=127.0.0.1`、`PORT=8600`、
  `FRONTEND_DIR`、回测参数（`COMMISSION_RATE=0.00025`、`SLIPPAGE_RATE=0.001`、`STAMP_TAX_SELL=0.0005`）。

### db.py
- `get_con() -> duckdb.DuckDBPyConnection`（每次新建只读连接，线程安全；含 `SET memory_limit`/`threads`）。
- `load_daily_qfq(ts_code, start=None, end=None) -> DataFrame[trade_date,open,high,low,close,vol,amount]`（按日期升序）。
- `load_daily_qfq_universe(start, end) -> DataFrame`：JOIN strict 视图，全池面板（打分/回测用，列同上 + ts_code）。
- `load_hourly(ts_code, start, end)`、`load_index_daily(ts_code, start, end)`、
  `list_securities() -> DataFrame[ts_code,name,kind,market]`（kind: equity/index，个股+指数合并供搜索）、
  `latest_trade_date() -> date`、`trade_calendar(start,end) -> list[date]`。

### resample.py
- `resample_ohlcv(df, rule) -> DataFrame`：`rule in {'W','M'}`，以 `trade_date` 为索引聚合
  open=first, high=max, low=min, close=last, vol/amount=sum；周期标签取区间内最后交易日（真实交易日，不用日历周五/月末）。

### indicators.py
全部函数输入 `DataFrame`（含 open/high/low/close/vol），输出与输入等长的列并写回副本；标准参数固定：
- `add_ma(df, periods=(5,10,20,60,120,250))` → `MA5..MA250`
- `add_ema(df, periods=(12,26,50))` → `EMA12..`
- `add_macd(df, fast=12, slow=26, signal=9)` → `DIF,DEA,MACD_HIST`（hist=2×(DIF−DEA)，国内口径）
- `add_kdj(df, n=9, k_period=3, d_period=3)` → `K,D,J`（J=3K−2D）
- `add_rsi(df, periods=(6,12,24))` → `RSI6,RSI12,RSI24`（SMA 口径/ Wilder 均可，文件内注明）
- `add_wr(df, periods=(6,10))` → `WR6,WR10`（0~-100）
- `add_boll(df, n=20, k=2)` → `BOLL_MID,BOLL_UP,BOLL_DN`
- `add_atr(df, n=14)` → `ATR14`
- `add_obv(df)` → `OBV`
- `add_adx(df, n=14)` → `ADX,PDI,MDI`
- `add_roc(df, periods=(20,60))` → `ROC20,ROC60`
- `compute_all(df) -> df`：一次算全。

### pivots.py（结构识别地基）
- `find_pivots(df, left=5, right=5) -> DataFrame[idx,trade_date,price,kind('H'/'L')]`：
  波段高点需左右各 left/right 根内最高；**右侧确认**——第 i 个 pivot 只有在 i+right 根之后才"生效"。
  所有下游结构/背离/谐波只使用 `asof_bar` 时已生效的 pivot（防未来函数的核心）。
- `zigzag(df, min_pct=0.05)`：基于 pivot 的交替序列。

### patterns.py（K线结构）
输入 df + pivots，输出 `list[PatternEvent]`：
`PatternEvent = {kind, name, direction('bull'/'bear'/'range'), start_idx, end_idx, confirm_idx|None, key_levels{...}, score(-100..100), star(bool), note(str)}`
必须实现（参数化阈值写在文件头）：
- W底/双重底、头肩底、圆弧底、三重底（bull）；M顶/双重顶、头肩顶（bear）；
- 箱体震荡（range，含上沿/下沿突破确认）、上升旗形、下跌旗形、上升楔形、下降楔形、对称/上升/下降三角形；
- 上升五浪位置判定（当前处于第几浪，基于 zigzag）、下跌 ABC 判定。
- `confirm_idx`：颈线/边界突破确认K线；未确认的结构 score 减半并标注「构筑中」。

### divergence.py
- `find_divergences(df, indicator_col, price_col='close') -> list[{kind('top'/'bottom'), idx1, idx2, confirmed_idx}]`：
  价格 pivot 新高/新低而指标 pivot 未新高/新低；第二个 pivot 右侧确认后才输出。

### fibonacci.py（**仅展示用途，零打分权重**——统计检验显示斐波那契位与随机水平无差异，见 MODEL_DESIGN.md）
- `dominant_swing(df, pivots, lookback)`：当前主导波段（最近一个完整 zigzag 腿）。
- `fib_analysis(df, pivots) -> {swing{start_idx,end_idx,dir}, levels{ratio:price}, position_ratio, nearest_level, golden_pocket(bool), extensions{1.272,1.618,2.618}}`：
  上涨波段算回撤位（0.236/0.382/0.5/0.618/0.786/0.886），下跌波段反弹同理；`position_ratio` 为现价在波段中的位置。

### harmonics.py（**仅展示用途，零打分权重**——无同行评审证据，见 MODEL_DESIGN.md）
- `find_xabcd(pivots) -> list[HarmonicEvent]`：取最近 5 个交替 pivot 为 X-A-B-C-D，
  校验各形态比率（容差 ±5%）：
  - Gartley: B=0.618XA, D=0.786XA, BC=0.382~0.886AB
  - Bat: B=0.382~0.5XA, D=0.886XA
  - Butterfly: D=1.272XA 扩展；Crab: D=1.618XA；Shark: D=0.886~1.13XC 区
  - `HarmonicEvent = {name, direction, x,a,b,c,d(索引+价), prz_low, prz_high, completed(bool), score, note}`。
  D 点必须已右侧确认才算 completed；未完成给出潜在 D 目标区（PRZ）。

### analysis.py（推背图聚合）
- `analyze(df, timeframe) -> AnalysisResult`：调 pivots/patterns/divergence/fib/harmonics + 指标信号，输出：
```
{
  annotations: [   # 前端画到图上
    {bar_idx|time, price, kind: 'pattern'|'divergence'|'indicator'|'fib'|'harmonic',
     label, direction, star: bool, detail: str,
     lines?: [{t1,p1,t2,p2,style}], zones?: [{t1,t2,top,bottom,color}]}
  ],
  summary: {
    trend, structure, momentum, volume, key_supports[], key_resistances[],
    target_price, stop_loss, risk_reward, outlook_text (3-5句中文客观分析)
  }
}
```
- 指标信号（收敛口径，2026-08-01 用户反馈）：只标注特别明显的 RSI6>80 超买 / <20 超卖；
  背离另加显著性过滤（两 pivot 价格波幅 ≥3%，指标反差相对 ≥15% 且有绝对下限：DIF/DEA 为价格 0.2%、RSI 5 点）。
  MACD/KDJ/均线交叉、WR、BOLL、放量/缩量等高频弱信号不再上图。
- 结构信号为标注主体：形态构筑里程碑（W底右底/三重底第三底/头肩底右肩/M顶右顶/头肩顶右肩确认，
  标在结构最后一个 pivot）、颈线/边界突破（confirm_idx）、突破后回踩颈线确认（bear 为反抽颈线确认，
  容差 1%，每形态只标首次）；现价到达斐波那契重要位（0.382/0.5/0.618/0.786 贴近 1.5% 或 golden pocket）
  与进入谐波 PRZ 时亦上图。
- `star=True` 仅用于：已确认的主要结构突破、回踩/反抽颈线确认、已确认明显背离、进入谐波 PRZ、关键斐波那契位（golden pocket）企稳。密度控制：同一 10 根K线窗口内同密度组（_grp，缺省=kind；pattern 按 突破/里程碑/回踩×形态种类 分组）只保留最重要的一个。
- **多尺度自适应分析（2026-08-02 用户反馈定稿，取代一切固定窗口规则）**：系统必须自动识别波段级别，禁止"近N根/固定ATR倍数"式硬编码窗口：
  1. pivots 层构建多尺度 zigzag（如 min_pct=3%/8%/15% 三级，参数文件头可调），每个 swing 标注级别（幅度+持续K线数）；
  2. 主导周期判定：最近已确认 swing 腿所在级别为当前交易级别，其上一级为背景级别；
  3. 结构时效性：结构是否"活跃"由**同级别当前腿**决定——结构终点落在本级别当前腿起点之前、或已被同级反向结构取代、或价格已越过其量度目标/失效点，即为历史结构（图上标注保留，不进 summary）；
  4. summary 分层输出：背景级别（大周期定方向与位置）+ 交易级别（当前结构与攻防位）+ 小级别（近期信号）；展望写"当下状态+前瞻条件"（突破 X 看至 Y / 跌破 Z 失效）；
  5. 止损 = 当前活跃结构所属级别的失效点（如 W 底右肩低点、旗面下沿、头肩右肩），无活跃结构时取交易级别最近已确认 pivot 低点，兜底 close−2×ATR；止损深度不得超过该结构自身深度（防引用古早深坑）；
  6. 目标位 = 活跃结构的量度目标（交易级别），背景级别构筑中结构给出潜在目标区。
- 形态描摹：PatternEvent 带 `trace`（构成形态的 pivot 折线与颈线/边界），annotations 以 `polylines` 透传，前端金色实线描摹+虚线颈线，任意 pan/zoom 可见。
- 目标位：最近斐波那契扩展位或结构量度目标（如头肩底 颈线+头到颈线距离）；止损：最近 pivot 低点/ATR 止损，取保守者。

### scoring.py — RYAN 技术面多因子打分模型 v2（证据驱动，2026-08-01 定稿）

设计依据（详见 `docs/MODEL_DESIGN.md`，含全部文献引用）：
- A股月度频率无动量、周/月度短期反转显著（Liu-Stambaugh-Yuan 2019 JFE；Chui-Titman-Wei 2010）→ 价格动量组废除，改为反转组。
- 换手率/异常换手是 A 股最稳健量价异象（LSY 2019；Carpenter-Lu-Whitelaw 2021 RFS）→ 量能组以换手率为核心，OBV 弃用。
- WR 与 KDJ %K 数学等价（WR=%K−100）；RSI/Stoch/CCI 同族 → 振荡器只留 RSI6 一个代表，KDJ/WR/MACD/BOLL 仅作图上信号展示，不进打分。
- 斐波那契位与谐波形态经统计检验与随机水平无差异（UPV 三市场研究）→ **零打分权重**，仅作推背图标注展示。
- 权重方案：组间固定等权为主基准（DeMiguel 2009 RFS：1/N 样本外最难击败；AQR QMJ 即组内等权），滚动 12 个月 ICIR 加权仅作敏感性对照，不进主模型。
- 因子治理：实现后跑因子两两截面 Spearman 相关（样本期内时序均值），|ρ|>0.6 的因子对必须合并或删除一个，结果写入回测报告。

五因子组（组间等权 0.20，总分 0~100，越高越优）：

| 组 | 因子（方向） | 构造 |
|---|---|---|
| G1 短期反转 | RET5(−)、RET20(−) | 5日/20日收益率，取负向（近期跌幅大者得分高）；已剔除当日涨跌停影响日 |
| G2 换手与量能 | TURN20(−)、ABNTURN(−)、量比背离(+) | 20日均换手率（负向）；当日换手/250日均换手（负向）；价跌量缩/价升量增的5日量价配合度 |
| G3 趋势质量 | 年化趋势(+)、MA60斜率(+)、ADX(+) | close>MA250 且 MA250 上行；MA60 的20日斜率；ADX14>25 且 PDI>MDI 程度。组内等权合成 |
| G4 波动与彩票 | VOL20(−)、MAX5(−)、距250日新高(+) | 20日日收益标准差（负向，低波异象）；近5日最大单日涨幅（负向，彩票效应）；close/250日最高价（George-Hwang 52周高点效应） |
| G5 结构形态 | 已确认底部结构(+)、已确认顶部结构(−) | patterns.py 输出：已确认 W底/头肩底/圆弧底突破 +1、构筑中 +0.3；已确认 M顶/头肩顶 −1；按 confirm_idx 距 t 根数做半衰期 20 根的指数衰减；无结构为 0 |

横截面处理流水线（每个调仓日）：
原始因子 → median/MAD 稳健 z-score → ±3 缩尾 → 方向调整（负向因子取反）→ 组内等权合成并再标准化 → 五组等权加总 → 映射到 0~100（横截面百分位）。

**防未来函数**：全部输入为 `trade_date <= asof_date`；pivot/背离/结构只取右侧已确认事件；换手率/收益类因子用 t 日及以前数据，t+1 开盘执行。
**防过拟合**：权重与参数全部固定写死为代码常量，不做历史拟合；任何参数变更必须记录试验序号 N 并更新 DSR 计算。
**展示与预测分离**：MACD/KDJ/RSI 超买超卖/WR/BOLL/斐波那契/谐波只出现在 `/api/analysis` 的图上标注与文字分析中，score 公式不得引用 fibonacci.py / harmonics.py 的输出。

### backtest.py
- `run_backtest(start='2016-01-01', end=latest, top_n=10, weight=1/top_n, rebalance='W')`：
  - 每周：以上周五（当周最后交易日）**收盘后**的打分排名取 TOP N；**下一个交易日开盘价**买入，下周最后交易日收盘价卖出（信号与执行严格隔一个时点）。
  - 成本（config 可调）：佣金万2.5双边、滑点千1双边、卖出印花税万5，合计约双边千三（海通金工标准口径）。
  - 可投资域（海通式，strict 视图已含大部分）：剔除 ST/停牌/上市不足 120 交易日/20日中位成交额 < 5000 万；**撮合约束**：买入日一字涨停则顺延至排名下一位；卖出日一字跌停则持有至下一可交易日（用 `security_trading_status.hit_limit_up/down`）。
  - 输出指标：净值序列、总收益、年化、最大回撤、夏普（rf=2%）、卡玛、胜率、周胜率、换手率；对比基准=当期可投资池等权组合与沪深300；超额收益、信息比率；分年度收益表。
  - **防过拟合报告（必须输出）**：
    a) DSR（Deflated Sharpe Ratio，Bailey & López de Prado 2014）：N = 截至本次运行记录到 `system_meta` 的累计试验次数，报告 DSR 与判定（>0.95 为通过）及 MinBTL；
    b) 分年度收益表（检验非单一年份驱动）；
    c) 因子组贡献：每次运行输出五组各自的组内等权子组合累计收益对比；
    d) 敏感性对照：同一信号用滚动 12 个月 ICIR 加权重算净值的对比曲线（只作对照，不进主模型）；
    e) 因子相关矩阵：五个组得分在两年度样本上的 Spearman 相关时序均值，|ρ|>0.6 报警。
  - 打分历史计算向量化：用 `load_daily_qfq_universe` 面板 + groupby 滚动计算，**不得逐股逐周循环拉库**。G5 结构因子历史回算成本高，允许近似：用 pivot 规则在面板上向量化识别"双底/双顶+颈线突破"代表事件，与单股精确版在 20 只抽样股上做一致性核对并记录差异率。

### results_db.py（系统记忆）
`data/results.duckdb` 表：
- `scores_daily(trade_date, ts_code, score, rank, group_json, model_version, computed_at)` 主键(trade_date,ts_code)
- `analysis_cache(ts_code, timeframe, asof_date, result_json, computed_at)` 主键三列
- `backtest_runs(run_id, params_json, metrics_json, created_at)`、`backtest_nav(run_id, trade_date, nav, bench_nav, pool_nav)`
- `system_meta(key, value)`（记录权威库 SHA-256、契约版本、最近打分日期）。
所有写入先查后写、幂等。接口：`save_scores/get_scores/save_analysis/get_analysis/save_backtest/get_backtest_list/get_backtest`。

### main.py（FastAPI）
静态托管 frontend 于 `/`；API 前缀 `/api`：
- `GET /api/meta` → {latest_trade_date, db_sha256, model_version, index_list[{ts_code,name}]}
- `GET /api/search?q=&limit=20` → [{ts_code,name,kind,market}]
- `GET /api/bars?ts_code=&timeframe=60m|1d|1w|1M&start=&end=` → {bars:[{time,o,h,l,c,v,amount}], name, currency_note}
  - time 为 UNIX 秒（UTC 口径按 lightweight-charts 约定）；60m 用真实 trade_time。
- `GET /api/indicators?ts_code=&timeframe=` → 与 bars 对齐的 MA/EMA/BOLL/MACD/KDJ/RSI/WR/VOL 序列（前端只做渲染，不在前端算指标）。
- `GET /api/analysis?ts_code=&timeframe=&refresh=0` → analysis.py 输出（走 results_db 缓存，refresh=1 重算）。
- `GET /api/top20?date=&refresh=0` → [{rank,ts_code,name,score,group_scores,change_pct,analysis_brief}]（当日缓存）。
- `GET /api/backtest?start=&end=&top_n=` → {metrics, nav_series[], yearly[], benchmark_compare}（结果存 results_db，参数相同直接返回缓存）。
- `POST /api/precompute` → 后台任务：增量补齐最近打分与指数分析缓存（启动时自动调用一次）。
- 启动时校验权威库可达、表齐全，日志打印库 SHA-256 与最新交易日。

## 4. 前端规范

暗黑机构风（TradingView 配色：背景 #131722，面板 #1e222d，网格 #2a2e39，文字 #d1d4dc，强调金 #f0b90b）。**中国配色：涨 #ef5350（红）跌 #26a69a（绿）**。中文字体栈优先 "Microsoft YaHei"。**策略方向 long-only**：只做多，看跌信号用于提示回避，不提供做空工具与做空建议。

- `chart.js`：封装 lightweight-charts。主图K线+MA/EMA/BOLL 叠加；副图窗格（VOL、MACD、KDJ、RSI、WR）用多 chart 同步 logical range（时间轴联动、十字光标联动）；支持滚轮缩放、拖拽平移、双击复位。
- `drawing.js`：画线工具条（直线/水平线/矩形/斐波那契回撤/盈亏比(多头/空头)），用 series primitive / 自绘 canvas overlay 实现，鼠标两/三点交互绘制，可删除、可拖动端点；按 `ts_code+timeframe` 存 localStorage。
- `analysis_view.js`：把 `/api/analysis` 的 annotations 渲染成图上标记（markers + 线段 + 区域色带），星号信号用金色大号标记；hover 显示 detail；图下渲染 summary 分析卡——趋势/结构/动量/量能/关键位/目标止损各占独立一段（2026-08-01 起不再展示背景/交易尺度行），展望按句分段落。
- `app.js`：三个分页切换。
  - 分页1：顶部指数选择按钮行（8个宽基），单列指数看板（K线+推背图+文字分析）；下方 TOP20 瀑布流两列卡片：每卡含迷你K线（可切换周期/指标/画线）、推背图标注、打分徽标与五因子组得分条形、精简分析。卡片懒加载（IntersectionObserver）。
  - 分页2：搜索框（防抖 300ms，后端检索），结果列表（代码/名称/市场/类型），点击进入单标的大看板（与分页1卡片同组件，全尺寸）。
  - 分页3：参数表单（起止、TOP N）、运行按钮、结果区：净值曲线（对数/普通切换）、回撤曲线、指标表（年化/最大回撤/夏普/卡玛/胜率/超额/信息比率）、分年度对比表、TOP10 vs 股票池 vs 沪深300 三线图。
- 密度控制：图上同屏标注超过 12 个时按 star 优先聚合，其余收成时间轴下方小点，hover 展开。

## 5. 启动与运维

- `启动看板.bat`：激活 `.venv` → `python -m backend.app.main`（uvicorn 127.0.0.1:8600）→ 延迟 3 秒 `start http://127.0.0.1:8600`。端口占用时自动 +1 重试最多 10 次。
- 首次启动自动：建 results 库 → 预计算最近交易日 TOP 打分与 8 个指数分析缓存（后台线程，不阻塞打开页面；页面显示"计算中"占位）。
- 打分缓存有效期：同一交易日不重复计算。

## 6. 质量红线

1. 权威库只读；任何写权威库、复制全表到别处的代码一律不允许。
2. 不引入未在本文件列出的第三方依赖；新依赖必须写入 requirements.txt。
3. 所有面向展示的K线数据默认前复权（指数除外）。
4. token/密钥只从环境变量读，不进代码、不进 git。
5. 每个后端模块附带 `if __name__ == '__main__':` 冒烟自检（用 600519.SH 与 000300.SH 各跑一次打印行数/事件数）。
6. 防未来函数：任何信号生成函数必须只依赖当前及历史行；review 时重点检查 shift/rolling 方向。
