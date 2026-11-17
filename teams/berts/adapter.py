"""Berts' public gateway adapter."""

from __future__ import annotations

from .runtime import (
    detect,
    ready,
    release,
    supported_locales,
    supported_media_types,
    supported_model_formats,
    unload,
)

TEAM_ID = "berts"


__all__ = [
    "TEAM_ID",
    "detect",
    "ready",
    "release",
    "supported_locales",
    "unload",
    "supported_media_types",
    "supported_model_formats",
]
