# -*- coding: utf-8 -*-
"""百家号开放平台接入工具(连通性预检 + 发文接口预留)。

用法:
    python scripts/baijiahao_check.py                  # 读取配置并测试连通性
    python scripts/baijiahao_check.py --init           # 生成本地配置模板(不入库)
    python scripts/baijiahao_check.py --publish <文章json>  # 发布图文(拿到密钥后启用)

配置文件: backend/data/baijiahao.local.json
    {
      "app_id":     "你的 app_id(百家号后台-开发-基本配置)",
      "app_token":  "你的 app_token(同上,泄露须立即重置)"
    }

密钥安全:
    - 配置文件含密钥,已加入 .gitignore(baijiahao.local.json),绝不能提交进 git
    - 日志与错误信息里会自动打码 app_token 前 4 后 4 位

接口说明(官方文档 https://baijiahao.baidu.com/builder/author/openapi):
    图文: POST https://baijiahao.baidu.com/builder/ui/article/api/article
    视频: POST https://baijiahao.baidu.com/builder/ui/article/api/video
    通用字段: app_id, app_token, title(8-40 字), content(富文本 HTML,不支持外链),
              cover_images(JSON 数组,对 / 转义), tag(逗号分隔,每个 <=10 字,最多 10 个)
    已知返回码: errno=0 不代表过审;60001009=当日发文额度用尽
"""
import argparse
import json
import sys
from pathlib import Path

# Windows 控制台可能是 cp1252/cp936,强制 stdout 用 UTF-8,中文提示才不会炸
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import urllib.request
import urllib.error

API_BASE = "https://baijiahao.baidu.com/builder/ui/article/api"
CONFIG_PATH = Path(__file__).resolve().parents[1] / "data" / "baijiahao.local.json"


def mask(token: str) -> str:
    if not token:
        return "(空)"
    if len(token) <= 8:
        return "*" * len(token)
    return f"{token[:4]}...{token[-4:]}"


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def init_config() -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    template = {"app_id": "", "app_token": ""}
    if CONFIG_PATH.exists():
        print(f"配置已存在: {CONFIG_PATH}")
        return
    CONFIG_PATH.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已生成配置模板: {CONFIG_PATH}")
    print("登录百家号后台 → 左侧「开发」→「基本配置」,复制 app_id 与 app_token 填入。")


def check() -> int:
    cfg = load_config()
    app_id = cfg.get("app_id", "")
    app_token = cfg.get("app_token", "")
    if not app_id or not app_token:
        print("[1/2] 密钥缺失: 配置文件里 app_id/app_token 为空。")
        print(f"      先运行 python scripts/baijiahao_check.py --init 生成模板,")
        print("      然后登录百家号后台「开发 → 基本配置」复制密钥填入。")
        print("      (若后台没有「开发」菜单,说明该账号未开通开发者服务,自助开通 2022.7 已关,需走官方商务)")
        return 1

    print(f"[1/2] 密钥已配置: app_id={app_id}, app_token={mask(app_token)}")
    print("[2/2] 测试接口连通性(发文额度查询)...")
    body = {"app_id": app_id, "app_token": app_token}
    req = urllib.request.Request(
        API_BASE + "/quota",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8", "replace")
            print(f"      HTTP {resp.status}")
            print(f"      响应: {raw[:500]}")
            return 0
    except urllib.error.HTTPError as e:
        print(f"      HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:500]}")
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"      连接失败: {e}")
        print("      (本机 git 走 127.0.0.1:7897 代理但当前未运行,若需代理请先开启)")
        return 1


def publish(payload_path: str) -> int:
    cfg = load_config()
    if not cfg.get("app_id") or not cfg.get("app_token"):
        print("密钥未配置,无法发布。先完成 --init 与填钥。")
        return 1
    payload = json.loads(Path(payload_path).read_text(encoding="utf-8"))
    payload.update({"app_id": cfg["app_id"], "app_token": cfg["app_token"]})
    print("发布 payload(已脱敏预览):")
    safe = dict(payload)
    safe["app_token"] = mask(cfg["app_token"])
    print(json.dumps(safe, ensure_ascii=False, indent=2)[:800])
    req = urllib.request.Request(
        API_BASE + "/article",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", "replace")
            print(f"HTTP {resp.status}, 响应: {raw[:500]}")
            print("注意: errno=0 只代表受理,不代表通过审核或已上线。")
            return 0
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:500]}")
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"发布失败: {e}")
        return 1


def main() -> int:
    ap = argparse.ArgumentParser(description="百家号开放平台接入工具")
    ap.add_argument("--init", action="store_true", help="生成密钥配置模板")
    ap.add_argument("--check", action="store_true", help="只做连通性测试")
    ap.add_argument("--publish", metavar="JSON_FILE", help="发布图文,payload 为 JSON 文件")
    args = ap.parse_args()
    if args.init:
        return init_config()
    if args.publish:
        return publish(args.publish)
    return check()


if __name__ == "__main__":
    raise SystemExit(main())
