# RYAN K线推背图 — 系统架构规范

版本：v1.1（2026-08-03：数据源切换为 tushare 兼容 HTTP API，回测/画线/60m 下线）
本文件是全系统唯一开发依据。所有模块必须遵守本规范中的接口签名、数据口径和参数标准。

---

## 0. 总目标

桌面端本地网站「RYAN K线推背图」：
- 分页1「指数看板」：宽基指数（上证指数/深证成指/创业板指/科创50/沪深300/中证500/中证1000/中证2000）单列展示 + 下方全市场搜索/个股看板（自选股/搜索历史侧栏）。
- 每个标的卡片：K线图（仅日线 + 指标）+ 图上推背图标注 + 图下精简文字分析（现况/走势/目标位/止盈止损）。
- ~~分页2「TOP20」~~：TOP20 榜单功能已下线（前后端与 scripts/bake_top20.py 已删除），不得再引用；打分模型 scoring.py 保留（见 MODEL_DESIGN.md）。
- ~~分页3「回测」~~：回测功能已下线（backtest.py 已删除），不得再引用。

## 1. 技术栈与目录

- 后端：Python 3.12 + FastAPI + uvicorn + DuckDB + pandas + numpy + requests（venv 在 `.venv/`）。
- 前端：原生 HTML/JS SPA，`frontend/vendor/lightweight-charts.standalone.production.js`（v4.2.3，已本地化，禁止 CDN）。
- 无前端构建步骤；后端 `main.py` 直接静态托管 `frontend/`。
- 数据源：**tushare 兼容 HTTP API**（见第 2 节）；派生数据写入 `data/results.duckdb`（独立研究结果库，仅存 analysis_cache/system_meta）；8 指数 K线/推背图为本地烘焙静态文件 `data/baked_charts.json`（提交入仓）。

```
RYAN技术面K线模型/
├─ 启动看板.bat              # 一键入口：启动后端并打开浏览器
├─ README.md
├─ .gitignore               # 忽略 .venv/ data/*.duckdb data/api_cache/ __pycache__
├─ docs/ARCHITECTURE.md      # 本文件
├─ backend/
│  ├─ requirements.txt
│  └─ app/
│     ├─ config.py  ts_api.py  resample.py
│     ├─ indicators.py  pivots.py  divergence.py
│     ├─ patterns.py  fibonacci.py  harmonics.py
│     ├─ analysis.py  scoring.py
│     ├─ results_db.py  main.py
├─ frontend/
│  ├─ index.html  css/app.css
│  ├─ vendor/lightweight-charts.standalone.production.js
│  └─ js/ api.js  chart.js  analysis_view.js  app.js
├─ data/                    # results.duckdb、api_cache/（git 忽略）；baked_charts.json（提交入仓）
└─ scripts/  bake_charts.py   # 8 指数 K线/推背图烘焙（冷启动秒开）
```

## 2. 数据口径（硬约束）

数据源 = tushare 兼容 HTTP API（`backend/app/ts_api.py` 封装）：
- 端点 `POST {TS_URL}`（默认 `https://ts.gyzcloud.top/api`，env `TS_URL` 覆盖），body `{"api_name","token","params","fields"}`；token 从环境变量 `TS_TOKEN` 读取，**禁止写进代码与任何 git 跟踪文件**。
- 全局限流：线程锁 + 最小调用间隔 `TS_MIN_INTERVAL` 秒（默认 0.45s ≈ 133次/分，低于 150次/分上限）；网络错误/5xx 指数退避重试，`code!=0` 抛 `TsApiError`。
- 磁盘缓存 `data/api_cache/`（JSON，git 忽略）：stock_basic/trade_cal 12 小时整包缓存；K 线类（daily/adj_factor/index_daily）按 ts_code 单文件存原始数据，空缓存全量拉取自 `KLINE_EARLIEST` 起（默认 2020-01-01，指数/个股一致），缓存最大日期 < 最新交易日时增量拉取（start_date=最大日+1）合并去重写回。接口返回的 K线展示窗口由 `KLINE_DISPLAY_START`（默认 2020-01-01）下限裁剪（`_load_bars_df` start 缺省时套用），烘焙文件同样只含该日起数据。

- 个股日线：API `daily`（原始价，vol=手，amount=千元）+ `adj_factor`；前复权在读时计算：**OHLC × adj_factor / max(adj_factor)**（max 取该股全历史最新因子），vol/amount 不动。
- 指数日线：API `index_daily`（无复权概念，直接用）。
- 个股名称/上市日：API `stock_basic`（list_status='L'）；指数名称：config 内置 8 宽基映射。
- 交易日历：API `trade_cal`（exchange='SSE'，cal_date/is_open）；最新交易日 = 不晚于今天的最近 is_open 日。
- 换手率：API `daily_basic`（turnover_rate，打分烘焙用；逐股 K 线接口不取）。
- 周线/月线：后端由日线聚合（resample.py），前端当前只展示日线。
- 60 分钟线：**API 版无此数据**，/api/bars timeframe=60m 一律 400「API 版暂无 60 分钟线」。

## 3. 后端模块规范

### config.py
- `RESULTS_DB_PATH = data/results.duckdb`、`HOST=0.0.0.0`、`PORT=8600`（env `PORT` 覆盖）、`FRONTEND_DIR`、`MODEL_VERSION`。
- `TS_TOKEN`（env，缺省空串）、`TS_URL`（env 覆盖）、`TS_MIN_INTERVAL`（env 覆盖，默认 0.45）。
- `BROAD_INDEX_CODES` / `BROAD_INDEX_NAMES`：8 个宽基指数代码与名称映射。

### ts_api.py（数据层，替代原 db.py）
- `call_api(api_name, params=None, fields=None, retries=3) -> DataFrame`：带限流/重试/gzip 的原始调用。
- `load_daily_qfq(ts_code, start=None, end=None) -> DataFrame[trade_date,open,high,low,close,vol,amount]`（前复权，日期升序，trade_date 为 datetime64）。
- `load_index_daily(ts_code, start=None, end=None)`（原始价，列同上）。
- `list_securities() -> DataFrame[ts_code,name,kind,market,list_date]`（kind: equity/index；进程内缓存 10 分钟）、
  `get_security_name(ts_code)`、`is_index(ts_code)`、
  `latest_trade_date() -> date`、`trade_calendar(start,end) -> list[date]`。
- 异常 `TsApiError`；main.py 注册为 503 + 中文 detail。

### resample.py
- `resample_ohlcv(df, rule) -> DataFrame`：`rule in {'W','M'}`，以 `trade_date` 为索引聚合
  open=first, high=max, low=min, close=last, vol/amount=sum；周期标签取区间内最后交易日（真实交易日，不用日历周五/月末）。
  （后端 1w/1M 仍走此路径，前端当前不展示。）

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
- 指标信号（收敛口径，2026-08-02 用户反馈）：只标注特别明显的 RSI6>90 超买 / <10 超卖（88/12 回差，衰竭/修复确认需区间极值 ≥93/≤7）；强趋势过滤——ADX≥25 且价在 MA60 同侧时不标注（强升势"超买一路"、强跌势"超卖一路"是趋势强度而非反转信号）。
  背离另加显著性过滤（两 pivot 价格波幅 ≥3%，指标反差相对 ≥15% 且有绝对下限：DIF/DEA 为价格 0.2%、RSI 5 点）。
  MACD/KDJ/均线交叉、WR、BOLL、放量/缩量等高频弱信号不再上图。
- 结构信号为标注主体：形态构筑里程碑（W底右底/三重底第三底/头肩底右肩/M顶右顶/头肩顶右肩确认，
  标在结构最后一个 pivot）、颈线/边界突破（confirm_idx）、突破后回踩颈线确认（bear 为反抽颈线确认，
  容差 1%，每形态只标首次）；现价到达斐波那契重要位（0.382/0.5/0.618/0.786 贴近 1.5% 或 golden pocket）
  与进入谐波 PRZ 时亦上图。
- `star=True` 仅用于：已确认的结构突破/破位（颈线/边界，构筑中不打星）、谐波反转（D点/进入 PRZ）；RSI 超买超卖、EMA 交叉、背离、回踩/反抽确认、斐波那契位一律不打星。密度控制：同一 10 根K线窗口内同密度组（_grp，缺省=kind；pattern 按 突破/里程碑/回踩×形态种类 分组；EMA 交叉为离散交替事件按事件唯一化不参与去重）只保留最重要的一个。顶部反转形态只在高位成立、底部反转形态只在低位成立（patterns.py `_position_ok`：极点在 250 根窗口区间分位 顶≥0.60/底≤0.40，叠加前置趋势校验）。
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
| G5 结构形态 | 已确认底部结构(+)、已确认顶部结构(−) | 面板向量化近似：双底/双顶+颈线突破，已确认 ±1、构筑中 ±0.3；按 confirm_idx 距 t 根数做半衰期 20 根的指数衰减；无结构为 0 |

横截面处理流水线（每个调仓日）：
原始因子 → median/MAD 稳健 z-score → ±3 缩尾 → 方向调整（负向因子取反）→ 组内等权合成并再标准化 → 五组等权加总 → 映射到 0~100（横截面百分位）。

**防未来函数**：全部输入为 `trade_date <= asof_date`；pivot/背离/结构只取右侧已确认事件；换手率/收益类因子用 t 日及以前数据，t+1 开盘执行。
**防过拟合**：权重与参数全部固定写死为代码常量，不做历史拟合；任何参数变更必须记录试验序号 N 并更新 DSR 计算。
**展示与预测分离**：MACD/KDJ/RSI 超买超卖/WR/BOLL/斐波那契/谐波只出现在 `/api/analysis` 的图上标注与文字分析中，score 公式不得引用 fibonacci.py / harmonics.py 的输出。

（注：TOP20 榜单功能已下线，scoring.py 当前无线上调用方，模型保留详见 MODEL_DESIGN.md。）

### 烘焙 K线/推背图（scripts/bake_charts.py → data/baked_charts.json，冷启动秒开）
- 标的 = config.BROAD_INDEX_CODES 8 个宽基指数（固定范围），逐标的打印进度，失败警告跳过。
- 每标的：`ts_api.load_index_daily` 全历史 1d bars（与 /api/bars 同形态同 time 口径）+ `analysis_mod.analyze` 全量结果（annotations 用 main `_annotations_to_epoch` 转 epoch，与 /api/analysis 响应完全同构）。
- 输出单行 JSON：`{"date":最新交易日,"version":1,"symbols":{ts_code:{"kind":"index","name","bars":[...],"analysis":{...}}}}`，UTF-8，ensure_ascii=False，**提交入仓**（约 3MB）。
- 服务端配合（main.py）：
  - `_baked_charts()` 懒加载进内存，mtime 变化自动重读，线程安全；文件缺失按无烘焙处理。
  - `_load_bars_df`：timeframe=1d 且标的在 baked 且 api_cache 无该标的 `index_daily_<code>.json` 缓存文件 → 直出 baked bars；缓存文件一旦由后台追新写入即切回 ts_api 实时路径。
  - `/api/analysis`：timeframe=1d 且非 refresh 且请求窗口起点 ≤ 烘焙窗口起点（覆盖烘焙窗口）且（无 K线缓存文件或缓存最大日期 ≤ 烘焙 asof_date）→ 直出 baked analysis，否则走原实时逻辑。
  - 启动时 `_warm_baked_symbols()` 起 daemon 线程：对 8 指数调 `load_index_daily` 增量拉 K线写 api_cache，随后逐指数预计算分析写 results_db（缓存键与端点一致 `1d@<api_cache第一根K线日期>#ANALYSIS_VERSION`）；全程 try/except，失败只记日志；无 TS_TOKEN 时整个 warmer 跳过。**更新 = 重跑脚本 + 提交 JSON。**
- 个股（非 8 指数）不做烘焙：搜索进入的个股走 API 实时路径，首次冷拉取较慢（前端有"分析计算中"提示），属可接受。

### results_db.py（系统记忆）
`data/results.duckdb` 表：
- `analysis_cache(ts_code, timeframe, asof_date, result_json, computed_at)` 主键三列
- `system_meta(key, value)`
所有写入先查后写、幂等。接口：`save_analysis/get_analysis/set_meta/get_meta`。
（scores_daily/backtest_runs/backtest_nav 已随 TOP20 榜单与回测下线移除。）

### main.py（FastAPI）
静态托管 frontend 于 `/`；API 前缀 `/api`：
- `GET /api/meta` → {latest_trade_date, model_version, snapshot:false（兼容字段）, index_list[{ts_code,name,has_60m:false}]}
- `GET /api/search?q=&limit=20` → [{ts_code,name,kind,market}]
- `GET /api/bars?ts_code=&timeframe=1d|1w|1M&start=&end=` → {bars:[{time,o,h,l,c,v,amount}], name, currency_note}
  - time 为 UNIX 秒（UTC 口径按 lightweight-charts 约定）；**timeframe=60m 一律 400「API 版暂无 60 分钟线」**；前端只展示 1d，1w/1M 后端保留 resample 路径。
  - 1d 且标的在 `baked_charts.json` 且本地无 api_cache K线缓存 → 直出烘焙 bars（冷启动秒回，见"烘焙 K线/推背图"一节）。
- `GET /api/indicators?ts_code=&timeframe=` → 与 bars 对齐的 MA/EMA/BOLL/MACD/KDJ/RSI/WR/VOL 序列（前端只做渲染，不在前端算指标）。
- `GET /api/analysis?ts_code=&timeframe=&refresh=0` → analysis.py 输出（走 results_db 的 analysis_cache，refresh=1 重算）；1d 且窗口覆盖烘焙窗口且无更新 K线缓存时直出 `baked_charts.json` 的分析结果。
- 启动时检查 TS_TOKEN：未设置打印醒目中文警告但照常启动（baked_charts 烘焙内容仍可用）；`TsApiError` 统一映射 503 + 中文 detail。

## 4. 前端规范

暗黑机构风（TradingView 配色：背景 #131722，面板 #1e222d，网格 #2a2e39，文字 #d1d4dc，强调金 #f0b90b）。**中国配色：涨 #ef5350（红）跌 #26a69a（绿）**。中文字体栈优先 "Microsoft YaHei"。**策略方向 long-only**：只做多，看跌信号用于提示回避，不提供做空工具与做空建议。

- `chart.js`：封装 lightweight-charts。**只保留日线周期**（TF_LIST 仅 1d，周期按钮行隐藏）；主图K线+MA/EMA/BOLL 叠加；副图窗格（VOL、MACD、KDJ、RSI、WR）用多 chart 同步 logical range（时间轴联动、十字光标联动）；支持滚轮缩放、拖拽平移、双击复位。**画线工具已整体删除**（无 drawing.js、无工具栏）。
- `analysis_view.js`：把 `/api/analysis` 的 annotations 渲染成图上标记（markers + 线段 + 区域色带），星号信号用金色大号标记；hover 显示 detail；图下渲染 summary 分析卡——趋势/结构/动量/量能/关键位/目标止损各占独立一段（2026-08-01 起不再展示背景/交易尺度行），展望按句分段落。
- `app.js`：单页主控（TOP20 分页与 detail 页已随榜单下线删除）。
  - 顶部指数选择按钮行（8个宽基），单列指数看板（K线+推背图+文字分析）；下方全市场搜索框（防抖 300ms，后端检索），结果列表（代码/名称/市场/类型），点击装入下方个股看板（与指数看板同组件，全尺寸）；右侧栏自选股/搜索历史（localStorage 持久化）。
- 手机端布局保留（响应式媒体查询）。
- 密度控制：图上同屏标注超过 12 个时按 star 优先聚合，其余收成时间轴下方小点，hover 展开。

## 5. 启动与运维

- `启动看板.bat`：`.venv\Scripts\python.exe -m backend.app.main`（uvicorn 0.0.0.0:8600）→ 延迟 4 秒打开 `http://127.0.0.1:8600`。
- 8 指数 K线/推背图走 `data/baked_charts.json`（scripts/bake_charts.py 烘焙提交）：冷启动秒开；启动后 daemon 线程自动增量追新 K线并预计算指数分析缓存，追新完成后无缝切回实时数据。文件缺失时全部回退实时路径，仅首次加载较慢。
- TS_TOKEN 未设置：行情类接口 503，baked_charts 烘焙内容照常可用，warmer 跳过。

## 6. 质量红线

1. `TS_TOKEN` 等密钥只从环境变量读，不进代码、不进 git 跟踪文件（含文档、测试脚本、提交信息）。
2. 不引入未在本文件列出的第三方依赖；新依赖必须写入 requirements.txt。
3. 所有面向展示的K线数据默认前复权（指数除外）；前复权口径 = OHLC × adj_factor / max(adj_factor)，不得在别处另造口径。
4. 访问 API 必须走 `ts_api.call_api`（统一限流/重试/缓存），禁止绕过它直接 requests 打行情端点；`data/api_cache/` 为本地缓存，git 忽略。
5. 每个后端模块附带 `if __name__ == '__main__':` 冒烟自检（不触网的模块用合成数据即可）。
6. 防未来函数：任何信号生成函数必须只依赖当前及历史行；review 时重点检查 shift/rolling 方向。
