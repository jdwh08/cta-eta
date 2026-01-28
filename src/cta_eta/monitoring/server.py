"""FastAPI monitoring server for CTA data collection daemons.

Provides three endpoints for progressive investigation:
1. GET /status - High-level daemon health (last poll, total records, staleness)
2. GET /api-calls - Recent API call history with success rates
3. GET /gaps - Data collection gaps from metrics and Parquet metadata
"""

from __future__ import annotations

import json
import os
import re
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Final

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

### OWN MODULES
from cta_eta.data_collection.config import get_config_section
from cta_eta.data_collection.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = get_logger(__name__)

# Constants
_DAEMON_STATE_DIR: Final[Path] = Path(".daemon_state")
_DAEMON_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-zA-Z0-9_-]+$")
_STALENESS_THRESHOLD_SECONDS: Final[float] = 300.0  # 5 minutes
_MIN_TOKEN_LENGTH: Final[int] = 32  # Minimum secure token length


class RateLimiter:
    """Simple in-memory rate limiter tracking requests per IP."""

    def __init__(self, requests_per_minute: int = 60) -> None:
        """Initialize rate limiter.

        Args:
            requests_per_minute: Maximum requests allowed per IP per minute

        """
        self._requests_per_minute = requests_per_minute
        # IP -> deque of timestamps
        self._request_history: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=requests_per_minute)
        )

    def check_rate_limit(self, client_ip: str) -> bool:
        """Check if request is within rate limit.

        Args:
            client_ip: Client IP address

        Returns:
            True if request allowed, False if rate limit exceeded

        """
        now = time.time()
        history = self._request_history[client_ip]

        # Remove requests older than 1 minute
        while history and history[0] < now - 60.0:
            history.popleft()

        # Check if limit exceeded
        if len(history) >= self._requests_per_minute:
            return False

        # Record this request
        history.append(now)
        return True


def verify_token(
    request: Request,
    authorization: str | None = None,
) -> None:
    """Verify bearer token authentication.

    Args:
        request: FastAPI request object
        authorization: Authorization header value

    Raises:
        HTTPException: If token is missing or invalid (401 Unauthorized)

    """
    # Get token from environment
    expected_token = os.getenv("MONITORING_TOKEN", "").strip()

    # Extract authorization header
    auth_header = authorization or request.headers.get("authorization", "")

    # Check for Bearer prefix
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Extract token
    token = auth_header[7:].strip()  # Remove "Bearer " prefix

    # Verify token
    if not expected_token or token != expected_token:
        logger.warning(
            "Unauthorized access attempt",
            extra={"extra_fields": {"client_ip": request.client.host if request.client else "unknown"}},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def validate_daemon_name(name: str) -> str:
    """Validate daemon name to prevent path traversal.

    Args:
        name: Daemon name to validate

    Returns:
        Validated daemon name

    Raises:
        HTTPException: If daemon name is invalid (400 Bad Request)

    """
    if not _DAEMON_NAME_PATTERN.match(name):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid daemon name. Must contain only alphanumeric characters, hyphens, and underscores.",
        )
    return name


def create_app() -> FastAPI:  # noqa: C901, PLR0915
    """Create and configure FastAPI application.

    Returns:
        Configured FastAPI application

    """
    # Load configuration
    try:
        monitoring_config = get_config_section("monitoring")
    except (ValueError, TypeError):
        # Use defaults if monitoring section not found
        monitoring_config = {
            "enabled": True,
            "host": "127.0.0.1",
            "port": 8000,
            "allowed_origins": ["http://localhost:3000"],
            "rate_limit_per_minute": 60,
        }

    # Validate token on startup
    token = os.getenv("MONITORING_TOKEN", "").strip()
    if not token:
        logger.error("MONITORING_TOKEN not set - server will reject all requests")
    elif len(token) < _MIN_TOKEN_LENGTH:
        logger.warning(
            f"MONITORING_TOKEN is weak (< {_MIN_TOKEN_LENGTH} characters) - consider using a stronger token",
            extra={"extra_fields": {"token_length": len(token)}},
        )

    # Initialize rate limiter
    rate_limit_per_minute = int(monitoring_config.get("rate_limit_per_minute", 60))
    rate_limiter = RateLimiter(requests_per_minute=rate_limit_per_minute)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        """Lifespan context manager for startup/shutdown events."""
        # Startup
        host = monitoring_config.get("host", "127.0.0.1")
        port = monitoring_config.get("port", 8000)
        logger.info(
            "Starting CTA Data Collection Monitor",
            extra={
                "extra_fields": {
                    "host": host,
                    "port": port,
                    "rate_limit_per_minute": rate_limit_per_minute,
                }
            },
        )
        yield
        # Shutdown
        logger.info("Shutting down CTA Data Collection Monitor")

    # Create app
    app = FastAPI(
        title="CTA Data Collection Monitor",
        description="Monitoring server for CTA data collection daemons",
        version="1.0.0",
        lifespan=lifespan,
    )

    # Add CORS middleware
    allowed_origins = monitoring_config.get("allowed_origins", ["http://localhost:3000"])
    if isinstance(allowed_origins, str):
        allowed_origins = [allowed_origins]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_methods=["GET"],
        allow_headers=["Authorization"],
        allow_credentials=False,
    )

    # Rate limiting middleware
    @app.middleware("http")
    async def rate_limit_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]  # noqa: ANN001, ANN202
        """Rate limiting middleware."""
        client_ip = request.client.host if request.client else "unknown"

        if not rate_limiter.check_rate_limit(client_ip):
            logger.info(
                "Rate limit exceeded",
                extra={"extra_fields": {"client_ip": client_ip}},
            )
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": "Rate limit exceeded. Please try again later."},
            )

        response = await call_next(request)
        return response

    @app.get("/status")
    async def get_status(
        _token: Annotated[None, Depends(verify_token)] = None,
    ) -> dict[str, dict[str, object]]:
        """Get high-level daemon health status.

        Returns daemon state for all daemons: last poll timestamp, total records,
        staleness indicator, and running state.

        Requires:
            Authorization: Bearer token authentication

        Returns:
            Dictionary mapping daemon names to status objects with:
            - last_poll_timestamp: float | None
            - total_records: int | None
            - is_stale: bool (True if last poll > 5 minutes ago)
            - daemon_name: str

        Example:
            {
                "WeatherDaemon": {
                    "daemon_name": "WeatherDaemon",
                    "last_poll_timestamp": 1769288629.97,
                    "total_records": 145,
                    "is_stale": false
                }
            }

        """
        if not _DAEMON_STATE_DIR.exists():
            return {}

        status_data: dict[str, dict[str, object]] = {}
        now = time.time()

        # Read all daemon state files (*.json, not diagnostics/events/metrics)
        for state_file in _DAEMON_STATE_DIR.glob("*.json"):
            # Skip diagnostics files
            if "diagnostics" in state_file.name:
                continue

            daemon_name = state_file.stem
            try:
                with state_file.open(encoding="utf-8") as f:
                    state = json.load(f)

                last_poll = state.get("last_collection_timestamp")
                records = state.get("records_stored_last_cycle")

                # Calculate staleness
                is_stale = False
                if last_poll is not None:
                    is_stale = (now - float(last_poll)) > _STALENESS_THRESHOLD_SECONDS

                status_data[daemon_name] = {
                    "daemon_name": daemon_name,
                    "last_poll_timestamp": last_poll,
                    "total_records": records,
                    "is_stale": is_stale,
                }
            except (json.JSONDecodeError, OSError) as e:
                logger.debug(
                    "Failed to read daemon state",
                    extra={"extra_fields": {"daemon_name": daemon_name, "error": str(e)}},
                )
                continue

        return status_data

    @app.get("/api-calls")
    async def get_api_calls(
        daemon: Annotated[str, Query(description="Daemon name")],
        limit: Annotated[int, Query(ge=1, le=500, description="Maximum number of records to return")] = 50,
        _token: Annotated[None, Depends(verify_token)] = None,
    ) -> dict[str, object]:
        """Get recent API call history for a daemon.

        Returns recent API calls with timing, success/failure status, and overall
        success rate for the returned window.

        Args:
            daemon: Daemon name (e.g., "WeatherDaemon", "TrainPositionDaemon")
            limit: Maximum number of records to return (default: 50, max: 500)

        Requires:
            Authorization: Bearer token authentication

        Returns:
            Dictionary with:
            - daemon_name: str
            - calls: List of recent API calls with timestamp, name, elapsed_ms, ok, error_type
            - window_success_rate: float (success rate for returned calls)
            - total_calls: int (number of calls returned)

        Raises:
            HTTPException: 400 if daemon name invalid, 404 if daemon not found

        """
        # Validate daemon name
        daemon = validate_daemon_name(daemon)

        # Read events.jsonl file
        events_file = _DAEMON_STATE_DIR / f"{daemon}.events.jsonl"
        if not events_file.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Daemon '{daemon}' not found or has no event history",
            )

        try:
            # Read events and filter for span events
            calls: list[dict[str, object]] = []
            with events_file.open(encoding="utf-8") as f:
                for line in f:
                    try:
                        event = json.loads(line.strip())
                        if event.get("kind") == "span":
                            calls.append({
                                "timestamp": event.get("ts"),
                                "name": event.get("name"),
                                "elapsed_ms": event.get("elapsed_ms"),
                                "ok": event.get("ok"),
                                "error_type": event.get("error_type"),
                            })
                    except json.JSONDecodeError:
                        continue

            # Take last N records
            recent_calls = calls[-limit:] if len(calls) > limit else calls

            # Calculate success rate for window
            success_count = sum(1 for call in recent_calls if call.get("ok"))
            total = len(recent_calls)
            success_rate = success_count / total if total > 0 else 0.0

        except OSError as e:
            logger.exception(
                "Failed to read events file",
                extra={"extra_fields": {"daemon_name": daemon, "error": str(e)}},
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to read event history",
            ) from e
        else:
            return {
                "daemon_name": daemon,
                "calls": recent_calls,
                "window_success_rate": success_rate,
                "total_calls": total,
            }

    @app.get("/gaps")
    async def get_gaps(
        daemon: Annotated[str, Query(description="Daemon name")],
        _limit: Annotated[int, Query(ge=1, le=100, description="Maximum number of gaps to return")] = 20,
        _token: Annotated[None, Depends(verify_token)] = None,
    ) -> dict[str, object]:
        """Get data collection gaps for a daemon.

        Returns gap information from recent metrics snapshots, showing when
        data collection was interrupted and for how long.

        Args:
            daemon: Daemon name (e.g., "WeatherDaemon", "TrainPositionDaemon")
            limit: Maximum number of gaps to return (default: 20, max: 100)

        Requires:
            Authorization: Bearer token authentication

        Returns:
            Dictionary with:
            - daemon_name: str
            - total_gaps: int
            - gaps: List of gap objects (from metrics.jsonl if available)
            - note: str (informational message about gap sources)

        Raises:
            HTTPException: 400 if daemon name invalid, 404 if daemon not found

        """
        # Validate daemon name
        daemon = validate_daemon_name(daemon)

        # Read metrics.jsonl file
        metrics_file = _DAEMON_STATE_DIR / f"{daemon}.metrics.jsonl"

        if not metrics_file.exists():
            # No metrics file yet - this is expected for newly started systems
            return {
                "daemon_name": daemon,
                "total_gaps": 0,
                "gaps": [],
                "note": "No metrics file found. Metrics are written periodically during daemon operation.",
            }

        try:
            # Read most recent metrics snapshot
            latest_metrics = None
            with metrics_file.open(encoding="utf-8") as f:
                for line in f:
                    try:
                        snapshot = json.loads(line.strip())
                        latest_metrics = snapshot
                    except json.JSONDecodeError:
                        continue

            if not latest_metrics:
                return {
                    "daemon_name": daemon,
                    "total_gaps": 0,
                    "gaps": [],
                    "note": "Metrics file is empty or corrupted",
                }

            # Extract gap information from metrics
            # Note: The current metrics structure from 08-01 doesn't explicitly track gaps
            # This would need to be enhanced in a future phase to include gap detection
            # For now, return the metrics structure for investigation

        except OSError as e:
            logger.exception(
                "Failed to read metrics file",
                extra={"extra_fields": {"daemon_name": daemon, "error": str(e)}},
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to read metrics",
            ) from e
        else:
            return {
                "daemon_name": daemon,
                "total_gaps": 0,
                "gaps": [],
                "metrics_snapshot": latest_metrics,
                "note": "Gap detection from metrics is a placeholder. Full gap analysis requires enhancement in Phase 08-03.",
            }

    return app


def run_server(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run the monitoring server.

    Args:
        host: Host to bind to (default: 127.0.0.1)
        port: Port to bind to (default: 8000)

    """
    import uvicorn  # noqa: PLC0415

    app = create_app()
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run_server()
