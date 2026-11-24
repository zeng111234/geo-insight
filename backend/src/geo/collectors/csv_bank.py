"""离线兜底采集器：从本地答案库文件里按 (问题, 模型) 取答案。

它的定位说清楚，免得被当成"能联网采集"：

- **用途**：没有平台 API 权限时，把「采集 → 落库 → 出指标」这条链路先跑通，
  也用来做回归演练（给定问题与模型，结果稳定可复现）。
- **不是**：真实数据来源。真实数据要么走导入通道（人工导出），
  要么等平台 API 权限下来后新增一个 ``kind="api"`` 的实现。

文件格式与导入通道一致（CSV 或 JSONL），由 ``GEO_COLLECTOR_CSV`` 指定。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from geo.collectors.base import CollectRequest, CollectResult, CollectorError
from geo.services.import_service import normalize_question

DEFAULT_TZ = timezone(timedelta(hours=8))


class CsvBankCollector:
    key = "csv_bank"
    display_name = "本地答案库（离线兜底）"
    kind = "offline"

    def __init__(self, path: str | Path | None = None):
        self._path = Path(path) if path else None
        self._cache: dict[tuple[str, str], dict] = {}
        self._stamp: tuple[float, int] | None = None

    # --- 协议 ---
    def path(self) -> Path | None:
        if self._path is not None:
            return self._path
        raw = os.getenv("GEO_COLLECTOR_CSV")
        return Path(raw) if raw else None

    def available(self) -> bool:
        target = self.path()
        return bool(target and target.is_file())

    def supports(self, model_key: str) -> bool:
        if not self.available():
            return False
        return any(key[1] == model_key for key in self._load())

    def collect(self, request: CollectRequest) -> CollectResult:
        target = self.path()
        if not target or not target.is_file():
            raise CollectorError("NOT_CONFIGURED", "没有设置 GEO_COLLECTOR_CSV，或文件不存在")
        index = self._load()
        row = index.get((normalize_question(request.question_text), request.model_key))
        if row is None:
            raise CollectorError(
                "NOT_FOUND",
                f"答案库里没有「{request.question_text[:30]}」× {request.model_key} 的记录",
            )
        text = str(row.get("answer") or row.get("answer_text") or row.get("text") or "").strip()
        if not text:
            raise CollectorError("EMPTY_TEXT", "答案库里的这一行正文为空")

        return CollectResult(
            text=text,
            model_key=request.model_key,
            channel=request.channel,
            # 采集时间取"现在"：这是这次取样的时间，不是源文件的日期
            collected_at=datetime.now(DEFAULT_TZ).isoformat(timespec="seconds"),
            model_version=row.get("model_version") or None,
            latency_ms=_int_or_none(row.get("latency_ms")),
            token_in=_int_or_none(row.get("token_in")),
            token_out=_int_or_none(row.get("token_out")),
            cost=_float_or_none(row.get("cost")),
            raw_uri=f"collect://csv_bank/{target.name}",
            raw=dict(row),
        )

    # --- 载入 ---
    def _load(self) -> dict[tuple[str, str], dict]:
        target = self.path()
        if target is None or not target.is_file():
            return {}
        stat = target.stat()
        stamp = (stat.st_mtime, stat.st_size)
        if self._stamp == stamp:
            return self._cache

        rows = _read_rows(target)
        index: dict[tuple[str, str], dict] = {}
        for row in rows:
            question = str(row.get("question") or row.get("question_text") or "").strip()
            model = str(row.get("model") or row.get("model_key") or "").strip()
            if not question or not model:
                continue
            index.setdefault((normalize_question(question), model), row)
        self._cache = index
        self._stamp = stamp
        return index


def _read_rows(path: Path) -> list[dict]:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "gbk"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise CollectorError("BAD_ENCODING", f"无法识别文件编码：{path.name}")

    if path.suffix.lower() in (".jsonl", ".json"):
        rows = []
        for line in text.splitlines():
            if line.strip():
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    rows.append(obj)
        return rows

    import csv as csv_module
    import io

    return [dict(row) for row in csv_module.DictReader(io.StringIO(text))]


def _int_or_none(value):
    try:
        return int(float(str(value).replace(",", ""))) if value not in (None, "") else None
    except ValueError:
        return None


def _float_or_none(value):
    try:
        return float(str(value).replace(",", "")) if value not in (None, "") else None
    except ValueError:
        return None
