"""爆款标签构造测试 (回归基线核心).

爆款定义: 14 天内进入叶子类目前 5% 且持续 ≥ 5 天

标签构造脚本的 bug 会污染所有下游模型, 必须独立测试。
覆盖率目标: 90%
"""

import pandas as pd
import pytest

from trendpicker.label import (
    compute_category_percentile,
    construct_hot_labels,
)


def _make_test_data(
    n_products=20,
    n_days=14,
    category_id="CAT_001",
):
    """生成测试数据: n_products 个商品, n_days 天."""
    import numpy as np

    np.random.seed(42)
    rows = []
    for pid in range(n_products):
        for day in range(n_days):
            rows.append({
                "product_id": f"P{pid:03d}",
                "date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=day),
                "category_id": category_id,
                "sales": float(np.random.randint(0, 100)),
            })
    return pd.DataFrame(rows)


class TestHotProductLabelBoundary:
    """爆款标签边界值测试."""

    def test_exact_5_days_qualifies(self):
        """持续 5 天 → 标签 = 1 (边界值)."""
        # 构造数据: 商品 P001 在前 5% 且持续正好 5 天
        rows = []
        for day in range(14):
            # P001 是爆款 (高销量), 其他商品低销量
            for pid in range(20):
                if pid == 0:
                    sales = 1000 if day < 5 else 0  # 前5天高销量
                else:
                    sales = 1
                rows.append({
                    "product_id": f"P{pid:03d}",
                    "date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=day),
                    "category_id": "CAT_001",
                    "sales": float(sales),
                })
        df = pd.DataFrame(rows)
        labeled = construct_hot_labels(df, min_days=5, top_percent=5, window_days=14)
        # P001 在前5天进入top5%, 持续5天 → is_hot = True (在第5天)
        p001_data = labeled[labeled["product_id"] == "P000"]
        assert any(p001_data["is_hot"])

    def test_4_days_does_not_qualify(self):
        """持续 4 天 → 标签 = 0 (边界值)."""
        rows = []
        for day in range(14):
            for pid in range(20):
                if pid == 0:
                    sales = 1000 if day < 4 else 0  # 前4天高销量
                else:
                    sales = 1
                rows.append({
                    "product_id": f"P{pid:03d}",
                    "date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=day),
                    "category_id": "CAT_001",
                    "sales": float(sales),
                })
        df = pd.DataFrame(rows)
        labeled = construct_hot_labels(df, min_days=5, top_percent=5, window_days=14)
        p001_data = labeled[labeled["product_id"] == "P000"]
        # 持续4天 < 5 → 不满足
        # 但注意: 第4天时 days_in_top=4 < 5, 所以 is_hot=False
        assert not p001_data["is_hot"].any()

    def test_exactly_top_5_percent_qualifies(self):
        """刚好第 5% 分位 → 标签 = 1."""
        # 20个商品, 前5% = 前1个商品
        rows = []
        for day in range(14):
            for pid in range(20):
                sales = 100 if pid == 0 else 10  # P000 最高
                rows.append({
                    "product_id": f"P{pid:03d}",
                    "date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=day),
                    "category_id": "CAT_001",
                    "sales": float(sales),
                })
        df = pd.DataFrame(rows)
        labeled = construct_hot_labels(df, min_days=5, top_percent=5, window_days=14)
        p000 = labeled[labeled["product_id"] == "P000"]
        # P000 百分位 = 100, 在前5%, 持续14天 >= 5天
        assert p000["is_hot"].any()

    def test_below_top_5_percent_does_not_qualify(self):
        """第 5.1% 分位 → 标签 = 0."""
        # 20个商品, P001 是第2名, 不在前5%
        rows = []
        for day in range(14):
            for pid in range(20):
                if pid == 0:
                    sales = 100
                elif pid == 1:
                    sales = 99  # 第2名, 不在前5% (5% of 20 = 1)
                else:
                    sales = 10
                rows.append({
                    "product_id": f"P{pid:03d}",
                    "date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=day),
                    "category_id": "CAT_001",
                    "sales": float(sales),
                })
        df = pd.DataFrame(rows)
        labeled = construct_hot_labels(df, min_days=5, top_percent=5, window_days=14)
        p001 = labeled[labeled["product_id"] == "P001"]
        # P001 百分位 < 95, 不在前5%
        assert not p001["is_hot"].any()


class TestLabelDateWindow:
    """日期窗口处理."""

    def test_14_day_window_boundary(self):
        """14 天窗口边界 (第 1 天 vs 第 15 天)."""
        rows = []
        for day in range(15):
            for pid in range(20):
                sales = 100 if pid == 0 else 10
                rows.append({
                    "product_id": f"P{pid:03d}",
                    "date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=day),
                    "category_id": "CAT_001",
                    "sales": float(sales),
                })
        df = pd.DataFrame(rows)
        labeled = construct_hot_labels(df, min_days=5, top_percent=5, window_days=14)
        # 应正常处理 15 天数据
        assert len(labeled) == 300

    def test_cross_month_window(self):
        """跨月窗口的日期处理."""
        rows = []
        for day in range(14):
            for pid in range(20):
                sales = 100 if pid == 0 else 10
                rows.append({
                    "product_id": f"P{pid:03d}",
                    "date": pd.Timestamp("2024-01-25") + pd.Timedelta(days=day),  # 跨1月和2月
                    "category_id": "CAT_001",
                    "sales": float(sales),
                })
        df = pd.DataFrame(rows)
        labeled = construct_hot_labels(df)
        assert len(labeled) == 280

    def test_missing_days_in_window(self):
        """窗口内缺失天数处理."""
        rows = []
        for day in range(14):
            for pid in range(20):
                if day == 5 and pid == 0:
                    continue  # P000 跳过第5天
                sales = 100 if pid == 0 else 10
                rows.append({
                    "product_id": f"P{pid:03d}",
                    "date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=day),
                    "category_id": "CAT_001",
                    "sales": float(sales),
                })
        df = pd.DataFrame(rows)
        labeled = construct_hot_labels(df)
        # 不应抛异常
        assert len(labeled) == 279


class TestCategoryPercentile:
    """类目分位数计算."""

    def test_percentile_computation(self):
        """分位数计算正确."""
        df = pd.DataFrame({
            "product_id": ["P0", "P1", "P2", "P3", "P4"],
            "sales": [10, 20, 30, 40, 50],
            "category_id": ["CAT", "CAT", "CAT", "CAT", "CAT"],
        })
        pct = compute_category_percentile(df, "sales")
        # 最高分 → 100, 最低分 → 0
        assert pct.iloc[4] == 100.0
        assert pct.iloc[0] == 0.0

    def test_single_product_category(self):
        """类目内仅 1 个商品 (退化场景)."""
        df = pd.DataFrame({
            "product_id": ["P0"],
            "sales": [50],
            "category_id": ["CAT"],
        })
        pct = compute_category_percentile(df, "sales")
        # 仅1个商品 → 100分位
        assert pct.iloc[0] == 100.0

    def test_tie_at_percentile(self):
        """分位边界并列情况."""
        df = pd.DataFrame({
            "product_id": ["P0", "P1", "P2"],
            "sales": [50, 50, 50],  # 全部并列
            "category_id": ["CAT", "CAT", "CAT"],
        })
        pct = compute_category_percentile(df, "sales")
        # 全部相同 → 应正常返回
        assert len(pct) == 3
