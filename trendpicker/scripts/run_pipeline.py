"""TrendPicker 完整流程端到端运行器（Phase 1 demo）.

读取 3 个数据源的归一化 JSONL（小红书 / 微信视频号 / 抖音电商），
跑通 ingestion → dedup → features → label → v1_rules 完整链路，
产出 Top-20 选品榜单与趋势报告到 reports/。

使用:
    cd /workspace/trendpicker
    python scripts/run_pipeline.py

数据来源:
    - TikHub API（小红书 search_notes / 微信视频号 wechat_search_v2）
    - JustOneAPI（抖音电商 search-item-list/v1，含 30 天 sale_axis）

爆款定义（与方案一致，见 tests/conftest.py hot_product_definition）:
    - top_percent: 5
    - min_days: 5
    - window_days: 14

注: 本脚本不修改 trendpicker 现有 src/credentials.py；
   数据已由并行子 agent 抓取落盘，本步骤只读 JSONL 不再调 API。
"""

from __future__ import annotations

import json
import logging
import math
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---- 配置 ----
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "normalized"
REPORTS_DIR = PROJECT_ROOT / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# 爆款定义（与 tests/conftest.py 的 hot_product_definition 一致）
HOT_TOP_PERCENT = 5        # 叶子类目前 5%
HOT_MIN_DAYS = 5           # 持续 ≥ 5 天
HOT_WINDOW_DAYS = 14       # 14 天观察窗口

# 抖音 sale_axis 覆盖 2026-08-23 ~ 2026-09-21 共 30 天；
# 取末段 14 天作为观察窗口（与方案 window_days=14 对齐）
WINDOW_END_DATE = "20260921"
WINDOW_START_DATE = "20260908"   # 14 天窗口起点

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("trendpicker.pipeline")


# ---- 1. ingestion: 加载归一化数据 ----

def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    """加载 JSONL 文件为字典列表。"""
    if not path.exists():
        log.warning("数据文件不存在: %s", path)
        return []
    out: List[Dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as e:
                log.warning("%s:%d JSON 解析失败: %s", path.name, line_no, e)
    log.info("已加载 %d 条记录 <- %s", len(out), path.name)
    return out


def ingest() -> Dict[str, List[Dict[str, Any]]]:
    """从 data/normalized/ 加载三个数据源。"""
    return {
        "xhs": load_jsonl(DATA_DIR / "xhs_notes.jsonl"),
        "wechat": load_jsonl(DATA_DIR / "wechat_videos.jsonl"),
        "douyin": load_jsonl(DATA_DIR / "douyin_ec.jsonl"),
    }


# ---- 2. dedup: 标题 Jaccard 相似度聚合 ----

_CJK_RE = re.compile(r"[\u4e00-\u9fa5]")
_NON_TOKEN_RE = re.compile(r"[^\u4e00-\u9fa5a-zA-Z0-9]")


def tokenize_zh(text: str) -> set[str]:
    """简易中文分词（按字切 + 英文/数字按 token）。

    项目里 dedup.py 未实现，这里用最简版（bigram + 字符级）做 demo。
    生产应换 jieba/hanlp。
    """
    if not text:
        return set()
    text = _NON_TOKEN_RE.sub("", text.lower())
    if not text:
        return set()
    # 中文按 bigram
    cjk_chars = _CJK_RE.findall(text)
    tokens: set[str] = set()
    for i in range(len(cjk_chars) - 1):
        tokens.add(cjk_chars[i] + cjk_chars[i + 1])
    # 单字也加入
    for c in cjk_chars:
        tokens.add(c)
    # 英文/数字连续段
    for m in re.finditer(r"[a-zA-Z0-9]+", text):
        if len(m.group()) >= 2:
            tokens.add(m.group())
    return tokens


def jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard 相似度。"""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def dedup_within(records: List[Dict[str, Any]], threshold: float = 0.85) -> List[Dict[str, Any]]:
    """同源内按标题 Jaccard 聚合，保留互动/销量最高者作为代表。"""
    enriched = []
    for r in records:
        toks = tokenize_zh(r.get("title", ""))
        r["_title_tokens"] = toks
        enriched.append(r)

    clusters: List[List[int]] = []
    assigned: Dict[int, int] = {}
    for i, r in enumerate(enriched):
        if not r["_title_tokens"]:
            clusters.append([i]); assigned[i] = len(clusters) - 1; continue
        best_cid, best_sim = -1, threshold
        for cid, members in enumerate(clusters):
            rep = enriched[members[0]]
            sim = jaccard(r["_title_tokens"], rep["_title_tokens"])
            if sim > best_sim:
                best_sim, best_cid = sim, cid
        if best_cid == -1:
            clusters.append([i]); assigned[i] = len(clusters) - 1
        else:
            clusters[best_cid].append(i); assigned[i] = best_cid

    deduped: List[Dict[str, Any]] = []
    for members in clusters:
        if len(members) == 1:
            rep = dict(enriched[members[0]])
        else:
            # 选互动/销量最高者
            def score(r: Dict[str, Any]) -> float:
                if r.get("source") == "douyin_ec":
                    return float(r.get("month_sale") or 0)
                return float(r.get("liked_count") or 0) + float(r.get("like_count") or 0)
            members.sort(key=lambda i: score(enriched[i]), reverse=True)
            rep = dict(enriched[members[0]])
            rep["dedup_cluster_size"] = len(members)
        rep.pop("_title_tokens", None)
        deduped.append(rep)

    removed = len(records) - len(deduped)
    log.info("dedup: %d -> %d（聚合 %d 条同款）", len(records), len(deduped), removed)
    return deduped


# ---- 3. features: 特征工程 ----

def _pct_rank(value: float, all_values: List[float]) -> float:
    """百分位排名 [0,1]。"""
    if not all_values:
        return 0.0
    below = sum(1 for v in all_values if v < value)
    return below / len(all_values)


def _trend_slope(sale_axis: List[Dict[str, Any]], window_days: int = 14) -> Tuple[float, float, float]:
    """计算 14 天观察窗口内的销量趋势。

    Returns:
        (slope, recent_7d_avg, prior_7d_avg)
        slope: 线性回归斜率（>0 上升）
        recent_7d_avg / prior_7d_avg: 末 7 天均 vs 前 7 天均，比值 >1 表示加速
    """
    if not sale_axis:
        return 0.0, 0.0, 0.0
    # 取 window_days 末段
    series = sale_axis[-window_days:]
    qtys = [float(p.get("qty") or 0) for p in series]
    if len(qtys) < 2:
        return 0.0, qtys[-1] if qtys else 0.0, 0.0
    # 线性回归斜率（最小二乘）
    n = len(qtys)
    xs = list(range(n))
    mean_x, mean_y = sum(xs) / n, sum(qtys) / n
    num = sum((xs[i] - mean_x) * (qtys[i] - mean_y) for i in range(n))
    den = sum((xs[i] - mean_x) ** 2 for i in range(n))
    slope = num / den if den else 0.0
    # 末 7 vs 前 7
    recent = qtys[-7:]
    prior = qtys[-14:-7] if len(qtys) >= 14 else qtys[:len(qtys) - 7]
    r_avg = sum(recent) / max(1, len(recent))
    p_avg = sum(prior) / max(1, len(prior))
    return slope, r_avg, p_avg


def _sustained_days(sale_axis: List[Dict[str, Any]]) -> int:
    """计算末段连续 ≥ 7 天平均日销的"持续天数"（用于爆款 min_days 判定）。"""
    if not sale_axis:
        return 0
    qtys = [float(p.get("qty") or 0) for p in sale_axis]
    if not qtys:
        return 0
    threshold = statistics.mean(qtys)  # 用整月均作为基线
    days = 0
    for q in reversed(qtys):
        if q >= threshold:
            days += 1
        else:
            break
    return days


def build_features(douyin: List[Dict[str, Any]],
                   xhs: List[Dict[str, Any]],
                   wechat: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """跨源特征工程。返回统一的 record 列表（保留 source 字段）。"""
    records: List[Dict[str, Any]] = []

    # 3.1 抖音电商特征
    all_month_sales = [float(r.get("month_sale") or 0) for r in douyin]
    all_good_ratios = [float(r.get("good_ratio") or 0) for r in douyin]
    all_author_nums = [float(r.get("cooper_author_num") or 0) for r in douyin]
    all_shop_scores = [float(r.get("shop_score") or 0) for r in douyin]
    all_commission_ratios = [float(r.get("commission_ratio") or 0) for r in douyin]

    for r in douyin:
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
            "feat_commission_ratio_pct": _pct_rank(float(r.get("commission_ratio") or 0), all_commission_ratios),
            "feat_trend_slope": slope,
            "feat_recent_7d_avg": r7,
            "feat_prior_7d_avg": p7,
            "feat_momentum_ratio": momentum,
            "feat_sustained_days": sustained,
            "feat_has_ranking": 1 if r.get("recommend_reason_text") else 0,
            "feat_price_band": _price_band(r.get("price")),
        })
        records.append(rec)

    # 3.2 小红书笔记特征（互动分位）
    all_xhs_eng = [float(r.get("liked_count") or 0) +
                   float(r.get("collected_count") or 0) +
                   float(r.get("comment_count") or 0) +
                   float(r.get("share_count") or 0) for r in xhs]
    for r in xhs:
        eng = (float(r.get("liked_count") or 0) +
               float(r.get("collected_count") or 0) +
               float(r.get("comment_count") or 0) +
               float(r.get("share_count") or 0))
        rec = dict(r)
        rec.update({
            "feat_engagement": eng,
            "feat_engagement_pct": _pct_rank(eng, all_xhs_eng),
            "feat_collected_ratio": (
                (float(r.get("collected_count") or 0) / float(r.get("liked_count") or 1))
                if r.get("liked_count") else 0.0
            ),
        })
        records.append(rec)

    # 3.3 微信视频号特征（点赞分位）
    all_wc_likes = [float(r.get("like_count") or 0) for r in wechat]
    for r in wechat:
        likes = float(r.get("like_count") or 0)
        rec = dict(r)
        rec.update({
            "feat_engagement": likes,
            "feat_engagement_pct": _pct_rank(likes, all_wc_likes),
        })
        records.append(rec)

    log.info("features: 共 %d 条统一记录（douyin=%d, xhs=%d, wechat=%d）",
             len(records), len(douyin), len(xhs), len(wechat))
    return records


def _price_band(price: Any) -> str:
    """价格带分桶（与 .env.example TRENDPICKER_PRICE_BAND_* 对齐）。"""
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


# ---- 4. label: 爆款标签构造 ----

def build_labels(douyin_records: List[Dict[str, Any]]) -> Dict[str, bool]:
    """构造爆款标签：叶子类目内月销量前 5% 且持续天数 ≥ 5。

    Returns:
        product_id -> is_hot (bool)
    """
    # 按叶子类目分组
    by_leaf: Dict[str, List[Dict[str, Any]]] = {}
    for r in douyin_records:
        leaf = r.get("category_l3_name") or r.get("category_l2_name") or r.get("category_l1_name") or "未分类"
        by_leaf.setdefault(leaf, []).append(r)

    hot_map: Dict[str, bool] = {}
    n_hot_total = 0
    n_leaf_with_hot = 0
    for leaf, items in by_leaf.items():
        items_sorted = sorted(items, key=lambda x: float(x.get("month_sale") or 0), reverse=True)
        top_k = max(1, math.ceil(len(items_sorted) * HOT_TOP_PERCENT / 100))
        top_items = items_sorted[:top_k]
        leaf_hot = 0
        for r in top_items:
            if r.get("feat_sustained_days", 0) >= HOT_MIN_DAYS:
                hot_map[r["product_id"]] = True
                leaf_hot += 1
        if leaf_hot:
            n_leaf_with_hot += 1
            n_hot_total += leaf_hot
    log.info("label: 在 %d 个叶子类目中标记 %d 个爆款（覆盖 %d 个叶子类目）",
             len(by_leaf), n_hot_total, n_leaf_with_hot)
    return hot_map


# ---- 5. v1_rules: 规则版评分 ----

def v1_rule_score(record: Dict[str, Any], is_hot: bool) -> Dict[str, Any]:
    """V1 规则版打分（可解释）。

    规则权重（每项 0~1 子分，加权和落 [0,1]）：
        - 销量分位     0.30
        - 趋势动量     0.20   （末7 / 前7，clip 到 [0,2] 再 /2）
        - 好评分位     0.10
        - 合作达人数分位 0.10
        - 店铺评分分位 0.10
        - 榜单信号     0.10   （有榜单原因 = 1，否则 0）
        - 佣金分位     0.10
    社媒源（小红书 / 微信视频号）只有互动分位 + 类目命中"百货"关键词加分。
    """
    contribs: Dict[str, float] = {}
    src = record.get("source")
    if src == "douyin_ec":
        momentum_raw = record.get("feat_momentum_ratio", 0.0)
        momentum = max(0.0, min(2.0, float(momentum_raw))) / 2.0
        contribs = {
            "month_sale_pct":   0.30 * record.get("feat_month_sale_pct", 0.0),
            "momentum":         0.20 * momentum,
            "good_ratio_pct":   0.10 * record.get("feat_good_ratio_pct", 0.0),
            "author_num_pct":   0.10 * record.get("feat_author_num_pct", 0.0),
            "shop_score_pct":   0.10 * record.get("feat_shop_score_pct", 0.0),
            "ranking_signal":   0.10 * record.get("feat_has_ranking", 0),
            "commission_pct":   0.10 * record.get("feat_commission_ratio_pct", 0.0),
        }
    else:
        eng_pct = record.get("feat_engagement_pct", 0.0)
        contribs = {
            "engagement_pct": 0.70 * eng_pct,
            "category_match": 0.30 * _category_match_bonus(record),
        }
    score = sum(contribs.values())
    # 爆款标签轻微加成（不超过 0.05，避免完全偏向已知爆款）
    if is_hot:
        score = min(1.0, score + 0.05)
    return {
        "score": round(score, 4),
        "contribs": {k: round(v, 4) for k, v in contribs.items()},
        "is_hot": is_hot,
    }


def _category_match_bonus(record: Dict[str, Any]) -> float:
    """社媒内容是否命中"百货"相关关键词。"""
    text = (record.get("title") or "") + " " + (record.get("desc") or "")
    if not text:
        return 0.0
    keywords = ["百货", "日用", "居家", "厨房", "生活用品", "家居"]
    hits = sum(1 for k in keywords if k in text)
    return min(1.0, hits / 3.0)


# ---- 6. 报告 ----

def render_report(records: List[Dict[str, Any]],
                  hot_map: Dict[str, bool],
                  stats: Dict[str, Any]) -> str:
    """生成 markdown 报告。"""
    # 给每条记录打分
    scored: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for r in records:
        is_hot = hot_map.get(r.get("product_id")) if r.get("source") == "douyin_ec" else False
        s = v1_rule_score(r, bool(is_hot))
        scored.append((r, s))
    scored.sort(key=lambda x: x[1]["score"], reverse=True)
    top20 = scored[:20]

    lines: List[str] = []
    lines.append("# TrendPicker 选品报告 · 百货类目")
    lines.append("")
    lines.append(f"- 报告生成时间: {datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')}")
    lines.append(f"- 数据窗口: 2026-08-23 ~ 2026-09-21（30 天）")
    lines.append(f"- 观察窗口（爆款判定）: {WINDOW_START_DATE} ~ {WINDOW_END_DATE}（14 天）")
    lines.append(f"- 爆款定义: 叶子类目前 {HOT_TOP_PERCENT}% 且连续 ≥ {HOT_MIN_DAYS} 天")
    lines.append("")
    lines.append("## 数据摄入")
    lines.append("")
    lines.append("| 数据源 | 平台 | 原始 | 去重后 | API |")
    lines.append("|---|---|---:|---:|---|")
    lines.append(f"| 小红书笔记 | TikHub `/xiaohongshu/app_v2/search_notes` | {stats['xhs_raw']} | {stats['xhs_dedup']} | 6 次调用 |")
    lines.append(f"| 微信视频号 | TikHub `/wechat_search/v2/fetch_search` | {stats['wechat_raw']} | {stats['wechat_dedup']} | 6 次调用 |")
    lines.append(f"| 抖音电商 | JustOneAPI `/douyin-ec/search-item-list/v1` | {stats['douyin_raw']} | {stats['douyin_dedup']} | 12 次调用 |")
    lines.append(f"| **合计** |  | **{stats['xhs_raw']+stats['wechat_raw']+stats['douyin_raw']}** | **{stats['xhs_dedup']+stats['wechat_dedup']+stats['douyin_dedup']}** | **24 次调用** |")
    lines.append("")
    lines.append("## 抖音电商叶子类目分布（TOP-10）")
    lines.append("")
    lines.append("| 排名 | 一级类目 | 商品数 |")
    lines.append("|---:|---|---:|")
    cat_counter = Counter(r.get("category_l1_name", "未知") for r in records if r.get("source") == "douyin_ec")
    for i, (cat, cnt) in enumerate(cat_counter.most_common(10), 1):
        lines.append(f"| {i} | {cat} | {cnt} |")
    lines.append("")
    lines.append("## Top-20 选品榜单（V1 规则版）")
    lines.append("")
    lines.append("| 排名 | 分数 | 标题 | 平台 | 类目 | 月销 | 末7日均 | 动量比 | 爆款 | 主要贡献 |")
    lines.append("|---:|---:|---|---|---|---:|---:|---:|:---:|---|")
    for i, (r, s) in enumerate(top20, 1):
        src = r.get("source", "")
        title = (r.get("title") or "")[:40]
        if src == "douyin_ec":
            cat = r.get("category_l3_name") or r.get("category_l2_name") or "-"
            month_sale = f"{r.get('month_sale', 0):,}"
            r7 = r.get("feat_recent_7d_avg", 0)
            mom = r.get("feat_momentum_ratio", 0)
            main_contribs = sorted(s["contribs"].items(), key=lambda x: -x[1])[:2]
            contrib_str = " + ".join(f"{k}({v:.3f})" for k, v in main_contribs)
            hot_mark = "🔥" if s["is_hot"] else ""
            lines.append(f"| {i} | {s['score']:.3f} | {title} | 抖音电商 | {cat} | {month_sale} | {r7:.0f} | {mom:.2f} | {hot_mark} | {contrib_str} |")
        elif src == "xhs":
            eng = int(r.get("feat_engagement", 0))
            main_contribs = sorted(s["contribs"].items(), key=lambda x: -x[1])[:2]
            contrib_str = " + ".join(f"{k}({v:.3f})" for k, v in main_contribs)
            lines.append(f"| {i} | {s['score']:.3f} | {title} | 小红书 | 社媒 | - | - | - |  | {contrib_str} |")
        elif src == "wechat_channels":
            eng = int(r.get("feat_engagement", 0))
            main_contribs = sorted(s["contribs"].items(), key=lambda x: -x[1])[:2]
            contrib_str = " + ".join(f"{k}({v:.3f})" for k, v in main_contribs)
            lines.append(f"| {i} | {s['score']:.3f} | {title} | 微信视频号 | 社媒 | - | - | - |  | {contrib_str} |")
    lines.append("")
    lines.append("## 爆款商品明细（叶子类目前 5% × 持续 ≥ 5 天）")
    lines.append("")
    hot_records = [r for r, s in scored if s["is_hot"]]
    lines.append(f"共 {len(hot_records)} 个爆款商品，覆盖以下叶子类目：")
    lines.append("")
    hot_leaf = Counter(r.get("category_l3_name") or r.get("category_l2_name") or "-" for r in hot_records)
    lines.append("| 叶子类目 | 爆款数 |")
    lines.append("|---|---:|")
    for leaf, cnt in hot_leaf.most_common():
        lines.append(f"| {leaf} | {cnt} |")
    lines.append("")
    lines.append("## 流程小结")
    lines.append("")
    lines.append("```")
    lines.append("ingestion: 3 数据源并行子 agent 抓取 → 385 条归一化记录")
    lines.append(f"dedup:     标题 Jaccard ≥ 0.85 同源聚合（{stats['xhs_dedup']+stats['wechat_dedup']+stats['douyin_dedup']} 条保留代表）")
    lines.append("features:  跨源分位排名 + 14 天窗口趋势斜率 + 持续天数")
    lines.append(f"label:     叶子类目前 5% × 持续 ≥ 5 天 → {len(hot_records)} 个爆款")
    lines.append("v1_rules:  7 维加权规则打分（销量30% / 动量20% / 好评10% / 达人10% / 店铺10% / 榜单10% / 佣金10%）")
    lines.append("report:    Top-20 榜单 + 爆款明细 + 类目分布")
    lines.append("```")
    lines.append("")
    lines.append("## V1 规则可解释性样例（Top-1 商品的打分拆解）")
    lines.append("")
    if top20:
        r1, s1 = top20[0]
        lines.append(f"- 商品: {r1.get('title', '')[:60]}")
        lines.append(f"- 平台: {r1.get('source', '')}")
        lines.append(f"- 总分: **{s1['score']:.4f}**")
        lines.append("")
        lines.append("| 规则 | 子分 | 权重 | 加权 |")
        lines.append("|---|---:|---:|---:|")
        weight_map = {
            "month_sale_pct": 0.30, "momentum": 0.20, "good_ratio_pct": 0.10,
            "author_num_pct": 0.10, "shop_score_pct": 0.10, "ranking_signal": 0.10,
            "commission_pct": 0.10, "engagement_pct": 0.70, "category_match": 0.30,
        }
        for k, v in s1["contribs"].items():
            w = weight_map.get(k, 0)
            sub = v / w if w else 0
            lines.append(f"| {k} | {sub:.3f} | {w:.2f} | {v:.4f} |")
        if s1["is_hot"]:
            lines.append(f"| 爆款加成 | - | - | +0.0500 |")
    lines.append("")
    lines.append("## 注意事项与已知限制")
    lines.append("")
    lines.append("- 本流程是 trendpicker 项目 Phase 1 端到端 demo；项目 src/ 下 `ingestion` / `features` / `label` / `v1_rules` / `dedup` 模块尚未实现（pytest 全部 skipped），本脚本用独立脚本跑通同等流程。")
    lines.append("- 社媒源（小红书 / 微信视频号）无销量数据，仅靠互动分位 + 类目命中度参与排名；故 Top-20 中电商商品占多数。")
    lines.append("- 抖音 sale_axis 已含 30 天逐日销量，足以构造 14 天观察窗口的斜率与持续天数。")
    lines.append("- 标题 Jaccard 用最简版中文 bigram，生产应换 jieba；图片 pHash 跨源同款聚合本次未做。")
    lines.append("- 凭据（TIKHUB_API_KEY / JUSTONEAPI_API_KEY）通过环境变量传递，未写入版本库；trendpicker/src/credentials.py 的 CREDENTIAL_NAMES 未注册这两条凭据，本脚本不依赖 credentials.py，直接读 JSONL。")
    return "\n".join(lines)


# ---- main ----

def main() -> int:
    log.info("=== TrendPicker Pipeline 启动 ===")
    # 1. ingestion
    raw = ingest()
    xhs_raw = raw["xhs"]
    wechat_raw = raw["wechat"]
    douyin_raw = raw["douyin"]

    # 2. dedup（同源内）
    xhs_dedup = dedup_within(xhs_raw)
    wechat_dedup = dedup_within(wechat_raw)
    douyin_dedup = dedup_within(douyin_raw)

    # 3. features
    records = build_features(douyin_dedup, xhs_dedup, wechat_dedup)

    # 4. label（仅抖音侧有销量，可构造爆款标签）
    douyin_records = [r for r in records if r.get("source") == "douyin_ec"]
    hot_map = build_labels(douyin_records)

    # 5. 报告
    stats = {
        "xhs_raw": len(xhs_raw), "xhs_dedup": len(xhs_dedup),
        "wechat_raw": len(wechat_raw), "wechat_dedup": len(wechat_dedup),
        "douyin_raw": len(douyin_raw), "douyin_dedup": len(douyin_dedup),
    }
    report_md = render_report(records, hot_map, stats)

    # 写报告
    report_path = REPORTS_DIR / f"baihuo_report_{datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d_%H%M%S')}.md"
    report_path.write_text(report_md, encoding="utf-8")
    log.info("报告已写入: %s", report_path)

    # 也写一份 scored JSONL 供后续使用
    scored_jsonl = REPORTS_DIR / "baihuo_scored.jsonl"
    with scored_jsonl.open("w", encoding="utf-8") as f:
        for r in records:
            is_hot = bool(hot_map.get(r.get("product_id")) if r.get("source") == "douyin_ec" else False)
            s = v1_rule_score(r, is_hot)
            out = {k: v for k, v in r.items() if not k.startswith("_")}
            out["v1_score"] = s["score"]
            out["v1_contribs"] = s["contribs"]
            out["is_hot"] = s["is_hot"]
            f.write(json.dumps(out, ensure_ascii=False) + "\n")
    log.info("评分数据已写入: %s", scored_jsonl)

    log.info("=== Pipeline 完成 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
