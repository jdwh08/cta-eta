"""Shared utilities for async daemons: error classification and discovery state.

Used by AsyncBaseDaemon implementations (e.g. WeatherDaemon, future TrainDaemon)
for run-loop exception handling and long-running discovery progress persistence.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from collections.abc import Callable


class ErrorCategory(Enum):
    """Error classification categories for daemon error handling."""

    TRANSIENT = "transient"  # Temporary errors that should be retried
    CONFIGURATION = "configuration"  # Configuration errors requiring immediate exit
    RATE_LIMIT = "rate_limit"  # Rate limit errors requiring backoff
    UNKNOWN = "unknown"  # Unknown errors, log and continue


def classify_error(error: Exception) -> ErrorCategory:
    """Classify an exception into an error category for appropriate handling.

    This function distinguishes between different types of errors to enable
    appropriate handling strategies:
    - TRANSIENT: Network errors, timeouts, temporary API failures (retry)
    - CONFIGURATION: Missing credentials, invalid config (exit gracefully)
    - RATE_LIMIT: HTTP 429 errors (apply backoff)
    - UNKNOWN: All other errors (log and continue)

    Args:
        error: Exception to classify

    Returns:
        ErrorCategory enum value indicating how to handle the error

    """
    # Configuration errors (missing credentials, invalid config)
    if isinstance(error, ValueError) and any(
        keyword in str(error).lower()
        for keyword in ["missing", "required", "invalid", "not set", "must be set"]
    ):
        return ErrorCategory.CONFIGURATION

    # Rate limit errors
    if (
        isinstance(error, httpx.HTTPStatusError)
        and error.response is not None
        and error.response.status_code == httpx.codes.TOO_MANY_REQUESTS
    ):
        return ErrorCategory.RATE_LIMIT

    # Transient errors (network issues, timeouts, temporary failures)
    if isinstance(error, (httpx.RequestError, httpx.TimeoutException, TimeoutError)):
        return ErrorCategory.TRANSIENT

    if isinstance(error, httpx.HTTPStatusError):
        # 5xx errors are typically transient
        if (
            error.response is not None
            and httpx.codes.INTERNAL_SERVER_ERROR
            <= error.response.status_code
            < httpx.codes.BAD_GATEWAY
        ):
            return ErrorCategory.TRANSIENT
        # 4xx errors (except 429) are typically configuration or client errors
        if (
            error.response is not None
            and httpx.codes.BAD_REQUEST
            <= error.response.status_code
            < httpx.codes.INTERNAL_SERVER_ERROR
        ):
            return ErrorCategory.CONFIGURATION

    # Default to unknown
    return ErrorCategory.UNKNOWN


class DiscoveryStateMarker:
    """Writes a progress marker for long-running batch discovery (e.g. cold-cache fill)."""

    def __init__(
        self,
        *,
        provider: str,
        total: int,
        write: Callable[[dict[str, object]], None],
        daemon_class: str,
    ) -> None:
        """Initialize the DiscoveryStateMarker.

        Args:
            provider: The provider of the discovery
            total: The total number of items to discover
            write: The function to write the discovery state
            daemon_class: The class of the daemon

        """
        self._provider = provider
        self._total = total
        self._write = write
        self._daemon_class = daemon_class
        self._succeeded = 0
        self._failed = 0
        self._started_at = time.time()

        self._payload: dict[str, object] = {
            "daemon_class": daemon_class,
            "provider": provider,
            "status": "in_progress",
            "total": total,
            "succeeded": 0,
            "failed": 0,
            "started_at": self._started_at,
            "updated_at": self._started_at,
        }

    def start(self) -> None:
        """Write the start of the discovery state."""
        self._write(self._payload)

    def success(self) -> None:
        """Write the success of the discovery state."""
        self._succeeded += 1
        self._payload["succeeded"] = self._succeeded
        self._payload["updated_at"] = time.time()
        self._write(self._payload)

    def failure(self) -> None:
        """Write the failure of the discovery state."""
        self._failed += 1
        self._payload["failed"] = self._failed
        self._payload["updated_at"] = time.time()
        self._write(self._payload)

    def finish(self, status: str, *, error: BaseException | None = None) -> None:
        """Write the finish of the discovery state.

        Args:
            status: The status of the discovery
            error: The error of the discovery

        """
        self._payload["status"] = status
        self._payload["updated_at"] = time.time()
        if error is not None:
            self._payload["error_type"] = type(error).__name__
            self._payload["error_message"] = str(error)
        self._write(self._payload)
