"""TikHub 数据源适配器.

通过 TikHub API 获取抖音商城 (TikTok Shop) 商品数据.

提供:
    - search_products: 按关键词搜索商品
    - get_product_detail: 获取商品详情
    - get_product_reviews: 获取商品评价
    - get_products_by_category: 按类目获取商品

数据字段映射到项目统一 schema:
    product_id / title / price / sales / category_id / source 等.
"""

import logging
from typing import List, Optional

import pandas as pd

from ..credentials import get_credential

logger = logging.getLogger(__name__)

# TikHub 区域常量 (抖音商城用 'cn')
REGION_CN = "cn"


def _get_client():
    """获取 TikHub 客户端 (惰性创建).

    Returns:
        TikHub 客户端实例
    """
    from tikhub import TikHub

    api_key = get_credential("TIKHUB_API_KEY")
    return TikHub(api_key=api_key)


def search_products(
    keyword: str,
    region: str = REGION_CN,
    count: int = 20,
) -> pd.DataFrame:
    """按关键词搜索抖音商城商品.

    Args:
        keyword: 搜索关键词
        region: 区域, 默认 'cn' (中国抖音商城)
        count: 返回数量, 默认 20

    Returns:
        商品 DataFrame, 列: product_id, title, price, sales, shop_name, source, region
    """
    client = _get_client()

    logger.info("TikHub 搜索商品: keyword=%s, region=%s", keyword, region)

    try:
        result = client.tiktok_shop_web.fetch_search_products_list_v2(
            search_word=keyword,
            region=region,
        )
    except Exception as e:
        logger.error("TikHub 商品搜索失败: %s", e)
        return pd.DataFrame()

    # 解析响应
    data = _extract_data(result)
    if not data:
        return pd.DataFrame()

    products = _parse_search_results(data)
    df = pd.DataFrame(products)
    return df.head(count)


def get_product_detail(
    product_id: str,
    region: str = REGION_CN,
) -> dict:
    """获取商品详情.

    Args:
        product_id: 商品 ID
        region: 区域

    Returns:
        商品详情 dict
    """
    client = _get_client()

    logger.info("TikHub 获取商品详情: product_id=%s", product_id)

    try:
        result = client.tiktok_shop_web.fetch_product_detail_v3(
            product_id=product_id,
            region=region,
        )
    except Exception as e:
        logger.error("TikHub 商品详情获取失败: %s", e)
        return {}

    data = _extract_data(result)
    return data if data else {}


def get_product_reviews(
    product_id: str,
    region: str = REGION_CN,
    count: int = 20,
) -> pd.DataFrame:
    """获取商品评价.

    Args:
        product_id: 商品 ID
        region: 区域
        count: 返回评价数

    Returns:
        评价 DataFrame, 列: review_id, content, rating, user_name
    """
    client = _get_client()

    logger.info("TikHub 获取商品评价: product_id=%s", product_id)

    try:
        result = client.tiktok_shop_web.fetch_product_reviews_v2(
            product_id=product_id,
            region=region,
        )
    except Exception as e:
        logger.error("TikHub 商品评价获取失败: %s", e)
        return pd.DataFrame()

    data = _extract_data(result)
    if not data:
        return pd.DataFrame()

    reviews = _parse_reviews(data)
    return pd.DataFrame(reviews).head(count)


def get_products_by_category(
    category_id: str,
    region: str = REGION_CN,
    count: int = 50,
) -> pd.DataFrame:
    """按类目获取商品列表.

    Args:
        category_id: 类目 ID
        region: 区域
        count: 返回数量

    Returns:
        商品 DataFrame
    """
    client = _get_client()

    logger.info("TikHub 按类目获取商品: category_id=%s", category_id)

    try:
        result = client.tiktok_shop_web.fetch_products_by_category_id(
            category_id=category_id,
            region=region,
        )
    except Exception as e:
        logger.error("TikHub 类目商品获取失败: %s", e)
        return pd.DataFrame()

    data = _extract_data(result)
    if not data:
        return pd.DataFrame()

    products = _parse_search_results(data)
    return pd.DataFrame(products).head(count)


# ==================== 内部工具函数 ====================

def _extract_data(result) -> Optional[dict]:
    """从 TikHub 响应对象中提取 data 字段.

    Args:
        result: TikHub API 响应对象

    Returns:
        data dict 或 None
    """
    # TikHub 响应可能是 dict 或对象
    if isinstance(result, dict):
        return result.get("data")

    if hasattr(result, "data"):
        data = result.data
        # data 可能是对象或 dict
        if isinstance(data, dict):
            return data
        if hasattr(data, "__dict__"):
            return data.__dict__
        return None

    return None


def _parse_search_results(data: dict) -> List[dict]:
    """解析搜索结果为统一 schema.

    Args:
        data: TikHub 返回的商品数据

    Returns:
        商品 dict 列表
    """
    products: List[dict] = []

    # TikHub 搜索结果通常在 data.products 或 data.list
    raw_list = _get_product_list(data)
    if not raw_list:
        return products

    for item in raw_list:
        if not isinstance(item, dict):
            continue

        product = {
            "product_id": str(item.get("product_id") or item.get("id") or ""),
            "title": item.get("title") or item.get("name") or "",
            "price": _parse_price(item),
            "sales": _parse_sales(item),
            "shop_name": item.get("shop_name") or item.get("seller_name") or "",
            "category_id": str(item.get("category_id") or ""),
            "source": "tikhub",
            "region": REGION_CN,
        }
        products.append(product)

    return products


def _parse_reviews(data: dict) -> List[dict]:
    """解析评价结果为统一 schema.

    Args:
        data: TikHub 返回的评价数据

    Returns:
        评价 dict 列表
    """
    reviews: List[dict] = []

    raw_list = _get_review_list(data)
    if not raw_list:
        return reviews

    for item in raw_list:
        if not isinstance(item, dict):
            continue

        review = {
            "review_id": str(item.get("review_id") or item.get("id") or ""),
            "content": item.get("content") or item.get("text") or "",
            "rating": item.get("rating") or item.get("star") or 0,
            "user_name": item.get("user_name") or item.get("nickname") or "",
        }
        reviews.append(review)

    return reviews


def _get_product_list(data: dict) -> list:
    """从响应数据中提取商品列表."""
    if not data:
        return []

    # 尝试多种可能的字段名
    for key in ["products", "product_list", "list", "items", "data"]:
        if key in data and isinstance(data[key], list):
            return data[key]

    # 如果 data 本身就是列表
    if isinstance(data, list):
        return data

    return []


def _get_review_list(data: dict) -> list:
    """从响应数据中提取评价列表."""
    if not data:
        return []

    for key in ["reviews", "review_list", "list", "items", "comments"]:
        if key in data and isinstance(data[key], list):
            return data[key]

    if isinstance(data, list):
        return data

    return []


def _parse_price(item: dict) -> float:
    """解析价格字段 (TikHub 价格单位通常为分).

    启发式判断: 整数且 > 100 视为分 (除以 100 转为元);
    带小数或 <= 100 视为元.
    """
    price = item.get("price") or item.get("sale_price") or item.get("min_price")

    if isinstance(price, bool):
        return 0.0

    if isinstance(price, (int, float)):
        price_val = float(price)
        # 整数且较大 → 以分为单位
        if price_val > 100 and price_val == int(price_val):
            return price_val / 100.0
        return price_val

    if isinstance(price, str):
        try:
            price_val = float(price.replace(",", ""))
            if price_val > 100 and price_val == int(price_val):
                return price_val / 100.0
            return price_val
        except (ValueError, TypeError):
            return 0.0

    return 0.0


def _parse_sales(item: dict) -> int:
    """解析销量字段."""
    sales = (
        item.get("sales")
        or item.get("sold_count")
        or item.get("sales_count")
        or item.get("sell_count")
        or 0
    )

    if isinstance(sales, (int, float)):
        return int(sales)

    if isinstance(sales, str):
        # 处理 "1.2万" 这种格式
        if "万" in sales:
            try:
                num = float(sales.replace("万", ""))
                return int(num * 10000)
            except (ValueError, TypeError):
                return 0
        try:
            return int(sales.replace(",", ""))
        except (ValueError, TypeError):
            return 0

    return 0
