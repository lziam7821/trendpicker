"""Presidio 脱敏测试 (合规护栏).

覆盖范围:
    - 中文昵称剥离 (自定义 Recognizer)
    - 中文手机号 11 位脱敏
    - 中文身份证 18 位脱敏
    - 邮箱脱敏
    - 兜底规则 (规则+正则, 非 pure NER)

覆盖率目标: 70%
"""

import pytest


@pytest.mark.skip(reason="等待 compliance/anonymizer.py 实现后补全")
class TestChinesePIIMasking:
    """中文 PII 脱敏."""

    def test_chinese_phone_masked(self):
        """11 位手机号 → 138****1234."""

    def test_chinese_id_card_masked(self):
        """18 位身份证号脱敏."""

    def test_chinese_name_masked(self):
        """中文姓名 (自定义 Recognizer) 脱敏."""

    def test_email_masked(self):
        """邮箱地址脱敏."""


@pytest.mark.skip(reason="等待 compliance/anonymizer.py 实现后补全")
class TestRegexFallback:
    """正则兜底 (规则+正则, 非 pure NER)."""

    def test_phone_regex_fallback(self):
        """Presidio 漏检时, 正则兜底捕获手机号."""

    def test_id_card_regex_fallback(self):
        """身份证号正则兜底."""

    def test_no_false_positive_on_product_specs(self):
        """商品规格数字 (例如重量 500g) 不被误判为 PII."""
