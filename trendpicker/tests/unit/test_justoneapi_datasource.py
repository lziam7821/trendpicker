"""JustOneAPI 抖音电商数据源适配器测试.

由于 JustOneAPI 需要付费 (按调用计费), 所有测试使用 mock 模拟 API 响应.
测试覆盖:
    - search_products: 搜索流程 (含业务失败/异常/空结果)
    - get_product_detail: 详情获取
    - get_product_reviews: 评价获取
    - _parse_search_results: 抖音电商响应解析
    - _extract_price: 价格提取 (分→元/回退路径)
    - _extract_sales: 销量提取 (含 '万' 单位)
    - _extract_category: 类目层级解析 (leaf_layer)
    - _extract_commission: 佣金提取
    - _to_float / _to_int: 安全类型转换
"""

import pandas as pd
import pytest

from trendpicker.datasources import justoneapi as joa


# ==================== 测试用样例数据 ====================

SAMPLE_SEARCH_RESPONSE_DATA = {
    "summary_promotions": [
        {
            "product_id": "3843782907940438092",
            "promotion_id": "p_001",
            "base_model": {
                "product_info": {
                    "name": "金钻缎光雾感口红",
                    "month_sale": {"origin": 150},
                    "sale_axis": [
                        {"x": "20260823", "y": 10},
                        {"x": "20260824", "y": 20},
                    ],
                    "category": {
                        "leaf_layer": 3,
                        "first_category": {
                            "category_id": "1",
                            "category_name": "美妆",
                        },
                        "second_category": {
                            "category_id": "10",
                            "category_name": "彩妆",
                        },
                        "third_category": {
                            "category_id": "100",
                            "category_name": "有色唇膏/口红",
                        },
                    },
                },
                "shop_info": {
                    "shop_id": "shop_001",
                    "shop_name": "明哥小铺01",
                },
                "marketing_info": {
                    "price_desc": {
                        "price": {"origin": 1990}  # 分, 应解析为 19.90
                    }
                },
                "promotion_info": {
                    "cos_info": {"commission_rate": 20.0}
                },
            },
        },
        {
            "product_id": "3567186900815080513",
            "promotion_id": "p_002",
            "base_model": {
                "product_info": {
                    "name": "黑钻三色口红持久不掉色",
                    "month_sale": {"origin": 14},
                    "sale_axis": [],
                    "category": {
                        "leaf_layer": 2,
                        "first_category": {
                            "category_id": "1",
                            "category_name": "美妆",
                        },
                        "second_category": {
                            "category_id": "11",
                            "category_name": "唇彩/唇蜜/唇釉",
                        },
                    },
                },
                "shop_info": {
                    "shop_id": "shop_002",
                    "shop_name": "姿色严选美妆店",
                },
                "marketing_info": {
                    "price_desc": {
                        "price": {"origin": 2990}  # 29.90
                    }
                },
            },
        },
    ]
}


class _MockResponse:
    """模拟 justoneapi SDK 的 Response 对象."""

    def __init__(self, success=True, code=200, message="", data=None):
        self.success = success
        self.code = code
        self.message = message
        self.data = data


@pytest.fixture
def mock_client(mocker):
    """Mock JustOneAPI 客户端."""
    mock = mocker.patch.object(joa, "_get_client")
    return mock.return_value


# ==================== search_products 测试 ====================


class TestSearchProducts:
    """商品搜索."""

    def test_search_products_returns_dataframe(self, mock_client):
        """搜索返回 DataFrame, 字段映射正确."""
        mock_client.douyin_ec.search_item_list_v1.return_value = _MockResponse(
            success=True, data=SAMPLE_SEARCH_RESPONSE_DATA
        )

        df = joa.search_products("口红", page=1, count=10)

        assert isinstance(df, pd.DataFrame)
        assert len(df) == 2

        # 第一条商品: 完整字段
        row0 = df.iloc[0]
        assert row0["product_id"] == "3843782907940438092"
        assert row0["title"] == "金钻缎光雾感口红"
        # 1990 分 → 19.90 元
        assert row0["price"] == 19.90
        assert row0["sales"] == 150
        assert row0["shop_name"] == "明哥小铺01"
        assert row0["shop_id"] == "shop_001"
        # leaf_layer=3 → third_category
        assert row0["category_id"] == "100"
        assert row0["category_name"] == "有色唇膏/口红"
        assert row0["commission"] == 20.0
        assert row0["source"] == "justoneapi"
        # sale_axis: 30 天日销量序列 (此处 2 条样本)
        assert isinstance(row0["sale_axis"], list)
        assert len(row0["sale_axis"]) == 2
        assert row0["sale_axis"][0] == {"x": "20260823", "y": 10}

        # 第二条: leaf_layer=2 → second_category, 无佣金
        row1 = df.iloc[1]
        assert row1["category_id"] == "11"
        assert row1["category_name"] == "唇彩/唇蜜/唇釉"
        assert row1["commission"] == 0.0
        assert row1["sale_axis"] == []

    def test_search_products_count_truncates(self, mock_client):
        """count 参数截断结果."""
        mock_client.douyin_ec.search_item_list_v1.return_value = _MockResponse(
            success=True, data=SAMPLE_SEARCH_RESPONSE_DATA
        )

        df = joa.search_products("口红", page=1, count=1)
        assert len(df) == 1

    def test_search_products_empty_result(self, mock_client):
        """空结果返回空 DataFrame."""
        mock_client.douyin_ec.search_item_list_v1.return_value = _MockResponse(
            success=True, data={"summary_promotions": []}
        )

        df = joa.search_products("不存在的关键词")
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 0

    def test_search_products_business_failure(self, mock_client):
        """业务失败 (success=False) 返回空 DataFrame, 不抛异常."""
        mock_client.douyin_ec.search_item_list_v1.return_value = _MockResponse(
            success=False,
            code=601,
            message="INSUFFICIENT BALANCE",
            data=None,
        )

        df = joa.search_products("口红")
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 0

    def test_search_products_api_error(self, mock_client):
        """API 异常返回空 DataFrame, 不抛异常."""
        mock_client.douyin_ec.search_item_list_v1.side_effect = Exception(
            "Connection timeout"
        )

        df = joa.search_products("口红")
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 0


# ==================== get_product_detail 测试 ====================


class TestGetProductDetail:
    """商品详情."""

    def test_get_product_detail_returns_dict(self, mock_client):
        """获取详情返回 dict."""
        mock_client.douyin_ec.get_item_detail_v2.return_value = _MockResponse(
            success=True,
            data={"product_id": "123", "title": "测试商品详情"},
        )

        result = joa.get_product_detail("123")
        assert isinstance(result, dict)
        assert result["product_id"] == "123"
        assert result["title"] == "测试商品详情"

    def test_get_product_detail_business_failure(self, mock_client):
        """业务失败返回空 dict."""
        mock_client.douyin_ec.get_item_detail_v2.return_value = _MockResponse(
            success=False, code=404, message="NOT FOUND"
        )

        result = joa.get_product_detail("123")
        assert result == {}

    def test_get_product_detail_error(self, mock_client):
        """API 异常返回空 dict."""
        mock_client.douyin_ec.get_item_detail_v2.side_effect = Exception("Error")

        result = joa.get_product_detail("123")
        assert result == {}


# ==================== get_product_reviews 测试 ====================


class TestGetProductReviews:
    """商品评价."""

    def test_get_product_reviews_returns_dataframe(self, mock_client):
        """获取评价返回 DataFrame."""
        mock_client.douyin_ec.get_item_comments_v1.return_value = _MockResponse(
            success=True,
            data={
                "comments": [
                    {
                        "comment_id": "c1",
                        "content": "很好用, 颜色正",
                        "star": 5,
                        "nickname": "买家A",
                    },
                    {
                        "comment_id": "c2",
                        "content": "一般般",
                        "star": 3,
                        "nickname": "买家B",
                    },
                ]
            },
        )

        df = joa.get_product_reviews("123", count=10)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 2
        assert df.iloc[0]["review_id"] == "c1"
        assert df.iloc[0]["content"] == "很好用, 颜色正"
        assert df.iloc[0]["rating"] == 5
        assert df.iloc[0]["user_name"] == "买家A"

    def test_get_product_reviews_count_truncates(self, mock_client):
        """count 参数截断."""
        comments = [
            {"comment_id": f"c{i}", "content": f"评价{i}", "star": 5}
            for i in range(10)
        ]
        mock_client.douyin_ec.get_item_comments_v1.return_value = _MockResponse(
            success=True, data={"comments": comments}
        )

        df = joa.get_product_reviews("123", count=3)
        assert len(df) == 3

    def test_get_product_reviews_business_failure(self, mock_client):
        """业务失败返回空 DataFrame."""
        mock_client.douyin_ec.get_item_comments_v1.return_value = _MockResponse(
            success=False, message="FAIL"
        )

        df = joa.get_product_reviews("123")
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 0

    def test_get_product_reviews_error(self, mock_client):
        """API 异常返回空 DataFrame."""
        mock_client.douyin_ec.get_item_comments_v1.side_effect = Exception("ERR")

        df = joa.get_product_reviews("123")
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 0


# ==================== _parse_search_results 测试 ====================


class TestParseSearchResults:
    """搜索结果解析."""

    def test_parse_summary_promotions(self):
        """从 summary_promotions 解析."""
        products = joa._parse_search_results(SAMPLE_SEARCH_RESPONSE_DATA)
        assert len(products) == 2
        assert products[0]["product_id"] == "3843782907940438092"
        assert products[1]["product_id"] == "3567186900815080513"

    def test_parse_promotions_fallback_key(self):
        """回退到 'promotions' 键."""
        data = {
            "promotions": [
                {
                    "product_id": "999",
                    "base_model": {
                        "product_info": {"name": "回退测试"},
                        "shop_info": {"shop_name": "店铺X"},
                        "marketing_info": {},
                    },
                }
            ]
        }
        products = joa._parse_search_results(data)
        assert len(products) == 1
        assert products[0]["product_id"] == "999"

    def test_parse_empty_data(self):
        """空 dict 返回空列表."""
        assert joa._parse_search_results({}) == []
        assert joa._parse_search_results(None) == []

    def test_parse_skip_items_without_product_id(self):
        """缺少 product_id 的条目被跳过."""
        data = {
            "summary_promotions": [
                {"product_id": "", "base_model": {}},
                {"base_model": {}},  # 无 product_id
                {
                    "product_id": "123",
                    "base_model": {
                        "product_info": {"name": "有效商品"},
                        "shop_info": {},
                        "marketing_info": {},
                    },
                },
            ]
        }
        products = joa._parse_search_results(data)
        assert len(products) == 1
        assert products[0]["product_id"] == "123"

    def test_parse_non_dict_items_skipped(self):
        """非 dict 的条目被跳过."""
        data = {
            "summary_promotions": [
                "invalid",
                None,
                {"product_id": "1", "base_model": {}},
            ]
        }
        products = joa._parse_search_results(data)
        assert len(products) == 1


# ==================== _extract_price 测试 ====================


class TestExtractPrice:
    """价格提取."""

    def test_extract_price_from_marketing_info(self):
        """从 marketing_info.price_desc.price.origin 提取 (分→元)."""
        marketing_info = {
            "price_desc": {"price": {"origin": 1990}}  # 分
        }
        assert joa._extract_price(marketing_info, {}) == 19.90

    def test_extract_price_marketing_zero_yuan(self):
        """origin=0 → 0.0 元."""
        marketing_info = {"price_desc": {"price": {"origin": 0}}}
        assert joa._extract_price(marketing_info, {}) == 0.0

    def test_extract_price_fallback_to_product_info_price(self):
        """marketing_info 缺失, 回退到 product_info.price."""
        assert joa._extract_price({}, {"price": 99.0}) == 99.0
        assert joa._extract_price({}, {"sale_price": 49.5}) == 49.5
        assert joa._extract_price({}, {"min_price": 19.9}) == 19.9

    def test_extract_price_missing(self):
        """价格缺失返回 0.0."""
        assert joa._extract_price({}, {}) == 0.0

    def test_extract_price_marketing_no_price_desc(self):
        """marketing_info 无 price_desc 字段, 回退."""
        marketing_info = {"other": "x"}
        assert joa._extract_price(marketing_info, {"price": 29.9}) == 29.9


# ==================== _extract_sales 测试 ====================


class TestExtractSales:
    """销量提取."""

    def test_extract_sales_from_month_sale_origin(self):
        """从 product_info.month_sale.origin 提取."""
        product_info = {"month_sale": {"origin": 150}}
        assert joa._extract_sales(product_info) == 150

    def test_extract_sales_month_sale_zero(self):
        """origin=0."""
        assert joa._extract_sales({"month_sale": {"origin": 0}}) == 0

    def test_extract_sales_fallback_to_direct_field(self):
        """month_sale 缺失, 回退."""
        assert joa._extract_sales({"sales": 100}) == 100
        assert joa._extract_sales({"sold_count": 79}) == 79
        assert joa._extract_sales({"sales_count": 200}) == 200

    def test_extract_sales_missing(self):
        """销量缺失返回 0."""
        assert joa._extract_sales({}) == 0


# ==================== _extract_category 测试 ====================


class TestExtractCategory:
    """类目层级解析."""

    def test_extract_category_leaf_layer_3(self):
        """leaf_layer=3 → third_category."""
        category = {
            "leaf_layer": 3,
            "first_category": {"category_id": "1", "category_name": "美妆"},
            "second_category": {"category_id": "10", "category_name": "彩妆"},
            "third_category": {"category_id": "100", "category_name": "有色唇膏/口红"},
        }
        cat_id, cat_name = joa._extract_category(category)
        assert cat_id == "100"
        assert cat_name == "有色唇膏/口红"

    def test_extract_category_leaf_layer_2(self):
        """leaf_layer=2 → second_category."""
        category = {
            "leaf_layer": 2,
            "first_category": {"category_id": "1", "category_name": "美妆"},
            "second_category": {"category_id": "11", "category_name": "唇彩/唇蜜/唇釉"},
        }
        cat_id, cat_name = joa._extract_category(category)
        assert cat_id == "11"
        assert cat_name == "唇彩/唇蜜/唇釉"

    def test_extract_category_leaf_layer_4(self):
        """leaf_layer=4 → fourth_category."""
        category = {
            "leaf_layer": 4,
            "first_category": {"category_id": "1", "category_name": "A"},
            "second_category": {"category_id": "2", "category_name": "B"},
            "third_category": {"category_id": "3", "category_name": "C"},
            "fourth_category": {"category_id": "4", "category_name": "D"},
        }
        cat_id, cat_name = joa._extract_category(category)
        assert cat_id == "4"
        assert cat_name == "D"

    def test_extract_category_no_leaf_layer_defaults_to_third(self):
        """无 leaf_layer, 默认 third_category."""
        category = {
            "first_category": {"category_id": "1", "category_name": "A"},
            "third_category": {"category_id": "3", "category_name": "C"},
        }
        cat_id, cat_name = joa._extract_category(category)
        assert cat_id == "3"

    def test_extract_category_fallback_to_first_non_empty(self):
        """叶子层为空时, 回退到第一个非空层."""
        category = {
            "leaf_layer": 3,
            "first_category": {"category_id": "1", "category_name": "美妆"},
            "second_category": {"category_id": "0", "category_name": ""},
            "third_category": {"category_id": "0", "category_name": ""},  # 叶子为空
        }
        cat_id, cat_name = joa._extract_category(category)
        assert cat_id == "1"
        assert cat_name == "美妆"

    def test_extract_category_empty_dict(self):
        """空 dict 返回 ('', '')."""
        assert joa._extract_category({}) == ("", "")
        assert joa._extract_category(None) == ("", "")


# ==================== _extract_commission 测试 ====================


class TestExtractCommission:
    """佣金提取."""

    def test_extract_commission_from_cos_info(self):
        """从 promotion_info.cos_info.commission_rate 提取."""
        base = {
            "promotion_info": {
                "cos_info": {"commission_rate": 20.0}
            }
        }
        assert joa._extract_commission(base) == 20.0

    def test_extract_commission_rate_alias_key(self):
        """cos_info.rate 字段."""
        base = {
            "promotion_info": {
                "cos_info": {"rate": 15.5}
            }
        }
        assert joa._extract_commission(base) == 15.5

    def test_extract_commission_missing(self):
        """缺失返回 0.0."""
        assert joa._extract_commission({}) == 0.0
        assert joa._extract_commission({"promotion_info": {}}) == 0.0


# ==================== _to_float / _to_int 测试 ====================


class TestToFloat:
    """安全转 float."""

    def test_to_float_int(self):
        assert joa._to_float(1990) == 1990.0

    def test_to_float_float(self):
        assert joa._to_float(19.90) == 19.90

    def test_to_float_string(self):
        assert joa._to_float("19.90") == 19.90

    def test_to_float_string_with_comma(self):
        """带逗号的字符串."""
        assert joa._to_float("1,990") == 1990.0

    def test_to_float_none(self):
        assert joa._to_float(None) == 0.0

    def test_to_float_bool(self):
        """bool 视为 0.0."""
        assert joa._to_float(True) == 0.0

    def test_to_float_invalid_string(self):
        assert joa._to_float("abc") == 0.0


class TestToInt:
    """安全转 int, 支持 '万' / 逗号."""

    def test_to_int_int(self):
        assert joa._to_int(150) == 150

    def test_to_int_float(self):
        assert joa._to_int(150.7) == 150

    def test_to_int_wan(self):
        """'2.5万' → 25000."""
        assert joa._to_int("2.5万") == 25000

    def test_to_int_wan_int(self):
        assert joa._to_int("10万") == 100000

    def test_to_int_string_with_comma(self):
        """'1,000' → 1000."""
        assert joa._to_int("1,000") == 1000

    def test_to_int_string_plain(self):
        assert joa._to_int("150") == 150

    def test_to_int_none(self):
        assert joa._to_int(None) == 0

    def test_to_int_bool(self):
        assert joa._to_int(True) == 0

    def test_to_int_invalid_string(self):
        assert joa._to_int("abc") == 0


# ==================== _parse_reviews 测试 ====================


class TestParseReviews:
    """评价解析."""

    def test_parse_reviews_comments_key(self):
        """从 'comments' 键解析."""
        data = {
            "comments": [
                {"comment_id": "c1", "content": "好评", "star": 5, "nickname": "A"},
            ]
        }
        reviews = joa._parse_reviews(data)
        assert len(reviews) == 1
        assert reviews[0]["review_id"] == "c1"
        assert reviews[0]["rating"] == 5

    def test_parse_reviews_reviews_key(self):
        """回退到 'reviews' 键."""
        data = {
            "reviews": [
                {"review_id": "r1", "content": "好评", "rating": 5},
            ]
        }
        reviews = joa._parse_reviews(data)
        assert len(reviews) == 1
        assert reviews[0]["review_id"] == "r1"

    def test_parse_reviews_list_key(self):
        """回退到 'list' 键."""
        data = {
            "list": [
                {"id": "l1", "text": "中评", "star": 3, "user_name": "X"},
            ]
        }
        reviews = joa._parse_reviews(data)
        assert len(reviews) == 1
        assert reviews[0]["review_id"] == "l1"
        assert reviews[0]["content"] == "中评"
        assert reviews[0]["user_name"] == "X"

    def test_parse_reviews_empty(self):
        """空数据返回空列表."""
        assert joa._parse_reviews({}) == []
        assert joa._parse_reviews(None) == []
        assert joa._parse_reviews({"comments": []}) == []
