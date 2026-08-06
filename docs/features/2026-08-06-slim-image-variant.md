# Publish a slim image variant for external-provider deployments

## Problem

Issue #32: the published server image bundles the local engines (the
`faster-whisper` and `piper` extras), which is the right default for the
zero-cloud deployment and the wrong one for its mirror image. An
operator who uses external providers for ASR, TTS and LLM pulls
heavyweight, GPL-licensed libraries the server never loads.

Only worth doing once external ASR and TTS providers exist, which they
now do (#11 closed with OpenAI ASR, OpenAI TTS and ElevenLabs TTS). It
pairs with the egress marking from #30: a slim deployment is the mirror
of the `local_only: true` one.

## Changes

- `SAMTAL_VARIANT` build argument on the Dockerfile, `default` or
  `slim`, resolving to the extras each installs. One Dockerfile rather
  than two, so the variants cannot drift: they are the same server and
  the only difference is which optional extras are present.
- Defaulted to `default`, so an unqualified `docker build` still
  produces the batteries-included image.
- An unrecognised variant fails the build with a message naming what was
  expected. A typo that silently produced a slim image would be found by
  an operator whose local engines had vanished, which is much too late.
- CI builds, checks and publishes both through a matrix. The default
  variant keeps `latest`, the dated tag and `sha-<short>` unsuffixed;
  slim takes `slim`, `<date>-slim` and `sha-<short>-slim`, following the
  convention where the unqualified name is the batteries-included image
  (`python:3.12` against `python:3.12-slim`). Nothing changes for an
  existing puller of `latest`.
- `silero` VAD is in both, being a core dependency rather than an
  optional extra: it is light and runs on every audio frame whichever
  ASR provider is configured, so a slim deployment still segments speech
  locally.

Two things beyond what the issue asked for:

- **The tag step is no longer gated on `main`.** It was, which meant the
  one step that decides what a variant is called was never exercised
  until after a merge. For a change whose entire substance is new tag
  names, that is the worst possible place to discover an expression was
  wrong. Computing tags is free; only the login and the push stay gated.
- **The refusal is matched against its message, not just its exit
  code,** so a refactor that turns the `ProviderError` into a generic
  crash is caught.

## Key parameters

| Name | Where | Default | Meaning |
|---|---|---|---|
| `SAMTAL_VARIANT` | `docker build --build-arg` | `default` | `default` installs both local engine extras; `slim` installs none. Anything else fails the build. |

No runtime configuration surface changes: nothing an operator sets in
YAML, and the server itself is identical in both.

## Measured

| | Size | Contains |
|---|---|---|
| default | 883 MB | both local engines, `piper-tts` (GPL-3.0) |
| **slim** | **494 MB** | neither; no GPL component |

A saving of 389 MB, 44%. The issue expected less, and the Dockerfile's
own comment claimed the engines cost "roughly 100 to 150 MB, since
onnxruntime (the large wheel) is already a core dependency through
pysilero-vad". That reasoning is wrong and the comment is now corrected:
`faster-whisper` brings its own inference stack rather than reusing the
onnxruntime `pysilero-vad` pulls in, which is where most of the
difference lives. Measured on arm64; the ratio should hold on amd64 and
the absolute numbers will not.

## Verification

Docker was available on this machine, so unlike the tag plumbing the
image behaviour was verified locally rather than through CI.

- [x] **The slim image boots with a config whose ASR and TTS name
  external providers.** Healthcheck reached `healthy`, `/healthz`
  returned `{"status":"ok","version":"0.1.0","revision":"unknown"}`.
- [x] **The slim image fails at boot with a clear error when the config
  names `piper` or `faster_whisper`.** Exit code 1 and the message
  `providers.asr.whisper: type "faster_whisper" needs the
  faster-whisper extra; install it with: uv sync --extra
  faster-whisper`. The same config boots healthy on the default image,
  so the refusal is the variant's doing and not the config's.
- [x] **The extras are present in default and absent in slim,** checked
  by import in both images; `pysilero_vad` imports in both.
- [x] **An unrecognised variant fails the build,** not silently
  producing the smaller image.
- [x] **The whole smoke conversation runs against the slim image:**
  5 passed, 1 skipped, the skip being the revision check that needs a
  CI-set variable.
- [x] `uv run pytest tests/unit -q`: 658 passed, 2 skipped;
  `tests/integration`: 27 passed; `ruff check` clean.
- [x] **CI builds and boot-checks both variants.** Confirmed on the
  merge to main: `test`, `image (default, latest)` and
  `image (slim, slim, -slim)` all green, the two image jobs starting at
  the same second and so running in parallel.
- [x] **The README's image-choice note matches the tags actually
  pushed.** The first publish from main produced exactly:

  ```
  ghcr.io/rafacm/samtal-server:latest
  ghcr.io/rafacm/samtal-server:2026-08-06-1110
  ghcr.io/rafacm/samtal-server:sha-9fd3de5
  ghcr.io/rafacm/samtal-server:slim
  ghcr.io/rafacm/samtal-server:2026-08-06-1107-slim
  ghcr.io/rafacm/samtal-server:sha-9fd3de5-slim
  ```

  All six resolve in the registry, each for `linux/amd64` and
  `linux/arm64` with distinct digests per variant.
- [x] **The published slim image really is slim.** Pulled back from
  GHCR rather than inferred from the build: `faster_whisper` absent,
  `piper` absent, `pysilero_vad` present. This is the whole chain, from
  build argument through the matrix to what an operator would actually
  run.
- [x] **The amd64 half of the matrix,** which local builds could not
  cover, is green on the same run.

Two things the run itself taught, both now in the README:

- **The dated tags do not pair.** The variants are built by separate
  jobs finishing minutes apart, so one commit produced
  `2026-08-06-1110` and `2026-08-06-1107-slim`. Accurate, since they
  really were built three minutes apart, and misleading to anyone
  pairing variants by date during a rollback. `sha-<short>` matches
  across both and is the handle to use. Documented rather than papered
  over with a job to share one timestamp, which would make two honest
  numbers look identical.
- **The variants were sharing one build cache.** Both wrote
  `type=gha` with no scope, which is a single namespace, so each run
  they overwrote each other's manifests. A problem this change created
  rather than one that predates it; now scoped per variant. Not a
  correctness risk, since BuildKit's cache is content addressed, but a
  hit-rate one. Reasoned rather than measured: showing it needs two
  runs after the scopes exist, because the first pays a one-time full
  miss.

## A gap this creates

The refusal message says `install it with: uv sync --extra
faster-whisper`. That is right for a source checkout and misleading in a
container, where the answer is not to install anything but to pull the
default variant. Left as it is rather than made container-aware, which
would mean the error path guessing where it is running; the README's
image-choice note says so instead, immediately under the message.

## Files modified

- `samtal-server/Dockerfile`
- `.github/workflows/samtal-server.yml`
- `samtal-server/tests/smoke/config.slim.yaml` (new)
- `samtal-server/tests/smoke/config.local-engines.yaml` (new)
- `samtal-server/tests/unit/test_config.py`
- `samtal-server/README.md`
- `samtal-server/config.deploy.example.yaml`
- `THIRD_PARTY_LICENSES.md`
- `CHANGELOG.md`
