"""特征工程模块.

提供:
    - min_max_normalize: Min-Max 归一化 (常量序列除零保护 / 越界截断)
    - three_sigma_clip: 3σ 截断 (outlier clipping)
    - growth_slope: 增速斜率 β 计算 (线性回归斜率)
    - window_aggregate: 时间窗口聚合
"""

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def min_max_normalize(series: pd.Series) -> pd.Series:
    """Min-Max 归一化, 将数据缩放到 [0, 1] 区间.

    常量序列 (max == min) 触发除零保护, 输出全 0.5.
    越界值 (理论上不该出现) 被截断到 [0, 1].

    Args:
        series: 输入数据 (pd.Series, 数值类型)

    Returns:
        归一化后的 pd.Series, 值域 [0, 1]
    """
    if series.empty:
        return series.astype(float)

    col_min = series.min()
    col_max = series.max()

    if col_max == col_min:
        # 常量序列: 除零保护, 输出全 0.5
        return pd.Series(np.full(len(series), 0.5), index=series.index, dtype=float)

    normalized = (series - col_min) / (col_max - col_min)
    # 越界截断 (防止浮点误差)
    return normalized.clip(lower=0.0, upper=1.0)


def three_sigma_clip(series: pd.Series) -> pd.Series:
    """3σ 截断: 将超出 μ ± 3σ 范围的离群点截断到边界.

    空序列不抛异常, 原样返回.
    标准差为 0 (常量序列) 时, 所有值在 μ ± 3σ 范围内, 无需截断.

    Args:
        series: 输入数据 (pd.Series, 数值类型)

    Returns:
        截断后的 pd.Series
    """
    if series.empty:
        return series.astype(float)

    mean = series.mean()
    std = series.std()

    if std == 0 or np.isnan(std):
        # 常量序列或单元素序列: 无离群点
        return series.astype(float)

    lower = mean - 3 * std
    upper = mean + 3 * std

    return series.clip(lower=lower, upper=upper)


def growth_slope(series: pd.Series, window: int = 14) -> float:
    """计算增速斜率 β (线性回归斜率).

    使用最小二乘法拟合 series 对时间索引的线性回归,
    斜率 β 反映增长趋势:
        - β > 0: 上升趋势
        - β < 0: 下降趋势
        - β ≈ 0: 趋势平稳

    Args:
        series: 输入数据 (pd.Series, 数值类型)
        window: 窗口大小 (用于截取最近 N 个数据点), 默认 14

    Returns:
        斜率 β (float), 空序列返回 0.0
    """
    if series.empty or len(series) < 2:
        return 0.0

    # 截取最近 window 个数据点
    recent = series.tail(window)
    if len(recent) < 2:
        return 0.0

    y = recent.to_numpy(dtype=float)
    x = np.arange(len(y), dtype=float)

    # 最小二乘法: β = cov(x, y) / var(x)
    x_mean = x.mean()
    y_mean = y.mean()
    cov_xy = np.sum((x - x_mean) * (y - y_mean))
    var_x = np.sum((x - x_mean) ** 2)

    if var_x == 0:
        return 0.0

    return float(cov_xy / var_x)


def window_aggregate(
    df: pd.DataFrame,
    date_col: str,
    value_col: str,
    window_days: int = 14,
    agg_func: str = "mean",
) -> pd.DataFrame:
    """时间窗口聚合: 按 window_days 天的滑动窗口聚合指定列.

    Args:
        df: 输入 DataFrame, 必须包含 date_col 和 value_col
        date_col: 日期列名
        value_col: 数值列名
        window_days: 窗口天数, 默认 14
        agg_func: 聚合函数, 默认 "mean"

    Returns:
        聚合后的 DataFrame, 包含 date_col 和聚合后的 value_col
    """
    if df.empty:
        return df

    df = df.copy()
    df[date_col] = pd.to_datetime(df[date_col])
    df = df.sort_values(date_col)

    df = (
        df.set_index(date_col)
        .rolling(f"{window_days}D", min_periods=1)
        .agg(agg_func)
        .reset_index()
    )

    return df
