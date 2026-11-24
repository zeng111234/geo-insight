# 决策 004：前端采用零依赖静态页，不引入 Next.js

**日期**：2026-11-11（2026-11-12 追认补录）
**状态**：已生效
**触发**：`/speckit-analyze` 发现 C3 —— `plan.md` 写 Next.js 15 + `frontend/src/`，实际交付是 `frontend/static/` 六页；更换前端基础设施却没有决策记录，违反宪法原则 V。

## 背景与约束

- 本机 PyPI 不可达，npm 依赖同样装不上；`FastAPI / SQLAlchemy` 已因同一原因换成 Starlette + 标准库。
- 用户明确不使用 Docker；本地形态是一个 Python 进程 + 一个 SQLite 文件（决策 002）。
- 用途是**面试演示**：可复现、零安装、双击 `start.bat` 就能跑，比框架完整度重要。
- 页面由后端同源托管，避免 `file://` 打开时的跨域故障。

## 备选方案

| 方案 | 优点 | 代价 | 结论 |
|---|---|---|---|
| A. Next.js 15 + TypeScript + TanStack Query + ECharts（原计划） | 组件化、类型安全、后续可扩展 | 需 npm 安装与构建；本机不可达；引入第二套进程与端口；演示机可能没有 Node | **放弃（当前形态）** |
| B. 零依赖静态 HTML/CSS/JS，由 Starlette 同源托管 | 一个进程、零安装、零构建；改完刷新即生效；面试现场可控 | 无组件复用、无类型检查、状态管理靠手写 | **采用** |
| C. 引入 CDN 版前端框架（Vue/React UMD） | 稍有组件化 | 离线演示会白屏；等于把可用性押在公网上 | **放弃** |

## 决定

首版及本地演示形态固定为方案 B：

- 页面位于 `frontend/static/`：`index.html`、`settings.html`、`reports.html`、`report.html`、`answer.html`、`studio.html`；
- 共用 `theme.css`（唯一色源）与 `app.css`（布局与组件），页面只写结构与脚本；
- 页面与接口同源，相对路径调用 `/v1/...`；仅在 `file://` 打开时给出明确提示条；
- 契约仍是权威：`contracts/openapi.yaml` 定义接口形状，前端按契约取字段，不反向定义接口。

## 代价与限制（不掩饰）

- 没有构建期类型检查，改用两道脚本闸门兜底：每个 `<script>` 块跑 `node --check`；按页面真实请求顺序用 Node 逐字段校验接口响应。
- 无虚拟 DOM 与组件复用，六页存在少量重复模板代码（导航与空态）。
- 图表用手写 SVG / CSS，交互能力弱于 ECharts。
- 若将来要做多租户 SaaS、复杂状态或大表格虚拟化，B 的维护成本会超过 A。

## 回退 / 升级条件

出现以下任一条即重新评估，并走决策修订，而不是随手替换：

1. 需要 npm 依赖可安装的环境，且演示不再要求「双击即用」；
2. 页面数 > 10，或同一交互组件重复出现 ≥ 3 次；
3. 需要服务端渲染、路由鉴权或复杂客户端缓存。

升级时保持 `contracts/openapi.yaml` 不变，只替换 `frontend/` 实现层（宪法原则 V）。

## 关联

- 决策 002（不用 Docker、SQLite 单进程）
- 宪法 v1.1.0 原则 V「分层与可替换」、原则 VI「先评估，再动手」
- `plan.md` Technical Context / Project Structure
