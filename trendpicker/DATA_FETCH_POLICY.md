# 数据获取强硬规则 (DATA FETCH POLICY)

> **本规则不可绕过。任何代码、子 agent、脚本、自动化流程都必须遵守。**

## 总则

1. **凡调用外部接口（TikHub / JustOneAPI / 蝉妈妈 / 1688 / 淘宝客 / 多多进宝 / 抖店 / LLM 等）获取到的数据，必须立即落库（`trendpicker.db` 或生产 PostgreSQL）。**
2. 落库前不得对数据做任何业务分析、特征工程、模型推理、报告生成。
3. 后续所有分析（特征、标签、规则打分、模型预测、报告渲染）**只允许从库中读取数据**，不得再次调用外部接口。
4. 唯一允许"先调接口不落库"的情形：
   - 单次性的 API 探活 / 字段结构探查（如确认响应字段名）
   - 拉取某条记录的详情补充（如商品详情、笔记详情）
   - 上述情形必须在调用日志中显式标注 `purpose=probe` 或 `purpose=detail_fetch`，并尽快把详情数据写回库。

## 数据落库契约

### 表结构（最小集）

- `raw_xhs_notes`        — 小红书笔记原始 + 归一化字段
- `raw_wechat_videos`    — 微信视频号原始 + 归一化字段
- `raw_douyin_ec_items`  — 抖音电商商品原始 + 归一化字段 + 30 天 `sale_axis`（JSON 字段）
- `fetch_log`            — 每次外部调用的元数据（source / endpoint / keyword / page / timestamp / response_file / status）

### 字段最低要求

每张 raw 表必须包含：
- 主键（`note_id` / `export_id` / `product_id`）
- `search_keyword`（搜索关键词，溯源用）
- `fetched_at`（落库时间戳，ISO8601 +08:00）
- `raw_response_file`（原始响应文件路径，便于回溯）
- 业务字段（标题、作者、销量、互动数等）

### 落库时机

```
外部 API 调用 → 原始响应存 raw/ 文件 → 解析 → INSERT/UPSERT 到 raw 表 → 提交 fetch_log
↓
后续所有读取: SELECT FROM raw_xxx WHERE ... → 分析 → 报告
```

**禁止**：分析阶段直接读 `data/raw/*.json` 绕过库；禁止每次分析都重新调 API。

## 凭据注册

调用外部接口前，凭据名必须在 `src/trendpicker/credentials.py` 的 `CREDENTIAL_NAMES` 中登记：

- `TIKHUB_API_KEY` — TikHub API（小红书 / 微信视频号 / TikTok 内容搜索）
- `JUSTONEAPI_API_KEY` — JustOneAPI（抖音电商商品 / 销量 / 蝉妈妈类数据）
- 其余凭据见 credentials.py 注释

未注册的凭据名禁止读取，禁止在代码里硬编码。

## 违规判定

任何分析脚本如果出现以下行为，视为违规：
1. 在分析阶段（非初次抓取阶段）调用外部 API
2. 直接读取 `data/raw/*.json` 做分析而不经过库
3. 同一关键词在 24 小时内重复调用外部 API 拉取（未命中库内缓存）

## 执行范围

- 本规则适用于：项目内 `src/trendpicker/` 模块、`scripts/` 脚本、并行子 agent、CI 流程、回测与预测流程
- 持久化层：本地 SQLite（`trendpicker.db`），P2 后切换 PostgreSQL，规则不变
