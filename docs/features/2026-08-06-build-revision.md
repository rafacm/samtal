# Let the server say which build it is running

## Problem

Issue #41: `samtal_server/__init__.py` sets `__version__ = "0.1.0"`,
hardcoded since the package skeleton and never bumped. It is what
`/health` returns, what the OTA endpoint reports, and what the MCP
server identity sends. Every build since M0 has reported the same
string, so nothing a running server said about itself distinguished one
deploy from another.

The real build identity existed, but only outside the process. The
publish workflow tags images with a build timestamp and a short commit
SHA; those tags are applied by `docker/metadata-action`, the Dockerfile
took no build argument, and OCI labels are image metadata that a process
cannot read about itself. The container had no route at all to its own
revision.

It matters now because field sessions on hardware are becoming how
barge-in behaviour gets investigated (#28), and the loop is: record a
session, change the logic, deploy, record another. Without a revision on
each recording, two sessions that behaved differently are
indistinguishable from one code change and two different rooms. That
confound lands on exactly the evidence that is expensive to collect. It
is also what a rollback wants: a pod reporting `0.1.0` cannot be matched
to the image tag that produced it without going and asking the cluster.

## Changes

- New `samtal_server/build_info.py` with a cached `revision()`, resolved
  once per process in three steps, first answer wins: `SAMTAL_REVISION`,
  then `git describe --always --dirty`, then `unknown`. Every failure in
  the git branch is an ordinary None rather than an exception, because
  the common case for it is an image with no `.git` and no git binary.
  The subprocess has a five second timeout, so a wedged git cannot hold
  up a boot.
- The checkout is located from the module's own path, not the process's
  working directory, which is not the server's to assume.
- `__version__` is untouched. The two answer different questions and
  keeping them separate is the point, rather than making one stand in
  for the other.
- Surfaced in three places, as the issue specifies: `/healthz` next to
  `version`; the `session_open` structured event; and the OTA reply,
  under a new top-level `server` key carrying name, version and
  revision. The human GET on the OTA path names it too, since somebody
  checking an endpoint is reachable also wants to know what answered.
- `Dockerfile` gains `ARG SAMTAL_REVISION=unknown` and turns it into an
  `ENV`. Defaulted rather than required: an image built without one runs
  and reports `unknown`.
- CI passes `SAMTAL_REVISION=${{ github.sha }}` to every image build,
  the same commit the `sha-` tag is computed from.

Two additions beyond what the issue asked for, both to close the gap
that the issue's own verification list is mostly unverifiable at a desk:

- The build argument goes to the **CI smoke build**, not only the
  publish step. The route from build argument to `ARG` to `ENV` to what
  the server reports exists only inside a real image, so without this it
  would first be exercised by a publish to main, which is the worst
  place to discover it is wrong.
- A smoke test asserting `/healthz` and the OTA reply both return the
  revision the image was built with, driven by a new
  `SAMTAL_SMOKE_REVISION`. It skips when that is unset, which is how a
  container someone started by hand behaves.

Also fixed in passing: the server README's log event table was missing
`session_idle`, added the same day the event itself was, in #20.

## Key parameters

| Name | Where | Default | Meaning |
|---|---|---|---|
| `SAMTAL_REVISION` | environment, set by the image build | unset | This build's revision. Wins over everything. |
| `SAMTAL_REVISION` | `docker build --build-arg` | `unknown` | What becomes the environment variable above. |
| `SAMTAL_SMOKE_REVISION` | environment, smoke lane only | unset | What the smoke lane expects the server to report. Unset skips the check. |

No configuration file surface changes: this is not something an operator
sets in YAML, it is something a build states about itself.

## Verification

`uv run pytest tests/unit -q`: 607 passed, 2 skipped.
`uv run pytest tests/integration -q`: 27 passed. `ruff check` clean.

Eight unit tests over the resolver alone, covering each source and each
way the git branch can fail: the environment variable winning, a
whitespace-only variable not counting as an answer, a real checkout
describing itself, a dirty tree reporting `-dirty`, a directory that is
not a checkout, git not being installed at all, git timing out, and the
answer being resolved exactly once across repeated calls. The checkout
cases run against a throwaway repository built in `tmp_path`, so they
test the resolver rather than this repository's history.

Against the issue's acceptance list:

- [x] **A server run from a working tree reports a `git describe` value,
  and a dirty tree says so.** Verified by actually running the server,
  not only in tests. All three sources, against a real process:

  | tree | `git describe --always --dirty` | what `/healthz` returned |
  |---|---|---|
  | uncommitted changes present | `1272747-dirty` | `{"status":"ok","version":"0.1.0","revision":"1272747-dirty"}` |
  | committed | `a14dd19` | `{"status":"ok","version":"0.1.0","revision":"a14dd19"}` |
  | `SAMTAL_REVISION=sha-deadbee` set | `a14dd19` (ignored) | `{"status":"ok","version":"0.1.0","revision":"sha-deadbee"}` |

  Note the format differs by source: `git describe` is short, while CI
  passes the full 40-character SHA, so a deployed pod reports 40
  characters against a `sha-<short>` image tag. Matching them is a
  prefix check. Found in the field by the infra team scripting it as
  equality, which is the natural reading of what the docs said.

  The OTA GET on the dirty run read `samtal-server 0.1.0 (revision
  1272747-dirty) OTA endpoint.`
- [x] **A `session_open` event carries the revision.** Unit test with the
  environment variable set, asserting the logged field.
- [x] **A build with no build argument reports `unknown` rather than
  failing to start.** Unit tests for both shapes of it: no checkout, and
  no git binary.
- [ ] **`/healthz` reports a revision matching the deployed image's
  `sha-` tag.** Not verified here. Docker is not available on this
  machine, so no image was built locally, and matching a *deployed*
  tag needs a deploy. The CI image lane on this change does exercise
  the whole path (build argument through to what the server reports)
  via the new smoke test, so what remains genuinely unverified is only
  the last hop: that the tag applied by `docker/metadata-action` and
  the build argument passed alongside it are the same commit. They read
  from the same `github.sha`.

## Files modified

- `samtal-server/samtal_server/build_info.py` (new)
- `samtal-server/samtal_server/app.py`
- `samtal-server/samtal_server/ota.py`
- `samtal-server/samtal_server/session.py`
- `samtal-server/Dockerfile`
- `samtal-server/tests/unit/test_build_info.py` (new)
- `samtal-server/tests/unit/test_health.py`
- `samtal-server/tests/unit/test_ota.py`
- `samtal-server/tests/unit/test_session_events.py`
- `samtal-server/tests/smoke/test_smoke.py`
- `samtal-server/README.md`
- `.github/workflows/samtal-server.yml`
- `CHANGELOG.md`
