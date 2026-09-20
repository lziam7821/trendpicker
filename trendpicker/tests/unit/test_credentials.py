"""凭据管理模块测试.

覆盖范围:
    - get_credential: Keychain 优先 + 环境变量回退
    - set_credential: 写入 Keychain
    - delete_credential: 删除凭据
    - 未注册凭据名抛 KeyError

覆盖率目标: 80%
"""

import pytest

from trendpicker.credentials import (
    CREDENTIAL_NAMES,
    delete_credential,
    get_credential,
    list_registered_credentials,
    set_credential,
)


class TestGetCredential:
    """读取凭据."""

    def test_get_credential_from_keychain(self, mock_keychain):
        """从 Keychain 读取凭据."""
        val = get_credential("LLM_API_KEY")
        assert val == "fake_keychain_value"

    def test_get_credential_from_env(self, mock_env, monkeypatch):
        """环境变量回退路径 (CI 模式)."""
        # Keychain 不可用时返回 None → 走环境变量回退
        monkeypatch.setattr("keyring.get_password", lambda s, n: None)
        val = get_credential("LLM_API_KEY")
        assert val == "test_llm_key_not_real"

    def test_get_credential_keychain_priority(self, mock_keychain, mock_env):
        """Keychain 优先于环境变量."""
        val = get_credential("CHANMAMA_API_KEY")
        assert val == "fake_keychain_value"

    def test_get_credential_unregistered_raises(self):
        """未注册凭据名抛 KeyError."""
        with pytest.raises(KeyError):
            get_credential("UNKNOWN_CREDENTIAL_XYZ")

    def test_get_credential_missing_raises(self, monkeypatch):
        """已注册但未配置的凭据抛 KeyError."""
        # Keychain 和环境变量均无值
        monkeypatch.setattr("keyring.get_password", lambda s, n: None)
        monkeypatch.delenv("DOUYIN_SHOP_TOKEN", raising=False)
        with pytest.raises(KeyError, match="未配置"):
            get_credential("DOUYIN_SHOP_TOKEN")


class TestSetCredential:
    """写入凭据."""

    def test_set_credential_calls_keyring(self, monkeypatch):
        """set_credential 调用 keyring.set_password."""
        calls = []
        def _fake_set(service, name, value):
            calls.append((service, name, value))
        monkeypatch.setattr("keyring.set_password", _fake_set)
        set_credential("LLM_API_KEY", "test_value")
        assert len(calls) == 1
        assert calls[0][2] == "test_value"

    def test_set_credential_unregistered_raises(self):
        """未注册凭据名抛 KeyError."""
        with pytest.raises(KeyError):
            set_credential("UNKNOWN_CREDENTIAL", "value")


class TestDeleteCredential:
    """删除凭据."""

    def test_delete_credential_calls_keyring(self, monkeypatch):
        """delete_credential 调用 keyring.delete_password."""
        called = []
        def _fake_delete(service, name):
            called.append((service, name))
        monkeypatch.setattr("keyring.delete_password", _fake_delete)
        delete_credential("LLM_API_KEY")
        assert len(called) == 1

    def test_delete_credential_unregistered_raises(self):
        """未注册凭据名抛 KeyError."""
        with pytest.raises(KeyError):
            delete_credential("UNKNOWN_CREDENTIAL")

    def test_delete_credential_not_found_no_error(self, monkeypatch):
        """凭据不存在时不抛异常."""
        import keyring
        monkeypatch.setattr(
            "keyring.delete_password",
            lambda s, n: (_ for _ in ()).throw(keyring.errors.PasswordDeleteError())
        )
        # 不应抛异常
        delete_credential("LLM_API_KEY")


class TestRegistry:
    """凭据注册清单."""

    def test_list_registered_credentials(self):
        """返回凭据清单."""
        creds = list_registered_credentials()
        assert "CHANMAMA_API_KEY" in creds
        assert "LLM_API_KEY" in creds
        assert "ALI1688_APP_KEY" in creds
        assert len(creds) == len(CREDENTIAL_NAMES)

    def test_all_credentials_have_description(self):
        """每个凭据都有用途说明."""
        creds = list_registered_credentials()
        for name, desc in creds.items():
            assert isinstance(desc, str)
            assert len(desc) > 0
