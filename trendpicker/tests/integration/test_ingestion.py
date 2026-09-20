"""数据摄入管道集成测试.

覆盖范围:
    - upsert 幂等性 (同一记录二次写入不产生重复行)
    - 增量策略 (商品ID + 日期联合主键)
    - 中断后续跑 (重启续跑机制)

覆盖率目标: 60%
"""

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from trendpicker.db import init_db
from trendpicker.ingestion import (
    get_last_ingested_date,
    incremental_ingest,
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
