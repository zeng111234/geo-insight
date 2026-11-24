# Implementation Plan: GEO Insight 品牌可见度监测平台

**Branch**: `001-geo-insight-platform` | **Date**: 2026-11-11 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-geo-insight-platform/spec.md`

## Summary

首版交付「配置监控 → 采样记录 → 解析口径 → 指标看板 → 周期报告」这一条闭环，不做诊断与自动发布。指标口径集中在纯函数里，所有数字带样本量与口径版本；报告是生成时刻的不可变快照。

**交付形态**：一个 Python 进程（Starlette）+ 一个 SQLite 文件 + 零依赖静态页，页面与接口同源。PostgreSQL + ClickHouse + 队列是规模化形态，不是本地前置条件（决策 002）。

**前端形态**：`frontend/static/` 下的 12 个页面（MVP 6 页 + 按客户截图补的 6 页），共用 `theme.css`（色板）、`app.css`（布局与组件）与 `app.js`（请求工具），不使用构建工具（决策 004）。侧栏由 `api/nav.py` 服务端统一注入，只有一份导航定义。PDF 由报告页在浏览器打印生成，不引入服务端渲染服务（决策 005）。

## Technical Context

### 当前交付形态（本地可跑，已验证）

**Language/Version**: Python 3.13（代码兼容 3.10+）

**Primary Dependencies**: Starlette + uvicorn（唯一两个运行期依赖）；标准库 `sqlite3`、`csv`、`hashlib`、`unittest`

**Storage**: SQLite 单文件 `backend/geo.db`，结构由 `backend/migrations/` 的迁移链管理（版本表 + checksum + 老库认领）；唯一约束承担幂等职责

**Frontend**: 原生 HTML/CSS/JS，零构建、零依赖；页面由后端同源托管

**Testing**: `unittest`（口径、配置、报告、内容工作室）+ `scripts/smoke.py`（真实 HTTP）+ Node 脚本做页面字段契约检查

**Target Platform**: Windows / macOS / Linux，单进程；浏览器 Chrome / Edge 最近两个版本

**Performance Goals**: 看板首屏 P95 ≤ 2s（本地 12 条样本实测 5 ms）；报告生成 ≤ 60s（实测 5 ms）

**Constraints**: 所有业务表带 `tenant_id`；原始答案只追加；指标与快照带 `metric_version`；首版不实施真实采集；不引入容器

**Scale/Scope**: 5 个监控对象、每对象 ≤200 个问题、3 个模型；峰值约 5,000 采样记录/日；12 个页面

### 规模化目标形态（数据量上来后替换，不阻塞本地交付）

| 层 | 目标 | 替换边界 |
|---|---|---|
| 业务库 | PostgreSQL 16 + pgvector | 只改 `repositories/`，口径与接口不动 |
| 明细与宽表 | ClickHouse 24（`metric_daily` 预聚合） | 同上；触发条件：样本 > 5 万或看板 P95 > 500ms |
| 队列 | Redis + Celery（聚合、报告异步化） | `services/` 调用不变，改为投递任务 |
| 对象存储 | S3 兼容（原始响应、报告 PDF） | `raw_uri` 已预留 |

## Constitution Check

*GATE: 对照 `.specify/memory/constitution.md` v1.1.0（2026-11-11 批准）。*

| 原则 | 结论 | 依据 |
|---|---|---|
| I. 口径先行，结果可追溯 | **PASS** | 口径集中在 `domain/metrics.py` 纯函数，返回 `{value, status, sample_base}`；`/v1/meta/methodology` 是页面与报告共用的唯一文案源；报告 payload 带 `metric_version` 与样本量；样本 <3 只返回 `insufficient_samples`（`tests/unit/test_metrics.py`、下钻 `in_denominator`） |
| II. 未接入必须显式声明 | **PASS** | 真实采集未接：接口保留、`run` 不产生真实样本；渠道连接/发布返回 `integration: "demo"` 与说明；工作室无积分、无 AI 成稿入口（决策 003，smoke 负向断言守护） |
| III. 律师内容合规红线 | **PASS（有缺口）** | 工作室保留合规提示与事实清单，正文风险词只提示不阻断；报告数字均带样本量与口径。缺口：合规命中记录尚未落库（`audit_log` 未写入），已记为 T102（Phase 7，未开始） |
| IV. 按风险分级的验证 | **PASS** | 口径、报告快照、数据重置三处做了**反向验证**（修复前断言为红）；静态页做脚本语法与字段契约检查；交付汇报标注验证层级 |
| V. 分层与可替换 | **PASS（1 处已知偏离）** | 控制器只解析请求/格式化响应，业务在 `services/`，SQL 收敛在 `repositories/`；已知偏离：`publish_article` 的渠道校验仍在 handler，待下沉到 `content_service` |
| VI. 先评估，再动手 | **PASS** | 决策 003 拒绝 1:1 复刻参考产品；决策 004/005 记录前端与 PDF 的技术取舍与回退条件 |

**Post-Design Re-check**: 设计与实现后复核仍为 PASS。新增复杂度只有「静态页替代框架」与「PDF 走打印」两项，均已在 Complexity Tracking 与决策记录中说明。

## Project Structure

### Documentation (this feature)

```text
specs/001-geo-insight-platform/
├── plan.md              # 本文件
├── research.md          # Phase 0：10 项技术决策
├── data-model.md        # Phase 1：19 张业务表、约束、状态机
├── quickstart.md        # Phase 1：可执行验收路径
├── contracts/
│   ├── openapi.yaml     # 22 个路径 / 27 个操作 / 32 个 schema
│   └── README.md        # 错误码、幂等、分页、鉴权、下钻约定
├── checklists/
│   └── requirements.md  # 规格质量清单（16/16）
└── tasks.md             # 任务清单（含 Phase 7 收敛与 Phase 8 本地交付）
```

### Source Code (repository root)

```text
backend/
├── run.py                    # 一键启动：seed 判断 → 端口检查 → 开浏览器 → uvicorn
├── geo.db                    # 本地单文件库（seed 可确定性重建）
├── src/geo/
│   ├── api/app.py            # 全部路由 + 页面托管（Starlette）
│   ├── domain/
│   │   ├── metrics.py        # 口径纯函数：提及率/排名/TopN/情感/Wilson
│   │   ├── methodology.py    # 口径说明文案（页面与报告共用）
│   │   └── enums.py
│   ├── repositories/
│   │   ├── sqlite_repository.py    # 读模型：指标、明细、信源、下钻、导出
│   │   ├── config_repository.py    # 配置：监控/别名/竞品/问题/采样
│   │   └── report_repository.py    # 报告快照读写
│   ├── services/
│   │   ├── metric_service.py
│   │   ├── config_service.py
│   │   ├── report_service.py
│   │   ├── import_service.py       # CSV/JSONL 导入
│   │   ├── collect_service.py      # 采集一条并落库
│   │   └── content_service.py      # 002 内容工作室
│   ├── migrate.py                  # 迁移链
│   └── seed.py
├── migrations/                     # 001 基线 / 002 导入结构 / 003 品牌类型
├── tests/
│   ├── unit/test_metrics.py
│   └── integration/{test_service,test_config,test_report,test_content,test_import}.py
└── scripts/smoke.py          # 真实 HTTP 冒烟（65 条断言）

frontend/static/
├── theme.css                 # 唯一色板（WCAG AA 校准）
├── app.css                   # 布局与组件（含打印样式）
├── index.html                # 工作台：指标卡 → 下钻抽屉 → 口径说明
├── settings.html             # 监控配置：品牌/别名/竞品/问题/采样/导出
├── studio.html               # 内容工作室（002）
├── reports.html              # 报告中心：生成 + 列表
├── report.html               # 报告详情：结论/对比/信源 + 打印
└── answer.html               # 答案详情：原文/提及/引用/推荐清单

contracts/openapi.yaml        # 构建时复制，运行时以 specs 下为准
start.bat                     # Windows 双击启动（CRLF + 纯 ASCII）
```

**Structure Decision**: 后端按 `api / domain / services / repositories` 分层，口径计算不依赖数据库与 HTTP，可脱离框架回归；前端用静态页而非框架，原因是本地无 npm registry 且演示目标是「跑得起来、点得通」，引入构建链只会增加失败面（决策 004）。页面共用一份色板与一套组件样式，避免逐页复制导致改色漏改。

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| 静态页替代 Next.js | 本机无法安装 npm 依赖；演示需要「双击即用」 | 引入构建链会让面试现场多一个失败点；12 个页面的重复部分已抽到 `app.js` 与服务端 `nav.py`，不靠组件框架也能收敛 |
| 单库 SQLite，无预聚合宽表 | 本地样本量 14 条，预聚合是纯开销 | 直接聚合已满足 P95 ≤ 2s（实测 5ms）；超过 5 万样本再换列式存储 |
| PDF 用浏览器打印 | 不引入 Gotenberg + Chromium（数百 MB） | 服务端渲染只为「一致的 PDF」，而浏览器打印的字段一致性可逐项校验（SC-005） |
| 无鉴权 | 单租户本地演示，无真实用户 | 引入登录会让演示多一步；契约保留 security 段，T091 记录待办 |
| 同步生成报告 | 数据量小，同步 5ms 完成 | 异步队列需要 Redis + Worker，本地无收益；接口仍返回 201 与耗时 |

## Phase 0 输出

`research.md` 记录 10 项决策：采集通道、列表序号识别、提及检测、情感判定、存储分工、指标预聚合、报告快照、调度与失败处理、多租户、测试策略。每项含 Decision / Rationale / Alternatives considered。

## Phase 1 输出

- `data-model.md`：19 张业务表，含字段、约束、4 组状态机、索引与 TTL。
- `contracts/openapi.yaml`：**22 个路径、27 个操作、32 个 schema**（v0.2.0），覆盖配置、采样计划、指标、下钻、明细、导出、信源、报告与口径说明。
- `contracts/README.md`：鉴权现状、错误码、重复提交保护、分页、下钻与报告哈希约定。
- `quickstart.md`：可执行验收路径，对应 P1 的 4 条场景与 SC-002/005/006/007。
