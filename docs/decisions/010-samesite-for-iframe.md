# 010 · 线上会话 cookie 用 SameSite=None，本地保持 lax

- 日期：2026-11-24
- 状态：已采纳
- 影响范围：`api/app.py` 的 `login_submit`、`tests/integration/test_auth_http.py`

## 背景

应用发布到线上后，从应用卡打开是一页空白。服务本身是好的——直接用分享链接打开、登录、调接口全部正常。

排查过程：

1. 分享链接带 `?sharecode=` 能打开登录页，截图确认渲染正常；
2. 不带 sharecode 返回网关的「链接不完整」页，说明链接本身没坏；
3. 登录响应头里 `geo_session` 是 `SameSite=lax`。

**应用卡是把站点放在 iframe 里打开的。** 而 `SameSite=lax` 的 cookie 在第三方上下文
（也就是 iframe 里）浏览器不会发送，于是登录成功后每一次后续请求都不带会话，
中间件把页面弹回 `/login`，用户看到的就是一页空白。

本地双击 `start.bat` 没这个问题，因为那是顶层窗口，不是 iframe。

## 决策

按请求协议分流，同一个 cookie 在两种环境下用不同属性：

| 环境 | SameSite | Secure |
|---|---|---|
| 线上（HTTPS，反向代理后面） | `None` | 是 |
| 本地（http://127.0.0.1） | `lax` | 否 |

判断 HTTPS 先看 `X-Forwarded-Proto` 再看 `request.url.scheme`——线上浏览器地址栏是 https，
但 uvicorn 收到的 scheme 可能仍是 http，只看 scheme 会判断错。

本地必须是 `lax`：浏览器不接受给 http 站点设 `SameSite=None`，而带 `Secure` 的 cookie
在 http 下会被直接丢掉，本地就登不进去了。

## 代价

`SameSite=None` 放弃了它提供的 CSRF 防护：跨站页面发起的 POST 也会带上会话 cookie。

这个项目目前可以承受：所有写操作都需要人主动点击，数据是演示数据，而且分享链接本身
带 sharecode 作为第一道门槛。但**真要对外用之前必须补 CSRF token**（每个写接口校验
一次性 token 或双提交 cookie）。这一条写进 README 的「尚未实现」，不靠记忆。

## 验证

- `test_https_login_sets_cross_site_cookie`：带 `X-Forwarded-Proto: https` 登录，
  断言 `SameSite=none` + `Secure`；
- `test_http_login_keeps_lax`：普通 http 登录，断言 `SameSite=lax` 且不带 `Secure`；
- 变异自检：把实现改回固定 `lax`，第一条红 1 条；还原后全绿；
- 线上实测：登录响应头为 `geo_session=...; HttpOnly; Max-Age=43200; Path=/; SameSite=none; Secure`，
  带该 cookie 访问 `/`、`/monitor`、`/studio`、`/v1/trackers` 均为 200。
