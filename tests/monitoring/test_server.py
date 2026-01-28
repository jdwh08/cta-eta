"""Tests for monitoring server endpoints."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

### OWN MODULES
from cta_eta.monitoring.server import create_app

if TYPE_CHECKING:
    from collections.abc import Generator


@pytest.fixture
def daemon_state_dir(tmp_path: Path) -> Generator[Path, None, None]:
    """Create temporary daemon state directory with test data."""
    state_dir = tmp_path / ".daemon_state"
    state_dir.mkdir()

    # Mock the daemon state directory
    import cta_eta.monitoring.server as server_module
    original_dir = server_module._DAEMON_STATE_DIR
    server_module._DAEMON_STATE_DIR = state_dir

    yield state_dir

    # Restore original
    server_module._DAEMON_STATE_DIR = original_dir


@pytest.fixture
def mock_token(monkeypatch: pytest.MonkeyPatch) -> str:
    """Set up mock authentication token."""
    token = "test_token_with_at_least_32_characters_for_security"
    monkeypatch.setenv("MONITORING_TOKEN", token)
    return token


@pytest.fixture
def client(daemon_state_dir: Path, mock_token: str) -> TestClient:
    """Create test client."""
    app = create_app()
    return TestClient(app)


@pytest.fixture
def auth_headers(mock_token: str) -> dict[str, str]:
    """Create authentication headers."""
    return {"Authorization": f"Bearer {mock_token}"}


class TestAuthentication:
    """Tests for authentication."""

    def test_request_without_token_returns_401(self, client: TestClient) -> None:
        """Test that requests without token are rejected."""
        response = client.get("/status")
        assert response.status_code == 401
        assert "authorization" in response.json()["detail"].lower()

    def test_request_with_invalid_token_returns_401(self, client: TestClient) -> None:
        """Test that requests with invalid token are rejected."""
        response = client.get(
            "/status",
            headers={"Authorization": "Bearer invalid_token"},
        )
        assert response.status_code == 401

    def test_request_with_valid_token_returns_200(
        self, client: TestClient, auth_headers: dict[str, str], daemon_state_dir: Path
    ) -> None:
        """Test that requests with valid token succeed."""
        # Create a daemon state file
        state_file = daemon_state_dir / "TestDaemon.json"
        state_file.write_text(json.dumps({
            "last_collection_timestamp": time.time(),
            "records_stored_last_cycle": 100,
        }))

        response = client.get("/status", headers=auth_headers)
        assert response.status_code == 200


class TestStatusEndpoint:
    """Tests for /status endpoint."""

    def test_empty_state_directory_returns_empty_dict(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """Test that empty state directory returns empty dict."""
        response = client.get("/status", headers=auth_headers)
        assert response.status_code == 200
        assert response.json() == {}

    def test_daemon_state_returned(
        self, client: TestClient, auth_headers: dict[str, str], daemon_state_dir: Path
    ) -> None:
        """Test that daemon state is returned correctly."""
        now = time.time()
        state_file = daemon_state_dir / "WeatherDaemon.json"
        state_file.write_text(json.dumps({
            "last_collection_timestamp": now,
            "records_stored_last_cycle": 145,
            "weather_interval_seconds": 1800,
        }))

        response = client.get("/status", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()

        assert "WeatherDaemon" in data
        daemon_status = data["WeatherDaemon"]
        assert daemon_status["daemon_name"] == "WeatherDaemon"
        assert daemon_status["last_poll_timestamp"] == now
        assert daemon_status["total_records"] == 145
        assert daemon_status["is_stale"] is False

    def test_stale_daemon_detected(
        self, client: TestClient, auth_headers: dict[str, str], daemon_state_dir: Path
    ) -> None:
        """Test that stale daemon is detected correctly."""
        old_timestamp = time.time() - 600.0  # 10 minutes ago
        state_file = daemon_state_dir / "StaleDaemon.json"
        state_file.write_text(json.dumps({
            "last_collection_timestamp": old_timestamp,
            "records_stored_last_cycle": 50,
        }))

        response = client.get("/status", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()

        assert "StaleDaemon" in data
        assert data["StaleDaemon"]["is_stale"] is True

    def test_multiple_daemons_returned(
        self, client: TestClient, auth_headers: dict[str, str], daemon_state_dir: Path
    ) -> None:
        """Test that multiple daemons are returned."""
        now = time.time()

        for daemon_name in ["WeatherDaemon", "TrainPositionDaemon"]:
            state_file = daemon_state_dir / f"{daemon_name}.json"
            state_file.write_text(json.dumps({
                "last_collection_timestamp": now,
                "records_stored_last_cycle": 100,
            }))

        response = client.get("/status", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()

        assert len(data) == 2
        assert "WeatherDaemon" in data
        assert "TrainPositionDaemon" in data

    def test_diagnostics_file_skipped(
        self, client: TestClient, auth_headers: dict[str, str], daemon_state_dir: Path
    ) -> None:
        """Test that diagnostics files are skipped."""
        now = time.time()

        # Create regular state file
        state_file = daemon_state_dir / "TestDaemon.json"
        state_file.write_text(json.dumps({
            "last_collection_timestamp": now,
            "records_stored_last_cycle": 100,
        }))

        # Create diagnostics file (should be ignored)
        diag_file = daemon_state_dir / "TestDaemon.diagnostics.json"
        diag_file.write_text(json.dumps({"some": "data"}))

        response = client.get("/status", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()

        assert len(data) == 1
        assert "TestDaemon" in data


class TestApiCallsEndpoint:
    """Tests for /api-calls endpoint."""

    def test_missing_daemon_returns_404(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """Test that missing daemon returns 404."""
        response = client.get("/api-calls?daemon=NonExistent", headers=auth_headers)
        assert response.status_code == 404

    def test_invalid_daemon_name_returns_400(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """Test that invalid daemon name returns 400."""
        response = client.get("/api-calls?daemon=../../../etc/passwd", headers=auth_headers)
        assert response.status_code == 400

    def test_api_calls_returned(
        self, client: TestClient, auth_headers: dict[str, str], daemon_state_dir: Path
    ) -> None:
        """Test that API calls are returned correctly."""
        events_file = daemon_state_dir / "TestDaemon.events.jsonl"

        # Write some span events
        events = [
            {
                "ts": time.time(),
                "kind": "span",
                "daemon_class": "TestDaemon",
                "diag_run_id": "test123",
                "name": "api_call_1",
                "elapsed_ms": 100.5,
                "ok": True,
            },
            {
                "ts": time.time(),
                "kind": "span",
                "daemon_class": "TestDaemon",
                "diag_run_id": "test123",
                "name": "api_call_2",
                "elapsed_ms": 200.3,
                "ok": False,
                "error_type": "TimeoutError",
            },
            {
                "ts": time.time(),
                "kind": "error",  # Not a span, should be filtered
                "daemon_class": "TestDaemon",
                "diag_run_id": "test123",
                "name": "some_error",
            },
        ]

        with events_file.open("w", encoding="utf-8") as f:
            for event in events:
                f.write(json.dumps(event) + "\n")

        response = client.get("/api-calls?daemon=TestDaemon", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()

        assert data["daemon_name"] == "TestDaemon"
        assert data["total_calls"] == 2  # Only span events
        assert len(data["calls"]) == 2
        assert data["window_success_rate"] == 0.5  # 1 success out of 2

        # Check first call
        call1 = data["calls"][0]
        assert call1["name"] == "api_call_1"
        assert call1["elapsed_ms"] == 100.5
        assert call1["ok"] is True

        # Check second call
        call2 = data["calls"][1]
        assert call2["name"] == "api_call_2"
        assert call2["ok"] is False
        assert call2["error_type"] == "TimeoutError"

    def test_limit_parameter_works(
        self, client: TestClient, auth_headers: dict[str, str], daemon_state_dir: Path
    ) -> None:
        """Test that limit parameter works correctly."""
        events_file = daemon_state_dir / "TestDaemon.events.jsonl"

        # Write 10 span events
        events = [
            {
                "ts": time.time(),
                "kind": "span",
                "daemon_class": "TestDaemon",
                "diag_run_id": "test123",
                "name": f"api_call_{i}",
                "elapsed_ms": 100.0,
                "ok": True,
            }
            for i in range(10)
        ]

        with events_file.open("w", encoding="utf-8") as f:
            for event in events:
                f.write(json.dumps(event) + "\n")

        response = client.get("/api-calls?daemon=TestDaemon&limit=5", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()

        assert data["total_calls"] == 5
        assert len(data["calls"]) == 5

    def test_limit_validation(
        self, client: TestClient, auth_headers: dict[str, str], daemon_state_dir: Path
    ) -> None:
        """Test that limit parameter is validated."""
        # Create events file
        events_file = daemon_state_dir / "TestDaemon.events.jsonl"
        events_file.write_text("")

        # Test limit too low
        response = client.get("/api-calls?daemon=TestDaemon&limit=0", headers=auth_headers)
        assert response.status_code == 422  # Validation error

        # Test limit too high
        response = client.get("/api-calls?daemon=TestDaemon&limit=1000", headers=auth_headers)
        assert response.status_code == 422  # Validation error


class TestGapsEndpoint:
    """Tests for /gaps endpoint."""

    def test_missing_metrics_file_returns_note(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """Test that missing metrics file returns informative note."""
        response = client.get("/gaps?daemon=TestDaemon", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()

        assert data["daemon_name"] == "TestDaemon"
        assert data["total_gaps"] == 0
        assert data["gaps"] == []
        assert "No metrics file found" in data["note"]

    def test_invalid_daemon_name_returns_400(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """Test that invalid daemon name returns 400."""
        response = client.get("/gaps?daemon=../../../etc/passwd", headers=auth_headers)
        assert response.status_code == 400

    def test_metrics_snapshot_returned(
        self, client: TestClient, auth_headers: dict[str, str], daemon_state_dir: Path
    ) -> None:
        """Test that metrics snapshot is returned."""
        metrics_file = daemon_state_dir / "TestDaemon.metrics.jsonl"

        # Write a metrics snapshot
        snapshot = {
            "ts": time.time(),
            "daemon_class": "TestDaemon",
            "diag_run_id": "test123",
            "metrics": {
                "time_window_metrics": {
                    "last_hour": {
                        "per_span_metrics": {
                            "api_call": {
                                "success_rate": 0.95,
                                "error_rate": 0.05,
                                "total_calls": 100,
                                "p50_ms": 150.0,
                                "p95_ms": 300.0,
                                "p99_ms": 500.0,
                            }
                        },
                        "overall_success_rate": 0.95,
                        "total_calls": 100,
                    }
                },
                "overall_health": 0.95,
            },
        }

        with metrics_file.open("w", encoding="utf-8") as f:
            f.write(json.dumps(snapshot) + "\n")

        response = client.get("/gaps?daemon=TestDaemon", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()

        assert data["daemon_name"] == "TestDaemon"
        assert "metrics_snapshot" in data
        assert data["metrics_snapshot"]["daemon_class"] == "TestDaemon"

    def test_limit_parameter_accepted(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """Test that limit parameter accepts valid values."""
        # Test valid limit values within range
        for limit in [1, 20, 50, 100]:
            response = client.get(f"/gaps?daemon=TestDaemon&limit={limit}", headers=auth_headers)
            assert response.status_code == 200  # Should succeed (or 404 if daemon not found)


class TestRateLimiting:
    """Tests for rate limiting."""

    def test_rate_limit_enforced(
        self, client: TestClient, auth_headers: dict[str, str], daemon_state_dir: Path
    ) -> None:
        """Test that rate limiting is enforced."""
        # Create a daemon state file
        state_file = daemon_state_dir / "TestDaemon.json"
        state_file.write_text(json.dumps({
            "last_collection_timestamp": time.time(),
            "records_stored_last_cycle": 100,
        }))

        # Make 61 requests rapidly (rate limit is 60/min)
        responses = []
        for _ in range(61):
            response = client.get("/status", headers=auth_headers)
            responses.append(response)

        # Last request should be rate limited
        assert responses[-1].status_code == 429
        assert "rate limit" in responses[-1].json()["detail"].lower()


class TestCORS:
    """Tests for CORS configuration."""

    def test_cors_middleware_configured(self, daemon_state_dir: Path) -> None:
        """Test that CORS middleware is configured."""
        # Verify that the app has CORS middleware configured
        from starlette.middleware.cors import CORSMiddleware

        app = create_app()

        # Check that CORS middleware is in the middleware stack
        has_cors = any(
            isinstance(middleware, CORSMiddleware)
            or (hasattr(middleware, "cls") and middleware.cls == CORSMiddleware)
            for middleware in app.user_middleware
        )
        assert has_cors, "CORS middleware should be configured"


class TestInputValidation:
    """Tests for input validation and sanitization."""

    def test_daemon_name_prevents_path_traversal(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """Test that daemon name validation prevents path traversal."""
        invalid_names = [
            "../../../etc/passwd",
            "..\\..\\..\\windows\\system32",
            "daemon/../../secrets",
            "daemon; rm -rf /",
        ]

        for invalid_name in invalid_names:
            response = client.get(f"/api-calls?daemon={invalid_name}", headers=auth_headers)
            assert response.status_code == 400

    def test_valid_daemon_names_accepted(
        self, client: TestClient, auth_headers: dict[str, str], daemon_state_dir: Path
    ) -> None:
        """Test that valid daemon names are accepted."""
        valid_names = [
            "WeatherDaemon",
            "TrainPositionDaemon",
            "daemon_with_underscores",
            "daemon-with-hyphens",
            "daemon123",
        ]

        for valid_name in valid_names:
            # Create events file for this daemon
            events_file = daemon_state_dir / f"{valid_name}.events.jsonl"
            events_file.write_text("")

            response = client.get(f"/api-calls?daemon={valid_name}", headers=auth_headers)
            # Should not return 400 (might return 404 if no events, but not 400)
            assert response.status_code != 400


class TestTokenValidation:
    """Tests for token validation on startup."""

    def test_weak_token_warning(self, daemon_state_dir: Path, monkeypatch: pytest.MonkeyPatch, caplog) -> None:
        """Test that weak token triggers warning on startup."""
        monkeypatch.setenv("MONITORING_TOKEN", "short")

        with caplog.at_level("WARNING"):
            _app = create_app()

        # Check that warning was logged
        assert any("weak" in record.message.lower() for record in caplog.records)

    def test_missing_token_error(self, daemon_state_dir: Path, monkeypatch: pytest.MonkeyPatch, caplog) -> None:
        """Test that missing token triggers error on startup."""
        monkeypatch.delenv("MONITORING_TOKEN", raising=False)

        with caplog.at_level("ERROR"):
            _app = create_app()

        # Check that error was logged
        assert any("not set" in record.message.lower() for record in caplog.records)
