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
itself imports nothing new from `onboarding`.

This route is a credential-bearing read, and the plan treats it as
one rather than as prose about error messages. The onboarding URL's
key segment is derived from the device-auth secret and stands in
front of the token issuer, which is exactly why the startup banner
deliberately does not print it; serving it here is the issue's
decision, and it is the second recorded exception (after `ota-url`)
to the cli-guide's "a credential never travels in a read" practice,
recorded there as such. The design that makes the exception safe:
the route sits behind the same bearer gate as every secret write
already does (the API token grants everything, secret writes
included, so this read widens nothing the token does not already
hold); the response carries `Cache-Control: no-store`; the CLI
renders the URL to stdout only, never to stderr, and the value
appears in no log record, no refusal sentence, and no
`__cause__`/`__context__` chain, with the existing quieted request
loggers covering the transport's own logging. The API description
prose (`api_descriptions/api.md`) gains a sentence saying the
runtime info read carries the onboarding URL and what protects it.
The runtime string is rendered on its own line, unbroken, so a
terminal-width concern never truncates a URL an operator will type. The URL is the retained
form (`public_origin` plus the derived path), same value and provenance
the startup banner and `ota-url` reason about, so a deployment names
itself identically wherever it is named. The operator's API request
does carry a Host header, but the address an operator dials the API on
is not evidence of the address a device can reach, so `info` does not
prefer it; #340 changes the device-facing GET, not this.

**One request or two.** `info` renders identity (the new route) and a
short entities summary (a count line per kind plus `default_agent`,
not the full `list` tree). Before either, it prints the address this
CLI actually contacted, on its own labelled line, because the API
address and the device-facing onboarding URL can legitimately differ
and "what server am I talking to" is the goal sentence. The line uses
the client's existing sanitized display form (`Address.shown`, which
strips secret-shaped query parameters), never the raw `--api-url`,
and a query-token test proves a credential-shaped parameter is absent
from the rendered line. The summary comes from the existing
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
`tools/publish.py` and two test files, and the living cli-guide
lines that name flat `status` (`:374`, `:622-626`). Two corrections
against the first cut of this plan: the `_runtime` docstring's
collision rationale is about the API namespace, which the CLI respell
does not change, so it stays current as written (extended to mention
the info route if that reads naturally); and `cli-guide-audit.md` is
a dated record whose rows are not edited into agreement with later
changes, so audit row 25 is left alone. Historical records
(CHANGELOG, docs/plans/, the cli-guide's pre-#223 counterexample at
`:216`) are not respelled. The command-spellings manifest is
regenerated in the same commit as the last doc edit.

**How `apply --no-reload` gates the second act.** `apply` becomes a
two-act row (`APPLY`, then `RELOAD`), and the selection is
invocation-aware by design rather than by tuple surgery: `Command`
gains an explicit act-selection hook (a callable from invocation to
the acts to run, defaulting to the static tuple), and `Command.acts()`
keeps answering the full static set, because the API contract test
enumerates coverage from it and coverage is about what the row can
reach, not what one invocation ran. The render is likewise
invocation-aware: the apply act renders through a quiet renderer when
the reload act follows (no per-entity `RELOAD_NOTICE`, since the
reload listing that follows says what applied) and through the
staging renderer under `--no-reload` (notices kept). This is a small
`Command`/`Act` interface change and M3 names it as such, with tests
proving both renderers and both request sequences (two requests by
default, one under the flag). `_act` raises out of a failed first
act, so a refused apply never reloads.
A committed apply whose reload act then fails renders the apply
result first and the reload failure after it, and the sentence it
adds claims only what the client actually knows: the write committed,
and this command did not receive a completed reload answer. It does
not claim the running state, because that state is unknowable from
here: a 409 means another reload is running and may have re-read the
store before or after this commit, and a transport failure or timeout
is ambiguous because a reload continues in shielded tasks after its
requester goes away. The sentence directs the operator to
`vinga diff` (which says whether the stored and running worlds agree)
and then `vinga reload` if they do not. Both shapes are tested: a
concurrent 409 from a held reload, and an ambiguous transport failure
mid-reload. Single-entity `set` writes keep their notice
untouched. The apply act keeps its unbounded read timeout and the
reload act its 60 s one, each on its own request.

**Banner punctuation.** The banner is the string the maintainer gave:
`vinga - Conversational AI. Sweded.` with a plain hyphen. The
no-em-dash rule is about em-dashes; no character in the banner is one.

## Design footprint

- `config/api.py` deepens: one new runtime route in `_runtime`,
  answering from `ApiRuntime`, with its OpenAPI presence and its
  refusal descriptions following the `_problems` pattern. The
  response model is a strict `RuntimeInfo` in `config/responses.py`,
  where every model the CLI's acts share already lives so the CLI
  imports no FastAPI. With onboarding off the URL and provenance
  fields are null and the flag says why; on an application built
  without a surrounding server (`build_api()` standalone) the route
  answers the honest 503 the other runtime reads answer, never
  invented identity data. `build_api`/`build_api_runtime` keep their
  existing positional calls working (appended defaulted parameters,
  or internal calls converted to keywords). Tests cover enabled,
  disabled, standalone-503, and exact equality of the served values
  with `onboarding_url()` and `revision()` through the composition
  root.
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
  `status`, the two living lines that name flat `status`),
  `vinga-server/README.md` status invocations and
  the "What the MCP servers are doing" section heading command, the
  root `README.md` lines this issue falsifies: `vinga list` at :98,
  apply/reload at :113-:121, and step 5's
  `docker compose exec vinga vinga-server config ota-url`, which M1
  replaces with `vinga info` the moment the API serves the URL,
  because leaving the container-exec spelling in Getting Started
  would retain the exact remote trap this issue retires (`ota-url`
  keeps its place in offline diagnosis and recovery documentation
  only; the full Getting Started restructure stays #346's). Also
  `docs/reference/cli.md`'s
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
- M2: bare invocation is parameterized over every `GROUPS` path,
  nested groups included, plus the root (replacing the
  `("provider",)` refusal pin); each case asserts exit 1, help on
  stderr only, and an empty `__cause__`/`__context__` chain, because
  raising `SystemExit` while handling Click's exception retains the
  context and the argument list, a leak this CLI has already had
  once; at least one case carries a credential-shaped query in
  `--api-url` and asserts the value reaches no stream and no log
  record in either format; the reworded
  description appears in the regenerated cli.md (drift test carries
  it); the moved row keeps `_status_block` rendering byte-identical
  (rendering tests re-point their argv to `mcp-server status`);
  the grammar completeness pins (`_ORDER`, tree/table agreement,
  refusal rows per family, driven rows) move with the row.
- M3: `apply` runs both acts in order (client-recording fixture);
  `--no-reload` runs one; a failed reload after a committed apply
  renders the committed-but-unanswered sentence; notices quiet under
  the default and present under `--no-reload`; `set` notices
  unchanged. The migration of the existing apply surface is named
  work, not fallout: inventory every apply invocation in the tests by
  grep, convert the tests that exercise storage semantics,
  idempotence, limits, recovery and preset validity to `--no-reload`
  (the unit runner's default `reload=None` runtime would otherwise
  turn every one into a committed write plus exit 1), inject a
  runtime where the default behavior is itself the subject, and recut
  the live-lane sequence so its bootstrap stays a staged apply where
  it asserts staging and one controlled mock-provider document proves
  the default reload end to end. A preset test whose command is no
  longer run verbatim is renamed to say what it now runs.

The no-leak lens on `info` is answered with a keyful sentinel suite,
not a claim: with onboarding on and a derived key present, pin the
successful render (URL on stdout, nowhere else), the 401 for a wrong
bearer (problem body carries no URL), a malformed response body (the
refusal sentence carries no fragment of it), the log records of the
whole invocation in both formats (no key segment anywhere), and the
exception chain on a failure (empty, per the grammar suite's chain
practice). The route's response-header pin covers
`Cache-Control: no-store`.

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

## Plan review round

External review of commit d7e699c2: backend codex (codex-cli
0.149.1), model gpt-5.6-sol, sandbox read-only, 2026-08-29, runtime
about 9 minutes. Verdict: ready after the P1/P2 amendments. Findings
condensed but faithful; resolutions appended per amendment.

1. **P1: `info` exposes a credential-bearing URL without a security
   design.** The onboarding URL's key is derived from the device-auth
   secret, guards the token issuer, and is deliberately kept out of
   retained startup logs; serving it over a read is a new exception to
   "a credential never travels in a read" (cli-guide), not merely to
   error-message quoting. The plan must specify bearer auth,
   TLS-or-loopback transport expectations, `Cache-Control: no-store`,
   stdout-only rendering, exclusion from stderr, logs, refusals and
   exception chains, the credential-practice and API security prose
   updates, and a keyful test over success, unauthorized, malformed
   response, logging and exception-chain retention.

   *Resolution*: adopted. The route is now specified as a
   credential-bearing read: bearer gate rationale, no-store header,
   stdout-only rendering, absence from stderr, logs, refusals and
   exception chains, the cli-guide practice gaining the recorded
   exception, the api.md prose sentence, and the keyful sentinel
   suite over success, unauthorized, malformed response, both log
   formats and the chain. The unbroken-line rendering rule covers
   terminal safety without truncation.

2. **P1: "stored but not applied" is unknowable after several reload
   failures.** A 409 reload-held means another reload may have re-read
   before or after the commit; a transport failure is ambiguous because
   the reload continues in shielded tasks; the client discards response
   status when building `ConfigError`. After any second-act failure the
   command may claim only that the write committed and that it did not
   receive a completed reload answer, directing the operator to
   `vinga diff` then `vinga reload`; test the concurrent 409 and an
   ambiguous transport failure.

   *Resolution*: adopted. The stored-but-not-applied sentence is
   replaced by a claim of exactly what the client knows (committed
   write, no completed reload answer), pointing at `vinga diff` then
   `vinga reload`, with the 409 and transport-ambiguity cases both
   tested.

3. **P1: tuple truncation cannot implement the promised notice
   behavior.** `Act.render` receives only the answer, not the
   invocation or knowledge of later acts, and `Command.acts()` is
   static and consumed by the API contract test. The plan must name an
   invocation-aware act-selection mechanism with distinct quiet and
   staging apply renderers while preserving a static enumeration for
   contract coverage, and include the `Command`/`Act` interface change
   with tests over both renderers and both request sequences.

4. **P2: M3 omits the migration of existing apply tests and live
   sequencing.** The unit runner supplies `reload=None`, so every
   successful apply test becomes a committed write plus exit 1; the
   live suite bootstraps through apply and asserts the agent stays
   unloaded until a later reload; preset tests apply cloud documents a
   default reload would try to build. Inventory every apply call,
   convert storage-semantics tests to `--no-reload`, inject a runtime
   where the default matters, and recut the live sequence so one
   controlled document proves the default end to end.

5. **P2: the runtime-info contract has no named shared model or
   no-runtime behavior.** Response models shared with CLI acts live in
   `config/responses.py` so the CLI need not import FastAPI;
   `build_api()` is routinely constructed without a running server.
   Add a strict `RuntimeInfo` model there, define nullable
   URL/provenance when onboarding is off, answer 503 with no
   surrounding server, preserve builder call compatibility, and test
   enabled, disabled, standalone-503 and exact equality with
   `onboarding_url()` and `revision()` through the composition root.

   *Resolution*: adopted: strict `RuntimeInfo` in
   `config/responses.py`, nullable-with-reason when onboarding is
   off, standalone 503, builder-call compatibility, and the four
   named test cases.

6. **P2: `info` does not show the API endpoint the CLI contacted.**
   The output has only server-returned identity and the device-facing
   origin, which can legitimately differ from the address dialed.
   Render a separate labelled line for the sanitized configuration API
   address using `Address.shown`, never the raw `--api-url`, with a
   query-token test proving credential removal.

7. **P2: two documentation edits are factually wrong.** The
   `_runtime` collision rationale is about the API namespace and is
   not superseded by a CLI respell; `cli-guide-audit.md` declares
   itself a dated record whose rows are not edited into agreement.
   Keep the rationale current (extend it to mention the info route if
   useful) and do not edit the audit.

8. **P2: Getting Started would retain the remote-`ota-url` trap.**
   The README would still instruct `docker compose exec ...
   config ota-url` after this issue ships the API-served URL. Step 5
   reuses the URL `vinga info` prints, no container exec; `ota-url`
   stays only in offline diagnosis and recovery documentation.

9. **P2: the bare-help tests miss the known exception-chain leak
   path.** Existing help tests require an empty exception chain
   because raising `SystemExit` while handling Click's exception
   retains context and argument list, a bug the Typer implementation
   already had once. Parameterize bare invocation over every `GROUPS`
   path including nested groups; at least one case carries a
   credential-shaped query in `--api-url`; each asserts exit 1, help
   only on stderr, no logs containing the value, and empty
   `__cause__`/`__context__`.

10. **P2: no test enforces the removal of flat `status`.** Inventory
    completeness would accept a retained alias as another row. Add a
    negative grammar pin: `("status",)` absent from `COMMANDS` and the
    root tree, `vinga status` answers the fixed unknown-command
    refusal, `vinga mcp-server status` succeeds.

11. **P2: the required changelog entry is absent.** Add dated Added
    and Changed entries for `info`, the status relocation, bare help,
    and default-reloading `apply`; old entries unchanged.
