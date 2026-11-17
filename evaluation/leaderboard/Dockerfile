FROM node:24-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:stable-alpine
# CVE-2026-14456 (HIGH) hits libssl3/libcrypto3, and CVE-2026-66046 /
# CVE-2026-76641 (HIGH) hit libexpat, all in this base image's Alpine
# packages; upgrade them explicitly rather than trusting the base tag to
# have picked up the fix already. CVE-2026-53612/53613/53614/76642/78408/
# 78409/78410 (all HIGH) hit libuuid the same way.
RUN apk update && apk upgrade --no-cache libssl3 libcrypto3 libexpat libuuid
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

# Run as the unprivileged `nginx` user (uid 101) rather than root. This is not
# defence in depth for its own sake -- it is what makes the deployment's
# `cap_drop: [ALL]` survivable at all, because a root master process needs two
# capabilities that are dropped there and is wedged between them:
#
#   * It creates /var/cache/nginx/{client,proxy,...}_temp at startup. On a
#     tmpfs pre-owned by uid 101 (which the compose files mount to avoid the
#     chown below), root is neither owner nor group and the directory is 0755,
#     so mkdir needs CAP_DAC_OVERRIDE -- the capability that normally makes
#     root ignore file permissions entirely.
#   * On a root-owned tmpfs mkdir succeeds, but nginx then chowns those
#     directories to the worker user, which needs CAP_CHOWN.
#
# As uid 101 neither applies: nginx skips the chown when it is not root
# (`ngx_create_paths` only chowns a directory whose owner differs), and it
# owns the cache tmpfs outright, so mkdir just works.
#
# The pid file moves to /tmp because /var/run's tmpfs is root-owned; /tmp is
# mounted world-writable and is already a tmpfs in every deployment.
RUN sed -i 's#^pid .*#pid /tmp/nginx.pid;#' /etc/nginx/nginx.conf
USER nginx

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://127.0.0.1:8080/ >/dev/null || exit 1
