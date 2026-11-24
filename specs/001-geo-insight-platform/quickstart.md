# Quickstart：首版验收路径

**Feature**: `001-geo-insight-platform`
**目的**：用真实可执行的命令验证 P1 的 4 条验收场景、配置写路径、下钻、报告快照与 SC-002/003/005/006/007。本文只写「怎么跑、看什么」。

> 本文所有命令都对应当前实现（Starlette + SQLite + 零依赖静态页，决策 002/004/005）。契约字段以 `contracts/openapi.yaml` v0.2.0 为准。

---

## 0. 前置条件

- Python 3.10+；**不依赖 Docker、PG、ClickHouse、Redis，也不需要 pip install**
- 端口 8123 空闲（被占用时 `start.bat` 会直接打开已有服务，不报错）

```bash
python backend/run.py           # Windows 直接双击 start.bat
# 浏览器打开 http://127.0.0.1:8123
BASE=http://127.0.0.1:8123/v1
T=11111111-1111-1111-1111-111111111111
```

预期：控制台打印「看板与接口已就绪」，页面 8 张指标卡有值，无 `Failed to fetch`。
本地版没有鉴权，`Authorization` 头不需要；租户由默认配置派生，不能给外部用户使用。

---

## 1. 指标口径：P1 的 4 条验收场景

```bash
# 场景 2：9 个有效样本、2 次提及
curl -s "$BASE/trackers/$T/metrics/overview?from=2026-11-01&to=2026-11-03"

# 场景 3：多出 1 条「提及但答案无列表」
curl -s "$BASE/trackers/$T/metrics/overview?from=2026-11-01&to=2026-11-04"

# 场景 4：只有 2 个样本
curl -s "$BASE/trackers/$T/metrics/overview?from=2026-11-05&to=2026-11-05"
```

**预期**

| 周期 | `valid_total` | `mention_rate` | `avg_rank` | `no_list_samples` |
|---|---|---|---|---|
| 11-01~11-03 | 9 | `0.2222` / `ok` | `insufficient_samples`（仅 2 个位次样本） | 0 |
| 11-01~11-04 | 12 | `0.25` / `ok` | `insufficient_samples`（分母仍为 2） | 1 |
| 11-05 | 2 | `insufficient_samples` | `insufficient_samples` | 0 |

- 每个指标带 `{value, status, sample_base}`，`sample_base < 3` 返回状态而非数字；`null` 不被写成 0。
- 情感分布的分母是**提及样本数**，不是有效样本数；0 提及时不显示「中性 100%」。
- 页面顶栏「口径说明」读 `GET /v1/meta/methodology`，其中含 `no_list_alert_threshold` 与 `sampling_failure_threshold`，阈值不是前端写死的。

---

## 2. 配置写路径（US2 / SC-001）

```bash
# 新建监控对象（返回扁平字段；完整配置用 GET 查看）
curl -s -X POST "$BASE/trackers" -H "Content-Type: application/json" \
  -d '{"name":"面试演示 · 医美","brand_name":"演示医美","industry":"医美","region":"大连","brand_aliases":["演示医美门诊"],"competitor_names":["竞品A"]}'

# 批量导入问题：每行一题，行首 * 表示核心；重复文本自动去重
curl -s -X POST "$BASE/trackers/$T/questions" -H "Content-Type: application/json" \
  -d '{"text":"*沈阳哪家律所最专业\n沈阳交通事故律师怎么收费\n沈阳刑事辩护律师大概需要多少费用"}'

# 查看问题、切换层级（次日生效）、采样计划与失败率
curl -s "$BASE/trackers/$T/questions"
curl -s -X PATCH "$BASE/questions/<question_id>" -H "Content-Type: application/json" -d '{"tier":"core"}'
curl -s "$BASE/trackers/$T/sampling-plan"
```

**预期**

- 导入返回 `created_count=2`、`duplicate_count=1`、`duplicates` 列出重复文本；再导一次 `created_count=0`（FR-005）。
- `PATCH /questions/{id}` 返回 `effective_from` 为次日，当日采样计划不变（FR-012）。
- `sampling-plan.daily_planned = (核心数×3 + 长尾数×1) × 模型数`；种子数据 4 核心 + 8 长尾 × 3 模型 = 60。
- `failure.rate = 失败 run_item / 全部 run_item`，超过 10% 时 `status = over_threshold`，看板与配置页同时提示（种子数据 214/1516 ≈ 14.1%：含 11-06、11-22 两次采集中断，失败样本不进任何指标分母）。
- 竞品增删后 `GET /trackers/{id}` 的 `competitors.version` 递增，历史指标不重算。

---

## 3. 下钻、答案详情、信源（SC-004 / FR-020/023）

```bash
Q="from=2026-11-01&to=2026-11-04"
curl -s "$BASE/trackers/$T/metrics/mention_rate/samples?$Q"     # sample_base=12
curl -s "$BASE/trackers/$T/metrics/avg_rank/samples?$Q"         # sample_base=2，无列表提及 in_denominator=false
curl -s "$BASE/trackers/$T/answers?$Q&limit=50"
curl -s "$BASE/answers/<answer_id>"                            # 原文、提及、引用、推荐清单
curl -s "$BASE/trackers/$T/citations?$Q"                       # 按 URL 去重，times 累计
curl -s "$BASE/trackers/$T/answers/export?$Q&format=csv" -o answers.csv
```

**预期**

- 下钻返回 `rule`（该指标的分母口径）与 `items[].in_denominator`；不计入分母的样本仍显示，便于解释。
- `citations` 中每个 URL 只出现一次，`total_occurrences ≥ total_urls`（FR-020）。
- CSV 带 UTF-8 BOM，Excel 可直接打开；`format=jsonl` 每行一条；导出不修改原始记录。

---

## 4. 报告快照与打印导出（US3 / SC-005/006）

```bash
Q="from=2026-11-01&to=2026-11-03"
curl -s -X POST "$BASE/trackers/$T/reports" -H "Content-Type: application/json" -d '{"from":"2026-11-01","to":"2026-11-03"}'
curl -s "$BASE/reports/<report_id>"
curl -s "$BASE/trackers/$T/reports"
# 浏览器打开 /report/<report_id> → 点「打印 / 导出 PDF」
```

**预期**

- 同步生成，返回 201 / `status=ready`；`payload_hash` 是 payload 规范化 JSON 的 sha256（64 位）。
- 首周期无上一周期可比：`comparable=false`，`incomparable_reasons` 非空，`delta_pp=null`，**不输出涨跌幅**。
- 生成 11-04~11-05 的报告时，模型与口径一致 → `comparable=true`，给出 `delta_pp`（演示数据 +23.3pp）。
- 改动后续答案数据后再 `GET /reports/{id}`，`payload_hash` 与所有指标不变（FR-028）。
- **同一 `(tracker, 起止日期, 口径版本)` 重复提交返回首次快照并带 `reused=true`**，不产生第二份报告。
- `period_start` 必须严格小于 `period_end`，单日周期返回 400；周期最长 92 天。
- PDF 由浏览器打印生成（决策 005），无服务端导出端点；SC-005 逐字段比对，不比对字体与布局。

---

## 5. 自动化验证

```bash
cd backend
python -m unittest discover -s tests -t . -p "test_*.py" -v   # 36 条
python scripts/smoke.py                                        # 80 条真实 HTTP 断言（自动选空闲端口）
```

`smoke.py` 自建临时库、在自动挑选的空闲端口启动 uvicorn、逐个请求断言后关闭进程，不动 `backend/geo.db`，也不会误连正在跑的开发服务。
另有 `node scripts/pagecheck.mjs`（70 条：28 条按页面请求顺序逐字段校验 + 13 页统一侧栏/单高亮/无死链 + 1 条 `data_range` 类型 + 26 条样式契约；需服务在跑；换端口用 `GEO_BASE=...`）。
`node scripts/browsercheck.mjs`（32 条）用系统 Chrome/Edge 的 `--dump-dom` 验证真实浏览器渲染结果：13 页无报错、无「加载中」残留，关键表格/图表与明细翻页条确实出现；不装 Playwright，找不到浏览器会打印 SKIP 而不是假装通过。`node scripts/pagercheck.mjs`（20 条）在桩 DOM 里加载真实 `app.js` 模拟点击翻页与页码跳转，验证明细表 10 行/页、边界禁用、换查询回第 1 页、跳转框越界钳制。另对每个 `<script>` 块跑 `node --check`。

**尚未执行的验收**（不要当作已通过）：真人试用 10 人（SC-001）、200 条黄金样本一致率（SC-002/003）、Schemathesis 契约 fuzz、**真人的浏览器交互点击**（browsercheck 只验渲染后的 DOM，不模拟点击/表单提交）、PDF 真人逐字段比对。原因见 README「尚未实现」。

---

## 6. 清理与重建

```bash
python backend/run.py --reset        # 清表重建，不删 geo.db（Windows 文件被占用时删文件会 PermissionError）
```

`seed` 是确定性的：重建后上表三组数字必须完全一致。

---

## 失败时先看哪里

| 现象 | 先查 |
|---|---|
| `Failed to fetch` | 页面是否用 `file://` 打开；是否统一从 `http://127.0.0.1:8123` 访问 |
| 提及率不是 22.2% | `valid_total` 是否把失败样本算进分母；`mention` 是否有重复行 |
| 平均排名分母不对 | `avg_rank.sample_base` 是否只含位次非空样本；无列表提及是否被误算 |
| 情感出现「中性 100%」且 0 提及 | 情感分母是否错用 `valid_total` |
| 报告数字变了 | 是否读的快照而不是实时查库 |
| 重复生成出现两份报告 | `save_report` 的同周期复用逻辑是否被绕过 |
| 页面缺字段 | 对照 `contracts/openapi.yaml`；页面按契约取字段，不反向定义接口 |
