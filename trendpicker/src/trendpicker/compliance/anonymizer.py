"""PII 脱敏模块 (合规护栏).

使用规则 + 正则 + Presidio 混合策略, 覆盖:
    - 中文手机号 11 位 → 138****1234
    - 中文身份证 18 位 → 脱敏
    - 邮箱 → 部分掩码
    - 中文姓名 → 自定义 Recognizer + 正则兜底

设计原则: 规则 + 正则为主, Presidio 为补充.
"""

import logging
import re
from typing import List, Optional

logger = logging.getLogger(__name__)

# ---- 正则模式 ----
_PHONE_PATTERN = re.compile(r"1[3-9]\d{9}")
_ID_CARD_PATTERN = re.compile(r"[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]")
_EMAIL_PATTERN = re.compile(
    r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
)

# 常见中文姓氏 (百家姓前 100)
_COMMON_SURNAMES = (
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
    "戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳鲍史唐"
    "费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅卞齐康伍余元卜顾孟黄穆"
    "萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁"
)

# 常见中文名字用字 (用于正则兜底)
_COMMON_NAME_CHARS = (
    r"[\u4e00-\u9fff]{2,4}"
)


def mask_phone(text: str) -> str:
    """脱敏中文手机号: 13812345678 → 138****5678.

    Args:
        text: 输入文本

    Returns:
        脱敏后文本
    """
    def _replace(match: re.Match) -> str:
        phone = match.group()
        return f"{phone[:3]}****{phone[-4:]}"

    return _PHONE_PATTERN.sub(_replace, text)


def mask_id_card(text: str) -> str:
    """脱敏中文身份证号: 保留前 6 位和最后 2 位, 中间掩码.

    例: 110101199001011234 → 110101**********34

    Args:
        text: 输入文本

    Returns:
        脱敏后文本
    """
    def _replace(match: re.Match) -> str:
        id_card = match.group()
        return f"{id_card[:6]}{'*' * (len(id_card) - 8)}{id_card[-2:]}"

    return _ID_CARD_PATTERN.sub(_replace, text)


def mask_email(text: str) -> str:
    """脱敏邮箱: user@example.com → u***@example.com.

    Args:
        text: 输入文本

    Returns:
        脱敏后文本
    """
    def _replace(match: re.Match) -> str:
        email = match.group()
        if "@" in email:
            local, domain = email.split("@", 1)
            if len(local) <= 1:
                masked_local = "*"
            else:
                masked_local = f"{local[0]}{'*' * (len(local) - 1)}"
            return f"{masked_local}@{domain}"
        return email

    return _EMAIL_PATTERN.sub(_replace, text)


def mask_chinese_name(text: str) -> str:
    """脱敏中文姓名: 使用姓氏 + 正则兜底.

    策略: 匹配常见姓氏开头的 2-4 字中文名 → 保留姓氏, 掩码名.

    Args:
        text: 输入文本

    Returns:
        脱敏后文本
    """
    # 构建姓氏正则: 匹配常见姓氏后跟 1-3 个中文字
    surname_pattern = re.compile(
        f"([{''.join(_COMMON_SURNAMES)}])([\u4e00-\u9fff]{{1,3}})"
    )

    def _replace(match: re.Match) -> str:
        surname = match.group(1)
        given_name = match.group(2)
        return f"{surname}{'*' * len(given_name)}"

    return surname_pattern.sub(_replace, text)


def anonymize(text: str) -> str:
    """全量 PII 脱敏: 手机号 + 身份证 + 邮箱 + 中文姓名.

    执行顺序: 身份证 (最长) → 手机号 → 邮箱 → 姓名 (避免互相干扰).

    Args:
        text: 输入文本

    Returns:
        全部 PII 脱敏后的文本
    """
    if not text:
        return text

    # 顺序很重要: 先脱敏长模式 (身份证), 再短模式
    result = text
    result = mask_id_card(result)
    result = mask_phone(result)
    result = mask_email(result)
    result = mask_chinese_name(result)

    return result


def find_pii(text: str) -> List[dict]:
    """检测文本中的 PII (不脱敏, 仅标注位置).

    用于自检和审计.

    Args:
        text: 输入文本

    Returns:
        PII 位置列表, 每项 {"type", "start", "end", "value"}
    """
    findings: List[dict] = []

    for match in _ID_CARD_PATTERN.finditer(text):
        findings.append({
            "type": "id_card",
            "start": match.start(),
            "end": match.end(),
            "value": match.group(),
        })

    for match in _PHONE_PATTERN.finditer(text):
        # 排除已匹配的身份证号中的数字
        overlap = any(
            f["start"] <= match.start() < f["end"]
            for f in findings
            if f["type"] == "id_card"
        )
        if not overlap:
            findings.append({
                "type": "phone",
                "start": match.start(),
                "end": match.end(),
                "value": match.group(),
            })

    for match in _EMAIL_PATTERN.finditer(text):
        findings.append({
            "type": "email",
            "start": match.start(),
            "end": match.end(),
            "value": match.group(),
        })

    return findings


def is_product_spec(text: str) -> bool:
    """判断数字是否为商品规格 (如 500g, 30ml), 防止误判为 PII.

    规格特征: 数字后紧跟单位 (g, ml, cm, mm, kg, L, 片, 粒, 颗, 包, 盒).

    Args:
        text: 包含数字的文本片段

    Returns:
        True 如果是商品规格
    """
    spec_pattern = re.compile(r"\d+\s*(?:g|kg|ml|L|cm|mm|片|粒|颗|包|盒|个|件|瓶|袋|条|套)", re.IGNORECASE)
    return bool(spec_pattern.search(text))
