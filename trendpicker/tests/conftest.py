"""Pytest 全局 fixtures 与配置.

加载规则:
    - 项目 src/ 加入 sys.path 以支持 `import trendpicker`
    - 测试数据隔离: 不读生产 DB / 不调真实 API
    - 爆款定义参数与方案保持一致
"""

import sys
from pathlib import Path

import pytest

# 将 src/ 加入 sys.path 以支持 `import trendpicker` (生产用 `pip install -e .`)
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))


@pytest.fixture(scope="session")
def project_root() -> Path:
    """项目根目录."""
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def test_data_dir(project_root: Path) -> Path:
    """测试数据目录 (与生产数据严格隔离)."""
    return project_root / "tests" / "fixtures"


@pytest.fixture(scope="session")
def sample_category_id() -> str:
    """测试用虚拟类目 ID."""
    return "TEST_CAT_001"


@pytest.fixture(scope="session")
def hot_product_definition() -> dict:
    """爆款定义参数 (与方案文档一致: 14 天 / 前 5% / 持续 5 天)."""
    return {
        "top_percent": 5,       # 叶子类目前 5%
        "min_days": 5,           # 持续 ≥ 5 天
        "window_days": 14,       # 14 天观察窗口
    }


@pytest.fixture
def mock_env(monkeypatch):
    """注入测试用凭据环境变量 (模拟 CI 路径).

    真实 Keychain 不可在 CI 用, 此 fixture 模拟 GitHub Actions secrets 注入。
    """
    test_credentials = {
        "CHANMAMA_API_KEY": "test_chanmama_key_not_real",
        "ALI1688_APP_KEY": "test_1688_key_not_real",
        "ALI1688_APP_SECRET": "test_1688_secret_not_real",
        "LLM_API_KEY": "test_llm_key_not_real",
    }
    for k, v in test_credentials.items():
        monkeypatch.setenv(k, v)
    return test_credentials


@pytest.fixture
def mock_keychain(monkeypatch):
    """Mock keyring.get_password 返回固定值 (本地路径模拟).

    用法:
        def test_x(mock_keychain):
            val = get_credential("LLM_API_KEY")  # 走 Keychain 路径
            assert val == "fake_keychain_value"
    """

    def _fake_get_password(service: str, name: str):
        if service != "trendpicker":
            return None
        return "fake_keychain_value"

    monkeypatch.setattr("keyring.get_password", _fake_get_password)
    return _fake_get_password
