# 接口契约约定

**Feature**: `001-geo-insight-platform`
**契约文件**: [openapi.yaml](./openapi.yaml)
**版本**: 0.2.0（首版，未冻结）

## 1. 基础约定

| 项 | 约定 |
|---|---|
| Base path | `/v1` |
| 认证 | 契约目标是 `Authorization: Bearer <JWT>`，租户从 token 解析；**首版未启用鉴权**，实现从 `X-Tenant-Id` 读到默认租户（见 tasks T091） |
| 内容类型 | 请求与响应均为 `application/json; charset=utf-8`；导出端点例外（`text/csv` / `application/x-ndjson`） |
| 时间 | 日期用 `YYYY-MM-DD`，时间戳用 RFC 3339（带时区） |
| 分页 | `page`（从 1 开始）+ `limit`（1~100，默认 20）；响应带 `page/limit/total` |
| 幂等 | 接受 `Idempotency-Key` 头，**首版不校验重复提交**；重复保护依赖数据库唯一键（见 §6） |
| 错误 | `{code, message, request_id, details}`；`request_id` 必须回写到日志，便于排查 |
| 版本 | 破坏性变更升 `/v2`；新增可选字段不升版本 |
| 端点范围 | §3 表内即当前实现；服务端 PDF 导出与 run 触发均不在首版 |

## 2. 错误码

| HTTP | code | 含义 | 客户端动作 |
|---|---|---|---|
| 400 | `VALIDATION_ERROR` | 参数不合法 | 展示字段级错误 |
| 401 | `UNAUTHENTICATED` | 未登录或 token 过期 | 跳登录（首版不会返回） |
| 403 | `INSUFFICIENT_PERMISSION` | 角色不足（viewer 改配置） | 提示无权限（首版不会返回） |
| 404 | `NOT_FOUND` | 资源不存在或无权访问 | 展示空状态 |
| 409 | `STATE_CONFLICT` | 状态机不允许（如 archived → active） | 提示当前状态 |
| 409 | `ALIAS_CONFLICT` | 别名重复 | 高亮已存在 |
| 409 | `COMPETITOR_CONFLICT` | 竞品重复 | 高亮已存在 |
| 422 | `INSUFFICIENT_SAMPLES` | 请求的周期内样本不足 | 提示换周期，不展示 0% |
| 429 | `RATE_LIMITED` | 触发限流 | 退避重试 |
| 500 | `INTERNAL_ERROR` | 未预期错误 | 展示 request_id |

> 注意：`INSUFFICIENT_SAMPLES` 在**指标响应**里不是 HTTP 错误，而是 `MetricValue.status = insufficient_samples`。只有「整段周期都没有任何样本」时才返回 422。

## 3. 端点概览

| 方法 | 路径 | 用途 | 权限 |
|---|---|---|---|
| GET | `/trackers` | 监控对象列表 | viewer |
| POST | `/trackers` | 创建监控对象（含别名、竞品、空问题集） | operator |
| GET | `/trackers/{id}` | 详情（品牌、竞品版本、采样计划、问题汇总） | viewer |
| PATCH | `/trackers/{id}` | 改名 / 改行业地区 / 改状态 | operator |
| GET | `/trackers/{id}/brand` | 品牌与别名 | viewer |
| POST | `/brands/{id}/aliases` | 新增别名 | operator |
| DELETE | `/brands/{id}/aliases/{aliasId}` | 删除别名 | operator |
| GET | `/trackers/{id}/competitors` | 竞品集合与版本 | viewer |
| POST | `/trackers/{id}/competitors` | 新增竞品（版本递增） | operator |
| DELETE | `/trackers/{id}/competitors/{competitorId}` | 移除竞品（版本递增） | operator |
| GET | `/trackers/{id}/questions` | 问题列表与计数（total/core/long_tail） | viewer |
| POST | `/trackers/{id}/questions` | 批量导入（≤500，`text` 每行一条，`*` 前缀标核心） | operator |
| PATCH | `/questions/{id}` | 改层级（次日生效）/ 状态 | operator |
| GET | `/trackers/{id}/sampling-plan` | 采样计划、每日预估量、失败率 | viewer |
| GET | `/meta/methodology` | 口径说明（分母、位次、去重规则） | viewer |
| GET | `/trackers/{id}/metrics/overview` | 指标卡 | viewer |
| GET | `/trackers/{id}/metrics/trend` | 趋势 | viewer |
| GET | `/trackers/{id}/metrics/{metric}/samples` | 指标构成样本（下钻） | viewer |
| GET | `/trackers/{id}/answers` | 问答明细 | viewer |
| GET | `/trackers/{id}/answers/export` | 原始答案导出（csv/jsonl） | operator |
| GET | `/answers/{id}` | 答案详情（原文、提及、引用、推荐清单） | viewer |
| GET | `/trackers/{id}/citations/summary` | 信源分布 | viewer |
| GET | `/trackers/{id}/citations` | 信源明细（按 URL 去重、次数累计） | viewer |
| GET | `/trackers/{id}/reports` | 报告列表 | viewer |
| POST | `/trackers/{id}/reports` | 生成报告（同步生成，201） | operator |
| GET | `/reports/{id}` | 报告快照 | viewer |

**未实现的契约项**：`POST /runs`（FR-009 手动触发采样）不在首版契约内，因为真实采集尚未接入；采样计划只读（T085 部分完成）。

**PDF 导出**：不提供服务端渲染端点。报告页 `/report/{id}` 带打印样式，浏览器「打印 → 另存为 PDF」即导出，避免引入 Gotenberg/Chromium 依赖（见 `docs/decisions/005-report-pdf-browser-print.md`）。

## 4. 指标响应约定

- 每个指标值都是对象，不是裸数字：`{value, status, sample_base}`。
- `status = insufficient_samples` 时 `value` 为 `null`，前端必须显示「样本不足」，禁止显示 0。
- `cards.sentiment.base` 是情感分母，等于提及样本数；未提及的答案不进入情感统计。
- `no_list_samples` 单独返回，前端在平均排名旁展示，不能并进 `valid_total`。
- `metrics/trend` 首版只返回日粒度，`model_changes` 为空数组；按周聚合与模型变更点标注延后（T094），不得据此做跨模型结论。
- 下钻响应里每条样本带 `in_denominator`：`false` 表示该样本展示但不计入该指标分母（例如无列表样本之于平均排名）。

## 5. 请求示例

```bash
curl -s -X POST "$BASE/v1/trackers" \
  -H "Content-Type: application/json" \
  -d '{"name":"辽宁辽征 · 律所","brand_name":"辽宁辽征律师事务所","industry":"律师事务所","region":"沈阳","brand_aliases":["辽征","辽征律所"],"competitor_names":["北京盈科（沈阳）律师事务所"]}'
```

```bash
curl -s "$BASE/v1/trackers/$TRACKER/metrics/overview?from=2026-11-01&to=2026-11-07&model=doubao&channel=web"
```

```bash
# 生成报告：周期至少 2 天（period_start < period_end）
curl -s -X POST "$BASE/v1/trackers/$TRACKER/reports" \
  -H "Content-Type: application/json" \
  -d '{"from":"2026-11-01","to":"2026-11-03"}'
```

## 6. 重复提交保护

首版不用 `Idempotency-Key` 去重，靠数据库约束兜底：

| 场景 | 约束 |
|---|---|
| 同一答案同一品牌只计一次提及 | `mention (answer_id, brand_id)` 唯一 |
| 同一文章同一渠道不重复入队 | `publication (article_id, channel_id)` 唯一 |
| 同一问题集内问题不重复 | `question (question_set_id, normalized_text)` 唯一 |
| 同一品牌别名不重复 | `brand_alias (brand_id, alias)` 唯一 |
| 采样项不重复 | `run_item (run_id, question_id, model_key, channel, sample_idx)` 唯一 |

## 7. 兼容性检查

- 契约测试用 Schemathesis 对实现跑属性测试，任何偏离 `openapi.yaml` 的响应都视为失败（T098，尚未执行）。
- 新增字段必须可选；删除字段、改类型、改枚举属于破坏性变更。
- `metric_version` 与接口版本独立：口径可以不改接口而升级版本，但响应里必须返回当前版本。
- 报告快照的 `payload_hash` 是 payload 规范化 JSON 的 sha256；改口径会改变哈希，可用于验证「快照未被改写」。
