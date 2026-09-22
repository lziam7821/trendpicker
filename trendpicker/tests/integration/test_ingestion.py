"""数据摄入管道集成测试.

覆盖范围:
    - upsert 幂等性 (同一记录二次写入不产生重复行)
    - 增量策略 (商品ID + 日期联合主键)
    - 中断后续跑 (重启续跑机制)
    - ingest_justoneapi_search 编排: justoneapi 数据源 → sale_axis 展开 → 增量写入

覆盖率目标: 60%
"""

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from trendpicker.db import init_db
from trendpicker.ingestion import (
    get_last_ingested_date,
    incremental_ingest,
    ingest_justoneapi_search,
    resume_ingest,
    update_ingestion_state,
    upsert_product,
    upsert_batch,
)


@pytest.fixture
def test_engine(tmp_path):
    """创建测试用内存数据库."""
    engine = create_engine("sqlite:///:memory:")
    # 手动创建 products 和 ingestion_state 表
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE products (
                product_id TEXT NOT NULL,
                date TEXT NOT NULL,
                category_id TEXT,
                title TEXT,
                sales REAL DEFAULT 0,
                price REAL,
                image_url TEXT,
                phash TEXT,
                source TEXT DEFAULT 'unknown',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (product_id, date)
            )
        """))
        conn.execute(text("""
            CREATE TABLE ingestion_state (
                source TEXT NOT NULL,
                last_date TEXT NOT NULL,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (source)
            )
        """))
        conn.commit()
    return engine


class TestUpsertIdempotency:
    """upsert 幂等性."""

    def test_upsert_same_record_twice_no_duplicate(self, test_engine):
        """二次 upsert 不产生重复行."""
        record = {
            "product_id": "P001",
            "date": "2024-01-01",
            "category_id": "CAT_001",
            "title": "测试商品",
            "sales": 100.0,
            "price": 29.9,
        }
        upsert_product(test_engine, record)
        upsert_product(test_engine, record)

        with test_engine.connect() as conn:
            rows = conn.execute(
                text("SELECT * FROM products WHERE product_id = 'P001' AND date = '2024-01-01'")
            ).fetchall()
        assert len(rows) == 1

    def test_upsert_partial_field_update(self, test_engine):
        """upsert 仅更新部分字段, 其他字段保持."""
        record1 = {"product_id": "P001", "date": "2024-01-01", "title": "旧标题", "sales": 50.0}
        record2 = {"product_id": "P001", "date": "2024-01-01", "title": "新标题", "sales": 100.0}
        upsert_product(test_engine, record1)
        upsert_product(test_engine, record2)

        with test_engine.connect() as conn:
            row = conn.execute(
                text("SELECT * FROM products WHERE product_id = 'P001' AND date = '2024-01-01'")
            ).fetchone()
        assert row[3] == "新标题"  # title updated
        assert row[4] == 100.0     # sales updated

    def test_upsert_composite_key(self, test_engine):
        """商品ID + 日期联合主键的 upsert."""
        records = [
            {"product_id": "P001", "date": "2024-01-01", "sales": 10.0},
            {"product_id": "P001", "date": "2024-01-02", "sales": 20.0},
            {"product_id": "P002", "date": "2024-01-01", "sales": 30.0},
        ]
        upsert_batch(test_engine, records)

        with test_engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM products")).scalar()
        assert count == 3


class TestIncrementalStrategy:
    """增量策略."""

    def test_incremental_only_new_dates(self, test_engine):
        """增量只摄入新日期的数据."""
        # 先摄入 2024-01-01 和 01-02
        records_batch1 = [
            {"product_id": "P001", "date": "2024-01-01", "sales": 10.0},
            {"product_id": "P001", "date": "2024-01-02", "sales": 20.0},
        ]
        incremental_ingest(test_engine, "chanmama", records_batch1)
        assert get_last_ingested_date(test_engine, "chanmama") == "2024-01-02"

        # 再摄入: 包含 01-02 (已存在) 和 01-03 (新)
        records_batch2 = [
            {"product_id": "P001", "date": "2024-01-02", "sales": 25.0},  # 更新
            {"product_id": "P001", "date": "2024-01-03", "sales": 30.0},  # 新增
        ]
        count = incremental_ingest(test_engine, "chanmama", records_batch2)
        assert count == 1  # 只有 01-03 是新的
        assert get_last_ingested_date(test_engine, "chanmama") == "2024-01-03"

    def test_incremental_resume_after_interrupt(self, test_engine):
        """中断后续跑 (重启续跑机制 - 状态落库)."""
        # 首次摄入到 01-03
        records1 = [
            {"product_id": "P001", "date": "2024-01-03", "sales": 30.0},
        ]
        incremental_ingest(test_engine, "chanmama", records1)

        # 模拟中断后续跑: 从 01-03 开始 (包含当天)
        records2 = [
            {"product_id": "P001", "date": "2024-01-03", "sales": 35.0},  # 更新当天
            {"product_id": "P001", "date": "2024-01-04", "sales": 40.0},  # 新增
        ]
        count = resume_ingest(test_engine, "chanmama", records2)
        assert count == 2  # 续跑包含当天
        assert get_last_ingested_date(test_engine, "chanmama") == "2024-01-04"

    def test_incremental_dedup_cross_run(self, test_engine):
        """跨运行去重."""
        record = {"product_id": "P001", "date": "2024-01-01", "sales": 10.0}

        # 第一次运行
        incremental_ingest(test_engine, "chanmama", [record])
        # 第二次运行 (相同日期, 不应重复)
        count = incremental_ingest(test_engine, "chanmama", [record])
        assert count == 0  # 增量模式跳过已存在的日期

        with test_engine.connect() as conn:
            rows = conn.execute(
                text("SELECT * FROM products WHERE product_id = 'P001'")
            ).fetchall()
        assert len(rows) == 1


class TestIngestJustOneAPISearch:
    """ingest_justoneapi_search 编排: 数据源 → 展开 sale_axis → 增量写入."""

    @pytest.fixture
    def mock_search(self, mocker):
        """Mock justoneapi.search_products, 返回可控 DataFrame."""
        df = pd.DataFrame([
            {
                "product_id": "P001",
                "title": "金钻缎光雾感口红",
                "price": 19.90,
                "sales": 150,
                "shop_name": "明哥小铺01",
                "category_id": "100",
                "category_name": "有色唇膏/口红",
                "commission": 20.0,
                "sale_axis": [
                    {"x": "20260823", "y": 10},
                    {"x": "20260824", "y": 20},
                    {"x": "20260825", "y": 25},
                ],
                "source": "justoneapi",
            },
            {
                "product_id": "P002",
                "title": "黑钻三色口红",
                "price": 29.90,
                "sales": 14,
                "shop_name": "姿色严选美妆店",
                "category_id": "11",
                "category_name": "唇彩/唇蜜/唇釉",
                "commission": 0.0,
                "sale_axis": [
                    {"x": "20260823", "y": 5},
                    {"x": "20260824", "y": 9},
                ],
                "source": "justoneapi",
            },
        ])
        return mocker.patch(
            "trendpicker.ingestion.joa.search_products",
            return_value=df,
        )

    def test_expands_sale_axis_to_daily_records(self, test_engine, mock_search):
        """sale_axis 展开为多条日记录 (P001×3 + P002×2 = 5 行)."""
        count = ingest_justoneapi_search(test_engine, "口红")

        assert count == 5
        assert get_last_ingested_date(test_engine, "justoneapi") == "2026-08-25"

        with test_engine.connect() as conn:
            rows = conn.execute(
                text("SELECT * FROM products WHERE source='justoneapi' ORDER BY product_id, date")
            ).fetchall()
        # 5 行: P001 (3) + P002 (2)
        assert len(rows) == 5
        # 第一条: P001/2026-08-23
        assert rows[0][0] == "P001"      # product_id
        assert rows[0][1] == "2026-08-23"  # date (格式转换)
        assert rows[0][3] == "金钻缎光雾感口红"  # title
        assert rows[0][4] == 10.0        # sales 来自 sale_axis[0].y
        assert rows[0][5] == 19.90       # price 快照
        assert rows[0][2] == "100"       # category_id

    def test_skips_products_without_sale_axis(self, test_engine, mocker):
        """sale_axis 为空的商品跳过."""
        df = pd.DataFrame([
            {
                "product_id": "P001",
                "title": "无销量序列商品",
                "price": 19.90,
                "sales": 0,
                "shop_name": "X",
                "category_id": "100",
                "category_name": "C",
                "commission": 0.0,
                "sale_axis": [],  # 空, 应跳过
                "source": "justoneapi",
            },
        ])
        mocker.patch(
            "trendpicker.ingestion.joa.search_products",
            return_value=df,
        )

        count = ingest_justoneapi_search(test_engine, "口红")
        assert count == 0

    def test_empty_search_returns_zero(self, test_engine, mocker):
        """搜索无结果返回 0."""
        mocker.patch(
            "trendpicker.ingestion.joa.search_products",
            return_value=pd.DataFrame(),
        )
        count = ingest_justoneapi_search(test_engine, "不存在关键词")
        assert count == 0

    def test_incremental_skips_existing_dates(self, test_engine, mock_search):
        """二次调用增量: 只摄入日期 > last_date 的记录."""
        # 第一次: 写入 P001 3天 + P002 2天
        first_count = ingest_justoneapi_search(test_engine, "口红")
        assert first_count == 5
        assert get_last_ingested_date(test_engine, "justoneapi") == "2026-08-25"

        # 第二次相同数据: 所有日期 <= last_date, 应跳过
        second_count = ingest_justoneapi_search(test_engine, "口红")
        assert second_count == 0

    def test_skips_invalid_sale_axis_points(self, test_engine, mocker):
        """sale_axis 中 x 非日期 / y 非数字的条目跳过."""
        df = pd.DataFrame([
            {
                "product_id": "P001",
                "title": "异常 sale_axis",
                "price": 9.9,
                "sales": 100,
                "shop_name": "S",
                "category_id": "1",
                "category_name": "C",
                "commission": 0.0,
                "sale_axis": [
                    {"x": "20260823", "y": 10},   # 有效
                    {"x": "bad-date", "y": 5},     # x 非日期 → 跳过
                    {"x": "20260824"},             # 无 y → 0 (仍写入, y=0)
                    {"x": 12345678, "y": 5},       # x 非 str → 跳过
                    "invalid",                    # 非 dict → 跳过
                ],
                "source": "justoneapi",
            },
        ])
        mocker.patch(
            "trendpicker.ingestion.joa.search_products",
            return_value=df,
        )

        count = ingest_justoneapi_search(test_engine, "口红")
        # 只 20260823 (y=10) 和 20260824 (y=0) 两条有效
        assert count == 2

        with test_engine.connect() as conn:
            rows = conn.execute(
                text("SELECT date, sales FROM products WHERE product_id='P001' ORDER BY date")
            ).fetchall()
        assert len(rows) == 2
        assert rows[0] == ("2026-08-23", 10.0)
        assert rows[1] == ("2026-08-24", 0.0)


class TestJustOneAPIDateFormatter:
    """_format_justoneapi_date / _safe_float / _safe_int 单元测试."""

    def test_format_date_yyyymmdd(self):
        from trendpicker.ingestion import _format_justoneapi_date
        assert _format_justoneapi_date("20260823") == "2026-08-23"

    def test_format_date_invalid_returns_empty(self):
        from trendpicker.ingestion import _format_justoneapi_date
        # 非字符串 / 长度不足 / 含非数字字符
        assert _format_justoneapi_date(12345678) == ""
        assert _format_justoneapi_date("2026") == ""
        assert _format_justoneapi_date("2026082") == ""
        assert _format_justoneapi_date("2026082a") == ""
        assert _format_justoneapi_date(None) == ""
        assert _format_justoneapi_date("") == ""

    def test_safe_float(self):
        from trendpicker.ingestion import _safe_float
        assert _safe_float(19.90) == 19.90
        assert _safe_float("19.90") == 19.90
        assert _safe_float(None) == 0.0
        assert _safe_float(True) == 0.0  # bool 视为 0
        assert _safe_float("abc") == 0.0

    def test_safe_int(self):
        from trendpicker.ingestion import _safe_int
        assert _safe_int(10) == 10
        assert _safe_int("10") == 10
        assert _safe_int(None) == 0
        assert _safe_int(True) == 0
        assert _safe_int("abc") == 0
