"""数据摄入管道集成测试.

覆盖范围:
    - upsert 幂等性 (同一记录二次写入不产生重复行)
    - 增量策略 (商品ID + 日期联合主键)
    - 中断后续跑 (重启续跑机制)

覆盖率目标: 60%
"""

import pytest


@pytest.mark.skip(reason="等待 ingestion 模块实现后补全")
class TestUpsertIdempotency:
    """upsert 幂等性."""

    def test_upsert_same_record_twice_no_duplicate(self):
        """二次 upsert 不产生重复行."""

    def test_upsert_partial_field_update(self):
        """upsert 仅更新部分字段, 其他字段保持."""

    def test_upsert_composite_key(self):
        """商品ID + 日期联合主键的 upsert."""


@pytest.mark.skip(reason="等待 ingestion 模块实现后补全")
class TestIncrementalStrategy:
    """增量策略."""

    def test_incremental_only_new_dates(self):
        """增量只摄入新日期的数据."""

    def test_incremental_resume_after_interrupt(self):
        """中断后续跑 (重启续跑机制 - 状态落库)."""

    def test_incremental_dedup_cross_run(self):
        """跨运行去重."""
