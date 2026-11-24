"""Metric definitions.

Every function here is pure: it takes counts or positions and returns a
``MetricValue``. This is the single place where the metric wording lives, so
the golden-sample regression can exercise it without a database.
"""

from dataclasses import asdict, dataclass

from geo.settings import settings


@dataclass(frozen=True)
class MetricValue:
    value: float | None
    status: str
    sample_base: int

    def to_dict(self) -> dict:
        return asdict(self)


def _metric(value: float | None, base: int, digits: int = 4) -> MetricValue:
    if base < settings.min_samples:
        return MetricValue(value=None, status="insufficient_samples", sample_base=base)
    if value is None:
        return MetricValue(value=None, status="insufficient_samples", sample_base=base)
    return MetricValue(value=round(value, digits), status="ok", sample_base=base)


def mention_rate(mention_total: int, valid_total: int) -> MetricValue:
    """Mentioned samples / valid samples. Failures never enter the denominator."""
    if valid_total <= 0:
        return MetricValue(value=None, status="insufficient_samples", sample_base=0)
    return _metric(mention_total / valid_total, valid_total)


def coverage_rate(covered_questions: int, active_questions: int) -> MetricValue:
    """Questions with at least one mention / active questions."""
    if active_questions <= 0:
        return MetricValue(value=None, status="insufficient_samples", sample_base=0)
    return _metric(covered_questions / active_questions, active_questions)


def avg_rank_summary(ranked: int, position_sum: float) -> MetricValue:
    """用「位次数 + 位次和」算平均位次。

    为什么要这个入口：按周聚合时拿不到每天的原始位次列表，
    只能拿到汇总。直接对「日平均值」再平均会因为各天条数不同而算错，
    所以统一回到 sum/count 这一步再除。
    """
    if not ranked:
        return MetricValue(value=None, status="insufficient_samples", sample_base=0)
    return _metric(position_sum / ranked, ranked, digits=2)


def avg_rank(positions: list[int]) -> MetricValue:
    """Mean list index. Only samples that actually have a list index count."""
    if not positions:
        return MetricValue(value=None, status="insufficient_samples", sample_base=0)
    return avg_rank_summary(len(positions), sum(positions))


def top_n_rate(positions: list[int], n: int) -> MetricValue:
    """Samples with position <= n / samples that have a position."""
    if not positions:
        return MetricValue(value=None, status="insufficient_samples", sample_base=0)
    hits = sum(1 for p in positions if p <= n)
    return _metric(hits / len(positions), len(positions))


def sentiment_distribution(sentiments: list[str]) -> dict:
    """Sentiment shares. The base is mention samples, never valid samples."""
    base = len(sentiments)
    counts = {"pos": 0, "neu": 0, "neg": 0}
    for s in sentiments:
        if s in counts:
            counts[s] += 1
    if base < settings.min_samples:
        return {
            "pos": None,
            "neu": None,
            "neg": None,
            "base": base,
            "status": "insufficient_samples",
        }
    return {
        "pos": round(counts["pos"] / base, 4),
        "neu": round(counts["neu"] / base, 4),
        "neg": round(counts["neg"] / base, 4),
        "base": base,
        "status": "ok",
    }


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson interval for a proportion. Used for trends and reports."""
    if total <= 0:
        return (0.0, 0.0)
    p = successes / total
    denominator = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * ((p * (1 - p) / total + z * z / (4 * total * total)) ** 0.5)
    return (round((centre - margin) / denominator, 4), round((centre + margin) / denominator, 4))
