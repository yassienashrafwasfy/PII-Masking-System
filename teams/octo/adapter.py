"""Octo's public gateway adapter."""

from __future__ import annotations

from masking_engine import detect, ready, release, supported_model_formats, unload

TEAM_ID = "octo"


def supported_media_types() -> frozenset[str]:
    return frozenset({"application/pdf", "text/plain", "text/csv", "application/json"})


__all__ = [
    "TEAM_ID",
    "detect",
    "ready",
    "release",
    "supported_model_formats",
    "unload",
    "supported_media_types",
]
