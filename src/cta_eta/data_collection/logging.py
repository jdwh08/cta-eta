"""Structured logging utilities for production observability."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from functools import wraps
from typing import Any, Self, TypeVar, cast

# Thread-safe context storage for request correlation
_default_log_context: dict[str, Any] = {}
_log_context: ContextVar[dict[str, Any]] = ContextVar(
    "log_context", default=_default_log_context
)

F = TypeVar("F", bound=Callable[..., Any])


class JSONFormatter(logging.Formatter):
    """Custom formatter that outputs log records as JSON."""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        dt = datetime.fromtimestamp(record.created, tz=UTC)
        timestamp = dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

        log_data: dict[str, Any] = {
            "timestamp": timestamp,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        context = _log_context.get()
        if context:
            log_data.update(context)

        if hasattr(record, "extra_fields"):
            extra_fields = record.extra_fields
            if not isinstance(extra_fields, dict):
                msg = f"extra_fields must be a dictionary, got {type(extra_fields)}"
                raise ValueError(msg)
            log_data.update(cast("dict[str, Any]", extra_fields))

        return json.dumps(log_data)


class HumanReadableFormatter(logging.Formatter):
    """Human-readable formatter for development."""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record in human-readable format."""
        timestamp = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        message = (
            f"[{timestamp}] {record.levelname:8s} {record.name}: {record.getMessage()}"
        )

        context = _log_context.get()
        if context:
            message += f" | context={context}"

        if hasattr(record, "extra_fields"):
            extra_fields = record.extra_fields
            if not isinstance(extra_fields, dict):
                msg = f"extra_fields must be a dictionary, got {type(extra_fields)}"
                raise ValueError(msg)
            message += f" | extra={cast('dict[str, Any]', extra_fields)}"

        return message


def setup_logger(
    name: str, log_level: str = "INFO", json_format: bool = True
) -> logging.Logger:
    """Configure and return a logger instance."""
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, log_level.upper()))

    # Remove existing handlers to avoid duplicates
    logger.handlers.clear()

    handler = logging.StreamHandler()
    handler.setLevel(getattr(logging, log_level.upper()))
    handler.setFormatter(JSONFormatter() if json_format else HumanReadableFormatter())
    logger.addHandler(handler)

    # Prevent propagation to avoid duplicate logs
    logger.propagate = False

    return logger


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance by name."""
    return logging.getLogger(name)


class log_context:  # noqa: N801
    """Context manager that adds extra fields to all logs within context."""

    context: dict[str, Any]
    token: Token[dict[str, Any]] | None

    def __init__(self, **kwargs: object) -> None:
        """Initialize log context values to apply within the block."""
        self.context = kwargs
        self.token = None

    def __enter__(self) -> Self:
        """Enter the context and merge values into the current log context."""
        existing = _log_context.get()
        self.token = _log_context.set({**existing, **self.context})
        return self

    def __exit__(self, *args: object) -> None:
        """Exit the context and restore the previous log context."""
        if self.token is not None:
            _log_context.reset(self.token)


def log_api_call(
    logger: logging.Logger,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Log API call lifecycle with timing and metadata as a decorator."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        def wrapper(*args: object, **kwargs: object) -> object:
            if not hasattr(func, "__name__"):
                msg = f"func must have a __name__ attribute, got {type(func)}"
                raise ValueError(msg)
            func_name = func.__name__

            start_time = time.perf_counter()
            logger.info(
                f"API call started: {func_name}",
                extra={
                    "extra_fields": {
                        "function": func_name,
                        "args": str(args) if args else None,
                        "kwargs": str(kwargs) if kwargs else None,
                    }
                },
            )

            try:
                result = func(*args, **kwargs)
            except Exception as e:
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                logger.error(  # noqa: G201
                    f"API call failed: {func_name}: {e}",
                    exc_info=True,
                    extra={
                        "extra_fields": {
                            "function": func_name,
                            "error_type": type(e).__name__,
                            "error_message": str(e),
                            "response_time_ms": round(elapsed_ms, 2),
                        }
                    },
                )
                raise

            elapsed_ms = (time.perf_counter() - start_time) * 1000
            logger.info(
                f"API call completed: {func_name}",
                extra={
                    "extra_fields": {
                        "function": func_name,
                        "response_time_ms": round(elapsed_ms, 2),
                    }
                },
            )
            return result

        return wrapper

    return decorator
