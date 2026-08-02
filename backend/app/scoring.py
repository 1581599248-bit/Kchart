"""RYAN 技术面多因子打分模型 v2（证据驱动，2026-08-01 定稿）。

设计依据：docs/MODEL_DESIGN.md §3-§4；接口规范：docs/ARCHITECTURE.md §3 scoring.py。

五因子组（组间等权 0.20，总分 0~100 横截面百分位，越高越优）：
- G1 短期反转：RET5(-)、RET20(-)
- G2 换手与量能：TURN20(-)、ABNTURN(-)、VOLCO5(+)（5日价升量增/价跌量缩一致度）
- G3 趋势质量：TRENDQ(+)（close>MA250 且 MA250 上行）、MA60SLOPE(+)（MA60 的20日斜率）、
  TRENDSTR(+)（ADX14 乘以 PDI/MDI 相对强度，刻画 "ADX14>25 且 PDI>MDI 程度"）
- G4 波动与彩票：VOL20(-)、MAX5(-)、DIST250H(+)（close/250日最高价）
- G5 结构形态：向量化双底/双顶+颈线突破事件（右5根确认，半衰期20根指数衰减）

横截面流水线（每个交易日）：
原始因子 → median/MAD 稳健 z-score → ±3 缩尾 → 方向统一（负向取反）
→ 组内等权 → 组内再标准化 → 五组等权 → 横截面百分位 0~100。

防未来函数：全部输入仅含 trade_date <= t 的行；pivot/结构事件右侧确认后才生效；
换手/收益类因子用 t 日及以前数据，t+1 开盘执行（执行在 backtest.py）。

参数全部冻结为文件头常量（MODEL_DESIGN.md §5 防过拟合协议：不允许网格寻优）。

本模块只做面板向量化计算：输入 universe 面板 DataFrame，输出全日期或指定日期的
横截面得分。禁止在本模块内连接数据库逐股拉取。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# ============ 冻结参数（MODEL_DESIGN.md §5：行业标准值，不允许优化） ============
MODEL_VERSION = "scoring_v2.0"

RET_SHORT = 5            # RET5
RET_LONG = 20            # RET20
TURN_WIN = 20            # TURN20 窗口
ABN_WIN = 250            # ABNTURN 基准窗口
VOLCO_WIN = 5            # 量价配合窗口
MA_TREND = 250           # 年线
MA_MID = 60              # MA60
MA60_SLOPE_WIN = 20      # MA60 斜率窗口
MA250_DIR_WIN = 20       # MA250 上行判定窗口
ADX_WIN = 14             # ADX14（Wilder 平滑）
VOL_WIN = 20             # VOL20
MAX_WIN = 5              # MAX5
HIGH_WIN = 250           # 250日最高价

# G5 结构形态（patterns.py 精确版的面板向量化近似：双底/双顶 + 颈线突破）
PIVOT_LEFT = 5           # pivot 左侧根数
PIVOT_RIGHT = 5          # pivot 右侧确认根数
DB_TOL = 0.05            # 双底/双顶两底（顶）价差容差 5%
DB_MIN_SEP = 10          # 两底（顶）最小间隔根数
DB_MAX_SEP = 250         # 两底（顶）最大间隔根数
DB_MIN_DEPTH = 0.02      # 颈线相对底（顶）最小深度 2%
DB_INVALID_PCT = 0.03    # 结构失效：收盘跌破（升破）底（顶）3%
FORMING_SCORE = 0.3      # 构筑中分值
CONFIRMED_SCORE = 1.0    # 已确认突破分值
HALF_LIFE = 20           # 半衰期 20 根指数衰减

# 因子方向（+1 越高越优，-1 取反）
FACTOR_DIRECTION = {
    "RET5": -1, "RET20": -1,
    "TURN20": -1, "ABNTURN": -1, "VOLCO5": +1,
    "TRENDQ": +1, "MA60SLOPE": +1, "TRENDSTR": +1,
    "VOL20": -1, "MAX5": -1, "DIST250H": +1,
    "G5": +1,
}

GROUPS = {
    "G1": ["RET5", "RET20"],
    "G2": ["TURN20", "ABNTURN", "VOLCO5"],
    "G3": ["TRENDQ", "MA60SLOPE", "TRENDSTR"],
    "G4": ["VOL20", "MAX5", "DIST250H"],
    "G5": ["G5"],
}
GROUP_NAMES = {
    "G1": "短期反转", "G2": "换手量能", "G3": "趋势质量", "G4": "波动彩票", "G5": "结构形态",
}

WINSOR_Z = 3.0           # ±3 缩尾
MIN_GROUPS = 4           # 总分至少需要的非空组数（上市 120~250 日的股票 G3 部分因子未就绪）

_EPS = 1e-12


# ======================= 原始因子（面板向量化） =======================

def compute_raw_factors(panel: pd.DataFrame) -> pd.DataFrame:
    """输入 universe 面板 → 追加原始因子列（未做方向/标准化）。

    panel 必须含列：ts_code, trade_date, open, high, low, close, vol, turnover_rate。
    （amount 列可选；turnover_rate 缺失的行相关因子为 NaN，由组内等权跳过。）
    全部计算仅使用 ≤ t 的历史行（rolling/shift 方向均已核查）。
    """
    df = panel.sort_values(["ts_code", "trade_date"]).reset_index(drop=True).copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])   # 统一为 Timestamp，便于 isin/比较
    g = df.groupby("ts_code", sort=False)

    close = df["close"]
    high = df["high"]
    low = df["low"]
    vol = df["vol"]
    turn = df["turnover_rate"] if "turnover_rate" in df.columns else pd.Series(np.nan, index=df.index)

    # ---- 收益类 ----
    ret1 = g["close"].pct_change()
    df["RET5"] = g["close"].pct_change(RET_SHORT)
    df["RET20"] = g["close"].pct_change(RET_LONG)

    # ---- G2 换手与量能 ----
    df["TURN20"] = g["turnover_rate"].transform(lambda s: s.rolling(TURN_WIN, min_periods=TURN_WIN).mean()) \
        if "turnover_rate" in df.columns else np.nan
    turn250 = g["turnover_rate"].transform(lambda s: s.rolling(ABN_WIN, min_periods=ABN_WIN // 2).mean()) \
        if "turnover_rate" in df.columns else pd.Series(np.nan, index=df.index)
    df["ABNTURN"] = turn / turn250.replace(0, np.nan)
    # 5日量价配合度：价升量增/价跌量缩的一致天数占比
    prev_close = g["close"].shift(1)
    prev_vol = g["vol"].shift(1)
    price_up = close > prev_close
    vol_up = vol > prev_vol
    agree = ((price_up & vol_up) | (~price_up & ~vol_up)).astype(float)
    agree[(close == prev_close) | (vol == prev_vol) | prev_close.isna() | prev_vol.isna()] = np.nan
    df["VOLCO5"] = agree.groupby(df["ts_code"], sort=False).transform(
        lambda s: s.rolling(VOLCO_WIN, min_periods=VOLCO_WIN).mean()
    )

    # ---- G3 趋势质量 ----
    ma60 = g["close"].transform(lambda s: s.rolling(MA_MID, min_periods=MA_MID).mean())
    ma250 = g["close"].transform(lambda s: s.rolling(MA_TREND, min_periods=MA_TREND).mean())
    ma250_up = ma250 > ma250.groupby(df["ts_code"], sort=False).shift(MA250_DIR_WIN)
    df["TRENDQ"] = ((close > ma250) & ma250_up).astype(float)
    df.loc[ma250.isna(), "TRENDQ"] = np.nan
    ma60_prev = ma60.groupby(df["ts_code"], sort=False).shift(MA60_SLOPE_WIN)
    df["MA60SLOPE"] = ma60 / ma60_prev - 1.0

    # ADX14（Wilder 平滑 = ewm(alpha=1/n, adjust=False)）
    up_move = high - g["high"].shift(1)
    down_move = g["low"].shift(1) - low
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    df["_TR"] = tr
    df["_PDM"] = plus_dm
    df["_MDM"] = minus_dm
    alpha = 1.0 / ADX_WIN

    def _wilder(s: pd.Series) -> pd.Series:
        return s.ewm(alpha=alpha, adjust=False, min_periods=ADX_WIN).mean()

    tr_s = g["_TR"].transform(_wilder)
    pdm_s = g["_PDM"].transform(_wilder)
    mdm_s = g["_MDM"].transform(_wilder)
    pdi = 100.0 * pdm_s / tr_s.replace(0, np.nan)
    mdi = 100.0 * mdm_s / tr_s.replace(0, np.nan)
    dx = 100.0 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    df["_DX"] = dx
    adx = g["_DX"].transform(_wilder)
    # "ADX14>25 且 PDI>MDI 程度"：ADX 乘以 PDI/MDI 相对强弱（有符号，连续值）
    df["TRENDSTR"] = adx * (pdi - mdi) / (pdi + mdi).replace(0, np.nan)

    # ---- G4 波动与彩票 ----
    df["_RET1"] = ret1
    df["VOL20"] = g["_RET1"].transform(lambda s: s.rolling(VOL_WIN, min_periods=VOL_WIN).std())
    df["MAX5"] = g["_RET1"].transform(lambda s: s.rolling(MAX_WIN, min_periods=MAX_WIN).max())
    high250 = g["high"].transform(lambda s: s.rolling(HIGH_WIN, min_periods=HIGH_WIN // 2).max())
    df["DIST250H"] = close / high250.replace(0, np.nan)

    # ---- G5 结构形态（向量化双底/双顶近似） ----
    df["G5"] = _structural_score_panel(df)

    df.drop(columns=["_TR", "_PDM", "_MDM", "_DX", "_RET1"], inplace=True)
    return df


# ======================= G5 结构形态 =======================

def _pivot_mask(arr: np.ndarray, left: int, right: int, kind: str) -> np.ndarray:
    """pivot 掩码（未做右确认平移）：严格局部最值。

    kind='L'：low[i] 严格小于 [i-left, i-1] 与 [i+1, i+right] 两段的最小值；
    kind='H' 镜像。严格不等式排除平台区伪 pivot。右侧不足 right 根的位置永不生效。
    """
    n = len(arr)
    s = pd.Series(arr)
    left_min = s.rolling(left, min_periods=left).min().shift(1)          # [i-left, i-1]
    right_min = s.shift(-right).rolling(right, min_periods=right).min()  # [i+1, i+right]
    left_max = s.rolling(left, min_periods=left).max().shift(1)
    right_max = s.shift(-right).rolling(right, min_periods=right).max()
    if kind == "L":
        mask = ((s < left_min) & (s < right_min)).to_numpy().copy()
    else:
        mask = ((s > left_max) & (s > right_max)).to_numpy().copy()
    mask[:left] = False
    mask[n - right:] = False   # 右侧未满 right 根：尚未确认，永不生效
    return mask


def _structural_score_stock(low: np.ndarray, high: np.ndarray, close: np.ndarray) -> np.ndarray:
    """单股双底/双顶+颈线突破事件分值序列（G5 原始分，面板近似版）。

    - pivot 右 5 根确认后才生效（confirm_idx = pivot_idx + PIVOT_RIGHT）；
    - 双底：两个相邻 pivot 低价差 ≤ DB_TOL，间隔 [DB_MIN_SEP, DB_MAX_SEP]，
      颈线 = 两底间最高价且深度 ≥ DB_MIN_DEPTH；第二底确认后构筑中 +0.3，
      收盘突破颈线起 +1；收盘跌破双底 (1-DB_INVALID_PCT) 失效；
    - 双顶镜像，分值取负；
    - 所有事件按 confirm/breakout 距 t 的根数做半衰期 20 根指数衰减并叠加。
    """
    n = len(close)
    out = np.zeros(n)
    if n < PIVOT_LEFT + PIVOT_RIGHT + 2:
        return out
    decay = 0.5 ** (np.arange(n) / HALF_LIFE)   # decay[k] = 距事件 k 根的权重

    pl = np.where(_pivot_mask(low, PIVOT_LEFT, PIVOT_RIGHT, "L"))[0]
    ph = np.where(_pivot_mask(high, PIVOT_LEFT, PIVOT_RIGHT, "H"))[0]

    def scan(piv: np.ndarray, base: np.ndarray, sign: float) -> None:
        # sign=+1 双底（颈线=max high，突破=close>neck），sign=-1 双顶镜像
        for k in range(len(piv) - 1):
            i1, i2 = piv[k], piv[k + 1]
            sep = i2 - i1
            if sep < DB_MIN_SEP or sep > DB_MAX_SEP:
                continue
            p1, p2 = base[i1], base[i2]
            if not (np.isfinite(p1) and np.isfinite(p2)) or p1 <= 0:
                continue
            if abs(p2 - p1) / p1 > DB_TOL:
                continue
            seg = high[i1:i2 + 1] if sign > 0 else low[i1:i2 + 1]
            neck = seg.max() if sign > 0 else seg.min()
            depth = (neck - max(p1, p2)) / max(p1, p2) if sign > 0 else (min(p1, p2) - neck) / min(p1, p2)
            if depth < DB_MIN_DEPTH:
                continue
            confirm = i2 + PIVOT_RIGHT   # 右确认后才生效
            if confirm >= n:
                continue
            floor = min(p1, p2) * (1 - DB_INVALID_PCT)
            ceil = max(p1, p2) * (1 + DB_INVALID_PCT)
            breakout = None
            end = n
            for j in range(confirm, n):
                c = close[j]
                if sign > 0:
                    if c < floor:      # 失效
                        end = j
                        break
                    if c > neck:
                        breakout = j
                        end = n
                        break
                else:
                    if c > ceil:
                        end = j
                        break
                    if c < neck:
                        breakout = j
                        end = n
                        break
            # 构筑中：confirm → breakout-1（或失效前）
            form_end = breakout if breakout is not None else end
            if form_end > confirm:
                k_arr = np.arange(form_end - confirm)
                out[confirm:form_end] += sign * FORMING_SCORE * decay[k_arr]
            if breakout is not None:
                k_arr = np.arange(n - breakout)
                out[breakout:] += sign * CONFIRMED_SCORE * decay[k_arr]

    scan(pl, low, +1.0)    # 双底
    scan(ph, high, -1.0)   # 双顶
    return out


def _structural_score_panel(df: pd.DataFrame) -> pd.Series:
    """面板级 G5：按 ts_code 分组对 numpy 数组计算（不触库、纯内存计算）。"""
    result = np.zeros(len(df))
    codes = df["ts_code"].to_numpy()
    low = df["low"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    # 分组边界（df 已按 ts_code, trade_date 排序）
    _, starts = np.unique(codes, return_index=True)
    starts = np.append(starts, len(df))
    for a, b in zip(starts[:-1], starts[1:]):
        result[a:b] = _structural_score_stock(low[a:b], high[a:b], close[a:b])
    return pd.Series(result, index=df.index)


# ======================= 横截面流水线 =======================

def _robust_z(s: pd.Series) -> pd.Series:
    """median/MAD 稳健 z-score；MAD 退化时回退 std，再退化则置 0。"""
    med = s.median()
    mad = (s - med).abs().median()
    scale = 1.4826 * mad
    if not np.isfinite(scale) or scale < _EPS:
        std = s.std()
        if not np.isfinite(std) or std < _EPS:
            return pd.Series(0.0, index=s.index)
        return (s - med) / std
    return (s - med) / scale


def compute_scores_panel(factors: pd.DataFrame, dates=None) -> pd.DataFrame:
    """原始因子面板 → 全日期横截面得分。

    返回 DataFrame[trade_date, ts_code, score, rank, G1..G5]（组分为标准化后等权组内合成，
    score 为 0~100 横截面百分位，rank=1 最优）。
    dates：可选，只输出这些交易日（list / set）。
    """
    df = factors
    if dates is not None:
        dset = set(pd.to_datetime(list(dates)))
        df = df[df["trade_date"].isin(dset)]
    cols = ["trade_date", "ts_code"]
    work = df[cols + list(FACTOR_DIRECTION)].copy()

    by_date = work.groupby("trade_date", sort=True)
    # 1) 稳健 z → ±3 缩尾 → 方向统一
    zcols = {}
    for f, sign in FACTOR_DIRECTION.items():
        z = by_date[f].transform(_robust_z).clip(-WINSOR_Z, WINSOR_Z) * sign
        zcols[f] = z
    zdf = pd.DataFrame(zcols, index=work.index)
    zdf["trade_date"] = work["trade_date"]
    zdf["ts_code"] = work["ts_code"]

    # 2) 组内等权（跳过 NaN，至少 1 个成员）→ 组内再标准化（稳健 z）
    gdf = zdf[["trade_date", "ts_code"]].copy()
    for gname, members in GROUPS.items():
        comp = zdf[members].mean(axis=1, skipna=True)
        comp[zdf[members].notna().sum(axis=1) == 0] = np.nan
        gdf[gname] = comp.groupby(zdf["trade_date"], sort=True).transform(_robust_z)

    # 3) 五组等权（至少 MIN_GROUPS 组非空）→ 百分位 0~100
    glist = list(GROUPS)
    nvalid = gdf[glist].notna().sum(axis=1)
    total = gdf[glist].mean(axis=1, skipna=True)
    total[nvalid < MIN_GROUPS] = np.nan
    gdf["total"] = total
    gdf["score"] = gdf.groupby("trade_date", sort=True)["total"].rank(pct=True) * 100.0
    gdf["rank"] = gdf.groupby("trade_date", sort=True)["total"].rank(
        ascending=False, method="first"
    ).astype("Int64")
    gdf.loc[gdf["score"].isna(), "rank"] = pd.NA

    out = gdf[["trade_date", "ts_code", "score", "rank"] + glist].dropna(subset=["score"])
    return out.reset_index(drop=True)


def score_asof(panel: pd.DataFrame, asof_date) -> pd.DataFrame:
    """给定面板（需含足够历史）与目标日，输出该日全池得分（含原始因子参考列）。

    返回列：trade_date, ts_code, score, rank, G1..G5 + 原始因子列（行对齐 asof 当日）。
    """
    fac = compute_raw_factors(panel)
    asof = pd.Timestamp(asof_date)
    scores = compute_scores_panel(fac, dates={asof})
    day_fac = fac[fac["trade_date"] == asof][
        ["ts_code"] + list(FACTOR_DIRECTION)
    ]
    return scores.merge(day_fac, on="ts_code", how="left")


# ======================= TOP20 榜单文案 =======================

def make_analysis_brief(group_scores: dict) -> str:
    """TOP20 榜单一句话解读（与现网文案一致）："XX面最强、XX面最弱"。

    group_scores：{G1..G5: 组标准化分}，None 组跳过；无有效组返回空串。
    """
    g_sorted = sorted(((k, v) for k, v in group_scores.items() if v is not None),
                      key=lambda kv: kv[1])
    if not g_sorted:
        return ""
    weak = GROUP_NAMES.get(g_sorted[0][0], "")
    strong = GROUP_NAMES.get(g_sorted[-1][0], "")
    return f"{strong}面最强、{weak}面最弱" if strong else ""


# ======================= 冒烟自检 =======================

if __name__ == "__main__":
    print(make_analysis_brief({"G1": 0.5, "G2": -1.2, "G3": 2.0, "G4": None, "G5": 0.1}))
