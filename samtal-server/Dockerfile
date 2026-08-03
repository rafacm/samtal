# samtal-server as a container image.
#
# Both local engines are baked in, because `docker run` with one mounted
# YAML has to serve a conversation and piper is the only real TTS today.
# They cost roughly 100 to 150 MB, since onnxruntime (the large wheel) is
# already a core dependency through pysilero-vad.
#
# piper-tts is GPL-3.0. Shipping it alongside an MIT server in one image
# is aggregation, not a derived work: the project stays MIT, and the
# image's contents are noted in THIRD_PARTY_LICENSES.md.
#
# Model weights are never baked in, per the repository rule. HOME=/data
# sends every engine's cache (Hugging Face for whisper models, piper's
# own for voices) to the mounted volume, where they download at first
# start and survive a new image.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app
# Bytecode compiled at build time rather than on every cold start; copy
# rather than hardlink, because the cache and the target are separate
# layers here.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Dependencies first, from the lockfile alone, so editing the server does
# not re-resolve or re-download a single wheel.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev --no-editable \
    --extra faster-whisper --extra piper

COPY samtal_server ./samtal_server
COPY README.md ./
RUN uv sync --frozen --no-dev --no-editable \
    --extra faster-whisper --extra piper


FROM python:3.12-slim-bookworm

# An unprivileged user whose home is the volume, so caches land there and
# a read-only root filesystem is a supported way to run this.
RUN useradd --uid 1000 --home-dir /data --no-create-home samtal \
    && mkdir /data \
    && chown samtal /data

COPY --from=builder /app/.venv /app/.venv

ENV PATH=/app/.venv/bin:$PATH \
    HOME=/data \
    SAMTAL_CONFIG=/config/config.yaml \
    SAMTAL_SERVER__LOG_FORMAT=json

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
