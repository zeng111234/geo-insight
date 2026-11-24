# 系统自更新（「表哥同款」：zip + update.json + 页内一键更新）

**场景**: 一个使用者、无服务器。同事拿到一个文件夹双击就能用；你发版后他在页面上点一下就完成升级。机制与 `gitee.com/zeng11234/teacher-desk-updates` 一致：远端放 `update.json`（版本清单）+ `app-<版本>.zip`（整包代码），客户端校验 sha256 后原地换代码并重启。

**安全边界（两头都守）**: 打包端排除一切运行时数据（`geo.db`、`data/`、登录态、备份），应用端保留这些路径不动——**更新只交换代码，数据永不参与**。

## 角色与动线

| | 你（发布者） | 同事（使用者） |
|---|---|---|
| 机器上要有 | Python + Git（开发机本来就有） | **只要 Python 3.10+**，不需要 git |
| 日常 | 开发、测试、`git push` | 双击 `start-team.bat` 干活 |
| 发版 | `python backend/scripts/release_package.py`（3 分钟） | 「系统更新」页 → 检查更新 → 一键更新 |
| 数据 | 在同事机器上，唯一一份 | 定期双击 `backup-team.bat` |

## 一次性准备

### 1. 建 Gitee 更新仓库（公开）

新建空仓库（如 `geo-updates`），拿到 raw 地址：
```
https://gitee.com/<你的账号>/geo-updates/raw/master/update.json
```

### 2. 首次发布

```bat
cd F:\geo\backend
python scripts\release_package.py --version 0.1.0 --notes "首个发布版"
```
产物在 `F:\geo\dist\`：`app-0.1.0.zip` + `update.json`。**两个文件同一个 commit** 提交推送到更新仓库（分开发会出现清单与包对不上）。

### 3. 交付给同事

把 `app-0.1.0.zip` 发给他（微信/ U 盘），解压到任意文件夹（路径随便，脚本自定位），然后你替他做一次（约 10 分钟）：

1. 装 Python 3.10+（勾 Add to PATH）
2. `pip install -r <文件夹>\backend\requirements.txt`（就 starlette + uvicorn 两个包）
3. 建 `<文件夹>\backend\data\team.local.bat`，写一行 `set GEO_ADMIN_PASSWORD=你们的密码`
4. 双击 `start-team.bat`，防火墙弹窗点「允许」
5. 打开 `http://127.0.0.1:8123/update`，**更新源 URL** 填第 1 步的 raw 地址并保存

## 发版流程（之后每次）

```bat
python scripts\release_package.py --notes "这次改了什么" --push-to F:\geo-updates
```

`--push-to` 指向更新仓库的本地 clone（如 `F:\geo-updates`）时，脚本会自动复制产物、
commit 并 push（**zip 和 update.json 保证同一个 commit**）；第一次推送会弹一次
Gitee 登录窗，之后凭据管理器记住。不带 `--push-to` 则只产出到 `dist\`，手工复制推送。

1. 本仓库的 `VERSION` 已被脚本改好，随源码一起提交
2. 微信喊他一声：系统更新页 → 检查更新 → 一键更新（页面确认后自动换代码重启，刷新即新版）

## 排障

- **更新卡住/失败**: 看 `backend\data\update.log`（worker 全程写日志）。
- **交换中途失败**: 旧代码整体留在 `.update-old\`，把它里面的内容手工挪回根目录即恢复；数据从头到尾没动过。
- **服务没自动起来**: 双击 `start-team.bat`。
- **sha256 校验不过**: 下载损坏，重试；持续失败检查更新仓库里 zip 和 update.json 是不是同一版本。
- **`/update` 页面进不去**: 直接访问 `http://127.0.0.1:8123/update`（侧栏没有入口，参考系统的菜单是对齐锁死的，别加）。

## 注意

- 更新源 URL 是这台机器的信任锚：json 与 zip 同源，sha256 防的是传输损坏不是投毒。更新仓库保持你自己的写权限。
- 共享实例上**永远不要**跑 `run.py --reset`（清真实数据重灌演示数据）。
- `update-team.bat`（git pull 方案）与本方案并存：适用于「同事机器是 git clone」的形态，二选一即可。
