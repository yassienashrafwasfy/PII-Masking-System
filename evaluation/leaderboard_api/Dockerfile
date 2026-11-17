FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN useradd --create-home --uid 10001 arena
COPY leaderboard_api/requirements.txt ./requirements.txt
# Patch the base image's OS packages in the same layer, so a fixable
# distro CVE does not sit in the published image until the base is rebuilt.
# `apt-get upgrade` alone isn't enough to guarantee that in CI: BuildKit's
# registry cache import can replay this RUN's old result verbatim whenever
# its command text is unchanged, serving packages as stale as the last time
# this exact line built from scratch. Naming the currently-flagged packages
# (CVE-2026-41992, CVE-2026-86145/89161, CVE-2026-11822/11824, and the
# perl-base CVEs fixed in 5.40.1-6+deb13u1) changes that text, forcing a
# real re-run.
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && apt-get install -y --only-upgrade gzip libpcre2-8-0 libsqlite3-0 perl-base \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir --upgrade pip setuptools \
 && pip install --no-cache-dir -r requirements.txt \
# The runtime needs no package manager. Removing pip and ensurepip also removes
# pip's vendored msgpack and the old setuptools inside the bundled ensurepip
# wheel, which are the only remaining flagged CVEs and are not importable by the
# application. Keeps the image without a way to install code at runtime.
 && rm -rf /usr/local/lib/python3.13/site-packages/pip \
           /usr/local/lib/python3.13/site-packages/pip-*.dist-info \
           /usr/local/lib/python3.13/ensurepip \
           /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.13
COPY leaderboard_api ./leaderboard_api
# Synthetic sample documents the masking console offers as one-click uploads.
# Baked in rather than mounted so the picker works on any host: the VPS has no
# equivalent of the local host folder, which is why it previously reported
# samples_available=false. See samples/README.md for what may go here.
COPY samples ./samples
USER arena
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health')" || exit 1
CMD ["uvicorn", "leaderboard_api.main:app", "--host", "0.0.0.0", "--port", "8080"]
