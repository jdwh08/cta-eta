"""Structured JSON logging for production observability."""

import json
import logging
import time
from collections.abc import Callable
from contextvars import ContextVar, Token
from functools import wraps
from typing import Any, TypeVar

# Thread-safe context storage for request correlation
_log_context: ContextVar[dict[str, Any]] = ContextVar("log_context", default={})

# Type variable for generic function wrapping
F = TypeVar("F", bound=Callable[..., Any])


class JSONFormatter(logging.Formatter):
    """Custom formatter that outputs log records as JSON."""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON with timestamp, level, logger, message, and extra fields.

        Args:
            record: logging.LogRecord

        Returns:
            str: JSON string
        """
        # Format timestamp with milliseconds in ISO 8601 format
        from datetime import datetime, timezone

        dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
        timestamp = dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

        log_data: dict[str, Any] = {
            "timestamp": timestamp,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add context variables if present
        context = _log_context.get()
        if context:
            log_data.update(context)

        # Add extra fields from the record
        if hasattr(record, "extra_fields"):
            extra_fields: dict[str, Any] = getattr(record, "extra_fields")
            log_data.update(extra_fields)

        return json.dumps(log_data)


class HumanReadableFormatter(logging.Formatter):
    """Human-readable formatter for development."""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record in human-readable format.

        Args:
            record: logging.LogRecord

        Returns:
            str: Human-readable string
        """
        timestamp = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        message = (
            f"[{timestamp}] {record.levelname:8s} {record.name}: {record.getMessage()}"
        )

        # Add context variables if present
        context = _log_context.get()
        if context:
            message += f" | context={context}"

        # Add extra fields from the record
        if hasattr(record, "extra_fields"):
            extra_fields: dict[str, Any] = getattr(record, "extra_fields")
            message += f" | extra={extra_fields}"

        return message


def setup_logger(
    name: str, log_level: str = "INFO", json_format: bool = True
) -> logging.Logger:
    """Configure and return a logger instance.

    Args:
        name: Logger name (typically module name)
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        json_format: If True, output JSON; if False, human-readable format

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, log_level.upper()))

    # Remove existing handlers to avoid duplicates
    logger.handlers.clear()

    # Create console handler
    handler = logging.StreamHandler()
    handler.setLevel(getattr(logging, log_level.upper()))

    # Set formatter based on format preference
    if json_format:
        formatter = JSONFormatter()
    else:
        formatter = HumanReadableFormatter()

    handler.setFormatter(formatter)
    logger.addHandler(handler)

    # Prevent propagation to avoid duplicate logs
    logger.propagate = False

    return logger


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance by name.

    Args:
        name: Logger name (typically module name)

    Returns:
        Logger instance
    """
    return logging.getLogger(name)


class log_context:
    """Context manager that adds extra fields to all logs within context.

    Usage:
        with log_context(request_id="abc123", trace_id="xyz789"):
            logger.info("Processing request")
    """

    context: dict[str, Any]
    token: Token[dict[str, Any]] | None

    def __init__(self, **kwargs: Any) -> None:
        """Initialize context manager with key-value pairs to add to logs."""
        self.context = kwargs
        self.token = None

    def __enter__(self) -> "log_context":
        """Enter context and set context variables."""
        # Merge with existing context
        existing = _log_context.get()
        new_context = {**existing, **self.context}
        self.token = _log_context.set(new_context)
        return self

    def __exit__(self, *args: object) -> None:
        """Exit context and reset context variables."""
        if self.token is not None:
            _log_context.reset(self.token)


def log_api_call(
    logger: logging.Logger,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator that logs API call lifecycle with timing and metadata.

    Logs:
        - Before call: method, url, params (INFO level)
        - After success: status_code, response_time_ms (INFO level)
        - After error: error_type, error_message (ERROR level)

    Args:
        logger: Logger instance to use for logging

    Returns:
        Decorator function

    Usage:
        @log_api_call(logger)
        def get_train_position(...):
            ...
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        def wrapper(*args: object, **kwargs: object) -> Any:
            # Extract URL if available (common in API functions)

            # Check that __name__ exists and is a string
            if not hasattr(func, "__name__"):
                raise ValueError(
                    f"func must have a __name__ attribute, got {type(func)}"
                )
            func_name = func.__name__

            # Log API call start
            start_time = time.perf_counter()
            logger.info(
                f"API call started: {func_name}",
                extra={
                    "extra_fields": {
                        "event": "api_call_start",
                        "function": func_name,
                        "args": str(args) if args else None,
                        "kwargs": str(kwargs) if kwargs else None,
                    }
                },
            )

            try:
                result = func(*args, **kwargs)
                elapsed_ms = (time.perf_counter() - start_time) * 1000

                # Log API call success
                logger.info(
                    f"API call completed: {func_name}",
                    extra={
                        "extra_fields": {
                            "event": "api_call_success",
                            "function": func_name,
                            "response_time_ms": round(elapsed_ms, 2),
                        }
                    },
                )
                return result

            except Exception as e:
                elapsed_ms = (time.perf_counter() - start_time) * 1000

                # Log API call error
                logger.error(
                    f"API call failed: {func_name}",
                    extra={
                        "extra_fields": {
                            "event": "api_call_error",
                            "function": func_name,
                            "error_type": type(e).__name__,
                            "error_message": str(e),
                            "response_time_ms": round(elapsed_ms, 2),
                        }
                    },
                )
                raise

        return wrapper

    return decorator
