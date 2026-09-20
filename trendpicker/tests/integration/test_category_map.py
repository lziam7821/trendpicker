"""类目对齐映射测试.

覆盖范围:
    - 蝉妈妈类目 -> 内部标准类目
    - 1688 类目 -> 内部标准类目
    - 叶子类目识别 (无下级)
    - 未映射类目的兜底策略

覆盖率目标: 60%
"""

import pytest

from trendpicker.category_map import (
    get_all_leaf_categories,
    get_category_name,
    get_category_path,
    is_leaf_category,
    map_category,
)


class TestCategoryMapping:
    """类目映射."""

    def test_chanmama_to_internal_mapping(self):
        """蝉妈妈类目正确映射到内部标准."""
        assert map_category("chanmama", "chanmama_women_clothing") == "C0101"
        assert map_category("chanmama", "chanmama_skincare") == "C0201"
        assert map_category("chanmama", "chanmama_snacks") == "C0301"

    def test_ali1688_to_internal_mapping(self):
        """1688 类目正确映射到内部标准."""
        assert map_category("ali1688", "1688_women_apparel") == "C0101"
        assert map_category("ali1688", "1688_cosmetics") == "C0202"
        assert map_category("ali1688", "1688_food") == "C0301"

    def test_leaf_category_boundary(self):
        """叶子类目识别准确 (无下级)."""
        assert is_leaf_category("C0101") is True   # 女装 - 叶子
        assert is_leaf_category("C01") is False    # 服饰 - 非叶子 (有子类目)
        assert is_leaf_category("C0201") is True   # 护肤 - 叶子

    def test_unmapped_category_fallback(self):
        """未映射类目的兜底策略 (不抛异常)."""
        result = map_category("chanmama", "unknown_category_xyz")
        assert result == "UNMAPPED"

        result = map_category("unknown_source", "any_category")
        assert result == "UNMAPPED"

    def test_category_path_consistency(self):
        """类目路径 (一级→二级→叶子) 一致性."""
        path = get_category_path("C0101")
        assert path == ["C01", "C0101"]
        assert get_category_name("C01") == "服饰"
        assert get_category_name("C0101") == "女装"
        assert len(get_all_leaf_categories()) > 0
