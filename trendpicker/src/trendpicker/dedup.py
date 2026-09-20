"""同款聚合模块.

提供:
    - jaccard_similarity: Jaccard 相似度 (标题分词后交集/并集)
    - chinese_tokenize: 中文分词 (基于字符级 n-gram 的简易分词)
    - compute_phash: 感知哈希 (pHash)
    - phash_distance: 汉明距离
    - aggregate_products: 同款聚合 (图搜优先 + 标题兜底)
"""

import hashlib
import logging
import re
from typing import Dict, List, Optional, Set

import numpy as np

logger = logging.getLogger(__name__)


def chinese_tokenize(text: str) -> Set[str]:
    """中文分词 (简易字符级 bigram + 英文单词).

    对中文按 2-gram 切分 (捕捉局部语义),
    对英文/数字按单词切分.

    Args:
        text: 输入文本

    Returns:
        分词集合 (Set[str])
    """
    if not text:
        return set()

    tokens: Set[str] = set()

    # 提取英文单词和数字
    alphanumeric_tokens = re.findall(r"[a-zA-Z0-9]+", text)
    tokens.update(t.lower() for t in alphanumeric_tokens)

    # 提取中文字符序列, 按 bigram 切分
    chinese_segments = re.findall(r"[\u4e00-\u9fff]+", text)
    for seg in chinese_segments:
        if len(seg) == 1:
            tokens.add(seg)
        else:
            for i in range(len(seg) - 1):
                tokens.add(seg[i : i + 2])

    return tokens


def jaccard_similarity(text_a: str, text_b: str) -> float:
    """计算两个文本的 Jaccard 相似度.

    Jaccard = |A ∩ B| / |A ∪ B|

    Args:
        text_a: 文本 A
        text_b: 文本 B

    Returns:
        相似度 [0.0, 1.0]
    """
    set_a = chinese_tokenize(text_a)
    set_b = chinese_tokenize(text_b)

    if not set_a and not set_b:
        return 1.0  # 两个空文本视为完全相同

    union = set_a | set_b
    if not union:
        return 0.0

    intersection = set_a & set_b
    return len(intersection) / len(union)


def compute_phash(image: np.ndarray) -> str:
    """计算图像的感知哈希 (pHash).

    流程:
        1. 转灰度
        2. 缩放到 32x32
        3. 计算 DCT
        4. 取左上 8x8 低频分量
        5. 以均值为阈值二值化 → 64 位哈希

    Args:
        image: 输入图像 (H, W, C) 或 (H, W)

    Returns:
        64 位哈希的十六进制字符串 (16 字符)
    """
    # 转灰度
    if image.ndim == 3:
        gray = np.mean(image, axis=2)
    else:
        gray = image.copy()

    # 缩放到 32x32 (简易最近邻)
    h, w = gray.shape
    if h != 32 or w != 32:
        row_idx = np.linspace(0, h - 1, 32).round().astype(int)
        col_idx = np.linspace(0, w - 1, 32).round().astype(int)
        gray = gray[np.ix_(row_idx, col_idx)]

    gray = gray.astype(np.float64)

    # 计算 DCT (使用 numpy 的 dct 实现)
    from scipy.fftpack import dct

    dct_result = dct(dct(gray, axis=0, norm="ortho"), axis=1, norm="ortho")

    # 取左上 8x8 低频分量
    low_freq = dct_result[:8, :8]
    # 使用中位数作为阈值 (更鲁棒)
    median_val = np.median(low_freq)

    # 二值化 → 64 位
    bits = (low_freq > median_val).flatten()
    # 转十六进制字符串
    hash_str = ""
    for i in range(0, 64, 4):
        nibble = 0
        for j in range(4):
            if i + j < 64 and bits[i + j]:
                nibble |= 1 << (3 - j)
        hash_str += f"{nibble:x}"

    return hash_str


def phash_distance(hash_a: str, hash_b: str) -> int:
    """计算两个 pHash 之间的汉明距离.

    Args:
        hash_a: 哈希 A (十六进制字符串)
        hash_b: 哈希 B (十六进制字符串)

    Returns:
        汉明距离 (不同 bit 数)
    """
    if len(hash_a) != len(hash_b):
        # 对不同长度的哈希, 补齐短的
        max_len = max(len(hash_a), len(hash_b))
        hash_a = hash_a.ljust(max_len, "0")
        hash_b = hash_b.ljust(max_len, "0")

    val_a = int(hash_a, 16)
    val_b = int(hash_b, 16)
    xor_result = val_a ^ val_b

    return bin(xor_result).count("1")


def aggregate_products(
    products: List[Dict],
    title_threshold: float = 0.5,
    phash_threshold: int = 10,
) -> List[List[Dict]]:
    """同款聚合: 图搜优先 + 标题兜底.

    聚合策略:
        1. 若两个商品有 pHash 且汉明距离 <= phash_threshold → 同款
        2. 若 pHash 不可用, 退回标题 Jaccard 相似度 >= title_threshold → 同款

    Args:
        products: 商品列表, 每个商品是 dict, 需含 "title" 字段,
                  可选 "phash" 字段
        title_threshold: 标题相似度阈值, 默认 0.5
        phash_threshold: pHash 汉明距离阈值, 默认 10

    Returns:
        聚合后的商品组列表, 每组是同款商品列表
    """
    if not products:
        return []

    n = len(products)
    # 并查集
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    # 两两比较
    for i in range(n):
        for j in range(i + 1, n):
            phash_i = products[i].get("phash")
            phash_j = products[j].get("phash")

            # 优先: pHash 比较
            if phash_i and phash_j:
                dist = phash_distance(phash_i, phash_j)
                if dist <= phash_threshold:
                    union(i, j)
                    continue

            # 兜底: 标题相似度
            title_i = products[i].get("title", "")
            title_j = products[j].get("title", "")
            sim = jaccard_similarity(title_i, title_j)
            if sim >= title_threshold:
                union(i, j)

    # 收集聚合组
    groups: Dict[int, List[Dict]] = {}
    for i in range(n):
        root = find(i)
        if root not in groups:
            groups[root] = []
        groups[root].append(products[i])

    return list(groups.values())
