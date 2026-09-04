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
