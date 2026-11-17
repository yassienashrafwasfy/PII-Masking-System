"""Synthify's public gateway adapter."""

from __future__ import annotations

from .runtime import (
    detect,
    ready,
    release,
    supported_media_types,
    supported_model_formats,
    unload,
)

TEAM_ID = "synthify"
__all__ = [
    "TEAM_ID",
    "detect",
    "ready",
    "release",
    "unload",
    "supported_media_types",
    "supported_model_formats",
]
