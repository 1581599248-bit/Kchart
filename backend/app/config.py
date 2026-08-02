"""全局配置（ARCHITECTURE.md 第3节 config.py 规范）。

路径常量、服务参数、回测成本参数。权威库路径可用环境变量 RYAN_AUTH_DB 覆盖。
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]  # 项目根 RYAN技术面K线模型/

# 权威库（只读外接，禁止写入）
AUTH_DB_PATH = os.environ.get(
    "RYAN_AUTH_DB",
    r"C:/Users/Administrator/Desktop/完整A股量化模型 数据库/RYAN重要全市场K线数据库.duckdb",
)

# 派生数据（研究结果库，可写）
DATA_DIR = BASE_DIR / "data"
RESULTS_DB_PATH = DATA_DIR / "results.duckdb"

FRONTEND_DIR = BASE_DIR / "frontend"

HOST = "0.0.0.0"   # 监听所有网卡：手机(iPhone)与电脑同一 Wi-Fi 时可用 电脑局域网IP:8600 打开
PORT = int(os.environ.get("PORT", 8600))   # Render 等平台通过 PORT 环境变量分配端口

# 快照模式（Render 部署）：权威库为精简快照（scripts/export_snapshot.py 导出），
# 不含个股 60 分钟线——启动检查放宽、个股 60m 接口明确报错、前端隐藏个股 60m 按钮
SNAPSHOT = os.environ.get("RYAN_SNAPSHOT") == "1"

MODEL_VERSION = "v1.0"

# 回测成本参数
COMMISSION_RATE = 0.00025   # 佣金万2.5双边
SLIPPAGE_RATE = 0.001       # 滑点千1双边
STAMP_TAX_SELL = 0.0005     # 卖出印花税万5

# 分页1顶部 8 个宽基指数（ts_code 已在 index_master 中逐一核实存在）
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

if __name__ == "__main__":
    print("AUTH_DB_PATH :", AUTH_DB_PATH, os.path.exists(AUTH_DB_PATH))
    print("RESULTS_DB_PATH:", RESULTS_DB_PATH)
    print("FRONTEND_DIR :", FRONTEND_DIR, FRONTEND_DIR.exists())
    print("HOST/PORT    :", HOST, PORT)
    print("MODEL_VERSION:", MODEL_VERSION)
    print("BROAD_INDEX_CODES:", len(BROAD_INDEX_CODES))
