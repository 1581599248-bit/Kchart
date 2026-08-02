# RYAN K线推背图

A股技术面多因子打分与K线结构分析系统。

## 一键启动

双击 **`启动看板.bat`**，浏览器自动打开 `http://127.0.0.1:8600`。

## 功能

- **指数看板**：上证/深成/创业板/科创50/沪深300/中证500/中证1000/中证2000 的 K线 + 推背图标注 + 文字分析
- **TOP20 瀑布流**：技术面五因子组打分排名，每标的含迷你K线、推背图、因子组得分、目标位/止损
- **搜索**：全市场指数与个股检索，完整K线看板（60m/日/周/月，MACD/KDJ/RSI/WR/BOLL/MA/EMA，画线工具）
- **手机端**：响应式布局（≤640px 断点），iPhone 与电脑同一 Wi-Fi 时用 `http://<电脑局域网IP>:8600` 打开

## 打分模型

五因子组等权（证据驱动，详见 `docs/MODEL_DESIGN.md`）：
短期反转 / 换手与量能 / 趋势质量 / 波动与彩票 / 结构形态。
斐波那契、谐波形态、MACD/KDJ 等仅作图上标注展示，不进打分公式。

## 数据

- 权威库（只读外接，**不在本仓库内**）：默认路径 `C:/Users/Administrator/Desktop/完整A股量化模型 数据库/RYAN重要全市场K线数据库.duckdb`，用环境变量 `RYAN_AUTH_DB` 指向实际位置
- 系统记忆（打分缓存/回测结果）：`data/results.duckdb`，git 忽略，首次运行自动重建
- API token 只从环境变量 `TS_TOKEN` 读取

## 新机器部署

1. 运行 `scripts/setup_env.bat` 创建 `.venv` 并安装依赖
2. 把权威库 duckdb 拷到本机，设 `set RYAN_AUTH_DB=<权威库duckdb路径>`（或放到默认路径）
3. 双击 `启动看板.bat`

## 架构

见 `docs/ARCHITECTURE.md`（唯一开发依据）。后端 FastAPI + DuckDB，前端原生 SPA + Lightweight Charts（已本地化）。
