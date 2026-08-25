# Put a simulated board in the grammar: implementation

The companion to [`2026-08-25-simulator.md`](2026-08-25-simulator.md),
one section per milestone, appended in the change that ticks the
milestone. It records deviations from the plan, resolutions of anything
the plan left open, and discoveries; a milestone with no deviations says
so explicitly.

## M1: the board and its check-in

PR TBD.

This section was written by the coordinating session from the
milestone's nine commits after a session interruption ended the
implementing agent before it could write it; the commits carry the
milestone's reasoning in their bodies, and everything below is read
from them and from the tree, not from memory.

### What landed

The device-side half of `vinga simulator`, in the order the commits
tell it: the address boundary first, then the wire inventory, then the
board, then the capability table, then the grammar rows, then the
tests, then the documents.

- `device_endpoint.py`, extracted from `doctor.py` (473 lines in, 338
  out of the doctor): the address policy AND the request lifecycle,
  because a client about to POST to a device-facing address needs the
  same library quieting, inside-the-boundary client construction,
  redirect refusal and class-only failure naming the doctor's GET
  needed. The address arrives as a parsed `Endpoint` whose
  `activation()` composition owns the one rule a string type cannot
  offer: the poll segment appends to the path and the query string
  stays behind it. The doctor's own suite runs unchanged against the
  moved code, which is the pin the move was made against. The one new
  rule is `websocket_target`, the stricter reading a client holding a
  device token makes of a far-side URL: it must parse, name `ws` or
  `wss`, carry a host, carry no userinfo, and never downgrade an
  `https` check-in to a plain socket; `reported_websocket` keeps the
  doctor's diagnostic reading beside it, which redacts instead of
  refusing because a diagnosis never goes there.
- `protocol/messages.py` deepened by making the send-side inventory
  public and adding `SERVER_MESSAGE_TYPES` beside it, with
  `server_states` derived from `tts_message`'s own `Literal` members
  rather than restated, so a fourth TTS state appears in the
  simulator's help without anybody remembering it. The
  server-to-device models themselves are M2's, per plan finding 3's
  resolution, and the module records the asymmetry.
- `simulator/board.py`: both halves of the identity derived and
  neither stored (the fixed locally-administered
  `02:00:00:00:00:01`, the client id a UUID5 over the normalized MAC
  under the repository's namespace); the check-in POST through the
  endpoint boundary; the four-state reading (`Activating`, `Admitted`,
  `Unwelcome`, `Refused`) as a strict schema under the five-step
  precedence, with `activation is not None` written that way; the
  claim ceremony as four steps whose fourth is the second check-in
  that mints the token; every wait bounded, with a remote `timeout_ms`
  able only to shorten the local ceiling and the firmware's ten-poll,
  three-second cadence kept.
- `simulator/capabilities.py`: one closed three-sided table that the
  help epilog, the committed reference and the tests all read, so a
  claim in prose is impossible because there is no prose. Message rows
  are derived from `protocol/messages.py` at `(type, state, mode)`
  granularity; every conversation row sits on "not available yet"
  naming `run`, which is the side M2 flips and then retires.
- The grammar rows: a `GROUPS` entry for the noun, a row for
  `check-in`, one new `Invocation` field for the address, `--mac` and
  `--claim` riding the fields the device verbs already have, and no
  derivation behind the URL positional, deliberately, so the headline
  command of a client install cannot inherit the server half's gate.
  `--claim` performs `ADD_DEVICE`, asserted by identity with the act
  behind `device pending claim`, and the contract check holds the
  simulator's rows to carrying a function rather than an `Act` so a
  future request of its own would arrive as an undecided operation.
- The lanes. Unit: 21 board cases including the ten-reply hostile
  table (each named with the outcome and exit code it must reach, the
  `activation={}` row separating `is not None` from truthiness), the
  counted 307 with the target never fetched, the four-request claim
  ceremony asserted off recorded requests with one MAC and one client
  id across all four, and the ten-poll cadence on a controlled clock;
  16 capability cases including the five both-ways assertions each
  held to going red; 15 endpoint cases. Live lane: the driven
  `simulator check-in` row runs the whole four-step ceremony against
  a real uvicorn and asserts the issue's three credentials plus the
  minted websocket address absent from all four observability
  surfaces; the `("simulator",)` family refusal hands the command an
  address with a credential-shaped segment. Wheel lane: the row is
  driven ungated, because the check-in is httpx and pydantic and
  nothing else, so a bare install carries it whole. The import
  inventory widened to the simulator package and `device_endpoint`.
- `tests/support/deployment.py`'s `check_in` stopped being a
  hand-written copy of the POST this milestone ships: it drives the
  production board and answers in the four-state vocabulary, so the
  board a lane uses and the board an operator runs are one structure.
- The documents: `cli.md`'s simulator section rendered from the same
  epilog the help prints, the changelog entry carrying the limit
  beside the command, `xiaozhi-notes.md`'s redirect clause corrected
  (the sdk-based test simulator could not show redirect intolerance;
  this one can, and has a lane asserting exactly one request), and
  the spelling census regenerated for the command the tree now
  quotes.

### Deviations

None found in the record. The nine commit bodies record choices the
plan already made and cite the plan's own findings for the two places
a reader might suspect one: `_MESSAGE_TYPES` becoming public without
the server-side models (finding 3's resolution assigns the models to
M2) and the capability table shipping with every conversation row on
the third side (finding 2's resolution, the releasable-merge rule).

### Verification

From `vinga-server/` on the milestone head `c984a122`: `uv run ruff
check .` clean; `uv run pytest tests/unit -q -n auto --dist loadfile`
3879 passed, 19 skipped, 1 failed in `test_tools_mcp_http.py`
(`test_a_url_nobody_answers_is_down_for_the_transport`, a file this
branch does not touch, failing on this network because the black-holed
probe times out instead of refusing; it passes in CI); `uv run pytest
tests/integration -q` 175 passed; the openapi, events and cli
reference drift checks byte-clean locally. The image and smoke lane
are CI's to prove.
