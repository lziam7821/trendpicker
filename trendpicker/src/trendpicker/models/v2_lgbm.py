"""V2 LightGBM 模型.

特点:
    - 固定随机种子: random_state=42 (保证可复现)
    - Isotonic Regression 校准概率输出
    - 与 V1 A/B 对比 (切换门槛: AUC ≥ 0.75 且命中率提升 ≥ 5pp)
"""

import logging
from datetime import date, timedelta
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import brier_score_loss, roc_auc_score

from ..features import min_max_normalize, three_sigma_clip

logger = logging.getLogger(__name__)

RANDOM_STATE = 42


def train(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    X_val: Optional[pd.DataFrame] = None,
    y_val: Optional[pd.Series] = None,
    params: Optional[dict] = None,
) -> dict:
    """训练 LightGBM 模型 + Isotonic 校准.

    固定 random_state=42 保证可复现.

    Args:
        X_train: 训练特征
        y_train: 训练标签
        X_val: 验证特征 (用于校准)
        y_val: 验证标签 (用于校准)
        params: LightGBM 参数

    Returns:
        dict: {"model", "calibrator", "features"}
    """
    import lightgbm as lgb

    default_params = {
        "objective": "binary",
        "metric": "auc",
        "learning_rate": 0.05,
        "num_leaves": 31,
        "max_depth": 6,
        "min_child_samples": 10,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 5,
        "random_state": RANDOM_STATE,
        "verbose": -1,
        "force_col_wise": True,
    }

    if params:
        default_params.update(params)
        # 始终强制 random_state
        default_params["random_state"] = RANDOM_STATE

    train_data = lgb.Dataset(X_train, label=y_train)

    valid_sets = [train_data]
    if X_val is not None and y_val is not None:
        valid_data = lgb.Dataset(X_val, label=y_val, reference=train_data)
        valid_sets.append(valid_data)

    model = lgb.train(
        default_params,
        train_data,
        num_boost_round=200,
        valid_sets=valid_sets,
        callbacks=[lgb.log_evaluation(50)] if len(valid_sets) > 1 else [],
    )

    # Isotonic 校准 (使用验证集)
    calibrator = None
    if X_val is not None and y_val is not None:
        val_preds = model.predict(X_val)
        calibrator = IsotonicRegression(out_of_bounds="clip")
        calibrator.fit(val_preds, y_val)

    return {
        "model": model,
        "calibrator": calibrator,
        "features": list(X_train.columns),
    }


def predict(model_dict: dict, X: pd.DataFrame) -> np.ndarray:
    """使用训练好的模型预测 (含 Isotonic 校准).

    Args:
        model_dict: train() 返回的 dict
        X: 特征 DataFrame

    Returns:
        预测概率 np.ndarray ([0, 1])
    """
    model = model_dict["model"]
    raw_preds = model.predict(X)

    # Isotonic 校准
    calibrator = model_dict.get("calibrator")
    if calibrator is not None:
        preds = calibrator.transform(raw_preds)
    else:
        preds = raw_preds

    # 截断到 [0, 1]
    preds = np.clip(preds, 0.0, 1.0)

    return preds


def backtest(
    df: pd.DataFrame,
    label_col: str = "is_hot",
    feature_cols: Optional[list] = None,
    product_id_col: str = "product_id",
    engine: Optional[Engine] = None,
    model_ver: str = "v2_lgbm_v1",
    lookback_days: int = 30,
    predict_horizon: int = 14,
) -> Dict:
    """V2 LightGBM 回测.

    Args:
        df: 商品级数据 (需含 label_col 和特征列)
        label_col: 标签列
        feature_cols: 特征列列表, 默认自动选择数值列
        product_id_col: 商品ID列
        engine: 数据库引擎
        model_ver: 模型版本名
        lookback_days: 回看天数
        predict_horizon: 预测天数

    Returns:
        回测结果 dict: {"auc", "hit_rate_top20", "brier_score", "model_dict"}
    """
    if df.empty:
        return {
            "auc": 0.0,
            "hit_rate_top20": 0.0,
            "brier_score": 1.0,
            "model_dict": None,
        }

    # 自动选择特征列
    if feature_cols is None:
        exclude_cols = {label_col, product_id_col, "date", "category_id"}
        feature_cols = [
            c for c in df.columns
            if c not in exclude_cols and df[c].dtype in [np.float64, np.int64, float, int]
        ]

    if len(feature_cols) == 0:
        return {
            "auc": 0.0,
            "hit_rate_top20": 0.0,
            "brier_score": 1.0,
            "model_dict": None,
        }

    # 填充 NaN
    X = df[feature_cols].fillna(0)
    y = df[label_col].astype(int)

    # 分割训练/验证 (按时间后50%作验证)
    split_idx = len(df) // 2
    X_train, X_val = X.iloc[:split_idx], X.iloc[split_idx:]
    y_train, y_val = y.iloc[:split_idx], y.iloc[split_idx:]

    if len(np.unique(y_train)) < 2 or len(np.unique(y_val)) < 2:
        # 退化场景: 单一类别
        return {
            "auc": 0.5,
            "hit_rate_top20": 0.0,
            "brier_score": 1.0,
            "model_dict": None,
        }

    # 训练
    model_dict = train(X_train, y_train, X_val, y_val)

    # 预测 (验证集)
    preds = predict(model_dict, X_val)

    # 指标
    auc = float(roc_auc_score(y_val, preds))
    brier = float(brier_score_loss(y_val, preds))

    # Top20 命中率
    n_top = min(20, len(y_val))
    top_indices = np.argsort(preds)[::-1][:n_top]
    hit_rate = float(y_val.iloc[top_indices].values.mean())

    result = {
        "auc": auc,
        "hit_rate_top20": hit_rate,
        "brier_score": brier,
        "model_dict": model_dict,
    }

    # 记录到 model_registry
    if engine is not None:
        _record_to_registry(
            engine,
            model_ver=model_ver,
            auc=auc,
            hit_rate=hit_rate,
            brier_score=brier,
        )

    return result


def _record_to_registry(
    engine: Engine,
    model_ver: str,
    auc: float,
    hit_rate: float,
    brier_score: float,
) -> None:
    """记录回测结果到 model_registry 表."""
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
                "note": "V2 LightGBM 回测, random_state=42",
            },
        )


def ab_compare(
    v1_result: Dict,
    v2_result: Dict,
    engine: Optional[Engine] = None,
    same_window: bool = True,
) -> Dict:
    """V1 vs V2 A/B 对比.

    切换门槛:
        V2 AUC ≥ 0.75 且命中率较 V1 提升 ≥ 5 个百分点

    Args:
        v1_result: V1 回测结果 dict
        v2_result: V2 回测结果 dict
        engine: 数据库引擎 (查询 model_registry 验证同窗口)
        same_window: 是否要求同一历史窗口

    Returns:
        dict: {"v1_auc", "v2_auc", "v1_hit_rate", "v2_hit_rate",
               "auc_delta", "hit_rate_delta_pp", "switch_decision"}
    """
    v1_auc = v1_result.get("auc", 0.0)
    v2_auc = v2_result.get("auc", 0.0)
    v1_hit = v1_result.get("hit_rate_top20", 0.0)
    v2_hit = v2_result.get("hit_rate_top20", 0.0)

    auc_delta = v2_auc - v1_auc
    hit_rate_delta_pp = (v2_hit - v1_hit) * 100

    # 切换决策
    if v2_auc >= 0.75 and hit_rate_delta_pp >= 5:
        decision = "PASS"
    else:
        decision = "FAIL"

    # 同窗口验证 (查询 model_registry)
    if same_window and engine is not None:
        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT * FROM v_ab_comparison LIMIT 1")
            )
            row = result.fetchone()
            if row:
                decision = dict(zip(result.keys(), row))["switch_decision"]

    return {
        "v1_auc": v1_auc,
        "v2_auc": v2_auc,
        "v1_hit_rate": v1_hit,
        "v2_hit_rate": v2_hit,
        "auc_delta": auc_delta,
        "hit_rate_delta_pp": hit_rate_delta_pp,
        "switch_decision": decision,
    }
