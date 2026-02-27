"""Fixtures for testing HTTP responses."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import httpx
import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

os.environ["CTA_API_KEY"] = "test"
os.environ["NWS_APP_NAME"] = "test"
os.environ["NWS_EMAIL"] = "test@example.com"
os.environ["OPENWEATHERMAP_API_KEY"] = "test"
os.environ["CHIDATA_APP_TOK"] = "test"
os.environ["CHIDATA_APP_SECRET"] = "test"  # noqa: S105


@pytest.fixture
def httpx_json_response() -> Callable[[object, int, str], httpx.Response]:
    """Build an httpx.Response with realistic raise_for_status/json behavior."""

    def _build(payload: object, status_code: int, url: str) -> httpx.Response:
        request = httpx.Request("GET", url)
        return httpx.Response(status_code=status_code, json=payload, request=request)

    return _build
