"""Monitoring server for CTA data collection daemons.

Provides HTTP endpoints for querying daemon status, API call history, and data gaps.
"""

from __future__ import annotations

__all__ = ["create_app"]

from cta_eta.monitoring.server import create_app
