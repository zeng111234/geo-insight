import os
from dataclasses import dataclass


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


settings = Settings()
