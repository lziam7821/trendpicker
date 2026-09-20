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

from trendpicker.compliance.anonymizer import (
    anonymize,
    find_pii,
    is_product_spec,
    mask_chinese_name,
    mask_email,
    mask_id_card,
    mask_phone,
)


class TestChinesePIIMasking:
    """中文 PII 脱敏."""

    def test_chinese_phone_masked(self):
        """11 位手机号 → 138****1234."""
        text = "联系我 13812345678 看看"
        result = mask_phone(text)
        assert "138****5678" in result
        assert "13812345678" not in result

    def test_chinese_id_card_masked(self):
        """18 位身份证号脱敏."""
        text = "身份证号 110101199001011234 保密"
        result = mask_id_card(text)
        assert "110101199001011234" not in result
        assert "110101" in result  # 前6位保留
        assert "34" in result      # 最后2位保留

    def test_chinese_name_masked(self):
        """中文姓名 (自定义 Recognizer) 脱敏."""
        text = "联系人张三和王小明"
        result = mask_chinese_name(text)
        # 姓氏保留, 名字掩码
        assert "张三" not in result
        assert "张" in result  # 姓保留
        assert "*" in result

    def test_email_masked(self):
        """邮箱地址脱敏."""
        text = "邮箱 user@example.com 联系"
        result = mask_email(text)
        assert "user@example.com" not in result
        assert "u***@example.com" in result
        assert "example.com" in result


class TestRegexFallback:
    """正则兜底 (规则+正则, 非 pure NER)."""

    def test_phone_regex_fallback(self):
        """Presidio 漏检时, 正则兜底捕获手机号."""
        text = "订单 13800001111 完成"
        findings = find_pii(text)
        phone_findings = [f for f in findings if f["type"] == "phone"]
        assert len(phone_findings) == 1

    def test_id_card_regex_fallback(self):
        """身份证号正则兜底."""
        text = "证件 110101200001011234 记录"
        findings = find_pii(text)
        id_findings = [f for f in findings if f["type"] == "id_card"]
        assert len(id_findings) == 1

    def test_no_false_positive_on_product_specs(self):
        """商品规格数字 (例如重量 500g) 不被误判为 PII."""
        text = "商品重量 500g 规格 30ml"
        findings = find_pii(text)
        # 不应匹配到手机号或身份证
        assert not any(f["type"] in ("phone", "id_card") for f in findings)
        # is_product_spec 应返回 True
        assert is_product_spec("500g") is True
        assert is_product_spec("30ml") is True


class TestFullAnonymization:
    """全量脱敏."""

    def test_anonymize_all_pii(self):
        """一次脱敏所有 PII."""
        text = "手机 13812345678 邮箱 test@site.com 身份证 110101199001011234"
        result = anonymize(text)
        assert "13812345678" not in result
        assert "test@site.com" not in result
        assert "110101199001011234" not in result

    def test_anonymize_empty(self):
        """空文本不报错."""
        assert anonymize("") == ""
