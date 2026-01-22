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

if TYPE_CHECKING:
    import logging
    from collections.abc import AsyncIterator, Mapping

_DEFAULT_SUMMARY_INTERVAL_S: Final[float] = 300.0  # 5 minutes
_DEFAULT_MAX_RECENT_EVENTS: Final[int] = 250
_DEFAULT_EVENT_LOG_MAX_BYTES: Final[int] = 5 * 1024 * 1024  # 5 MiB
_DEFAULT_EVENT_LOG_BACKUPS: Final[int] = 3


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
        cls, raw: Mapping[str, object] | None, *, daemon_name: str
    ) -> DaemonDiagnosticsConfig:
        """Create a DaemonDiagnosticsConfig from a raw configuration."""
        if not raw:
            return cls()

        def _get_bool(key: str, default: bool) -> bool:
            value = raw.get(key, default)
            return bool(value)

        def _get_float(key: str, default: float) -> float:
            value = raw.get(key, default)
            try:
                return float(value)  # type: ignore[arg-type]
            except ValueError:
                return default

        def _get_int(key: str, default: int) -> int:
            value = raw.get(key, default)
            try:
                return int(value)  # type: ignore[arg-type]
            except ValueError:
                return default

        event_log_path = raw.get("event_log_path")
        if event_log_path is None:
            # Sensible default location when diagnostics are enabled.
            event_log_path = f".daemon_state/{daemon_name}.events.jsonl"

        return cls(
            enabled=_get_bool("enabled", False),
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
            p50 = _percentile(sorted_samples, 50)
            p95 = _percentile(sorted_samples, 95)
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
        path_str = self._config.event_log_path
        if not self.enabled or not path_str:
            return
        path = Path(path_str)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            _rotate_if_needed(
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


def _percentile(sorted_samples: list[float], pct: int) -> float:
    """Calculate a percentile of a list of samples."""
    if not sorted_samples:
        return 0.0
    if pct <= 0:
        return round(sorted_samples[0], 2)
    if pct >= 100:  # noqa: PLR2004
        return round(sorted_samples[-1], 2)
    k = (len(sorted_samples) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_samples) - 1)
    if f == c:
        return round(sorted_samples[f], 2)
    d0 = sorted_samples[f] * (c - k)
    d1 = sorted_samples[c] * (k - f)
    return round(d0 + d1, 2)


def _rotate_if_needed(path: Path, *, max_bytes: int, backups: int) -> None:
    """Rotate a file if it exceeds a maximum size."""
    if backups <= 0:
        return
    try:
        st = path.stat()
    except FileNotFoundError:
        return
    except OSError:
        return

    if st.st_size < max_bytes:
        return

    # Rotate: file -> .1, .1 -> .2, ... oldest dropped.
    for idx in range(backups, 0, -1):
        src = path.with_suffix(path.suffix + f".{idx}")
        dst = path.with_suffix(path.suffix + f".{idx + 1}")
        if idx == backups:
            with suppress(FileNotFoundError):
                dst.unlink()
        if src.exists():
            with suppress(OSError):
                src.replace(dst)

    with suppress(OSError):
        path.replace(path.with_suffix(path.suffix + ".1"))
