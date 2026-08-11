# samtal-server as a container image, in two variants.
#
# `default` bakes in both local engines, because `docker run` with one
# mounted YAML has to be able to serve a whole conversation with no
# account anywhere. `slim` leaves them out, for the mirror-image
# deployment whose ASR and TTS name external providers and which would
# otherwise carry heavyweight, GPL-licensed libraries the server never
# loads. Pick with `--build-arg SAMTAL_VARIANT=slim`.
#
# One Dockerfile rather than two, so the variants cannot drift: they are
# the same server, and the only difference is which optional extras are
# installed.
#
# pysilero-vad stays in both. It is a core dependency, it is light, and
# it runs on every audio frame whichever ASR provider is configured.
#
# Measured on arm64: 883 MB default against 494 MB slim. Most of the
# difference is not piper but faster-whisper, which brings its own
# inference stack rather than reusing the onnxruntime pysilero-vad
# already pulls in.
#
# piper-tts is GPL-3.0, which is why it is an optional extra and why
# only the default variant carries it. Shipping it alongside an MIT
# server in one image is aggregation, not a derived work: the project
# stays MIT, and the image's contents are noted in
# THIRD_PARTY_LICENSES.md. The slim variant contains no GPL component.
#
# Model weights are never baked in, per the repository rule. HOME=/data
# sends every engine's cache (Hugging Face for whisper models, piper's
# own for voices) to the mounted volume, where they download at first
# start and survive a new image.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

# Which variant to build. Defaulted, so an unqualified `docker build`
# still produces the batteries-included image every existing puller of
# `latest` expects.
ARG SAMTAL_VARIANT=default

WORKDIR /app
# Bytecode compiled at build time rather than on every cold start; copy
# rather than hardlink, because the cache and the target are separate
# layers here.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# The extras each variant installs, resolved once and written down where
# both sync steps read it. An unrecognised variant fails the build here:
# a typo that silently produced a slim image would be found by an
# operator whose local engines had vanished, which is much too late.
RUN set -eu; \
    case "$SAMTAL_VARIANT" in \
      default) echo "--extra faster-whisper --extra piper" > /tmp/extras ;; \
      slim) : > /tmp/extras ;; \
      *) echo "unknown SAMTAL_VARIANT '$SAMTAL_VARIANT'; expected default or slim" >&2; \
         exit 1 ;; \
    esac; \
    echo "building the $SAMTAL_VARIANT variant with extras: $(cat /tmp/extras)"

# Dependencies first, from the lockfile alone, so editing the server does
# not re-resolve or re-download a single wheel.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev --no-editable $(cat /tmp/extras)

COPY samtal_server ./samtal_server
COPY README.md ./
RUN uv sync --frozen --no-dev --no-editable $(cat /tmp/extras)


FROM python:3.12-slim-bookworm

# An unprivileged user whose home is the volume, so caches land there and
# a read-only root filesystem is a supported way to run this.
RUN useradd --uid 1000 --home-dir /data --no-create-home samtal \
    && mkdir /data \
    && chown samtal /data

COPY --from=builder /app/.venv /app/.venv

# Which build this is. There is no `.git` in the image and no git to
# read it with, and OCI labels are image metadata a process cannot read
# its own copy of, so the only route in is a build argument becoming an
# environment variable. Defaulted rather than required: an image built
# without one runs and reports `unknown`, which is the honest answer.
ARG SAMTAL_REVISION=unknown

# The domain half of the configuration lives in a SQLite database, and
# SAMTAL_SERVER__DATABASE__DIR is where this image keeps it: on the
# mounted volume, the one place an unprivileged user with a read-only
# root filesystem can write. The model default (/var/lib/samtal) is the
# generic one and is neither created nor writable here.
ENV PATH=/app/.venv/bin:$PATH \
    HOME=/data \
    SAMTAL_CONFIG=/config/config.yaml \
    SAMTAL_SERVER__DATABASE__DIR=/data/db \
    SAMTAL_SERVER__LOG_FORMAT=json \
    SAMTAL_REVISION=$SAMTAL_REVISION

USER samtal
VOLUME /data
EXPOSE 8003

# Assumes the default port; an operator who changes server.port overrides
# this too, which is documented rather than templated.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8003/healthz')"]

# Exec form, so SIGTERM reaches the server rather than a shell: it is
# what lets the shutdown drain run at all. Give `docker stop` a -t above
# server.drain_s.
ENTRYPOINT ["samtal-server"]
