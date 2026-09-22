"""JustOneAPI 抖音电商数据源适配器.

通过 JustOneAPI 获取抖音电商 (中国大陆) 商品数据.
相比 TikHub (TikTok Shop 海外), JustOneAPI 提供:
    - 中国大陆抖音商城数据 (真实国内市场)
    - 30 天日销量序列 sale_axis (趋势分析关键)
    - 完整类目层级 first/second/third category + leaf_layer
    - 联盟佣金 commission (选品参考)

数据字段映射到项目统一 schema:
    product_id / title / price / sales / category_id / shop_name / source / sale_axis
"""

import logging
from typing import List, Optional

import pandas as pd

from ..credentials import get_credential

logger = logging.getLogger(__name__)


def _get_client():
    """获取 JustOneAPI 客户端 (惰性创建)."""
    from justoneapi import JustOneAPIClient

    token = get_credential("JUSTONEAPI_TOKEN")
    return JustOneAPIClient(token=token)


def search_products(keyword: str, page: int = 1, count: int = 30) -> pd.DataFrame:
    """按关键词搜索抖音电商商品.

    Args:
        keyword: 搜索关键词
        page: 页码 (默认第 1 页)
        count: 返回数量上限 (默认 30, 即一页上限)

    Returns:
        商品 DataFrame, 列:
        product_id, title, price, sales, shop_name, category_id,
        category_name, commission, sale_axis, source

        sale_axis: 30 天日销量 list[dict{x, y}] (趋势分析用)
    """
    client = _get_client()

    logger.info("JustOneAPI 抖音电商搜索: keyword=%s, page=%s", keyword, page)

    try:
        response = client.douyin_ec.search_item_list_v1(keyword=keyword, page=page)
    except Exception as e:
        logger.error("JustOneAPI 商品搜索失败: %s", e)
        return pd.DataFrame()

    if not response.success:
        logger.warning(
            "JustOneAPI 搜索业务失败: code=%s, message=%s",
            response.code,
            response.message,
        )
        return pd.DataFrame()

    products = _parse_search_results(response.data)
    df = pd.DataFrame(products)
    return df.head(count)


def get_product_detail(product_id: str) -> dict:
    """获取商品详情.

    Args:
        product_id: 商品 ID

    Returns:
        商品详情 dict
    """
    client = _get_client()

    logger.info("JustOneAPI 获取商品详情: product_id=%s", product_id)

    try:
        response = client.douyin_ec.get_item_detail_v2(product_id=product_id)
    except Exception as e:
        logger.error("JustOneAPI 商品详情获取失败: %s", e)
        return {}

    if not response.success:
        logger.warning("商品详情业务失败: %s", response.message)
        return {}

    return response.data if response.data else {}


def get_product_reviews(product_id: str, count: int = 20) -> pd.DataFrame:
    """获取商品评价.

    Args:
        product_id: 商品 ID
        count: 返回评价数

    Returns:
        评价 DataFrame, 列: review_id, content, rating, user_name
    """
    client = _get_client()

    logger.info("JustOneAPI 获取商品评价: product_id=%s", product_id)

    try:
        response = client.douyin_ec.get_item_comments_v1(product_id=product_id)
    except Exception as e:
        logger.error("JustOneAPI 商品评价获取失败: %s", e)
        return pd.DataFrame()

    if not response.success:
        logger.warning("商品评价业务失败: %s", response.message)
        return pd.DataFrame()

    reviews = _parse_reviews(response.data)
    return pd.DataFrame(reviews).head(count)


# ==================== 内部工具函数 ====================

def _parse_search_results(data) -> List[dict]:
    """解析搜索结果为统一 schema.

    JustOneAPI 抖音电商搜索响应结构:
        data.summary_promotions (list) 每个含:
            product_id, promotion_id, base_model{...}
        base_model.product_info: name, month_sale{origin}, sale_axis, category{...}
        base_model.marketing_info.price_desc.price.origin (分)
        base_model.shop_info.shop_name
    """
    products: List[dict] = []

    if not isinstance(data, dict):
        return []

    promotions = data.get("summary_promotions") or data.get("promotions") or []
    if not isinstance(promotions, list):
        return []

    for item in promotions:
        if not isinstance(item, dict):
            continue

        product_id = str(item.get("product_id") or "")
        if not product_id:
            continue

        base = item.get("base_model") or {}
        if not isinstance(base, dict):
            continue

        product_info = base.get("product_info") or {}
        shop_info = base.get("shop_info") or {}
        marketing_info = base.get("marketing_info") or {}

        # 价格: marketing_info.price_desc.price.origin (分)
        price_yuan = _extract_price(marketing_info, product_info)

        # 销量: product_info.month_sale.origin
        sales = _extract_sales(product_info)

        # 30 天日销量序列 (趋势分析关键)
        sale_axis = product_info.get("sale_axis") or []
        if not isinstance(sale_axis, list):
            sale_axis = []

        # 类目层级
        category = product_info.get("category") or {}
        category_id, category_name = _extract_category(category)

        # 佣金 (联盟推广)
        commission = _extract_commission(base)

        product = {
            "product_id": product_id,
            "title": product_info.get("name") or "",
            "price": price_yuan,
            "sales": sales,
            "shop_name": shop_info.get("shop_name") or "",
            "shop_id": str(shop_info.get("shop_id") or ""),
            "category_id": category_id,
            "category_name": category_name,
            "commission": commission,
            "sale_axis": sale_axis,
            "source": "justoneapi",
        }
        products.append(product)

    return products


def _parse_reviews(data) -> List[dict]:
    """解析评价结果为统一 schema."""
    reviews: List[dict] = []
    if not isinstance(data, dict):
        return []

    raw_list = (
        data.get("comments")
        or data.get("reviews")
        or data.get("list")
        or []
    )
    if not isinstance(raw_list, list):
        return []

    for item in raw_list:
        if not isinstance(item, dict):
            continue
        review = {
            "review_id": str(item.get("comment_id") or item.get("review_id") or item.get("id") or ""),
            "content": item.get("content") or item.get("text") or item.get("comment") or "",
            "rating": item.get("rating") or item.get("star") or 0,
            "user_name": item.get("user_name") or item.get("nickname") or "",
        }
        reviews.append(review)

    return reviews


def _extract_price(marketing_info: dict, product_info: dict) -> float:
    """提取价格 (元).

    抖音电商价格路径:
        marketing_info.price_desc.price.origin (分, 整数)
    """
    price_desc = marketing_info.get("price_desc") or {}
    if isinstance(price_desc, dict):
        price = price_desc.get("price") or {}
        if isinstance(price, dict):
            origin = price.get("origin")
            if origin is not None:
                # origin 单位是分, 转为元
                return _to_float(origin) / 100.0

    # 回退: product_info 直接字段
    for key in ("price", "sale_price", "min_price"):
        val = product_info.get(key)
        if val is not None:
            return _to_float(val)

    return 0.0


def _extract_sales(product_info: dict) -> int:
    """提取销量.

    抖音电商销量路径:
        product_info.month_sale.origin (int)
    """
    month_sale = product_info.get("month_sale") or {}
    if isinstance(month_sale, dict):
        origin = month_sale.get("origin")
        if origin is not None:
            return _to_int(origin)

    # 回退: 直接字段
    for key in ("sales", "sold_count", "sales_count"):
        val = product_info.get(key)
        if val is not None:
            return _to_int(val)

    return 0


def _extract_category(category: dict) -> tuple:
    """提取叶子类目 ID 和名称.

    抖音电商类目层级: first → second → third → fourth
    leaf_layer 指示叶子在第几层.
    """
    if not isinstance(category, dict):
        return "", ""

    leaf_layer = category.get("leaf_layer") or 3

    layer_map = {
        1: "first_category",
        2: "second_category",
        3: "third_category",
        4: "fourth_category",
    }

    leaf_key = layer_map.get(int(leaf_layer), "third_category")
    leaf = category.get(leaf_key) or {}

    if isinstance(leaf, dict):
        cat_id = str(leaf.get("category_id") or "")
        cat_name = leaf.get("category_name") or ""
        if cat_id and cat_id != "0":
            return cat_id, cat_name

    # 回退: 找第一个非空的层
    for key in ("first_category", "second_category", "third_category", "fourth_category"):
        layer = category.get(key) or {}
        if isinstance(layer, dict):
            cat_id = str(layer.get("category_id") or "")
            cat_name = layer.get("category_name") or ""
            if cat_id and cat_id != "0":
                return cat_id, cat_name

    return "", ""


def _extract_commission(base: dict) -> float:
    """提取联盟佣金率 (百分比).

    抖音电商佣金通常在 promotion_info.cos_info 或 marketing_info.
    """
    # 路径 1: promotion_info.cos_info.commission_rate
    promotion_info = base.get("promotion_info") or {}
    if isinstance(promotion_info, dict):
        cos_info = promotion_info.get("cos_info") or {}
        if isinstance(cos_info, dict):
            rate = cos_info.get("commission_rate") or cos_info.get("rate")
            if rate is not None:
                return _to_float(rate)

    return 0.0


def _to_float(value) -> float:
    """安全转 float."""
    if value is None or isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", ""))
        except (ValueError, TypeError):
            return 0.0
    return 0.0


def _to_int(value) -> int:
    """安全转 int, 支持 '1.2万' / '1,000' / 数字."""
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        if "万" in value:
            try:
                return int(float(value.replace("万", "")) * 10000)
            except (ValueError, TypeError):
                return 0
        try:
            return int(value.replace(",", ""))
        except (ValueError, TypeError):
            return 0
    return 0
