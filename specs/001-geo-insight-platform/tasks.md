---
description: "Task list for GEO Insight 首版实现"
---

# Tasks: GEO Insight 品牌可见度监测平台

**Input**: Design documents from `/specs/001-geo-insight-platform/`

**Prerequisites**: plan.md（必需）、spec.md（用户故事）、research.md、data-model.md、contracts/、quickstart.md

**Tests**: 本清单包含测试任务，因为 spec 的 SC-003/005/007 与 quickstart.md 明确要求黄金样本回归、契约测试与 E2E；不采用全量 TDD，只在口径、契约与关键用户路径上设测试闸门。

**Organization**: 按用户故事分组，每个故事可独立实现、独立验证、独立交付。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 不同文件、无未完成依赖，可并行
- **[Story]**: 所属用户故事（US1 / US2 / US3）
- 每条任务都带精确文件路径

## 实现状态（2026-11-12 更新）

**已完成 27 条 `[X]`**：原有 16 条 + 文档/契约修复轮新增 10 条（T082、T083、T088、T089、T090、T092、T093、T096、T099、T101）+ 本轮 T103。其余 76 条按「面试范围声明」明确不执行。

**本轮新增工件**：`backend/src/geo/repositories/config_repository.py`、`report_repository.py`；`backend/src/geo/services/config_service.py`、`report_service.py`；`backend/src/geo/domain/methodology.py`；`backend/tests/integration/test_config.py`、`test_report.py`；前端 `settings.html`、`reports.html`、`report.html`、`answer.html`、`app.css`。

**环境与决策偏差**：PyPI 不可达，FastAPI / SQLAlchemy / Schemathesis 装不上；**Docker 明确不使用**（决策 002）；前端不使用 Next.js（决策 004）；报告 PDF 走浏览器打印（决策 005）。实现为 Starlette + 标准库 `sqlite3` + 零依赖静态页，接口形状以 `contracts/openapi.yaml` 为准，替换只发生在 repository / api / frontend 三层，口径纯函数不变。

**看板同源托管**：`api/app.py` 提供 `/`、`/static` 与各页面路由；`file://` 打开时给出明确提示条。`scripts/smoke.py` 有回归断言，修复前 `GET /` 为 404，断言确实会红。

**不适用**：T004（docker-compose 编排，决策 002）；T021（AI 成稿与积分，决策 003 取消，属 002 特性清单）。PG/CH/Redis 相关任务（T006、T013-T015、T020-T022）保留为规模化部署待办。

**本轮（2026-11-12 上午）**：补 T103 —— `seed.py` 内置 2 份演示报告，解决 `--reset` 后 `/reports` 空白导致演示中断；新增 smoke 断言并做反向验证。冷启动演练通过。

**仍未开始（按优先级）**：T087 真实提及解析器、T091 鉴权与租户隔离、T094 按周聚合、T098 契约回归工具链、T100 片段级情感及真实采集验证。这些依赖外部账号 / 依赖安装 / 真实数据，按用户要求暂缓，不阻塞面试演示。

---

## Path Conventions

- 后端：`backend/src/geo/`、`backend/tests/`、`backend/scripts/`
- 前端：`frontend/static/`（六页 + `theme.css` + `app.css`，无 `frontend/src/`、无构建工具，决策 004）
- 契约：`specs/001-geo-insight-platform/contracts/openapi.yaml` 为权威源，根目录 `contracts/openapi.yaml` 是同步副本
- 基础设施：无 `infra/docker-compose.yml`（决策 002 明确搁置）
- 启动：`backend/run.py`（单进程）与根目录 `start.bat`（Windows 双击）

**前端任务覆盖关系（决策 004）**：原 Next.js 任务与静态页交付的对应关系如下，同一工作不重复实现。

| 原任务 | 由谁替代 |
|---|---|
| T039 / T040 / T041 / T042 / T043 | `frontend/static/index.html`（指标卡、趋势、明细、信源、下钻）+ `answer.html` |
| T057 / T058 / T059 | `frontend/static/settings.html`（监控详情、创建、问题导入与分层） |
| T069 / T070 / T071 | `frontend/static/reports.html` + `report.html`（列表、快照、不可比提示） |
| T009 / T028 | 无构建工具，页面直接 `fetch` 相对路径 `/v1/...`，不需要生成的 API 客户端 |

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: 项目骨架、依赖、本地运行环境

- [X] T001 创建仓库骨架与根 README，目录包含 `backend/`、`frontend/`、`contracts/`、`infra/`，在 `README.md` 写明本地启动三步
- [ ] T002 初始化后端项目与依赖 in `backend/pyproject.toml`，固定 Python 3.12，声明 fastapi、pydantic v2、sqlalchemy 2、alembic、celery、redis、clickhouse-connect、pytest、schemathesis
- [ ] T003 [P] 初始化前端项目 in `frontend/package.json`，Next.js 15 App Router + TypeScript + Tailwind 4 + TanStack Query 5 + ECharts 5 + shadcn/ui
- [ ] T004 ~~[P] 编写本地编排 in `infra/docker-compose.yml`~~ **不适用（决策 002）**：本地运行改为单进程 `backend/run.py` + SQLite，不引入容器 （路径按决策 002/004 作废：该能力已由 `frontend/static/` 或 SQLite 实现替代；不要按原路径执行）
- [ ] T005 [P] 配置格式化与静态检查：`backend/pyproject.toml` 中启用 ruff + mypy，`frontend/.eslintrc.json` 与 `frontend/.prettierrc` 启用 eslint + prettier
- [ ] T006 初始化数据库迁移框架 in `backend/alembic.ini` 与 `backend/alembic/env.py`，连接串从 `GEO_DATABASE_URL` 读取 （路径按决策 002/004 作废：该能力已由 `frontend/static/` 或 SQLite 实现替代；不要按原路径执行）
- [X] T007 实现配置加载（标准库 dataclass + 环境变量，含 min_samples / 采样失败率阈值 / 无列表提示阈值）in `backend/src/geo/settings.py`
- [ ] T008 编写 CI in `.github/workflows/ci.yml`，包含后端 lint/单测、前端 lint/build、契约校验三步
- [ ] T009 [P] 配置 OpenAPI 客户端生成 in `frontend/openapi-ts.config.ts`，输入 `contracts/openapi.yaml`，输出 `frontend/src/lib/api/generated/` （路径按决策 002/004 作废：该能力已由 `frontend/static/` 或 SQLite 实现替代；不要按原路径执行）

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 所有用户故事共用的数据、口径、鉴权与任务基础设施

**⚠️ CRITICAL**: 本阶段完成前，任何用户故事都不能开始

- [ ] T010 创建基础实体迁移 in `backend/alembic/versions/0001_core_entities.py`，含 tenant、app_user、brand、brand_alias、competitor_set、competitor；`brand_alias` 加 `UNIQUE (brand_id, alias)`，`app_user` 加 `UNIQUE (tenant_id, email)` （路径按决策 002/004 作废：该能力已由 `frontend/static/` 或 SQLite 实现替代；不要按原路径执行）
- [ ] T011 创建监控与问题迁移 in `backend/alembic/versions/0002_tracker_questions.py`，含 tracker、question_set、question；`question` 加 `UNIQUE (question_set_id, normalized_text)`，`tier` 枚举 `core|long_tail`，默认 `long_tail` （路径按决策 002/004 作废：该能力已由 `frontend/static/` 或 SQLite 实现替代；不要按原路径执行）
- [ ] T012 创建采样迁移 in `backend/alembic/versions/0003_sampling.py`，含 sampling_plan、run、run_item；`run_item` 加 `UNIQUE (run_id, question_id, model_key, channel, sample_idx)`，`sample_idx` 限制 1..3，`core_times=3`、`long_tail_times=1` （路径按决策 002/004 作废：该能力已由 `frontend/static/` 或 SQLite 实现替代；不要按原路径执行）
- [ ] T013 创建答案与提及迁移 in `backend/alembic/versions/0004_answers.py`，含 answer、answer_block、mention、citation；`mention` 加 `UNIQUE (answer_id, brand_id)`（同答案同品牌只计 1 次），`citation` 不设唯一约束（同 URL 多次引用保留多行），`mention.position` 允许 NULL （路径按决策 002/004 作废：该能力已由 `frontend/static/` 或 SQLite 实现替代；不要按原路径执行）
- [ ] T014 创建报告与审计迁移 in `backend/alembic/versions/0005_reports.py`，含 report、report_snapshot、audit_log；`report_snapshot` 与 report 1:1，加 `payload_hash`，校验 `period_start < period_end` （路径按决策 002/004 作废：该能力已由 `frontend/static/` 或 SQLite 实现替代；不要按原路径执行）
- [ ] T015 创建 ClickHouse 表 in `infra/clickhouse/init.sql`，含 metric_daily（ReplacingMergeTree，按 `toYYYYMM(date)` 分区）、answer_fact、citation_fact（按 `ingested_at` 分区）
- [X] T016 定义领域枚举 in `backend/src/geo/domain/enums.py`，含 TrackerStatus、RunStatus、RunItemStatus、ReportStatus、Tier、Channel、Sentiment、ErrorCode，值与 data-model.md 一致
- [X] T017 [P] 实现口径纯函数 in `backend/src/geo/domain/metrics.py`，包含 `mention_rate`、`coverage_rate`、`avg_rank`、`top_n_rate`、`sentiment_distribution`、`wilson_interval`，每个函数返回 `{value, status, sample_base}`，`sample_base < 3` 时返回 `insufficient_samples`
- [ ] T018 [P] 准备黄金样本集 in `backend/tests/fixtures/golden/answers.jsonl`，200 条人工标注，覆盖有列表、无列表、否定句、多品牌、错写别名五种情况
- [X] T019 编写口径单测 in `backend/tests/unit/test_metrics.py`，断言 22.2%（2/9）、无列表样本排除、`sample_base=2` 返回 `insufficient_samples`、情感分母等于提及数
- [ ] T020 实现数据库会话与租户上下文 in `backend/src/geo/repositories/pg/session.py`，每次连接设置 `app.tenant_id` 以启用行级安全
- [ ] T021 [P] 实现 ClickHouse 客户端与批量写入 in `backend/src/geo/repositories/ch/client.py`，写入按 `answer_id` 幂等
- [ ] T022 实现鉴权与角色 in `backend/src/geo/api/deps.py`，解析 Bearer JWT，提供 `require_role("operator"|"admin")` 依赖，viewer 不得调用写接口
- [ ] T023 实现统一错误与审计 in `backend/src/geo/api/errors.py` 与 `backend/src/geo/services/audit_service.py`，错误体 `{code, message, request_id, details}`，写操作记录 `audit_log`
- [ ] T024 配置 Celery in `backend/src/geo/workers/celery_app.py`，定义 `aggregate` 与 `report` 两个队列，失败重试 2 次、指数退避
- [ ] T025 实现种子脚本 in `backend/src/geo/seed.py`，支持 `--fixtures golden`、`--scenario mention-2-of-9|no-list|two-samples`、`--reset`，供 quickstart.md 直接调用
- [X] T026 搭建单一 API 应用与路由表，挂载 trackers / questions / sampling / metrics / answers / citations / reports / meta / studio 九组接口 in `backend/src/geo/api/app.py`
- [ ] T027 接入结构化日志与 trace in `backend/src/geo/logging.py`，每条请求日志带 `request_id` 与 `tenant_id`
- [ ] T028 搭建前端布局与 API 客户端 in `frontend/src/app/layout.tsx` 与 `frontend/src/lib/api/client.ts`，统一注入 token、解析错误体、透传 `request_id` （路径按决策 002/004 作废：该能力已由 `frontend/static/` 或 SQLite 实现替代；不要按原路径执行）

**Checkpoint**: 数据库、口径、鉴权、队列、种子数据就绪，三个用户故事可以并行开始

---

## Phase 3: User Story 1 - 查看品牌在 AI 答案中的表现 (Priority: P1) 🎯 MVP

**Goal**: 用户打开监控详情，一屏看到提及数/提及率/平均排名/TopN/情感分布，并能下钻到具体答案与引用。

**Independent Test**: 用 `python -m geo.seed --fixtures golden` 导入样本后打开详情页，指标与 quickstart.md 第 2~4 节的预期一致；3 次点击内可看到某条答案的原文与引用。

### Tests for User Story 1

- [ ] T029 [P] [US1] 契约测试指标与明细接口 in `backend/tests/contract/test_metrics.py`，用 Schemathesis 校验 `/metrics/overview`、`/metrics/trend`、`/answers`、`/citations/summary`
- [X] T030 [P] [US1] 集成测试指标链路：2/9=22.2%、无列表排除、双样本样本不足、情感分母 per FR-015/FR-016/FR-018 in `backend/tests/integration/test_service.py`

### Implementation for User Story 1

- [X] T031 [P] [US1] 实现指标查询仓储（SQLite 聚合，按 tracker/question/model/channel/date 过滤）in `backend/src/geo/repositories/sqlite_repository.py`, `backend/src/geo/services/metric_service.py`
- [X] T032 [P] [US1] 实现答案与提及查询仓储，支持 question / model / 日期 / 引用范围过滤与分页 in `backend/src/geo/repositories/sqlite_repository.py`
- [X] T033 [P] [US1] 实现信源查询仓储：类型分布、媒体排行、URL 去重明细，`unknown_published_share` 单独计数 per FR-020/FR-026 in `backend/src/geo/repositories/sqlite_repository.py`
- [X] T034 [US1] 实现指标服务 in `backend/src/geo/services/metric_service.py`，组装 `MetricOverview`：`no_list_samples` 单独返回、每个指标独立判 `insufficient_samples`、`sentiment.base = mention_total`
- [X] T035 [US1] 实现 `GET /v1/trackers/{trackerId}/metrics/overview`：from/to 可选，返回 metric_version 与三件套 per FR-016/FR-018 in `backend/src/geo/api/app.py`
- [X] T036 [US1] 实现 `GET /v1/trackers/{trackerId}/metrics/trend` 与日粒度趋势 in `backend/src/geo/services/metric_service.py`, `backend/src/geo/api/app.py`
- [X] T037 [US1] 实现 `GET /v1/trackers/{trackerId}/answers` 与 `GET /v1/answers/{answerId}`，返回原文、提及、引用、推荐清单 in `backend/src/geo/api/app.py`
- [X] T038 [US1] 实现 `GET ...citations/summary` 与 `GET ...citations`，返回 unknown_published_share / video_share / URL 去重明细 per FR-020/FR-021/FR-026 in `backend/src/geo/repositories/sqlite_repository.py`, `backend/src/geo/api/app.py`
- [ ] T039 [P] [US1] 实现指标卡组件 in `frontend/src/components/metric-card.tsx`，`status=insufficient_samples` 时显示「样本不足」，平均排名旁显示「无列表样本 N 条」 （路径按决策 002/004 作废：该能力已由 `frontend/static/` 或 SQLite 实现替代；不要按原路径执行）
- [ ] T040 [P] [US1] 实现趋势图组件 in `frontend/src/components/metric-trend.tsx`，渲染 `model_changes` 标注，缺失数据点断线而不是补 0 （路径按决策 002/004 作废：该能力已由 `frontend/static/` 或 SQLite 实现替代；不要按原路径执行）
- [ ] T041 [P] [US1] 实现明细抽屉 in `frontend/src/components/answer-drawer.tsx`，展示答案原文、提及片段、引用列表，支持复制原文 （路径按决策 002/004 作废：该能力已由 `frontend/static/` 或 SQLite 实现替代；不要按原路径执行）
- [ ] T042 [US1] 实现信源面板 in `frontend/src/components/citation-panel.tsx`，展示类型环形图、媒体排行、内容明细表 （路径按决策 002/004 作废：该能力已由 `frontend/static/` 或 SQLite 实现替代；不要按原路径执行）
- [ ] T043 [US1] 组装监控详情页 in `frontend/src/app/trackers/[trackerId]/page.tsx`，含周期选择、模型/通道筛选、指标区、趋势区、明细入口 （路径按决策 002/004 作废：该能力已由 `frontend/static/` 或 SQLite 实现替代；不要按原路径执行）
- [X] T044 [US1] 补齐空状态、样本不足、请求失败态与口径说明抽屉 per FR-019/SC-008 in `frontend/static/index.html`, `frontend/static/app.css`
- [ ] T045 [US1] 编写 P1 E2E in `frontend/tests/e2e/metrics.spec.ts`，覆盖 quickstart.md 第 2~4 节三条场景

**Checkpoint**: US1 可独立演示：导入样本 → 看指标 → 下钻答案，不依赖配置流程

---

## Phase 4: User Story 2 - 配置监控对象与问题集 (Priority: P2)

**Goal**: 运营能创建监控对象、维护品牌别名与竞品、批量导入问题并标记核心/长尾，系统按 3/1 生成采样计划。

**Independent Test**: 从空白创建监控对象、导入 20 个问题、标记 5 个核心，详情返回 `{total:20, core:5, long_tail:15}`；重复导入返回 20 条 duplicates。

### Tests for User Story 2

- [ ] T046 [P] [US2] 契约测试配置接口 in `backend/tests/contract/test_trackers.py`，覆盖 `/trackers`、`/trackers/{id}/questions`、`/questions/{id}`
- [ ] T047 [P] [US2] 集成测试配置流程 in `backend/tests/integration/test_tracker_flow.py`，覆盖建对象、导入查重、层级修改、采样计划生成

### Implementation for User Story 2

- [ ] T048 [P] [US2] 实现监控对象仓储 in `backend/src/geo/repositories/pg/tracker_repository.py`，支持 draft/active/paused/archived 状态机，archived 不可回 active
- [ ] T049 [P] [US2] 实现问题仓储 in `backend/src/geo/repositories/pg/question_repository.py`，导入时按 `normalized_text` 查重，返回 created 与 duplicates
- [ ] T050 [US2] 实现监控对象服务 in `backend/src/geo/services/tracker_service.py`，创建时写入品牌、别名、竞品集合版本，未绑定问题集时返回 `has_questions=false`
- [ ] T051 [US2] 实现 `POST /v1/trackers` 与 `GET /v1/trackers` in `backend/src/geo/api/v1/trackers.py`，列表返回 `question_summary`
- [ ] T052 [US2] 实现 `GET /v1/trackers/{id}` 与 `PATCH /v1/trackers/{id}` in `backend/src/geo/api/v1/trackers.py`，状态变更走状态机校验，非法流转返回 409 `STATE_CONFLICT`
- [ ] T053 [US2] 实现问题导入与查询 in `backend/src/geo/api/v1/questions.py`，`POST` 最多 500 条，`GET` 返回 total/core/long_tail
- [ ] T054 [US2] 实现问题层级修改 in `backend/src/geo/api/v1/questions.py`，`PATCH` 修改 tier 后写入 `sampling_plan.effective_from = 次日`，不回改历史
- [ ] T055 [US2] 实现采样计划生成 in `backend/src/geo/services/sampling_service.py`，core=3、long_tail=1，按问题 × 模型展开 run_item
- [ ] T056 [US2] 实现 `POST /v1/runs` 与 `GET /v1/runs/{id}` in `backend/src/geo/api/v1/runs.py`，支持 `Idempotency-Key`，返回 planned/succeeded/failed/skipped
- [ ] T057 [P] [US2] 实现监控列表页 in `frontend/src/app/page.tsx`，展示名称、状态、问题数、核心/长尾数 （路径按决策 002/004 作废：该能力已由 `frontend/static/` 或 SQLite 实现替代；不要按原路径执行）
- [ ] T058 [US2] 实现创建与编辑表单 in `frontend/src/features/trackers/tracker-form.tsx`，含品牌别名多值输入、竞品多值输入、未绑定问题集提示 （路径按决策 002/004 作废：该能力已由 `frontend/static/` 或 SQLite 实现替代；不要按原路径执行）
- [ ] T059 [US2] 实现问题导入与分层 in `frontend/src/features/trackers/question-import.tsx`，支持粘贴/文件导入、行前缀 `*` 标记核心、重复项高亮 （路径按决策 002/004 作废：该能力已由 `frontend/static/` 或 SQLite 实现替代；不要按原路径执行）
- [ ] T060 [US2] 编写配置流程 E2E in `frontend/tests/e2e/tracker-setup.spec.ts`，覆盖创建、导入 20 条、标 5 核心、计数校验

**Checkpoint**: US2 完成后，US1 可以接真实配置的数据；两个故事互不阻塞

---

## Phase 5: User Story 3 - 生成并交付周期报告 (Priority: P3)

**Goal**: 选周期生成报告，60 秒内导出 PDF；报告是快照，不可比场景有明确标注。

**Independent Test**: 给定已有指标数据，生成报告并导出 PDF；修改库中后续数据后重新打开，数字与 `payload_hash` 不变；模型集合不一致时 `comparable=false`。

### Tests for User Story 3

- [ ] T061 [P] [US3] 契约测试报告接口 in `backend/tests/contract/test_reports.py`，覆盖 `POST /trackers/{id}/reports`、`GET /reports/{id}`、`POST /reports/{id}/export`
- [ ] T062 [P] [US3] 集成测试报告快照 in `backend/tests/integration/test_report_flow.py`，覆盖快照不变、不可比标注、幂等重试

### Implementation for User Story 3

- [ ] T063 [P] [US3] 实现报告仓储 in `backend/src/geo/repositories/pg/report_repository.py`，report 与 report_snapshot 1:1，写入后只读
- [ ] T064 [US3] 实现报告快照生成 in `backend/src/geo/services/report_service.py`，聚合指标、趋势、信源、结论页，写入 `payload` 与 `payload_hash`
- [ ] T065 [US3] 实现可比性判断 in `backend/src/geo/services/report_service.py`，比较 `model_set_hash`、`metric_version`、`competitor_set_version`、样本量，返回 `comparable` 与 `incomparable_reasons`
- [ ] T066 [US3] 实现 `POST /v1/trackers/{id}/reports` 与 `GET /v1/reports/{id}` in `backend/src/geo/api/v1/reports.py`，生成走 Celery，接口返回 202
- [ ] T067 [US3] 实现 PDF 导出 in `backend/src/geo/services/export_service.py`，用 HTML 模板 + Gotenberg 渲染，输出 `download_url` 与 `expires_at`
- [ ] T068 [US3] 实现重试幂等 in `backend/src/geo/workers/report.py`，同一 `Idempotency-Key` 不产生第二份报告，失败重试不重复占用配额
- [ ] T069 [P] [US3] 实现报告列表与生成入口 in `frontend/src/features/reports/report-list.tsx`，展示周期、状态、可比性 （路径按决策 002/004 作废：该能力已由 `frontend/static/` 或 SQLite 实现替代；不要按原路径执行）
- [ ] T070 [US3] 实现报告详情 in `frontend/src/app/reports/[reportId]/page.tsx`，渲染快照、样本量、口径版本、生成时间 （路径按决策 002/004 作废：该能力已由 `frontend/static/` 或 SQLite 实现替代；不要按原路径执行）
- [ ] T071 [US3] 实现不可比提示 in `frontend/src/features/reports/incomparable-banner.tsx`，按原因展示文案，隐藏涨跌幅 （路径按决策 002/004 作废：该能力已由 `frontend/static/` 或 SQLite 实现替代；不要按原路径执行）
- [ ] T072 [US3] 编写报告 E2E in `frontend/tests/e2e/reports.spec.ts`，覆盖生成、导出、快照不变、不可比提示

**Checkpoint**: 三个故事全部可独立演示，且能串联成完整交付

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: 跨故事的验证、性能、安全与交付文档

- [ ] T073 全量契约回归 in `backend/tests/contract/`，Schemathesis 跑 `contracts/openapi.yaml` 全路径，失败即阻断发布
- [ ] T074 黄金样本回归 in `backend/tests/integration/test_golden_regression.py`，断言提及一致率 ≥ 90%、情感一致率 ≥ 85%（SC-003）
- [ ] T075 性能压测 in `backend/tests/perf/k6-dashboard.js`，看板首屏 P95 ≤ 2s，报告生成 ≤ 60s（SC-002/005）
- [ ] T076 权限矩阵测试 in `backend/tests/integration/test_permissions.py`，viewer 不可写、跨租户不可见、archived 不可激活
- [ ] T077 数据保留与归档 in `backend/src/geo/workers/retention.py`，answer/citation 180 天后归档到对象存储，audit_log 保留 365 天
- [ ] T078 补齐审计覆盖 in `backend/src/geo/services/audit_service.py`，所有写操作记录 before/after，导出记录操作人
- [ ] T079 文档与运行手册 in `README.md` 与 `docs/runbook.md`，写启动、备份、采集未启用的说明与回滚步骤
- [ ] T080 按 quickstart.md 全流程验收 in `specs/001-geo-insight-platform/quickstart.md`，逐条勾选并在 PR 描述附结果
- [ ] T081 清理与重构后端入口与配置 in `backend/src/geo/settings.py` 与 `backend/src/geo/api/v1/router.py`，删除未使用依赖、统一命名、补类型注解，确保 mypy 无 error

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 无依赖，立即可做
- **Foundational (Phase 2)**: 依赖 Setup，**阻塞所有用户故事**
- **US1 / US2 / US3**: 都依赖 Foundational；三者之间无强依赖，可并行
- **Polish**: 依赖所需故事完成

### User Story Dependencies

- **US1 (P1)**: Foundational 完成即可开始，不依赖其他故事；用种子数据独立验证
- **US2 (P2)**: Foundational 完成即可开始；与 US1 无代码依赖，但 US1 的看板可以消费 US2 的配置数据
- **US3 (P3)**: Foundational 完成即可开始；报告读取 `metric_daily`，与 US1 共享口径函数，但不依赖 US1 的接口实现

### Within Each User Story

- 契约/集成测试先写并确认失败（若采用 TDD）
- 仓储 → 服务 → 接口 → 前端 → E2E
- 每个故事在 Checkpoint 处独立验收

---

## Parallel Example: User Story 1

```bash
# 测试可并行
Task: "契约测试指标与明细接口 in backend/tests/contract/test_metrics.py"
Task: "集成测试指标链路 in backend/tests/integration/test_metrics_flow.py"

# 三个查询仓储可并行
Task: "实现指标查询仓储 in backend/src/geo/repositories/ch/metric_repository.py"
Task: "实现答案与提及查询仓储 in backend/src/geo/repositories/ch/answer_repository.py"
Task: "实现信源查询仓储 in backend/src/geo/repositories/ch/citation_repository.py"

# 前端三个组件可并行
Task: "实现指标卡组件 in frontend/src/components/metric-card.tsx"
Task: "实现趋势图组件 in frontend/src/components/metric-trend.tsx"
Task: "实现明细抽屉 in frontend/src/components/answer-drawer.tsx"
```

---

## Implementation Strategy

### MVP First (US1 Only)

1. Phase 1 + Phase 2（口径与数据是硬前置）
2. Phase 3（US1）
3. **停下来验证**：按 quickstart.md 第 2~4 节跑一遍，指标与人工核对一致
4. 可对内演示，但不对外交付（没有配置入口，数据靠种子）

### Incremental Delivery

1. Setup + Foundational → 底座就绪
2. + US1 → 看得见（MVP）
3. + US2 → 能自助配置，可给第一个客户试用
4. + US3 → 能交付报告，可收费
5. Polish → 性能、权限、归档、文档补齐

### Parallel Team Strategy

- 1 人：按 T001→T081 顺序，使用 P1 优先
- 2 人：A 做 Phase 1/2 + US1，B 在 Foundational 后做 US2 前端与 US3 报告
- 3 人：Foundational 后一人一个故事；US1 与 US3 需先对齐 `metric_service` 的返回结构，避免各自解释

---

## Notes

- [P] 任务 = 不同文件、无未完成依赖
- [Story] 标签用于追溯，Phase 1/2/6 不带
- 口径函数是三个故事的共同基础，任何改动都要跑黄金样本回归
- 每个 Checkpoint 都要能独立演示，不允许「等另一个故事做完才能看」
- 真实采集不在本清单内；进入实现前需单独立项验证登录态、风控与 DOM 稳定性
- 提交粒度：每条任务或每组逻辑任务一次提交，提交信息引用任务 ID

---

## Phase 7: Convergence

> 对照 spec.md（FR-001~035、SC-001~008）、plan.md 与当前代码的差距，仅追加、不改动任何既有任务。
> **宪法复核（2026-11-12，v1.1.0 已批准，不再跳过）**：原则 I/II/IV/VI 合规；原则 III 有缺口（合规命中未落审计表）；原则 V 有 1 处已知偏离（`publish_article` 的渠道校验仍在 handler）。逐条结论见 `plan.md` 的 Constitution Check。
> 数据库层差异（PostgreSQL / ClickHouse）不计入缺口：已由 `docs/decisions/002-local-run-no-docker.md` 明确搁置。
> 契约缺口 U1/U2/U3 已补齐：`contracts/openapi.yaml` v0.2.0 增加别名、竞品、采样计划、口径说明、指标样本下钻、答案详情、批量导出、信源明细等路径；PDF 按决策 005 走浏览器打印，服务端导出路径已从契约移除。
> 编号按追加顺序生成，**不是执行顺序**（T087 之后出现 T100/T101/T088 属正常）。重复任务已在各条末尾标注其覆盖的既有任务编号，表示同一份工作，不重复实现。

### 配置能力（US2 写路径，当前只有读接口）

- [X] T082 实现监控对象与品牌别名的写接口（创建/编辑/停用、别名增删），路由与状态码对齐 `contracts/openapi.yaml` per FR-001/FR-002 (missing) in `backend/src/geo/api/app.py`, `backend/src/geo/repositories/sqlite_repository.py` （已实现；覆盖 T050-T051，同一工作不重复做）
- [X] T083 实现问题集创建、批量导入去重与层级修改（导入返回新增/重复数，层级变更次日生效）per FR-004/FR-005/FR-012 (missing) in `backend/src/geo/services/question_service.py`, `backend/src/geo/api/app.py` （已实现；覆盖 T052-T054，同一工作不重复做）
- [ ] T084 实现问题集绑定、竞品集版本记录与「未绑定问题集不会产生数据」提示 per FR-003/FR-006/FR-007 (missing) in `backend/src/geo/services/tracker_service.py` （部分：竞品版本已实现；问题集绑定与「未绑定」提示未做）
- [ ] T085 实现采样计划生成与 run 触发接口（核心 3 次/日、长尾 1 次/日，幂等键五元组）per FR-008/FR-009 (missing) in `backend/src/geo/services/sampling_service.py` （部分：采样计划查询已实现；run 触发接口未做；覆盖 T055-T056 的计划部分）
- [ ] T086 实现采样失败率统计与单问题手动补采（补采记录可区分来源）per FR-010/FR-011 (missing) in `backend/src/geo/services/sampling_service.py`, `backend/src/geo/api/app.py` （部分：失败率统计已实现；单问题手动补采未做）

### 解析与口径

- [X] T087 实现提及识别与列表位次解析器：别名/错写匹配、有序列表序号、无列表判定，并接入黄金样本回归 per FR-013/FR-014 in `backend/src/geo/domain/mention_parser.py`
  **2026-11-24 完成（003 US4 收编）**：精确/归一化匹配 + misspell 人工清单 + 有序列表行号解析；一键清空重跑走 `POST /v1/trackers/{id}/mentions/recompute`（服务层 `mention_service`，审计 `mention.recompute`）。30 条人工标注样本 P/R = 100%（FR-008 口径 ≥80%），黄金窗口 9 样本 2 提及在整库重跑后不变（`tests/integration/test_mentions.py`）。
- [ ] T100 实现情感片段级聚合：按片段多数取值，平票按负面>中性>正面，保留全部片段 per FR-017 (partial) in `backend/src/geo/domain/metrics.py` （未做：情感按答案单值聚合，片段多数与平票规则未落实现有样本结构）
- [X] T101 实现引用明细按 URL 去重（计数仍按出现次数累计）per FR-020 (partial) in `backend/src/geo/repositories/sqlite_repository.py` （已实现：/v1/trackers/{id}/citations 按 URL 去重、次数累计）

### 报告（US3 完全缺失，表已存在）

- [X] T088 实现报告生成与不可变快照存储（指标概览、趋势、信源、结论、样本量与口径版本）per FR-027/FR-028 (missing) in `backend/src/geo/services/report_service.py` （已实现；覆盖 T064-T065，同一工作不重复做）
- [X] T089 实现报告可比性判断：模型集合、竞品集合、口径版本不一致时标注不可比且不输出涨跌幅 per FR-029 (missing) in `backend/src/geo/services/report_service.py` （已实现；覆盖 T066，同一工作不重复做）
- [X] T090 实现 PDF 导出与失败重试幂等（不产生重复报告、不重复占配额）per FR-030/FR-031 (missing) in `backend/src/geo/services/report_service.py` （已实现：PDF 走浏览器打印=决策 005，重试幂等；积分已按决策 003 取消；覆盖 T067-T068）

### 权限与数据生命周期

- [ ] T091 实现登录会话与三角色校验（管理员/运营/只读），租户从会话派生而非请求头 per FR-032/FR-033 (missing) in `backend/src/geo/api/auth.py`, `backend/src/geo/api/app.py` （未做：首版租户由默认配置派生；覆盖 T022/T076 的目标，留待多租户阶段）
- [ ] T097 实现监控对象停用/二次确认删除与原始答案批量导出（含 180 天保留期校验）per FR-034/FR-035 (missing) in `backend/src/geo/services/tracker_service.py` （部分：停用/归档状态机与 CSV/JSONL 导出已实现；彻底删除二次确认未做）

### 看板交互（接口已有，页面缺）

- [X] T092 在看板增加口径说明面板：分母、位次定义、去重规则，2 次点击可达 per FR-019/SC-008 (partial) in `frontend/static/index.html` （已实现；覆盖 T041，同一工作不重复做）
- [X] T093 实现指标卡→构成样本列表→单条答案原文的下钻链路 per FR-023/SC-004 (partial) in `frontend/static/index.html`, `frontend/static/answer.html` （已实现；覆盖 T042，同一工作不重复做）
- [ ] T094 趋势支持按周聚合，并在模型集合变化处标注变更点 per FR-024 (partial) in `backend/src/geo/services/metric_service.py`, `frontend/static/index.html` （部分：日粒度趋势已实现；按周聚合与模型变更标注未做）
- [ ] T095 问答明细补齐引用类型筛选、答案原文与引用来源侧栏 per FR-022 (partial) in `frontend/static/index.html` （部分：答案原文与引用侧栏已实现；引用类型筛选未做）
- [X] T096 补齐信源明细与媒体排行视图（当前只有 summary 接口）per FR-025 (partial) in `backend/src/geo/api/app.py`, `frontend/static/index.html` （已实现；覆盖 T043 的媒体排行与 URL 明细部分）

### 工程质量

- [ ] T098 用 Schemathesis 对 `contracts/openapi.yaml` 跑契约回归，失败项修到一致 per plan: API 契约 (missing) in `backend/tests/contract/test_openapi.py` （未做：PyPI 不可达装不了 Schemathesis；改用 smoke HTTP 断言 + Node 字段契约检查；覆盖 T073 的目标）
- [X] T099 无列表样本占比超过阈值时在看板给出数据质量提示（阈值默认 50%，可配置）per plan: 数据质量策略 (missing) in `backend/src/geo/settings.py`, `frontend/static/index.html` （已实现；阈值由 /v1/meta/methodology 下发，映射 FR-015）
- [ ] T102 记录正文合规词命中到 `audit_log`（含文章 ID、命中词、时间、操作人），补齐宪法原则 III 的审计缺口 per constitution: 原则 III (missing) in `backend/src/geo/services/content_service.py`, `backend/src/geo/repositories/sqlite_repository.py`
- [X] T103 种子数据内置 2 份演示报告（不可比 / 可比 +23.3pp），保证 `--reset` 后报告中心不为空 per SC-007 in `backend/src/geo/seed.py`, `backend/scripts/smoke.py`

---

## 附录 A：成功标准的可执行映射（2026-11-12 补）

`spec.md` 的 SC-001/005 已改为可判定表述；下表把 8 条 SC 映射到实际可跑的检查，避免「有任务但无断言」。

| SC | 验收动作 | 当前可执行检查 |
|---|---|---|
| SC-001 | 10 名试用运营中 ≥9 人 10 分钟内完成建对象→导入 20 题→标 5 核心 | `test_config.py` 的导入去重与层级断言 + `settings.html` 现场演示；**真人试用未做** |
| SC-002 | 指标与人工标注一致 | `test_metrics.py` 13 条口径单测；200 条黄金样本仍未建立（T018） |
| SC-003 | 口径回归 | `test_service.py`、`test_report.py` + smoke 的 rank samples / no-list 分母断言 |
| SC-004 | 3 次点击内看到答案原文与引用 | `/` 指标卡 → 样本下钻 → `/answer/{id}`；`backend/scripts/pagecheck.mjs` 逐字段校验 |
| SC-005 | 60 秒内生成报告、打印 PDF 逐字段一致 | smoke 报告生成 + 哈希断言；`report.html` 打印同源渲染；**真人打印比对未做** |
| SC-006 | 报告不可比时不给涨跌 | `test_report.py` 模型集合不一致断言 + smoke 首周期不可比断言 |
| SC-007 | 演示数据可确定性重建 | `python -m geo.seed --reset`，smoke 每次用独立临时库 |
| SC-008 | 口径说明 2 次点击可达 | `/` 顶栏「口径说明」抽屉读 `/v1/meta/methodology`，smoke 有断言 |

## 附录 B：宪法门禁复核记录

- 时间：2026-11-12；宪法版本：v1.1.0（6 条原则）。
- 结论：I / II / IV / VI PASS；III PASS 有缺口（合规命中记录未落 `audit_log`）；V PASS 有 1 处偏离（`publish_article` 业务校验在 handler）。
- 处理：缺口与偏离保留为已知项，不伪装成通过；下次 `/speckit-converge` 必须重新核对这两条。
- 依据：`plan.md` 的 Constitution Check、`docs/decisions/003~005`、`scripts/smoke.py` 的负向断言与反向验证记录。

---

## 本轮实现范围声明（面试版，2026-11-12）

用户明确要求：**页面先搭好就行；爬虫类、付费类、麻烦的功能不做**。据此把剩余 76 条待办分成三类，避免清单看起来像「还有一堆没做完」。

**A. 已完成、可演示（27 条 `[X]`）**：13 个页面、30 个接口、指标口径与下钻、配置写路径、报告快照与打印导出、演示数据（含 2 份报告）。

**补充 1（侧栏与页面扩容）**：按客户截图把侧栏扩成四组（工作台 / 优化执行 / 分析报告 / 洞察工具），由 `backend/src/geo/api/nav.py` 服务端统一注入，新增 `/monitor`、`/insight`、`/opportunities`、`/tasks`、`/assets`、`/sources` 六个接真实接口的页面。历史页面的三种 aside 写法曾导致 `/studio` 与 `/report/{id}` 出现双份侧栏，已放宽注入正则并加 13 页结构断言；3 个导出链接从 `href="#"` 改为按钮 + 真实下载地址。

**补充 2（2026-11-12 上午，用户要求「打开看一下」）**：用系统 Chrome 无头渲染 13 页并逐张看图，发现三层检查都漏掉的真实问题：
- `/insight` 三个面板全报 `Cannot read properties of undefined (reading 'slice')`：`/metrics/trend` 返回 `date`，页面写成 `points[i].day`；且一个大 `try/catch` 把三个面板一起刷成错误。改为安全取值 + 每个面板独立兜底；趋势图把 `null`（样本不足）画成「—」而不是 0%，避免与结论口径打架；新增「本期结论」面板（4 条，全部由已取到的接口推导）。
- `/assets` 的「数据首日/末日」显示「无」：`config_service.tracker_detail` 把仓储层返回的 **tuple** 直接塞进响应，JSON 变成数组，页面按对象读 `first_day` 静默失败。改为对象，与 `openapi.yaml` 一致。
- `/insight` 信源分布的紫色进度条根本没渲染：`.barfill` 是 inline `<span>`，`width/height` 不生效；同时 `14 · 50.0%` 折行。已块化并加宽数值列。
- 按「内容要足够多」补实三页：`/opportunities` 加问题×模型覆盖矩阵 + 14 行样本明细（日期可点开答案原文）；`/tasks` 加文章明细表；`/assets` 加问题覆盖表。全部复用已有接口，未造数据。
- **补一层检查**：新增 `backend/scripts/browsercheck.mjs`（初版 19 条，现已扩到 27 条），调系统 Chrome `--dump-dom` 验证渲染后的 DOM 无报错、无占位残留、关键表格/图表确实存在。此前 HTTP 200 + `node --check` 都证明不了「页面 JS 真的跑通」，这次正是从这里漏过去的。检查自身也修了两处误报：脚本源码里的 `notice error` 字面、用假 ID 请求详情页。
- 反向验证：修复前 `/insight` 截图确有三个红色错误条、`data_range` 响应是数组、进度条只有灰轨道 —— 新检查在这些状态下均为红。

**补充 3（用户反馈「点击首页后的侧边变得很奇怪」）**：用 900 / 1100 / 1600 三档窗口截图复现，定位到三处叠加：
- `app.css` 的 `@media (max-width: 1180px)` 直接 `aside.side { display:none }` 并让 `.shell` 变单列 —— 预览面板/笔记本宽度下**整块导航凭空消失**。改为 1180px 时侧栏收窄到 168px 仍保留；≤760px 改成顶部横向导航而非隐藏。
- `studio.html` **没有引入 `app.css`**，自带一套旧 `aside`/`nav` 样式，导致该页侧栏分组、图标、底部与其余 11 页不一致；它的内联媒体查询还有 `aside { display:none }`（连右侧抽屉都会被隐藏）。已引入统一 app.css、删除冲突的 8 条旧规则、只保留 `.studio` 三栏转单列。
- 窄屏连带挤压：首页顶栏「口径说明」换行到第二行；「信源分布」标题被长 badge 压成两行；趋势/信源用内联栅格比例在 900px 下右列只剩 ~260px，表格被截断。改 `header.top` 窄屏收起副标题、`.panel > h2` 允许 badge 换行而不压标题、`.main-split` ≤1000px 转单列。
- 顺手修两处首页视觉瑕疵：`citeBadge` 往 badge 里再插一个 badge 导致「…18 个 URL 18 个 URL…」重复文字；情感倾向卡在样本不足时仍渲染空灰色进度条。
- 新增 26 条静态样式契约（13 页必须引 theme.css + app.css、不得内联隐藏侧栏、app.css 不得隐藏侧栏、中等宽度保留侧栏列）。做了变异检查：把旧规则喂给同一批正则，8 条自检全部红/不误判，证明不是空断言。
- 三档截图逐张复看：900px 下侧栏四组与底部「数据范围」齐全、顶栏一行、趋势与信源上下排列不截断、无重复文字与灰条；`/studio` 侧栏与其它页一致。

**补充 4（用户反馈「每个页面有种头重脚轻的感觉」）**：用 1600×1400 长截图诊断，定位到两个结构原因并各修一处：
- **grid 默认 stretch 把矮面板拉到和邻居一样高**，多出来的高度变成面板内部空白。首页趋势面板 SVG 只有 150px，却被右侧更长的信源面板拉高，下方露出大片空白。改 `.grid { align-items: start }`，面板高度由内容决定。
- **页脚停在内容末尾、不吸底**，内容短的页面（如报告中心 y=200 以下 1200px 全空）下方仍是一片空白。改 `main { display:flex; flex-direction:column }` + `.content { flex:1 }`，让统一页脚沉到视口底部收口。
- 统一页脚由 `nav.py` 的 `PAGE_FOOTER` 服务端注入（口径规则 + 数据范围 + 演示态声明），13 页只有一份定义，幂等注入不重复。
- 定点补实最空的三页（全部复用已有接口）：首页趋势面板加每日摘要表（日期/有效样本/提及数/提及率/平均排名）；`/insight` 加「分问题表现」表（按问题聚合提及率与各模型提及/样本）；`/reports` 加 5 张概览卡 + 「快照与口径明细」表（模型集合 hash、竞品集合版本、快照哈希、结论数）。报告中心文件体积 60KB → 115KB。
- browsercheck 的 EXTRA 断言扩到 23 条（初版 19 条，+首页摘要表、insight 分问题、reports 概览卡与快照明细）。**又踩一次检查写太严**：正则写 `id="trendTable">`，实际 DOM 里元素还带 `style` 属性，导致功能正常但断言假红；改为 `id="x"[^>]*>`，并做变异自检（未渲染占位判红 / 带属性判绿 / 无表格仍判红）。
- 验证：单测 35 / 冒烟 73 / 页面契约 67 / 浏览器 DOM 23 全绿；页脚注入 4 条幂等自检通过；reports、insight、home 长截图逐张复看，页面底部均有页脚收口，内容密度明显提升。

**补充 5（用户反馈「点击任务发布会跳转到优化任务」+「拿什么投面试」）**：
- 「任务发布」原先是锚点 `/tasks#publications`，和「优化任务」进同一个页面，两个入口看着像 bug。改成独立页面 `/publish`：创作进度归 `/tasks`，投放与效果归 `/publish`。新页面含 4 张概览卡（发布记录 / 已连接渠道 / 有发布记录的文章 / 发布后平均提及率变化）、发布记录表（含渠道、状态、回链、失败原因）、渠道状态表（7 个渠道：登录方式、账号、最后检查）、发布前后效果对比（before→after 提及率、delta_pp、可比性、说明）。数据全部来自已有接口 `/trackers/{id}/articles`、`/articles/{id}`、`/articles/{id}/impact`、`/channels`。
- 新增 `docs/interview/面试交付包.md`：项目定位、两版简历项目描述（工程向 / 产品向）、3 分钟演示脚本（按页面逐段讲什么）、7 个追问怎么答（数据哪来的 / 为什么不用 Docker / 快照为什么不可变 / 样本不足为什么显示「样本不足」 / 怎么保证页面不假 / 竞品 / 为什么不做鉴权）、主动交代的未做清单、按性价比排序的建议补功能。
- 对「功能还是没咋做」给出判断：核心链路（监测→机会→内容→发布→复盘）是通的，**真正会被追问击穿的是「数据哪来的」这一环**（当前是种子数据）。建议优先补「数据导入」（采集层可插拔，1 天工作量）与「竞品横向对比」（数据已有，纯聚合展示，1 天），而不是堆页面数量。
- 验证：单测 35 / 冒烟 73 / 页面契约 70 / 浏览器 DOM 27 全绿（新增 /publish 页的 3 条渲染断言 + 1 条页面断言 + 2 条样式契约）；20 个 JS 块语法零错。

**补充 6（用户定调「做好页面给他们看能搭出来，功能入职后再完善」——把数据做厚）**：
- 判断：页面框架没问题，但每个面板只有一两行数据，看着像空壳。**框架是对的，肉没长上**。方案：黄金窗口（11-01~05，口径演示）逐字节不动，只往后追加富数据：问题 2 → 12（4 核心 + 8 长尾）、数据 5 天 → 30 天、答案 14 → 1302 条、竞品 2 → 5、文章 3 → 8、发布记录 2 → 6、报告 2 → 5、引用 ~3600 条（含 30 个热点 URL 被反复引用）。
- 故事线：11-04 内容发布 → 提及率从 ~26% 爬坡到 ~41%；11-06、11-22 各一次采集中断（当日 run_item 全 failed，不进指标分母，趋势留缺口）；/publish 有发布成功、渠道审核驳回（合规原因）、定时待发布三种状态；报告环比有涨有跌（+23.3 / +4.8 / -0.3 / +5.7pp）。
- 断言策略：黄金窗口字面量全部保留（19 项手算复核 PASS）；富周期用关系断言（valid_total=1288、晚于内容的提及率 > 早于内容、totals 全量）；字面量更新项均先手算再上（daily_planned=60、failed=214、questions 4/8、articles=8、competitors=5）。
- **看图抓出的真 bug（HTTP/单测层全绿也拦不住）**：① `/monitor` 全量拉 100 条再前端过滤，「14 条答案」实为 196 条里的残片 → 改服务端 `questionId` 过滤 + 真实总数；② 信源明细 `total_urls/total_occurrences` 只在 LIMIT 100 行上求和 → 「3254 次引用只报 100 次出现」，改全量 SQL；③ 热点 URL 缺失导致「次数累计」每行都是 1、特性不可见 → 种子里 25% 引用复用 30 个热点 URL；④ 趋势图 30 点每点标数值糊成一团（工作台与 insight 同款）→ 标签抽稀；⑤ insight 迷你图标签同款问题未随工作台一起修。
- 测试侧联动：2 条单测字面量跟种子走（attempted 16→1516、failed 2→214）；「观察期未满」提示的触发条件因数据覆盖 11-06 而消失，**没有删断言**，改用 ART_PUB3 + 14 天窗口仍能触发该提示，保留回归保护；新增 totals 全量口径测试（先写了一行恒真废断言，自查后删掉，改为与 DB 直接比对）。
- 验证：单测 36 / 冒烟 78 / 页面契约 70 / 浏览器 DOM 27 全绿；黄金窗口 19 项手算复核逐项 PASS；13 页 1600×1400 逐张看图（含修复后复看）。

**补充 7（用户反馈「无限下滑，应该搞成 10 个一页的分页」）**：
- 问题：明细表全量渲染（样本明细 1302 行、问答明细、信源明细 100 行、每日摘要 30 行），页面被拉得无限长。统一加 **10 行/页分页**。
- 方案分两档：数据已在手的表（每日摘要、覆盖矩阵、样本明细、分问题表现）用 `GEO.pagerSlice` 客户端分页；接口本来就支持 page 的（答案列表、信源明细）用服务端分页——**「共 2435 条」必须真能翻到第 244 页**，不能又是只翻前 100 的假总数（与 /monitor「14 条答案」同类问题）。为此给 `/citations` 接口补了 `page/limit` 参数（仓储层 OFFSET），默认行为不变。
- 公共组件进 `app.js`（pagerInit/pagerNav/pagerSlice/pagerReset + document 级点击委托），5 个页面统一接入；换查询条件（日期/问题/对象）必须 `pagerReset` 回第 1 页。**踩坑：首页 index.html 是自包含的、没引 app.js，直接调 `GEO.pagerSlice` 报 `GEO is not defined`，三个面板卡在「加载中」**——共享组件的页面必须真的加载共享脚本，browsercheck 当场抓到（pending=3）。
- 验证：单测 36 / 冒烟 80（+2 条分页断言：答案与信源翻页不重叠、总数一致）/ 页面契约 70 / 浏览器 DOM 32（+5 条翻页条渲染断言）/ **pagercheck 14 条**（新：桩 DOM 加载真实 app.js 模拟点击，验 10 行/页、翻页换行、边界禁用、reset 回第 1 页）。

**补充 8（2026-11-12 下午，面试前修复：第二个监控对象 + 页码直达 + 面板空白）**：
- **监控对象切换器只有 1 个选项** → `seed_alt_tracker()` 新增「华辰教育 · 教培」（教育培训/北京，4 题 2 核心 + 2 长尾，12 天 208 条答案，3 个竞品，提及率 25%→50% 爬坡）。黄金对象 1111... 数据一行不动，断言锁在黄金 ID 上（tracker list 断言改 ==2 + 按 ID 查找黄金对象）。**踩坑：citation.owner_type 有 CHECK 约束 `IN ('brand','competitor','media')`，备选池用了 `ugc/professional` 直接 IntegrityError** —— 与黄金池同样的坑，schema 约束必须先查再写数据。
- **信源明细 244 页只能「下一页」** → `pagerNav` 加页码输入框 + 跳转按钮（pages > 3 时显示），点击/Enter 均可跳转，越界钳制到 [1, pages]。
- **面板高度不齐留白**（用户截图指出「左下空白」）→ 根因：`.grid { align-items: start }` 让矮面板只包住内容，同排邻居更高时左下露出页面底色。修法：`.c2/.c3/.main-split` 等高（`align-items: stretch`），图表**按容器实际像素尺寸绘制**（viewBox 1:1，文字不变形），`body.fill` 类标记需要拉伸的图表容器。两层配合：外部空隙收进卡片、内部图表填满不留白。
- **pagercheck 踩坑**：桩 DOM 的 `closest()` 对任何选择器都返回按钮，导致 `[data-pager-jump]` 误命中普通翻页按钮 —— 4 条翻页断言假红。修桩：只在选择器真正匹配时返回节点（与真实 DOM 语义一致）。
- 验证：单测 36 / 冒烟 80 / 页面契约 70 / 浏览器 DOM 32 / **pagercheck 20 条**（+6 条页码直达：跳转框出现/跳转/越界钳制/Enter/≤3 页不显示）全绿；黄金窗口 11 项手算逐项 PASS；截图验证洞察页两面板等高、图表填满、信源页跳转框出现。

**B. 本轮明确不执行（用户授权暂缓，不是遗漏）**

| 类别 | 任务 ID | 原因 |
|---|---|---|
| 真实采集 / 爬虫 / 网页端登录态 | T087、T085(run 触发)、T086(手动补采)、T091 相关 | 需要平台账号与授权，风控与 DOM 稳定性不可控；宪法原则 II 要求不提供空壳接口 |
| 付费 / 计费 / AI 成稿 | 决策 003 已整体取消；002 的 T019~T021 | 与「人工成稿 + 系统核对」的定位冲突，且按次计费与「被 AI 推荐」的价值无关 |
| 鉴权 / 多租户隔离 | T091、T022、T076 | 单人本地演示不需要；上线前必须补 |
| 重型工程质量 | T098(Schemathesis)、T018(200 条黄金样本)、E2E/Playwright、T100(片段级情感) | PyPI 不可达或依赖真实标注数据；当前用 80 条 HTTP 断言 + 36 条单测 + 70 条页面字段/样式契约 + 32 条浏览器 DOM 检查 + 20 条分页组件行为测试替代。表单提交等完整交互流仍未覆盖 |
| 规模化存储 | T006、T013~T015、T020~T021、迁移链 | 决策 002：本地 SQLite 单文件，PG/CH/Redis 是数据量上来后的形态 |
| 其他延后 | T084(问题集绑定)、T094(按周聚合)、T095(引用类型筛选)、T097(二次确认删除)、T102(合规审计落库) | 不影响面试演示路径 |

**C. 验收口径**：本轮「完成」= 双击 `start.bat` → 13 个页面可打开、指标有值、报告可生成可打印、重置后数据不空。**不等于** 103 条任务全部完成，也不等于生产可用。

> 清单门禁：`specs/001-geo-insight-platform/checklists/requirements.md` 16/16 通过。另有 `docs/checklists/geo-requirements.md`（41 条需求评审清单，reviewer 拥有、全部未勾选），不在 FEATURE_DIR/checklists 内，按 speckit-implement 规则**不作为本轮实现门禁**；它需要产品评审后由 reviewer 勾选，不能由实现者代勾。
