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
server states it: the `websocket` object gains `access`, a literal
from a closed set chosen where `token_for` already classifies,
because the field explains the token beside it.

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
promise governs: stock firmware ignores unknown keys in the reply
(the existing comment at the emission site says so), the
simulator's own `_Reply` model is `extra="ignore"`, and nothing
existing moves or changes meaning. This is also the direction #386
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
last step is amended: the field decides when it is present, and a
missing field (an older server image, exactly the skew #386
documents) falls back to today's token rule, so the simulator
against an old image behaves as it does now rather than worse.
A reply whose field contradicts its token (`"token"` with an empty
token, `"open"` or `"denied"` with a non-empty one) is `Refused` as
the contradictory replies already are: refused rather than
resolved. An unknown literal in the field is read as the field
being absent, so a newer server never strands an older simulator.

**`converse()` presents no credential it does not have.** The
websocket opener currently always sends `Authorization: Bearer
{token}`, which with an empty token is a header asserting an empty
credential. It skips the header when the token is empty: truthful,
and inert against every server (auth off never reads it; auth on
refuses an absent credential the same as an empty one).

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
- The `check-in` three-causes tail becomes a named constant beside
  its siblings, and its causes become accurate by subtraction: with
  the field in the reply, the auth-off deployment never reaches the
  `Unwelcome` arm, so the three configurations it names are once
  again the complete list for the replies that do. Against an older
  server that sends no field, the tail gains the fourth reading the
  issue asked for: the deployment may simply not issue tokens, and
  a server built before the reply could say so looks exactly like
  this. `check-in` on an `"open"` admission says the deployment
  issues no tokens, so the operator learns auth is off from the
  report rather than from the absence of a failure.
- `NOT_ADMITTED_AFTER_CLAIM` (the post-ceremony trap) also gains
  the older-server reading, since the ceremony-then-`Unwelcome`
  sequence is precisely what an auth-off deployment on an old image
  produces.

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
  `test_ota.py` exact-equality pin on the `server` block is
  untouched by construction (the field lands under `websocket`);
  whichever existing assertions pin the `websocket` block's keys
  are extended rather than loosened.
- **Simulator reading** (`tests/unit/test_simulator_board.py`): an
  `"open"` reply classifies `Admitted` with an empty token and a
  resolved websocket target; a `"denied"` reply classifies
  `Unwelcome`; a field-less reply keeps today's classification both
  ways (the fixtures' existing bodies, unchanged, are that case);
  the two contradictions refuse; an unknown literal falls back to
  the token rule. The existing
  `test_the_conversation_verb_refuses_a_board_that_may_not_speak`
  keeps its `unwelcome()` half by making that fixture an explicit
  `"denied"` (or leaving it field-less), and a new case beside it
  proves `run` proceeds past classification on an `"open"` body.
- **Messages** (`test_simulator_board.py`): the three-causes tail
  as a constant, with the fourth (older-server) reading asserted;
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
- **The firmware.** The field is additive inside an object whose
  unknown keys stock firmware ignores; the compatibility floor is
  untouched, and the plan says so where the field is emitted.
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
  `token_for` deepened to answer (token, access); the `websocket`
  object gains the closed `access` field; the server-half tests
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
  the fallback and contradiction rules above; `converse()` skips
  the empty-credential header; the four sentences repaired, the
  inline tail named; the capability row updated; the simulator and
  integration tests above; `docs/reference/cli.md` regenerated;
  the 2026-08-25 plan and implementation docs amended; a CHANGELOG
  entry. Design footprint: deepens `read()` (one decision site
  keeps owning classification) and the CLI's rendering of it; no
  new module. Documentation footprint: the amended 2026-08-25 plan
  pair, `docs/reference/cli.md` through its generator, the root
  README's simulator bullet confirmed still true (it promises
  check-in, claim and conversation, all of which this widens rather
  than narrows), and `CHANGELOG.md`.
