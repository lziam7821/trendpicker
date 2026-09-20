"""同款聚合单元测试.

覆盖范围:
    - 标题相似度 (Jaccard / cosine / 中文分词)
    - 主图哈希 (pHash 汉明距离)
    - 聚合策略 (图搜优先 + 标题兜底)

覆盖率目标: 80%
"""

import numpy as np
import pytest

from trendpicker.dedup import (
    aggregate_products,
    chinese_tokenize,
    compute_phash,
    jaccard_similarity,
    phash_distance,
)


class TestTitleSimilarity:
    """标题相似度."""

    def test_jaccard_identical_titles(self):
        """完全相同: 相似度 = 1.0."""
        sim = jaccard_similarity("夏季短袖T恤", "夏季短袖T恤")
        assert sim == 1.0

    def test_jaccard_disjoint_titles(self):
        """无重叠: 相似度 = 0."""
        sim = jaccard_similarity("apple banana", "橙子葡萄")
        assert sim == 0.0

    def test_jaccard_partial_overlap(self):
        """部分重叠: 相似度计算正确."""
        sim = jaccard_similarity("夏季短袖T恤", "夏季长袖T恤")
        # 有交集 "夏季", "T恤", "袖T" 但不完全
        assert 0 < sim < 1

    def test_jaccard_chinese_tokenization(self):
        """中文分词后的相似度."""
        tokens = chinese_tokenize("夏季短袖T恤abc123")
        # 应包含字母数字 token 和中文 bigram
        assert "abc123" in tokens
        assert any(t.startswith("夏") for t in tokens)
        assert "夏季" in tokens
        assert "短袖" in tokens
        assert len(tokens) > 0


class TestImageHash:
    """主图哈希 (pHash)."""

    def test_phash_identical_images(self):
        """相同图片: 汉明距离 = 0."""
        np.random.seed(42)
        image = np.random.randint(0, 255, (64, 64, 3), dtype=np.uint8)
        hash_a = compute_phash(image)
        hash_b = compute_phash(image)
        assert phash_distance(hash_a, hash_b) == 0

    def test_phash_similar_images(self):
        """相似图片: 汉明距离 < 阈值."""
        np.random.seed(42)
        base = np.random.randint(0, 255, (64, 64), dtype=np.uint8).astype(float)
        # 轻微修改
        similar = base.copy()
        similar[0:4, 0:4] += 5
        hash_a = compute_phash(base)
        hash_b = compute_phash(similar)
        assert phash_distance(hash_a, hash_b) < 10

    def test_phash_different_images(self):
        """不同图片: 汉明距离 > 阈值."""
        np.random.seed(42)
        img_a = np.random.randint(0, 50, (64, 64), dtype=np.uint8)
        np.random.seed(99)
        img_b = np.random.randint(200, 255, (64, 64), dtype=np.uint8)
        hash_a = compute_phash(img_a)
        hash_b = compute_phash(img_b)
        assert phash_distance(hash_a, hash_b) > 10


class TestAggregation:
    """聚合策略."""

    def test_aggregate_by_image_hash(self):
        """按主图哈希聚合同款."""
        np.random.seed(42)
        image = np.random.randint(0, 255, (64, 64, 3), dtype=np.uint8)
        products = [
            {"title": "商品A", "phash": compute_phash(image)},
            {"title": "不同标题", "phash": compute_phash(image)},
            {"title": "完全不同商品", "phash": "0000000000000000"},
        ]
        groups = aggregate_products(products, phash_threshold=5)
        # 前两个应聚合 (相同图片), 第三个单独
        assert len(groups) == 2

    def test_aggregate_with_title_fallback(self):
        """图片哈希兜底 + 标题相似度."""
        products = [
            {"title": "夏季短袖T恤", "phash": None},
            {"title": "夏季短袖T恤", "phash": None},
            {"title": "冬季羽绒服", "phash": None},
        ]
        groups = aggregate_products(products, title_threshold=0.5)
        # 前两个标题完全相同 → 聚合, 第三个单独
        assert len(groups) == 2
