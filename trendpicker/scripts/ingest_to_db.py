"""把已抓取的归一化 JSONL 数据落库到 SQLite (trendpicker.db).

执行强硬规则 (DATA_FETCH_POLICY.md):
    - 外部 API 数据必须立即落库
    - 落库前不做任何分析
    - 后续只读库分析

使用:
    cd /workspace/trendpicker
    python scripts/ingest_to_db.py

数据源 (已抓取的归一化 JSONL):
    data/normalized/xhs_notes.jsonl
    data/normalized/wechat_videos.jsonl
    data/normalized/douyin_ec.jsonl

入库后:
    - 表 raw_xhs_notes / raw_wechat_videos / raw_douyin_ec_items
    - 表 fetch_log (调用元数据)
    - 表 features (跨源特征工程, 供后续分析读取)
    - 表 product_predictions (爆款预测结果)
"""

from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
import statistics
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "normalized"
DB_PATH = PROJECT_ROOT / "trendpicker.db"

HOT_TOP_PERCENT = 5
HOT_MIN_DAYS = 5
HOT_WINDOW_DAYS = 14

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("trendpicker.ingest")


# ---- 中文 token 化（dedup 用，最简 bigram）----
_NON_TOKEN_RE = re.compile(r"[^\u4e00-\u9fa5a-zA-Z0-9]")
_CJK_RE = re.compile(r"[\u4e00-\u9fa5]")


def tokenize_zh(text: str) -> set[str]:
    if not text:
        return set()
    text = _NON_TOKEN_RE.sub("", text.lower())
    if not text:
        return set()
    cjk = _CJK_RE.findall(text)
    tokens: set[str] = set()
    for i in range(len(cjk) - 1):
        tokens.add(cjk[i] + cjk[i + 1])
    for c in cjk:
        tokens.add(c)
    for m in re.finditer(r"[a-zA-Z0-9]+", text):
        if len(m.group()) >= 2:
            tokens.add(m.group())
    return tokens


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


# ---- 特征工程辅助 ----
def _pct_rank(value: float, all_values: List[float]) -> float:
    if not all_values:
        return 0.0
    return sum(1 for v in all_values if v < value) / len(all_values)


def _trend_slope(sale_axis: List[Dict[str, Any]], window_days: int = 14) -> tuple[float, float, float]:
    if not sale_axis:
        return 0.0, 0.0, 0.0
    qtys = [float(p.get("qty") or 0) for p in sale_axis[-window_days:]]
    if len(qtys) < 2:
        return 0.0, qtys[-1] if qtys else 0.0, 0.0
    n = len(qtys)
    xs = list(range(n))
    mx, my = sum(xs) / n, sum(qtys) / n
    num = sum((xs[i] - mx) * (qtys[i] - my) for i in range(n))
    den = sum((xs[i] - mx) ** 2 for i in range(n))
    slope = num / den if den else 0.0
    r7 = sum(qtys[-7:]) / max(1, len(qtys[-7:]))
    p7_list = qtys[-14:-7] if len(qtys) >= 14 else qtys[:max(0, len(qtys) - 7)]
    p7 = sum(p7_list) / max(1, len(p7_list)) if p7_list else 0.0
    return slope, r7, p7


def _sustained_days(sale_axis: List[Dict[str, Any]]) -> int:
    if not sale_axis:
        return 0
    qtys = [float(p.get("qty") or 0) for p in sale_axis]
    if not qtys:
        return 0
    threshold = statistics.mean(qtys)
    days = 0
    for q in reversed(qtys):
        if q >= threshold:
            days += 1
        else:
            break
    return days


def _price_band(price: Any) -> str:
    try:
        p = float(price or 0)
    except (TypeError, ValueError):
        return "unknown"
    if p < 9.9:
        return "0-9.9"
    if p < 29.9:
        return "9.9-29.9"
    if p < 59.9:
        return "29.9-59.9"
    if p < 99.9:
        return "59.9-99.9"
    return "99.9+"


def _category_match_bonus(record: Dict[str, Any]) -> float:
    text = (record.get("title") or "") + " " + (record.get("desc") or "")
    if not text:
        return 0.0
    keywords = ["百货", "日用", "居家", "厨房", "生活用品", "家居"]
    return min(1.0, sum(1 for k in keywords if k in text) / 3.0)


# ---- Schema ----
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS fetch_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,            -- xhs / wechat / douyin
    endpoint TEXT,                  -- API endpoint
    keyword TEXT,                   -- 搜索关键词
    page INTEGER,                   -- 页码
    raw_response_file TEXT,         -- 原始响应文件路径
    fetched_at TEXT NOT NULL,       -- ISO8601 +08:00
    status TEXT,                   -- ok / error
    purpose TEXT DEFAULT 'ingest'  -- ingest / probe / detail_fetch
);

CREATE TABLE IF NOT EXISTS raw_xhs_notes (
    note_id TEXT PRIMARY KEY,
    search_keyword TEXT,
    title TEXT,
    desc TEXT,
    author_nickname TEXT,
    author_id TEXT,
    publish_time TEXT,
    liked_count INTEGER DEFAULT 0,
    collected_count INTEGER DEFAULT 0,
    comment_count INTEGER DEFAULT 0,
    share_count INTEGER DEFAULT 0,
    tag_list_json TEXT,             -- JSON array
    image_urls_json TEXT,           -- JSON array
    note_type TEXT,
    xsec_token TEXT,
    raw_response_file TEXT,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_wechat_videos (
    export_id TEXT PRIMARY KEY,
    search_keyword TEXT,
    doc_id TEXT,
    title TEXT,
    desc TEXT,
    author_nickname TEXT,
    author_username TEXT,
    publish_time TEXT,
    like_count INTEGER DEFAULT 0,
    comment_count INTEGER,
    share_count INTEGER,
    view_count INTEGER,
    duration INTEGER,
    cover_url TEXT,
    video_url TEXT,
    raw_response_file TEXT,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_douyin_ec_items (
    product_id TEXT PRIMARY KEY,
    promotion_id TEXT,
    search_keyword TEXT,
    title TEXT,
    main_image_url TEXT,
    detail_url TEXT,
    category_l1_name TEXT,
    category_l2_name TEXT,
    category_l3_name TEXT,
    leaf_layer INTEGER,
    price REAL,
    price_text TEXT,
    regular_price REAL,
    month_sale INTEGER DEFAULT 0,
    good_ratio REAL,
    commission_fee REAL,
    commission_ratio REAL,
    cooper_author_num INTEGER,
    shop_id TEXT,
    shop_name TEXT,
    shop_score INTEGER,
    tag_codes_json TEXT,
    tag_texts_json TEXT,
    recommend_reason_text TEXT,
    sale_axis_json TEXT,            -- 30 天逐日销量 JSON
    raw_response_file TEXT,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS features (
    record_id TEXT PRIMARY KEY,    -- source + ':' + id
    source TEXT NOT NULL,          -- xhs / wechat / douyin_ec
    source_id TEXT NOT NULL,       -- note_id / export_id / product_id
    search_keyword TEXT,
    title TEXT,
    category_l1_name TEXT,
    category_l2_name TEXT,
    category_l3_name TEXT,
    -- 抖音特征
    feat_month_sale_pct REAL,
    feat_good_ratio_pct REAL,
    feat_author_num_pct REAL,
    feat_shop_score_pct REAL,
    feat_commission_ratio_pct REAL,
    feat_trend_slope REAL,
    feat_recent_7d_avg REAL,
    feat_prior_7d_avg REAL,
    feat_momentum_ratio REAL,
    feat_sustained_days INTEGER,
    feat_has_ranking INTEGER,
    feat_price_band TEXT,
    -- 社媒特征
    feat_engagement REAL,
    feat_engagement_pct REAL,
    feat_collected_ratio REAL,
    -- dedup
    dedup_cluster_size INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS product_predictions (
    record_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    title TEXT,
    category_l1_name TEXT,
    category_l2_name TEXT,
    category_l3_name TEXT,
    is_hot INTEGER DEFAULT 0,             -- 过去爆款标签 (叶子类目前 5% × 持续 ≥ 5 天)
    past_hot_reason TEXT,                 -- 过去爆款原因
    predicted_hot INTEGER DEFAULT 0,       -- V1 规则预测: score >= 0.70 视为预期爆款
    prediction_reason TEXT,               -- 预测原因
    v1_score REAL,
    v1_contribs_json TEXT,                -- 规则贡献拆解 JSON
    -- 各平台数据表现（聚合该商品/笔记在多平台的表现）
    platform_perf_json TEXT
);
"""


def _now_iso() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not path.exists():
        return out
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError as e:
                    log.warning("%s JSON 解析失败: %s", path.name, e)
    return out


# ---- dedup within source ----
def _dedup_within(records: List[Dict[str, Any]], threshold: float = 0.85) -> List[Dict[str, Any]]:
    enriched = [{**r, "_toks": tokenize_zh(r.get("title") or "")} for r in records]
    clusters: List[List[int]] = []
    for i, r in enumerate(enriched):
        if not r["_toks"]:
            clusters.append([i]); continue
        best_cid, best_sim = -1, threshold
        for cid, members in enumerate(clusters):
            sim = jaccard(r["_toks"], enriched[members[0]]["_toks"])
            if sim > best_sim:
                best_sim, best_cid = sim, cid
        if best_cid == -1:
            clusters.append([i])
        else:
            clusters[best_cid].append(i)

    out: List[Dict[str, Any]] = []
    for members in clusters:
        if len(members) == 1:
            rep = dict(enriched[members[0]])
        else:
            def score(r: Dict[str, Any]) -> float:
                if r.get("source") == "douyin_ec":
                    return float(r.get("month_sale") or 0)
                return float(r.get("liked_count") or 0) + float(r.get("like_count") or 0)
            members.sort(key=lambda i: score(enriched[i]), reverse=True)
            rep = dict(enriched[members[0]])
            rep["dedup_cluster_size"] = len(members)
        rep.pop("_toks", None)
        out.append(rep)
    return out


# ---- V1 规则评分 ----
def _v1_rule_score(record: Dict[str, Any], is_hot: bool) -> tuple[float, Dict[str, float]]:
    contribs: Dict[str, float] = {}
    src = record.get("source")
    if src == "douyin_ec":
        mom_raw = record.get("feat_momentum_ratio", 0.0)
        mom = max(0.0, min(2.0, float(mom_raw))) / 2.0
        contribs = {
            "month_sale_pct": 0.30 * record.get("feat_month_sale_pct", 0.0),
            "momentum": 0.20 * mom,
            "good_ratio_pct": 0.10 * record.get("feat_good_ratio_pct", 0.0),
            "author_num_pct": 0.10 * record.get("feat_author_num_pct", 0.0),
            "shop_score_pct": 0.10 * record.get("feat_shop_score_pct", 0.0),
            "ranking_signal": 0.10 * record.get("feat_has_ranking", 0),
            "commission_pct": 0.10 * record.get("feat_commission_ratio_pct", 0.0),
        }
    else:
        eng_pct = record.get("feat_engagement_pct", 0.0)
        contribs = {
            "engagement_pct": 0.70 * eng_pct,
            "category_match": 0.30 * _category_match_bonus(record),
        }
    score = sum(contribs.values())
    if is_hot:
        score = min(1.0, score + 0.05)
    return score, contribs


# ---- 过去爆款标签 ----
def _build_hot_map(douyin_records: List[Dict[str, Any]]) -> Dict[str, bool]:
    by_leaf: Dict[str, List[Dict[str, Any]]] = {}
    for r in douyin_records:
        leaf = r.get("category_l3_name") or r.get("category_l2_name") or r.get("category_l1_name") or "未分类"
        by_leaf.setdefault(leaf, []).append(r)
    hot: Dict[str, bool] = {}
    for leaf, items in by_leaf.items():
        items_sorted = sorted(items, key=lambda x: float(x.get("month_sale") or 0), reverse=True)
        top_k = max(1, math.ceil(len(items_sorted) * HOT_TOP_PERCENT / 100))
        for r in items_sorted[:top_k]:
            if r.get("feat_sustained_days", 0) >= HOT_MIN_DAYS:
                hot[r["product_id"]] = True
    return hot


# ---- 爆款原因 / 预测原因生成 ----
def _past_hot_reason(record: Dict[str, Any]) -> str:
    leaf = record.get("category_l3_name") or record.get("category_l2_name") or "-"
    parts = []
    parts.append(f"叶子类目「{leaf}」月销前 5%")
    if record.get("recommend_reason_text"):
        parts.append(record["recommend_reason_text"])
    sd = record.get("feat_sustained_days", 0)
    parts.append(f"连续 {sd} 天达销量基线")
    mom = record.get("feat_momentum_ratio", 0.0)
    if mom >= 1.2:
        parts.append(f"末 7 天日均较前 7 天上升 {(mom-1)*100:.0f}%")
    elif mom < 0.9:
        parts.append(f"末 7 天日均较前 7 天下降 {(1-mom)*100:.0f}%")
    return " · ".join(parts)


def _prediction_reason(record: Dict[str, Any], score: float, contribs: Dict[str, float], is_hot: bool) -> str:
    src = record.get("source")
    top = sorted(contribs.items(), key=lambda x: -x[1])[:2]
    top_str = " + ".join(f"{k}({v:.3f})" for k, v in top)
    predicted_hot = score >= 0.70
    if src == "douyin_ec":
        if predicted_hot:
            return f"V1 规则总分 {score:.3f} ≥ 0.70 阈值；主贡献: {top_str}；{'叠加爆款加成 +0.05' if is_hot else '未触发爆款标签加成'}"
        else:
            return f"V1 规则总分 {score:.3f} < 0.70 阈值；主贡献: {top_str}；销量/动量综合未达爆款预测线"
    else:
        return f"社媒互动分位主导，V1 总分 {score:.3f}；主贡献: {top_str}；{'类目匹配 + 互动双高，预期为社媒爆款' if predicted_hot else '互动或类目命中不足'}"


# ---- 平台表现聚合 ----
def _platform_perf(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """对同一组记录聚合各平台的表现摘要。"""
    perf: Dict[str, Any] = {}
    douyin_recs = [r for r in records if r.get("source") == "douyin_ec"]
    xhs_recs = [r for r in records if r.get("source") == "xhs"]
    wc_recs = [r for r in records if r.get("source") == "wechat_channels"]

    if douyin_recs:
        perf["douyin_ec"] = {
            "count": len(douyin_recs),
            "total_month_sale": sum(int(r.get("month_sale") or 0) for r in douyin_recs),
            "avg_good_ratio": round(sum(float(r.get("good_ratio") or 0) for r in douyin_recs) / max(1, len(douyin_recs)), 2),
            "hot_count": sum(1 for r in douyin_recs if r.get("is_hot")),
            "top_leaf_categories": _top_leaves(douyin_recs, 3),
        }
    if xhs_recs:
        perf["xhs"] = {
            "count": len(xhs_recs),
            "total_engagement": sum(int(r.get("feat_engagement") or 0) for r in xhs_recs),
            "avg_engagement_pct": round(sum(float(r.get("feat_engagement_pct") or 0) for r in xhs_recs) / max(1, len(xhs_recs)), 3),
        }
    if wc_recs:
        perf["wechat_channels"] = {
            "count": len(wc_recs),
            "total_likes": sum(int(r.get("like_count") or 0) for r in wc_recs),
            "avg_engagement_pct": round(sum(float(r.get("feat_engagement_pct") or 0) for r in wc_recs) / max(1, len(wc_recs)), 3),
        }
    return perf


def _top_leaves(records: List[Dict[str, Any]], n: int) -> List[Dict[str, Any]]:
    from collections import Counter
    cnt = Counter(r.get("category_l3_name") or r.get("category_l2_name") or "-" for r in records)
    return [{"leaf": leaf, "count": c} for leaf, c in cnt.most_common(n)]


# ---- 主流程 ----
def main() -> int:
    log.info("=== ingest_to_db 启动 (遵守 DATA_FETCH_POLICY.md) ===")

    # 删除旧库重新建（demo 一次成型）
    if DB_PATH.exists():
        DB_PATH.unlink()
        log.info("已删除旧库 %s", DB_PATH.name)

    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA_SQL)
    log.info("已创建 schema")

    fetched_at = _now_iso()

    # 1. 加载 JSONL (这是落库动作本身, 不是分析)
    xhs_raw = _load_jsonl(DATA_DIR / "xhs_notes.jsonl")
    wc_raw = _load_jsonl(DATA_DIR / "wechat_videos.jsonl")
    dy_raw = _load_jsonl(DATA_DIR / "douyin_ec.jsonl")
    log.info("加载 JSONL: xhs=%d, wechat=%d, douyin=%d", len(xhs_raw), len(wc_raw), len(dy_raw))

    # 2. 落 raw 表 + fetch_log
    _insert_xhs(conn, xhs_raw, fetched_at)
    _insert_wechat(conn, wc_raw, fetched_at)
    _insert_douyin(conn, dy_raw, fetched_at)
    conn.commit()
    log.info("raw 表已落库")

    # 3. dedup
    xhs_dedup = _dedup_within(xhs_raw)
    wc_dedup = _dedup_within(wc_raw)
    dy_dedup = _dedup_within(dy_raw)
    log.info("dedup: xhs %d->%d, wc %d->%d, dy %d->%d",
             len(xhs_raw), len(xhs_dedup), len(wc_raw), len(wc_dedup), len(dy_raw), len(dy_dedup))

    # 4. 特征工程
    all_month_sales = [float(r.get("month_sale") or 0) for r in dy_dedup]
    all_good_ratios = [float(r.get("good_ratio") or 0) for r in dy_dedup]
    all_author_nums = [float(r.get("cooper_author_num") or 0) for r in dy_dedup]
    all_shop_scores = [float(r.get("shop_score") or 0) for r in dy_dedup]
    all_comm_ratios = [float(r.get("commission_ratio") or 0) for r in dy_dedup]

    all_xhs_eng = [float(r.get("liked_count") or 0) + float(r.get("collected_count") or 0) +
                   float(r.get("comment_count") or 0) + float(r.get("share_count") or 0) for r in xhs_dedup]
    all_wc_likes = [float(r.get("like_count") or 0) for r in wc_dedup]

    features_records: List[Dict[str, Any]] = []
    for r in dy_dedup:
        sale_axis = r.get("sale_axis") or []
        slope, r7, p7 = _trend_slope(sale_axis, HOT_WINDOW_DAYS)
        sustained = _sustained_days(sale_axis)
        momentum = (r7 / p7) if p7 > 0 else (1.0 if r7 > 0 else 0.0)
        rec = dict(r)
        rec.update({
            "feat_month_sale_pct": _pct_rank(float(r.get("month_sale") or 0), all_month_sales),
            "feat_good_ratio_pct": _pct_rank(float(r.get("good_ratio") or 0), all_good_ratios),
            "feat_author_num_pct": _pct_rank(float(r.get("cooper_author_num") or 0), all_author_nums),
            "feat_shop_score_pct": _pct_rank(float(r.get("shop_score") or 0), all_shop_scores),
            "feat_commission_ratio_pct": _pct_rank(float(r.get("commission_ratio") or 0), all_comm_ratios),
            "feat_trend_slope": slope,
            "feat_recent_7d_avg": r7,
            "feat_prior_7d_avg": p7,
            "feat_momentum_ratio": momentum,
            "feat_sustained_days": sustained,
            "feat_has_ranking": 1 if r.get("recommend_reason_text") else 0,
            "feat_price_band": _price_band(r.get("price")),
        })
        features_records.append(("douyin_ec", rec))

    for r in xhs_dedup:
        eng = (float(r.get("liked_count") or 0) + float(r.get("collected_count") or 0) +
               float(r.get("comment_count") or 0) + float(r.get("share_count") or 0))
        rec = dict(r)
        rec.update({
            "feat_engagement": eng,
            "feat_engagement_pct": _pct_rank(eng, all_xhs_eng),
            "feat_collected_ratio": (float(r.get("collected_count") or 0) / float(r.get("liked_count") or 1)) if r.get("liked_count") else 0.0,
        })
        features_records.append(("xhs", rec))

    for r in wc_dedup:
        likes = float(r.get("like_count") or 0)
        rec = dict(r)
        rec.update({
            "feat_engagement": likes,
            "feat_engagement_pct": _pct_rank(likes, all_wc_likes),
        })
        features_records.append(("wechat_channels", rec))

    # 5. 落 features 表
    _insert_features(conn, features_records, fetched_at)
    conn.commit()
    log.info("features 表已落库: %d 条", len(features_records))

    # 6. 过去爆款标签 + V1 预测
    douyin_recs = [r for src, r in features_records if src == "douyin_ec"]
    hot_map = _build_hot_map(douyin_recs)

    all_recs_for_perf = [r for _, r in features_records]
    perf = _platform_perf(all_recs_for_perf)

    predictions: List[Dict[str, Any]] = []
    for src, rec in features_records:
        rid = f"{src}:{rec.get('product_id') or rec.get('note_id') or rec.get('export_id')}"
        is_hot = bool(hot_map.get(rec.get("product_id"))) if src == "douyin_ec" else False
        score, contribs = _v1_rule_score(rec, is_hot)
        predicted_hot = 1 if score >= 0.70 else 0
        past_reason = _past_hot_reason(rec) if (src == "douyin_ec" and is_hot) else (
            "未达爆款门槛（叶子类目前 5% 且持续 ≥ 5 天）" if src == "douyin_ec" else
            "社媒源无销量数据，未参与爆款标签构造"
        )
        pred_reason = _prediction_reason(rec, score, contribs, is_hot)
        predictions.append({
            "record_id": rid,
            "source": src,
            "source_id": rec.get("product_id") or rec.get("note_id") or rec.get("export_id"),
            "title": rec.get("title"),
            "category_l1_name": rec.get("category_l1_name"),
            "category_l2_name": rec.get("category_l2_name"),
            "category_l3_name": rec.get("category_l3_name"),
            "is_hot": int(is_hot),
            "past_hot_reason": past_reason,
            "predicted_hot": predicted_hot,
            "prediction_reason": pred_reason,
            "v1_score": score,
            "v1_contribs_json": json.dumps(contribs, ensure_ascii=False),
            "platform_perf_json": json.dumps(perf, ensure_ascii=False),
        })

    _insert_predictions(conn, predictions)
    conn.commit()
    log.info("product_predictions 表已落库: %d 条（其中预期爆款 %d 条）",
             len(predictions), sum(1 for p in predictions if p["predicted_hot"]))

    # 7. 写 fetch_log（把已有抓取动作登记）
    _insert_fetch_log(conn, fetched_at)
    conn.commit()

    # 验证
    cur = conn.execute("SELECT COUNT(*) FROM raw_xhs_notes")
    xhs_n = cur.fetchone()[0]
    cur = conn.execute("SELECT COUNT(*) FROM raw_wechat_videos")
    wc_n = cur.fetchone()[0]
    cur = conn.execute("SELECT COUNT(*) FROM raw_douyin_ec_items")
    dy_n = cur.fetchone()[0]
    cur = conn.execute("SELECT COUNT(*) FROM features")
    fe_n = cur.fetchone()[0]
    cur = conn.execute("SELECT COUNT(*) FROM product_predictions WHERE predicted_hot=1")
    pred_n = cur.fetchone()[0]
    cur = conn.execute("SELECT COUNT(*) FROM product_predictions WHERE is_hot=1")
    past_n = cur.fetchone()[0]
    log.info("=== 落库完成 ===")
    log.info("raw_xhs_notes=%d, raw_wechat_videos=%d, raw_douyin_ec_items=%d", xhs_n, wc_n, dy_n)
    log.info("features=%d, 过去爆款=%d, 预期爆款=%d", fe_n, past_n, pred_n)
    log.info("DB 路径: %s", DB_PATH)
    conn.close()
    return 0


def _insert_xhs(conn: sqlite3.Connection, records: List[Dict[str, Any]], fetched_at: str) -> None:
    cols = ("note_id", "search_keyword", "title", "desc", "author_nickname", "author_id",
            "publish_time", "liked_count", "collected_count", "comment_count", "share_count",
            "tag_list_json", "image_urls_json", "note_type", "xsec_token",
            "raw_response_file", "fetched_at")
    placeholders = ",".join(["?"] * len(cols))
    sql = f"INSERT OR REPLACE INTO raw_xhs_notes ({','.join(cols)}) VALUES ({placeholders})"
    for r in records:
        vals = (
            r.get("note_id"), r.get("search_keyword"), r.get("title"), r.get("desc"),
            r.get("author_nickname"), r.get("author_id"), r.get("publish_time"),
            int(r.get("liked_count") or 0), int(r.get("collected_count") or 0),
            int(r.get("comment_count") or 0), int(r.get("share_count") or 0),
            json.dumps(r.get("tag_list") or [], ensure_ascii=False),
            json.dumps(r.get("image_urls") or [], ensure_ascii=False),
            r.get("note_type"), r.get("xsec_token"), r.get("raw_response_file"), fetched_at,
        )
        conn.execute(sql, vals)


def _insert_wechat(conn: sqlite3.Connection, records: List[Dict[str, Any]], fetched_at: str) -> None:
    cols = ("export_id", "search_keyword", "doc_id", "title", "desc", "author_nickname",
            "author_username", "publish_time", "like_count", "comment_count", "share_count",
            "view_count", "duration", "cover_url", "video_url", "raw_response_file", "fetched_at")
    placeholders = ",".join(["?"] * len(cols))
    sql = f"INSERT OR REPLACE INTO raw_wechat_videos ({','.join(cols)}) VALUES ({placeholders})"
    for r in records:
        vals = (
            r.get("export_id"), r.get("search_keyword"), r.get("doc_id"), r.get("title"), r.get("desc"),
            r.get("author_nickname"), r.get("author_username"), r.get("publish_time"),
            int(r.get("like_count") or 0), r.get("comment_count"), r.get("share_count"),
            r.get("view_count"), r.get("duration"), r.get("cover_url"), r.get("video_url"),
            r.get("raw_response_file"), fetched_at,
        )
        conn.execute(sql, vals)


def _insert_douyin(conn: sqlite3.Connection, records: List[Dict[str, Any]], fetched_at: str) -> None:
    cols = ("product_id", "promotion_id", "search_keyword", "title", "main_image_url", "detail_url",
            "category_l1_name", "category_l2_name", "category_l3_name", "leaf_layer",
            "price", "price_text", "regular_price", "month_sale", "good_ratio",
            "commission_fee", "commission_ratio", "cooper_author_num", "shop_id", "shop_name",
            "shop_score", "tag_codes_json", "tag_texts_json", "recommend_reason_text",
            "sale_axis_json", "raw_response_file", "fetched_at")
    placeholders = ",".join(["?"] * len(cols))
    sql = f"INSERT OR REPLACE INTO raw_douyin_ec_items ({','.join(cols)}) VALUES ({placeholders})"
    for r in records:
        vals = (
            r.get("product_id"), r.get("promotion_id"), r.get("search_keyword"), r.get("title"),
            r.get("main_image_url"), r.get("detail_url"), r.get("category_l1_name"),
            r.get("category_l2_name"), r.get("category_l3_name"), r.get("leaf_layer"),
            r.get("price"), r.get("price_text"), r.get("regular_price"),
            int(r.get("month_sale") or 0), r.get("good_ratio"), r.get("commission_fee"),
            r.get("commission_ratio"), r.get("cooper_author_num"), r.get("shop_id"),
            r.get("shop_name"), r.get("shop_score"),
            json.dumps(r.get("tag_codes") or [], ensure_ascii=False),
            json.dumps(r.get("tag_texts") or [], ensure_ascii=False),
            r.get("recommend_reason_text"),
            json.dumps(r.get("sale_axis") or [], ensure_ascii=False),
            r.get("raw_response_file"), fetched_at,
        )
        conn.execute(sql, vals)


def _insert_features(conn: sqlite3.Connection, records: List[tuple[str, Dict[str, Any]]], fetched_at: str) -> None:
    cols = ("record_id", "source", "source_id", "search_keyword", "title",
            "category_l1_name", "category_l2_name", "category_l3_name",
            "feat_month_sale_pct", "feat_good_ratio_pct", "feat_author_num_pct", "feat_shop_score_pct",
            "feat_commission_ratio_pct", "feat_trend_slope", "feat_recent_7d_avg", "feat_prior_7d_avg",
            "feat_momentum_ratio", "feat_sustained_days", "feat_has_ranking", "feat_price_band",
            "feat_engagement", "feat_engagement_pct", "feat_collected_ratio", "dedup_cluster_size")
    placeholders = ",".join(["?"] * len(cols))
    sql = f"INSERT OR REPLACE INTO features ({','.join(cols)}) VALUES ({placeholders})"
    for src, r in records:
        rid = f"{src}:{r.get('product_id') or r.get('note_id') or r.get('export_id')}"
        sid = r.get("product_id") or r.get("note_id") or r.get("export_id")
        vals = (
            rid, src, sid, r.get("search_keyword"), r.get("title"),
            r.get("category_l1_name"), r.get("category_l2_name"), r.get("category_l3_name"),
            r.get("feat_month_sale_pct"), r.get("feat_good_ratio_pct"), r.get("feat_author_num_pct"),
            r.get("feat_shop_score_pct"), r.get("feat_commission_ratio_pct"),
            r.get("feat_trend_slope"), r.get("feat_recent_7d_avg"), r.get("feat_prior_7d_avg"),
            r.get("feat_momentum_ratio"), r.get("feat_sustained_days"), r.get("feat_has_ranking"),
            r.get("feat_price_band"), r.get("feat_engagement"), r.get("feat_engagement_pct"),
            r.get("feat_collected_ratio"), r.get("dedup_cluster_size", 1),
        )
        conn.execute(sql, vals)


def _insert_predictions(conn: sqlite3.Connection, predictions: List[Dict[str, Any]]) -> None:
    cols = ("record_id", "source", "source_id", "title", "category_l1_name", "category_l2_name",
            "category_l3_name", "is_hot", "past_hot_reason", "predicted_hot", "prediction_reason",
            "v1_score", "v1_contribs_json", "platform_perf_json")
    placeholders = ",".join(["?"] * len(cols))
    sql = f"INSERT OR REPLACE INTO product_predictions ({','.join(cols)}) VALUES ({placeholders})"
    for p in predictions:
        vals = (
            p["record_id"], p["source"], p["source_id"], p["title"], p["category_l1_name"],
            p["category_l2_name"], p["category_l3_name"], p["is_hot"], p["past_hot_reason"],
            p["predicted_hot"], p["prediction_reason"], p["v1_score"], p["v1_contribs_json"],
            p["platform_perf_json"],
        )
        conn.execute(sql, vals)


def _insert_fetch_log(conn: sqlite3.Connection, fetched_at: str) -> None:
    # 把已抓取的 JSONL 反推 fetch_log（每个 raw_response_file 一行）
    cur = conn.execute("SELECT DISTINCT search_keyword, raw_response_file FROM raw_xhs_notes WHERE raw_response_file IS NOT NULL")
    for kw, fp in cur.fetchall():
        conn.execute(
            "INSERT INTO fetch_log (source, endpoint, keyword, page, raw_response_file, fetched_at, status, purpose) VALUES (?,?,?,?,?,?,?,?)",
            ("xhs", "tikhub:/xiaohongshu/app_v2/search_notes", kw, 1, fp, fetched_at, "ok", "ingest")
        )
    cur = conn.execute("SELECT DISTINCT search_keyword, raw_response_file FROM raw_wechat_videos WHERE raw_response_file IS NOT NULL")
    for kw, fp in cur.fetchall():
        conn.execute(
            "INSERT INTO fetch_log (source, endpoint, keyword, page, raw_response_file, fetched_at, status, purpose) VALUES (?,?,?,?,?,?,?,?)",
            ("wechat", "tikhub:/wechat_search/v2/fetch_search", kw, 1, fp, fetched_at, "ok", "ingest")
        )
    cur = conn.execute("SELECT DISTINCT search_keyword, raw_response_file FROM raw_douyin_ec_items WHERE raw_response_file IS NOT NULL")
    for kw, fp in cur.fetchall():
        conn.execute(
            "INSERT INTO fetch_log (source, endpoint, keyword, page, raw_response_file, fetched_at, status, purpose) VALUES (?,?,?,?,?,?,?,?)",
            ("douyin", "justoneapi:/douyin-ec/search-item-list/v1", kw, 1, fp, fetched_at, "ok", "ingest")
        )


if __name__ == "__main__":
    raise SystemExit(main())
