"""数据库管理模块.

管理 SQLite 数据库连接, 执行迁移脚本, 提供 upsert 等基础操作.

本地使用 SQLite, P2 后切换 PostgreSQL (接口不变, 仅改连接字符串).
"""

import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_MIGRATIONS_DIR = _PROJECT_ROOT / "migrations"

# 商品数据表 (ingestion 使用)
_PRODUCT_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS products (
    product_id    TEXT NOT NULL,
    date          TEXT NOT NULL,
    category_id   TEXT NOT NULL,
    title         TEXT,
    sales         REAL DEFAULT 0,
    price         REAL,
    image_url     TEXT,
    phash         TEXT,
    source        TEXT DEFAULT 'unknown',
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (product_id, date)
);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category_id);
CREATE INDEX IF NOT EXISTS idx_products_date ON products(date);
CREATE INDEX IF NOT EXISTS idx_products_source ON products(source);
"""

# 摄入运行状态表 (增量/续跑)
_INGESTION_STATE_SQL = """
CREATE TABLE IF NOT EXISTS ingestion_state (
    source       TEXT NOT NULL,
    last_date    TEXT NOT NULL,
    updated_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source)
);
"""


def get_engine(db_path: Optional[str] = None) -> Engine:
    """创建 SQLAlchemy 引擎.

    Args:
        db_path: 数据库文件路径, 默认从环境变量 TRENDPICKER_DB_PATH 读取

    Returns:
        SQLAlchemy Engine
    """
    if db_path is None:
        db_path = os.environ.get("TRENDPICKER_DB_PATH", "trendpicker.db")

    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    return engine


def init_db(engine: Optional[Engine] = None) -> Engine:
    """初始化数据库: 执行迁移脚本 + 创建商品表.

    Args:
        engine: SQLAlchemy 引擎, 默认自动创建

    Returns:
        初始化后的 Engine
    """
    if engine is None:
        engine = get_engine()

    with engine.connect() as conn:
        # 执行迁移脚本
        for migration_file in sorted(_MIGRATIONS_DIR.glob("*.sql")):
            logger.info("执行迁移: %s", migration_file.name)
            sql_text = migration_file.read_text(encoding="utf-8")
            # SQLite 需要逐条执行 (多语句)
            statements = [s.strip() for s in sql_text.split(";") if s.strip()]
            for stmt in statements:
                conn.execute(text(stmt))

        # 创建商品表
        for stmt in [s.strip() for s in _PRODUCT_TABLE_SQL.split(";") if s.strip()]:
            conn.execute(text(stmt))

        # 创建摄入状态表
        for stmt in [_INGESTION_STATE_SQL.strip()]:
            conn.execute(text(stmt))

        conn.commit()

    logger.info("数据库初始化完成")
    return engine


@contextmanager
def get_session(engine: Engine) -> Generator[Session, None, None]:
    """获取数据库 Session (上下文管理器).

    Args:
        engine: SQLAlchemy 引擎

    Yields:
        Session 对象
    """
    session = Session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
