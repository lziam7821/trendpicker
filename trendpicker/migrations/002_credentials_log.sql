-- ============================================================
-- 凭据访问审计日志表
-- 用途: 记录凭据读取/轮换/写入/删除, 满足合规审计要求
-- 轮换周期建议:
--   服务商 API Key (蝉妈妈/1688/淘宝客/多多进宝) - 90 天
--   LLM API Key - 30 天
-- ============================================================

CREATE TABLE IF NOT EXISTS credentials_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    credential_name TEXT    NOT NULL,                       -- 凭据名 (来自 CREDENTIAL_NAMES)
    action          TEXT    NOT NULL CHECK(action IN ('read', 'rotate', 'set', 'delete')),
    actor           TEXT    NOT NULL DEFAULT 'system',      -- 操作人 (单人即本人)
    occurred_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    note            TEXT                                    -- 备注 (例如 "90 天定期轮换")
);

-- 查询凭据的访问历史 (审计)
CREATE INDEX IF NOT EXISTS idx_credentials_log_name ON credentials_log(credential_name);

-- 查询某类操作 (例如所有 rotate 事件)
CREATE INDEX IF NOT EXISTS idx_credentials_log_action ON credentials_log(action);

-- 按时间倒序查询最近事件 (轮换提醒)
CREATE INDEX IF NOT EXISTS idx_credentials_log_time ON credentials_log(occurred_at DESC);

-- 轮换提醒视图: 列出 90 天未轮换的服务商凭据
CREATE VIEW IF NOT EXISTS v_rotation_overdue AS
SELECT
    credential_name,
    MAX(occurred_at) AS last_rotated,
    JULIANDAY('now') - JULIANDAY(MAX(occurred_at)) AS days_since_rotation
FROM credentials_log
WHERE action = 'rotate'
GROUP BY credential_name
HAVING days_since_rotation > 90;
