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
    """采集失败码。**唯一权威定义**——采集器、CLI、种子数据都用这里的词。

    曾经这里是 TIMEOUT / RATE_LIMITED / REFUSED / EMPTY / OTHER 五个没人读的值，
    而 seed.py 自己用 AUTH_EXPIRED / UPSTREAM_ERROR，采集器又用 AUTH_FAILED。
    三套词汇并存的结果是：同一种失败在界面上显示成不同名字，
    按字符串分组的统计会把同一种失败算成三类。

    按"怎么处理"分组，比按字母序有用：

    - **配置问题**（改配置才能好）：NOT_CONFIGURED / NO_COLLECTOR / UNSUPPORTED_MODEL
    - **请求问题**（改问法或参数）：BAD_REQUEST / UNKNOWN_QUESTION / UNKNOWN_TRACKER
    - **平台问题**（等一会再试）：AUTH_FAILED / RATE_LIMITED / TIMEOUT / CONNECTION / UPSTREAM_ERROR
    - **内容问题**（拿到了但不能用）：EMPTY_TEXT / BAD_RESPONSE / BAD_ENCODING / NOT_FOUND
    - **额度问题**（今天计划采满了）：NO_SLOT
    """

    # 配置
    NOT_CONFIGURED = "NOT_CONFIGURED"
    NO_COLLECTOR = "NO_COLLECTOR"
    UNKNOWN_COLLECTOR = "UNKNOWN_COLLECTOR"
    NOT_AVAILABLE = "NOT_AVAILABLE"
    UNSUPPORTED_MODEL = "UNSUPPORTED_MODEL"
    # 请求
    BAD_REQUEST = "BAD_REQUEST"
    UNKNOWN_QUESTION = "UNKNOWN_QUESTION"
    UNKNOWN_TRACKER = "UNKNOWN_TRACKER"
    # 平台
    AUTH_FAILED = "AUTH_FAILED"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    CONNECTION = "CONNECTION"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"
    # 内容
    EMPTY_TEXT = "EMPTY_TEXT"
    BAD_RESPONSE = "BAD_RESPONSE"
    BAD_ENCODING = "BAD_ENCODING"
    NOT_FOUND = "NOT_FOUND"
    # 额度
    NO_SLOT = "NO_SLOT"


class MediaType(StrEnum):
    """引用来源的媒体类型，对应 ``citation.media_type``。

    取值与前端 ``monitor.html`` 的 ``TYPE_TEXT`` 一一对应（那里是中文标签）。
    曾经这里有 encyclopedia / ecommerce / social / qa / doc / gov / official
    八个值，但库里一个都没出现过——实际用的是 wiki / wechat / database，
    前端也只认那七个。枚举与现场不符时，"照枚举写"的人会写进前端不认识的值。

    新增类型必须同时改前端 TYPE_TEXT，否则页面上会显示成英文原词。
    """

    NEWS = "news"
    VIDEO = "video"
    FORUM = "forum"
    BLOG = "blog"
    WECHAT = "wechat"
    WIKI = "wiki"
    DATABASE = "database"
    OTHER = "other"


class CiteType(StrEnum):
    """引用形态（``citation.cite_type``）。

    视频与资料库是独立形态，其余跟平台走：
    DeepSeek 的引用挂在正文块上（inline），豆包是答案级（reference）。
    见 ``collectors/citations.py`` 的 ``infer_cite_type``。
    """

    INLINE = "inline"
    REFERENCE = "reference"
    VIDEO = "video"


TIER_DAILY_SAMPLES = {Tier.CORE: 3, Tier.LONG_TAIL: 1}
