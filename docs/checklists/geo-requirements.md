# 需求质量 Checklist：GEO Insight 系统整体方案

**Purpose**：在进入 `/speckit-specify` 与 `/speckit-plan` 之前，检查 GEO 系统的需求是否完整、清晰、可度量，重点覆盖指标口径、采集可行性、多租户计费与优化归因四类高风险域。
**Created**：2026-11-11
**Feature**：[GEO-Insight-整体方案.md](../GEO-Insight-整体方案.md)

**Note**：本清单由 `/speckit-checklist` 规范生成。当前项目尚未创建 feature 目录（`.specify/feature.json` 不存在），因此暂存于 `docs/checklists/`；执行 `/speckit-specify` 后应迁入 `specs/<feature>/checklists/`。
**Review Ownership**：本清单是 reviewer 所有的需求质量评审工件。只有当 reviewer 确认"需求质量 criterion 已满足"时才可标记 `[x]`。
**Marker Semantics**：`[x]` 表示需求已被评审且质量达标，不代表实现完成。

---

## A. 指标与口径（最高风险）

- [ ] CHK001 提及率的分母是否明确到"问题 × 模型 × 通道 × 采样次数"，并与"覆盖率"的定义明确区分？[Clarity, Plan §3.8]
- [ ] CHK002 "平均排名"的位次口径是否在三种候选（推荐列表序号 / 段落序号 / 首次出现字符位置）中选定一种并写入需求？[Ambiguity, Plan §3.8]
- [ ] CHK003 同一答案中品牌多次出现时，提及数按 1 次还是 N 次统计，是否有明确规定？[Ambiguity, Plan §3.8]
- [ ] CHK004 多品牌混合答案中，情感倾向如何归属到具体品牌，是否定义到"品牌-片段"粒度？[Clarity, Plan §3.8]
- [ ] CHK005 样本量不足时，UI 与报告是否规定显示"样本不足"而不是 0%，阈值是否量化？[Edge Case, Plan §3.2]
- [ ] CHK006 Top1/Top3/Top10 是否与"平均排名"使用同一位次口径，是否明确分母为有效样本？[Consistency, Plan §3.8]
- [ ] CHK007 引用占比是否规定同一 URL 多次出现是否去重，视频引用占比的判定依据是什么？[Ambiguity, Plan §3.8]
- [ ] CHK008 Share of Voice 的竞品集版本是否固定，竞品集变更后历史指标是重算还是冻结？[Consistency, Plan §3.6]

## B. 采集与采样

- [ ] CHK009 网页端与 API 两条通道的指标是否明确规定不得混算，UI 是否要求标注通道？[Consistency, Plan §2.2]
- [ ] CHK010 每个问题、每个模型、每条通道的最低采样次数是否量化（例如 ≥3 次/日）？[Measurability, Plan §3.2]
- [ ] CHK011 核心问题与长尾问题的分层标准是否量化（按搜索量、业务价值还是人工指定）？[Gap]
- [ ] CHK012 采集失败的 `error_code` 分类（限流/验证码/需登录/模型拒答/超时）是否与 Credits 退款规则一一对应？[Completeness, Plan §3.7]
- [ ] CHK013 失败样本是否明确不计入分母，部分失败时页面如何呈现，是否定义？[Clarity, Plan §3.2]
- [ ] CHK014 网页端采集的登录态来源、授权边界与禁止行为是否写入需求？[Gap, 合规]
- [ ] CHK015 验证码/风控触发后的降级路径与人工接管流程是否定义（暂停、补采、人工上传）？[Coverage, Exception Flow]
- [ ] CHK016 采集并发数、单平台限速、单租户配额是否有量化上限？[Non-Functional, Gap]

## C. 数据模型与接口

- [ ] CHK017 原始响应、截图、引用列表的保留期限与客户删除请求的处理时限是否定义？[Completeness, Gap]
- [ ] CHK018 答案、引用、指标三类数据的口径版本号机制是否定义，是否支持按版本重算？[Consistency, Plan §1.1]
- [ ] CHK019 模型版本静默升级后，趋势图是否允许跨版本连线，是否要求标注版本变更点？[Gap]
- [ ] CHK020 接口的错误返回结构、`request_id`、幂等键适用范围是否统一规定？[Completeness, Plan §3.7]
- [ ] CHK021 异步任务（采样、报告、内容生成）的进度反馈方式（轮询/SSE/Webhook）是否明确？[Clarity, Plan §3.7]

## D. 多租户、权限与计费

- [ ] CHK022 多租户隔离级别（独立库 / schema / 行级 + RLS）是否明确选定？[Clarity, Plan §3.7]
- [ ] CHK023 代理商-客户-品牌三层权限的可见范围与操作权限是否逐项定义？[Coverage, Gap]
- [ ] CHK024 Credits 的预扣、结算、退款状态机是否完整，异常中断时如何回滚？[Completeness, Plan §3.6]
- [ ] CHK025 超额、欠费、并发任务冲突三种场景的产品行为是否定义？[Edge Case, Gap]
- [ ] CHK026 计费单价随模型成本变动的调价规则、老客户保护期是否写明？[Gap]
- [ ] CHK027 客户能否导出原始答案与截图，导出范围与权限是否定义？[Completeness, Gap]

## E. 报告与优化闭环

- [ ] CHK028 报告快照是否要求不可变（30 天后打开仍为当时口径），是否定义重算入口？[Consistency, Plan §3.5]
- [ ] CHK029 周期对比的"可比"条件（同问题集、同模型、同通道、样本量下限）是否量化？[Measurability, Plan §3.5]
- [ ] CHK030 报告是否禁止因果表述，是否要求对照组或免责声明？[Clarity, Plan §0.2 A8]
- [ ] CHK031 优化机会的排序公式（影响面 × 差距 × 可执行性）权重是否定义，是否可解释？[Gap]
- [ ] CHK032 机会转任务是否强制携带证据链（answer_id / citation_id），是否可下钻？[Completeness, Plan §3.4]
- [ ] CHK033 内容生成的事实卡约束与禁说词检查是否可验证，未通过时是否阻断发布？[Measurability, Plan §3.4]
- [ ] CHK034 发布渠道的合规清单、人工确认点与回链复核周期是否定义？[Coverage, Gap]

## F. 非功能、边界与假设

- [ ] CHK035 看板 P95 响应时间、报告生成时长、数据延迟（T+1 小时？）是否有量化目标？[Measurability, Plan §5.1]
- [ ] CHK036 采集成功率的统计口径（按 run_item 还是按 run）是否明确？[Clarity, Plan §5.1]
- [ ] CHK037 行业信源分类标准（新闻/视频/百科/电商…）与"未知"占比过高时的处理策略是否定义？[Clarity, Plan §0.3 L4]
- [ ] CHK038 海外模型与多地区是否在首期范围内，是否明确排除？[Scope, Gap]
- [ ] CHK039 私有化是否为硬需求；若是，离线部署、采集外网出口、数据回传方式是否定义？[Assumption, Gap]
- [ ] CHK040 品牌别名的维护责任人、纠错流程与误判申诉机制是否定义？[Coverage, Gap]
- [ ] CHK041 需求 ID 与验收标准的追溯机制是否建立（FR / SC 编号规则）？[Traceability, Gap]

## Notes

- 仅当 reviewer 确认需求质量达标后才勾选 `[x]`；未解决、待澄清、待补写的条目保持未勾选。
- `/speckit-implement` 会把本清单的勾选状态当作 gate 读取，但不得修改标记。
- `checklists/requirements.md` 是 `/speckit-specify` 与 `/speckit-clarify` 维护的内置清单，与本清单生命周期不同。
- 建议处理顺序：CHK001–CHK008（口径）→ CHK009–CHK016（采集）→ CHK022–CHK027（计费/权限）→ 其余。
- 上述条目中，CHK002、CHK007、CHK011、CHK031、CHK039 会直接改变架构或成本模型，应在 `/speckit-plan` 前闭环。
- 2026-11-11 首轮澄清（见 `docs/decisions/001-clarifications.md`）：CHK002（列表序号）、CHK010（核心 3 次/长尾 1 次）、CHK011（人工分层）已定；CHK009、CHK014 因「当前不实施真实采集」暂缓，不代表关闭。勾选状态仍待 reviewer 确认。
