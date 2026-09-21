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
        # 模拟 TikHub 返回结构
        mock_client.tiktok_shop_web.fetch_search_products_list_v2.return_value = {
            "code": 200,
            "data": {
                "products": [
                    {
                        "product_id": "123",
                        "title": "测试商品口红",
                        "price": 9900,  # 分
                        "sales": 1000,
                        "shop_name": "测试店铺",
                        "category_id": "cat_001",
                    },
                    {
                        "product_id": "456",
                        "title": "测试商品面霜",
                        "price": 159.0,
                        "sales": "2.5万",
                        "shop_name": "测试店铺2",
                        "category_id": "cat_002",
                    },
                ]
            },
        }

        df = tikhub_module.search_products("口红", region="cn", count=10)

        assert isinstance(df, pd.DataFrame)
        assert len(df) == 2
        assert df.iloc[0]["product_id"] == "123"
        assert df.iloc[0]["title"] == "测试商品口红"
        # 价格 9900 分 → 99.0 元
        assert df.iloc[0]["price"] == 99.0
        # 销量 "2.5万" → 25000
        assert df.iloc[1]["sales"] == 25000
        assert df.iloc[0]["source"] == "tikhub"

    def test_search_products_empty_result(self, mock_client):
        """空结果返回空 DataFrame."""
        mock_client.tiktok_shop_web.fetch_search_products_list_v2.return_value = {
            "code": 200,
            "data": None,
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

    def test_parse_price_in_cents(self):
        """价格以分为单位."""
        assert tikhub_module._parse_price({"price": 9900}) == 99.0

    def test_parse_price_in_yuan(self):
        """价格以元为单位."""
        assert tikhub_module._parse_price({"price": 99.0}) == 99.0

    def test_parse_price_string(self):
        """价格字符串."""
        assert tikhub_module._parse_price({"price": "99.00"}) == 99.0

    def test_parse_price_missing(self):
        """价格缺失."""
        assert tikhub_module._parse_price({}) == 0.0


class TestSalesParsing:
    """销量解析."""

    def test_parse_sales_int(self):
        """整数销量."""
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
