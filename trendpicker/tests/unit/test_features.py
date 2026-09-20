"""特征工程单元测试.

覆盖范围:
    - Min-Max 归一化 (常量序列除零保护 / 越界截断)
    - 3σ 截断 (outlier clipping)
    - 增速斜率 β 计算 (线性增长 / 下降趋势 / 窗口大小影响)
    - 时间窗口聚合

覆盖率目标: 80%
"""

import numpy as np
import pandas as pd
import pytest

from trendpicker.features import (
    growth_slope,
    min_max_normalize,
    three_sigma_clip,
    window_aggregate,
)


class TestNormalization:
    """Min-Max 归一化."""

    def test_min_max_normalize_basic(self):
        """基础归一化: 数据落 [0, 1]."""
        series = pd.Series([10, 20, 30, 40, 50])
        result = min_max_normalize(series)
        assert result.min() == 0.0
        assert result.max() == 1.0
        assert result.iloc[2] == 0.5

    def test_min_max_normalize_constant_series(self):
        """常量序列: 除零保护, 输出全 0 或全 0.5."""
        series = pd.Series([5, 5, 5, 5, 5])
        result = min_max_normalize(series)
        # 常量序列输出全 0.5
        assert all(v == 0.5 for v in result)

    def test_min_max_normalize_out_of_range(self):
        """超出 [0, 1] 的值被截断到边界."""
        series = pd.Series([0, 1, 100])
        result = min_max_normalize(series)
        assert result.max() <= 1.0
        assert result.min() >= 0.0


class TestSigmaClipping:
    """3σ 截断."""

    def test_three_sigma_clip_no_outliers(self):
        """标准正态分布数据无变化."""
        np.random.seed(42)
        data = pd.Series(np.random.randn(1000) * 10 + 50)
        result = three_sigma_clip(data)
        # 1000 个标准正态样本, 超出 3σ 的极少 (< 0.3%)
        diff = (result - data).abs()
        assert (diff > 0.01).sum() <= 10

    def test_three_sigma_clip_outliers(self):
        """离群点被截断到 μ ± 3σ 边界."""
        # 构造数据: 大部分在 10-20 范围, 一个极端离群点
        base = list(range(10, 20)) * 5  # 50 个点在 10-19
        series = pd.Series(base + [1000])
        result = three_sigma_clip(series)
        mean = series.mean()
        std = series.std()
        upper_bound = mean + 3 * std
        assert result.max() <= upper_bound + 1e-6
        # 离群点 1000 应被截断到上界
        assert result.iloc[-1] < 1000

    def test_three_sigma_clip_empty_series(self):
        """空序列不抛异常."""
        series = pd.Series([], dtype=float)
        result = three_sigma_clip(series)
        assert len(result) == 0


class TestGrowthSlope:
    """增速斜率 β 计算."""

    def test_beta_linear_growth(self):
        """线性增长: β 接近斜率."""
        # y = 2x + 1, 斜率 = 2
        series = pd.Series([1, 3, 5, 7, 9, 11, 13, 15, 17, 19])
        beta = growth_slope(series, window=10)
        assert abs(beta - 2.0) < 0.01

    def test_beta_negative_trend(self):
        """下降趋势: β 为负."""
        series = pd.Series([20, 18, 16, 14, 12, 10, 8, 6, 4, 2])
        beta = growth_slope(series, window=10)
        assert beta < 0
        assert abs(beta - (-2.0)) < 0.01

    def test_beta_window_size_effect(self):
        """不同窗口大小对 β 的影响."""
        # 前 10 个点上升, 后 10 个点下降
        series = pd.Series(list(range(10)) + list(range(10, 0, -1)))
        beta_short = growth_slope(series, window=5)
        beta_long = growth_slope(series, window=20)
        # 短窗口(看最近5个)→ 下降趋势 (负)
        assert beta_short < 0
        # 长窗口(看全部)→ 先升后降, 斜率接近0
        assert abs(beta_long) < abs(beta_short) + 0.01


class TestWindowAggregate:
    """时间窗口聚合."""

    def test_window_aggregate_mean(self):
        """滑动窗口均值."""
        dates = pd.date_range("2024-01-01", periods=5)
        df = pd.DataFrame({"date": dates, "value": [1, 2, 3, 4, 5]})
        result = window_aggregate(df, "date", "value", window_days=3, agg_func="mean")
        # 第1天: mean(1) = 1
        assert result.iloc[0]["value"] == 1.0
        # 第3天: mean(1,2,3) = 2
        assert abs(result.iloc[2]["value"] - 2.0) < 0.01

    def test_window_aggregate_empty(self):
        """空 DataFrame 不抛异常."""
        df = pd.DataFrame({"date": [], "value": []})
        result = window_aggregate(df, "date", "value")
        assert len(result) == 0
