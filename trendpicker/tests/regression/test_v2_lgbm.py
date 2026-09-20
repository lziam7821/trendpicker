"""V2 LightGBM 回测 + 与 V1 A/B 对比测试.

切换门槛:
    V2 AUC ≥ 0.75 且命中率较 V1 提升 ≥ 5 个百分点

固定随机种子: LightGBM random_state=42 (保证可复现)
校准: Isotonic Regression
"""

import pytest


@pytest.mark.skip(reason="等待 v2_lgbm 模块实现后补全")
class TestV2Backtest:
    """V2 回测."""

    def test_v2_random_seed_reproducibility(self):
        """相同数据 + random_state=42 → 相同预测."""

    def test_v2_auc_meets_threshold(self):
        """回测 AUC ≥ 0.75."""

    def test_v2_brier_score_calibration(self):
        """Brier score 反映校准误差 (Isotonic 后)."""


@pytest.mark.skip(reason="等待 v2_lgbm 模块实现后补全")
class TestABComparison:
    """V1 vs V2 A/B 对比."""

    def test_v2_beats_v1_on_auc(self):
        """V2 AUC > V1 AUC."""

    def test_v2_beats_v1_on_hit_rate_by_5pp(self):
        """V2 命中率较 V1 提升 ≥ 5 个百分点."""

    def test_ab_same_historical_window(self):
        """A/B 在同一历史窗口进行 (避免数据泄漏)."""

    def test_model_registry_versioning(self):
        """每次回测写入 model_registry (版本/日期/AUC/命中率/是否采纳)."""
