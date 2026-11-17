FROM ghcr.io/astral-sh/uv:0.11.29 AS uv
FROM python:3.12-slim@sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a

ARG GIT_SHA=unknown

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app \
    REPOSITORY_ROOT=/app \
    GIT_SHA=$GIT_SHA

WORKDIR /app
COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock platform/masking-gateway/requirements-runtime.txt ./
# BuildKit's registry cache import can replay this RUN's old result verbatim
# whenever its command text is unchanged, serving packages as stale as the
# last time this exact line built from scratch. Naming every currently-flagged
# package (util-linux CVE-2026-53612/53613/53614, openssl CVE-2026-14456,
# CVE-2026-41992, CVE-2026-86145/89161, CVE-2026-11822/11824, and the
# perl-base CVEs fixed in 5.40.1-6+deb13u1) changes that text, forcing a real
# re-run instead of silently dropping packages from the upgrade set.
# Not pinned on purpose: this only upgrades packages the base image already
# ships to the newest patched build, so an exact version would defeat it and
# break the build the day the mirror drops that version.
# hadolint ignore=DL3008
RUN --mount=type=cache,target=/root/.cache/uv \
    apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install --only-upgrade --yes util-linux openssl gzip libpcre2-8-0 libsqlite3-0 perl-base \
    && uv sync --locked --no-dev --no-install-project \
    && uv pip install --python /opt/venv/bin/python --requirement requirements-runtime.txt \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app app \
    && chown -R app:app /app /opt/venv \
    && rm -rf /var/lib/apt/lists/*
COPY --chown=app:app platform/masking-gateway/gunicorn.conf.py ./gunicorn.conf.py
COPY --chown=app:app platform/masking-gateway/src/masking_engine ./masking_engine/
COPY --chown=app:app platform/model-service/src/ ./model-service/
COPY --chown=app:app platform/common ./common/
COPY --chown=app:app teams/ ./teams/

USER app
EXPOSE 8080
CMD ["gunicorn", "--config", "/app/gunicorn.conf.py", "--chdir", "/app/model-service", "service:application"]
