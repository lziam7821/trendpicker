"""同款聚合单元测试.

覆盖范围:
    - 标题相似度 (Jaccard / cosine / 中文分词)
    - 主图哈希 (pHash 汉明距离)
    - 聚合策略 (图搜优先 + 标题兜底)

覆盖率目标: 80%
"""

import pytest


@pytest.mark.skip(reason="等待 dedup.py 实现后补全")
class TestTitleSimilarity:
    """标题相似度."""

    def test_jaccard_identical_titles(self):
        """完全相同: 相似度 = 1.0."""

    def test_jaccard_disjoint_titles(self):
        """无重叠: 相似度 = 0."""

    def test_jaccard_partial_overlap(self):
        """部分重叠: 相似度计算正确."""

    def test_jaccard_chinese_tokenization(self):
        """中文分词后的相似度."""


@pytest.mark.skip(reason="等待 dedup.py 实现后补全")
class TestImageHash:
    """主图哈希 (pHash)."""

    def test_phash_identical_images(self):
        """相同图片: 汉明距离 = 0."""

    def test_phash_similar_images(self):
        """相似图片: 汉明距离 < 阈值."""

    def test_phash_different_images(self):
        """不同图片: 汉明距离 > 阈值."""


@pytest.mark.skip(reason="等待 dedup.py 实现后补全")
class TestAggregation:
    """聚合策略."""

    def test_aggregate_by_image_hash(self):
        """按主图哈希聚合同款."""

    def test_aggregate_with_title_fallback(self):
        """图片哈希兜底 + 标题相似度."""
