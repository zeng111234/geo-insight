from enum import StrEnum


class TrackerStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class RunItemStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class ReportStatus(StrEnum):
    QUEUED = "queued"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"


class Tier(StrEnum):
    CORE = "core"
    LONG_TAIL = "long_tail"


class Channel(StrEnum):
    API = "api"
    WEB = "web"


class Sentiment(StrEnum):
    POS = "pos"
    NEU = "neu"
    NEG = "neg"


class SubjectType(StrEnum):
    OWN = "own"
    COMPETITOR = "competitor"


class MetricStatus(StrEnum):
    OK = "ok"
    INSUFFICIENT_SAMPLES = "insufficient_samples"


class ErrorCode(StrEnum):
    TIMEOUT = "TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    REFUSED = "REFUSED"
    EMPTY = "EMPTY"
    OTHER = "OTHER"


class MediaType(StrEnum):
    NEWS = "news"
    VIDEO = "video"
    ENCYCLOPEDIA = "encyclopedia"
    ECOMMERCE = "ecommerce"
    SOCIAL = "social"
    QA = "qa"
    DOC = "doc"
    GOV = "gov"
    FORUM = "forum"
    OFFICIAL = "official"
    OTHER = "other"


TIER_DAILY_SAMPLES = {Tier.CORE: 3, Tier.LONG_TAIL: 1}
