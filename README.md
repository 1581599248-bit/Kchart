# RYAN K线推背图

A股技术面多因子打分与K线结构分析系统。

## 一键启动

1. 设置数据 API token（只需一次）：`setx TS_TOKEN <你的token>`（重开终端生效），或当前窗口临时 `set TS_TOKEN=<你的token>`
2. 双击 **`启动看板.bat`**，浏览器自动打开 `http://127.0.0.1:8600`

## 功能

- **指数看板**：上证/深成/创业板/科创50/沪深300/中证500/中证1000/中证2000 的日线 K线 + 推背图标注 + 文字分析
- **搜索**：全市场指数与个股检索，完整K线看板（日线，MACD/KDJ/RSI/WR/BOLL/MA/EMA）+ 自选股/搜索历史
- **手机端**：响应式布局（≤640px 断点），iPhone 直接打开线上地址；局域网内也可用 `http://<电脑局域网IP>:8600`

## 打分模型

五因子组等权（证据驱动，详见 `docs/MODEL_DESIGN.md`）：
短期反转 / 换手与量能 / 趋势质量 / 波动与彩票 / 结构形态。
斐波那契、谐波形态、MACD/KDJ 等仅作图上标注展示，不进打分公式。

## 数据

- 全部行情数据来自 **tushare 兼容 HTTP API**：token 从环境变量 `TS_TOKEN` 读取（**不要写进任何会提交的文件**），接口地址默认 `https://ts.gyzcloud.top/api`，可用 `TS_URL` 覆盖
- 运行时磁盘缓存：`data/api_cache/`（git 忽略，自动增量更新）
- 推背图分析缓存：`data/results.duckdb`（git 忽略，自动重建）
- **8 宽基指数的 K线/推背图是烘焙静态文件** `data/baked_charts.json`（`scripts/bake_charts.py` 生成并随仓库提交）：冷启动秒开，服务启动后后台线程自动增量追新、追新完成即切回实时数据。更新 = 重跑脚本 + 提交该文件
- 未设置 `TS_TOKEN` 时：烘焙的指数 K线/推背图仍可看，行情类接口返回 503 提示

## 新机器部署

1. 运行 `scripts/setup_env.bat` 创建 `.venv` 并安装依赖
2. `setx TS_TOKEN <你的token>`
3. 双击 `启动看板.bat`

## Render 部署（线上版）

线上版与本地完全一致：指数 K线/推背图用仓库里的烘焙文件（冷启动秒开），启动后后台自动从 API 增量追新；个股日线数据运行时从 API 实时拉取。

1. Render Dashboard → New → Blueprint → 选本仓库，`render.yaml` 自动识别（免费档，`/api/meta` 健康检查）
2. 部署时 Render 会提示填写 **`TS_TOKEN`**（render.yaml 里 `sync:false`，密钥不进仓库），粘贴你的 token
3. 等构建完成（约 2-3 分钟），访问 `https://<服务名>.onrender.com`

注意：免费档 15 分钟无访问会休眠，冷启动约 30 秒；token 到期后换新值（Render 服务 Settings → Environment 改 `TS_TOKEN` 即可，无需改代码）。

## 架构

见 `docs/ARCHITECTURE.md`（唯一开发依据）。后端 FastAPI + DuckDB（分析缓存），前端原生 SPA + Lightweight Charts（已本地化）。
