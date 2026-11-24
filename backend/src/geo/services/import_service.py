"""答案导入通道：把外部 CSV/JSONL 变成 answer + mention + citation。

四条设计原则，都是踩过的坑换来的：

1. **不猜业务。** 字段名支持中英文别名，但不做"看起来像时间就当时间"的推断。
   缺什么、哪一行缺、错在哪个字段，都要能指出来。
2. **一行坏不拖累整批。** 默认 skip：坏行进报告，好行照常入库。
   需要严格模式时用 ``on_error="abort"``，整批回滚、一行不留。
3. **幂等靠 row_hash。** 同一份文件导两次，第二次全部 skipped，不会翻倍。
   row_hash 由"观测身份"决定（监控对象+问题+模型+渠道+采集时间+答案原文），
   刻意不含提及和引用——否则修一个错别字就会变成两条重复观测，把指标算歪。
4. **dry-run 走真路径。** 校验与写入是同一条代码，dry-run 只是最后回滚。
   这样"演练通过"才真的等于"能入库"，而不是另写一套空校验糊弄自己。
"""

from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlparse

from geo.repositories import import_repository as repo
from geo.settings import settings

# 无时区的时间按北京时间理解，与 sampling_plan.timezone 的默认值一致
DEFAULT_TZ = timezone(timedelta(hours=8))
MAX_ERRORS_PER_ROW = 5
SUPPORTED_FORMATS = ("csv", "jsonl")

COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "question": ("question", "question_text", "问题", "提问", "问题文本"),
    "question_id": ("question_id", "问题id", "问题ID"),
    "answer": ("answer", "answer_text", "text", "回答", "答案", "回复", "答案正文"),
    "model": ("model", "model_key", "模型", "模型key", "平台"),
    "collected_at": ("collected_at", "time", "collected", "采集时间", "时间", "日期", "回答时间"),
    "channel": ("channel", "渠道", "通道"),
    "tier": ("tier", "层级", "问题层级"),
    "intent": ("intent", "意图", "问题意图"),
    "latency_ms": ("latency_ms", "latency", "耗时", "延迟", "延迟ms"),
    "token_in": ("token_in", "输入token", "prompt_tokens"),
    "token_out": ("token_out", "输出token", "completion_tokens"),
    "cost": ("cost", "成本", "花费"),
    "model_version": ("model_version", "模型版本"),
    "raw_uri": ("raw_uri", "原始地址", "原文地址"),
    "mentions": ("mentions", "提及", "品牌提及"),
    "citations": ("citations", "引用", "来源", "参考链接"),
}
HEADER_MAP = {alias.strip().lower(): canon for canon, aliases in COLUMN_ALIASES.items() for alias in aliases}

TIER_MAP = {"core": "core", "核心": "core", "long_tail": "long_tail", "长尾": "long_tail", "longtail": "long_tail"}
CHANNEL_MAP = {"api": "api", "接口": "api", "web": "web", "网页": "web"}
SENTIMENT_MAP = {
    "pos": "pos",
    "positive": "pos",
    "正面": "pos",
    "neu": "neu",
    "neutral": "neu",
    "中性": "neu",
    "neg": "neg",
    "negative": "neg",
    "负面": "neg",
}
CITE_TYPE_MAP = {"inline": "inline", "reference": "reference", "video": "video", "内联": "inline", "参考": "reference"}
OWNER_TYPE_MAP = {"brand": "brand", "competitor": "competitor", "media": "media"}


class ImportFailure(RuntimeError):
    """文件级问题：读不了、格式不对、找不到监控对象。整批无法开始。"""


class _BadField(ValueError):
    def __init__(self, field_name: str, code: str, message: str):
        super().__init__(message)
        self.field_name = field_name
        self.code = code


@dataclass(frozen=True)
class RowError:
    row_number: int
    field: str
    code: str
    message: str
    raw: str = ""


@dataclass
class RowResult:
    row_number: int
    answer: dict | None = None
    errors: list[RowError] = field(default_factory=list)


@dataclass
class ImportReport:
    source_name: str
    source_format: str
    total: int = 0
    inserted: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[RowError] = field(default_factory=list)
    batch_id: str | None = None
    dry_run: bool = False
    status: str = "completed"
    report_path: str | None = None
    message: str = ""

    def as_dict(self) -> dict:
        return {
            "source_name": self.source_name,
            "source_format": self.source_format,
            "status": self.status,
            "dry_run": self.dry_run,
            "batch_id": self.batch_id,
            "total": self.total,
            "inserted": self.inserted,
            "skipped": self.skipped,
            "failed": self.failed,
            "report_path": self.report_path,
            "message": self.message,
            "errors": [e.__dict__ for e in self.errors],
        }


# ---------------------------------------------------------------- 解析


def sha(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def normalize_question(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ImportFailure(f"无法识别文件编码（试过 UTF-8 与 GBK）：{path.name}")


def detect_format(path: Path, override: str = "auto") -> str:
    if override and override != "auto":
        if override not in SUPPORTED_FORMATS:
            raise ImportFailure(f"不支持的格式：{override}")
        return override
    suffix = path.suffix.lower().lstrip(".")
    if suffix in SUPPORTED_FORMATS:
        return suffix
    raise ImportFailure(f"无法从扩展名判断格式（{path.name}），请用 --format csv/jsonl 指定")


def read_records(path: Path, fmt: str) -> tuple[list[tuple[int, dict]], list[RowError]]:
    """返回 (行号, 原始字典) 列表与读取阶段的错误。"""
    if not path.is_file():
        raise ImportFailure(f"文件不存在：{path}")
    text = _read_text(path)

    if fmt == "csv":
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise ImportFailure("CSV 缺少表头，第一行必须是字段名")
        if not HEADER_MAP.keys() & {str(n).strip().lower() for n in reader.fieldnames if n}:
            raise ImportFailure(
                "CSV 表头里没有任何可识别的字段。至少需要 answer、model、collected_at、question 之一，"
                f"当前表头：{'、'.join(str(n) for n in reader.fieldnames)}"
            )
        records: list[tuple[int, dict]] = []
        problems: list[RowError] = []
        for row_number, row in enumerate(reader, start=2):
            if None in row:
                problems.append(
                    RowError(row_number, "", "BAD_COLUMN_COUNT", "这一行的列数比表头多，多余内容无法归属")
                )
                continue
            if not any(str(v or "").strip() for v in row.values()):
                continue  # 空行直接跳过
            records.append((row_number, dict(row)))
        return records, problems

    records = []
    problems = []
    for row_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError as exc:
            problems.append(RowError(row_number, "", "BAD_JSON_LINE", f"这一行不是合法 JSON：{exc}"))
            continue
        if not isinstance(obj, dict):
            problems.append(RowError(row_number, "", "BAD_JSON_LINE", "每行必须是一个 JSON 对象"))
            continue
        records.append((row_number, obj))
    return records, problems


def _canonical(raw: dict) -> dict:
    out: dict = {}
    for key, value in raw.items():
        if key is None:
            continue
        canon = HEADER_MAP.get(str(key).strip().lower())
        if not canon:
            continue
        if isinstance(value, str):
            value = value.strip()
        out[canon] = value
    return out


def _parse_time(field_name: str, value: str) -> str:
    text = str(value).strip().replace("/", "-")
    candidate = text.replace(" ", "T")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        raise _BadField(
            field_name, "BAD_TIMESTAMP", f"时间无法解析：{text!r}，示例：2026-11-01 09:00:00"
        ) from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=DEFAULT_TZ)
    return parsed.isoformat(timespec="seconds")


def _parse_int(field_name: str, value) -> int | None:
    if value in (None, ""):
        return None
    try:
        number = int(float(str(value).replace(",", "")))
    except ValueError:
        raise _BadField(field_name, "BAD_NUMBER", f"{field_name} 需要数字，收到 {value!r}") from None
    if number < 0:
        raise _BadField(field_name, "BAD_NUMBER", f"{field_name} 不能是负数：{number}")
    return number


def _parse_cost(field_name: str, value) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(str(value).replace(",", ""))
    except ValueError:
        raise _BadField(field_name, "BAD_NUMBER", f"{field_name} 需要数字，收到 {value!r}") from None
    if number < 0:
        raise _BadField(field_name, "BAD_NUMBER", f"{field_name} 不能是负数：{number}")
    return number


def _parse_json_list(field_name: str, value) -> list:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(str(value))
    except ValueError:
        raise _BadField(
            field_name, "BAD_JSON", f'{field_name} 需要 JSON 数组，例如 [{{"brand":"某某"}}]'
        ) from None
    if not isinstance(parsed, list):
        raise _BadField(field_name, "BAD_JSON", f"{field_name} 必须是 JSON 数组")
    return parsed


# ---------------------------------------------------------------- 上下文


@dataclass
class _Context:
    conn: sqlite3.Connection
    tenant_id: str
    tracker: dict
    now: str
    questions: dict[str, str]
    question_ids: set[str]
    subjects: dict[str, tuple[str, str]]
    create_questions: bool = False
    question_set_id: str | None = None
    _brand_ids: dict[tuple[str, str], str] = field(default_factory=dict)

    def ensure_question(self, text: str, tier: str, intent: str | None) -> str:
        normalized = normalize_question(text)
        existing = self.questions.get(normalized)
        if existing:
            return existing
        if self.question_set_id is None:
            self.question_set_id = repo.ensure_question_set(
                self.conn, self.tracker["id"], self.tenant_id, f"{self.tracker['name']} 导入问题", self.now
            )
        new_id = repo.create_question(
            self.conn,
            self.tenant_id,
            self.question_set_id,
            text,
            normalized,
            tier,
            intent,
            self.now,
        )
        self.questions[normalized] = new_id
        self.question_ids.add(new_id)
        return new_id

    def brand_id(self, subject_type: str, display_name: str) -> str:
        key = (subject_type, display_name)
        cached = self._brand_ids.get(key)
        if cached:
            return cached
        value = repo.ensure_brand(self.conn, self.tenant_id, display_name, self.now, kind=subject_type)
        self._brand_ids[key] = value
        return value


def _load_question_ids(conn: sqlite3.Connection, tracker_id: str) -> set[str]:
    rows = conn.execute(
        """SELECT q.id FROM question q JOIN question_set qs ON qs.id = q.question_set_id
           WHERE qs.tracker_id = ?""",
        (tracker_id,),
    ).fetchall()
    return {r[0] for r in rows}


# ---------------------------------------------------------------- 单行校验


def _parse_mentions(ctx: _Context, row_number: int, value) -> tuple[list[dict], list[RowError]]:
    errors: list[RowError] = []
    parsed: list[dict] = []
    try:
        items = _parse_json_list("mentions", value)
    except _BadField as exc:
        return [], [RowError(row_number, exc.field_name, exc.code, str(exc))]

    seen: set[str] = set()
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            errors.append(RowError(row_number, "mentions", "BAD_JSON", f"mentions 第 {index} 项不是对象"))
            continue
        name = str(item.get("brand") or item.get("name") or "").strip()
        if not name:
            errors.append(
                RowError(row_number, "mentions", "MISSING_FIELD", f"mentions 第 {index} 项缺少 brand")
            )
            continue
        hit = ctx.subjects.get(name.lower())
        if not hit:
            errors.append(
                RowError(
                    row_number,
                    "mentions",
                    "UNKNOWN_BRAND",
                    f"提及的主体「{name}」既不是本方品牌/别名，也不在竞品清单里",
                )
            )
            continue
        subject_type, display_name = hit
        if display_name in seen:
            continue  # 同一主体重复出现，只保留第一次（通常排名最靠前）
        seen.add(display_name)

        raw_sentiment = str(item.get("sentiment") or "neu").strip().lower()
        sentiment = SENTIMENT_MAP.get(raw_sentiment)
        if not sentiment:
            errors.append(
                RowError(
                    row_number,
                    "mentions",
                    "BAD_SENTIMENT",
                    f"情绪取值「{item.get('sentiment')}」无法识别，可用 pos/neu/neg 或 正面/中性/负面",
                )
            )
            continue
        position = item.get("position")
        if position not in (None, ""):
            try:
                position = int(position)
            except (TypeError, ValueError):
                errors.append(RowError(row_number, "mentions", "BAD_NUMBER", f"position 需要整数：{position!r}"))
                continue
            if position < 1:
                errors.append(RowError(row_number, "mentions", "BAD_NUMBER", "position 从 1 开始"))
                continue
        else:
            position = None

        confidence = item.get("sentiment_confidence")
        try:
            confidence = float(confidence) if confidence not in (None, "") else None
        except (TypeError, ValueError):
            confidence = None

        parsed.append(
            {
                "subject_type": subject_type,
                "display_name": display_name,
                "matched_alias": name,
                "match_type": "exact",
                "position": position,
                "sentiment": sentiment,
                "sentiment_confidence": confidence,
                "snippet": str(item.get("snippet") or f"{display_name} 出现在答案中")[:300],
            }
        )
    return parsed, errors


def _parse_citations(row_number: int, value) -> tuple[list[dict], list[RowError]]:
    errors: list[RowError] = []
    parsed: list[dict] = []
    try:
        items = _parse_json_list("citations", value)
    except _BadField as exc:
        return [], [RowError(row_number, exc.field_name, exc.code, str(exc))]

    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            errors.append(RowError(row_number, "citations", "BAD_JSON", f"citations 第 {index} 项不是对象"))
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            errors.append(
                RowError(row_number, "citations", "MISSING_FIELD", f"citations 第 {index} 项缺少 url")
            )
            continue
        if not url.lower().startswith(("http://", "https://")):
            errors.append(RowError(row_number, "citations", "BAD_URL", f"url 必须以 http(s):// 开头：{url}"))
            continue
        domain = str(item.get("domain") or "").strip() or (urlparse(url).hostname or "")
        if not domain:
            errors.append(RowError(row_number, "citations", "BAD_URL", f"无法从 url 解析域名：{url}"))
            continue

        cite_type = str(item.get("cite_type") or "inline").strip().lower()
        cite_type = CITE_TYPE_MAP.get(cite_type)
        if not cite_type:
            errors.append(
                RowError(row_number, "citations", "BAD_CITE_TYPE", "cite_type 可用 inline/reference/video")
            )
            continue
        owner_type = str(item.get("owner_type") or "media").strip().lower()
        owner_type = OWNER_TYPE_MAP.get(owner_type, "media")

        published_at = item.get("published_at")
        published_text = None
        if published_at not in (None, ""):
            try:
                published_text = _parse_time("citations.published_at", published_at)
            except _BadField as exc:
                errors.append(RowError(row_number, exc.field_name, exc.code, str(exc)))
                continue

        parsed.append(
            {
                "url": url,
                "url_hash": sha(url),
                "domain": domain,
                "media_name": item.get("media_name"),
                "media_type": str(item.get("media_type") or "other").strip()[:32] or "other",
                "owner_type": owner_type,
                "position": index,
                "cite_type": cite_type,
                "published_at": published_text,
            }
        )
    return parsed, errors


def _parse_row(
    ctx: _Context, row_number: int, raw: dict, seen_hashes: dict[str, int]
) -> RowResult:
    canon = _canonical(raw)
    errors: list[RowError] = []
    raw_text = json.dumps(raw, ensure_ascii=False)[:500]

    def bad(field_name: str, code: str, message: str) -> None:
        if len(errors) < MAX_ERRORS_PER_ROW:
            errors.append(RowError(row_number, field_name, code, message, raw_text))

    text = str(canon.get("answer") or "").strip()
    if not text:
        bad("answer", "MISSING_FIELD", "答案正文为空")
    model_key = str(canon.get("model") or "").strip()
    if not model_key:
        bad("model", "MISSING_FIELD", "缺少模型（model）")

    collected_at = None
    if not canon.get("collected_at"):
        bad("collected_at", "MISSING_FIELD", "缺少采集时间（collected_at）")
    else:
        try:
            collected_at = _parse_time("collected_at", canon["collected_at"])
        except _BadField as exc:
            bad(exc.field_name, exc.code, str(exc))

    channel = "api"
    if canon.get("channel"):
        channel = CHANNEL_MAP.get(str(canon["channel"]).strip().lower(), "")
        if not channel:
            bad("channel", "BAD_CHANNEL", f"渠道「{canon['channel']}」无法识别，可用 api/web 或 接口/网页")

    tier = "long_tail"
    if canon.get("tier"):
        tier = TIER_MAP.get(str(canon["tier"]).strip().lower(), "")
        if not tier:
            bad("tier", "BAD_TIER", f"层级「{canon['tier']}」无法识别，可用 core/long_tail 或 核心/长尾")

    latency_ms = token_in = token_out = cost = None
    try:
        latency_ms = _parse_int("latency_ms", canon.get("latency_ms"))
    except _BadField as exc:
        bad(exc.field_name, exc.code, str(exc))
    try:
        token_in = _parse_int("token_in", canon.get("token_in"))
    except _BadField as exc:
        bad(exc.field_name, exc.code, str(exc))
    try:
        token_out = _parse_int("token_out", canon.get("token_out"))
    except _BadField as exc:
        bad(exc.field_name, exc.code, str(exc))
    try:
        cost = _parse_cost("cost", canon.get("cost"))
    except _BadField as exc:
        bad(exc.field_name, exc.code, str(exc))

    # 问题：优先 question_id，其次问题文本。这里只做查找；
    # 真要建问题也要等这一行其余字段全部通过，免得留下没人用的问题。
    question_id = None
    question_text = ""
    if canon.get("question_id"):
        candidate = str(canon["question_id"]).strip()
        if candidate in ctx.question_ids:
            question_id = candidate
        else:
            bad("question_id", "UNKNOWN_QUESTION", f"question_id {candidate} 不属于当前监控对象")
    elif canon.get("question"):
        question_text = str(canon["question"]).strip()
        question_id = ctx.questions.get(normalize_question(question_text))
        if not question_id and not ctx.create_questions:
            bad(
                "question",
                "UNKNOWN_QUESTION",
                f"问题不在当前监控对象下：「{question_text[:40]}」；加 --create-questions 可自动建",
            )
    else:
        bad("question", "MISSING_FIELD", "缺少 question 或 question_id")

    mentions, mention_errors = _parse_mentions(ctx, row_number, canon.get("mentions"))
    citations, citation_errors = _parse_citations(row_number, canon.get("citations"))
    errors.extend(mention_errors + citation_errors)

    if errors:
        return RowResult(row_number, errors=errors[:MAX_ERRORS_PER_ROW])

    if not question_id:
        question_id = ctx.ensure_question(question_text, tier, canon.get("intent"))

    # 观测身份：刻意不含提及/引用，改了标注不该产生第二条观测
    identity = "|".join(
        [ctx.tracker["id"], question_id, model_key, channel, collected_at, sha(text)]
    )
    row_hash = sha(identity)
    if row_hash in seen_hashes:
        return RowResult(
            row_number,
            errors=[
                RowError(
                    row_number,
                    "",
                    "DUPLICATE_IN_FILE",
                    f"与第 {seen_hashes[row_hash]} 行是同一条观测（问题/模型/渠道/时间/原文都相同）",
                    raw_text,
                )
            ],
        )
    seen_hashes[row_hash] = row_number

    return RowResult(
        row_number,
        answer={
            "tenant_id": ctx.tenant_id,
            "tracker_id": ctx.tracker["id"],
            "question_id": question_id,
            "model_key": model_key,
            "model_version": canon.get("model_version") or None,
            "channel": channel,
            "text": text,
            "text_hash": sha(text),
            "collected_at": collected_at,
            "latency_ms": latency_ms,
            "token_in": token_in,
            "token_out": token_out,
            "cost": cost,
            "raw_uri": canon.get("raw_uri") or "",  # 批次已知后补
            "row_hash": row_hash,
            "mentions": mentions,
            "citations": citations,
        },
    )


def rollback_batch(
    conn: sqlite3.Connection,
    batch_id: str,
    *,
    tenant_id: str | None = None,
    dry_run: bool = False,
    now: str | None = None,
) -> dict:
    """撤销一次导入：删掉这批写入的 answer 及其提及/引用/分块。

    为什么需要它：同一条观测一旦入库，靠重新导入是改不动的（会被 row_hash 判为重复）。
    标错了提及、导错了文件，唯一的出路就是整批撤回再重导。

    刻意不动的东西：brand 行。竞品的 brand 行是"主体注册"，可能被别的批次的提及引用，
    跟着批次删会让别人的提及失去主语。

    已回滚过的批次再回滚：不做任何事，返回 ``already_rolled_back``。
    这是幂等而不是报错——脚本重跑不该因为"已经撤过了"就红。
    """
    tenant = tenant_id or settings.default_tenant_id
    stamp = now or datetime.now().astimezone().isoformat(timespec="seconds")

    batch = repo.batch_by_id(conn, batch_id)
    if not batch:
        raise ImportFailure(f"没有这个导入批次：{batch_id}")
    if batch["tenant_id"] != tenant:
        raise ImportFailure(f"批次 {batch_id} 不属于当前租户")

    counts = repo.batch_data_counts(conn, batch_id)
    if batch["status"] == "rolled_back":
        return {
            "batch_id": batch_id,
            "source_name": batch["source_name"],
            "previous_status": batch["status"],
            "status": "rolled_back",
            "dry_run": dry_run,
            "already_rolled_back": True,
            "answers": 0,
            "mentions": 0,
            "citations": 0,
            "blocks": 0,
            "message": "该批次此前已回滚，本次未做任何改动",
        }

    if dry_run:
        return {
            "batch_id": batch_id,
            "source_name": batch["source_name"],
            "previous_status": batch["status"],
            "status": batch["status"],
            "dry_run": True,
            "already_rolled_back": False,
            **counts,
            "message": f"演练：将删除 {counts['answers']} 条答案及其提及/引用，未写入任何改动",
        }

    deleted = repo.delete_batch_data(conn, batch_id)
    repo.mark_batch_rolled_back(conn, batch_id, stamp)
    conn.commit()
    return {
        "batch_id": batch_id,
        "source_name": batch["source_name"],
        "previous_status": batch["status"],
        "status": "rolled_back",
        "dry_run": False,
        "already_rolled_back": False,
        **deleted,
        "message": f"已回滚 {deleted['answers']} 条答案、{deleted['mentions']} 条提及、{deleted['citations']} 条引用",
    }


# ---------------------------------------------------------------- 报告


def _write_report(report: ImportReport, report_dir: str | Path, stamp: str) -> str | None:
    if not report.errors:
        return None
    base = Path(report_dir)
    base.mkdir(parents=True, exist_ok=True)
    # 去掉原扩展名再接 .csv，否则会得到 xxx.csv.csv
    stem = Path(report.source_name).stem or "import"
    safe = re.sub(r"[^A-Za-z0-9_.\u4e00-\u9fff-]+", "_", stem)[:60]
    path = base / f"{stamp}-{safe}.csv"
    # utf-8-sig 是为了 Excel 双击打开不乱码
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["行号", "字段", "错误码", "说明", "原始行"])
        for error in report.errors:
            writer.writerow([error.row_number, error.field, error.code, error.message, error.raw])
    return str(path)


def _stamp(now: str) -> str:
    return now.replace(":", "").replace("-", "")[:15]


# ---------------------------------------------------------------- 主流程


def import_file(
    conn: sqlite3.Connection,
    *,
    path: str | Path,
    tenant_id: str | None = None,
    tracker_id: str | None = None,
    dry_run: bool = False,
    create_questions: bool = False,
    on_error: str = "skip",
    report_dir: str | Path | None = None,
    now: str | None = None,
) -> ImportReport:
    """把一份 CSV/JSONL 导进库。

    本函数独占 ``conn`` 的事务：成功时 commit，dry-run 或中止时 rollback。
    调用方不要在同一连接上留未提交的改动。
    """
    if on_error not in ("skip", "abort"):
        raise ImportFailure("on_error 只能是 skip 或 abort")

    source = Path(path)
    fmt = detect_format(source)
    stamp = now or datetime.now().astimezone().isoformat(timespec="seconds")
    tenant = tenant_id or settings.default_tenant_id

    if tracker_id:
        tracker = repo.get_tracker(conn, tracker_id, tenant)
        if not tracker:
            raise ImportFailure(f"监控对象不存在或不属于该租户：{tracker_id}")
    else:
        tracker = repo.default_tracker(conn, tenant)
        if not tracker:
            raise ImportFailure("该租户下没有任何监控对象，请先创建一个再导入")

    records, read_errors = read_records(source, fmt)
    if not records and read_errors:
        raise ImportFailure(f"文件里没有一行可用的记录：{read_errors[0].message}")

    ctx = _Context(
        conn=conn,
        tenant_id=tenant,
        tracker=tracker,
        now=stamp,
        questions=repo.load_questions(conn, tracker["id"]),
        question_ids=_load_question_ids(conn, tracker["id"]),
        subjects=repo.load_subjects(conn, tenant),
        create_questions=create_questions,
        question_set_id=repo.question_set_for(conn, tracker["id"]),
    )

    report = ImportReport(
        source_name=source.name, source_format=fmt, total=len(records) + len(read_errors), dry_run=dry_run
    )
    report.errors.extend(read_errors)

    batch_id = repo.create_batch(conn, tenant, tracker["id"], source.name, fmt, stamp)
    try:
        seen_hashes: dict[str, int] = {}
        results: list[RowResult] = []
        for row_number, raw in records:
            result = _parse_row(ctx, row_number, raw, seen_hashes)
            results.append(result)
            report.errors.extend(result.errors)

        failed_rows = {error.row_number for error in report.errors}
        report.failed = len(failed_rows)

        if on_error == "abort" and report.errors:
            conn.rollback()
            report.status = "aborted"
            report.message = "存在错误行，按 --on-error abort 整批未落库"
            report.report_path = _write_report(report, report_dir or settings.import_report_dir, _stamp(stamp))
            return report

        for result in results:
            if result.answer is None:
                continue
            answer = dict(result.answer)
            if repo.row_hash_exists(conn, answer["row_hash"]):
                report.skipped += 1
                continue
            answer["batch_id"] = batch_id
            answer["raw_uri"] = answer["raw_uri"] or f"import://{batch_id}/{result.row_number}"
            answer_id = repo.insert_answer(conn, answer)
            for mention in answer["mentions"]:
                brand_id = ctx.brand_id(mention["subject_type"], mention["display_name"])
                repo.insert_mention(conn, answer_id, brand_id, mention)
            for citation in answer["citations"]:
                repo.insert_citation(conn, answer_id, citation)
            report.inserted += 1

        report.report_path = _write_report(report, report_dir or settings.import_report_dir, _stamp(stamp))
        report.batch_id = None if dry_run else batch_id
        repo.finish_batch(
            conn,
            batch_id,
            inserted=report.inserted,
            skipped=report.skipped,
            failed=report.failed,
            total=report.total,
            status="completed",
            report_uri=report.report_path,
            finished_at=stamp,
        )
        if dry_run:
            conn.rollback()
            report.status = "dry_run"
            report.message = "演练完成，已回滚，未写入任何数据"
        else:
            conn.commit()
            report.status = "completed"
            report.message = f"新增 {report.inserted} 条，跳过重复 {report.skipped} 条"
        return report
    except Exception as exc:
        conn.rollback()
        report.status = "failed"
        report.message = f"导入中断：{exc}"
        raise
