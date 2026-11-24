# 006 用自建迁移链，不引入 Alembic

- 日期：2026-11-24
- 状态：已采纳

## 背景

首版建表靠 `schema.sql` 一把 `CREATE TABLE IF NOT EXISTS`。这在纯演示阶段没问题，
但一旦要接真实数据，改字段就是危险动作：没有版本记录，谁也不知道某个库改到了哪一步；
`ALTER TABLE` 也不幂等，中途失败会留下半改状态。

## 决策

在 `backend/migrations/` 下按 `NNN_名称.sql|.py` 建迁移链，由 `geo/migrate.py` 执行，只做三件事：

1. **版本表** `schema_migration(version, name, checksum, applied_at)`，重复启动不会重复执行；
2. **checksum 校验**，已应用的迁移被事后改动直接报错，而不是悄悄漂移；
3. **老库基线认领**，用 `schema.sql` 建出来的库可以平滑接入（校验 001 声明的表都在），不丢数据。

不引入 Alembic。它要拖进 SQLAlchemy 与 autogenerate 配置，与"单进程 + 单文件 SQLite + 双击启动"
的前提冲突（见决策 002）。

## 两条硬规则

- **已应用的迁移不能再改。** 要改就新增版本号。checksum 会拦住你。
- **未应用的迁移必须能安全重跑。** `.sql` 用 `IF NOT EXISTS`，`.py` 自己查 `PRAGMA table_info`。
  这样中途失败时改完文件重跑即可，不依赖 DDL 事务回滚（SQLite 的 `executescript` 本来也不保证原子性）。

## 代价

| 代价 | 说明 |
|---|---|
| 没有 down migration | 回滚要手工写反向脚本 |
| 不能自动生成 | 改模型后迁移要手写，靠 13 条单测兜底 |
| 只适用于 SQLite 方言 | 换 PostgreSQL 时这层要重写（但那时本来就该换 Alembic） |

## 当时的替代方案

- **继续用 schema.sql**：省事，但改字段时没有安全网。
- **Alembic**：功能全，但为 3 个迁移文件引入一整套依赖不划算。
- **每次改结构就删库重建**：演示期可行，接了真实数据就不可接受。
