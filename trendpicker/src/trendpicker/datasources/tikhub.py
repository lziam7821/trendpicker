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

# TikHub 区域常量
# TikTok Shop (tiktok_shop_web) 支持的 region: BR/ID/JP/MX/MY/PH/SG/TH/US/VN
# 注意: 不支持中国大陆 (CN), 中国大陆抖音商城需通过 douyin_web 直播间商品接口
REGION_US = "US"  # 默认区域 (TikTok Shop 美国)
REGION_CN = "cn"  # 中国大陆 (用于 douyin_web 系列接口, 非 tiktok_shop_web)


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
    region: str = REGION_US,
    count: int = 20,
) -> pd.DataFrame:
    """按关键词搜索 TikTok Shop 商品.

    Args:
        keyword: 搜索关键词
        region: 区域, 默认 'US' (TikTok Shop 美国)
            支持的 region: BR/ID/JP/MX/MY/PH/SG/TH/US/VN
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
    products = _parse_search_results(result)
    df = pd.DataFrame(products)
    return df.head(count)


def get_product_detail(
    product_id: str,
    region: str = REGION_US,
) -> dict:
    """获取商品详情.

    Args:
        product_id: 商品 ID
        region: 区域, 默认 'US'

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
    region: str = REGION_US,
    count: int = 20,
) -> pd.DataFrame:
    """获取商品评价.

    Args:
        product_id: 商品 ID
        region: 区域, 默认 'US'
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
    region: str = REGION_US,
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

    products = _parse_search_results(result)
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

    TikTok Shop 搜索响应结构:
        data.data.component_data.products (list) 每个含:
            product_id, title, product_price_info.sale_price_decimal,
            sold_info.sold_count, seller_info.shop_name

    Args:
        data: TikHub 返回的顶层响应 dict

    Returns:
        商品 dict 列表 (统一 schema)
    """
    products: List[dict] = []

    raw_list = _get_product_list(data)
    if not raw_list:
        return products

    for item in raw_list:
        if not isinstance(item, dict):
            continue

        # seller_info.shop_name
        seller_info = item.get("seller_info") or {}
        shop_name = (
            seller_info.get("shop_name")
            if isinstance(seller_info, dict)
            else None
        ) or item.get("shop_name") or ""

        product = {
            "product_id": str(item.get("product_id") or item.get("id") or ""),
            "title": item.get("title") or item.get("name") or "",
            "price": _parse_price(item),
            "sales": _parse_sales(item),
            "shop_name": shop_name,
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
    """从响应数据中提取商品列表.

    TikTok Shop 搜索响应路径 (顶层 result dict):
        result.data.data.component_data.products
    其中第一层 data 是 TikHub 包装层, 第二层 data 是 TikTok Shop 业务数据.
    """
    if not isinstance(data, dict):
        return []

    # 路径 1: result.data.data.component_data.products (TikTok Shop v2)
    outer = data.get("data")
    if isinstance(outer, dict):
        inner = outer.get("data")
        if isinstance(inner, dict):
            cd = inner.get("component_data")
            if isinstance(cd, dict):
                products = cd.get("products")
                if isinstance(products, list):
                    return products

    # 路径 2: data.products / data.list (兼容其他接口)
    for key in ["products", "product_list", "list", "items"]:
        if key in data and isinstance(data[key], list):
            return data[key]

    # data 本身是列表
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
    """解析价格字段.

    TikTok Shop 商品价格嵌套在:
        product_price_info.sale_price_decimal (字符串, 如 '4.99')

    兼容旧字段: price / sale_price / min_price (数字或字符串)
    """
    # 路径 1: product_price_info.sale_price_decimal (TikTok Shop v2)
    ppi = item.get("product_price_info")
    if isinstance(ppi, dict):
        for key in ("sale_price_decimal", "sale_price", "single_product_price_decimal"):
            val = ppi.get(key)
            if val is not None:
                return _to_float(val)

    # 路径 2: 直接字段
    price = item.get("price") or item.get("sale_price") or item.get("min_price")
    return _to_float(price)


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


def _parse_sales(item: dict) -> int:
    """解析销量字段.

    TikTok Shop 销量嵌套在:
        sold_info.sold_count (int)

    兼容旧字段: sales / sold_count / sales_count (含 "1.2万" 字符串)
    """
    # 路径 1: sold_info.sold_count (TikTok Shop v2)
    si = item.get("sold_info")
    if isinstance(si, dict):
        for key in ("sold_count", "sales_count", "sell_count"):
            val = si.get(key)
            if val is not None:
                return _to_int(val)

    # 路径 2: 直接字段
    sales = (
        item.get("sales")
        or item.get("sold_count")
        or item.get("sales_count")
        or item.get("sell_count")
        or 0
    )
    return _to_int(sales)


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
