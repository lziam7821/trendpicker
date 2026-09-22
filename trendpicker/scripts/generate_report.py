"""从 SQLite 库读取数据生成精简版评估报告.

严格遵守 DATA_FETCH_POLICY.md:
    - 本脚本只读 SQLite 库, 不调用任何外部 API
    - 所有数据从 product_predictions / features / raw_* 表中 SELECT 出来

输出:
    - reports/assessment_report_<timestamp>.md  精简版评估报告
    - 报告字段严格 6 项:
        1. 商品名称 (只展示真实电商商品, 不混入社媒笔记/视频标题)
        2. 分类
        3. 过去爆款原因分析
        4. 预期是否属于爆款
        5. 预测原因
        6. 各平台数据表现 (抖音电商叶子类目内销量排名 + 同类目在小红书/视频号种草热度)

使用:
    cd /workspace/trendpicker
    python scripts/generate_report.py
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "trendpicker.db"
REPORTS_DIR = PROJECT_ROOT / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("trendpicker.report")


def _now_iso() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def _category_label(row: Dict[str, Any]) -> str:
    parts = []
    for k in ("category_l1_name", "category_l2_name", "category_l3_name"):
        v = row.get(k)
        if v:
            parts.append(v)
    return " / ".join(parts) if parts else "—"


def _extract_keywords(leaf_category: str, title: str) -> List[str]:
    """从叶子类目 + 商品标题中提取用于在社媒中匹配种草热度的关键词。

    策略:
        1. 叶子类目名按 "/" 切分, 每段长度 >=2 视为关键词
        2. 如果叶子类目为"其他 XXX" / "普通 XXX" 这种泛化词, 从标题里提取
    """
    kws: List[str] = []
    if leaf_category:
        for part in leaf_category.split("/"):
            part = part.strip()
            if len(part) >= 2 and not part.startswith("其他") and part != "未分类":
                kws.append(part)
    # 去重保序
    seen = set()
    out = []
    for k in kws:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _social_seeding_count(conn: sqlite3.Connection, keywords: List[str], source: str) -> tuple[int, List[str]]:
    """统计 features 表中某源 (xhs/wechat_channels) 标题命中关键词的记录数.

    返回 (命中数, 命中的代表性标题样本, 最多 2 条)
    """
    if not keywords:
        return 0, []
    # 拼 SQL: title LIKE '%kw1%' OR title LIKE '%kw2%' ...
    clauses = " OR ".join(["title LIKE ?" for _ in keywords])
    params = [f"%{kw}%" for kw in keywords]
    sql = f"SELECT title FROM features WHERE source=? AND ({clauses}) LIMIT 5"
    cur = conn.execute(sql, [source] + params)
    rows = [r[0] for r in cur.fetchall() if r[0]]
    return len(rows), rows[:2]


def _douyin_rank_in_leaf(conn: sqlite3.Connection, leaf: str, product_id: str) -> tuple[int, int, int]:
    """查询该商品在叶子类目内的销量排名.

    Returns: (rank, total_in_leaf, month_sale)
    """
    if not leaf:
        return 0, 0, 0
    cur = conn.execute(
        """SELECT product_id, month_sale
           FROM raw_douyin_ec_items
           WHERE category_l3_name = ?
           ORDER BY month_sale DESC""", [leaf]
    )
    rows = cur.fetchall()
    total = len(rows)
    rank = 0
    sale = 0
    for i, (pid, ms) in enumerate(rows, 1):
        if pid == product_id:
            rank = i
            sale = ms or 0
            break
    return rank, total, sale


def _platform_perf_text(conn: sqlite3.Connection, r: Dict[str, Any]) -> str:
    """渲染该商品在各平台的数据表现."""
    leaf = r.get("category_l3_name") or ""
    title = r.get("title") or ""
    pid = r.get("source_id") or ""

    # 1. 抖音电商: 该商品在叶子类目内的销量排名
    rank, total, sale = _douyin_rank_in_leaf(conn, leaf, pid)

    # 2. 同类目商品总数 (同叶子类目有多少竞争商品)
    dy_in_leaf = total

    # 3. 社媒种草热度: 用叶子类目关键词在社媒标题里命中数
    keywords = _extract_keywords(leaf, title)
    xhs_count, xhs_samples = _social_seeding_count(conn, keywords, "xhs")
    wc_count, wc_samples = _social_seeding_count(conn, keywords, "wechat_channels")

    parts: List[str] = []
    # 抖音电商表现
    if leaf:
        parts.append(f"抖音电商：叶子类目「{leaf}」共 {dy_in_leaf} 个竞品, 本商品月销 {sale:,} 排第 {rank}/{dy_in_leaf}")
    else:
        parts.append(f"抖音电商：月销 {sale:,}")
    # 小红书种草
    if xhs_count:
        sample = xhs_samples[0][:30] + "..." if xhs_samples and xhs_samples[0] else ""
        parts.append(f"小红书：{xhs_count} 条种草笔记命中关键词 [{','.join(keywords)}]")
    else:
        parts.append(f"小红书：0 条种草笔记 (关键词 [{','.join(keywords) if keywords else '—'}])")
    # 视频号种草
    if wc_count:
        sample = wc_samples[0][:30] + "..." if wc_samples and wc_samples[0] else ""
        parts.append(f"视频号：{wc_count} 条种草视频命中")
    else:
        parts.append(f"视频号：0 条种草视频")

    return " | ".join(parts)


def main() -> int:
    log.info("=== generate_report 启动（仅读库, 不调 API）===")

    if not DB_PATH.exists():
        log.error("数据库不存在: %s, 请先运行 python scripts/ingest_to_db.py", DB_PATH)
        return 1

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # 只筛抖音电商真实商品, 不混入社媒笔记/视频标题
    cur = conn.execute(
        """SELECT record_id, source, source_id, title,
                  category_l1_name, category_l2_name, category_l3_name,
                  is_hot, past_hot_reason, predicted_hot, prediction_reason,
                  v1_score
           FROM product_predictions
           WHERE source='douyin_ec'
           ORDER BY v1_score DESC
           LIMIT 30"""
    )
    rows = [dict(r) for r in cur.fetchall()]

    # 类目分布统计 (全库 douyin_ec)
    cur = conn.execute(
        """SELECT category_l1_name, COUNT(*) AS cnt
           FROM product_predictions
           WHERE source='douyin_ec' AND category_l1_name IS NOT NULL
           GROUP BY category_l1_name
           ORDER BY cnt DESC LIMIT 5"""
    )
    cat_top = [(r["category_l1_name"], r["cnt"]) for r in cur.fetchall()]

    # 总数统计 (只算真实商品)
    cur = conn.execute("SELECT COUNT(*) FROM product_predictions WHERE source='douyin_ec'")
    total = cur.fetchone()[0]
    cur = conn.execute("SELECT COUNT(*) FROM product_predictions WHERE source='douyin_ec' AND is_hot=1")
    past_hot_total = cur.fetchone()[0]
    cur = conn.execute("SELECT COUNT(*) FROM product_predictions WHERE source='douyin_ec' AND predicted_hot=1")
    pred_hot_total = cur.fetchone()[0]

    # 社媒全局统计 (单独列出)
    cur = conn.execute("SELECT COUNT(*) FROM features WHERE source='xhs'")
    xhs_total = cur.fetchone()[0]
    cur = conn.execute("SELECT COUNT(*) FROM features WHERE source='wechat_channels'")
    wc_total = cur.fetchone()[0]

    # 渲染报告
    ts = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S")
    report_path = REPORTS_DIR / f"assessment_report_{ts}.md"

    lines: List[str] = []
    lines.append("# TrendPicker 百货类目评估报告")
    lines.append("")
    lines.append(f"- 生成时间: {_now_iso()}")
    lines.append(f"- 数据来源: trendpicker.db（本地 SQLite, 遵守 DATA_FETCH_POLICY.md）")
    lines.append(f"- 评估范围: 仅真实电商商品 {total} 条（抖音电商）；过去爆款 {past_hot_total} 个, 预期爆款 {pred_hot_total} 个")
    lines.append(f"- 一级类目 TOP-5: " + "、".join(f"{c}({n})" for c, n in cat_top))
    lines.append(f"- 社媒种草数据: 小红书 {xhs_total} 条笔记 / 视频号 {wc_total} 条视频（用于同类目种草热度参照, 不进入榜单）")
    lines.append("")
    lines.append("> **数据获取规则**: 外部接口（TikHub / JustOneAPI）数据已落库; 本报告所有分析均从库中读取, 未调用任何外部 API。")
    lines.append("> **商品名称说明**: 报告仅展示抖音电商真实商品标题; 社媒内容标题（笔记/视频）不作为商品列出。")
    lines.append("")
    lines.append("## 评估清单（Top-30 真实商品, 按 V1 规则总分降序）")
    lines.append("")
    lines.append("| # | 商品名称 | 分类 | 过去爆款原因分析 | 预期爆款 | 预测原因 | 各平台数据表现 |")
    lines.append("|---:|---|---|---|:---:|---|---|")
    for i, r in enumerate(rows, 1):
        title = (r.get("title") or "—")
        if len(title) > 50:
            title = title[:50] + "..."
        cat = _category_label(r)
        past_reason = r.get("past_hot_reason") or "—"
        pred_flag = "是" if r.get("predicted_hot") == 1 else "否"
        pred_reason = r.get("prediction_reason") or "—"
        perf = _platform_perf_text(conn, r)
        lines.append(f"| {i} | {title} | {cat} | {past_reason} | **{pred_flag}** | {pred_reason} | {perf} |")
    lines.append("")
    lines.append("## 字段说明")
    lines.append("")
    lines.append("- **商品名称**: 抖音电商商品标题（来自 raw_douyin_ec_items.title, 真实电商商品）")
    lines.append("- **分类**: 一级类目 / 二级类目 / 叶子类目")
    lines.append("- **过去爆款原因分析**: 叶子类目前 5% 且持续 ≥ 5 天 = 过去爆款; 原因列出榜单 / 持续天数 / 动量")
    lines.append("- **预期是否属于爆款**: V1 规则总分 ≥ 0.70 = 是; < 0.70 = 否")
    lines.append("- **预测原因**: V1 规则 7 维贡献拆解（销量 30% / 动量 20% / 好评 10% / 达人 10% / 店铺 10% / 榜单 10% / 佣金 10%）")
    lines.append("- **各平台数据表现**: 该商品在抖音电商叶子类目内的销量排名 + 同类目在小红书 / 视频号的种草热度（用叶子类目关键词在社媒标题里匹配）")
    lines.append("")
    lines.append("## 规则与流程合规性")
    lines.append("")
    lines.append("- 落库链路: 外部 API → 原始响应存 raw/ → 解析 → INSERT/UPSERT 到 raw_xxx → fetch_log")
    lines.append("- 分析链路（本报告）: SELECT FROM product_predictions WHERE source='douyin_ec' → 6 字段渲染 → Markdown 报告")
    lines.append("- 本报告未触及任何外部 API; 所有数据均从 trendpicker.db 读取")
    lines.append("- 凭据 TIKHUB_API_KEY / JUSTONEAPI_API_KEY 已在 credentials.py 的 CREDENTIAL_NAMES 中登记, 落库时通过环境变量读取, 未写入版本库")
    lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    log.info("报告已生成: %s", report_path)
    log.info("Top-30 已渲染 %d 行真实商品", len(rows))

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
