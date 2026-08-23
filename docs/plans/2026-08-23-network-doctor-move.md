# Move the network doctor out of config/cli.py

Issue #244, from the 2026-08-22 complexity audit (#246). Deviations,
resolutions and discoveries land in the companion
`2026-08-23-network-doctor-move-implementation.md`, one section per
milestone, appended in the change that ticks the milestone.

## Goal

Give the endpoint doctor its own module behind its own top-level
command, `vinga-server doctor [URL]`, and leave `config/cli.py` to
configuration commands. The diagnosis itself does not change: the same
four verdicts, the same probe discipline, the same no-leak sentences.
What changes is where the code lives and how the command is spelled.

## The issue's decision, restated

About 400 lines of `config/cli.py` are an HTTP and WebSocket endpoint
diagnostic that lives there only because it reads `ServerConfig`. It
gets its own module and subcommand; no behavior change to the
diagnosis; `config/cli.py` shrinks to configuration commands.

The issue's function census is the audit's, taken at `9acb6bbf`.
Re-censused at `9b91582d`:

- `_canonical_slash` no longer exists; #225 replaced the
  slash-following logic with the refuse-all-redirects rule, and
  `_redirect_refused` is its successor in the cluster.
- `_ota_url` is in the audit's list but is the `ota-url` command, not
  doctor machinery. It stays in the config CLI: three event catalog
  messages (`docs/reference/events.md`) pin the spelling
  `vinga-server config ota-url`, so moving it would change a committed
  artifact and reword shipped events for no audit payoff. What the two
  commands genuinely share, the onboarding URL derivation, moves to
  the one home it should have had (decision 3).
- Today's doctor cluster, verified by grep at `9b91582d`: `_doctor`,
  `_device_url`, `_probed`, `_redirect_refused`, `_describe`,
  `_not_vinga_server`, `_plain_websocket`, `_reported_websocket`,
  `_unreadable_websocket`, plus the constants `SUPPLIED_ENDPOINT`,
  `PARSED_BODY_LENGTH`, `DESCRIBE_FIRST_LINE`,
  `DESCRIBE_WEBSOCKET_LINE`, `ONBOARDING_OFF_FOR_DOCTOR`, and the
  doctor's `check = commands.add_parser("doctor", ...)` block.

## Decisions

### 1. A top-level command, relocating without an alias

`vinga-server doctor [URL]` joins `config`, `conversations` and
`events` as a fourth word-dispatched command group in `main.py`,
imported lazily like the others. `vinga-server config doctor` is
removed, with no alias: the pre-release stance (no third-party
installs to support, boards resettable) prices the rename at zero,
and an alias would be a second spelling to document, test and later
retire. After the removal, `config doctor` answers the config
grammar's ordinary invalid-choice sentence through its existing
ConfigError boundary; nothing new is needed for that.

The dispatch itself gets a no-echo boundary, because the doctor is
the first command whose ordinary argument can be a secret. Today an
unrecognized first word falls through to the root argparse parser,
whose errors echo what was typed, so
`vinga-server docter https://host/<secret>/` would print the secret
URL on stderr. `main.py` therefore stops falling through: after the
word checks, an `argv[1]` that does not start with `-` is an unknown
command word and answers a fixed sentence naming the known words
(`config`, `conversations`, `events`, `doctor`) without repeating
what was typed, exit 2; and the root parser for the remaining
server-argument shapes gets the conversations-style error override
(fixed sentences, never argparse's), so no shape echoes. Entry-point
tests drive `main()` itself: a misspelled `doctor` followed by a
planted secret-shaped URL asserts the URL appears on no stream, the
`doctor` word asserts successful dispatch into the new module, and
`config doctor` asserts the config grammar's refusal.

The honest behavior deltas, all recorded in `CHANGELOG.md`:

- the command's spelling;
- `ONBOARDING_OFF_FOR_DOCTOR` updates its fix sentence to
  `vinga-server doctor URL`;
- the doctor's usage errors now come from its own parser (decision 5);
- the entry point's unknown-word and root-parser errors become fixed
  no-echo sentences;
- a shown URL loses its secret-shaped query parameters along with its
  userinfo (decision 4), a display-only strengthening.

Everything else the doctor prints, refuses and probes is
byte-identical.

### 2. The module: `src/vinga_server/doctor.py`

A top-level module beside `main.py`, mirroring the shape of
`conversations/cli.py`: a `main(argv)` whose ConfigError boundary
prints to stderr and returns 1, its own `_Parser`, and the command
body. It owns the whole cluster from the census above.

The one sentence: callers, and the operator, stop having to know that
diagnosing an endpoint has anything to do with the configuration
surface; the module owns the probe, the describe-parse and the four
verdicts, and reaches none of the database machinery. Its
module-level imports are `httpx`, `config.loader` (ConfigError,
`load_file_config`) and `config.printing` (decision 4). The
`onboarding.origin` import is function-local to the no-URL branch,
with a comment saying why: the onboarding package's `__init__`
eagerly imports `unbound`, which imports `device.bindings`, which
imports `config.store` and `vinga_server.db`, so a module-level
import would pull the database machinery into every doctor
invocation, URL argument or not.

The import-weight contract is deliberately narrow: it is a statement
about `import vinga_server.doctor` alone, not about the installed
entry point, which imports `app` and its whole graph before any
dispatch; changing that is not this issue's work. The pin is a
subprocess test asserting that after `import vinga_server.doctor`,
none of `vinga_server.config.cli`, `vinga_server.config.store`,
`vinga_server.config.api` and `vinga_server.db` is in `sys.modules`.
Beside it, a behavioral pin: a doctor probe of a supplied URL, run
with the working directory and config pointing at an empty tmp path,
creates no database file, which is the property the import discipline
exists to keep.

Not `onboarding/doctor.py`: the onboarding package is boot-path
server code whose `__init__` imports every submodule, and a CLI
command does not belong in the server's import graph.

### 3. The onboarding URL derivation moves to `onboarding/origin.py`

`_onboarding_url`, the `ONBOARDING_OFF` template and the
auth-secret-unset refusal move to `onboarding/origin.py` as a public
`onboarding_url(server, fix)`. `origin.py`'s own docstring already
names itself the one place that decides how a deployment names
itself, and it already composes with `keys.onboarding_key`; the
derivation is the third assembly it was built to hold. Both callers
(`_ota_url` in the config CLI, the doctor) already import the module.

Each caller keeps its own fix sentence: `ONBOARDING_OFF_FOR_URL`
stays in `cli.py`, `ONBOARDING_OFF_FOR_DOCTOR` moves to `doctor.py`
with its new spelling. `origin.py` gains one import,
`config.loader.ConfigError`, which introduces no cycle (`loader`
imports nothing from `onboarding`).

`_server_config` is a two-line accessor over `load_file_config`;
each CLI keeps its own copy rather than sharing a pass-through.

### 4. One home for the no-leak text vocabulary: `config/printing.py`

`_parsed`, `_without_userinfo`, `_printable` and `GLIMPSE_LENGTH`
serve both sides after the split: `cli.py`'s transport policy
(`_permitted`) and renderings, and the doctor's `_device_url`,
`_reported_websocket` and verdict line. Neither module may import the
other (the doctor must not pull the config CLI's machinery; the
config CLI importing URL hygiene from the doctor would be backwards),
and two copies of a no-leak rule are exactly the drift the audit's
economy rule spends rigor against.

They move to a new `src/vinga_server/config/printing.py` as
`parsed_url`, `shown_url`, `printable` and `GLIMPSE_LENGTH`.
`parsed_url` and `printable` move byte-identical. `shown_url` is
`_without_userinfo` corrected, not blessed: the old helper kept the
query string, and `_reported_websocket` prints its result, so a far
side reporting `wss://host/ws?token=<secret>` published the secret
on stdout, and the same hole sat under `_permitted`'s refusal
sentences for an operator-typed API URL. The repository already has
the rule that a secret-shaped query parameter is a URL credential
(`config/models.py`, `url_credential` and
`without_url_credential`), so `shown_url` composes both rules: the
host rebuilt from parsed parts as today, the userinfo gone, and the
query filtered through `models.is_secret_option` the way
`without_url_credential` filters it, reusing that predicate rather
than restating it. This is a recorded security behavior delta,
display-only and strengthening, in the changelog beside the others;
its sentinel tests plant `?token=<sentinel>` in a far-side websocket
URL and in an operator-typed API URL and assert the sentinel reaches
no stream.

The one sentence: callers stop having to know how text nobody
vouched for, an operator-typed URL or a far-side string, is kept off
retained surfaces: refusals that quote nothing, a URL shown only
with its credentials (userinfo and secret-shaped query parameters)
taken out, far-side text bounded and made printable. Exposing
`shown_url` as the single display door, rather than separate strip
primitives a caller must order correctly, is what makes the module
the rule's home instead of a toolbox beside it. It lives in
`config/` beside the family's failure type (`loader.ConfigError`,
which `parsed_url` raises) and the credential predicate it reuses,
and is light enough for the top-level doctor to import. Two caller
modules from day one (`cli.py`, `doctor.py`), each using it at more
than one surface, so it passes the deletion test.

`PARSED_BODY_LENGTH`, `SUPPLIED_ENDPOINT` and the two DESCRIBE
patterns are doctor-only and move to `doctor.py`.

### 5. The doctor gets its own client seam, and the config seam loses a dead default

`doctor.build_client(url)` is the doctor's own seam: an
`httpx.Client` with no token parameter at all, because the OTA
endpoint is the token issuer and a client that cannot carry an
Authorization header cannot leak one to whatever answers at a
device-facing address. Its connect and read bounds keep the values
the shared client used (5 s connect, 30 s read), restated as the
doctor's own constants with the doctor's own reason (a bounded
connect and a generous read for one GET of a static description);
they are no longer tied to the API's database-busy margin, which was
never the doctor's reason.

`cli.build_client` becomes API-only: its docstring drops the OTA
half, and its `token: str | None = None` default goes, because after
the move every production caller passes the resolved token and a
seam's untaken branch is the kind the audit told us to delete. The
acceptance suites' replacement factories change signature in step.

Per the honest-seams lens, each seam's default-construction policy
gets its own pin: the existing construction pins in
`test_config_cli_onboarding.py` split into the doctor seam's pin (no
auth header ever, its timeouts) in the doctor's suite and the config
seam's pin (bearer header, its timeouts) in the transport suite.

### 6. The doctor's usage sentences are fixed, conversations-style

The config grammar's `_usage_problem` passes argparse's own words
through for all but the unrecognized-arguments shape. The doctor
cannot afford that: the thing an operator mistypes at this command is
a URL, and an OTA URL can be the deployment's own secret, so an
argparse sentence that quotes the argument (`invalid choice`,
`unrecognized arguments: <url>`) is a leak. The doctor's `_Parser`
uses the `conversations/cli.py` pattern: a marker-matched table of
fixed sentences and a deliberately vague fallback, none of which ever
contain argparse's text. This is the one place the move strengthens a
surface rather than preserving it, and it is why losing the config
grammar's shared boundary costs nothing.

Grammar: `vinga-server doctor [URL] [--config PATH]`. No
subcommands, no `--api-url`, no `--local`, because the doctor reaches
no API and no database.

## Module layout after the change

- `src/vinga_server/doctor.py`: new; `main`, `_Parser`, the command
  body and the census's nine functions and doctor-only constants; its
  own `build_client` seam and timeout constants.
- `src/vinga_server/config/printing.py`: new; `parsed_url`,
  `shown_url`, `printable`, `GLIMPSE_LENGTH`; all but `shown_url`
  moved byte-identical from `cli.py`, `shown_url` per decision 4.
- `src/vinga_server/onboarding/origin.py`: gains `onboarding_url`,
  `ONBOARDING_OFF` and the auth-unset refusal, moved from `cli.py`;
  gains the `ConfigError` import; docstring's command spellings
  updated.
- `src/vinga_server/config/cli.py`: loses the doctor cluster, the
  moved shared helpers and the doctor's parser block; `_ota_url` now
  calls `origin.onboarding_url`; `build_client` tightens per decision
  5; module docstring's "two commands stand outside" prose becomes
  one command.
- `src/vinga_server/main.py`: `DOCTOR_COMMAND` word dispatch, lazy
  import, beside the three existing groups.
- `README.md` (vinga-server), repository `README.md`,
  `config.example.yaml`: command spelling updates. `CHANGELOG.md`
  under `## 2026-08-23`, `### Changed`.

## Tests

The 1,061-line `tests/unit/test_config_cli_onboarding.py` is the pin,
committed green before the move. Its doctor suites move to a new
`tests/unit/test_doctor.py` driving `doctor.main()` directly; the
`ota-url` suites stay, and the file docstring narrows to one command.
The mechanical rule for the whole test diff, in the spirit of #264's
wording retreat: entry-point call sites (`cli.main(["doctor", ...])`
becomes `doctor.main([...])`), monkeypatch targets
(`cli.build_client` becomes `doctor.build_client`), import paths of
moved names, and the single assertion pinning the
`ONBOARDING_OFF_FOR_DOCTOR` spelling (line 623). Assertion bodies are
otherwise byte-unchanged, sentinel assertions (the PASTED far-side
credential, the secret-URL non-echo checks) included; the
real-describe-handler case moves with the doctor file so a change to
the OTA handler's prose still cannot pass unnoticed.

`tests/unit/test_config_cli_rendering.py` retargets its `_printable`
and `GLIMPSE_LENGTH` imports to `config.printing`'s public names. The
support runner (`tests/support/config_cli.py`) is untouched; the
doctor no longer runs through it. Usage-error coverage for the new
parser asserts the fixed sentences and, for the unrecognized-argument
shape, plants a secret-shaped URL and asserts it does not appear on
stderr.

New pins, per decision 5: one for each client seam's construction
policy. A new import-weight test pins decision 2 by asserting that
importing `vinga_server.doctor` leaves `vinga_server.config.cli`,
`vinga_server.config.store`, `vinga_server.config.api` and
`vinga_server.db` out of `sys.modules` (run in a subprocess so the
unit lane's own imports cannot mask it), and the behavioral pin
beside it asserts a probe creates no database file.

## Verification

All from `vinga-server/`: `uv run ruff check .`,
`uv run pytest tests/unit -q`, `uv run pytest tests/integration -q`,
and the generated-document drift checks. The committed artifacts
(`docs/reference/api-openapi.json`, the config reference,
`docs/reference/events.md`) are expected byte-identical, which the
existing drift checks assert. `config schema` has no drift check (CI
never runs it; the consolidation plan left it manually verified), so
it is pinned by hand: capture `uv run vinga-server config schema` on
the base commit before the move, byte-diff the post-move output
against the capture, and record the command and result in the
implementation doc.

Inventories by tooling: `grep -n` for each censused name in
`config/cli.py` must come back empty after the move;
`grep -rn "config doctor"` across the repository (vendor excluded)
must return only `CHANGELOG.md` history and this plan's family;
counts refreshed after any rebase.

## Risks

- **A missed spelling site.** `config doctor` appears in two READMEs,
  the example YAML, one test assertion and `origin.py`'s docstring;
  the grep above is the guard, run at review time, not from memory.
- **Fixture entanglement in the test split.** The onboarding test
  file shares its environment fixture and vector constants across
  both commands' suites; the split duplicates the small fixture
  rather than growing `tests/support`, and the implementation doc
  records what was duplicated and why.
- **Import weight regression.** Someone later adds a convenience
  import and the doctor quietly pulls the database machinery; the
  subprocess import test turns that into a red test instead of a slow
  command.

## Milestones

- [ ] **M1: the doctor moves whole.**
  One milestone, because every piece above is one rebase-safe move
  whose halves are not independently releasable: the module, the
  dispatch, the derivation and printing homes, the seam split, the
  test split, the docs and the changelog. Design footprint: adds
  `doctor.py` (the diagnosis stops being a configuration concern) and
  `config/printing.py` (one home for the no-leak text vocabulary);
  deepens `onboarding/origin.py` (the URL derivation joins the module
  that already decides how a deployment names itself) and
  `config/cli.py` (which stops carrying a second product); `main.py`
  gains one dispatch line pair. `main` stays releasable at the
  milestone boundary by construction, since there is only one.

## Plan review round

External review of commit `dda0fe17`, 2026-08-23. Backend: codex
CLI 0.149.0, model `gpt-5.6-sol`, read-only sandbox; the first
codex round since the quota reset (every prior round of the #246
batch ran on the claude fallback). Runtime 8m28s. Verdict as
received: ready after the P1/P2 amendments. Findings condensed but
faithful:

1. **P1: a misspelled top-level command bypasses the doctor's
   no-leak parser.** `main.py` word-dispatches exact matches and
   falls through to plain argparse, which echoes unrecognized
   arguments, so `vinga-server docter https://host/<secret>/`
   prints the secret URL on stderr. The planned tests drive
   `doctor.main()` directly and never cross this path.

   *Resolution* (this commit): decision 1 gains the no-echo dispatch
   boundary: unknown first words answer a fixed sentence naming the
   known commands, the root parser's errors become fixed
   conversations-style sentences, entry-point tests plant a
   secret-shaped URL behind a misspelled word and assert absence,
   and the delta joins the changelog list.

2. **P1: the import-weight contract contradicts the eager import
   graph.** `onboarding/__init__.py` eagerly imports `unbound`,
   which imports `device.bindings`, which imports `config.store`
   and `vinga_server.db`, so a doctor that imports
   `onboarding.origin` at module level cannot keep those out of
   `sys.modules`. The installed entry point also imports `app`
   before dispatch, so the claim must not read as a statement about
   deployed startup weight. The planned test also omitted
   `config.api`, which the contract names.

   *Resolution* (this commit): the `onboarding.origin` import
   becomes function-local to the no-URL branch with the import-graph
   reason in a comment; the contract is restated as being about
   `import vinga_server.doctor` alone, with the entry point's eager
   graph named as out of scope; the subprocess test asserts all four
   forbidden modules; and a behavioral pin asserts a probe creates
   no database file.

3. **P1: the proposed no-leak module preserves a query-credential
   leak.** `_without_userinfo` keeps the query string and
   `_reported_websocket` prints its result, so a far side reporting
   `wss://host/ws?token=<secret>` publishes the secret on stdout.
   `config/models.py` already recognizes secret-shaped query
   parameters as URL credentials (`url_credential`,
   `without_url_credential`); moving the narrower helper
   byte-identically would bless the weaker rule as the one home,
   and the doctor suite tests userinfo credentials but no query
   credential.

   *Resolution* (this commit): the display helper becomes
   `shown_url`, which strips userinfo and filters secret-shaped
   query parameters through the predicate `config/models.py` already
   owns; the fix is recorded as a display-only security delta in the
   changelog list; sentinel tests plant `?token=<sentinel>` in a
   far-side websocket URL and in an operator-typed API URL; and the
   module's interface is the one display door rather than strip
   primitives a caller must order.

4. **P2: the config-schema byte-identity claim has no
   verification.** CI's drift checks cover the references and the
   OpenAPI document but never run `config schema`, and a
   `git diff --stat` cannot see changed command output.

   *Resolution* (this commit): the verification section stops
   claiming CI covers it and pins `config schema` by hand: a capture
   on the base commit, a byte-diff after the move, both recorded in
   the implementation doc.

5. **P3: the mechanical test move carries over a private
   reach-in.** The exception-chain test calls `cli._device_url`
   directly and would keep doing so against `doctor._device_url`;
   the design guide requires every reach-in in a moved-new test
   file to be resolved explicitly.
