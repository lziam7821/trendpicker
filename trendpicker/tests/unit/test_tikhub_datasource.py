"""TikHub 数据源适配器测试.

由于 TikHub API 需要付费且有余额限制, 所有测试使用 mock 模拟 API 响应.
"""

import pandas as pd
import pytest

from trendpicker.datasources import tikhub as tikhub_module


@pytest.fixture
def mock_client(mocker):
    """Mock TikHub 客户端."""
    mock = mocker.patch.object(tikhub_module, "_get_client")
    return mock.return_value


class TestSearchProducts:
    """商品搜索."""

    def test_search_products_returns_dataframe(self, mock_client):
        """搜索返回 DataFrame."""
        # 模拟 TikTok Shop 真实返回结构: data.data.component_data.products
        mock_client.tiktok_shop_web.fetch_search_products_list_v2.return_value = {
            "code": 200,
            "data": {
                "data": {
                    "component_data": {
                        "products": [
                            {
                                "product_id": "1729571327109927766",
                                "title": "Silk Finish Lipstick",
                                "product_price_info": {
                                    "sale_price_decimal": "4.99",
                                },
                                "sold_info": {"sold_count": 79},
                                "seller_info": {"shop_name": "Test Shop"},
                            },
                            {
                                "product_id": "456",
                                "title": "测试商品面霜",
                                "product_price_info": {
                                    "sale_price_decimal": "15.90",
                                },
                                "sold_info": {"sold_count": 250},
                                "seller_info": {"shop_name": "测试店铺2"},
                            },
                        ]
                    }
                }
            },
        }

        df = tikhub_module.search_products("lipstick", region="US", count=10)

        assert isinstance(df, pd.DataFrame)
        assert len(df) == 2
        assert df.iloc[0]["product_id"] == "1729571327109927766"
        assert df.iloc[0]["title"] == "Silk Finish Lipstick"
        # sale_price_decimal "4.99" → 4.99
        assert df.iloc[0]["price"] == 4.99
        # sold_count 250
        assert df.iloc[1]["sales"] == 250
        assert df.iloc[0]["shop_name"] == "Test Shop"
        assert df.iloc[0]["source"] == "tikhub"

    def test_search_products_empty_result(self, mock_client):
        """空结果返回空 DataFrame."""
        mock_client.tiktok_shop_web.fetch_search_products_list_v2.return_value = {
            "code": 200,
            "data": {"data": {"component_data": {"products": []}}},
        }

        df = tikhub_module.search_products("不存在的关键词")
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 0

    def test_search_products_api_error(self, mock_client):
        """API 异常返回空 DataFrame."""
        mock_client.tiktok_shop_web.fetch_search_products_list_v2.side_effect = Exception(
            "API Error"
        )

        df = tikhub_module.search_products("口红")
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 0


class TestGetProductDetail:
    """商品详情."""

    def test_get_product_detail_returns_dict(self, mock_client):
        """获取商品详情返回 dict."""
        mock_client.tiktok_shop_web.fetch_product_detail_v3.return_value = {
            "code": 200,
            "data": {"product_id": "123", "title": "测试商品"},
        }

        result = tikhub_module.get_product_detail("123")
        assert isinstance(result, dict)
        assert result["product_id"] == "123"

    def test_get_product_detail_error(self, mock_client):
        """API 异常返回空 dict."""
        mock_client.tiktok_shop_web.fetch_product_detail_v3.side_effect = Exception("Error")

        result = tikhub_module.get_product_detail("123")
        assert result == {}


class TestGetProductReviews:
    """商品评价."""

    def test_get_product_reviews_returns_dataframe(self, mock_client):
        """获取评价返回 DataFrame."""
        mock_client.tiktok_shop_web.fetch_product_reviews_v2.return_value = {
            "code": 200,
            "data": {
                "reviews": [
                    {
                        "review_id": "r1",
                        "content": "很好用",
                        "rating": 5,
                        "user_name": "买家A",
                    },
                    {
                        "review_id": "r2",
                        "content": "一般",
                        "rating": 3,
                        "user_name": "买家B",
                    },
                ]
            },
        }

        df = tikhub_module.get_product_reviews("123", count=10)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 2
        assert df.iloc[0]["content"] == "很好用"
        assert df.iloc[0]["rating"] == 5


class TestPriceParsing:
    """价格解析."""

    def test_parse_price_from_product_price_info(self):
        """从 product_price_info.sale_price_decimal 取价."""
        item = {"product_price_info": {"sale_price_decimal": "4.99"}}
        assert tikhub_module._parse_price(item) == 4.99

    def test_parse_price_direct_float(self):
        """直接 price 字段 (数字)."""
        assert tikhub_module._parse_price({"price": 99.0}) == 99.0

    def test_parse_price_string(self):
        """price 字符串."""
        assert tikhub_module._parse_price({"price": "99.00"}) == 99.0

    def test_parse_price_missing(self):
        """价格缺失."""
        assert tikhub_module._parse_price({}) == 0.0


class TestSalesParsing:
    """销量解析."""

    def test_parse_sales_from_sold_info(self):
        """从 sold_info.sold_count 取销量."""
        item = {"sold_info": {"sold_count": 79}}
        assert tikhub_module._parse_sales(item) == 79

    def test_parse_sales_direct_int(self):
        """直接 sales 字段."""
        assert tikhub_module._parse_sales({"sales": 100}) == 100

    def test_parse_sales_wan(self):
        """万单位销量."""
        assert tikhub_module._parse_sales({"sales": "2.5万"}) == 25000

    def test_parse_sales_string(self):
        """字符串销量."""
        assert tikhub_module._parse_sales({"sales": "1,000"}) == 1000

    def test_parse_sales_missing(self):
        """销量缺失."""
        assert tikhub_module._parse_sales({}) == 0
