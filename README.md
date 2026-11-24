# GEO Insight

品牌在 AI 答案中的可见度监测。当前是可运行的**面试演示版**：一个 Python 进程同时提供 13 个页面和全部接口，数据落在一个 SQLite 文件里，双击 `start.bat` 即可启动。

规格、计划、数据模型、接口契约在 `specs/001-geo-insight-platform/`；内容工作室单独在 `specs/002-content-studio/`。

## 三分钟演示路径

1. **双击 `start.bat`**（Python 3.10+ 即可，不装依赖、不用 Docker）。浏览器自动打开看板。
2. 看板 `/`：8 张指标卡 → 趋势 → 问答明细 → 信源明细。可主动讲三句：
   - 平均排名显示「样本不足」，是因为只有 2 个带列表位次的样本，**低于 3 条不给数字**（口径规则，不是 bug）；
   - 点指标卡会下钻到构成该指标的样本，再点可看答案原文与引用；
   - 顶栏「口径说明」把分母、位次定义、去重规则讲清楚，阈值由接口下发而非写死。
3. `/settings` 监控配置：新建监控对象、批量导入问题（自动去重）、切换核心/长尾、增删别名与竞品（竞品集合版本递增）。
4. `/reports` 报告中心（**演示数据已自带 5 份报告**，含不可比与 +23.3pp 可比两类）→ 打开详情页，点「打印 / 导出 PDF」。报告是生成时刻的不可变快照，带 `payload_hash`；口径或模型集合变化时标注「不可比」且不输出涨跌幅。
5. `/studio` 内容工作室：三栏（创作要求 / 正文 / 事实证据）→ 发布通道（演示态）→ 发布后提及率 **22.2% → 40.0%，+17.8pp**。
6. 左侧栏按**工作台 / 优化执行 / 分析报告 / 洞察工具**四组展开，另外 6 个页面（问题监控、AI 洞察、优化机会、优化任务、品牌资产、行业信息分布）都接真实接口，不是占位页。

演示结束关掉黑色命令行窗口即停止服务。**重复双击不会报错**，服务已在运行时会直接帮你打开页面。

## 启动（不用 Docker）

**Windows 双击 `start.bat`**，或命令行：

```bash
python backend/run.py
```

浏览器打开 <http://127.0.0.1:8123>。**不要用 `file://` 直接打开 html**——浏览器会拦掉本地页面对 `127.0.0.1` 的请求；页面顶部也会给出这条提示。

| 路径 | 内容 |
|---|---|
| `/` | 工作台：指标卡、趋势、问答明细、信源、下钻、口径说明 |
| `/settings` | 监控配置：对象、品牌别名、竞品、问题集、采样计划与失败率 |
| `/reports` | 报告中心：生成与列表 |
| `/report/{id}` | 报告详情：快照、环比结论、打印导出 PDF |
| `/answer/{id}` | 答案详情：原文、提及位次、引用、推荐清单 |
| `/studio` | 内容工作室（002）：创作要求、正文、事实证据、发布通道、发布后对比 |
| `/monitor` | 问题监控：按问题查看每个模型的回答、位次与引用 |
| `/insight` | AI 洞察：趋势、模型对比、信源结构与采样计划 |
| `/opportunities` | 优化机会：未提及组合、提及率样本下钻与采样计划 |
| `/tasks` | 优化任务：文章列表、发布记录与效果关联 |
| `/assets` | 品牌资产：品牌信息、别名、竞品集合与采样计划 |
| `/sources` | 行业信息分布：媒体排行、类型分布、发布时间完整度、URL 去重内容明细 |

侧栏由 `backend/src/geo/api/nav.py` 在服务端统一注入，13 个页面只有一份导航定义；导出按钮点击时用当前筛选条件拼真实下载地址，没有 `href="#"` 假入口。

```bash
python backend/run.py --port 8124     # 换端口
python backend/run.py --reset         # 重建演示数据（清表，不删文件）
python backend/run.py --no-browser    # 不自动开浏览器
```

数据库就是 `backend/geo.db` 一个文件；`Ctrl+C` 停止。

`--reset` 会重建全部演示数据，**包括 5 份报告**（含一份不可比、一份 +23.3pp 可比），所以重置后报告中心不会是空的。

## 接口

契约权威源：`specs/001-geo-insight-platform/contracts/openapi.yaml`（OpenAPI 3.1，21 路径），根目录 `contracts/openapi.yaml` 是同步副本。

```bash
T=11111111-1111-1111-1111-111111111111
B=http://127.0.0.1:8123/v1

curl "$B/health"
curl "$B/meta/methodology"                            # 口径说明与阈值
curl "$B/trackers/$T"
curl "$B/trackers/$T/metrics/overview?from=2026-11-01&to=2026-11-03"
curl "$B/trackers/$T/metrics/mention_rate/samples?from=2026-11-01&to=2026-11-03"
curl "$B/trackers/$T/answers?from=2026-11-01&to=2026-11-03"
curl "$B/trackers/$T/answers/export?from=2026-11-01&to=2026-11-03&format=csv"
curl "$B/trackers/$T/citations?from=2026-11-01&to=2026-11-03"
curl -X POST "$B/trackers/$T/reports" -H "Content-Type: application/json" \
  -d '{"from":"2026-11-01","to":"2026-11-03"}'
```

内容工作室（002）：

```bash
curl "$B/channels"
curl "$B/trackers/$T/articles"
curl "$B/articles/55555555-5555-5555-5555-555555555551/impact"
```

**发布与登录都是展示态**：接口会真的落库（渠道状态、发布记录），但不发起任何真实登录或发帖，响应带 `integration: "demo"`。同一周期重复生成报告返回首次快照并标注 `reused`。

## 演示数据与预期

演示数据分两层：**黄金窗口**（口径演示，数值被断言锁死）与**富数据窗口**（页面密度与内容故事）。
全库规模：12 个问题（4 核心 + 8 长尾）、30 天、1302 条有效答案、1516 条采样记录（214 条失败）、
8 篇文章 / 6 条发布记录、5 份报告、5 个竞品、约 2400 个去重 URL。
另有第二个监控对象「华辰教育 · 教培」（4 题、12 天、208 条答案），顶栏切换器可切换查看——**切换不影响黄金口径**。

| 周期 | 有效样本 | 提及 | 预期 |
|---|---|---|---|
| 11-01 ~ 11-03 | 9 | 2 | 提及率 22.22%；平均排名样本不足 |
| 11-01 ~ 11-04 | 12 | 3 | `no_list_samples = 1`；平均排名分母仍只有 2 |
| 11-05 | 2 | 1 | `insufficient_samples`，不显示 0% 或 100% |
| 11-06、11-22 | 0 | — | 采集中断：当日 run_item 全部 failed，不进任何指标分母，趋势上留缺口 |
| 11-07 ~ 11-30 | 1288 | — | 提及率随内容投放爬坡（约 26% → 41%）；发布后效果在报告与 /publish 可比 |

对应 `specs/001-geo-insight-platform/quickstart.md` 第 2~4 节。

## 验证

```bash
cd backend
python -m unittest discover -s tests -t . -p "test_*.py" -v   # 36 条
python scripts/smoke.py                                        # 80 条真实 HTTP 断言（自动选空闲端口）
node scripts/pagecheck.mjs                                    # 70 条页面字段契约 + 侧栏结构 + 样式契约（需服务在跑）
node scripts/browsercheck.mjs                                 # 32 条真实浏览器 DOM 检查（需系统 Chrome / Edge）
node scripts/pagercheck.mjs                                   # 20 条分页组件行为测试（真实 app.js + 模拟点击/跳转）
```

`smoke.py` 自建临时库、在**自动挑选的空闲端口**启动 uvicorn、逐个请求断言后关闭进程，不碰演示数据，也不会误连到正在运行的开发服务（显式指定 `GEO_SMOKE_PORT` 且端口被占用时直接报错）。

`pagecheck.mjs` 按页面请求顺序逐字段校验（70 条，含 13 页单侧栏/单高亮/无死链、详情接口 `data_range` 类型，以及 13 页必须引入统一样式表、不得隐藏侧栏的样式契约）。`browsercheck.mjs` 调系统 Chrome/Edge 的 `--dump-dom`，验证 **JS 真的在浏览器里跑完**：13 页无报错、无「加载中」残留，关键表格/图表与**明细翻页条**确实渲染出来。这一层不装 Playwright、不加依赖 —— HTTP 200 和 `node --check` 都拦不住「页面读了接口里不存在的字段」。找不到浏览器时会明确打印 SKIP，不假装通过。`pagercheck.mjs` 在桩 DOM 里加载真实 `app.js` 模拟点击翻页（20 条），验证 10 行/页、边界禁用、换查询回第 1 页、页码直达跳转。另对每个 `<script>` 块跑 `node --check`。测试库为 `%TEMP%\geo_test_suite.db`。

## 实现范围与偏差

| 计划 | 实际 | 影响 |
|---|---|---|
| FastAPI | Starlette 原生路由 | 状态码与响应体对齐 `openapi.yaml`；无自动文档生成 |
| SQLAlchemy + Alembic | 标准库 `sqlite3` + `schema.sql` | 表与唯一约束对齐 `data-model.md`；无迁移版本链 |
| PostgreSQL + ClickHouse | SQLite 单库 | 指标实时从明细聚合；十万级样本会变慢 |
| pytest / Schemathesis | `unittest` + smoke + Node 字段契约 | 无自动化契约 fuzz |
| Next.js 前端 | 12 个零依赖静态页（决策 004） | 无组件化与类型检查，靠脚本闸门兜底 |
| 服务端 PDF 渲染 | 浏览器打印（决策 005） | 需人工点一次「另存为 PDF」，不能后台批量 |

替换只发生在 `repositories/`、`api/`、`frontend/` 三层，口径纯函数 `domain/metrics.py` 不变。决策记录在 `docs/decisions/002`~`005`。

## 目录

```text
backend/
  run.py                     # 一键启动（seed 判断 + 端口检查 + 开浏览器 + uvicorn）
  src/geo/
    domain/metrics.py        # 口径纯函数
    domain/methodology.py    # 口径说明（页面与报告共用）
    domain/enums.py
    services/                # metric / config / report / content
    repositories/            # sqlite / config / report
    api/app.py               # 路由 + 同源托管 13 个页面
    api/nav.py               # 服务端统一侧栏（唯一导航定义）
    seed.py, schema.sql
  tests/unit, tests/integration
  scripts/smoke.py, scripts/pagecheck.mjs, scripts/browsercheck.mjs, scripts/pagercheck.mjs
frontend/static/             # 13 个页面 + theme.css + app.css + app.js
start.bat                    # Windows 双击启动
specs/001-geo-insight-platform/  # spec / plan / data-model / contracts / tasks / quickstart
specs/002-content-studio/        # 内容工作室
docs/decisions/                  # 002 不用 Docker、003 不复刻、004 静态页、005 打印导出
```

## 尚未实现（按用户要求暂缓）

- **真实采集**：登录态、风控、DOM 稳定性全部未验证；`mention` 目前由 seed 写入，没有真实提及解析器
- **鉴权与租户隔离**：首版租户由默认配置派生，不能给外部用户直接使用
- 渠道真实登录与发帖（002 T019/T020）；按周聚合；片段级情感聚合；合规命中审计落库
- 契约 fuzz（Schemathesis）、交互 E2E（表单提交等完整用户流）、200 条黄金样本回归。页面渲染层已由 `browsercheck.mjs` 覆盖、分页翻页点击已由 `pagercheck.mjs` 覆盖，但**其余交互（表单提交、下钻抽屉、打印）仍未模拟用户操作**
- **AI 成稿与积分**：按决策 003 整体取消，不是待办
