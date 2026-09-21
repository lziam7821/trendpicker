"""凭据管理模块.

三层防护设计:
    1. 代码禁硬编码 - 由 detect-secrets pre-commit hook 在提交前阻断
    2. 存储分离 - 非敏感配置走 .env, 敏感凭据走 macOS Keychain (keyring 库)
    3. 访问审计与轮换 - credentials_log 表记录读取/轮换/写入

读取路径:
    - 本地: macOS Keychain (优先)
    - CI: 环境变量回退 (GitHub Actions secrets 注入)
    两条路径共用同一 get_credential 函数, 代码无分支。

凭据清单 (注册制 - 未在 CREDENTIAL_NAMES 登记的凭据名禁止读取):
    - TIKHUB_API_KEY          TikHub API Key - 抖音商城数据 (主数据源)
    - CHANMAMA_API_KEY        蝉妈妈 API Key (备用数据源)
    - ALI1688_APP_KEY         1688 AppKey (货源匹配)
    - ALI1688_APP_SECRET      1688 AppSecret (货源匹配)
    - TAOBAOKE_TOKEN          淘宝客 token (联盟数据补充)
    - PDD_DUO_TOKEN           多多进宝 token (联盟数据补充)
    - LLM_API_KEY             LLM API Key (标题润色/情感分析)
    - DOUYIN_SHOP_TOKEN       抖店店铺授权 token (P2 后启用)
    - PDD_SHOP_TOKEN          PDD 店铺授权 token (P2 后启用)

轮换周期建议:
    - 服务商 API Key (蝉妈妈/1688/淘宝客/多多进宝): 每 90 天
    - LLM API Key: 每 30 天
"""

import logging
import os
from typing import Dict

import keyring
from dotenv import load_dotenv

# 仅加载非敏感配置 (类目 ID / 价格带 / 模型版本号 / DB 路径等)
load_dotenv()

logger = logging.getLogger(__name__)

# Keychain 服务名 - 用于本地凭据命名空间隔离
SERVICE_NAME = os.environ.get("TRENDPICKER_KEYRING_SERVICE", "trendpicker")

# 凭据清单: name -> 用途说明 (审计与轮换提醒用)
CREDENTIAL_NAMES: Dict[str, str] = {
    "TIKHUB_API_KEY": "TikHub API Key - 抖音商城数据主数据源 (90 天轮换)",
    "CHANMAMA_API_KEY": "蝉妈妈 API Key - 备用数据源 (90 天轮换)",
    "ALI1688_APP_KEY": "1688 AppKey - 货源匹配 (90 天轮换)",
    "ALI1688_APP_SECRET": "1688 AppSecret - 货源匹配 (90 天轮换)",
    "TAOBAOKE_TOKEN": "淘宝客 token - 联盟数据补充 (90 天轮换)",
    "PDD_DUO_TOKEN": "多多进宝 token - 联盟数据补充 (90 天轮换)",
    "LLM_API_KEY": "LLM API Key - 标题润色/情感分析 (30 天轮换)",
    "DOUYIN_SHOP_TOKEN": "抖店店铺授权 token - 销售回流 (P2 后启用)",
    "PDD_SHOP_TOKEN": "PDD 店铺授权 token - 销售回流 (P2 后启用)",
}


def get_credential(name: str) -> str:
    """读取凭据值.

    优先级:
        1. macOS Keychain (本地开发路径)
        2. 环境变量 (GitHub Actions CI 路径 - secrets 注入)

    Args:
        name: 凭据名, 必须在 CREDENTIAL_NAMES 中注册

    Returns:
        凭据值 (str)

    Raises:
        KeyError: 凭据名未注册, 或 Keychain 与环境变量均缺失
    """
    if name not in CREDENTIAL_NAMES:
        raise KeyError(
            f"未注册的凭据名: {name}. "
            f"请在 CREDENTIAL_NAMES 中登记后再调用 get_credential."
        )

    # 1. 优先读 macOS Keychain (Linux 无 backend 时回退到环境变量)
    try:
        value = keyring.get_password(SERVICE_NAME, name)
    except keyring.errors.NoKeyringError:
        # Linux/CI 环境无 Keychain backend, 直接走环境变量
        value = None

    # 2. 回退环境变量 (CI 路径 - GitHub Actions secrets)
    if value is None:
        value = os.environ.get(name)

    if value is None:
        raise KeyError(
            f"凭据 {name} 未配置: Keychain (service={SERVICE_NAME}) "
            f"与环境变量均缺失. "
            f"本地用 set_credential() 写入 Keychain, CI 在 repo secrets 注入."
        )

    logger.debug("读取凭据 %s", name)
    return value


def set_credential(name: str, value: str) -> None:
    """将凭据写入 macOS Keychain.

    仅用于本地初始化或轮换写入. CI 环境不应调用此函数.

    Args:
        name: 凭据名 (必须在 CREDENTIAL_NAMES 中注册)
        value: 凭据值

    Raises:
        KeyError: 凭据名未注册
    """
    if name not in CREDENTIAL_NAMES:
        raise KeyError(f"未注册的凭据名: {name}. 请先在 CREDENTIAL_NAMES 登记.")
    keyring.set_password(SERVICE_NAME, name, value)
    logger.info("已写入凭据 %s 到 Keychain (service=%s)", name, SERVICE_NAME)


def delete_credential(name: str) -> None:
    """从 Keychain 删除凭据 (轮换或下线时用).

    Args:
        name: 凭据名

    Raises:
        KeyError: 凭据名未注册
    """
    if name not in CREDENTIAL_NAMES:
        raise KeyError(f"未注册的凭据名: {name}")
    try:
        keyring.delete_password(SERVICE_NAME, name)
        logger.info("已从 Keychain 删除凭据 %s", name)
    except keyring.errors.PasswordDeleteError:
        logger.warning("凭据 %s 在 Keychain 中不存在, 跳过删除", name)


def list_registered_credentials() -> Dict[str, str]:
    """返回凭据清单 (name -> 用途说明).

    用于:
        - 审计: 列出所有应轮换的凭据
        - 自检: 启动时验证凭据是否齐全
        - 文档: 生成凭据清单
    """
    return dict(CREDENTIAL_NAMES)
