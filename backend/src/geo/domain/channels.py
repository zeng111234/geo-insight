"""模型 × 端渠道清单（决策 009 / roadmap E2 的唯一口径）。

参考系统把「模型」画成 8 个模型 × 网页/手机 = 16 个渠道。我们只有 3 个模型的
官方 API 通道，所以 13 个格子必须显式写「未接入」，不能画空图、不填假数字。

为什么不把 available 写成「库里有没有数据」：演示库里某个模型恰好没样本，
不代表这条采集线没接入；反过来，接了线也可能今天还没采到。接入状态只由
**是否注册了采集器**决定，样本的多少另用 samples 字段表达。

`client` 是 answer 表上的列（决策 009：加列，不拆 model_key）。老数据一律
`web`——迁移加列时回填，不靠猜测。
"""

from __future__ import annotations

# 采集器注册表中的三个通道（collectors/__init__.py）。新增 adapter 时同步这里。
AVAILABLE_KEYS = frozenset(
    {
        "doubao:web",
        "deepseek:web",
        "kimi:web",
    }
)

# 8 个模型 × 2 个端。顺序按参考系统的观感：先网页后手机，同一模型挨在一起。
_MODELS = (
    ("doubao", "豆包"),
    ("deepseek", "DeepSeek"),
    ("kimi", "Kimi"),
    ("qwen", "千问"),
    ("yuanbao", "元宝"),
    ("wenxin", "文心一言"),
    ("quark", "夸克 AI"),
    ("bing", "Bing AI"),
)
_CLIENTS = (("web", "网页"), ("mobile", "手机"))


def _channel(model_key: str, model_label: str, client: str, client_label: str) -> dict:
    key = f"{model_key}:{client}"
    available = key in AVAILABLE_KEYS
    return {
        "key": key,
        "model_key": model_key,
        "client": client,
        "label": f"{model_label}{client_label}",
        "available": available,
        "note": "" if available else "未接入",
    }


CHANNELS: tuple[dict, ...] = tuple(
    _channel(model_key, model_label, client, client_label)
    for model_key, model_label in _MODELS
    for client, client_label in _CLIENTS
)


def channel_list() -> list[dict]:
    """返回全新的一叠字典，调用方改它不会污染全局清单。"""
    return [dict(c) for c in CHANNELS]


def is_known_client(value: object) -> bool:
    return value in {client for client, _ in _CLIENTS}
