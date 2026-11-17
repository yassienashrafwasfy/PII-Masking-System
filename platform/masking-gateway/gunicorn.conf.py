"""Shared production HTTP settings for the gateway and model services."""

from __future__ import annotations

import os


def positive_integer(name: str, default: int) -> int:
    """Read a strictly positive integer from the environment."""
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive integer") from exc
    if value < 1:
        raise RuntimeError(f"{name} must be a positive integer")
    return value


bind = "0.0.0.0:8080"
workers = 1
threads = positive_integer("WEB_THREADS", 8)
worker_class = "gthread"
timeout = positive_integer("REQUEST_TIMEOUT_SECONDS", 6600)
graceful_timeout = 30
keepalive = 5
accesslog = "-"
access_log_format = "%({x-request-id}i)s %(m)s %(U)s %(s)s %(b)s %(L)s"
errorlog = "-"
capture_output = True
