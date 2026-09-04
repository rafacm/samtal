# Admit the simulator where the deployment issues no tokens: implementation

Companion to
[`2026-09-04-simulator-tokenless-admission.md`](2026-09-04-simulator-tokenless-admission.md).
One section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out. A milestone with no deviations
says so explicitly.

## M1: the reply says why the token is empty

### What was done

`ota/reply.py`. A module-level `Access` literal (`token`, `open`,
`denied`, each with its meaning beside it) and a frozen `Admission`
carrying a token and the word for it as one answer. `token_for` now
answers an `Admission` rather than a bare string, from the two facts it
already read and in the order the plan settles: a device no agent
covers is `denied` whatever the auth setting says, because being
unresolved is the stronger fact; auth off with something to reach is
`open` with the empty token; anything else is the issued credential and
`token`. `check_version` binds the admission once before it assembles
the body, so the token and its explanation come from one call and
cannot disagree; the `websocket` object takes `admission.token` where
it used to take the call, and the body gains `access` at the top level
with the boundary argument in a comment beside it (the firmware parses
exactly `activation`, `mqtt`, `websocket`, `server_time` and `firmware`
and ignores every other top-level key, while it writes every member of
`websocket` into NVS). The module docstring's list of what the reply
carries gained the word.

`ota/__init__.py`. `Access` and `Admission` join the gathering and the
`__all__` beside `token_for`, whose return type they are: the package
is the name a caller reaches, and the two halves of that answer have to
be reachable by the same name as the function.

Tests. `tests/unit/test_ota_tokens.py` gained a `checked_in` helper
answering the whole reply, with `issued_token` now one line over it,
and three cases: a bound device on an auth-on deployment answers
`token` with a non-empty credential beside it; auth off answers `open`
with the empty token; and a device no agent covers answers `denied`
under either auth setting, parametrized over the setting so the
precedence is pinned rather than described. The existing empty-token
pin, `test_disabled_auth_still_sends_an_empty_token`, is byte-unchanged,
which is what keeps the new `open` case additive.
`tests/unit/test_ota.py::test_reply_carries_the_websocket_url_the_device_needs`
now reads the body rather than only the `websocket` object and asserts
`access == "denied"` beside the empty token it already pinned:
extended, with nothing loosened. The `server`-block exact-equality pin
and the `websocket` object's shape are untouched, the second by
construction.

Documents. `docs/xiaozhi-notes.md` gained two bullets in the OTA
check-in section, immediately after the existing reading of the empty
token that they qualify: what the field says, and why it is top-level
rather than a `websocket` member, the second citing the parser's five
keys (recorded in the ceremony section below it) and the
hardware-observed NVS persistence in the v1 implementation record.
`CHANGELOG.md` gained one `### Added` entry under the existing
`## 2026-09-04` heading.

Nothing generated changed except `tests/unit/command-spellings.txt`,
regenerated with `uv run python -m tests.unit.test_command_spellings`
and never by hand. `docs/reference/` is untouched, as the milestone
predicted: the OTA reply is not in the OpenAPI document, and no help
text or capability row moved in M1.

### Deviations from the plan

None in substance. Three decisions the plan left to implementation:

**The pair is a frozen dataclass rather than a tuple.** A two-tuple
would have made every reader count positions at the one call site that
unpacks it, and the plan's whole point is that the token and its
explanation are one answer; `Admission(token, access)` says which is
which at the point of use, and being frozen means the answer cannot be
edited between the decision and the body.

**`token_for` keeps its name.** The plan says the function deepens
rather than moves, and the name is what callers, the package `__all__`
and the module's own prose already reach for. Renaming it to match its
widened answer would have been a second change riding along with this
one, and its docstring now says both halves.

**The field sits between `server` and `websocket` in the body.** JSON
object order means nothing to any reader of this reply; it is placed
where a person reading the assembled body meets it just before the
token it explains.

### Discoveries

**A CHANGELOG entry alone stales the census manifest.** The manifest
records the line number of every command spelling in every tracked
file, so inserting a dated entry at the top of the current section
moved all 121 CHANGELOG rows below it. The regeneration is mechanical
and the diff is line numbers only, but a change that touches no command
at all still has to run the generator.

### Verification

Run from `vinga-server/`, with the development Postgres up:

- `uv run ruff check .`: `All checks passed!`
- `uv run pytest tests/unit -q`: `5234 passed, 19 skipped in 558.46s`
- `uv run pytest tests/integration -q`: `238 passed in 378.19s`
- `scripts/check_doc_links.py`: `checked 180 files, 0 failures`

No device checkpoint, and none is owed: the plan's review round moved
the field to the top level precisely so that the `websocket` object a
stock board persists into NVS is untouched, and the compatibility
argument rests on the firmware parser's fixed five keys rather than on
an observation this milestone would have had to make.

### M1 PR review round

PR [#387](https://github.com/rafacm/vinga/pull/387). Backend codex
(codex-cli 0.153.0), model `gpt-5.6-sol`, read-only sandbox,
2026-09-04, against `b4d67c1f`, runtime 4m17s, posted by the
self-posting script. No findings; verdict mergeable as is. Nothing
to resolve.

## M2: the simulator reads it, and the sentences stop contradicting each other

### What was done

`simulator/board.py`. The three words spelled as module constants
(`ACCESS_TOKEN`, `ACCESS_OPEN`, `ACCESS_DENIED`) with `KNOWN_ACCESS`
beside them, spelled here rather than imported from `ota/reply.py`
because this half is the client and what it may reach is the published
protocol. `_Reply` gained `access` as a strict optional string, with the
reason for not typing it as a `Literal` beside the field. `read()`
recognizes the word before it asks anything else, so "this reply carries
no word I know" is one fact from the first question to the last; then
refuses the whole contradiction matrix through `_contradicted`, ordered
in front of the activation classification and beside the existing
activation-beside-a-token check; then decides admission through
`_admitted`, which is the word where there is one and the token where
there is not. `CONTRADICTORY_ACCESS` is the sentence for the new rows.
The module docstring, `Admitted`'s and `Unwelcome`'s docstrings and
`read()`'s own now tell the story the code tells; the word joins the
token and the URL under the never-print rule, and reaches no output
surface.

`config/cli.py`. `CANNOT_CONVERSE` points at `check-in` first and
conditions the claim on the board that is showing a code.
`NOT_ADMITTED_AFTER_CLAIM` gains the older-server reading. The inline
three-causes tail becomes `MAY_NOT_SPEAK`, a named constant beside its
siblings, with five readings enumerated from the decision sites:
`ota/reply.py` withholds the token whenever nothing resolves the board,
`onboarding/unbound.py` withholds the code for four reasons of its own
(onboarding off, an unloaded agent that `default_agent` may name as well
as a binding, a pending table that would take no more, and a
non-authoritative binding view), and the fifth is not a configuration at
all but a server too old to say it issues no tokens. `TOKEN_ISSUED` and
`NO_TOKEN_ISSUED` are the two readings of an admitted board, chosen off
the state's own token. `CLAIM_HELP` and `_claimed`'s fourth-step
docstring speak of the check-in that admits the board rather than of one
that mints a credential, and `_simulator_run`'s docstring says where the
empty token comes from.

`simulator/capabilities.py`. The states row says the reply's own word is
what tells them apart; the claim row stops promising a token.

Tests. `tests/unit/test_simulator_board.py`: the three fixtures
byte-unchanged as the old-server bodies, three siblings
(`admitted_without_a_token`, `turned_away`, `admitted_with_a_token`) for
the new protocol, the classification cases both ways, the fallback
parametrized over an absent and an unknown word with and without a
token, five contradiction rows in the hostile table, the credential
shaped word in the four-surface no-leak inventory and again on the
replies this client accepts, the message assertions, the `run` verb's
turned-away variant and the case that proves it opens a socket where the
deployment issues none.
`tests/unit/test_simulator_capabilities.py` pins both changed rows.
`tests/integration/test_cli_simulator.py` gained the issue's own
reproduction on a deployment of its own: authentication off, a default
agent, no `--claim`, one check-in admitted with an empty token, and a
conversation that reaches its close.

Documents. `docs/reference/cli.md` regenerated through
`vinga-server config cli-reference` (the help text moved), the census
manifest through `uv run python -m tests.unit.test_command_spellings`,
the 2026-08-25 plan pair amended with dated notes pointing here, and one
`### Fixed` pair in `CHANGELOG.md` under the existing dated section.

The root README's simulator bullet was reread and is still true: it
promises that `vinga simulator` "checks in, claims itself, and holds a
conversation", and this milestone widens where the third of those works
rather than narrowing any of them. It is unchanged.

### Deviations from the plan

**The capability row had no pin to update.** The plan and the milestone
brief both say the states row's wording is pinned by
`tests/unit/test_simulator_capabilities.py` and that the pin moves in the
same commit. It was not: that file holds the five structural assertions
and the message-row granularity case, and the only thing pinning the
prose rows' words was the generated `docs/reference/cli.md`. Rather than
leave the reference as the only reader, a case was added
(`test_the_states_row_says_what_the_reply_is_read_by`) holding both
changed rows to the vocabulary the reply and the command now use.

**`NOTHING_TO_CLAIM` was reread and kept.** The plan allowed adjusting it
only if it still sends a reader in a circle. It does not: it says the
claim addresses a board showing a code, that a bound board needs none and
a board this deployment will not admit is not one a claim can help, and
that running without `--claim` says which of the two it is. Running
without the flag is now an answer rather than a redirection, since an
admitted board on a token-less deployment holds its conversation there.
The half that circled was `CANNOT_CONVERSE`, which pointed back at the
flag, and that is the sentence that changed.

**The new refusals get their own sentence.** The plan says a
contradictory word is `Refused` "as the contradictory replies already
are". `CONTRADICTORY_REPLY` names the activation-beside-a-token shape in
as many words, so reusing it would have answered the wrong fixed
sentence, which this suite treats as being as wrong as answering with a
value. `CONTRADICTORY_ACCESS` sits beside it and names the three rows it
covers.

**The no-leak sentinel joins the inventory twice.** The existing
four-surface case asserts exit 1 on every answer it walks, and an
unrecognized word is accepted rather than refused, so the sentinel could
only join that loop on a reply refused for another reason. It does, and a
sibling case carries the harder half: the same sentinel on the replies
this client accepts, where the command goes on to report a state and
something could still print it.

**The integration case gets its own deployment and its own fixture.**
Parametrizing the existing `live` fixture over the auth setting would
have run the token cases on a deployment that asks for no token, which is
exactly the bite the plan says to preserve. The new fixture builds the
same app from a `Config` carrying `server.auth.enabled: false` and
`default_agent`; the lane's own conftest sets `VINGA_AUTH_SECRET` for
every process, and it does not matter, because `build_device_auth`
answers None on the flag before it looks at the environment.

### Discoveries

**The empty bearer header needed no case of its own.** The plan's
finding-4 resolution keeps `conversation.py` untouched, and the
integration case confirms why: the simulator sends
`Authorization: Bearer ` with an empty token, `ws.refusal_reason` returns
None before reading any header when `device_auth is None`, and the
handshake is accepted. The header is the firmware's own behavior on a
board with nothing in NVS, so fidelity and correctness agree here.

**The census manifest stales on documentation edits, again.** Three of
this milestone's commits regenerate `tests/unit/command-spellings.txt`
with no command spelling changed in any of them: the manifest records
line numbers, and an inserted paragraph moves every row below it in that
file. M1 recorded the same thing about a CHANGELOG entry; it is a
property of the manifest rather than of either change.

### Verification

Run from `vinga-server/`, with the development Postgres up:

- `uv run ruff check .`: `All checks passed!`
- `uv run pytest tests/unit -q`: `5255 passed, 19 skipped in 558.60s`
- `uv run pytest tests/integration -q`: `239 passed in 354.24s`
- `scripts/check_doc_links.py`: `checked 181 files, 0 failures`

No device checkpoint, and none is owed: nothing on the wire changed in
this milestone. The field it reads was shipped by M1, whose own record
carries the compatibility argument.
