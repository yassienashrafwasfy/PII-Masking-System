FROM python:3.12-slim@sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a

ARG GIT_SHA=unknown

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GIT_SHA=$GIT_SHA

WORKDIR /app
# BuildKit's registry cache import can replay this RUN's old result verbatim
# whenever its command text is unchanged, serving packages as stale as the
# last time this exact line built from scratch. Naming every currently-flagged
# package (util-linux CVE-2026-53612/53613/53614, openssl CVE-2026-14456,
# CVE-2026-41992, CVE-2026-86145/89161, CVE-2026-11822/11824, and the
# perl-base CVEs fixed in 5.40.1-6+deb13u1) changes that text, forcing a real
# re-run instead of silently dropping packages from the upgrade set.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install --only-upgrade --yes util-linux openssl gzip libpcre2-8-0 libsqlite3-0 perl-base \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app app
COPY platform/masking-gateway/requirements-runtime.txt /tmp/requirements-runtime.txt
RUN pip install --no-cache-dir --requirement /tmp/requirements-runtime.txt \
    && rm /tmp/requirements-runtime.txt
COPY --chown=app:app platform/masking-gateway/gunicorn.conf.py ./gunicorn.conf.py
COPY --chown=app:app platform/masking-gateway/src/gateway.py platform/masking-gateway/src/server.py ./
COPY --chown=app:app platform/masking-gateway/src/masking_engine ./masking_engine/
COPY --chown=app:app platform/common ./common/

USER app
EXPOSE 8080
CMD ["gunicorn", "--config", "/app/gunicorn.conf.py", "gateway:application"]
