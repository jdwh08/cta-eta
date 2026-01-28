"""Async daemon diagnostics and lightweight tracing.

This module intentionally avoids heavyweight observability dependencies. It provides:
- Low-overhead span timing (async context manager)
- Structured error/event recording with bounded memory
- Periodic summary logging for long-running daemons
- Optional JSONL event sink for offline analysis (rotated by size)

Design goals:
- Safe for 24/7 operation (bounded memory, best-effort I/O, never raises on telemetry)
- Helpful for diagnosing network/rate-limit issues (timeout types, durations, counts)
"""

from __future__ import annotations

import json
import time
import uuid
from collections import Counter, defaultdict, deque
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

import httpx

### OWN MODULES
from cta_eta.data_collection.config import get_config_section
from cta_eta.data_collection.logging import get_logger
from cta_eta.data_collection.utils import percentile, rotate_file_if_needed

if TYPE_CHECKING:
    import logging
    from collections.abc import AsyncIterator, Mapping

_DEFAULT_SUMMARY_INTERVAL_S: Final[float] = 300.0  # 5 minutes
_DEFAULT_MAX_RECENT_EVENTS: Final[int] = 250
_DEFAULT_EVENT_LOG_MAX_BYTES: Final[int] = 5 * 1024 * 1024  # 5 MiB
_DEFAULT_EVENT_LOG_BACKUPS: Final[int] = 3

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class DaemonDiagnosticsConfig:
    """Configuration for daemon diagnostics."""

    enabled: bool = False
    summary_interval_seconds: float = _DEFAULT_SUMMARY_INTERVAL_S
    max_recent_events: int = _DEFAULT_MAX_RECENT_EVENTS

    # If set, events are appended as JSONL and rotated by size.
    event_log_path: str | None = None
    event_log_max_bytes: int = _DEFAULT_EVENT_LOG_MAX_BYTES
    event_log_backups: int = _DEFAULT_EVENT_LOG_BACKUPS

    @classmethod
    def from_config(
        cls,
        cfg: Mapping[str, object] | None,
        *,
        daemon_name: str,
        config: dict[str, dict[str, str | int | float | bool]] | None = None,
    ) -> DaemonDiagnosticsConfig:
        """Create a DaemonDiagnosticsConfig from a raw configuration or the global config.

        When `cfg` is None, the `[diagnostics]` section is taken from `config` if
        provided, or from `config.toml` via `get_config_section` when `config` is None.
        Pass `config={}` in tests to avoid loading from disk while still using this path.
        """
        if cfg is None:
            cfg = get_config_section("diagnostics", config=config)
        if not cfg:
            return cls()

        def _get_bool(key: str, default: bool) -> bool:
            value = cfg.get(key, default)
            return bool(value)

        def _get_float(key: str, default: float) -> float:
            value = cfg.get(key, default)
            try:
                return float(value)  # type: ignore[arg-type]
            except ValueError:
                return default

        def _get_int(key: str, default: int) -> int:
            value = cfg.get(key, default)
            try:
                return int(value)  # type: ignore[arg-type]
            except ValueError:
                return default

        event_log_path = cfg.get("event_log_path")
        if event_log_path is None:
            # Sensible default location when diagnostics are enabled.
            logger.warning(
                "No event_log_path provided, using default",
                extra={"extra_fields": {"daemon_name": daemon_name}},
            )
            event_log_path = f".daemon_state/{daemon_name}.events.jsonl"

        return cls(
            enabled=bool(cfg.get("enabled", False)),
            summary_interval_seconds=max(
                1.0, _get_float("summary_interval_seconds", _DEFAULT_SUMMARY_INTERVAL_S)
            ),
            max_recent_events=max(
                10, _get_int("max_recent_events", _DEFAULT_MAX_RECENT_EVENTS)
            ),
            event_log_path=str(event_log_path) if event_log_path else None,
            event_log_max_bytes=max(
                1024, _get_int("event_log_max_bytes", _DEFAULT_EVENT_LOG_MAX_BYTES)
            ),
            event_log_backups=max(
                0, _get_int("event_log_backups", _DEFAULT_EVENT_LOG_BACKUPS)
            ),
        )


class DaemonDiagnostics:
    """Lightweight diagnostics recorder for long-running daemons."""

    def __init__(
        self,
        *,
        logger: logging.Logger,
        daemon_name: str,
        config: DaemonDiagnosticsConfig,
    ) -> None:
        """Initialize the DaemonDiagnostics."""
        self._logger = logger
        self._daemon = daemon_name
        self._config = config

        self._recent_events: deque[dict[str, object]] = deque(
            maxlen=config.max_recent_events
        )
        self._span_counts: Counter[str] = Counter()
        self._span_errors: Counter[str] = Counter()
        self._error_types: Counter[str] = Counter()
        self._durations_ms: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=250)
        )
        # Timestamped span records for rolling window metrics
        self._span_records: deque[tuple[float, str, bool, float]] = deque(maxlen=1000)

        self._last_summary_at = time.monotonic()
        self._run_id = uuid.uuid4().hex

    @property
    def enabled(self) -> bool:
        """Whether the diagnostics are enabled."""
        return self._config.enabled

    @property
    def run_id(self) -> str:
        """Unique id for this daemon process run (useful for correlating restarts)."""
        return self._run_id

    def new_cycle_id(self) -> str:
        """Create a short correlation id for one polling cycle."""
        return uuid.uuid4().hex[:10]

    @asynccontextmanager
    async def span(self, name: str, **fields: object) -> AsyncIterator[None]:
        """Async span used to time an operation and capture exceptions."""
        if not self.enabled:
            yield
            return

        start = time.perf_counter()
        ok = False
        try:
            yield
            ok = True
        except Exception as e:
            self.record_error(name, e, **fields)
            raise
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            self.record_span(name, elapsed_ms, ok=ok, **fields)

    def record_span(
        self, name: str, elapsed_ms: float, *, ok: bool, **fields: object
    ) -> None:
        """Record a span."""
        if not self.enabled:
            return
        self._span_counts[name] += 1
        if not ok:
            self._span_errors[name] += 1
        self._durations_ms[name].append(float(elapsed_ms))
        # Store timestamped span record for rolling window calculations
        self._span_records.append((time.time(), name, ok, float(elapsed_ms)))
        self._record_event(
            kind="span",
            name=name,
            elapsed_ms=round(float(elapsed_ms), 2),
            ok=ok,
            **fields,
        )

    def record_error(self, name: str, exc: BaseException, **fields: object) -> None:
        """Record an error."""
        if not self.enabled:
            return
        exc_type = type(exc).__name__
        self._error_types[exc_type] += 1
        http_fields: dict[str, object] = {}
        # Enrich common HTTPX exception types for diagnosing rate limits vs timeouts.
        with suppress(Exception):
            if isinstance(exc, httpx.HTTPStatusError):
                http_fields["http_status"] = exc.response.status_code
                http_fields["http_url"] = str(exc.request.url)
                http_fields["http_method"] = exc.request.method
            elif isinstance(exc, httpx.RequestError):
                http_fields["http_url"] = str(exc.request.url)
                http_fields["http_method"] = exc.request.method
        self._record_event(
            kind="error",
            name=name,
            error_type=exc_type,
            error_message=str(exc),
            **http_fields,
            **fields,
        )

    def record_event(self, kind: str, **fields: object) -> None:
        """Record an event."""
        if not self.enabled:
            return
        self._record_event(kind=kind, **fields)

    def maybe_log_summary(self, *, force: bool = False) -> None:
        """Log a compact summary at most every summary_interval_seconds."""
        if not self.enabled:
            return
        now = time.monotonic()
        if (
            not force
            and (now - self._last_summary_at) < self._config.summary_interval_seconds
        ):
            return
        self._last_summary_at = now

        # Keep this summary compact; details are in event logs / recent events.
        spans = dict(self._span_counts)
        span_errors = dict(self._span_errors)
        error_types = dict(self._error_types)

        # Duration percentiles (p50/p95) per span where we have enough samples.
        duration_summary: dict[str, dict[str, float]] = {}
        for span_name, samples in self._durations_ms.items():
            if not samples:
                continue
            sorted_samples = sorted(samples)
            p50 = percentile(sorted_samples, 50)
            p95 = percentile(sorted_samples, 95)
            duration_summary[span_name] = {"p50_ms": p50, "p95_ms": p95}

        self._logger.info(
            "Daemon diagnostics summary",
            extra={
                "extra_fields": {
                    "daemon_class": self._daemon,
                    "diag_run_id": self._run_id,
                    "span_counts": spans,
                    "span_errors": span_errors,
                    "error_types": error_types,
                    "duration_ms": duration_summary,
                    "recent_event_count": len(self._recent_events),
                }
            },
        )

    def snapshot(self) -> dict[str, object]:
        """Return an in-memory snapshot suitable for persistence/debugging."""
        return {
            "daemon_class": self._daemon,
            "diag_run_id": self._run_id,
            "span_counts": dict(self._span_counts),
            "span_errors": dict(self._span_errors),
            "error_types": dict(self._error_types),
            "recent_events": list(self._recent_events),
        }

    def calculate_metrics(self) -> dict[str, object]:
        """Calculate rolling window metrics for monitoring.

        Returns a dictionary with:
        - per_span_metrics: {span_name: {success_rate, error_rate, total_calls, p50_ms, p95_ms, p99_ms}}
        - time_window_metrics: {last_hour: {...}, last_24h: {...}}
        - overall_health: aggregate health score (all spans combined)
        """
        if not self.enabled:
            return {}

        now = time.time()
        one_hour_ago = now - 3600.0
        twenty_four_hours_ago = now - 86400.0

        # Filter span records by time windows
        last_hour_records = [r for r in self._span_records if r[0] >= one_hour_ago]
        last_24h_records = [r for r in self._span_records if r[0] >= twenty_four_hours_ago]

        # Calculate metrics for each time window
        def _calculate_window_metrics(records: list[tuple[float, str, bool, float]]) -> dict[str, object]:
            """Calculate metrics for a specific time window."""
            if not records:
                return {}

            # Group by span name
            span_data: dict[str, list[tuple[bool, float]]] = defaultdict(list)
            for ts, name, ok, duration_ms in records:
                span_data[name].append((ok, duration_ms))

            per_span: dict[str, dict[str, float | int]] = {}
            total_success = 0
            total_calls = 0

            for span_name, data in span_data.items():
                success_count = sum(1 for ok, _ in data if ok)
                total = len(data)
                durations = [d for _, d in data]
                sorted_durations = sorted(durations)

                total_success += success_count
                total_calls += total

                per_span[span_name] = {
                    "success_rate": success_count / total if total > 0 else 0.0,
                    "error_rate": (total - success_count) / total if total > 0 else 0.0,
                    "total_calls": total,
                    "p50_ms": percentile(sorted_durations, 50) if sorted_durations else 0.0,
                    "p95_ms": percentile(sorted_durations, 95) if sorted_durations else 0.0,
                    "p99_ms": percentile(sorted_durations, 99) if sorted_durations else 0.0,
                }

            overall_success_rate = total_success / total_calls if total_calls > 0 else 0.0

            return {
                "per_span_metrics": per_span,
                "overall_success_rate": overall_success_rate,
                "total_calls": total_calls,
            }

        last_hour_metrics = _calculate_window_metrics(last_hour_records)
        last_24h_metrics = _calculate_window_metrics(last_24h_records)

        return {
            "time_window_metrics": {
                "last_hour": last_hour_metrics,
                "last_24h": last_24h_metrics,
            },
            "overall_health": last_hour_metrics.get("overall_success_rate", 0.0) if last_hour_metrics else 0.0,
        }

    def _record_event(self, *, kind: str, **fields: object) -> None:
        event: dict[str, object] = {
            "ts": time.time(),
            "kind": kind,
            "daemon_class": self._daemon,
            "diag_run_id": self._run_id,
            **fields,
        }
        self._recent_events.append(event)
        self._write_event_jsonl_best_effort(event)

    def _write_event_jsonl_best_effort(self, event: Mapping[str, object]) -> None:
        """Try to write an event to a JSONL file.

        Args:
            event: The event to write.

        """
        path_str = self._config.event_log_path
        if not self.enabled or not path_str:
            return
        path = Path(path_str)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            rotate_file_if_needed(
                path,
                max_bytes=self._config.event_log_max_bytes,
                backups=self._config.event_log_backups,
            )
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(dict(event), separators=(",", ":")) + "\n")
        except OSError:
            # Never let telemetry I/O take down the daemon.
            with suppress(OSError):
                self._logger.debug(
                    "Diagnostics event write failed",
                    extra={"extra_fields": {"daemon_class": self._daemon}},
                )
