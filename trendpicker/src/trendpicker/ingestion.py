"""数据摄入管道模块.

提供:
    - upsert_product: upsert 单条/批量商品数据 (幂等)
    - incremental_ingest: 增量摄入 (只摄入新日期数据)
    - resume_ingest: 续跑 (从上次中断处继续)
    - get_last_ingested_date: 查询某数据源最后摄入的日期
    - ingest_justoneapi_search: 抖音电商(国内)数据摄入编排
        (调用 justoneapi 数据源 → 展开日销序列 → 增量写入 products 表)
"""

import logging
from datetime import date, datetime
from typing import List, Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from .datasources import justoneapi as joa
from .db import get_engine, get_session, init_db

logger = logging.getLogger(__name__)


# 抖音电商国内数据源标识 (写入 products.source 列, 区分多源数据)
JUSTONEAPI_SOURCE = "justoneapi"


def upsert_product(
    engine: Engine,
    record: dict,
    table_name: str = "products",
) -> bool:
    """Upsert 单条商品记录 (幂等).

    使用商品ID + 日期作为联合主键, 重复写入时更新而非报错.

    Args:
        engine: SQLAlchemy 引擎
        record: 商品记录 dict, 需含 product_id 和 date
        table_name: 表名, 默认 "products"

    Returns:
        True 如果插入成功
    """
    required_fields = ["product_id", "date"]
    for field in required_fields:
        if field not in record:
            raise ValueError(f"记录缺少必填字段: {field}")

    with engine.begin() as conn:
        # 先尝试删除已有记录 (联合主键)
        conn.execute(
            text(
                f"DELETE FROM {table_name} "
                f"WHERE product_id = :pid AND date = :dt"
            ),
            {"pid": record["product_id"], "dt": str(record["date"])},
        )
        # 插入新记录
        columns = list(record.keys())
        placeholders = {k: f":{k}" for k in columns}
        col_str = ", ".join(columns)
        val_str = ", ".join(placeholders.values())
        conn.execute(
            text(f"INSERT INTO {table_name} ({col_str}) VALUES ({val_str})"),
            record,
        )

    return True


def upsert_batch(
    engine: Engine,
    records: List[dict],
    table_name: str = "products",
) -> int:
    """批量 upsert 商品记录.

    Args:
        engine: SQLAlchemy 引擎
        records: 商品记录列表
        table_name: 表名

    Returns:
        成功写入的记录数
    """
    if not records:
        return 0

    count = 0
    with engine.begin() as conn:
        for record in records:
            required_fields = ["product_id", "date"]
            for field in required_fields:
                if field not in record:
                    raise ValueError(f"记录缺少必填字段: {field}")

            conn.execute(
                text(
                    f"DELETE FROM {table_name} "
                    f"WHERE product_id = :pid AND date = :dt"
                ),
                {"pid": record["product_id"], "dt": str(record["date"])},
            )
            columns = list(record.keys())
            placeholders = {k: f":{k}" for k in columns}
            col_str = ", ".join(columns)
            val_str = ", ".join(placeholders.values())
            conn.execute(
                text(f"INSERT INTO {table_name} ({col_str}) VALUES ({val_str})"),
                record,
            )
            count += 1

    return count


def get_last_ingested_date(
    engine: Engine,
    source: str,
) -> Optional[str]:
    """查询某数据源最后摄入的日期.

    用于增量摄入和续跑.

    Args:
        engine: SQLAlchemy 引擎
        source: 数据源名称

    Returns:
        最后摄入的日期字符串 (YYYY-MM-DD), 无记录返回 None
    """
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT last_date FROM ingestion_state WHERE source = :src"
            ),
            {"src": source},
        )
        row = result.fetchone()

    if row is None:
        return None
    return row[0]


def update_ingestion_state(
    engine: Engine,
    source: str,
    last_date: str,
) -> None:
    """更新数据源的摄入状态 (续跑锚点).

    Args:
        engine: SQLAlchemy 引擎
        source: 数据源名称
        last_date: 最后摄入日期
    """
    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM ingestion_state WHERE source = :src"
            ),
            {"src": source},
        )
        conn.execute(
            text(
                "INSERT INTO ingestion_state (source, last_date) VALUES (:src, :dt)"
            ),
            {"src": source, "dt": last_date},
        )


def incremental_ingest(
    engine: Engine,
    source: str,
    records: List[dict],
    date_col: str = "date",
) -> int:
    """增量摄入: 只写入日期 > last_date 的记录.

    流程:
        1. 查询 ingestion_state 获取 last_date
        2. 过滤 records 中 date > last_date 的记录
        3. upsert 过滤后的记录
        4. 更新 ingestion_state

    Args:
        engine: SQLAlchemy 引擎
        source: 数据源名称
        records: 待摄入记录列表
        date_col: 日期字段名

    Returns:
        实际写入的记录数
    """
    last_date = get_last_ingested_date(engine, source)

    if last_date:
        # 增量: 只取日期 > last_date 的记录
        new_records = [
            r for r in records
            if str(r.get(date_col, "")) > last_date
        ]
    else:
        new_records = records

    if not new_records:
        logger.info("数据源 %s 无新记录, 跳过", source)
        return 0

    count = upsert_batch(engine, new_records)

    # 更新状态为最大日期
    max_date = max(str(r[date_col]) for r in new_records)
    update_ingestion_state(engine, source, max_date)

    logger.info("数据源 %s 增量摄入 %d 条记录, last_date=%s", source, count, max_date)
    return count


def resume_ingest(
    engine: Engine,
    source: str,
    records: List[dict],
    date_col: str = "date",
) -> int:
    """续跑: 从上次中断处继续摄入.

    与 incremental_ingest 类似, 但明确语义为"从中断点恢复",
    会读取 ingestion_state 并从中断日期开始 (包含当天).

    Args:
        engine: SQLAlchemy 引擎
        source: 数据源名称
        records: 待摄入记录列表
        date_col: 日期字段名

    Returns:
        实际写入的记录数
    """
    last_date = get_last_ingested_date(engine, source)

    if last_date:
        # 续跑: 从 last_date 开始 (包含当天, 因为可能中断在当天)
        resume_records = [
            r for r in records
            if str(r.get(date_col, "")) >= last_date
        ]
    else:
        resume_records = records

    if not resume_records:
        return 0

    # upsert 保证幂等 (重复写入不会产生重复行)
    count = upsert_batch(engine, resume_records)

    # 更新状态
    max_date = max(str(r[date_col]) for r in resume_records)
    update_ingestion_state(engine, source, max_date)

    logger.info("数据源 %s 续跑摄入 %d 条记录, last_date=%s", source, count, max_date)
    return count


def query_products(
    engine: Engine,
    category_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    source: Optional[str] = None,
) -> pd.DataFrame:
    """查询商品数据.

    Args:
        engine: SQLAlchemy 引擎
        category_id: 类目 ID 过滤
        start_date: 开始日期
        end_date: 结束日期
        source: 数据源过滤

    Returns:
        商品数据 DataFrame
    """
    query = "SELECT * FROM products WHERE 1=1"
    params: dict = {}

    if category_id:
        query += " AND category_id = :cat"
        params["cat"] = category_id
    if start_date:
        query += " AND date >= :sd"
        params["sd"] = start_date
    if end_date:
        query += " AND date <= :ed"
        params["ed"] = end_date
    if source:
        query += " AND source = :src"
        params["src"] = source

    return pd.read_sql(text(query), engine.connect(), params=params)


# ==================== 数据源编排: JustOneAPI 抖音电商 ====================


def ingest_justoneapi_search(
    engine: Engine,
    keyword: str,
    page: int = 1,
    count: int = 30,
) -> int:
    """从 JustOneAPI 搜索抖音电商商品并增量摄入.

    流程:
        1. 调用 justoneapi.search_products 获取商品 + 30 天日销序列
        2. 把每个商品的 sale_axis 展开为多条 (product_id, date, sales) 日记录
           共享 title/price/category_id/shop_name 等快照字段
        3. 调用 incremental_ingest 增量写入 products 表

    sale_axis 中 x 格式为 'YYYYMMDD', 转为 'YYYY-MM-DD' 后写入 date 列.
    sale_axis 为空的商品跳过 (无可摄入的日销量数据).

    Args:
        engine: SQLAlchemy 引擎
        keyword: 搜索关键词
        page: 页码, 默认 1
        count: 商品数上限

    Returns:
        实际写入的记录数 (按行计, 一件商品可写多条日记录)
    """
    df = joa.search_products(keyword=keyword, page=page, count=count)
    if len(df) == 0:
        logger.info("JustOneAPI 搜索无结果, keyword=%s", keyword)
        return 0

    records = _expand_justoneapi_to_daily(df)
    if not records:
        logger.info("JustOneAPI 搜索结果无 sale_axis 可展开, keyword=%s", keyword)
        return 0

    logger.info(
        "JustOneAPI 摄入: keyword=%s, 商品=%d, 日记录=%d",
        keyword, len(df), len(records),
    )

    return incremental_ingest(engine, JUSTONEAPI_SOURCE, records)


def _expand_justoneapi_to_daily(df: pd.DataFrame) -> List[dict]:
    """把 justoneapi 搜索 DataFrame 展开为日记录列表.

    每行商品的 sale_axis 是 [{x: 'YYYYMMDD', y: 日销}, ...], 展开后:
        每条 sale_axis 生成一条记录 {product_id, date, sales, title, ...}

    sale_axis 为空 / 非列表 / y 非数字 的条目跳过.

    Args:
        df: justoneapi.search_products 返回的 DataFrame

    Returns:
        日记录列表, 每条 dict 含 product_id/date/title/price/sales/
        category_id/shop_name/source 字段
    """
    records: List[dict] = []

    for _, row in df.iterrows():
        sale_axis = row.get("sale_axis")
        if not isinstance(sale_axis, list) or len(sale_axis) == 0:
            continue

        product_id = str(row.get("product_id") or "")
        if not product_id:
            continue

        title = row.get("title") or ""
        price = _safe_float(row.get("price"))
        category_id = str(row.get("category_id") or "")

        for point in sale_axis:
            if not isinstance(point, dict):
                continue
            date_str = _format_justoneapi_date(point.get("x"))
            if not date_str:
                continue
            sales_val = _safe_int(point.get("y"))

            records.append({
                "product_id": product_id,
                "date": date_str,
                "title": title,
                "price": price,
                "sales": float(sales_val),
                "category_id": category_id,
                "source": JUSTONEAPI_SOURCE,
            })

    return records


def _format_justoneapi_date(x) -> str:
    """把 sale_axis 的 x 字段转为 'YYYY-MM-DD'.

    sale_axis.x 格式 'YYYYMMDD' (如 '20260823') → '2026-08-23'.
    非字符串或长度不足返回空串.

    Args:
        x: 原始日期值

    Returns:
        'YYYY-MM-DD' 字符串或空串
    """
    if not isinstance(x, str) or not x.isdigit() or len(x) != 8:
        return ""
    return f"{x[0:4]}-{x[4:6]}-{x[6:8]}"


def _safe_float(value) -> float:
    """安全转 float (None/字符串/异常均返回 0.0)."""
    if value is None or isinstance(value, bool):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value) -> int:
    """安全转 int (None/字符串/异常均返回 0)."""
    if value is None or isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
