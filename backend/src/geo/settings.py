import os
from dataclasses import dataclass
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
# 项目根（backend 的上一级）。路径必须从这里推，不能靠 cwd。
PROJECT_ROOT = BACKEND_DIR.parent

# 演示默认密码。只在 `start.bat` 的本机演示场景用；对外服务必须设 GEO_ADMIN_PASSWORD。
# 单独提出来是因为 doctor --server 要拿它做守门——文档里写"部署前要改"没人拦，
# 得有一条命令真能查出"还在用演示密码"。决策 008。
DEMO_ADMIN_PASSWORD = "geo-demo-2026"


@dataclass(frozen=True)
class Settings:
    """Runtime configuration.

    Local development and demos run on SQLite in a single Python process —
    Docker is intentionally not used (see docs/decisions/002-local-run-no-docker.md).
    PostgreSQL + ClickHouse remain the target shape for large deployments; only
    the repository implementation changes when that happens.
    """

    # 数据库固定在项目根，**不跟着 cwd 走**。
    # 曾经是相对路径 "geo.db"：双击 start.bat（脚本先 cd backend）写到 backend/geo.db，
    # 而命令行在项目根启动写到 geo/geo.db，于是同一台机器上长出两个库，
    # 数据看起来"丢了"，其实只是看错了文件（2026-11-25 实际踩到）。
    # 换机器、换启动方式都必须指向同一个文件，这条比"方便"重要。
    database_path: str = os.getenv("GEO_DB_PATH", str(PROJECT_ROOT / "geo.db"))
    default_tenant_id: str = os.getenv("GEO_TENANT_ID", "00000000-0000-0000-0000-000000000001")
    api_prefix: str = "/v1"
    metric_version: str = "v1"
    min_samples: int = int(os.getenv("GEO_MIN_SAMPLES", "3"))
    # FR-010：失败率超过该阈值时在看板提示。默认 10%，可用环境变量覆盖。
    sampling_failure_threshold: float = float(os.getenv("GEO_FAILURE_THRESHOLD", "0.1"))
    # 首版报告同步生成；超过该时长说明数据量已不适合 SQLite 直算。
    report_sync_budget_ms: int = int(os.getenv("GEO_REPORT_BUDGET_MS", "60000"))
    # 无列表样本占比告警阈值（spec Edge Cases 要求展示占比，plan 要求超阈值提示）
    no_list_alert_threshold: float = float(os.getenv("GEO_NO_LIST_THRESHOLD", "0.5"))
    # 迁移链目录；测试会让它指向临时目录
    migrations_dir: str = os.getenv("GEO_MIGRATIONS_DIR", str(BACKEND_DIR / "migrations"))
    # 导入失败报告的存放目录，相对路径按 backend/ 解析
    import_report_dir: str = os.getenv("GEO_IMPORT_REPORT_DIR", str(BACKEND_DIR / "import_reports"))

    # ---- 鉴权 ----
    # 默认开启。只在自动化检查（页面契约 / 浏览器 DOM / 分页行为）时显式关掉，
    # 见 README「验证」一节；业务代码不读这个开关去做别的事。
    auth_required: bool = os.getenv("GEO_AUTH_REQUIRED", "1") != "0"
    admin_email: str = os.getenv("GEO_ADMIN_EMAIL", "demo@example.com")
    # 演示默认密码。真实使用必须用环境变量覆盖，README 里写明了。
    admin_password: str = os.getenv("GEO_ADMIN_PASSWORD", DEMO_ADMIN_PASSWORD)
    session_hours: int = int(os.getenv("GEO_SESSION_HOURS", "12"))
    cookie_name: str = "geo_session"
    # CSRF 双提交 cookie 名(登录后非 HttpOnly 下发,页面 JS 读它放进 X-CSRF-Token)
    csrf_cookie_name: str = "geo_csrf"
    # 登录限流(FR-006):连续失败 N 次锁定 M 秒,锁定期内正确密码也拒绝
    login_max_failures: int = int(os.getenv("GEO_LOGIN_MAX_FAILURES", "5"))
    login_lockout_s: int = int(os.getenv("GEO_LOGIN_LOCKOUT_S", "300"))
    # ---- 安全加固 ----
    # 允许的跨源来源白名单(逗号分隔)。同源部署不需要跨源,留空即只允许同源;
    # 本地调试从别的端口打开页面时才需要配。绝不默认 "*"——那是把接口对任意站点敞开。
    # 生产若要跨源(如独立前端域名),显式列出,例如 https://geo.example.com。
    cors_origins: tuple[str, ...] = tuple(
        o.strip() for o in os.getenv("GEO_CORS_ORIGINS", "").split(",") if o.strip()
    )

    # ---- 渠道发布（决策 011：发布通道真实化）----
    # 渠道自动化开关(登录派发 + 发布 worker)。默认开；冒烟/自动化测试设 0:
    # 发布走「纯入队」语义、connect 不派登录子进程,不在测试里真的起浏览器。
    publish_worker_enabled: bool = os.getenv("GEO_PUBLISH_WORKER", "1") != "0"
    # 单条发布的子进程超时（秒）：浏览器自动化含页面等待，给足余量。
    publish_timeout_s: int = int(os.getenv("GEO_PUBLISH_TIMEOUT", "600"))
    # 全局发布并发上限：同时最多几个浏览器发布在跑。
    # 一个发布 worker ≈ 一个 Python 子进程 + 一个 Chrome(400~500MB)。
    # 默认 3：2 核 4G 下约 1.8G，给系统和 uvicorn 留余量；4 核 8G 可调到 4。
    # 进程内信号量，和 session/渠道锁一样只对单进程部署有效（决策 012）。
    # max(1, ...) 防止误配 0/负数把队列永久卡死。
    publish_concurrency: int = max(1, int(os.getenv("GEO_PUBLISH_CONCURRENCY", "3")))
    # 服务器模式:worker 调 CLI 时要求无头浏览器。默认 0 保持本机开发行为
    # (搜狐本来无头,其余渠道有头可盯)。服务器没显示器必须设 1,否则浏览器
    # 启动即崩——这不是"更省资源",是"能不能跑"的区别。
    publish_headless: bool = os.getenv("GEO_PUBLISH_HEADLESS", "0") == "1"


settings = Settings()
