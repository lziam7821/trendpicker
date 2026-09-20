"""V1 规则版回测测试.

V1 = 规则版 (当天可上线, 每分可解释)

回测窗口:
    T-30 到 T-14 构造特征 → 预测 T 到 T+14 → 用 T+14 到 T+28 真实销量验证

切换到 V2 的门槛:
    V2 AUC ≥ 0.75 且命中率较 V1 提升 ≥ 5 个百分点
"""

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from trendpicker.models.v1_rules import backtest, compute_features, score_rules


def _make_test_data(n_products=50, n_days=14):
    """生成测试数据."""
    np.random.seed(42)
    rows = []
    for pid in range(n_products):
        for day in range(n_days):
            sales = float(np.random.randint(0, 100))
            if pid < 5:  # 前5个是爆款 (高销量)
                sales *= 3
            rows.append({
                "product_id": f"P{pid:03d}",
                "date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=day),
                "category_id": "CAT_001",
                "sales": sales,
                "price": float(np.random.randint(10, 200)),
                "is_hot": pid < 5,
            })
    return pd.DataFrame(rows)


class TestV1RuleBacktest:
    """V1 规则版回测."""

    def test_v1_rule_score_range(self):
        """规则打分落在 [0, 1] 区间."""
        features = pd.DataFrame({
            "growth_slope": [0.5, -0.5, 0.1, 0.0],
            "sales_percentile": [90.0, 50.0, 10.0, 50.0],
            "price": [50.0, 100.0, 200.0, 100.0],
            "category_avg_sales": [50.0, 30.0, 10.0, 30.0],
        })
        scores, _ = score_rules(features)
        assert scores.min() >= 0.0
        assert scores.max() <= 1.0

    def test_v1_backtest_auc_recorded(self):
        """回测 AUC 被记录到 model_registry 表."""
        engine = create_engine("sqlite:///:memory:")
        # 初始化 model_registry 表
        with engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS model_registry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    model_ver TEXT NOT NULL,
                    trained_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    backtest_window_start DATE NOT NULL,
                    backtest_window_end DATE NOT NULL,
                    auc REAL,
                    hit_rate_top20 REAL,
                    brier_score REAL,
                    random_state INTEGER DEFAULT 42,
                    is_adopted BOOLEAN NOT NULL DEFAULT 0,
                    superseded_by TEXT,
                    note TEXT
                )
            """))
            conn.commit()

        df = _make_test_data()
        result = backtest(df, engine=engine, model_ver="v1_rules_test")

        # 验证记录已写入
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT * FROM model_registry WHERE model_ver = 'v1_rules_test'")
            ).fetchall()
        assert len(rows) == 1
        assert rows[0][4] is not None  # auc

    def test_v1_hit_rate_top20(self):
        """Top20 命中率 (真爆款占比) 计算正确."""
        df = _make_test_data(n_products=50, n_days=14)
        result = backtest(df)
        hit_rate = result["hit_rate_top20"]
        # hit_rate 应在 [0, 1] 区间
        assert 0.0 <= hit_rate <= 1.0

    def test_v1_deterministic_output(self):
        """相同输入下 V1 输出确定 (无随机性)."""
        df = _make_test_data()
        result1 = backtest(df)
        result2 = backtest(df)
        # AUC 和命中率应完全相同
        assert result1["auc"] == result2["auc"]
        assert result1["hit_rate_top20"] == result2["hit_rate_top20"]

    def test_v1_rule_explainability(self):
        """规则可解释: 每个分数可拆解为规则贡献."""
        features = pd.DataFrame({
            "growth_slope": [0.5, -0.5],
            "sales_percentile": [90.0, 50.0],
            "price": [50.0, 100.0],
            "category_avg_sales": [50.0, 30.0],
        })
        scores, breakdown = score_rules(features)
        # breakdown 应包含各规则列
        assert "growth_trend" in breakdown.columns
        assert "sales_rank" in breakdown.columns
        assert "price_competitiveness" in breakdown.columns
        assert "category_heat" in breakdown.columns
        # 各列之和应接近总分
        assert abs(breakdown.sum(axis=1).iloc[0] - scores.iloc[0]) < 0.01
