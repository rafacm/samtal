# vinga info, and a CLI polish round

Plan for [#341](https://github.com/rafacm/vinga/issues/341). Implementation
notes land in the companion
`2026-08-29-vinga-info-cli-polish-implementation.md`, one section per
milestone, appended in the change that ticks the milestone here.

## Goal

One read that says what server the CLI is talking to (`vinga info`), a
CLI that answers a bare invocation with its help instead of a scolding,
help text in the user's vocabulary, the MCP status read under its noun,
and `apply` doing what its name promises. All of it fell out of the
2026-08-29 Getting Started walkthrough recorded on the issue.

## The issue's decisions, restated

- `vinga info` exists, over the API, and opens with the banner
  `vinga - Conversational AI. Sweded.` followed by the server version
  and revision hash (a release version joins when releases exist). It
  carries the onboarding URL and a short configured-entities summary.
  The offline file-half `ota-url` command stays for doctor territory.
- Flat `status` moves to `vinga mcp-server status`. No alias
  (pre-release stance).
- Bare `vinga` prints the help; the practice is recorded in
  `docs/architecture/cli-guide.md`.
- The root help's first sentence drops "the domain half" for user
  vocabulary.
- The lean on `apply`: it reloads by default, `--no-reload` stages;
  the plan review weighs the alternative (renaming the storing
  command) before this is implemented in M3.

## Open questions, resolved

**Where the onboarding URL crosses into the API.** No API route serves
the origin today, and `config/api.py` deliberately imports only
`onboarding.pending` (the import-weight pin,
`tests/unit/test_onboarding_import_weight.py`). The URL therefore
reaches the API the way every other runtime fact does: through
`ApiRuntime`, filled by the composition root in `app.py`, which already
sits downstream of `onboarding.origin` via `serving.py`. The API gains
a `GET /api/runtime/info` returning version, revision, the onboarding
URL with its provenance, and whether onboarding is enabled; `api.py`
itself imports nothing new from `onboarding`. The URL is the retained
form (`public_origin` plus the derived path), same value and provenance
the startup banner and `ota-url` reason about, so a deployment names
itself identically wherever it is named. The operator's API request
does carry a Host header, but the address an operator dials the API on
is not evidence of the address a device can reach, so `info` does not
prefer it; #340 changes the device-facing GET, not this.

**One request or two.** `info` renders identity (the new route) and a
short entities summary (a count line per kind plus `default_agent`,
not the full `list` tree). The summary comes from the existing
`GET /api/config` as a second act on the same row; `Command.does`
already takes a tuple and `conversation show` already runs two acts in
order, so no new machinery. A server reachable for act one but failing
act two renders the identity block and then the refusal, which is the
established multi-act behavior.

**Exit and stream for a bare invocation.** Bare `vinga` (and a bare
noun group) prints that page's help to stderr and exits 1. Stdout
stays data-only (the cli-guide's stdout/stderr practice), the exit
code keeps saying the invocation was not a completed command (git's
behavior for a bare `git`), and the reader gets the grammar without
typing a second command. `--help` keeps printing to stdout with exit
0: asking for help is not a failure; arriving without a command still
is, it just stops being unhelpful. The two existing rationale comments
at the `no_args_is_help=False` sites are rewritten to say this, and
the pinned refusal sentence test moves to pin the new behavior.

**What `mcp-server status` touches beyond the row.** The move is a
row relocation in `COMMANDS` (top-level row to `kind="mcp-server"`),
an `_ORDER` update, and the respell sweep the census enumerated:
`vinga-server/README.md` invocations, the `models.py:1727` field
description (which regenerates `domain-config.md` and
`api-openapi.json`), docstrings in `tools/mcp/manager.py`,
`tools/publish.py` and two test files, the `_runtime` docstring in
`api.py` whose collision rationale ("an `mcp_servers` entry may
legally be named `status`") is superseded by the move and is rewritten
to record that history, and the living cli-guide lines that name flat
`status` (`:374`, `:622-626`, audit row 25). Historical records
(CHANGELOG, docs/plans/, the cli-guide's pre-#223 counterexample at
`:216`) are not respelled. The command-spellings manifest is
regenerated in the same commit as the last doc edit.

**How `apply --no-reload` gates the second act.** `apply` becomes a
two-act row (`APPLY`, then `RELOAD`), and the row grows a boolean
option that truncates the act tuple to its first element. `_act`
raises out of a failed first act, so a refused apply never reloads.
A committed apply whose reload then refuses (409 held, 503 no
runtime, snapshot-only server) renders the apply result first and the
reload refusal after it, stating plainly that the write is stored and
`vinga reload` applies it; that sentence is the honest outcome, not a
rollback, because the write is durable and correct. With the default
now reloading, the `RELOAD_NOTICE` lines `apply` used to print per
written entity are suppressed when the reload act runs (the reload
listing that follows says what applied); they still print under
`--no-reload`, and single-entity `set` writes keep their notice
untouched. The apply act keeps its unbounded read timeout and the
reload act its 60 s one, each on its own request.

**Banner punctuation.** The banner is the string the maintainer gave:
`vinga - Conversational AI. Sweded.` with a plain hyphen. The
no-em-dash rule is about em-dashes; no character in the banner is one.

## Design footprint

- `config/api.py` deepens: one new runtime route in `_runtime`,
  answering from `ApiRuntime`, with its `api_descriptions/` page and
  OpenAPI presence. No new module.
- `app.py` (composition root) deepens: it already assembles the
  runtime facts; it adds the identity facts (version, revision,
  onboarding URL callable) to what it hands `build_api_runtime`.
  Callers of the API stop having to know where the origin is derived.
- `config/cli.py` deepens: two new rows (`info`), one moved row
  (`mcp-server status`), one reworded constant, one changed boundary
  behavior (bare invocation), one row gaining an act and an option
  (`apply`). No new module; the grammar tables are the home of all of
  it. The deletion test admits no new layer anywhere in this issue.
- `onboarding/origin.py` is read, not changed (M1 consumes
  `onboarding_url` through the composition root).

## Documentation footprint

- Generated through generators: `docs/reference/cli.md`,
  `docs/reference/api-openapi.json`, `docs/reference/domain-config.md`
  (the `models.py` field description), the command-spellings manifest.
- Hand-maintained, updated in the milestone that falsifies them:
  `docs/architecture/cli-guide.md` (a no-args-help paragraph under
  "One sentence and exit 1", the apply naming under "A write says what
  it did", the flat-system-verbs section gains `info` and loses
  `status`, the two living lines that name flat `status`), its audit
  companion row 25, `vinga-server/README.md` status invocations and
  the "What the MCP servers are doing" section heading command, the
  root `README.md` lines the census listed (`vinga list` at :98,
  apply/reload at :113-:121) only where this issue falsifies them (the
  full Getting Started rewrite is #346), and `docs/reference/cli.md`'s
  hand-written halves where they describe the bare-invocation refusal
  and the command inventory.
- M1's new route needs no board or device guide change; no page under
  `docs/devices/` speaks about the API. The milestone with no
  documentation footprint beyond generators is none of them; each
  section above names its pages.

## Tests

Reuse the existing harnesses: the `runner()` fixture for in-process
CLI tests, `test_cli_live.py` for one live round of `info`, the
contract test (`covered ∪ excluded == document`) which forces the new
route into the committed OpenAPI, and the drift checks for every
generated artifact. New pins, by milestone:

- M1: `info` renders banner, version, revision, onboarding URL with
  provenance, counts and `default_agent` (runner test with injected
  runtime); the route answers 401 without the bearer like its
  neighbors; the multi-act refusal shape (identity up, config read
  refused) renders both parts; the live lane drives `info` once so the
  driven-row completeness pins stay green.
- M2: bare `vinga` and one bare group print help to stderr and exit 1
  (replacing the `("provider",)` refusal pin); the reworded
  description appears in the regenerated cli.md (drift test carries
  it); the moved row keeps `_status_block` rendering byte-identical
  (rendering tests re-point their argv to `mcp-server status`);
  the grammar completeness pins (`_ORDER`, tree/table agreement,
  refusal rows per family, driven rows) move with the row.
- M3: `apply` runs both acts in order (client-recording fixture);
  `--no-reload` runs one; a reload refusal after a committed apply
  renders the stored-not-applied sentence; notices suppressed under
  the default and present under `--no-reload`; `set` notices
  unchanged; the live lane drives `apply` once end to end and asserts
  the reload listing follows.

No new secrets territory: `info` carries no credential (the onboarding
URL's key segment is the one recorded exception to the quoting rule,
exactly as `ota-url` already prints it), and the no-leak lens is
answered by reusing the existing origin values rather than composing
new ones.

## Risks

- The bare-group behavior change flips a pinned refusal; the pin moves
  deliberately in the same commit, and the cli-guide paragraph is the
  record of why. Mitigation: M2 touches the boundary and the pin
  together.
- `apply`'s notice suppression could hide a real staging state if the
  reload act fails silently; mitigated by the explicit
  stored-not-applied sentence and its pin.
- The respell sweep can stale the spellings census mid-stack;
  mitigated by regenerating in the same commit as the last doc edit,
  per the standing rule.
- Stacked PRs over review rounds pay the usual rebase tax; the
  milestones are cut so M1 (new surface) and M2 (grammar polish) touch
  different rows, keeping the M2-on-M1 rebase textual.

## Milestones

- [ ] M1: `vinga info`. The `/api/runtime/info` route fed through
  `ApiRuntime`, the two-act `info` row with banner and counts render,
  OpenAPI description page, generated references, live-lane drive,
  cli-guide's flat-verbs section gains `info`.
- [ ] M2: the polish. Bare invocations print help (boundary change,
  rationale comments, cli-guide practice paragraph), the reworded
  root description, `status` relocated to `mcp-server status` with the
  respell sweep and superseded-rationale rewrites, generated
  references and census regenerated.
- [ ] M3: `apply` reloads by default. The two-act row with
  `--no-reload`, notice suppression, stored-not-applied refusal
  rendering, README and server-README lines this falsifies, cli-guide
  naming rationale.
