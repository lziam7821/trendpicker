"""V1 规则版回测测试.

V1 = 规则版 (当天可上线, 每分可解释)

回测窗口:
    T-30 到 T-14 构造特征 → 预测 T 到 T+14 → 用 T+14 到 T+28 真实销量验证

切换到 V2 的门槛:
    V2 AUC ≥ 0.75 且命中率较 V1 提升 ≥ 5 个百分点
"""

import pytest


@pytest.mark.skip(reason="等待 v1_rules 模块实现后补全")
class TestV1RuleBacktest:
    """V1 规则版回测."""

    def test_v1_rule_score_range(self):
        """规则打分落在 [0, 1] 区间."""

    def test_v1_backtest_auc_recorded(self):
        """回测 AUC 被记录到 model_registry 表."""

    def test_v1_hit_rate_top20(self):
        """Top20 命中率 (真爆款占比) 计算正确."""

    def test_v1_deterministic_output(self):
        """相同输入下 V1 输出确定 (无随机性)."""

    def test_v1_rule_explainability(self):
        """规则可解释: 每个分数可拆解为规则贡献."""
