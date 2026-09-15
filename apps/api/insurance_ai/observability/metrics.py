"""Small, dependency-free Prometheus metrics registry.

The API exports process-local metrics. A Prometheus-compatible collector should
scrape every replica and aggregate across the stable, deliberately low-cardinality
labels defined in this module.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Iterable

LabelValues = tuple[str, ...]


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _labels(names: tuple[str, ...], values: LabelValues) -> str:
    if not names:
        return ""
    rendered = ",".join(
        f'{name}="{_escape_label(value)}"' for name, value in zip(names, values, strict=True)
    )
    return "{" + rendered + "}"


def _number(value: float) -> str:
    if math.isinf(value):
        return "+Inf" if value > 0 else "-Inf"
    if value.is_integer():
        return str(int(value))
    return format(value, ".12g")


class Counter:
    """Thread-safe monotonically increasing labeled counter."""

    metric_type = "counter"

    def __init__(self, name: str, help_text: str, label_names: Iterable[str] = ()) -> None:
        self.name = name
        self.help_text = help_text
        self.label_names = tuple(label_names)
        self._values: dict[LabelValues, float] = {}
        self._lock = threading.Lock()

    def inc(self, *label_values: str, amount: float = 1.0) -> None:
        labels = tuple(str(value) for value in label_values)
        if len(labels) != len(self.label_names):
            raise ValueError(f"{self.name} requires {len(self.label_names)} labels")
        if amount < 0:
            raise ValueError("counter amount cannot be negative")
        with self._lock:
            self._values[labels] = self._values.get(labels, 0.0) + amount

    def render_samples(self) -> list[str]:
        with self._lock:
            values = sorted(self._values.items())
        return [
            f"{self.name}{_labels(self.label_names, labels)} {_number(value)}"
            for labels, value in values
        ]

    def clear(self) -> None:
        with self._lock:
            self._values.clear()


class Gauge(Counter):
    """Thread-safe labeled gauge."""

    metric_type = "gauge"

    def set(self, value: float, *label_values: str) -> None:
        labels = tuple(str(item) for item in label_values)
        if len(labels) != len(self.label_names):
            raise ValueError(f"{self.name} requires {len(self.label_names)} labels")
        with self._lock:
            self._values[labels] = value

    def dec(self, *label_values: str, amount: float = 1.0) -> None:
        self.inc(*label_values, amount=-amount)

    def inc(self, *label_values: str, amount: float = 1.0) -> None:
        labels = tuple(str(value) for value in label_values)
        if len(labels) != len(self.label_names):
            raise ValueError(f"{self.name} requires {len(self.label_names)} labels")
        with self._lock:
            self._values[labels] = self._values.get(labels, 0.0) + amount


class Histogram:
    """Prometheus histogram with fixed buckets and labeled count/sum."""

    metric_type = "histogram"

    def __init__(
        self,
        name: str,
        help_text: str,
        label_names: Iterable[str] = (),
        buckets: Iterable[float] = (),
    ) -> None:
        self.name = name
        self.help_text = help_text
        self.label_names = tuple(label_names)
        self.buckets = tuple(sorted(set(float(bucket) for bucket in buckets)))
        if any(not math.isfinite(bucket) or bucket <= 0 for bucket in self.buckets):
            raise ValueError("histogram buckets must be finite and positive")
        self._counts: dict[LabelValues, list[int]] = {}
        self._totals: dict[LabelValues, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def observe(self, value: float, *label_values: str) -> None:
        labels = tuple(str(item) for item in label_values)
        if len(labels) != len(self.label_names):
            raise ValueError(f"{self.name} requires {len(self.label_names)} labels")
        if not math.isfinite(value) or value < 0:
            raise ValueError("histogram observation must be finite and non-negative")
        with self._lock:
            counts = self._counts.setdefault(labels, [0] * (len(self.buckets) + 1))
            for index, upper_bound in enumerate(self.buckets):
                if value <= upper_bound:
                    counts[index] += 1
            counts[-1] += 1
            count, total = self._totals.get(labels, (0, 0.0))
            self._totals[labels] = (count + 1, total + value)

    def render_samples(self) -> list[str]:
        lines: list[str] = []
        with self._lock:
            counts = {key: list(value) for key, value in self._counts.items()}
            totals = dict(self._totals)
        bucket_names = (*self.label_names, "le")
        for labels in sorted(totals):
            for upper_bound, count in zip((*self.buckets, math.inf), counts[labels], strict=True):
                bucket_labels = (*labels, _number(upper_bound))
                lines.append(f"{self.name}_bucket{_labels(bucket_names, bucket_labels)} {count}")
            count, total = totals[labels]
            lines.append(f"{self.name}_count{_labels(self.label_names, labels)} {count}")
            lines.append(f"{self.name}_sum{_labels(self.label_names, labels)} {_number(total)}")
        return lines

    def clear(self) -> None:
        with self._lock:
            self._counts.clear()
            self._totals.clear()


class MetricsRegistry:
    """Registry producing Prometheus text exposition format 0.0.4."""

    def __init__(self) -> None:
        self._metrics: list[Counter | Gauge | Histogram] = []

    def register(self, metric: Counter | Gauge | Histogram):
        if any(existing.name == metric.name for existing in self._metrics):
            raise ValueError(f"duplicate metric: {metric.name}")
        self._metrics.append(metric)
        return metric

    def render(self) -> str:
        lines: list[str] = []
        for metric in self._metrics:
            lines.extend(
                (
                    f"# HELP {metric.name} {metric.help_text}",
                    f"# TYPE {metric.name} {metric.metric_type}",
                    *metric.render_samples(),
                )
            )
        return "\n".join(lines) + "\n"

    def clear(self) -> None:
        for metric in self._metrics:
            metric.clear()


REGISTRY = MetricsRegistry()

HTTP_REQUESTS = REGISTRY.register(
    Counter(
        "insurance_ai_http_requests_total",
        "Completed HTTP requests.",
        ("method", "route", "status"),
    )
)
HTTP_REQUEST_DURATION = REGISTRY.register(
    Histogram(
        "insurance_ai_http_request_duration_seconds",
        "End-to-end HTTP request duration in seconds.",
        ("method", "route"),
        (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 120),
    )
)
HTTP_REQUESTS_ACTIVE = REGISTRY.register(
    Gauge(
        "insurance_ai_http_requests_active",
        "HTTP requests currently executing.",
        ("method",),
    )
)
PROVIDER_REQUESTS = REGISTRY.register(
    Counter(
        "insurance_ai_provider_requests_total",
        "Completed provider operations.",
        ("provider", "operation", "outcome"),
    )
)
PROVIDER_REQUEST_DURATION = REGISTRY.register(
    Histogram(
        "insurance_ai_provider_request_duration_seconds",
        "Provider operation duration in seconds.",
        ("provider", "operation"),
        (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120),
    )
)
MODEL_QUEUE_REJECTIONS = REGISTRY.register(
    Counter(
        "insurance_ai_model_queue_rejections_total",
        "Model operations rejected before inference.",
        ("provider", "reason"),
    )
)
MODEL_REQUESTS_ACTIVE = REGISTRY.register(
    Gauge(
        "insurance_ai_model_requests_active",
        "Model operations currently admitted to inference.",
        ("provider",),
    )
)
