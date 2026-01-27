"""Shared exceptions for the data collection module.

This module centralizes all custom exceptions used across the data collection
package to avoid cross-cutting imports and circular dependencies.
"""

from __future__ import annotations


class ConfigurationError(ValueError):
    """Base exception for configuration-related errors.

    Raised when required configuration is missing, invalid, or misconfigured.
    Examples: missing API keys, invalid credentials, malformed config files.

    This exception enables daemon error classification to reliably identify
    configuration errors without fragile string matching on error messages.
    """


class APIResponseError(Exception):
    """Base exception for API response parsing errors.

    Raised when an API response cannot be parsed or has unexpected structure.
    Examples: missing required fields, type mismatches, malformed JSON.

    This exception enables daemon error classification to distinguish between
    configuration errors (which should exit) and response parsing errors
    (which may be transient or require investigation).
    """


class CTATrackerAPIError(Exception):
    """Exception raised when CTA Train Tracker API returns an error code in response body.

    CTA API returns HTTP 200 with error details in the JSON body under ctatt.errCd
    and ctatt.errNm. This exception preserves those values for daemon-level handling.

    Attributes:
        err_cd: CTA error code (e.g., "102", "500")
        err_nm: CTA error message (optional, may be None)

    """

    def __init__(self, err_cd: str, err_nm: str | None = None) -> None:
        """Initialize CTATrackerAPIError with error code and optional message.

        Args:
            err_cd: CTA error code from ctatt.errCd
            err_nm: CTA error message from ctatt.errNm (optional)

        """
        self.err_cd = err_cd
        self.err_nm = err_nm
        msg = f"CTA API error {err_cd}"
        if err_nm:
            msg += f": {err_nm}"
        super().__init__(msg)


class DaemonNotStartedError(RuntimeError):
    """Raised when an async daemon API is used before `start()`."""
