# 002 · 本地运行不使用 Docker，SQLite 作为唯一本地存储

**状态**：已采纳（用户决定）
**日期**：2026-11-11
**影响范围**：本地启动方式、开发依赖、数据层实现、quickstart 验收步骤

## 背景

`plan.md` 原本假设本地用 Docker Compose 起 PostgreSQL 16 + ClickHouse 24 + Redis 7 + MinIO。
实际使用中出现了两个问题：

1. 这台机器跑 Docker 很卡，四个容器常驻会明显拖慢系统，日常开发不现实；
2. 当前环境 Docker 守护进程本身未启动，容器方案从未真正跑通过，属于纸面方案。

## 决策

**本地开发与演示不使用 Docker，也不安装 PostgreSQL / ClickHouse / Redis / MinIO。**
唯一运行形态是：一个 Python 进程 + 一个 SQLite 文件（`backend/geo.db`）。

具体约定：

- 启动方式统一为 `python backend/run.py`（Windows 双击 `start.bat`），一条命令同时提供接口与看板页面；
- 页面由后端托管在 `http://127.0.0.1:8123/`，不再单独起静态服务器，也不再支持 `file://` 直接打开；
- 测试与冒烟脚本只依赖标准库，不连接任何外部服务；
- `infra/docker-compose.yml` 不再创建；`tasks.md` 中相关的编排任务标注为「不适用（决策 002）」，不勾选为完成。

## 理由

- 口径正确性不依赖数据库种类。所有指标计算集中在 `domain/metrics.py` 的纯函数里，输入是样本列表，输出是 `{value, status, sample_base}`，换存储不影响结果。
- 当前演示数据量是十几条答案，SQLite 单文件在毫秒级返回；引入四个容器只增加启动成本和故障面。
- 数据层已经被 repository 层隔开，将来要换 PostgreSQL，改动面是 `repositories/` 与迁移脚本，不涉及业务逻辑。

## 代价（明确接受）

| 代价 | 说明 | 触发补救的条件 |
|---|---|---|
| 无预聚合宽表 | 指标每次从明细实时聚合，样本量大后变慢 | 单监控对象累计样本 > 5 万，或看板 P95 > 500ms |
| 无迁移版本链 | `schema.sql` 全量建表，改表要手工处理 | 出现第二个部署环境（测试/预发）时补 Alembic |
| 无并发写入能力 | SQLite 写锁全库，采集批量入库会阻塞读 | 接入真实采集、需要并发写入时换 PostgreSQL |
| 无 Redis | 没有缓存与任务队列，采集调度无法异步 | 需要定时采样时，先用进程内调度，再评估队列 |

## 保留的生产路径

`plan.md` 里的 PostgreSQL + ClickHouse 仍是规模化部署的目标形态，只是**不作为本地开发前置条件**。
迁移时按 repository 接口替换实现，不动 `domain/`、`api/` 与前端契约。

## 相关改动

- `backend/run.py`：一键启动，含 seed、端口占用检查、就绪后开浏览器
- `backend/src/geo/api/app.py`：新增 `/` 与 `/static` 路由，页面与接口同源
- `frontend/static/index.html`：API 基址改为同源相对路径，`file://` 下给出明确提示
- `README.md`：启动段重写，删掉静态服务器与容器步骤
- `specs/001-geo-insight-platform/quickstart.md`：验收步骤改为 SQLite 路径，容器步骤降级为「规模化部署时再看」
- `start.bat`：Windows 双击入口
