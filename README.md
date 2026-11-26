# GEO Insight

品牌在 AI 答案中的可见度监测。当前是可运行的**面试演示版**：一个 Python 进程同时提供 13 个页面和全部接口，数据落在一个 SQLite 文件里，双击 `start.bat` 即可启动。

**第一次打开系统、想知道"这到底是干嘛的、该从哪看起"→ 先读 [`docs/使用手册.md`](docs/使用手册.md)**（小白版：大白话讲定位、配真实截图、手把手走一条完整流程，末尾附术语对照表）。本 README 是启动/验证/排障的技术索引，两者分工不同。

规格、计划、数据模型、接口契约在 `specs/001-geo-insight-platform/`；内容工作室单独在 `specs/002-content-studio/`。

## 三分钟演示路径

1. **双击 `start.bat`**（Python 3.10+ 即可，不装依赖、不用 Docker）。浏览器自动打开**登录页**，
   账号已预填，密码 `geo-demo-2026`（启动日志也会打印）。登录后进入看板。
2. 看板 `/`：8 张指标卡 → 趋势 → 问答明细 → 信源明细。可主动讲三句：
   - 平均排名显示「样本不足」，是因为只有 2 个带列表位次的样本，**低于 3 条不给数字**（口径规则，不是 bug）；
   - 点指标卡会下钻到构成该指标的样本，再点可看答案原文与引用；
   - 顶栏「口径说明」把分母、位次定义、去重规则讲清楚，阈值由接口下发而非写死。
3. `/settings` 监控配置：新建监控对象、批量导入问题（自动去重）、切换核心/长尾、增删别名与竞品（竞品集合版本递增）、
   维护行业/地区/核心词（核心词决定答案是否被判「疑似跑题」）。
4. `/reports` 报告中心（**演示数据已自带 5 份报告**，含不可比与 +23.3pp 可比两类）→ 打开详情页，点「打印 / 导出 PDF」。报告是生成时刻的不可变快照，带 `payload_hash`；口径或模型集合变化时标注「不可比」且不输出涨跌幅。
5. `/studio` 内容工作室：三栏（创作要求 / 正文 / 事实证据）→ 发布通道（演示态）→ 发布后提及率 **22.2% → 40.0%，+17.8pp**。
6. 左侧栏按**工作台 / 优化执行 / 分析报告 / 品牌资产 / 探索工具**五组展开（决策 009 逐字对齐参考系统），另外 6 个页面（问题监控、AI 舆情、优化机会、优化任务、品牌资产、行业信源分布）都接真实接口，不是占位页。`/settings` 不在菜单里，从问题监控的「问题设置」或左栏「＋ 新建」进入。

演示结束关掉黑色命令行窗口即停止服务。**重复双击不会报错**，服务已在运行时会直接帮你打开页面。

**打不开了怎么办**（按顺序试，多数是第 1 条）：

1. **看 `start.bat` 开出来的那个黑窗口还在不在。** 服务就活在那个窗口里，窗口一关就停。
   如果服务是别的程序（比如 AI 助手）在后台临时拉起来的，那个进程会随对话结束一起被回收，
   浏览器就会显示「无法访问此网站」。重新双击 `start.bat` 即可，数据不会丢。
2. 窗口在、页面还是打不开：端口被别的程序占了。`netstat -ano | findstr :8123` 看是谁，
   或换个端口启动：`set GEO_PORT=8124` 再双击。
3. 页面能开但数据一片空白：先 `Ctrl+Shift+R` 强制刷新（静态资源没有版本号，改过前端会命中缓存）；
   还不行就看黑窗口里的报错，那里有完整堆栈。

### 换一台电脑，先跑自检

```bash
python backend/scripts/doctor.py
```

这个仓库会在多台机器上轮流跑，而有些东西是**机器的、不进仓库的**：Python 版本、装没装依赖、
浏览器装在哪个路径、本地账密文件、数据库状态。它们出问题时的症状都是「打不开」或
「按钮没反应」，从报错看不出是哪一层。自检脚本把这些一次列出来，9 项检查不到 1 秒。

必须解决（`[FAIL]`）的才拦住启动，`[WARN]` 只是提醒。几件换机器后必然要重做的事：

- **数据库不跟着 git 走**（`*.db` 已忽略）。新机器首次启动会建库并灌演示数据；
  想把旧机器的数据带过去，就手动拷 `geo.db`。
- **渠道账密文件 `backend/data/channels.local.json` 不入库**，新机器要重新配；
  没有它也能用，发布时转人工扫码/输密码。
- **各平台登录态 `backend/src/.browser-profiles/` 不入库**，新机器首次发布要扫一次码。

**数据库位置固定跟随项目根，不跟随当前目录**：无论双击 `start.bat`（它会先 `cd backend`）
还是命令行在项目根启动，都指向同一个 `geo.db`。这一点是刻意的——
早先库路径是相对的，两种启动方式各写一个库，数据看起来「丢了」（见决策 013）。

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
| `/monitor` | 问题监控工作台：左侧选对象 → 右侧 10 个 tab，全部接真实数据 |
| `/insight` | AI 舆情：趋势、模型对比、信源结构与采样计划 |
| `/opportunities` | 优化机会：未提及组合、提及率样本下钻与采样计划 |
| `/tasks` | 优化任务：文章列表、发布记录与效果关联 |
| `/assets` | 品牌资产：品牌信息、别名、竞品集合与采样计划 |
| `/sources` | 行业信源分布：媒体排行、类型分布、发布时间完整度、URL 去重内容明细 |

**问题监控工作台**（对齐参考视频的批次 A，计划见 `docs/video-reference-plan.md`）

`/monitor` 用一页承载 10 个维度：左侧是监控对象列表（可搜索），右侧是对象头 + tab 栏 + 内容区。
切换对象和 tab 都写进 URL（`?tracker=…&tab=…`），刷新与前进后退都回到原处。

| tab | 状态 |
|---|---|
| 指标看板 | **已接** `/metrics/overview` + `/metrics/by-question` + `/metrics/trend`：5 张指标卡 + 12 个问题的分问题表现（含对比条）+ 提及率趋势（缺数据断开）+ 引用概览 |
| 问答明细 | **已接**：问题下拉 + 回答列表（服务端分页）+ 右栏引用信源，点一条回答右栏跟着换；行内「截图」导出该回答 PNG，「纠正」人工标注并回显 |
| 指标明细 | **已接** `/metrics/{m}/samples`：5 个指标可切，表里标出**哪些样本进了分母**，12 行/页 |
| 趋势分析 | **已接** `/metrics/trend`：按日 / 按周切换（`granularity=week`，周一为起点，标出实际天数），提及率折线（缺数据断开）+ 摘要表；平均排名按「位次和 ÷ 位次数」重算，不是日平均值再平均 |
| 信源分析 | **已接** `/citations/summary` + `/citations`：总量、类型分布、媒体排行、URL 去重明细（服务端分页） |
| 竞品排名 | **已接** `/competitors/ranking`：提及数、占比、平均位次、情感分布，带对比条 |
| 竞品统计 | **已接** `/competitors/cooccurrence`：共现矩阵（含本方品牌），矩阵对称性有断言 |
| 视频·图文引用 | **已接** `/citations?mediaType=`：7 个类目可切，服务端过滤后分页 |
| 情感倾向 | **已接** `/metrics/sentiment-by-model`：按模型拆正/中/负，样本不足的模型只标「样本不足」不给比例；合并后与看板口径逐位一致 |
| 采集健康 | **已接** `/collection-health`：应采 / 已采 / 缺口 / 达成率，按模型与按天两张表；没配采样计划时返回 `configured=false` 并说明原因，不画空图 |

**核心词**在 `/settings` 里维护（如「律所，律师，刑事辩护」）。它决定**跑题判定**：
答案正文一个核心词都没出现时，问答明细里标「疑似跑题」；没配核心词时接口返回 `off_topic: null`
（未判定），不会把「没配置」显示成「全都跑题」。这条判断由服务端算（`domain/relevance.py`），
前端只负责显示，避免两个地方各写一套字符串匹配。

**人工纠正（P2）**：问答明细每条回答有「纠正」，写入 `/v1/answers/{id}/correction`
（GET 回显 / POST 保存 / DELETE 撤销），可标「本品牌是否提及 / 情感 / 位次 / 备注」。
纠正存**独立表**，不写回 `mention`——黄金窗口的 19 项手算值基于自动识别，
写回会让已验收基准作废；纠正作为人工事实源单独可查、可审计、回显「已纠正」标记。
截图用 canvas 把该回答正文画成 PNG 下载，零前端依赖、不靠 CDN。
CDP 真点由 `p2check.mjs` 守（8 条，含刷新后回显与取消不改），跑在新实例端口、不碰 8123。

10 个 tab 全部接真实数据，没有占位或写死数字。新增的三个聚合接口（竞品排名、竞品共现、情感按模型）
与既有口径做了交叉校验：情感按模型合并后与看板总览逐字段相等，采集健康的「已采」等于总览 `valid_total`。

侧栏由 `backend/src/geo/api/nav.py` 在服务端统一注入，13 个页面只有一份导航定义；导出按钮点击时用当前筛选条件拼真实下载地址，没有 `href="#"` 假入口。

```bash
python backend/run.py --port 8124     # 换端口
python backend/run.py --reset         # 重建演示数据（清表，不删文件）
python backend/run.py --no-browser    # 不自动开浏览器
```

数据库就是项目根的 `geo.db` 一个文件（路径由 `settings` 从项目根推导，**不跟着 cwd 走**——
所以双击 `start.bat` 和命令行启动用的是同一个库，见决策 013）；`Ctrl+C` 停止。

`--reset` 会重建全部演示数据，**包括 5 份报告**（含一份不可比、一份 +23.3pp 可比），所以重置后报告中心不会是空的。

**最小日志（H4-5）**：每个请求在控制台输出一行 JSON，含 `method/path/status/duration_ms/request_id`
（只记 URL 路径、不记 query，避免把 token 写进日志）。`/health` 的 `ops` 字段给出进程内最近 200 个请求的
错误率（只算 5xx）、慢请求数（默认 >1000ms）、P95——没有样本时错误率是 `null` 而非 0。
`GEO_LOG_WINDOW` / `GEO_SLOW_MS` 可调。这是进程内的，重启清零、不落盘，不假装成持久化监控；
`/health` 与 `/static` 自己不计数，避免看板探针污染分母。

## 备份与恢复

数据只有一份（项目根的 `geo.db`），丢了自己重建不出来——里面是真实的采集结果与配置。

```bash
python backend/scripts/backup.py                  # 备份一次（服务开着也能备）
python backend/scripts/backup.py --list           # 看有哪几份、各有多少数据
python backend/scripts/backup.py --keep 14        # 备份 + 只留最近 14 份
python backend/scripts/backup.py --prune 7        # 只清理，不备份
python backend/scripts/backup.py --verify 某文件   # 校验一个备份能不能用
python backend/scripts/backup.py --restore 某文件  # 用它覆盖当前库（**先停服务**）
```

Windows 上双击 `backup-team.bat` 等同 `--keep 14`。

**为什么不是「把 geo.db 复制一份」**：SQLite 在 WAL 模式下，已提交的数据可能还留在
`geo.db-wal` 里没写进主文件。复制主文件会得到一份能打开、能查询、只是少了最近几条的备份，
而且**不会报错**——等真需要它的那天才发现少了关键的一段。`backup.py` 用 `VACUUM INTO`
让 SQLite 自己生成一致快照，服务在跑也能安全执行。

**恢复**必须停服务（关掉 `start.bat` 的黑窗口）。会先尝试拿独占锁，拿不到直接报错、
不会硬写。恢复前自动把当前库存成 `pre-restore-*`（选错文件时用它回退），
恢复后自动跑迁移链，所以旧版本的备份也能直接用。

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

**② 采集器（DeepSeek 真实调用已跑通，豆包待 key）**

`collectors/` 定义 `Collector` 协议（`available / supports / collect`），注册表显式登记，不做目录扫描。
`deepseek`（官方 API · 联网搜索）已做真实调用；`doubao`（火山方舟）代码在但没配 key、没做真实验证；
`csv_bank` 是离线兜底。真实 key 放 `backend/data/keys.local.json`（已 gitignore），日志只报来源、不打印 key 片段。

```bash
cd backend
python scripts/collect.py --list                       # 看哪个采集器可用
# 建独立试点对象（真实答案只进这个对象，不灌演示对象）
python scripts/setup_pilot.py --name "辽征试点" --brand "辽宁辽征" \
  --target "沈阳哪一家律所最专业" --control "沈阳律师事务所排名前十" \
  --model deepseek --core-times 3 --long-tail-times 1 --dry-run
# 去掉 --dry-run 真正建；然后跑一轮，跑完打印账并正常退出（不靠 Ctrl+C）
python scripts/collect.py --tracker <上一步的 tracker id> --model deepseek --collector deepseek \
  --loop --rounds 1
```

- **一天只采计划里的缺口**：同日再跑一次缺口为 0，一次 API 都不调（已实测 `inserted=0`）；
  超计划要加 `--force`，超出的样本会多算进指标。
- `--rounds 1` 是给脚本/定时任务用的：跑完一轮打印账并退出 0；不加则一直循环，靠 Ctrl+C 停。
- `--loop` 每轮打印「缺口 → 补采数 → 失败数 → 今日还差」；连续 3 次限流/超时收手，认证失败立即停。
- 采集后**立刻跑提及识别**（不用等下一步）；原始响应落 `backend/data/raw/<日期>/`，`answer.raw_uri` 可回溯。
- `answer.cost` 故意留空：单价随账号与时段变，写死会埋错账；只记 `token_in/token_out`。

**不做的路**：不爬网页、不模拟登录、不碰风控对抗。产品形态是 1–2 人内部工具，走官方 API + 人工兜底。

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

**⑤ 渠道发布（已接入系统，决策 011；七渠道齐备）**

七个渠道全部有真实发布器（`src/geo/publishers/`），浏览器登录态持久化在
`backend/src/.browser-profiles/<渠道>/`（已 gitignore，绝不入库）。**渠道连接、登录态检测
与发布已接进系统**：`/studio` 发布通道面板点「连接」弹浏览器扫码、「检测」开无头浏览器
验证（约 10-15 秒）、勾选后「一键发布」走真实浏览器自动化；发布记录状态（published /
draft_saved / blocked / failed / unknown）与失败原因如实落库可查。CLI 形态保留供校准：

```bash
cd backend
python scripts/sohu_publish.py login                # 搜狐号:扫码,存会话
python scripts/sohu_publish.py publish draft.json
python scripts/zhihu_publish.py login               # 知乎:创作中心(www.zhihu.com/creator)
python scripts/zhihu_publish.py publish draft.json
python scripts/wechat_publish.py login              # 公众号:mp.weixin.qq.com 扫码
python scripts/wechat_publish.py publish draft.json   # 默认只存草稿箱
python scripts/baijiahao_check.py --init            # 百家号:开放平台 API(需后台密钥)
```

- draft.json 通用字段 `title` / `content_html`；各渠道标题字数限制不同（百家号 8-40、搜狐 5-72、知乎 2-100、公众号 ≤64），长度不对在**开浏览器之前**就拒绝。
- **账密可选**：`backend/data/channels.local.json`（已 gitignore，与百家号密钥同一安全等级）写
  `{"sohu": {"username": "...", "password": "..."}}`，`login` 时自动填表提交；弹验证码/滑块/短信
  一律转人工在窗口里完成，**工具不做任何绕过**（范围守卫）。没配置就走扫码。
- 2026-11-24 实测（登录态逐页探测）：搜狐整体改版——老发文地址 `mp/post/createArticle` 已 404，
  新后台在 `mpfe/v4`，编辑器 URL 直连会被弹回，必须从内容管理页点「发布内容」按钮进
  （SPA 路由）；未登录不再跳 404 而是静默弹回门户页，`check` 以「停在后台 URL」为判据。
  **未实名认证的账号发不了文章**（点「发布内容」只进发动态），`publish` 会返回
  `blocked` 而不是硬闯；完成实名后需要用 `--show` 校准一次发布按钮/频道选择器。
- 知乎/公众号的编辑器选择器按公开资料编写（登录墙后无法离线核实），**首次发布加 `--show` 人工盯一遍**，确认流程没改版再转无头。
- 公众号默认「保存为草稿」：群发要点发表、再由管理员在手机上安全验证，这一步平台设计上必须人来，工具不做任何绕过。
- 发布结果如实报 `published / draft_saved / rejected / not_logged_in / unknown`，`unknown` 一律人工到后台确认——宁可不报成功，不伪造成功。
- 渠道状态是**真实登录态**（六态：connected / disconnected / expired / connecting /
  needs_config / not_implemented），检测/登录结果带附注落库，页面加载只读库不开浏览器。
- 同一渠道的登录/检测/发布互斥（浏览器 profile 只有一份），并发请求回 409；
  `GEO_PUBLISH_WORKER=0` 时发布走纯入队、connect 不派登录子进程（冒烟/自动化测试用）。
- **全局发布并发闸门**：`GEO_PUBLISH_CONCURRENCY`（默认 3）限制同时跑的浏览器发布数——
  一个 worker ≈ 一个 Chrome（400–500MB），2 核 4G 跑 3 路约 1.8G，留足系统余量；
  4 核 8G 可调到 4。超出的发布停在 `queued`（页面显示"排队中"），拿到槽位才写 `running` 并
  开浏览器；百家号走 API 不起浏览器，**不占槽**。闸门是进程内信号量，和 session 一样只对
  单进程部署有效（决策 012）；服务重启会把遗留的 `queued`/`running` 一起清扫成 failed，
  不会永远显示"排队中"／"发布中"。
- **第二批（2026-11-24）**：抖音（图文通道，draft 需 `images`）、今日头条、小红书（笔记形态，
  同样需图）发布器就位并注册进系统。
- **文章配图**：内容工作室的正文编辑区可上传图片（png/jpg/gif，单张 ≤10MB），图片挂在
  文章上、存 `backend/data/article_images/`（已 gitignore），文件名记进 `article.images`。
  发布时 draft 里带上这些图片的**绝对路径**，抖音/小红书直接用；纯文字渠道忽略。
  文件名由服务端生成（uuid），上传校验扩展名+文件头，取图/删除都校验图片归属，
  避免目录穿越和越权访问。没配图就发抖音/小红书，发布器会返回 `rejected` 并说明缺图。
  **注意网络现状**：本机对 toutiao.com / xiaohongshu.com 全域 TLS 被重置（抖音/搜狐/知乎/
  公众号正常，换 IP 无效），两渠道的登录与发布要等网络恢复才能校准——check 会把连接错误
  如实落附注，不装有效。

### 服务器部署（无显示器环境）

服务器没有图形界面，必须同时满足三件事，否则浏览器起不来或立刻崩：

1. **`GEO_PUBLISH_HEADLESS=1`**：worker 调 CLI 时给浏览器渠道追加 `--headless`。
   默认 0 是本机的"有头可盯"行为；服务器不设这个，知乎/公众号/抖音/头条/小红书五个渠道
   会在浏览器启动那步直接崩（它们默认有头）。搜狐本来就无头、百家号走 API，两者不受影响。
2. **登录态要先在本地登录好再上传**：扫码/验证码必须人来做，服务器做不了。在本机对每个渠道
   跑 `python scripts/<渠道>_publish.py login` 登录成功，把整个
   `backend/src/.browser-profiles/<渠道>/` 目录传到服务器同一路径（该目录已 gitignore）。
   登录态会过期，过期后要重做这一步。
3. **Linux 启动参数自动加**：`launch()` 在 Linux 下自动带 `--no-sandbox`（root 必需）和
   `--disable-dev-shm-usage`（Docker 的 64MB /dev/shm 会让 Chrome 崩）。非 root 容器想保留
   沙箱，设 `GEO_CHROME_SANDBOX=1`（此时只加 shm 参数）。Windows/macOS 不加任何参数。

**安全取舍**：headless 环境的浏览器指纹（`navigator.webdriver`、WebGL 软件渲染、插件数为 0）
比本地更容易被平台风控识别，弹验证码时也没人能点——代码明确不做任何绕过，遇到验证就停到超时。
所以服务器自动发布只适合风控宽松的渠道，小红书这类重风控渠道仍建议本地有头盯跑。

**验证到哪**：参数拼接与平台判断有 8 条单测 + 3 次变异守着；真实服务器端到端发布**没有跑过**
（需要真实账号登录态和平台网络），属于"代码就位、未实机验证"。

### 上线前自检 + 进程守护

服务器和"两台电脑轮流跑"最大的差别是：**本地能跑，上线才炸的那几项没人拦**。

**第一道门：空库首启演练**（所有单测都在已建好的库上跑，从零起服务这条路以前没人验过）：

```bash
python backend/scripts/deploy_rehearse.py     # 临时空库+随机端口，跑真 run.py，退出码必须 0
```

它起一个真服务（不碰真实 geo.db），验：空库→迁移全链→seed→`/health`→13 个页面与
4 个首屏接口全 200→迁移版本等于仓库最高版本。以后谁加了迁移却没接上，这条会先红，
而不是等服务器首启才炸。注：`GEO_AUTH_REQUIRED=0` 只是让页面能直接打开，鉴权链路另有测试。

**第二道门：配置自检**（叠加在普通自检之上，只读、不改配置）：

```bash
python backend/scripts/doctor.py --server     # 退出码必须是 0
```

它额外查：演示默认密码还没改 / 鉴权被关（FAIL）、Linux 无显示器却没开
`GEO_PUBLISH_HEADLESS`（FAIL）、`/dev/shm` 与磁盘空间、以及 HTTPS 反代提示。
前两项是决策 008「部署前必须改密码」从文档约定变成真能拦的检查；
HTTPS 那条守的是应用卡 iframe 白屏——cookie 只在请求"看起来是 HTTPS"时才带
`Secure; SameSite=None`，反代没透传 `X-Forwarded-Proto: https` 就会弹回登录页。
判据写成纯函数，13 条测试 + 2 次变异守着（本地造不出"Linux 无 DISPLAY"，所以造函数入参）。

**进程守护**：`infra/geo-insight.service` 是 systemd 模板，按注释改好用户/路径/密码后：
`sudo cp … /etc/systemd/system/` → `daemon-reload` → `enable --now` → `journalctl -u geo-insight -f`。
只起 **1 个 worker**：session 和发布并发闸门都是进程内的，多 worker 会让登录态漂移。
**未在真实 Linux 实机验证**——首次部署先手动 `python backend/run.py --no-browser` 起一次确认，
再交给 systemd。

**依赖版本**：`requirements.txt` 是宽松声明（开发时装新版用），服务器**必须用锁文件**：

```bash
pip install -r backend/requirements.lock.txt
```

lock 固定了全量 579 项测试通过时的确切版本（starlette 1.3.1 / uvicorn 0.51.0 /
playwright 1.61.0 及传递依赖）。为什么不能直接装宽松声明：`>=0.40` 允许装到 1.x 大版本，
行为可能变；且 playwright 没被宽松声明覆盖——服务器照它装完，发布功能直接不可用。
6 条测试守着「直接/传递依赖齐全、每行精确 pin、不含测试依赖、与宽松声明不打架」。
升级依赖的做法：本地先跑全量回归，绿了再更新 lock 的 pin。

**定时备份**：`infra/geo-insight-backup.service` + `.timer` 每天 03:17 跑 `backup.py --keep 7`，
`Persistent=true` 保证宕机那天的备份在开机后补跑。备份用 `VACUUM INTO` 读一致性快照，
**服务不用停**；恢复才要先停服务。安装：

```bash
sudo cp infra/geo-insight-backup.* /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now geo-insight-backup.timer
systemctl list-timers geo-insight-backup.timer     # 确认下次触发时间
```

**两套 unit 的 `GEO_DB_PATH` 必须一致**——主服务把库放哪，备份就得备哪份；配置不一致时
备份出的是空库/旧库，平时看不出来，恢复才发现。.timer 未在真实 Linux 实机验证，
首次部署用 `sudo systemctl start geo-insight-backup` 手动跑一次、看 journalctl 确认再放手。
备份名解析也修了一处真 bug：`--list` 打印的裸文件名现在能直接喂给 `--verify`/`--restore`
（此前按 cwd 解析会 NOT_FOUND），4 条测试守着。

**恢复演练**（H4-4 判据）：备份能不能真恢复、旧备份跨迁移能不能自动补齐，
别等真出事那天才发现。跑：

```bash
python backend/scripts/restore_rehearse.py
```

它拿 `backups/` 里真实的旧备份（当前是 v15），在临时库上演一遍恢复，验：迁移自动跑到
当前版本（016–020 一个不落）、数据条数回来、`pre-restore-*` 安全副本生成、恢复后新列新表
齐全。全程不碰真实 `geo.db` 与 `backups/`。**没验证的是**：真实停服后恢复（脚本用临时库，
不干扰正在跑的服务）与恢复后的全量 HTTP/浏览器回归——那需要专门停服窗口。

**上线清单**（缺一项就别开公网）：

1. `GEO_ADMIN_PASSWORD` 换成强密码，`GEO_AUTH_REQUIRED=1`；
2. 反代终结 TLS 并透传 `X-Forwarded-Proto: https`，否则应用卡白屏；
3. `GEO_PUBLISH_HEADLESS=1`，本地登录好的 `.browser-profiles/` 传到同一路径；
4. 数据库路径固定（默认项目根 `geo.db`，可用 `GEO_DB_PATH` 指到数据盘）；
5. `pip install -r backend/requirements.lock.txt`（不要用宽松声明）；
6. 业务日期按北京时间算，不跟服务器时区（`date.today()` 在 UTC 机器上凌晨会错一天；
   采集/指标已用 `business_time` + `substr` 收口，见决策 007）；
7. 定时备份 timer 已 `enable --now`，两套 unit 的 `GEO_DB_PATH` 一致，手动跑一次确认；
8. `deploy_rehearse.py` 与 `doctor.py --server` 都退出 0，再 `systemctl enable --now geo-insight`。

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
python -m unittest discover -s tests -t . -p "test_*.py" -v   # 全量（系统 Python 3.12；含 HTTP 层鉴权、审计、核心词、周聚合、畸形输入 fuzz）
python scripts/smoke.py                                        # 86 条真实 HTTP 断言（自己登录，自动选空闲端口）

# 后三个不看登录态：启动服务时显式关掉鉴权
GEO_AUTH_REQUIRED=0 python run.py --no-browser &
node scripts/pagecheck.mjs                                    # 77 条页面字段契约 + 侧栏结构 + 样式契约
node scripts/browsercheck.mjs                                 # 75 条真实浏览器 DOM 检查（需系统 Chrome / Edge）
node scripts/pagercheck.mjs                                   # 22 条分页组件行为测试（真实 app.js + 模拟点击/跳转）
node scripts/interactioncheck.mjs                             # 46 条 CDP 真实点击（删除确认/暂停恢复/新建抽屉+字段落库/表格浏览/首页下钻+口径/报告打印/采集任务/设置删除确认，需 Chrome/Edge）
node scripts/imagecheck.mjs                                   # 8 条 CDP 真点：文章配图 选图→上传→预览取真 PNG 字节→删除→无 JS 异常（自包含测试图，需 Chrome/Edge）
node scripts/p3check.mjs                                      # 8 条 CDP 真点：模型×端筛选（选端→URL带client→API/竞品tab请求带client→刷新保持→清除还原，需 Chrome/Edge）
node scripts/freshcheck.mjs                                   # 服务/工作区代码版本一致性（旧进程假红灯的机器判据）
python scripts/deploy_rehearse.py                              # 部署首启演练：临时空库起真服务，验迁移链+seed+13页+4接口
python scripts/restore_rehearse.py                             # 恢复演练：真实旧备份→临时库，验迁移补齐+数据完整+安全副本
# P2 专项：先在 8137 起新实例（GEO_PORT=8137），再跑：
GEO_BASE=http://127.0.0.1:8137 node scripts/p2check.mjs        # 8 条 CDP 真点：截图产图/纠正落库回显/取消不改
```

全部通过。各脚本精确条数以 `specs/003-next-stage-roadmap/tasks.md` 的「验证状态」为准
（单测 476，2026-11-25 干净检出实测全绿；测试数只增不减）。

**检查红了先查服务新旧**：`browsercheck` / `interactioncheck` 打的是正在运行的 8123 进程。
改完代码不重启，旧进程不会加载新版本——表现是 `/studio` 六阶段步骤条、
`/monitor?tab=health` 失败率告警这类断言假红，但代码是对的。
先跑 `node scripts/freshcheck.mjs`：它比 `/health` 的 `code_rev`（服务启动时读一次 HEAD）
与工作区 HEAD——一致就 PASS，不一致明确报「旧进程，请重启」，退出码 1。
旧服务没有 `code_rev` 字段时会说「无法判断」而不是误报通过（那说明服务早于本检查启动，
更该重启）。`Ctrl+C` 停掉再双击 `start.bat` 重开即可。
换端口用 `GEO_BASE=http://127.0.0.1:8124 node scripts/freshcheck.mjs`。

用**系统 Python** 跑（`start.bat` 用的也是它）：`backend/.venv` 是历史残留，里面只有 pip，
`starlette` 装在系统环境。空 venv 里跑单测不会报错，HTTP 层那 10 条会自动跳过并说明原因。

`smoke.py` 自建临时库、在**自动挑选的空闲端口**启动 uvicorn、逐个请求断言后关闭进程，不碰演示数据，也不会误连到正在运行的开发服务（显式指定 `GEO_SMOKE_PORT` 且端口被占用时直接报错）。

`pagecheck.mjs` 按页面请求顺序逐字段校验（77 条，含 13 页单侧栏/单高亮/无死链、详情接口 `data_range` 类型，以及 13 页必须引入统一样式表、不得隐藏侧栏的样式契约）。`browsercheck.mjs` 调系统 Chrome/Edge 的 `--dump-dom`，验证 **JS 真的在浏览器里跑完**：22 个 URL 无报错、无「加载中」残留，关键表格/图表与**明细翻页条**确实渲染出来（问题监控按 tab 拆成 10 个 URL 分别断言）。这一层不装 Playwright、不加依赖 —— HTTP 200 和 `node --check` 都拦不住「页面读了接口里不存在的字段」。找不到浏览器时会明确打印 SKIP，不假装通过。`pagercheck.mjs` 在桩 DOM 里加载真实 `app.js` 模拟点击翻页（22 条），验证 10 行/页、边界禁用、换查询回第 1 页、页码直达跳转。`interactioncheck.mjs` 走 CDP 真点按钮（46 条）：删除弹框的禁用态、输错名称不放行、取消不删、暂停与恢复、新建抽屉的开合与字段（官网/核心词/问题/16 渠道清单）、表单提交真建对象并验证新字段落库（官网回显、核心词、`*`/核心问题标记、问题导入）、对象头「表格浏览」直达表格视图、首页指标卡下钻抽屉（真加载样本表+分页）、口径说明抽屉（真加载定义表与规则）、报告详情加载真快照并点「打印」真调用 `window.print`（注入计数桩，headless 下不弹原生对话框）、采集健康页任务表行数与接口真实 run 数一致（不是空壳）、设置页别名删除确认（弹框文案说清后果 + 取消不掉数据，只读不写库）；按决策要求，跟只读 DOM 检查分开——不可逆操作必须真点一遍。写库的用例（表单提交）用产品自己的 DELETE 接口在 finally 里清理，跑完 tracker 数回到原值。另对每个 `<script>` 块跑 `node --check`。测试库为 `%TEMP%\geo_test_suite.db`。

`test_input_validation.py` 是 Schemathesis 装不了之后的**手写契约 fuzz**：用 TestClient 对真接口打畸形日期/整数/枚举/超长串/特殊字符，断言不 500、错误带 `request_id` 可追溯、分页 limit 被钳制、合法参数仍 200。它守的是「新增接口漏一个 `try/except` 就打出 500」这类回归——变异自检时删掉分页的异常保护，畸形整数当场 500、测试红，证明断言抓的是真漏洞。

关键断言都做过**变异自检**（把实现改错，确认检查会变红）：迁移 checksum、导入幂等、dry-run 回滚、竞品识别、畸形输入四处均已验证「不是摆设」。

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

- **真实平台采集**：DeepSeek 已跑真实调用并入库（见「数据接入②」），含 1 条最小验证与 6 条试点基线；
  豆包采集器已实现但未配 key、未做真实验证；Kimi 等尚未接。不爬网页、不模拟登录、不做验证码对抗。
  提及识别 v1 已交付（精确/归一化匹配，重算入审计），且采集后自动执行，不再依赖导入时的 `mentions` 字段
- **多用户与角色**：只有一个预置账号，没有注册、改密、角色区分（`app_user.role` 已在表里但没启用）；
  session 存在进程内，重启即失效，多进程部署会互相看不见。**决策 012 已明确：当前只服务 1–2 人，
  不做多用户/租户隔离/PostgreSQL，这不是待办**
- **渠道发布的收尾**（002 T019/T020 已随决策 011 完成，七渠道齐备）：页面/接口集成已上线，
  剩余为——知乎/公众号选择器未经实测校准（登录墙后，首跑用 `--show`）；搜狐待实名认证后
  校准发布按钮/频道；**本机到 toutiao/xiaohongshu 的 TLS 被重置，两渠道待网络修复后
  登录校准**（疑与本机代理/安全软件有关）；抖音/小红书发图文需要封面图素材（内容侧问题）。
  另：片段级情感聚合；合规命中审计落库（按周聚合已随 H2-5 完成，从本清单移除）
- **审计覆盖别名/竞品/对象删除与导入批次回滚**（`tracker.delete`、`import.rollback` 已接，
  删除与记账同一事务）。审计查询走 `scripts/audit_log.py`（`--action import.rollback` 只看回滚），
  没有 HTTP 查询接口——审计读取只做本地工具，不对外暴露。仍未接审计的破坏性操作：
  答案删除、问题删除（当前界面不提供这两个动作）
- 契约 fuzz（Schemathesis）、交互 E2E（完整用户流）、200 条黄金样本回归。页面渲染层已由 `browsercheck.mjs` 覆盖、分页翻页点击已由 `pagercheck.mjs` 覆盖；删除确认、暂停恢复、新建抽屉与字段落库、表格浏览、首页下钻/口径抽屉、报告打印、采集任务、设置页删除确认已由 `interactioncheck.mjs` 做 CDP 真实点击（46 条，已入库，可重复回归）。当前界面没有答案编辑功能（详情页只读），故不存在要模拟的编辑弹框
- ~~CSRF 防护~~ **已补上（003 US3，2026-11-24）**：双提交 cookie（`geo_csrf` + `X-CSRF-Token`
  头，决策 010 的洞清零）+ 登录限流（连续 5 错锁定，`GEO_LOGIN_MAX_FAILURES`/`GEO_LOGIN_LOCKOUT_S`
  可调）。注意 `GEO_AUTH_REQUIRED=0` 时鉴权与 CSRF 一并跳过（自动化检查专用）
- **AI 成稿与 AI 助手面板**：按决策 003 取消；决策 009 改判为 H2.5 恢复，但**真调 LLM 尚未接**（额度由脚本人工发放）。
  积分账本本身已落地，见下。

### 积分账本（决策 009：账本是真的，定价是假的）

`credit_ledger` 已落库：**余额 = 流水求和**，不另存"当前余额"字段；金额是整数分，不用浮点。
同一 `(ref_type, ref_id)` 只能记一笔，网络重试不会重复扣；冲正记反向流水，不删原记录。

```bash
python backend/scripts/credits.py --grant 10000 --reason "试点初始额度"
python backend/scripts/credits.py --balance
python backend/scripts/credits.py --list
```

**这个账本不用于收费**：额度由脚本人工发放，没有支付入口，界面上的数字只是演示"每次调用有成本"。
不要把它当计费系统接支付——决策 009 明确"账本真、定价假"。
