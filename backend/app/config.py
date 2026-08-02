"""全局配置（ARCHITECTURE.md 第3节 config.py 规范）。

路径常量、服务参数、tushare 兼容 HTTP API 参数（token 只允许环境变量 TS_TOKEN）。
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]  # 项目根 RYAN技术面K线模型/

# 派生数据（研究结果库，可写）
DATA_DIR = BASE_DIR / "data"
RESULTS_DB_PATH = DATA_DIR / "results.duckdb"

FRONTEND_DIR = BASE_DIR / "frontend"

HOST = "0.0.0.0"   # 监听所有网卡：手机(iPhone)与电脑同一 Wi-Fi 时可用 电脑局域网IP:8600 打开
PORT = int(os.environ.get("PORT", 8600))   # Render 等平台通过 PORT 环境变量分配端口

MODEL_VERSION = "v1.0"

# tushare 兼容 HTTP API（backend/app/ts_api.py）
TS_TOKEN = os.environ.get("TS_TOKEN", "")                    # 禁止写进代码/跟踪文件
TS_URL = os.environ.get("TS_URL", "https://ts.gyzcloud.top/api")
TS_MIN_INTERVAL = float(os.environ.get("TS_MIN_INTERVAL", 0.45))  # 最小调用间隔秒（≈133次/分）
TS_MAX_INFLIGHT = int(os.environ.get("TS_MAX_INFLIGHT", 2))      # 同时在飞请求数上限（上游有并发限制）
KLINE_EARLIEST = os.environ.get("KLINE_EARLIEST", "20180101")    # K线拉取最早日期：全量拉取从该日起（比展示窗口多留 2 年指标 warmup）
KLINE_DISPLAY_START = os.environ.get("KLINE_DISPLAY_START", "2020-01-01")  # 图上展示/烘焙只从该日起（缩短加载）

# 分页1顶部 8 个宽基指数
BROAD_INDEX_CODES = [
    "000001.SH",   # 上证指数
    "399001.SZ",   # 深证成指
    "399006.SZ",   # 创业板指
    "000688.SH",   # 科创50
    "000300.SH",   # 沪深300
    "000905.SH",   # 中证500
    "000852.SH",   # 中证1000
    "932000.CSI",  # 中证2000
]
BROAD_INDEX_NAMES = {
    "000001.SH": "上证指数",
    "399001.SZ": "深证成指",
    "399006.SZ": "创业板指",
    "000688.SH": "科创50",
    "000300.SH": "沪深300",
    "000905.SH": "中证500",
    "000852.SH": "中证1000",
    "932000.CSI": "中证2000",
}

if __name__ == "__main__":
    print("RESULTS_DB_PATH:", RESULTS_DB_PATH)
    print("FRONTEND_DIR :", FRONTEND_DIR, FRONTEND_DIR.exists())
    print("HOST/PORT    :", HOST, PORT)
    print("TS_URL       :", TS_URL, "| TS_TOKEN 已设置:", bool(TS_TOKEN))
    print("MODEL_VERSION:", MODEL_VERSION)
    print("BROAD_INDEX_CODES:", len(BROAD_INDEX_CODES))
