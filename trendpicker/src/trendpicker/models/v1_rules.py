"""V1 规则版模型.

特点:
    - 当天可上线, 每分可解释
    - 规则打分, 输出落在 [0, 1] 区间
    - 确定性输出 (无随机性)

规则体系:
    - 增长趋势 (β > 0 加分)
    - 销量排名 (百分位高加分)
    - 价格竞争力 (低价加分)
    - 类目热度

回测窗口: T-30 → T+14 预测 → T+14 → T+28 真实销量验证
"""

import logging
from datetime import date, datetime, timedelta
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from ..db import get_engine, init_db
from ..features import growth_slope, min_max_normalize, three_sigma_clip

logger = logging.getLogger(__name__)

# 规则权重 (可解释, 可调整)
RULE_WEIGHTS: Dict[str, float] = {
    "growth_trend": 0.30,      # 增长趋势权重
    "sales_rank": 0.30,        # 销量排名权重
    "price_competitiveness": 0.20,  # 价格竞争力
    "category_heat": 0.20,     # 类目热度
}


def score_rules(
    features_df: pd.DataFrame,
    weights: Optional[Dict[str, float]] = None,
) -> Tuple[pd.Series, pd.DataFrame]:
    """规则打分.

    基于特征列计算规则分数, 输出 [0, 1] 区间.
    每个分数可拆解为各规则贡献.

    所需特征列:
        - growth_slope: 增速斜率 β
        - sales_percentile: 销量百分位 (0-100)
        - price: 价格
        - category_avg_sales: 类目平均销量

    Args:
        features_df: 特征 DataFrame
        weights: 规则权重, 默认使用 RULE_WEIGHTS

    Returns:
        (scores, breakdown) 元组:
        - scores: 综合分数 pd.Series ([0, 1])
        - breakdown: 各规则贡献分解 DataFrame
    """
    if weights is None:
        weights = RULE_WEIGHTS

    n = len(features_df)
    if n == 0:
        return (
            pd.Series(dtype=float),
            pd.DataFrame(),
        )

    breakdown = pd.DataFrame(index=features_df.index)

    # 规则1: 增长趋势 (β > 0 加分, 用 sigmoid 映射到 [0, 1])
    if "growth_slope" in features_df.columns:
        beta = features_df["growth_slope"].fillna(0)
        # sigmoid 映射: β 越大分数越高
        growth_score = 1 / (1 + np.exp(-beta))
        breakdown["growth_trend"] = growth_score * weights["growth_trend"]
    else:
        breakdown["growth_trend"] = 0.5 * weights["growth_trend"]

    # 规则2: 销量排名 (百分位越高分数越高)
    if "sales_percentile" in features_df.columns:
        rank_score = features_df["sales_percentile"].fillna(0) / 100.0
        breakdown["sales_rank"] = rank_score * weights["sales_rank"]
    else:
        breakdown["sales_rank"] = 0.5 * weights["sales_rank"]

    # 规则3: 价格竞争力 (低价加分, 用倒数映射)
    if "price" in features_df.columns:
        prices = features_df["price"].fillna(0)
        if prices.max() > 0:
            price_norm = min_max_normalize(prices)
            # 低价 → 高分 (取反)
            price_score = 1.0 - price_norm
        else:
            price_score = pd.Series(0.5, index=features_df.index)
        breakdown["price_competitiveness"] = price_score * weights["price_competitiveness"]
    else:
        breakdown["price_competitiveness"] = 0.5 * weights["price_competitiveness"]

    # 规则4: 类目热度 (类目平均销量越高越热)
    if "category_avg_sales" in features_df.columns:
        heat = features_df["category_avg_sales"].fillna(0)
        if heat.max() > 0:
            heat_score = min_max_normalize(heat)
        else:
            heat_score = pd.Series(0.5, index=features_df.index)
        breakdown["category_heat"] = heat_score * weights["category_heat"]
    else:
        breakdown["category_heat"] = 0.5 * weights["category_heat"]

    # 综合分数
    scores = breakdown.sum(axis=1)

    # 截断到 [0, 1]
    scores = scores.clip(lower=0.0, upper=1.0)

    return scores, breakdown


def compute_features(
    df: pd.DataFrame,
    product_id_col: str = "product_id",
    date_col: str = "date",
    sales_col: str = "sales",
    price_col: str = "price",
    category_col: str = "category_id",
    window: int = 14,
) -> pd.DataFrame:
    """从原始商品-日级数据计算 V1 模型所需特征.

    Args:
        df: 商品-日级数据
        product_id_col: 商品ID列
        date_col: 日期列
        sales_col: 销量列
        price_col: 价格列
        category_col: 类目列
        window: 窗口大小, 默认 14

    Returns:
        特征 DataFrame (每商品一行)
    """
    if df.empty:
        return df

    df = df.copy()
    df[date_col] = pd.to_datetime(df[date_col])

    features_list = []

    for product_id, group in df.groupby(product_id_col):
        group = group.sort_values(date_col)
        recent = group.tail(window)

        feat: Dict[str, float] = {
            product_id_col: product_id,
        }

        # 增长趋势
        if sales_col in recent.columns:
            feat["growth_slope"] = growth_slope(recent[sales_col], window=window)
        else:
            feat["growth_slope"] = 0.0

        # 销量百分位 (在类目内)
        if category_col in group.columns and sales_col in group.columns:
            cat = group[category_col].iloc[-1]
            cat_data = df[df[category_col] == cat]
            latest_sales = cat_data.groupby(product_id_col)[sales_col].last()
            if len(latest_sales) > 1:
                rank = latest_sales.rank(method="average")
                feat["sales_percentile"] = float(
                    (rank.loc[product_id] - 1) / (len(latest_sales) - 1) * 100
                )
            else:
                feat["sales_percentile"] = 100.0
        else:
            feat["sales_percentile"] = 0.0

        # 价格
        if price_col in recent.columns:
            feat[price_col] = float(recent[price_col].iloc[-1]) if not recent[price_col].empty else 0.0

        # 类目平均销量
        if category_col in group.columns and sales_col in group.columns:
            cat = group[category_col].iloc[-1]
            cat_data = df[df[category_col] == cat]
            feat["category_avg_sales"] = float(cat_data[sales_col].mean())
        else:
            feat["category_avg_sales"] = 0.0

        if category_col in group.columns:
            feat[category_col] = group[category_col].iloc[-1]

        features_list.append(feat)

    return pd.DataFrame(features_list)


def backtest(
    df: pd.DataFrame,
    label_col: str = "is_hot",
    product_id_col: str = "product_id",
    date_col: str = "date",
    sales_col: str = "sales",
    price_col: str = "price",
    category_col: str = "category_id",
    engine: Optional[Engine] = None,
    model_ver: str = "v1_rules",
    window: int = 14,
    lookback_days: int = 30,
    predict_horizon: int = 14,
) -> Dict:
    """V1 规则版回测.

    回测窗口: T-30 到 T-14 构造特征 → 预测 T 到 T+14
    → 用 T+14 到 T+28 真实销量验证.

    Args:
        df: 商品-日级数据 (需含 label_col)
        label_col: 爆款标签列
        engine: 数据库引擎 (记录到 model_registry)
        model_ver: 模型版本名
        window: 特征窗口
        lookback_days: 回看天数
        predict_horizon: 预测天数

    Returns:
        回测结果 dict: {"auc", "hit_rate_top20", "scores", "breakdown"}
    """
    from sklearn.metrics import roc_auc_score

    if df.empty:
        return {"auc": 0.0, "hit_rate_top20": 0.0, "scores": pd.Series(), "breakdown": pd.DataFrame()}

    # 计算特征
    features = compute_features(
        df,
        product_id_col=product_id_col,
        date_col=date_col,
        sales_col=sales_col,
        price_col=price_col,
        category_col=category_col,
        window=window,
    )

    # 打分
    scores, breakdown = score_rules(features)

    # 对齐标签
    if label_col in df.columns:
        latest_labels = df.groupby(product_id_col)[label_col].last()
        features_idx = features.set_index(product_id_col)
        features_idx["score"] = scores.values
        features_idx[label_col] = latest_labels

        y_true = features_idx[label_col].astype(int).values
        y_score = features_idx["score"].values

        # AUC
        if len(np.unique(y_true)) > 1:
            auc = float(roc_auc_score(y_true, y_score))
        else:
            auc = 0.0

        # Top20 命中率
        n_top = min(20, len(y_true))
        top_indices = np.argsort(y_score)[::-1][:n_top]
        hit_rate = float(y_true[top_indices].mean())
    else:
        auc = 0.0
        hit_rate = 0.0

    result = {
        "auc": auc,
        "hit_rate_top20": hit_rate,
        "scores": scores,
        "breakdown": breakdown,
    }

    # 记录到 model_registry
    if engine is not None:
        _record_to_registry(
            engine,
            model_ver=model_ver,
            auc=auc,
            hit_rate=hit_rate,
            brier_score=None,
        )

    return result


def _record_to_registry(
    engine: Engine,
    model_ver: str,
    auc: float,
    hit_rate: float,
    brier_score: Optional[float] = None,
) -> None:
    """记录回测结果到 model_registry 表.

    Args:
        engine: 数据库引擎
        model_ver: 模型版本
        auc: AUC 值
        hit_rate: Top20 命中率
        brier_score: Brier 分数 (V1 可为 None)
    """
    today = date.today()
    window_start = today - timedelta(days=30)
    window_end = today + timedelta(days=28)

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO model_registry "
                "(model_ver, backtest_window_start, backtest_window_end, "
                "auc, hit_rate_top20, brier_score, random_state, is_adopted, note) "
                "VALUES (:ver, :ws, :we, :auc, :hr, :bs, 42, 0, :note)"
            ),
            {
                "ver": model_ver,
                "ws": str(window_start),
                "we": str(window_end),
                "auc": auc,
                "hr": hit_rate,
                "bs": brier_score,
                "note": "V1 规则版回测",
            },
        )
