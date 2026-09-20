-- ============================================================
-- 模型版本登记表
-- 用途: 跟踪每次模型训练的指标, 作为 V1 → V2 切换决策依据
-- 切换门槛: V2 AUC ≥ 0.75 且命中率较 V1 提升 ≥ 5 个百分点
-- 回测窗口: T-30 → T+14 预测 → T+14 → T+28 真实销量验证
-- 固定随机种子: LightGBM random_state=42 (可复现)
-- ============================================================

CREATE TABLE IF NOT EXISTS model_registry (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    model_ver             TEXT     NOT NULL,                           -- 例如 "v1_rules", "v2_lgbm_v3"
    trained_at            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    backtest_window_start DATE     NOT NULL,                           -- T-30
    backtest_window_end   DATE     NOT NULL,                           -- T+28
    auc                   REAL,                                        -- AUC 指标 (V2 门槛 ≥ 0.75)
    hit_rate_top20        REAL,                                        -- Top20 命中率 (真爆款占比)
    brier_score            REAL,                                        -- 校准误差 (Isotonic 后)
    random_state          INTEGER  DEFAULT 42,                         -- 固定随机种子
    is_adopted            BOOLEAN  NOT NULL DEFAULT 0,                 -- 是否采纳为生产版本
    superseded_by         TEXT,                                        -- 被哪个版本替代
    note                  TEXT                                         -- 备注 (例如切换决策依据)
);

-- 同一版本同一天不可重复登记 (重复训练需更新 model_ver)
CREATE UNIQUE INDEX IF NOT EXISTS idx_model_registry_ver
    ON model_registry(model_ver, trained_at);

-- 快速查询当前生产版本
CREATE INDEX IF NOT EXISTS idx_model_registry_adopted
    ON model_registry(is_adopted) WHERE is_adopted = 1;

-- 按时间倒序查询训练历史
CREATE INDEX IF NOT EXISTS idx_model_registry_time
    ON model_registry(trained_at DESC);

-- ============================================================
-- 视图: V1 vs V2 A/B 对比 (切换决策依据)
-- ============================================================
CREATE VIEW IF NOT EXISTS v_ab_comparison AS
SELECT
    v1.model_ver           AS v1_ver,
    v1.auc                 AS v1_auc,
    v1.hit_rate_top20      AS v1_hit_rate,
    v2.model_ver           AS v2_ver,
    v2.auc                 AS v2_auc,
    v2.hit_rate_top20      AS v2_hit_rate,
    (v2.auc - v1.auc)               AS auc_delta,
    (v2.hit_rate_top20 - v1.hit_rate_top20) * 100 AS hit_rate_delta_pp,
    CASE
        WHEN v2.auc >= 0.75
         AND (v2.hit_rate_top20 - v1.hit_rate_top20) * 100 >= 5
        THEN 'PASS'
        ELSE 'FAIL'
    END                     AS switch_decision
FROM model_registry v1
JOIN model_registry v2
    ON v2.trained_at > v1.trained_at
   AND v2.model_ver LIKE 'v2_%'
   AND v1.model_ver LIKE 'v1_%'
   AND v2.backtest_window_start = v1.backtest_window_start  -- 同一历史窗口
   AND v2.backtest_window_end   = v1.backtest_window_end;
