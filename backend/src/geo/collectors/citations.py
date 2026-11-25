"""把「模型返回的引用」变成能写进 ``citation`` 表的一行。

这一层只做三件事：定 URL、判媒体类型、定引用形态。不做提及识别，
也不做「这条引用是不是客户自己的」——那需要 brand/competitor 上下文，
属于分析层（见 ``repositories/import_repository`` 的落库分工）。

## 为什么 URL 不在这里归一化后再算 hash

``citation.url_hash`` 用的是 **原始 URL 的 sha256**，与导入通道
（``import_service`` 里 ``"url_hash": sha(url)``）完全一致。
如果在采集这一侧先归一化再哈希，同一个页面走导入和走采集会得到两个 hash，
「按 URL 去重」立刻失效。

归一化（去 utm、去尾斜杠、http→https）是 **D4 归因匹配** 时用的，
放在这里导出但不参与落库，两边算法不会打架。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from geo.domain.enums import CiteType, MediaType

# 媒体类型只认这几个值，**从枚举派生而不是另写一份**。
# 两份字面量迟早会分叉：有人加了新类型只改一边，另一边照样"看起来对"。
MEDIA_TYPES = tuple(item.value for item in MediaType)

# 顺序有意义：先匹配到的赢。放在前面的是「即使域名里也带新闻字样，也该按论坛算」的站点。
_DOMAIN_RULES: tuple[tuple[tuple[str, ...], MediaType], ...] = (
    (("zhihu.com", "zhihu.cn", "tieba.baidu.com", "douban.com", "v2ex.com", "xueqiu.com"), MediaType.FORUM),
    (("mp.weixin.qq.com", "weixin.qq.com", "xiaohongshu.com"), MediaType.WECHAT),
    (("bilibili.com", "b23.tv", "douyin.com", "kuaishou.com", "youtube.com",
      "youtu.be", "v.qq.com", "youku.com", "iqiyi.com", "ixigua.com"), MediaType.VIDEO),
    (("baike.baidu.com", "wikipedia.org", "wikiwand.com", "baike.so.com", "zh.wikipedia.org"), MediaType.WIKI),
    (("wenshu.court.gov.cn", "pkulaw.com", "itslaw.com", "faxin.cn", "openlaw.cn",
      "judgelaw.com", "chinacourt.org"), MediaType.DATABASE),
    (("blog.", "/blog", "csdn.net", "jianshu.com", "cnblogs.com", "medium.com",
      "substack.com", "wordpress.com"), MediaType.BLOG),
)

# 追踪参数：不影响页面内容，只影响「同一条引用被当成几条」。
# 保留 fbclid/gclid 之外的一律去掉；spm 是阿里系，from/utm 是最常见的搬运痕迹。
_TRACKING_PREFIXES = ("utm_", "spm", "scm", "from_", "share_")
_TRACKING_EXACT = {
    "from", "share_token", "share_source", "fbclid", "gclid", "msclkid",
    "ref", "ref_src", "refer", "source", "src", "chksm", "scene", "clicktime",
}


@dataclass(frozen=True)
class CitationDraft:
    """一条待落库的引用。字段与 ``citation`` 表一一对应，不多不少。"""

    url: str
    title: str | None = None
    site_name: str | None = None
    published_at: str | None = None
    media_type: str = MediaType.OTHER.value
    cite_type: str = CiteType.REFERENCE.value
    position: int = 0


def normalize_url(url: str) -> str:
    """去掉追踪参数与尾斜杠，把 scheme/host 统一成小写。

    只用于**匹配**（D4 归因），不用于落库 hash。空输入返回空字符串。
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    parts = urlsplit(raw)
    if not parts.netloc:
        # 没有 scheme 的裸域名（"example.com/a"）也尽力归一
        parts = urlsplit("https://" + raw)

    scheme = (parts.scheme or "https").lower()
    if scheme == "http":
        scheme = "https"  # 同一个页面 http/https 视为同一条
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]

    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not _is_tracking(key)
    ]
    path = parts.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    return urlunsplit((scheme, host, path, urlencode(query), ""))


def _is_tracking(key: str) -> bool:
    lowered = key.lower()
    return lowered in _TRACKING_EXACT or lowered.startswith(_TRACKING_PREFIXES)


def domain_of(url: str) -> str:
    """取主机名，去 www.。取不到就返回空串（调用方会归到 other）。"""
    raw = (url or "").strip()
    if not raw:
        return ""
    parts = urlsplit(raw if "//" in raw else "//" + raw)
    host = (parts.hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def classify_media(url: str, site_name: str | None = None) -> str:
    """按域名判媒体类型，判不出再看站点名，最后归 other。

    刻意不按「标题里有没有新闻」猜：标题是模型给的，不可靠；
    域名是结构信息，同一站点永远同类。
    """
    host = domain_of(url)
    probe = f"{host}{(urlsplit(url).path or '') if host else ''}".lower()

    for needles, media_type in _DOMAIN_RULES:
        if any(needle in probe for needle in needles):
            return media_type.value

    if site_name:
        name = site_name.lower()
        if "百科" in site_name:
            return MediaType.WIKI.value
        if "知乎" in site_name or "论坛" in site_name or "贴吧" in site_name:
            return MediaType.FORUM.value
        if "公众号" in site_name:
            return MediaType.WECHAT.value
        if "视频" in site_name or "抖音" in site_name or "b站" in name:
            return MediaType.VIDEO.value
        if "裁判" in site_name or "案例" in site_name or "法规" in site_name:
            return MediaType.DATABASE.value

    return MediaType.NEWS.value if host else MediaType.OTHER.value


def infer_cite_type(media_type: str, platform_default: str) -> str:
    """引用形态：视频与资料库显然不是「正文内联引用」，其余跟平台走。

    ``platform_default`` 由采集器给：DeepSeek 的引用挂在正文块上（inline），
    豆包的 url_citation 是答案级（reference）。同一个媒体类型在两个平台
    形态可能不同，所以不让这里硬定。
    """
    if media_type == MediaType.VIDEO:
        return CiteType.VIDEO.value
    if media_type in (MediaType.WIKI, MediaType.DATABASE):
        return CiteType.REFERENCE.value
    if platform_default in (CiteType.INLINE, CiteType.REFERENCE):
        return platform_default
    return CiteType.REFERENCE.value


def build_drafts(
    raw_items: list[dict],
    *,
    platform_default: str,
    extra_by_url: dict[str, dict] | None = None,
) -> list[CitationDraft]:
    """把平台原始引用列表变成 draft，按 URL 去重并重新编号 position。

    ``extra_by_url``：补字段用的旁证。DeepSeek 的 ``site_name`` / ``page_age``
    不在 citation 里，在 ``web_search_tool_result`` 里；调用方按 url 建好映射传进来。

    去重而不是累加：同一条答案里同一个 URL 被引两次，对「谁被引用了」没有增量，
    留着只会让引用条数虚高。position 从 1 开始，记第一次出现的位置。
    """
    extras = extra_by_url or {}
    seen: set[str] = set()
    drafts: list[CitationDraft] = []

    for item in raw_items:
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        key = normalize_url(url) or url
        if key in seen:
            continue
        seen.add(key)

        side = extras.get(url) or extras.get(key) or {}
        site_name = item.get("site_name") or side.get("site_name")
        published = item.get("published_at") or side.get("page_age") or None
        media_type = classify_media(url, site_name)

        drafts.append(
            CitationDraft(
                url=url,
                title=item.get("title") or side.get("title") or None,
                site_name=site_name,
                published_at=_iso_or_none(published),
                media_type=media_type,
                cite_type=infer_cite_type(media_type, platform_default),
                position=len(drafts) + 1,
            )
        )

    return drafts


def _iso_or_none(value) -> str | None:
    """发布时间只接受能解析的 ISO 串。解析不了就当未知，不猜今天。"""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    # 2025-10-15T16:00:00+00:00 / 2026-09-04T10:24:00+08:00 / 2025-10-15
    if re.match(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?([+-]\d{2}:?\d{2}|Z)?)?$", text):
        return text.replace(" ", "T")
    return None
