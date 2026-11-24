# Tasks: 内容工作室与多渠道发布

**Input**: `specs/002-content-studio/spec.md`
**Status**: 首版（界面 + 占位接口）已实现，真实登录与发帖按 FR-016 排除在本期之外

**说明**：本文件是 002 自己的任务清单，编号独立于 001。001 的收敛任务在 `specs/001-geo-insight-platform/tasks.md` 的 Phase 7。

## Phase 1: 数据与接口

- [X] T001 扩展表结构：`article`、`article_fact`、`channel_account`、`publication`，含 `UNIQUE(article_id, channel_id)` per FR-011 in `backend/src/geo/schema.sql`
- [X] T002 写入演示数据：3 篇文章、7 个渠道、2 条已发布记录、5 条事实 per FR-002/FR-007 in `backend/src/geo/seed.py`
- [X] T003 实现渠道列表与连接占位接口 `GET /v1/channels`、`POST /v1/channels/{id}/connect`，返回 `integration=demo` per FR-007/FR-008 in `backend/src/geo/api/app.py`
- [X] T004 实现文章列表、详情（含事实与发布记录）`GET /v1/trackers/{id}/articles`、`GET /v1/articles/{id}` per FR-001/FR-005 in `backend/src/geo/api/app.py`
- [X] T005 实现正文保存 `PATCH /v1/articles/{id}`，版本号递增 per FR-003 in `backend/src/geo/api/app.py`, `backend/src/geo/repositories/sqlite_repository.py`
- [X] T006 实现一键发布 `POST /v1/articles/{id}/publish`：拒绝未连接渠道、按渠道幂等入队 per FR-009/FR-010/FR-011 in `backend/src/geo/api/app.py`
- [X] T007 实现发布后提及率对比服务：按首次发布时间切窗、复用提及口径、样本不足只给状态 per FR-012/FR-013/FR-014 in `backend/src/geo/services/content_service.py`

## Phase 2: 页面

- [X] T008 搭建工作室三栏布局：创作要求 / 正文编辑 / 事实与证据 per FR-001/FR-002 in `frontend/static/studio.html`
- [X] T009 实现文章切换、正文保存、合规风险词提示（不阻断保存）per FR-003/FR-006 in `frontend/static/studio.html`
- [X] T010 实现事实面板：待核实排在已确认之前并标色 per FR-005 in `frontend/static/studio.html`
- [X] T011 实现发布通道面板：勾选已连接渠道、连接占位、一键发布、发布记录与回链 per FR-007~FR-010 in `frontend/static/studio.html`
- [X] T012 实现发布后提及率面板：前后窗口、样本量、涨跌、不可比与「未发布」状态 per FR-012~FR-015 in `frontend/static/studio.html`
- [X] T013 移除 Credits 与「AI 生成草稿/视频脚本/助手」入口，改为「人工成稿 · 系统只做事实核对与合规提示」，并删掉 6 个死链导航 per FR-004/决策 003 in `frontend/static/studio.html`
- [X] T014 后端 `/studio` 路由托管页面，看板加入口链接 in `backend/src/geo/api/app.py`, `frontend/static/index.html`

## Phase 3: 验证

- [X] T015 集成测试：发布前后手算一致（22.22% → 40%，+17.8pp）、未发布无对比、2 样本窗口为 insufficient per SC-002/SC-003 in `backend/tests/integration/test_content.py`
- [X] T016 集成测试：同渠道重复发布不产生重复记录、保存正文版本 +1、待核实事实排序 per FR-003/FR-005/FR-011 in `backend/tests/integration/test_content.py`
- [X] T017 冒烟断言 10 条：`/studio` 可访问、7 渠道 2 已连接、3 文章、impact 三项数值、未发布状态、未连接渠道 400、发布 202、重复发布不重复 per FR-008~FR-011 in `backend/scripts/smoke.py`
- [ ] T018 页面级 E2E（点击连接 → 勾选 → 发布 → 刷新仍在）per SC-004 (missing) — 需要 Playwright，本期不引入

## Phase 4: 后续（本期明确不做）

- [ ] T019 真实渠道登录：扫码 / OAuth / Cookie 管理，替换 `pending_integration` 占位 per FR-016 (missing)
- [ ] T020 真实发帖与失败重试、发布回调、回链自动回填 per FR-010 (missing)
- [X] ~~T021 AI 生成正文与视频脚本、积分扣减~~ **取消（决策 003）**：不做成稿、不做积分；将来若做只允许「提纲建议」，需重新立规。
- [ ] T022 文章新建/删除与模板管理，替换当前仅切换演示文章的芯片 per FR-002 (missing)

---

## 验证状态（2026-11-11）

- 单测 + 集成：21 条全过（001 的 13 条 + 002 的 8 条）。
- 冒烟：34 条真实 HTTP 断言全过，其中 10 条属于 002。
- 演示数据手算对齐：发布前 2/9 = 22.22%，发布后 2/5 = 40%，差值 +17.8pp。
- 未验证：真实登录、真实发帖、浏览器点击链路（无 Playwright）；页面视觉只做了结构与脚本语法检查。
