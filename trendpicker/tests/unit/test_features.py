"""特征工程单元测试.

覆盖范围:
    - Min-Max 归一化 (常量序列除零保护 / 越界截断)
    - 3σ 截断 (outlier clipping)
    - 增速斜率 β 计算 (线性增长 / 下降趋势 / 窗口大小影响)
    - 时间窗口聚合

覆盖率目标: 80%
"""

import pytest


@pytest.mark.skip(reason="等待 features.py 实现后补全")
class TestNormalization:
    """Min-Max 归一化."""

    def test_min_max_normalize_basic(self):
        """基础归一化: 数据落 [0, 1]."""

    def test_min_max_normalize_constant_series(self):
        """常量序列: 除零保护, 输出全 0 或全 0.5."""

    def test_min_max_normalize_out_of_range(self):
        """超出 [0, 1] 的值被截断到边界."""


@pytest.mark.skip(reason="等待 features.py 实现后补全")
class TestSigmaClipping:
    """3σ 截断."""

    def test_three_sigma_clip_no_outliers(self):
        """标准正态分布数据无变化."""

    def test_three_sigma_clip_outliers(self):
        """离群点被截断到 μ ± 3σ 边界."""

    def test_three_sigma_clip_empty_series(self):
        """空序列不抛异常."""


@pytest.mark.skip(reason="等待 features.py 实现后补全")
class TestGrowthSlope:
    """增速斜率 β 计算."""

    def test_beta_linear_growth(self):
        """线性增长: β 接近斜率."""

    def test_beta_negative_trend(self):
        """下降趋势: β 为负."""

    def test_beta_window_size_effect(self):
        """不同窗口大小对 β 的影响."""
