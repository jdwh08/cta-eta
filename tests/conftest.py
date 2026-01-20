"""Fixtures for testing HTTP responses."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

if TYPE_CHECKING:
    from typing import Any
    from collections.abc import Callable


@pytest.fixture
def httpx_json_response() -> Callable[[Any, int, str], httpx.Response]:
    """Build an httpx.Response with realistic raise_for_status/json behavior."""

    def _build(payload: Any, status_code: int, url: str) -> httpx.Response:
        request = httpx.Request("GET", url)
        return httpx.Response(status_code=status_code, json=payload, request=request)

    return _build
