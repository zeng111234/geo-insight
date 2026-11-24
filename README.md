# GEO Insight

品牌在 AI 答案中的可见度监测。当前是可运行的**面试演示版**：一个 Python 进程同时提供 13 个页面和全部接口，数据落在一个 SQLite 文件里，双击 `start.bat` 即可启动。

规格、计划、数据模型、接口契约在 `specs/001-geo-insight-platform/`；内容工作室单独在 `specs/002-content-studio/`。

## 三分钟演示路径

1. **双击 `start.bat`**（Python 3.10+ 即可，不装依赖、不用 Docker）。浏览器自动打开**登录页**，
   账号已预填，密码 `geo-demo-2026`（启动日志也会打印）。登录后进入看板。
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

### 登录

服务默认开启鉴权，未登录时页面跳 `/login`、接口返回 401。

| 项 | 值 |
|---|---|
| 账号 | `demo@example.com`（登录页已预填） |
| 密码 | `geo-demo-2026`（**演示默认值**，启动日志会打印） |
| 覆盖方式 | `GEO_ADMIN_PASSWORD` / `GEO_ADMIN_EMAIL` / `GEO_SESSION_HOURS`（默认 12 小时） |
| 退出 | 侧栏底部「退出登录」 |

实现上的三个取舍（都比较保守，写在 `services/auth_service.py` 顶部）：

- 密码用标准库 `hashlib.scrypt`（N=2¹⁴），不引 bcrypt/argon2；哈希串自带参数，将来调强度不用迁移。
- Session 存在**进程内 dict**，不引 JWT、不建 session 表。代价是**重启后要重新登录**——单进程单用户，
  这比维护一张要清理过期的表划算。
- 登录后租户来自会话，**不再信任 `X-Tenant-Id` 请求头**，否则任何登录用户改个头就能看别人的数据。

自动化检查（页面契约 / 浏览器 DOM / 分页行为）不看登录态，启动服务时加 `GEO_AUTH_REQUIRED=0` 即可关闭；
业务代码不会因为这个开关做别的事。`smoke.py` 相反，它**真的登录一次**再跑，覆盖 401 / 302 / cookie / 登出。

| 路径 | 内容 |
|---|---|
| `/` | 工作台：指标卡、趋势、问答明细、信源、下钻、口径说明 |
| `/settings` | 监控配置：对象、品牌别名、竞品、问题集、采样计划与失败率 |
| `/reports` | 报告中心：生成与列表 |
| `/report/{id}` | 报告详情：快照、环比结论、打印导出 PDF |
| `/answer/{id}` | 答案详情：原文、提及位次、引用、推荐清单 |
| `/studio` | 内容工作室（002）：创作要求、正文、事实证据、发布通道、发布后对比 |
| `/monitor` | 问题监控工作台：左侧选对象 → 右侧 10 个 tab（指标看板、问答明细已接数据，其余 8 个见下） |
| `/insight` | AI 洞察：趋势、模型对比、信源结构与采样计划 |
| `/opportunities` | 优化机会：未提及组合、提及率样本下钻与采样计划 |
| `/tasks` | 优化任务：文章列表、发布记录与效果关联 |
| `/assets` | 品牌资产：品牌信息、别名、竞品集合与采样计划 |
| `/sources` | 行业信息分布：媒体排行、类型分布、发布时间完整度、URL 去重内容明细 |

**问题监控工作台**（对齐参考视频的批次 A，计划见 `docs/video-reference-plan.md`）

`/monitor` 用一页承载 10 个维度：左侧是监控对象列表（可搜索），右侧是对象头 + tab 栏 + 内容区。
切换对象和 tab 都写进 URL（`?tracker=…&tab=…`），刷新与前进后退都回到原处。

| tab | 状态 |
|---|---|
| 指标看板 | **已接** `/metrics/overview` + `/metrics/by-question` + `/metrics/trend`：5 张指标卡 + 12 个问题的分问题表现（含对比条）+ 提及率趋势（缺数据断开）+ 引用概览 |
| 问答明细 | **已接**：问题下拉 + 回答列表（服务端分页）+ 右栏引用信源，点一条回答右栏跟着换 |
| 指标明细 | **已接** `/metrics/{m}/samples`：5 个指标可切，表里标出**哪些样本进了分母**，12 行/页 |
| 趋势分析 | **已接** `/metrics/trend`：提及率折线（缺数据的日子断开不插值）+ 每日摘要表 |
| 信源分析 | **已接** `/citations/summary` + `/citations`：总量、类型分布、媒体排行、URL 去重明细（服务端分页） |
| 竞品排名 | **已接** `/competitors/ranking`：提及数、占比、平均位次、情感分布，带对比条 |
| 竞品统计 | **已接** `/competitors/cooccurrence`：共现矩阵（含本方品牌），矩阵对称性有断言 |
| 视频·图文引用 | **已接** `/citations?mediaType=`：7 个类目可切，服务端过滤后分页 |
| 情感倾向 | **已接** `/metrics/sentiment-by-model`：按模型拆正/中/负，样本不足的模型只标「样本不足」不给比例；合并后与看板口径逐位一致 |
| 采集健康 | **已接** `/collection-health`：应采 / 已采 / 缺口 / 达成率，按模型与按天两张表；没配采样计划时返回 `configured=false` 并说明原因，不画空图 |

10 个 tab 全部接真实数据，没有占位或写死数字。新增的三个聚合接口（竞品排名、竞品共现、情感按模型）
与既有口径做了交叉校验：情感按模型合并后与看板总览逐字段相等，采集健康的「已采」等于总览 `valid_total`。

侧栏由 `backend/src/geo/api/nav.py` 在服务端统一注入，13 个页面只有一份导航定义；导出按钮点击时用当前筛选条件拼真实下载地址，没有 `href="#"` 假入口。

```bash
python backend/run.py --port 8124     # 换端口
python backend/run.py --reset         # 重建演示数据（清表，不删文件）
python backend/run.py --no-browser    # 不自动开浏览器
```

数据库就是 `backend/geo.db` 一个文件；`Ctrl+C` 停止。

`--reset` 会重建全部演示数据，**包括 5 份报告**（含一份不可比、一份 +23.3pp 可比），所以重置后报告中心不会是空的。

## 数据接入

演示数据由 `seed.py` 生成。要换成真实数据，有两条路，都走同一套写入与同一套幂等规则。

**① 批量导入（现在就能用）**

```bash
cd backend
python scripts/import_answers.py --file data/import-sample.csv --dry-run   # 先演练，不落库
python scripts/import_answers.py --file data/import-sample.csv
python scripts/import_answers.py --file data/import-sample.jsonl --create-questions
```

- 表头支持中英文（`问题/答案/模型/采集时间` 与 `question/answer/model/collected_at` 等价），
  `mentions` / `citations` 用 JSON 数组字符串。
- **幂等**：同一份文件导两次，第二次全部计入「跳过重复」，不会翻倍。判断依据是
  (监控对象, 问题, 模型, 渠道, 采集时间, 答案原文) 这组观测身份，刻意不含提及/引用——
  否则改一个错别字就会变成两条重复观测，把指标算歪。
- **坏行不拖累整批**：默认坏行跳过并写入 `backend/import_reports/*.csv`（UTF-8 BOM，Excel 直接打开），
  报告里有行号、字段、错误码、原始行。要严格模式就用 `--on-error abort`，整批不落库。
- 退出码：0 全部成功、2 有失败行但其余已入库、1 无法开始，可直接接进脚本。

**撤销一次导入**

同一条观测入库后，靠重新导入是改不动的（会被判成重复）。标错了提及、导错了文件，
唯一出路是整批撤回再重导：

```bash
cd backend
python scripts/import_answers.py --list-batches                  # 找批次 id
python scripts/import_answers.py --delete-batch <id> --dry-run   # 先看会删多少
python scripts/import_answers.py --delete-batch <id>             # 真回滚
```

- 级联删掉这批写入的 answer 及其 mention / citation / answer_block，批次状态置为 `rolled_back`。
- **只动自己这一批**，别的批次和演示数据不受影响。
- 重复回滚是幂等的，不会报错，也不会再删东西。
- **竞品的 `brand` 行不跟着删**：那是「主体注册」，可能被别的批次的提及引用，跟着删会让别人的提及失去主语。
- 回滚后可以重新导入同一份文件，此时会正常新增而不是判重复。

**② 采集器（接口已就位，等平台权限）**

`collectors/` 定义 `Collector` 协议（`available / supports / collect`），注册表显式登记，不做目录扫描。
目前只有一个离线实现 `csv_bank`：从本地答案库按 (问题, 模型) 取答案，用来把「采集 → 落库 → 出指标」
这条链路先跑通。拿到平台 API 后新增一个 `kind="api"` 的实现即可，写入与指标层不用动。

```bash
cd backend
python scripts/collect.py --list
GEO_COLLECTOR_CSV=data/import-sample.jsonl python scripts/collect.py \
  --question "沈阳哪一家律所最专业" --model doubao
```

**说明**：采集器只负责拿回原文，不做提及识别；`mention` 目前仍由 seed 或导入时的 `mentions` 字段写入。
自动识别提及是下一步的事，不假装已经做了。

**③ 缺样本清单（人工采集的日常闭环）**

没有平台 API 权限时，最现实的日常动作是：算缺口 → 人工去各平台问 → 导回结果。三步都不碰数据库：

```bash
cd backend
python scripts/sample_gaps.py                                  # 今天的缺口
python scripts/sample_gaps.py --from 2026-11-18 --to 2026-11-24  # 补历史
python scripts/sample_gaps.py --date 2026-11-20 --export gaps.csv
# 人工在 Excel 里填「答案」列 → 导入 → 缺口自动缩小
python scripts/import_answers.py --file gaps.csv
```

- 应有条数取自采样计划：核心问题 `core_times` 次/天，长尾 `long_tail_times` 次/天，每个模型各算一份。
- 已有条数按「北京时间自然日 × 问题 × 模型」统计，**不区分渠道**（人工用网页问也算采到）。
- 采多了不算负缺口；总数里的「已有」按应有封顶，不会出现「已有 5 / 应有 3」。
- 导出的 CSV 用中文表头，且列名就是导入通道认的那几个，填完直接导回来；采集时间已预填当天 09:00，按实际改。
- **核销是实时差分，不是状态机**：没有「待办表」要维护，重算一次就是最新缺口。撤销导入批次后缺口会重新出现，这也是对的——数据没了就该重采。
- `--fail-on-gap` 让存在缺口时返回 2，方便挂定时任务报警。

**④ 删改留痕（审计）**

删别名、移除竞品这类不可逆操作，界面先弹确认框（写清后果，不只是「确定吗」），确认后**删除与审计在同一个事务里提交**——
审计写失败就整体回滚，不会出现「删了没记账」。

```bash
python scripts/audit_log.py                        # 最近 20 条
python scripts/audit_log.py --action alias.delete  # 只看某类动作
python scripts/audit_log.py --json
```

- `before_json` 存**完整行快照**，不是差异：差异能算出来，快照丢了就真丢了。
- 记「谁」：`actor_id` 取自登录会话，查询时 join 出邮箱；鉴权关闭时留空，不编造系统用户。
- 只记删改，不记读——记全量读会把真正要查的那条淹掉。
- 两个删除接口都校验对象归属当前租户：知道 id 也删不了别人的数据。

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
python -m unittest discover -s tests -t . -p "test_*.py" -v   # 134 条（含 HTTP 层鉴权与审计，需 starlette）
python scripts/smoke.py                                        # 86 条真实 HTTP 断言（自己登录，自动选空闲端口）

# 后三个不看登录态：启动服务时显式关掉鉴权
GEO_AUTH_REQUIRED=0 python run.py --no-browser &
node scripts/pagecheck.mjs                                    # 77 条页面字段契约 + 侧栏结构 + 样式契约
node scripts/browsercheck.mjs                                 # 68 条真实浏览器 DOM 检查（需系统 Chrome / Edge）
node scripts/pagercheck.mjs                                   # 22 条分页组件行为测试（真实 app.js + 模拟点击/跳转）
```

合计 404 条，全部通过。

用**系统 Python** 跑（`start.bat` 用的也是它）：`backend/.venv` 是历史残留，里面只有 pip，
`starlette` 装在系统环境。空 venv 里跑单测不会报错，HTTP 层那 10 条会自动跳过并说明原因。

`smoke.py` 自建临时库、在**自动挑选的空闲端口**启动 uvicorn、逐个请求断言后关闭进程，不碰演示数据，也不会误连到正在运行的开发服务（显式指定 `GEO_SMOKE_PORT` 且端口被占用时直接报错）。

`pagecheck.mjs` 按页面请求顺序逐字段校验（77 条，含 13 页单侧栏/单高亮/无死链、详情接口 `data_range` 类型，以及 13 页必须引入统一样式表、不得隐藏侧栏的样式契约）。`browsercheck.mjs` 调系统 Chrome/Edge 的 `--dump-dom`，验证 **JS 真的在浏览器里跑完**：22 个 URL 无报错、无「加载中」残留，关键表格/图表与**明细翻页条**确实渲染出来（问题监控按 tab 拆成 10 个 URL 分别断言）。这一层不装 Playwright、不加依赖 —— HTTP 200 和 `node --check` 都拦不住「页面读了接口里不存在的字段」。找不到浏览器时会明确打印 SKIP，不假装通过。`pagercheck.mjs` 在桩 DOM 里加载真实 `app.js` 模拟点击翻页（22 条），验证 10 行/页、边界禁用、换查询回第 1 页、页码直达跳转。另对每个 `<script>` 块跑 `node --check`。测试库为 `%TEMP%\geo_test_suite.db`。

关键断言都做过**变异自检**（把实现改错，确认检查会变红）：迁移 checksum、导入幂等、dry-run 回滚、竞品识别四处均已验证「不是摆设」。

## 实现范围与偏差

| 计划 | 实际 | 影响 |
|---|---|---|
| FastAPI | Starlette 原生路由 | 状态码与响应体对齐 `openapi.yaml`；无自动文档生成 |
| SQLAlchemy + Alembic | 标准库 `sqlite3` + 自建迁移链（`backend/migrations/`） | 表与唯一约束对齐 `data-model.md`；版本表 + checksum 校验 + 老库基线认领，够用且零依赖 |
| PostgreSQL + ClickHouse | SQLite 单库 | 指标实时从明细聚合；十万级样本会变慢 |
| pytest / Schemathesis | `unittest` + smoke + Node 字段契约 | 无自动化契约 fuzz |
| Next.js 前端 | 12 个零依赖静态页（决策 004） | 无组件化与类型检查，靠脚本闸门兜底 |
| 服务端 PDF 渲染 | 浏览器打印（决策 005） | 需人工点一次「另存为 PDF」，不能后台批量 |

替换只发生在 `repositories/`、`api/`、`frontend/` 三层，口径纯函数 `domain/metrics.py` 不变。决策记录在 `docs/decisions/002`~`005`。

## 目录

```text
backend/
  run.py                     # 一键启动（seed 判断 + 端口检查 + 开浏览器 + uvicorn）
  migrations/                # 001 基线 / 002 导入结构 / 003 品牌类型，按版本号顺序应用
  data/                      # 导入示例（CSV 中文表头、JSONL 带提及与引用）
  src/geo/
    domain/metrics.py        # 口径纯函数
    domain/methodology.py    # 口径说明（页面与报告共用）
    domain/enums.py
    migrate.py               # 迁移链：版本表、checksum、老库认领
    collectors/              # 采集器协议 + 注册表 + 离线兜底实现
    services/                # metric / config / report / content / import / collect
    repositories/            # sqlite / config / report / import
    api/app.py               # 路由 + 同源托管 13 个页面
    api/nav.py               # 服务端统一侧栏（唯一导航定义）
    seed.py
  tests/unit, tests/integration, tests/fixtures.py
  scripts/                   # smoke / 三个页面检查 / import_answers / sample_gaps / collect
frontend/static/             # 13 个页面 + theme.css + app.css + app.js
start.bat                    # Windows 双击启动
specs/001-geo-insight-platform/  # spec / plan / data-model / contracts / tasks / quickstart
specs/002-content-studio/        # 内容工作室
docs/decisions/                  # 002 不用 Docker、003 不复刻、004 静态页、005 打印导出
```

## 尚未实现（按用户要求暂缓）

- **真实平台采集**：登录态、风控、DOM 稳定性全部未验证。采集器协议与离线实现已就位（见「数据接入」），
  但没有接任何平台 API；`mention` 也没有自动提及解析器，靠 seed 或导入时的 `mentions` 字段写入
- **多用户与角色**：只有一个预置账号，没有注册、改密、角色区分（`app_user.role` 已在表里但没启用）；
  session 存在进程内，重启即失效，多进程部署会互相看不见
- 渠道真实登录与发帖（002 T019/T020）；按周聚合；片段级情感聚合；合规命中审计落库
- **审计只覆盖两类删除**（别名、竞品）。导入批次回滚同样是破坏性操作，还没接审计；
  也没有审计的查询接口（只有 `scripts/audit_log.py`）
- 契约 fuzz（Schemathesis）、交互 E2E（表单提交等完整用户流）、200 条黄金样本回归。页面渲染层已由 `browsercheck.mjs` 覆盖、分页翻页点击已由 `pagercheck.mjs` 覆盖；删除确认做过一次 CDP 真实点击验证（临时脚本，未入库），但**其余交互（表单提交、下钻抽屉、打印）仍未模拟用户操作**
- **AI 成稿与积分**：按决策 003 整体取消，不是待办
