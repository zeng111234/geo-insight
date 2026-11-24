# 011 · 发布通道真实化：CLI 发布器接入系统

- 日期：2026-11-24
- 状态：已采纳（覆盖 009 中「发布通道维持展示态」的部分）
- 影响范围：`api/app.py`、`services/publish_service.py`、`repositories/sqlite_repository.py`、
  `migrations/007`、`seed.py`、`frontend/static/{studio,publish,tasks}.html`、smoke 与集成测试

## 背景

002 首版按 FR-016 排除了真实登录与发帖，渠道面板一直是 seed 的假数据（微信公众号假
「已连接」等），发布接口返回 `integration: "demo"`、202 入队后没有任何消费者。后来
四个渠道的真实发布器已经以 CLI 形态存在（`backend/src/geo/publishers/` +
`scripts/*_publish.py`：搜狐/知乎/公众号走 Playwright + 持久化登录态，百家号走开放
平台 API），只是没接进系统。用户明确要求：渠道面板要能用，不要假数据。

009 当年记录「不做真实多平台登录与自动发帖」的动机是不为演示造假页面；现在做的是
反过来——**把已经真了的 CLI 接进来，把假状态删掉**。009 其余部分（侧栏对齐等）不受影响。

## 决策

1. **渠道状态即真实登录态**。`channel_account.status` 扩为
   connected / disconnected / expired / connecting / needs_config / not_implemented
   （迁移 007 重建表改 CHECK，新增 `last_check_note` 附注列）。seed 不再灌假「已连接」。
2. **检测走真实子进程**（用户手动点「检测」才触发）：`POST /v1/channels/{id}/check`
   在线程池里跑 CLI 的 `check`（无头浏览器，10-15 秒），结果落库；页面加载只读库，不开浏览器。
3. **连接是交互式的**：`POST /v1/channels/{id}/connect` 派生可见登录子进程（扫码），
   状态置 connecting，监控线程按退出码落库。HTTP 只回 202，不等扫码。
4. **发布是异步真实的**：`POST /v1/articles/{id}/publish` 校验后复用幂等入队
   （failed/blocked/unknown 记录重置回 queued 支持重试，running/queued 不重复派发），
   每条记录派 worker 线程跑 CLI `publish`（分钟级浏览器自动化），结果信封映射为
   publication 状态 published / draft_saved / blocked / failed / unknown。**宁可不报成功，不伪造成功**。
5. **同渠道互斥**：浏览器 profile 每渠道只有一份，check/login/publish 进程内互斥，
   busy 时 409。单进程部署，进程内锁够用；多进程是 H4 的事。
6. **启动清扫**：create_app 把遗留的 running publication 置 failed、connecting 渠道置
   disconnected——worker 线程随进程死掉，页面绝不能显示「进行中」而实际没人管。
7. **测试缝**：子进程出口只有一个（`publish_service._run_cli`），测试换假实现不打真
   浏览器；`GEO_PUBLISH_WORKER=0` 走纯入队（冒烟用）。

## 边界（红线，写死不动）

- 不绕过任何验证码/滑块/短信验证；平台要人验证时就转人工（发布器早就这么做）。
- 不碰原创声明开关（滥用会被永久关权限）。
- 账号凭据只存在本地文件（`data/channels.local.json`，已 gitignore），不进库不进 git。
- 公众号默认只存草稿箱；群发需要管理员手机验证，这是平台设计，必须人来。

## 代价与已知风险

- app 第一次引入子进程 + 后台线程（此前全是同步请求处理）。worker 用守护线程 +
  各自开 SQLite 连接，重启即清扫，复杂度可控但确实引入了新的失败面。
- 写接口仍无 CSRF（决策 010 的已知洞），发布现在是真实对外动作，这条的紧迫性上升了——
  依然排在 003 spec 的 P1，不与本决策捆绑。
- 搜狐/知乎/公众号的编辑器选择器部分未实测校准（登录墙后），发布失败会如实落
  failed/blocked 而不是假装成功。

## 第二批（同日）：抖音 / 今日头条 / 小红书

七渠道齐备。抖音走图文通道（draft 需 `images`，无图在派发前拒绝）；头条按文章形态；
小红书按笔记形态（同样需图）。**实测发现本机对 toutiao.com / xiaohongshu.com 全域
TLS 被重置**（抖音正常，换 IP 无效）——两渠道模块就位并注册，登录与选择器校准
挂起在网络修复之后；check 如实把连接错误落附注。worker=0 时 connect 也不派
登录子进程（冒烟曾真弹过一次浏览器，随后堵上）。

## 验证

- 单测 13 条：markdown 转换、结果信封解析与状态映射、标题预检；
- HTTP 集成 14 条（假 runner）：诚实初始态、connect/check/publish 全语义、
  busy 409、worker 关闭纯入队、重试不重复建行、启动清扫；
- 冒烟 92 条（worker=0）：诚实渠道状态、not_implemented/needs_config 400、
  发布 202 + integration=real、幂等；黄金窗口 22.22%/40%/17.8pp 断言原样保留；
- 端到端手验：点「检测」看搜狐落库 connected（附实名 note）、发布实测走 blocked 语义。
