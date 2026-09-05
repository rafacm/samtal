# Upstream drift watch: implementation notes

Companion to
[the plan](2026-09-05-upstream-drift-watch.md), one section per
milestone, appended in the change that ticks the milestone there.

## M1: the manifest, the check, and the drift workflow

### The prerequisite: resolving the server-side watched paths

The plan made resolving the server repository's paths an explicit M1
prerequisite rather than a guess. Both upstream repositories were
cloned bloblessly into a scratch directory outside the checkout and
listed at the pinned commits with `git ls-tree -r`; nothing was taken
from memory and nothing was cloned into the worktree.

**xinnan-tech/xiaozhi-esp32-server at
`de45f73efdd24e9343427a56b5d22f857b6bb7a7`.** Four paths, all present
at the pin:

| Resolved path | How it was identified |
| --- | --- |
| `main/manager-api/src/main/java/xiaozhi/modules/device/controller/OTAController.java` | The tree listing at the pin has exactly one `OTAController.java`. It is the manager-api endpoint the activation ceremony was reconstructed from, which the notes' activation section names by filename. |
| `main/manager-api/src/main/java/xiaozhi/modules/device/service/impl/DeviceServiceImpl.java` | Likewise exactly one `DeviceServiceImpl.java` at the pin, the service behind that controller, and the notes' other named server-side source. |
| `main/xiaozhi-server/core/connection.py` | The connection lifecycle. At the pin it reads the `device-id` header (`self.device_id = self.headers.get("device-id", None)`), mints the session id, and builds the server's `hello` from config with `session_id` and `audio_params` attached. That is the server half of the handshake the notes describe. |
| `main/xiaozhi-server/core/handle/helloHandle.py` | The other half: `handleHelloMessage` consumes the device's `hello`, reading `audio_params.format`, the `features` map, and the `mcp` and `aec` flags. |

**The handshake core is two files, not one.** The plan's candidate was
descriptive and singular. At the pin the work is split: the connection
module owns the header read, the session id and the outbound welcome,
and the hello handler owns the inbound message. Watching only one
would miss half the handshake, so both are in the manifest. The
neighbouring `core/handle/textHandler/helloMessageHandler.py` is a
seventeen-line registry adapter that forwards to `handleHelloMessage`
and carries no protocol fact, so it is deliberately not watched;
`core/websocket_server.py` is the server bootstrap, and the only thing
the notes read from it (the `ws://host:8000/xiaozhi/v1/` path) sits in
the page's explicitly unmaintained historical section, which the
manifest header's rule excludes.

**78/xiaozhi-esp32 at
`dd99da00dc4c89ed4ab07fcec038c03f13f4de50`.** All four candidate paths
confirmed present at the pin by the same listing: `docs/websocket.md`,
`main/protocols/` (eight files), `main/ota.cc`, `main/application.cc`.

**No candidate was missing at either pin**, so there is no
missing-path finding to record.

### What landed

- `docs/upstream-watch.yaml`, the manifest: per repository the URL, the
  pinned commit, the read date and the paths above, with a header
  carrying the resolution loop, the rule for adding a path and the
  rename caveat.
- `scripts/upstream_watch.py`, the one parser, with `check`, `print`,
  `report` and `decide`.
- `.github/workflows/upstream-drift.yml`, the weekly watch, plus the
  paths-filtered `pull_request` dry run.
- The `check` step in `.github/workflows/docs.yml`.
- `vinga-server/tests/unit/test_upstream_watch.py`, twenty-six tests.
- The currency section's new paragraph in `docs/xiaozhi-notes.md`, and
  the `CHANGELOG.md` entry.

### Deviations from the plan

1. **The manifest's repositories are a YAML list, not a mapping keyed
   by repository.** The plan calls for "keys per repository". A mapping
   would have made the plan's own duplicate-row failure unreachable,
   because a duplicated key is silently dropped by every YAML parser
   before the check could see it. A list keeps the duplicate visible
   and keeps the manifest's own order, which is the report's ordering.
2. **The handshake core resolved to two paths**, as recorded above.
3. **`report` writes a zero-byte file when nothing moved**, rather than
   signalling emptiness through an exit code. The workflow tests the
   file with `[ -s ]`, which keeps "nothing moved" distinct from
   "something failed" without overloading the exit status that
   validation failures use.
4. **The report body is one line per paragraph.** GitHub renders issue
   bodies with the `breaks` extension, so a hand-wrapped paragraph
   arrives with a line break mid-clause. The script's own source stays
   wrapped; only the emitted body is not.
5. **The synthetic clones in the test suite use `--no-local`.** Git's
   local hardlink-or-copy shortcut flaked during development ("failed
   to copy file to .../objects/bc/...: No such file or directory") on
   roughly one run in three. The git transport is both stable and what
   the workflow actually exercises.
6. **`docs.yml` needed no new provisioning.** The plan allowed for
   adding the pinned setup-uv and frozen sync to the docs lane; that
   lane already runs both for the census, so the check is one step
   placed after the existing install.

### Verification

- `uv run pytest tests/unit/test_upstream_watch.py -q`: 26 passed, run
  three times serially and twice under `-n auto --dist loadfile`.
- The whole unit lane, serially and the way CI runs it.
- `python3 scripts/check_doc_links.py .`: 199 files, 0 failures.
- The agreement check bites in all four directions, proved against
  copies of the manifest and the notes rather than the committed
  files: a mutated commit, a mutated read date, a row missing from
  either side, and a row duplicated on either side each exit 1 naming
  the repository and without echoing the disagreeing value. These are
  tests, not a one-off session run, so they keep biting.
- Ruff does not cover `scripts/`. Its configuration lives in
  `vinga-server/pyproject.toml` and `uv run ruff check .` runs from
  `vinga-server/`, so the repository-root scripts are outside it, as
  `check_doc_links.py` has been all along. The new script was linted
  explicitly under the same rule set
  (`uv run ruff check --select E,F,W,I,UP,B --line-length 100
  ../scripts/upstream_watch.py`) and passes; the test file is inside
  the lane and passes with it.
- The workflow YAML was parsed with `yaml.safe_load` and its triggers,
  permissions, concurrency and step list read back.

### What could not be verified before merge, and one thing that was

**The workflow itself cannot run before it merges.** GitHub accepts
`workflow_dispatch` only for a workflow already on the default branch,
which is precisely why the paths-filtered `pull_request` trigger
exists: the PR's diff touches the workflow, the manifest and the
script, so the PR fires its own dry run. That run is the pre-merge
evidence for clone, resolve, diff and report construction against live
upstream. The issue-management step is the one path no pre-merge run
exercises, because it is gated off for pull requests by design; its
decision logic is covered by the `decide` tests instead, and the `gh`
calls around it are three lines of `case`.

**What was verified locally is the whole report path.** The `report`
subcommand was run against real blobless clones of both upstreams,
fetched with `--tags --filter=blob:none` the way the workflow fetches
them, and it produced a well-formed report. Both repositories have
moved under watched paths since their pins: upstream firmware shows
`main/application.cc` and `main/protocols/protocol.h` changed across
two commits, and the upstream server shows both handshake files
changed across four, one of them literally "send server hello before
MCP initialize". So the first scheduled run after merge is expected to
open the drift issue immediately. That is the feature working, not a
surprise. The same run also exercised the behind-the-pin branch for
real: the server repository's latest qualifying tag, `v0.9.6`, is an
ancestor of the pin, and the report rendered it as a not-diffed line
rather than diffing backwards.
