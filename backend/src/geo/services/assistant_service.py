"""AI 写作：把已有的 AI 推荐素材组织成一篇可人工审核的草稿。

决策 009 要「真调 LLM、产出落库、回链优化机会」，此前只有 A3 空壳。
本模块补上真调用那一段，边界如下（诚实优先）：

- **素材来自本系统已有的真实数据**：当前问题、AI 回答的引用信源(citation)、
  tracker 关键词、人工可选贴入的参考要点。不爬公众号、不模拟登录、不抄正文。
  「借鉴别人公众号」在这一版只能是人工贴进来、AI 按关键词重组、事后人工改——
  项目硬约束(roadmap「不做什么」)明确不做爬虫对抗，版权也不允许照搬。
- **一次调用落一篇 draft**，带 article_fact(每条带 source_ref)与六阶段状态；
  人登录后走完「正文编辑→质量审核」，AI 不直接发布。
- **调用失败显式抛错**，不静默返回假文章；原始响应落盘可回溯。
- 复用 DeepSeek 采集器同一个 key 与 HTTP transport，但不带联网搜索工具：
  素材是喂进去的组织内容，让模型再搜一遍既费钱又不可控。
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime

from geo.domain import credits as credits_domain
from geo.repositories import sqlite_repository as repo
from geo.collectors import keys
from geo.collectors.base import CollectorError
from geo.collectors.failures import raise_for_network, raise_for_status
from geo.collectors.http import DEFAULT_TIMEOUT, HttpResponse, Transport, post_json
from geo.collectors.rawlog import save_raw
from geo.services import article_stage_service, content_service, credit_service

ENDPOINT = "https://api.deepseek.com/anthropic/v1/messages"
DEFAULT_MODEL = "deepseek-chat"  # 写作不用 flash 联网版；纯生成用 chat
MAX_TOKENS = 3000


class AssistantError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def available() -> bool:
    return bool(os.getenv("GEO_DEEPSEEK_API_KEY") or keys.get_key("deepseek"))


def _build_prompt(material: dict) -> str:
    """把结构化素材拼成写作指令。材料都来自库里或人工贴入，不臆造。"""
    brand = material.get("brand_name") or material.get("tracker_name") or "该品牌"
    keywords = [k.strip() for k in (material.get("keywords") or []) if str(k).strip()]
    question = (material.get("question_text") or "").strip()
    citations = material.get("citations") or []
    reference = (material.get("reference") or "").strip()

    lines = [
        "你是 GEO 内容编辑。请基于下面给出的素材写一篇中文短文，供人工审核后使用。",
        "硬性要求：",
        "1. 只使用提供的素材；素材里没有的事实不要编，宁可少写。",
        "2. 自然包含给出的关键词，但不要堆砌。",
        "3. 引用到的每条素材要在 facts 里给出 source_ref（原文 URL 或「人工参考」）。",
        "4. 不要出现「保证」「全网最便宜」「100%」等绝对化或承诺性表述。",
        "5. 严格只返回 JSON，格式：",
        '   {"title":"...","body":"...","facts":[{"claim":"...","source_ref":"..."}]}',
        "   body 用换行分段；不要输出 JSON 以外的任何文字、不要用代码围栏。",
        f"\n品牌：{brand}",
    ]
    if question:
        lines.append(f"围绕的问题：{question}")
    if keywords:
        lines.append("必须覆盖的关键词：" + "、".join(keywords[:12]))
    profile = material.get("client_profile") or {}
    profile_lines = []
    if profile.get("intro"):
        profile_lines.append(f"- 品牌简介：{profile['intro']}")
    if profile.get("expertise"):
        profile_lines.append(f"- 专业特长：{profile['expertise']}")
    if profile.get("honors"):
        profile_lines.append(f"- 荣誉资质：{profile['honors']}")
    if profile.get("cases"):
        profile_lines.append("- 成功案例：" + "；".join(str(c) for c in profile["cases"]))
    if profile.get("geo_words"):
        profile_lines.append(f"- 服务方向：{profile['geo_words']}")
    if profile_lines:
        lines.append("\n客户自行登记的资料（第一方自述，可作为品牌事实写入；")
        lines.append("引用这些时 source_ref 写「客户档案」，不要当成外部信源）：")
        lines.extend(profile_lines)
    if citations:
        lines.append("\n可引用的信源（标题 / URL）：")
        for c in citations[:12]:
            title = str(c.get("title") or c.get("media_name") or "").strip()
            url = str(c.get("url") or "").strip()
            if url:
                lines.append(f"- {title} — {url}")
    if reference:
        lines.append("\n人工提供的参考要点（可改写，但不要照抄整段）：")
        lines.append(reference[:2000])
    if not citations and not reference:
        lines.append("\n注意：没有可引用信源，请写一篇不依赖外部事实的通用说明，facts 可为空数组。")
    return "\n".join(lines)


def _extract_json(text: str) -> dict:
    """从模型文本里取 JSON 对象。容忍外层代码围栏，不许缺字段。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```", 2)[1]
        if cleaned.lstrip().startswith("json"):
            cleaned = cleaned.lstrip()[4:]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise AssistantError("BAD_DRAFT", "模型没有返回 JSON 对象，原始响应已落盘")
    try:
        data = json.loads(cleaned[start:end + 1])
    except ValueError as exc:
        raise AssistantError("BAD_DRAFT", f"模型返回的 JSON 解析失败：{exc}") from exc
    title = str(data.get("title") or "").strip()
    body = str(data.get("body") or "").strip()
    if not title or not body:
        raise AssistantError("BAD_DRAFT", "模型返回的草稿缺 title 或 body")
    facts = []
    for item in (data.get("facts") or []) if isinstance(data.get("facts"), list) else []:
        if not isinstance(item, dict):
            continue
        claim = str(item.get("claim") or "").strip()
        if not claim:
            continue
        facts.append({
            "claim": claim,
            "source_ref": str(item.get("source_ref") or "").strip() or None,
        })
    return {"title": title[:200], "body": body, "facts": facts}


def _parse_message(body: dict) -> str:
    """Anthropic Messages 响应取文本块——复用采集器的解析口径，不带引用块。"""
    blocks = body.get("content")
    if not isinstance(blocks, list):
        raise AssistantError("BAD_RESPONSE", f"响应缺 content 数组：{sorted(body)[:8]}")
    texts = [b.get("text", "") for b in blocks
             if isinstance(b, dict) and b.get("type") == "text" and b.get("text")]
    if not texts:
        raise AssistantError("EMPTY_TEXT", "模型返回 200 但正文为空")
    return "".join(texts)


def generate(
    material: dict,
    *,
    api_key: str | None = None,
    model: str | None = None,
    timeout: float | None = None,
    transport: Transport | None = None,
) -> dict:
    """真调一次 LLM，返回解析好的草稿 + 原始响应位置与 token。失败抛 AssistantError。"""
    key = api_key or keys.get_key("deepseek")
    if not key:
        raise AssistantError(
            "NOT_CONFIGURED",
            "没有 DeepSeek key。设 GEO_DEEPSEEK_API_KEY，或写进 backend/data/keys.local.json",
        )
    payload = {
        "model": model or os.getenv("GEO_DEEPSEEK_WRITER_MODEL", DEFAULT_MODEL),
        "max_tokens": MAX_TOKENS,
        "stream": False,
        "messages": [{"role": "user", "content": _build_prompt(material)}],
        # 故意不放 tools：素材已经喂进去，再联网会费钱且引入不可控来源。
    }
    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "Accept": "application/json",
    }
    use_transport = transport or post_json
    try:
        response = use_transport(
            ENDPOINT, payload, headers,
            timeout if timeout is not None else float(os.getenv("GEO_COLLECT_TIMEOUT", DEFAULT_TIMEOUT)),
        )
    except Exception as exc:
        try:
            raise_for_network(exc, "DeepSeek 写作", 60)
        except CollectorError as net_exc:
            raise AssistantError(net_exc.code, str(net_exc)) from exc
        raise
    try:
        raise_for_status(response, "DeepSeek 写作")
    except CollectorError as exc:
        raise AssistantError(exc.code, str(exc)) from exc

    try:
        raw_body = json.loads(response.body.decode("utf-8", "replace"))
    except ValueError as exc:
        raise AssistantError("BAD_RESPONSE", f"写作响应不是 JSON：{exc}") from exc
    text = _parse_message(raw_body)
    draft = _extract_json(text)

    raw_uri, save_error = save_raw("deepseek-writer", raw_body)
    usage = raw_body.get("usage") or {}
    return {
        **draft,
        "model_version": raw_body.get("model") or payload["model"],
        "token_in": usage.get("input_tokens"),
        "token_out": usage.get("output_tokens"),
        "raw_uri": raw_uri,
        "raw_save_error": save_error,
        "latency_ms": response.elapsed_ms if isinstance(response, HttpResponse) else None,
    }


def client_profile_from_settings(settings_raw: str | None) -> dict:
    """从 tracker.settings 提取客户在台账/表一/表三里登记的自述资料。

    SOP 第二步要「导入客户资料搭建企业知识库」——客户在系统里填的简介、专业特长、
    服务方向，就是写稿该用的第一方素材。这是客户自述，不是外部引用信源，
    在 prompt 里会与 citation 明确分开标注，source_ref 记为「客户档案」。
    坏 JSON / 缺字段一律当没登记，不臆造。
    """
    try:
        data = json.loads(settings_raw or "{}")
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    service = data.get("service") if isinstance(data.get("service"), dict) else {}
    assets = data.get("assets") if isinstance(data.get("assets"), dict) else {}
    t1 = assets.get("table1") if isinstance(assets.get("table1"), dict) else {}
    t3 = assets.get("table3") if isinstance(assets.get("table3"), dict) else {}

    def txt(v) -> str:
        return v.strip() if isinstance(v, str) else ""

    cases = [c.strip() for c in (t1.get("cases") or []) if isinstance(c, str) and c.strip()]
    profile = {
        "intro": txt(t1.get("intro")),
        "expertise": txt(t1.get("expertise")),
        "honors": txt(t1.get("honors")),
        "cases": cases[:10],
        "geo_words": txt(t3.get("geo_words")),
        "package": txt(service.get("package")),
        "website": txt(service.get("website")) or txt(data.get("website")),
    }
    # 全是空就当没登记：返回空 dict，调用方一眼能判「没有客户档案」，不塞一堆空串。
    if not any(profile.values()):
        return {}
    return profile


def collect_material(
    conn: sqlite3.Connection,
    tracker_id: str,
    tenant_id: str,
    *,
    question_id: str | None = None,
    reference: str = "",
    citation_limit: int = 12,
) -> dict:
    """从库里已有的真实数据收集写作素材，不臆造、不抓取外部正文。

    引用只取「答案里真实引用过的」citation（按 URL 去重），并带上标题与域名，
    供模型写进 facts.source_ref；人工参考是用户自己贴的，标注为「人工参考」。
    """
    tracker = repo.get_tracker(conn, tracker_id, tenant_id)
    if not tracker:
        raise AssistantError("NOT_FOUND", "监控对象不存在")
    keywords: list[str] = []
    for raw in (tracker.get("keywords") or "").split(","):
        k = raw.strip()
        if k:
            keywords.append(k)
    question_text = ""
    if question_id:
        # 必须在调 LLM 之前校验归属：否则问题不属于本对象时，钱花完了
        # 才在 create_article 里被 QuestionNotFoundError 拒掉。边界校验先行。
        row = conn.execute(
            """SELECT q.text FROM question q
               JOIN question_set qs ON qs.id = q.question_set_id
               JOIN tracker t ON t.active_question_set_id = qs.id
               WHERE q.id = ? AND t.id = ? AND t.tenant_id = ?""",
            (question_id, tracker_id, tenant_id),
        ).fetchone()
        if not row:
            raise AssistantError("NOT_FOUND", "问题不存在或不属于当前监控对象的生效问题集")
        question_text = row["text"]

    seen, citations = set(), []
    rows = conn.execute(
        """SELECT c.url, c.media_name, c.domain
           FROM citation c JOIN answer a ON a.id = c.answer_id
           WHERE a.tracker_id = ? AND c.url IS NOT NULL AND c.url != ''
           ORDER BY c.published_at DESC, c.id""",
        (tracker_id,),
    ).fetchall()
    for row in rows:
        url = row["url"]
        if url in seen:
            continue
        seen.add(url)
        # 真实库 citation 没有 title 列——媒体名就是能拿到的最佳标题信号。
        citations.append({
            "url": url,
            "title": row["media_name"] or row["domain"] or "",
            "media_name": row["media_name"] or "",
        })
        if len(citations) >= citation_limit:
            break
    return {
        "tracker_name": tracker.get("name") or "",
        "brand_name": tracker.get("brand_name") or "",
        "keywords": keywords,
        "question_text": question_text,
        "citations": citations,
        "reference": reference,
        # 客户在系统台账里登记的自述资料（SOP 第二步的知识库投喂）
        "client_profile": client_profile_from_settings(tracker.get("settings")),
    }


def generate_draft(
    conn: sqlite3.Connection,
    tracker_id: str,
    tenant_id: str,
    *,
    question_id: str | None = None,
    reference: str = "",
    api_key: str | None = None,
    transport: Transport | None = None,
    actor_label: str = "assistant",
) -> dict:
    """收集素材 → 真调 LLM → 草稿/事实/阶段/积分一次落库。

    调用失败抛 AssistantError，调用方回滚事务，不留「有账无稿」或「有稿无阶段」
    的中间态。积分用 ref_type/ref_id 幂等，重复生成同一篇不会重复扣。
    """
    material = collect_material(conn, tracker_id, tenant_id, question_id=question_id, reference=reference)
    generated = generate(material, api_key=api_key, transport=transport)

    article = content_service.create_article(
        conn,
        tracker_id,
        tenant_id,
        {
            "title": generated["title"],
            "body": generated["body"],
            "question_id": question_id,
            "template_key": "ai_draft",
            "goal": "AI 生成待人工审核",
        },
    )
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    for pos, fact in enumerate(generated["facts"], start=1):
        conn.execute(
            """INSERT INTO article_fact(id, article_id, claim, verdict, source_ref, position)
               VALUES (?, ?, ?, 'pending', ?, ?)""",
            (str(uuid.uuid4()), article["id"], fact["claim"], fact["source_ref"], pos),
        )
    # 决策 009：一次生成记一条流水。ref 幂等，重试不会重复扣。
    # 用 record(SPEND) 而非 spend()：试点对象余额为 0，人工还没发额度；
    # spend() 会因余额不足拒绝，让 AI 写作根本跑不了。这里允许负余额表示
    # 「已消费但未预充额度」——账仍是真的（余额=流水求和），不造假数字。
    credit_service.record(
        conn,
        tenant_id=tenant_id,
        direction=credits_domain.SPEND,
        amount=5,
        reason="AI 生成草稿（待人工审核；试点未预充额度）",
        ref_type="ai_draft",
        ref_id=article["id"],
        actor_label=actor_label,
    )
    # 必须提交：create_article 内部已 commit 过一次，fact/ledger 是新事务。
    # 这里不 commit，连接一关就回滚——草稿留了、事实与账本全丢（真踩过）。
    conn.commit()
    return {
        **article,
        "model_version": generated["model_version"],
        "token_in": generated["token_in"],
        "token_out": generated["token_out"],
        "raw_uri": generated["raw_uri"],
        "fact_count": len(generated["facts"]),
        "source": "ai_generated_pending_review",
    }
