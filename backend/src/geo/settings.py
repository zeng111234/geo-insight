import os
from dataclasses import dataclass
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    """Runtime configuration.

    Local development and demos run on SQLite in a single Python process —
    Docker is intentionally not used (see docs/decisions/002-local-run-no-docker.md).
    PostgreSQL + ClickHouse remain the target shape for large deployments; only
    the repository implementation changes when that happens.
    """

    database_path: str = os.getenv("GEO_DB_PATH", "geo.db")
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
    admin_password: str = os.getenv("GEO_ADMIN_PASSWORD", "geo-demo-2026")
    session_hours: int = int(os.getenv("GEO_SESSION_HOURS", "12"))
    cookie_name: str = "geo_session"

    # ---- 渠道发布（决策 011：发布通道真实化）----
    # 渠道自动化开关(登录派发 + 发布 worker)。默认开；冒烟/自动化测试设 0:
    # 发布走「纯入队」语义、connect 不派登录子进程,不在测试里真的起浏览器。
    publish_worker_enabled: bool = os.getenv("GEO_PUBLISH_WORKER", "1") != "0"
    # 单条发布的子进程超时（秒）：浏览器自动化含页面等待，给足余量。
    publish_timeout_s: int = int(os.getenv("GEO_PUBLISH_TIMEOUT", "600"))


settings = Settings()
