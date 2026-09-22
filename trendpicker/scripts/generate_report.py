"""从 SQLite 库读取数据生成精简版评估报告.

严格遵守 DATA_FETCH_POLICY.md:
    - 本脚本只读 SQLite 库, 不调用任何外部 API
    - 所有数据从 product_predictions / features / raw_* 表中 SELECT 出来

输出:
    - reports/assessment_report_<timestamp>.md  精简版评估报告
    - 报告字段严格 6 项:
        1. 商品名称
        2. 分类
        3. 过去爆款原因分析
        4. 预期是否属于爆款
        5. 预测原因
        6. 各平台数据表现情况

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
    """拼装类目路径，缺失层级跳过。"""
    parts = []
    for k in ("category_l1_name", "category_l2_name", "category_l3_name"):
        v = row.get(k)
        if v:
            parts.append(v)
    return " / ".join(parts) if parts else "—（社媒内容无类目归属）"


def _platform_perf_text(perf_json: str, record_source: str, record_id: str) -> str:
    """把 platform_perf_json 渲染成一段可读文本。

    平台数据表现是聚合数据（全数据集 3 个平台），不是单条记录的数据；
    我们这里把聚合摘要 + 当前记录自身在平台上的位置一起呈现。
    """
    if not perf_json:
        return "—"
    try:
        perf = json.loads(perf_json)
    except json.JSONDecodeError:
        return "—（解析失败）"

    lines: List[str] = []

    # 抖音电商
    dy = perf.get("douyin_ec")
    if dy:
        lines.append(
            f"抖音电商：共 {dy['count']} 条商品，总月销 {dy['total_month_sale']:,}，"
            f"平均好评率 {dy['avg_good_ratio']}%，过去爆款 {dy['hot_count']} 个；"
            f"TOP-3 叶子类目：" + "、".join(f"{t['leaf']}({t['count']})" for t in dy["top_leaf_categories"])
        )

    # 小红书
    xhs = perf.get("xhs")
    if xhs:
        lines.append(
            f"小红书：共 {xhs['count']} 条笔记，总互动 {xhs['total_engagement']:,}，"
            f"平均互动分位 {xhs['avg_engagement_pct']}"
        )

    # 微信视频号
    wc = perf.get("wechat_channels")
    if wc:
        lines.append(
            f"微信视频号：共 {wc['count']} 条视频，总点赞 {wc['total_likes']:,}，"
            f"平均互动分位 {wc['avg_engagement_pct']}"
        )

    return " | ".join(lines) if lines else "—"


def main() -> int:
    log.info("=== generate_report 启动（仅读库，不调 API）===")

    if not DB_PATH.exists():
        log.error("数据库不存在: %s，请先运行 python scripts/ingest_to_db.py", DB_PATH)
        return 1

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # 读取所有预测结果（按 v1_score 降序，取前 30 个最值得评估的）
    cur = conn.execute(
        """SELECT record_id, source, source_id, title,
                  category_l1_name, category_l2_name, category_l3_name,
                  is_hot, past_hot_reason, predicted_hot, prediction_reason,
                  v1_score, platform_perf_json
           FROM product_predictions
           ORDER BY v1_score DESC
           LIMIT 30"""
    )
    rows = [dict(r) for r in cur.fetchall()]

    # 类目分布统计（全库）
    cur = conn.execute(
        """SELECT category_l1_name, COUNT(*) AS cnt
           FROM product_predictions
           WHERE category_l1_name IS NOT NULL
           GROUP BY category_l1_name
           ORDER BY cnt DESC LIMIT 5"""
    )
    cat_top = [(r["category_l1_name"], r["cnt"]) for r in cur.fetchall()]

    # 爆款总数
    cur = conn.execute("SELECT COUNT(*) FROM product_predictions WHERE is_hot=1")
    past_hot_total = cur.fetchone()[0]
    cur = conn.execute("SELECT COUNT(*) FROM product_predictions WHERE predicted_hot=1")
    pred_hot_total = cur.fetchone()[0]
    cur = conn.execute("SELECT COUNT(*) FROM product_predictions")
    total = cur.fetchone()[0]

    # 渲染报告
    ts = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S")
    report_path = REPORTS_DIR / f"assessment_report_{ts}.md"

    lines: List[str] = []
    lines.append("# TrendPicker 百货类目评估报告")
    lines.append("")
    lines.append(f"- 生成时间: {_now_iso()}")
    lines.append(f"- 数据来源: trendpicker.db（本地 SQLite，遵守 DATA_FETCH_POLICY.md）")
    lines.append(f"- 评估总数: {total} 条；过去爆款 {past_hot_total} 个；预期爆款 {pred_hot_total} 个")
    lines.append(f"- 一级类目 TOP-5: " + "、".join(f"{c}({n})" for c, n in cat_top))
    lines.append("")
    lines.append("> **数据获取规则**：外部接口（TikHub / JustOneAPI）数据已落库；本报告所有分析均从库中读取，未调用任何外部 API。")
    lines.append("")
    lines.append("## 评估清单（Top-30，按 V1 规则总分降序）")
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
        perf = _platform_perf_text(r.get("platform_perf_json") or "", r.get("source"), r.get("record_id"))
        lines.append(f"| {i} | {title} | {cat} | {past_reason} | **{pred_flag}** | {pred_reason} | {perf} |")
    lines.append("")
    lines.append("## 字段说明")
    lines.append("")
    lines.append("- **商品名称**：抖音电商商品标题 / 小红书笔记标题 / 微信视频号视频标题")
    lines.append("- **分类**：抖音电商的「一级类目 / 二级类目 / 叶子类目」；社媒源无类目归属")
    lines.append("- **过去爆款原因分析**：叶子类目前 5% 且持续 ≥ 5 天 = 过去爆款；原因列出关键指标（榜单、持续天数、动量）")
    lines.append("- **预期是否属于爆款**：V1 规则总分 ≥ 0.70 = 是；< 0.70 = 否")
    lines.append("- **预测原因**：V1 规则 7 维贡献拆解（销量 30% / 动量 20% / 好评 10% / 达人 10% / 店铺 10% / 榜单 10% / 佣金 10%）；社媒源走 2 维（互动 70% + 类目命中 30%）")
    lines.append("- **各平台数据表现**：3 个平台的全局聚合摘要（抖音电商总月销/好评/类目分布、小红书总互动、视频号总点赞）")
    lines.append("")
    lines.append("## 规则与流程合规性")
    lines.append("")
    lines.append("- 落库链路：外部 API → 原始响应存 raw/ 文件 → 解析 → INSERT/UPSERT 到 raw_xxx 表 → 提交 fetch_log")
    lines.append("- 分析链路（本报告）：SELECT FROM product_predictions → 6 字段渲染 → Markdown 报告")
    lines.append("- 本报告未触及任何外部 API；所有数据均从 `trendpicker.db` 读取")
    lines.append(f"- 凭据 TIKHUB_API_KEY / JUSTONEAPI_API_KEY 已在 `credentials.py` 的 CREDENTIAL_NAMES 中登记，落库时通过环境变量读取，未写入版本库")
    lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    log.info("报告已生成: %s", report_path)
    log.info("Top-30 已渲染 %d 行", len(rows))

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
