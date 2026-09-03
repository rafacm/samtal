# vinga-server as a container image, in two variants.
#
# `default` bakes in both local engines, because a mounted YAML and a
# seeded database have to be able to serve a whole conversation with no
# account anywhere. `slim` leaves them out, for the mirror-image
# deployment whose ASR and TTS name external providers and which would
# otherwise carry heavyweight, GPL-licensed libraries the server never
# loads. Pick with `--build-arg VINGA_VARIANT=slim`.
#
# One Dockerfile rather than two, so the variants cannot drift: they are
# the same server, and the only difference is which optional extras are
# installed.
#
# pysilero-vad stays in both. It is part of the serve extra rather than
# one of the optional engines, it is light, and it runs on every audio
# frame whichever ASR provider is configured.
#
# The serve extra is what makes this an image of a server rather than of
# the configuration client: the package's default install is the client
# half, and both sync steps below name `--extra serve` for both
# variants. That name lives here and in the contributor's dev group and
# nowhere else. An operator runs this image and installs nothing.
#
# The `sim` extra is deliberately not here, and that is a decision rather
# than an oversight. It carries the websocket client `vinga simulator
# run` speaks, which is a thing a person does FROM a workstation TO a
# deployment; an image that carried it would be a server that could
# pretend to be a board talking to itself.
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
ARG VINGA_VARIANT=default

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
    case "$VINGA_VARIANT" in \
      default) echo "--extra faster-whisper --extra piper" > /tmp/extras ;; \
      slim) : > /tmp/extras ;; \
      *) echo "unknown VINGA_VARIANT '$VINGA_VARIANT'; expected default or slim" >&2; \
         exit 1 ;; \
    esac; \
    echo "building the $VINGA_VARIANT variant with extras: $(cat /tmp/extras)"

# Dependencies first, from the lockfile alone, so editing the server does
# not re-resolve or re-download a single wheel.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev --no-editable --extra serve $(cat /tmp/extras)

COPY src ./src
COPY README.md ./
# The wheel force-includes examples/ (the recipe fragments ship inside
# the package), so the build needs them where hatchling looks.
COPY examples ./examples
RUN uv sync --frozen --no-dev --no-editable --extra serve $(cat /tmp/extras)

# The Postgres driver's binary implementation, named here rather than in
# the `serve` extra. That extra carries the plain `psycopg`, so an
# install of it does not drag a bundled libpq in behind it and the
# source-backed install a custom image build may want stays available;
# this image wants the wheel, because it is what makes the runtime stage
# need no libpq of its own. Pinned to the version the lockfile resolved,
# so the image and the lane run the same driver.
RUN uv pip install "psycopg[binary]"


FROM python:3.12-slim-bookworm

# An unprivileged user whose home is the volume, so caches land there and
# a read-only root filesystem is a supported way to run this.
RUN useradd --uid 1000 --home-dir /data --no-create-home vinga \
    && mkdir /data \
    && chown vinga /data

COPY --from=builder /app/.venv /app/.venv

# The entrypoint wrapper, copied while this stage is still root so that
# it lands root-owned and executable. It is three lines and one
# decision, documented in the file: whether there is a configuration
# file to name.
COPY docker-entrypoint.sh /usr/local/bin/vinga-entrypoint

# Which build this is. There is no `.git` in the image and no git to
# read it with, and OCI labels are image metadata a process cannot read
# its own copy of, so the only route in is a build argument becoming an
# environment variable. Defaulted rather than required: an image built
# without one runs and reports `unknown`, which is the honest answer.
ARG VINGA_REVISION=unknown

# Where the state lives is not this image's to say any more. Both halves
# of the configuration are in a Postgres database an operator points the
# VINGA_DB_* variables at, and this image ships no default for them
# beyond the model's own: an image that named a host would be naming
# somebody else's deployment. /data stays for the model caches and for
# anything else an unprivileged user with a read-only root filesystem
# has to write.
#
# There is no wait loop in the entrypoint either. A database that is not
# reachable is a boot that refuses with a fixed sentence, and restarting
# is the orchestrator's job rather than a shell script's.
#
# VINGA_CONFIG is deliberately not here. The server half needs no file,
# and naming one that may not exist made the mount mandatory: a named
# file that is not there is refused, which is right for an operator who
# named it and wrong for an image that named it on their behalf. The
# entrypoint below names /config/config.yaml when it is mounted and says
# nothing when it is not.
#
# VINGA_CONTAINER is this image telling the server where it is running.
# Nothing in a container reliably says so from the inside (/.dockerenv
# is Docker's alone, a cgroup path differs by runtime and by version),
# and the one thing that knows for certain is the image itself, so it
# states it. What it buys is one warning: a provider base_url naming
# localhost means this container rather than the machine hosting it,
# which is a boot that looks clean and a conversation that fails at the
# first round, so the server says so as it builds that provider, with
# host.docker.internal as the likely fix.
ENV PATH=/app/.venv/bin:$PATH \
    HOME=/data \
    VINGA_SERVER__LOG_FORMAT=json \
    VINGA_CONTAINER=1 \
    VINGA_REVISION=$VINGA_REVISION

USER vinga
VOLUME /data
EXPOSE 8003

# Liveness, deliberately, and not readiness. Docker has one health slot
# and it is not a restart trigger: an unhealthy container is surfaced and
# gated on (`--wait`, `depends_on: service_healthy`) rather than
# replaced, so a container that went unhealthy while draining would turn
# every redeploy into a reported failure. An orchestrator with two probe
# slots points restart at /healthz and traffic admission at /readyz; the
# server README says so for whoever is writing that manifest.
#
# Assumes the default port; an operator who changes server.port overrides
# this too, which is documented rather than templated.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8003/healthz')"]

# Exec form, so SIGTERM reaches the server rather than a shell: it is
# what lets the shutdown drain run at all. Give `docker stop` a -t above
# server.drain_s.
#
# The wrapper copied above ends in `exec`, so the server replaces it as
# PID 1 and the signal still arrives; anything after the image name is
# still the server's own command line, so `docker run ... config list`
# reads as it always has.
ENTRYPOINT ["/usr/local/bin/vinga-entrypoint"]
