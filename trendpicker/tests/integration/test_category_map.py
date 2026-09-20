"""类目对齐映射测试.

覆盖范围:
    - 蝉妈妈类目 -> 内部标准类目
    - 1688 类目 -> 内部标准类目
    - 叶子类目识别 (无下级)
    - 未映射类目的兜底策略

覆盖率目标: 60%
"""

import pytest


@pytest.mark.skip(reason="等待 category_map 模块实现后补全")
class TestCategoryMapping:
    """类目映射."""

    def test_chanmama_to_internal_mapping(self):
        """蝉妈妈类目正确映射到内部标准."""

    def test_ali1688_to_internal_mapping(self):
        """1688 类目正确映射到内部标准."""

    def test_leaf_category_boundary(self):
        """叶子类目识别准确 (无下级)."""

    def test_unmapped_category_fallback(self):
        """未映射类目的兜底策略 (不抛异常)."""

    def test_category_path_consistency(self):
        """类目路径 (一级→二级→叶子) 一致性."""
