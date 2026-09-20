"""类目对齐映射模块.

将不同平台的类目体系映射到内部标准类目.
内部标准类目: 一级 → 二级 → 叶子 (无下级).

提供:
    - map_category: 将外部类目映射到内部标准
    - is_leaf_category: 判断是否为叶子类目
    - get_category_path: 获取类目路径
    - get_all_leaf_categories: 获取所有叶子类目
"""

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# 内部标准类目树: category_id -> {name, parent, children}
_INTERNAL_CATEGORIES: Dict[str, Dict] = {
    # 一级
    "C01": {"name": "服饰", "parent": None, "children": ["C0101", "C0102"]},
    "C02": {"name": "美妆", "parent": None, "children": ["C0201", "C0202"]},
    "C03": {"name": "食品", "parent": None, "children": ["C0301"]},
    # 二级 (叶子)
    "C0101": {"name": "女装", "parent": "C01", "children": []},
    "C0102": {"name": "男装", "parent": "C01", "children": []},
    "C0201": {"name": "护肤", "parent": "C02", "children": []},
    "C0202": {"name": "彩妆", "parent": "C02", "children": []},
    "C0301": {"name": "零食", "parent": "C03", "children": []},
}

# 蝉妈妈 → 内部标准类目映射
_CHANMAMA_MAP: Dict[str, str] = {
    "chanmama_women_clothing": "C0101",
    "chanmama_men_clothing": "C0102",
    "chanmama_skincare": "C0201",
    "chanmama_makeup": "C0202",
    "chanmama_snacks": "C0301",
}

# 1688 → 内部标准类目映射
_ALI1688_MAP: Dict[str, str] = {
    "1688_women_apparel": "C0101",
    "1688_men_apparel": "C0102",
    "1688_skincare": "C0201",
    "1688_cosmetics": "C0202",
    "1688_food": "C0301",
}

# 平台 → 映射表路由
_SOURCE_MAPS: Dict[str, Dict[str, str]] = {
    "chanmama": _CHANMAMA_MAP,
    "ali1688": _ALI1688_MAP,
}


def map_category(source: str, external_category_id: str) -> str:
    """将外部平台类目映射到内部标准类目.

    未映射的类目返回 "UNMAPPED" 而非抛异常.

    Args:
        source: 数据源名称 ("chanmama" / "ali1688")
        external_category_id: 外部平台类目 ID

    Returns:
        内部标准类目 ID, 未映射时返回 "UNMAPPED"
    """
    source = source.lower()
    mapping = _SOURCE_MAPS.get(source)

    if mapping is None:
        logger.warning("未知数据源: %s, 类目 %s 无法映射", source, external_category_id)
        return "UNMAPPED"

    internal_id = mapping.get(external_category_id)
    if internal_id is None:
        logger.info(
            "未映射类目: source=%s, external_id=%s → UNMAPPED", source, external_category_id
        )
        return "UNMAPPED"

    return internal_id


def is_leaf_category(category_id: str) -> bool:
    """判断是否为叶子类目 (无子类目).

    Args:
        category_id: 内部标准类目 ID

    Returns:
        True 如果是叶子类目或未知类目 (未知类目视为叶子)
    """
    cat = _INTERNAL_CATEGORIES.get(category_id)
    if cat is None:
        # 未知类目: 视为叶子 (不阻断流水线)
        return True

    return len(cat["children"]) == 0


def get_category_path(category_id: str) -> List[str]:
    """获取类目路径 (一级 → 二级 → 叶子).

    Args:
        category_id: 内部标准类目 ID

    Returns:
        类目 ID 路径列表, 从根到当前节点
    """
    path: List[str] = []
    current_id: Optional[str] = category_id
    visited: set = set()

    while current_id and current_id not in visited:
        visited.add(current_id)
        cat = _INTERNAL_CATEGORIES.get(current_id)
        if cat is None:
            path.insert(0, current_id)
            break
        path.insert(0, current_id)
        current_id = cat.get("parent")

    return path


def get_all_leaf_categories() -> List[str]:
    """获取所有叶子类目 ID 列表.

    Returns:
        叶子类目 ID 列表
    """
    return [
        cat_id
        for cat_id, cat in _INTERNAL_CATEGORIES.items()
        if len(cat["children"]) == 0
    ]


def get_category_name(category_id: str) -> str:
    """获取类目名称.

    Args:
        category_id: 内部标准类目 ID

    Returns:
        类目名称, 未知类目返回 "未知类目"
    """
    cat = _INTERNAL_CATEGORIES.get(category_id)
    if cat is None:
        return "未知类目"
    return cat["name"]
