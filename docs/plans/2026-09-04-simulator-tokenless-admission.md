# Admit the simulator where the deployment issues no tokens

Plan for [#369](https://github.com/rafacm/vinga/issues/369).
Implementation notes land in the companion
`2026-09-04-simulator-tokenless-admission-implementation.md`, one
section per milestone, appended in the change that ticks the
milestone here.

## Goal

With `auth.enabled: false`, a `default_agent` set and the store
applied, the server admits the simulated board and says so in its
own log, and the simulator refuses to hold the conversation anyway,
sending the reader in a circle through three messages that
contradict each other. The cause is on the wire: issuing device
tokens is what `auth.enabled` turns off, so an admitted board on
such a deployment is answered with an empty `websocket.token`,
which is byte for byte what a board that was never admitted
receives. The simulator's reading, "no token means not admitted",
was the only reading the reply supported. This plan makes the reply
say why the token is empty, teaches the simulator to read it, and
repairs the messages so no path advises its own opposite.

## The issue's decisions, restated

- The simulator treats a token-less admission as admission when the
  deployment does not issue tokens, and holds the conversation.
- The messages stop contradicting each other: `run --claim` on a
  board that was never offered a code must not advise dropping the
  flag when dropping it advises adding it back, and `check-in`'s
  reading of a token-less reply must cover the deployment that has
  device authentication turned off.
- Both halves are worth doing even if either alone were judged
  sufficient.

## Where the facts already live

The admission story has one wire shape. `check_version`
(`ota/reply.py`) always emits `websocket.url`; `token_for` answers
`""` when `device_auth is None` (auth off) or when nothing resolves
the MAC, and the two reasons are indistinguishable in the body. The
simulator's whole classification is `board.read()`
(`simulator/board.py:462`), a five-step precedence whose last step
is "a non-empty token decides `Admitted` against `Unwelcome`". The
three contradictory sentences are `NOTHING_TO_CLAIM`
(`config/cli.py:5484`), `CANNOT_CONVERSE` (`cli.py:5606`), and the
inline three-causes tail of `_reported` (`cli.py:5749`); a fourth
the issue did not list, `NOT_ADMITTED_AFTER_CLAIM` (`cli.py:5523`),
fires on the auth-off `--claim` happy path after a successful
ceremony. The websocket handshake itself never asks for a
credential when auth is off: `ws.refusal_reason` returns `None`
before reading the header, so a token-less `converse()` already
works; only the classification in front of it refuses.

Two settled decisions bound the fix. The empty token is itself
deliberate and pinned (`tests/unit/test_ota_tokens.py`,
`test_disabled_auth_still_sends_an_empty_token`): the firmware
persists what it is given, and an empty string clears a token
another server left in NVS, so the server must not mint a fake
credential. And the simulator plan's decision 4
(`docs/plans/2026-08-25-simulator.md`) made the check-in's answer a
closed set of four states with a stated precedence; `auth.enabled`
appears nowhere in that plan, so this is an unconsidered case to
amend, not a decision to overturn.

## Open questions, resolved

**The reply gains a closed field saying what the empty token
means.** The distinction the simulator needs (admitted without a
credential, versus not admitted) exists only server-side, so the
server states it: the reply body gains a top-level `access` field,
a literal from a closed set chosen where `token_for` already
classifies. Top-level and not inside `websocket`, because the two
boundaries differ in what stock firmware does with them: unknown
top-level fields are ignored (the `server` block's own comment
says so, and is the precedent), while the firmware writes every
member of `websocket` into NVS, so a key added there would become
a stray NVS entry on every stock board rather than an invisible
extension.

- `"token"`: admitted, and the non-empty `token` beside it is the
  credential.
- `"open"`: admitted, and this deployment issues no device tokens;
  connect without a credential.
- `"denied"`: not admitted; the token is empty because there is
  nothing to admit.

`token_for` deepens into answering the pair (token, access) from
the two facts it already reads (`device_auth is None`, the
resolution), so the token and its explanation cannot disagree. The
addition is additive on the protocol surface the compatibility
promise governs: an unknown top-level key is the boundary stock
firmware demonstrably ignores, the simulator's own `_Reply` model
is `extra="ignore"`, and nothing existing moves or changes
meaning. This is also the direction #386
argues for independently: the server states the state, and the
client phrases what to do about it in its own grammar.

**`Admitted` absorbs the open deployment; no fifth state.** After
the field, an `"open"` reply classifies as `Admitted` with an empty
`token`, because everything the simulator does next is identical:
resolve the websocket target (the URL rules still run; the plan's
never-print rule for the token and URL still holds) and hold the
conversation. A fifth state that behaves exactly like `Admitted`
would fold nothing apart; decision 4's own argument for closed
states cuts the other way here, and the state count in
`capabilities.py`'s row stays a true sentence. The precedence's
last step is amended: the field decides when it is present and
recognized, and a missing field (an older server image, exactly
the skew #386 documents) falls back to today's token rule, so the
simulator against an old image behaves as it does now rather than
worse. The producer stays typed to the closed three-value set; the
consumer models the field as a strict optional string and
recognizes the known set at `read()`, because a `Literal` on the
model would turn an unknown value into a malformed reply instead
of an absent field. An unrecognized value is read as the field
being absent, which is conservative compatibility with servers
this simulator does not know, not a promise that a future
empty-token admission mode survives the fallback (it would read
`Unwelcome`, as today's rule says). The value is far-side bytes
under the never-print rule: like the token and the URL, it reaches
no output surface.
A reply whose field contradicts what stands beside it is `Refused`
as the contradictory replies already are, refused rather than
resolved, and the matrix is stated whole: `"token"` with an empty
token; `"open"` or `"denied"` with a non-empty one; and an
activation object beside `"open"` or `"token"`, since a board
being claimed is by definition not yet admitted and no server
decision site can emit that pairing. Activation is compatible only
with an absent field (an older server) or `"denied"`, and the
contradiction checks run before the activation classification the
way the existing token-beside-activation check already does.

**`converse()` keeps the firmware's own headers, empty bearer
included.** The 2026-08-25 plan's fidelity contract promises the
four firmware handshake headers, and stock firmware with no stored
token sends exactly the empty bearer the simulator sends today.
The header is harmless under both auth modes: auth off never reads
it, and auth on treats an empty and an absent credential
identically. `_opened` therefore stays unchanged, and the fact is
recorded here rather than re-derived.

**The four sentences, repaired.**

- `CANNOT_CONVERSE` (run, on a genuinely unadmitted board) stops
  advising `--claim` unconditionally; it points at `check-in` for
  the which-state question and mentions the claim only for the
  board that shows a code. With the behavior fix, this sentence no
  longer fires on the auth-off deployment at all, which is the
  larger repair.
- `NOTHING_TO_CLAIM` (claim, on a board with no code) becomes
  correct as written once `run` without `--claim` actually works;
  its wording is kept, reread against the new behavior during
  implementation, and adjusted only if it still sends anyone in a
  circle.
- The `check-in` tail becomes a named constant beside its
  siblings, and its cause list becomes complete rather than merely
  shorter: with the field in the reply, the auth-off deployment
  never reaches the `Unwelcome` arm, and the causes that remain
  are enumerated from the decision sites that produce an
  empty-token, no-activation reply rather than from the old
  sentence. That enumeration adds two the old sentence missed: the
  binding view being non-authoritative suppresses activation while
  resolving nothing (`onboarding/unbound.py`, recorded in
  `docs/xiaozhi-notes.md`), and the unloaded-agent cause covers a
  `default_agent` naming an unloaded agent as well as a device
  binding, so its wording broadens. Against an older server that
  sends no field, the tail gains the further reading the issue
  asked for: the deployment may simply not issue tokens, and a
  server built before the reply could say so looks exactly like
  this. `check-in` on an `"open"` admission says the deployment
  issues no tokens, so the operator learns auth is off from the
  report rather than from the absence of a failure.
- `NOT_ADMITTED_AFTER_CLAIM` (the post-ceremony trap) also gains
  the older-server reading, since the ceremony-then-`Unwelcome`
  sequence is precisely what an auth-off deployment on an old image
  produces.
- The token-minting vocabulary around the claim flow is retired
  wherever tokenless admission falsifies it: `CLAIM_HELP` stops
  promising that the follow-up check-in issues a token,
  `_claimed`'s explanation of its fourth step speaks of the final
  check-in admitting the board rather than minting a credential,
  and the capability rows that repeat the promise move with them,
  with `docs/reference/cli.md` regenerated.

## Module layout

No new module. `ota/reply.py` deepens `token_for` into answering
the classification it already computes; `simulator/board.py`
deepens `read()` and its `_Reply` model to carry and apply the
field; `config/cli.py` reworks the four sentences it already owns;
`simulator/capabilities.py` updates the row that states the reply
reading. The fact "why is this token empty" gets one home on each
side of the wire: the server's `token_for` writes it, the
simulator's `read()` applies it, and no third place re-derives it.

## Tests

- **Server half** (`tests/unit/test_ota_tokens.py`,
  `tests/unit/test_ota.py`): the three-way field. Auth on and
  resolved answers `"token"` with the credential beside it; auth
  off and resolved answers `"open"` with the empty token the
  existing pin already demands (that pin stays byte-identical);
  unresolved answers `"denied"` under both auth settings. The
  `test_ota.py` exact-equality pin on the `server` block and the
  `websocket` object's existing shape are both untouched by
  construction (the field is top-level); whichever existing
  assertions pin the body's top-level keys are extended rather
  than loosened.
- **Simulator reading** (`tests/unit/test_simulator_board.py`): an
  `"open"` reply classifies `Admitted` with an empty token and a
  resolved websocket target; a `"denied"` reply classifies
  `Unwelcome`; a field-less reply keeps today's classification both
  ways (the fixtures' existing bodies, unchanged, are that case);
  every row of the contradiction matrix refuses, as hostile-reply
  cases: `"token"` with an empty token, `"open"` and `"denied"`
  with a non-empty one, and an activation object beside `"open"`
  (the empty-token variant included) and beside `"token"`; an
  unknown value falls back to the token rule, and a
  credential-shaped unknown value (a sentinel that must appear
  nowhere) joins the existing four-surface no-leak inventory:
  stdout, stderr, logs and exception chains. The existing
  `test_the_conversation_verb_refuses_a_board_that_may_not_speak`
  keeps its `unwelcome()` half by making that fixture an explicit
  `"denied"` (or leaving it field-less), and a new case beside it
  proves `run` proceeds past classification on an `"open"` body.
- **Messages** (`test_simulator_board.py`): the tail as a
  constant, with the older-server reading, the binding-view cause
  and the broadened unloaded-agent wording each asserted;
  `check-in` on an `"open"` body reporting admission and that the
  deployment issues no tokens; the reworded `CANNOT_CONVERSE`
  asserted where it still fires.
- **End to end** (`tests/integration/test_cli_simulator.py`): the
  issue's own reproduction as a test: a deployment with auth off
  and a default agent, `vinga simulator run <ota-url>` with no
  `--claim`, and the conversation reaches its close. The existing
  auth-on cases stay on auth-on deployments so their bite (the
  token actually opening the socket, the doctored empty token
  refused) is preserved.
- **Vocabulary propagation**: `tests/support/deployment.py` answers
  in the four-state vocabulary already and needs no change; the
  `tests/integration/test_cli_live.py` `Unwelcome` assertion is on
  an auth-on bound-not-loaded case and stays true.
- **Capability table**
  (`tests/unit/test_simulator_capabilities.py`): the states row's
  wording change lands with the row's pin updated in the same
  commit, and the generated `docs/reference/cli.md` regenerates
  through its generator wherever help text moved
  (`tests/unit/test_config_docgen.py` is the tripwire).

## Risks

- **Version skew in both directions.** An old simulator against a
  new server ignores the field (`extra="ignore"`); a new simulator
  against an old image falls back to the token rule and behaves
  exactly as today, with the two post-ceremony and three-causes
  sentences now naming that possibility. Neither pairing gets
  worse; the matched pair gets correct.
- **The firmware.** The field is an additive top-level key, the
  one boundary stock firmware demonstrably ignores; it stays out
  of `websocket`, whose members the firmware persists to NVS. The
  compatibility floor is untouched, and the plan says so where the
  field is emitted.
- **Doc and census staleness.** Help-text and capability wording
  changes stale `docs/reference/cli.md` (regenerated through the
  generator) and possibly the command-spellings manifest
  (regenerated through
  `uv run python -m tests.unit.test_command_spellings`), both run
  before each PR.
- **The old plan's record.** Decision 4's table and precedence in
  `docs/plans/2026-08-25-simulator.md`, and the `Unwelcome`
  interpretation note in its implementation doc, are amended in
  place with a dated note pointing here, so the record and the code
  do not silently diverge.

## Milestones

- [ ] **M1: the reply says why the token is empty.**
  `token_for` deepened to answer (token, access); the reply body
  gains the closed top-level `access` field; the server-half tests
  above; the protocol addition recorded in `docs/xiaozhi-notes.md`
  beside the existing reply-shape notes; a CHANGELOG entry. Design
  footprint: deepens `token_for`, whose callers stop having to
  re-derive why a token is empty; no new module, no new state
  anywhere else. Documentation footprint: `docs/xiaozhi-notes.md`
  (the reply shape) and `CHANGELOG.md`; no generated reference
  changes because the OTA reply is not in the OpenAPI document.
  Releasable alone: the field is additive and nothing reads it yet.
- [ ] **M2: the simulator reads it, and the sentences stop
  contradicting each other.** `board.read()` applies the field with
  the fallback and contradiction rules above; the four sentences
  repaired, the inline tail named; the capability row updated; the simulator and
  integration tests above; `docs/reference/cli.md` regenerated;
  the 2026-08-25 plan and implementation docs amended; a CHANGELOG
  entry. Design footprint: deepens `read()` (one decision site
  keeps owning classification) and the CLI's rendering of it; no
  new module. Documentation footprint: the amended 2026-08-25 plan
  pair, `docs/reference/cli.md` through its generator, the root
  README's simulator bullet confirmed still true (it promises
  check-in, claim and conversation, all of which this widens rather
  than narrows), and `CHANGELOG.md`.

## Plan review round

Backend codex (codex-cli 0.153.0), model `gpt-5.6-sol`, sandbox
read-only, 2026-09-04, against commit `de7ff059`; the reviewer ran
6m32s. Verdict: ready after the P1/P2 amendments.

1. **P2: the firmware compatibility argument uses the wrong
   unknown-key boundary.** The comment at `ota/reply.py` supports
   unknown top-level fields, not arbitrary members inside
   `websocket`; the 2026-08-02 implementation doc records that
   firmware writes every `websocket` member into NVS. Adding
   `access` inside `websocket` is therefore not operationally
   invisible, and M1 has no device checkpoint establishing
   harmlessness under the compatibility promise. The plan should
   put the discriminator in a top-level field the firmware truly
   ignores, or acknowledge the new NVS entry and require a device
   checkpoint before calling M1 releasable.

   *Resolution*: accepted in full; the field moves to the top
   level of the reply body. The open-questions section now argues
   the boundary explicitly (top-level keys ignored, `websocket`
   members persisted to NVS), the firmware risk names it, the
   milestone and test text follow, and the `websocket` object's
   shape is untouched by construction, so no device checkpoint is
   needed.

2. **P2: the contradiction matrix omits activation plus
   `access="open"`.** The existing precedence processes
   contradictions before activation; a reply carrying both an
   activation object and `access="open"` would classify
   `Activating` despite simultaneously claiming admission, and no
   server decision site can emit that combination. Activation is
   compatible only with an absent field (old server) or
   `"denied"`; `activation + "open"` and `activation + "token"`
   must be `Refused`, with hostile-reply tests including the
   empty-token `"open"` case.

   *Resolution*: accepted in full. The contradiction matrix is now
   stated whole in the classification section, activation is
   declared compatible only with an absent field or `"denied"`,
   the checks are ordered before the activation classification,
   and the test list names every row as a hostile-reply case,
   the empty-token `"open"`-beside-activation variant included.

3. **P2: the claimed complete `Unwelcome` diagnosis still omits
   reachable causes.** `onboarding/unbound.py` can suppress
   activation when the binding view is not authoritative,
   producing no agents, no token and no activation (documented in
   `docs/xiaozhi-notes.md`), and the bound-agent-not-loaded
   wording is too narrow because `default_agent` can also name an
   unloaded agent. Enumerate binding-view failure as its own
   cause, broaden the unloaded cause, and assert both messages in
   tests.

   *Resolution*: accepted in full. The tail's causes are now
   enumerated from the decision sites that produce an empty-token,
   no-activation reply, adding the non-authoritative binding view
   as its own cause and broadening the unloaded-agent cause to
   name `default_agent`, with both asserted in the message tests.

4. **P2: omitting the empty Authorization header contradicts the
   simulator's settled fidelity contract and is unnecessary.** The
   2026-08-25 plan promises the four firmware handshake headers;
   the server treats empty and absent credentials identically
   under auth-on and ignores the header under auth-off, and the
   proposed integration test would pass either way. Leave
   `_opened` unchanged and record that the empty bearer header is
   harmless under both modes.

   *Resolution*: accepted in full; the header change is dropped.
   The resolution block now records the fidelity contract, the
   firmware's own empty-bearer behavior and the harmlessness under
   both auth modes, and the M2 milestone no longer touches
   `conversation.py`.

5. **P2: token-specific help and capability text will remain false
   after tokenless admission works.** `CLAIM_HELP` promises the
   follow-up check-in issues a token, the capability table repeats
   it, and `_claimed` explains its fourth step in token-minting
   terms. Replace these with the final websocket admission
   vocabulary, update both affected capability rows, and
   regenerate the CLI reference.

   *Resolution*: accepted in full. The sentences section gains the
   retirement of the token-minting vocabulary (`CLAIM_HELP`, the
   `_claimed` fourth-step explanation, both capability rows), and
   M2 already carried the CLI reference regeneration.

6. **P2: unknown `access` handling is underspecified and lacks the
   required no-leak pin.** A `Literal`-typed consumer field would
   make an unknown value a malformed reply rather than an absent
   field; model the consumer as a strict optional string
   recognized at `read()`, keep the producer typed to the closed
   three-value set, add a credential-shaped unknown value to the
   four-surface no-leak inventory, and describe the fallback as
   conservative compatibility rather than support for future
   empty-token admission modes.

   *Resolution*: accepted in full. The classification section now
   states the producer/consumer typing split with its reason, the
   fallback claim is softened to conservative compatibility with
   the future-mode caveat spelled out, the value joins the
   never-print rule beside the token and URL, and the test list
   gains the credential-shaped sentinel across the four-surface
   no-leak inventory.

7. **P3: the fixture-preservation instructions contradict each
   other.** The plan says field-less fixture bodies stay unchanged
   and also permits making `unwelcome()` an explicit `"denied"`.
   Keep `activating()`, `admitted()` and `unwelcome()`
   byte-unchanged as old-server fixtures; add separate `open` and
   explicit `denied` helpers for the new protocol cases.
