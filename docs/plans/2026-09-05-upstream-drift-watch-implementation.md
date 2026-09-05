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
- `scripts/upstream_watch.py`, the one parser, with `check`, `clone`,
  `report` and `decide`.
- `.github/workflows/upstream-drift.yml`, the weekly watch as two
  jobs, plus the paths-filtered `pull_request` dry run.
- The `check` step in `.github/workflows/docs.yml`.
- `vinga-server/tests/unit/test_upstream_watch.py`, forty-three tests.
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
7. **The subcommand set is `check`, `clone`, `report`, `decide`, not
   `check`, `print`, `report`, `decide`.** The plan named `print`
   because the workflow's shell was to loop over its rows and clone.
   The PR review moved the cloning into the script (see finding 3
   below), after which nothing consumed the rows, so `print` was
   retired rather than left as a subcommand with no caller. The count
   the plan states is unchanged: one parser, four subcommands.
8. **The workflow is two jobs rather than one.** Also from the review
   (finding 1): the plan's single job would have held `issues: write`
   while cloning and diffing upstream code, and a step-scoped
   `GH_TOKEN` does not undo what `actions/checkout` leaves in
   `.git/config`. The read half and the write half are now separate
   jobs with separate permissions.

### Verification

- `uv run pytest tests/unit/test_upstream_watch.py -q`: 43 passed,
  run repeatedly, serially and under `-n auto --dist loadfile`.
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

### PR review round

Backend codex (codex-cli 0.153.0), model `gpt-5.6-sol`, 2026-09-05,
against commit `aec14fb2` of PR #405; the reviewer ran 3m39s. Verdict:
not mergeable, on three P1 and four P2 findings. All seven are fixed,
one commit each, and every fix carries its own tests.

1. **P1: the checkout leaves the token where every later step can read
   it.** `actions/checkout` defaults `persist-credentials` to true, so
   the job's token sits in `.git/config` for the rest of the job,
   including script code a pull request can change. Scoping `GH_TOKEN`
   to one step was a claim the checkout had already broken.

   *Fixed*, both halves. Every checkout passes
   `persist-credentials: false`, and the workflow is two jobs whose
   permissions describe what each can do: `drift` holds `contents: read`
   and runs on all three triggers; `issue` holds `issues: write`, needs
   `drift`, downloads the report as an artifact, and runs only for the
   schedule and a non-dry-run dispatch. Workflow-level `permissions` is
   empty so neither job inherits anything, and a pull request no longer
   starts a write-capable job at all rather than starting one and
   relying on a step condition.

2. **P1: failure paths could echo values or a traceback.** Argparse's
   error path repeats what was typed; undecodable input raised
   `UnicodeDecodeError`; an unwritable output path or a git that could
   not be launched escaped as a traceback whose frames hold the
   documents this script exists not to echo.

   *Fixed.* Every failure leaves through `Refusal`, whose message is
   assembled from literals and validated identifiers only, and `main`
   is the single boundary that prints it. `_FixedMessageParser` answers
   a bad invocation with a usage line of our own. The decode policy is
   stated: our own documents strictly, with a refusal on failure, and
   upstream's bytes with `errors="replace"`, which is safe precisely
   because U+FFFD is not a backtick, a newline, a tab or a `#` and so
   cannot close a fence early or forge a heading, a row or a
   name-status separator. Refusals are raised after their `except` arm
   rather than inside it, the discipline `vinga_server.config.cli`
   states at length: `from None` suppresses the traceback, not the
   chain. Five tests, including a credential-shaped unknown argument
   asserted absent from both streams.

3. **P1: git's stderr went straight into the log.** The clone loop ran
   in the workflow's shell with stderr inherited, so a failure printed
   the URL git was handed and whatever the remote said.

   *Fixed.* A `clone` subcommand does the blobless clone and the
   all-tags fetch with both streams captured, and a failure is a fixed
   sentence naming only the repository. git's environment is narrowed
   so it cannot stop to ask a runner for a password, and every git call
   carries a timeout, because that failure mode is a hang. Tested
   against a credential-shaped host in the reserved `.invalid` domain,
   which fails without a network.

4. **P2: `gh issue list` returns thirty by default.** The
   exactly-one-match discipline held only inside that window.

   *Fixed.* The listing asks for a thousand, with the reason stated
   beside it, and two tests pin the script's half: a match behind forty
   non-candidates is still found, and an ambiguity split across that
   distance still refuses naming both.

5. **P2: git failures read as legitimate states.** A failed
   `tag --list` returned the same `None` as a repository with no
   qualifying tag, and any nonzero `merge-base --is-ancestor` was read
   as non-ancestry and then as divergence, so a corrupt clone became a
   confident claim about upstream's history.

   *Fixed.* Tag enumeration that fails is its own refusal, and ancestry
   goes through one helper accepting only 0 and 1. Both tested against
   synthetic breakage that leaves the rest of git working: an unusable
   `tag.sort` in the clone's config, and a corrupted loose commit object
   in the middle of the history, where the pin and `origin/HEAD` still
   resolve and only the walk between them fails.

6. **P2: the version comparison was not deterministic.** Padding a
   two-part tag to a triple made `v1.2` and `v1.2.0` equal, with
   first-seen winning; leading zeros collided the same way.

   *Fixed.* Leading zeros are outside the accepted syntax, which is
   semver's rule and here a determinism rule. The remaining tie is
   broken explicitly in favour of the three-part spelling, stated in
   place, and the tag text is the last key component so the ordering is
   total. Three tests, including `v0.10.0` over `v0.9.0`.

7. **P2: a multiline URL injected rows into the shell handoff.**
   `startswith("https://")` accepted a value carrying a newline, which
   the line-oriented clone loop then read as two rows, the second
   naming a directory of the manifest's choosing.

   *Fixed*, strongest form. The loop is gone with finding 3, and the
   value is unrepresentable too: `urlsplit` with a scheme allowlist, a
   host that has to be there, and a refusal for any whitespace or
   control character anywhere in the URL, with the derived directory
   name checked for separators and dot segments. `check` additionally
   holds the committed manifest to https, so the `file://` form the
   tests need cannot reach the file a scheduled workflow fetches over
   the internet. `print` is retired, which is recorded as deviation 7
   above.

### What the review round could not verify, and what it did

Everything above is covered by the suite except two things, both named
here rather than implied.

- **`actions/upload-artifact@v5` and `actions/download-artifact@v5` are
  the one version pair in this workflow that no local run exercises.**
  The rest of the workflow's actions are the versions the other two
  workflows already pin. The PR's own dry run exercises the upload; the
  download runs only in the write job, which a pull request never
  starts.
- **The whole read half was re-run against live upstream after the
  fixes**, through the new `clone` subcommand rather than a shell loop:
  both repositories cloned blobless with their tags, and the report
  came back with the same drift as before.

#### Delta re-review

Backend codex (codex-cli 0.153.0), model `gpt-5.6-terra`, sandbox
read-only, 2026-09-05, against commit c06edb0b (the fix round's
head); a few minutes. No delta findings: all seven resolutions
verified in place (the permission split with empty workflow-level
permissions and non-persisted credentials, the fixed-message
refusal boundary, the captured clone streams, the listing limit,
the git-fault distinctions, the total release ordering, and the
strict URL validation with the print handoff retired), the
artifact handoff confirmed conditional on drift, and the issue job
confirmed unreachable from a pull request. Verdict: mergeable as
is.
