# Tasks: 下一阶段演进(真实数据接入与参考系统对齐)

**Input**: `specs/003-next-stage-roadmap/spec.md`
**Status**: 2026-11-24 起草,按 spec 优先级排期;US1 调研已启动

**说明**: 本文件编号独立于 001/002。002 的渠道收尾(T019/T020 剩余)由「渠道发布真实化」任务单独推进,不在本清单重复。001 Phase 7 的遗留项(T087 提及解析、T095 引用筛选等)已收编进本清单对应条目。

## Phase 0: 调研与路线决策(US1,P1,零代码)

- [X] T001 三平台准入调研结论文档:豆包 / DeepSeek / Kimi 每平台四项硬结论(准入条件、价格、限流、原始回答存储条款)+ 「自动采集 / 人工流程」分叉建议 per FR-001/SC-001 in `docs/platform-access-research.md`
  **2026-11-24 完成**:三平台均判定「可行」——个人实名即可开通,无企业门槛,存储 180 天原文均无条款障碍;月成本区间 ¥33(豆包 lite 低优)~ ¥2,400(Kimi k3 实时);所有信源为官方域名并附 URL。工程注意事项(Kimi 的 max_completion_tokens 陷阱、DeepSeek 思考 token 计费、豆包低优 flex)一并归档。
- [X] T002 路线分叉决策:按 T001 结论定主线(自动采集 vs 人工流程强化),结论回写 `docs/roadmap.md` 风险登记第一条 per FR-001/FR-002 in `docs/roadmap.md`
  **2026-11-24 完成**:风险登记第一条结案(三平台无企业门槛),H3 走自动采集;H3-2 首个 adapter 建议选 DeepSeek(个人/企业权益无差异、并发 2500、错峰半价)。人工流程保留为降级路径不拆除 → T003 不触发。
- [ ] T003 (条件)人工流程强化:缺样本清单 → 人工采集 → 导入闭环的日常使用文档补齐 per FR-002/SC-002 — 仅当 T002 判「无资质走人工」时启动

## Phase 1: H2 收尾(US2,P1,1.0 人日)

- [ ] T004 H2-6 问题集与监控对象绑定:一对象多问题集可切换,切换后问题列表、指标口径、核心词与跑题判定同步变化 per FR-003 in `backend/src/geo/services/tracker_service.py`, `backend/src/geo/api/app.py`, `frontend/static/index.html` + 新增迁移
- [ ] T005 H2-7 引用类型筛选:inline / reference / video 生效到接口与页面,筛选条数与手工 SQL 一致 per FR-004 in `backend/src/geo/api/app.py`, `frontend/static/index.html`;**过滤逻辑与 E1b(T016)必须同一份实现**,不允许两处各写一套(spec Edge 硬约束)
- [ ] T006 H2 收尾验证:黄金窗口断言(22.22% / 9 样本)不动,全量测试数只增不减 per FR-012/SC-007

## Phase 2: 安全补齐(US3,P1,对外演示前必须)

- [X] T007 CSRF token:所有写接口校验(一次性 token 或双提交 cookie,零依赖原则),无 token 或 token 无效返回 403 且数据不变 per FR-005 in `backend/src/geo/api/`(中间件层)
  **2026-11-24 完成**:双提交 cookie——登录时 session 带 csrf_token 并以非 HttpOnly 的 `geo_csrf` 下发,页面 JS(app.js/studio api()/logout 表单)放进 `X-CSRF-Token` 头,`CsrfMiddleware` 用 `secrets.compare_digest` 比对;未登录写仍走 401,鉴权关闭(自动化检查)时跳过。决策 010 的洞就此补上。
- [X] T008 登录限流:连续 5 次密码错误触发锁定,锁定期内正确密码也拒绝 per FR-006 in `backend/src/geo/services/auth_service.py`
  **2026-11-24 完成**:账号+IP 联合标识(单拦账号会被撞锁 DoS,单拦 IP 换账号即绕),进程内状态,阈值/时长走 `GEO_LOGIN_MAX_FAILURES`/`GEO_LOGIN_LOCKOUT_S`;登录页渲染锁定剩余秒数。
- [X] T009 既有测试适配:全量测试改为先取 token 再请求,允许按测试类做最小化适配而非逐条重写 per SC-003/spec Edge
  **2026-11-24 完成**:TestClient 类在登录后挂默认 `X-CSRF-Token` 头(每类一行);smoke 的 `_request` 从 cookie 罐取 token 自动附带。按类最小化适配,没逐条重写。
- [X] T010 安全验证:模拟跨站写请求(带 cookie 无 token)100% 被 403;连续 5 错后第 6 次登录被限流 per SC-003
  **2026-11-24 完成**:集成测试 5 条(缺 token 403/伪造 token 403/带 token 放行/限流锁定/锁定过期)+ 冒烟 4 条显式断言;两个变异自检过(拿掉 CSRF 校验红 3 条,拿掉限流红 2 条)。

## Phase 3: 提及识别 v1(US4,P2)

- [X] T011 提及识别器:品牌名 + 别名的精确与归一化匹配(大小写、全半角、常见错写),替代 seed/导入时手工写 mentions per FR-007(收编 001 T087) in `backend/src/geo/domain/mention_parser.py`
  **2026-11-24 完成**:精确/归一化两级匹配(纯拉丁别名加词边界),错写走 `brand_alias` 的 misspell 人工清单;列表行号解析为 position;否定句照算提及且情感判 neg。
- [X] T012 重跑语义:识别结果一键清空,改别名后重算 per FR-007 in `backend/src/geo/services/mention_service.py`, `backend/src/geo/repositories/mention_repository.py`, `backend/src/geo/api/app.py`(POST `/v1/trackers/{id}/mentions/recompute`)
  **2026-11-24 完成**:只重算自品牌(竞品提及不动)、只扫 is_valid=1;删改与审计同事务(`mention.recompute`);HTTP 实测 1302 条答案、444 条提及一比一复现。
- [X] T013 30 条人工标注样本验证:精确率与召回率 ≥ 80%,漏检清单可导出 per FR-008/SC-004 in `backend/tests/labeled_mentions.json`, `backend/scripts/mention_eval.py`, `backend/tests/unit/test_mention_parser.py`
  **2026-11-24 完成**:30 条人工标注样本精确率/召回率 100%(过线口径 80%);漏检与误报清单可导 CSV(`--out`);标注说明与替换指引写在样本文件里。样本为仿真实文本,真实采集上线后可整份替换重跑。
- [X] T014 否定句提及(如「不建议选 A 品牌」)按「提及但负面情感」处理,不算未提及 per spec Edge in `backend/src/geo/domain/mention_parser.py`
  **2026-11-24 完成**:否定线索扫描(命中点同句与前 30 字),命中给 sentiment=neg/0.6;单测断言否定样本不进漏检清单。

## Phase 4: H2.5 参考系统对齐(US5,P2;顺序硬约束 E1a→E1b→E1c→E2→E1d→E1e→E3→E4)

- [ ] T015 E1a 对象头按钮组:导出 CSV / 下载报告 / 暂停监控 / 删除;导出条数 = 当前筛选样本数,暂停删除落库并写审计 per FR-009/roadmap E1a in `frontend/static/index.html`, `backend/src/geo/api/app.py`
- [ ] T016 E1b 引用栏改独立第三栏 +「全部 / 已监控」筛选;栏宽 ≥ 320px 不截断域名,筛选口径复用 T005 的同一份过滤逻辑 per roadmap E1b
- [ ] T017 E1c 行内复制 + 表格/阅读视图切换;剪贴板内容 = 回答全文,两视图行数一致 per roadmap E1c
- [ ] T018 E2 模型 × 端 16 渠道(加 `client` 列):同日同模型不同端分开统计,老数据默认 `web`,13 个空渠道显式「未接入」per spec US5/roadmap E2 + 新增迁移
- [ ] T019 E1d 筛选条提到 tab 之上(品牌 / 问题 / 模型 / 日期),状态进 URL;切筛选后 10 个 tab 数字同步变化,刷新保住筛选 per roadmap E1d
- [ ] T020 E1e 新建监控改右侧抽屉(复用 `/settings` 字段与校验),校验错误就地提示 per roadmap E1e
- [ ] T021 E3 内容工作室六阶段流水线,阶段状态落库,刷新停在原阶段 per roadmap E3(依赖渠道发布真实化任务收口,见下方依赖注记)
- [ ] T022 E4 AI 助手 + 积分账本 = US7(P3,最贵 4.5 人日):余额 = 流水求和;AI 真调 LLM 或显式标失败 per spec US7/roadmap E4 — 建议真实数据验证价值后再做

## Phase 5: 采集调度与失败处理(US6,P3,条件启动)

- [ ] T023 第一个采集 adapter:1 问题 × 1 模型 × 1 次,跑通即停,失败原因可归类 per roadmap H3-2 — **条件:T002 判「走自动采集」才启动**,且必须在 T001 归档之后(FR-001)
- [ ] T024 采集调度器:进程内循环 + 幂等键(问题, 模型, 日期, 样本序),重复调度不产生重复样本 per US6/roadmap H3-3
- [ ] T025 失败重试 + 失败率告警:复用 `sampling_failure_threshold`,失败原因分类统计(超时/风控/验证码/拒答) per FR-010/roadmap H3-4
- [ ] T026 原始响应存档:`raw_uri` 换 `collect://` 等真实可回溯前缀,与演示数据(`s3://geo-raw/demo/`)一眼可分 per FR-011/roadmap H3-6
- [ ] T027 连续 3 天真实采样观察(10 问题 × 3 天 × 3 次 = 90 条),看数据质量与失败分布 per roadmap H3-7 — 条件任务

---

## 依赖与顺序注记

- **US3 必须先于 Phase 4 的新写接口**(E1a 暂停/删除、E3、E4 都在扩大攻击面)——spec US3 明确要求。
- **T005(H2-7)先于 T016(E1b)**:H2-7 是 E1b 的口径基础,先做避免 E1b 返工(US2 why)。
- **T023~T027 依赖 T002 路线结论**:调研判「无资质」时整相下调,T003 升 P1(spec Edge)。
- **T021(E3 六阶段)与渠道发布任务有功能耦合**:六阶段含发布阶段,渠道发布真实化未收口前不动。
- 每阶段收尾硬约束:全量测试数只增不减、黄金窗口手算值不动、结构变更只走新增迁移版本(FR-012)。

## 验证状态(2026-11-24)

- 基线:全量 442 项(单测 182 / 冒烟 86 / 页面契约 77 / 浏览器 75 / 分页 22)全绿,黄金窗口 22.22% / 9 样本。
- 本 spec 尚无已实现任务;渠道发布真实化任务在途,其新增测试会使 442 基线继续上移,收尾时以「不低于收尾时点实际数」为准。
