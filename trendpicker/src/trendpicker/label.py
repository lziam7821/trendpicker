"""爆款标签构造模块.

爆款定义: 14 天内进入叶子类目前 5% 且持续 ≥ 5 天.

提供:
    - compute_category_percentile: 类目内分位数计算
    - construct_hot_labels: 爆款标签构造 (边界值严格判断)
"""

import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def compute_category_percentile(
    df: pd.DataFrame,
    value_col: str,
    category_col: str = "category_id",
    top_percent: float = 5.0,
) -> pd.Series:
    """计算每个商品在其类目内的分位数排名.

    top_percent=5 表示前 5% 的阈值分位线.

    Args:
        df: 商品数据, 需含 value_col 和 category_col
        value_col: 排名依据列 (如销量)
        category_col: 类目列, 默认 "category_id"
        top_percent: 前 N% 分位线, 默认 5

    Returns:
        pd.Series: 每行的分位数 (0-100), 100 表示类目内最高
    """
    if df.empty:
        return pd.Series(dtype=float)

    def _percentile_rank(group: pd.Series) -> pd.Series:
        """计算一组数据的百分位排名."""
        if len(group) == 0:
            return pd.Series(dtype=float)

        if len(group) == 1:
            # 退化场景: 仅 1 个商品 → 100 分位
            return pd.Series([100.0], index=group.index)

        # 百分位排名: (rank - 1) / (n - 1) * 100
        ranks = group.rank(method="average")
        n = len(group)
        return (ranks - 1) / (n - 1) * 100

    percentiles = df.groupby(category_col)[value_col].transform(_percentile_rank)
    return percentiles


def construct_hot_labels(
    df: pd.DataFrame,
    product_id_col: str = "product_id",
    date_col: str = "date",
    category_col: str = "category_id",
    sales_col: str = "sales",
    top_percent: float = 5.0,
    min_days: int = 5,
    window_days: int = 14,
) -> pd.DataFrame:
    """构造爆款标签.

    爆款定义:
        在 window_days 天的观察窗口内, 商品在其叶子类目内
        销量排名进入前 top_percent%, 且持续 >= min_days 天.

    Args:
        df: 商品-日级数据, 需含 product_id_col, date_col, category_col, sales_col
        product_id_col: 商品ID列, 默认 "product_id"
        date_col: 日期列, 默认 "date"
        category_col: 类目列, 默认 "category_id"
        sales_col: 销量列, 默认 "sales"
        top_percent: 前百分之N算爆款, 默认 5
        min_days: 持续天数下限, 默认 5
        window_days: 观察窗口天数, 默认 14

    Returns:
        DataFrame: 每行增加 "percentile" 和 "is_hot" 列
    """
    if df.empty:
        return df.assign(percentile=pd.Series(dtype=float), is_hot=pd.Series(dtype=bool))

    result = df.copy()
    result[date_col] = pd.to_datetime(result[date_col])

    # 计算每日类目内分位数
    result["percentile"] = compute_category_percentile(
        result, sales_col, category_col, top_percent
    )

    # 判断每天是否进入前 top_percent
    threshold = 100.0 - top_percent
    result["in_top_percent"] = result["percentile"] >= threshold

    # 计算窗口内持续天数
    result = result.sort_values([product_id_col, date_col])
    result["days_in_top"] = (
        result.groupby(product_id_col)["in_top_percent"]
        .rolling(window=window_days, min_periods=1)
        .sum()
        .reset_index(level=0, drop=True)
    )

    # 爆款标签: 持续天数 >= min_days
    result["is_hot"] = result["days_in_top"] >= min_days

    return result


def get_hot_products(
    df: pd.DataFrame,
    product_id_col: str = "product_id",
    date_col: str = "date",
    category_col: str = "category_id",
    sales_col: str = "sales",
    top_percent: float = 5.0,
    min_days: int = 5,
    window_days: int = 14,
) -> pd.DataFrame:
    """获取爆款商品列表 (每个商品最新一天的标签).

    Args:
        同 construct_hot_labels

    Returns:
        DataFrame: 仅含 is_hot=True 的商品, 每个商品一行 (最新日期)
    """
    labeled = construct_hot_labels(
        df,
        product_id_col=product_id_col,
        date_col=date_col,
        category_col=category_col,
        sales_col=sales_col,
        top_percent=top_percent,
        min_days=min_days,
        window_days=window_days,
    )

    if labeled.empty:
        return labeled

    # 取每个商品最新日期的行
    labeled = labeled.sort_values(date_col)
    latest = labeled.groupby(product_id_col).last().reset_index()

    return latest[latest["is_hot"]].copy()
