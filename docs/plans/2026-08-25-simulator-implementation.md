# Put a simulated board in the grammar: implementation

The companion to [`2026-08-25-simulator.md`](2026-08-25-simulator.md),
one section per milestone, appended in the change that ticks the
milestone. It records deviations from the plan, resolutions of anything
the plan left open, and discoveries; a milestone with no deviations says
so explicitly.

## M1: the board and its check-in

PR #299.

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

None from the plan's decisions. The nine commit bodies record choices
the plan already made and cite the plan's own findings for the two
places a reader might suspect one: `_MESSAGE_TYPES` becoming public
without the server-side models (finding 3's resolution assigns the
models to M2) and the capability table shipping with every
conversation row on the third side (finding 2's resolution, the
releasable-merge rule).

Four interpretation decisions the implementing agent made where the
plan was silent, recovered from the coordinator's resume note and
verified against the tree:

- **The doctor re-imports the moved names rather than having its
  callers rewired**, so the seam its own suite patches stays where
  that suite reaches it (`doctor.py:60-74`); the move is proven by
  the suite running unchanged, which was the plan's pin.
- **`Admitted` reports the websocket URL's presence and protocol
  version, never its value**, extending the "can be the deployment's
  secret" rule from the OTA URL to the address the reply derives
  from it.
- **`Unwelcome`'s sentence names the three configurations that
  produce it** (onboarding off, nothing resolving the MAC, no default
  agent covering it), because a 200 with an empty token is the trap
  state and a sentence that just said "not admitted" would send a
  reader to the network.
- **`ACTIVATION_SEGMENT` is spelled client-side with an equality test
  against `ota.router.ACTIVATE_SEGMENT`**
  (`test_device_endpoint.py:40`) rather than imported, because the
  router module imports FastAPI, which the client half does not
  carry; the test is what keeps two spellings one fact.

### PR review round

External review of PR #299's diff (`main...06e15721`), 2026-08-25.
Backend: codex CLI, model `gpt-5.6-sol`, read-only sandbox. Verdict as
received: mergeable after the listed fixes. Six findings, three P1 and
three P2. Five are adopted as prescribed and one is adopted as a
convention change with its severity declined, recorded under the
finding rather than left as a silent difference. One commit each, and
every fix that closes a leak names the shape it is red against.

1. **P1: the activation fields could reflect the secret OTA URL to
   stdout.** `printable` bounds far-side text and takes the control
   characters out of it, and a supplied address is perfectly printable
   and perfectly short, so an endpoint that echoed the request's own
   path or query into `code`, `message` or `challenge` would publish
   through this command's stdout the segment no sentence here prints.
   The four-surface cases covered refused replies only, despite the
   plan's exhaustive claim.

   *Resolution* (`664b345f`): `Endpoint` gains the inventory of its own
   parts a reply may not hand back (the path and the query, whole and
   in their parts, longest first) and one door those three fields go
   through, `repeated`, which redacts the inventory case-insensitively
   and then bounds and makes printable, so the two rules cannot be
   applied by halves. The host is deliberately not in the inventory:
   the activation message carries the deployment's origin on purpose,
   because that is the line the firmware draws for a person to type
   into a browser. Parts shorter than four characters are left alone,
   since those are what a path is made of (`/x/`, `/v1/`) rather than
   what it hides, and matching them as substrings would take a letter
   out of the middle of an ordinary word. Redaction rather than refusal
   because a deployment whose `public_url` carries a path would
   otherwise have a correct reply refused. Bite: rendering the three
   fields through `printable` alone fails the new case in all three of
   its parametrizations.

2. **P1: a superseded claim exposed the rejected `--mac` in the HTTP
   body and the CLI error.** `claim_device`'s two `DeviceAlreadyBound`
   refusals interpolated the normalized MAC, which the API places in a
   response body and the CLI relays to stderr.

   *Resolution* (`a1a06106`): the mechanism is adopted and the severity
   is declined. **The leak framing does not hold**, and the evidence is
   in this repository: the plan's own decision 2 says in as many words
   that a MAC is not a credential (it is printed on the box and
   broadcast in the clear in every Wi-Fi frame), and the same route
   answers a SUCCESSFUL claim with `device <mac> bound to <agent>`, on
   purpose and under a case that says why ("the thing the operator did
   not have to go and find"), which the simulator's own claim prints to
   stdout. What does hold is the convention: every other refusal in the
   configuration store names its condition without the value
   (`NO_SUCH_DEVICE`), these two were the exception, this is the one
   write here a caller reaches without sending the MAC, and its
   sentence travels further than any other in that file. So both become
   public fixed constants, `ALREADY_BOUND` and `ALREADY_COVERED`. The
   two existing pins moved from a substring of the old sentence to the
   whole constant with the address asserted absent, and two cases
   joined them: the superseded claim over the API body and the log
   records, and the same race met through the simulator's claim over
   stdout, stderr, the records and the exception chain. Bite:
   prefixing either refusal with `devices.{written}: ` again fails the
   simulator's case on the sentence assertion.

3. **P1: concurrent requests could undo the logger quieting.**
   `logs.quieted` recorded in its own docstring that two threads over
   the same names would restore each other's levels, as a limit rather
   than a defect, and `device_endpoint.requested` relies on it.

   *Resolution* (`f1fc5f0c`): the whole block, from raising the level
   to putting it back, is taken under one module-level reentrant lock,
   which is the mechanism the review prescribed. Reentrant so that a
   caller already inside one boundary opening another does not
   deadlock; what it costs is that two threads quieting the same
   loggers take turns, which is the shape both callers already have.
   The failure is a leak rather than a cosmetic one and the new case
   shows it whole: the first thread in saves the loud level, the second
   saves the quiet one, and the first OUT restores the loud level under
   a request the second is still making. Bite: with the lock removed
   the interleaving assertion fails, and past it the second request's
   URL is in a captured httpx record and both loggers end at WARNING
   rather than the INFO they started at.

4. **P2: the activation ceiling did not bound an in-flight poll.** The
   deadline was checked between polls while every poll carried the flat
   thirty-second read bound, so a valid `timeout_ms=6000` could still
   hold the first poll for thirty seconds. The existing case summed
   mocked sleeps and passed either way.

   *Resolution* (`aec95a1c`): the remaining budget is computed before
   each poll and passed to the request, and `device_endpoint` shortens
   that request's connect and read bounds to it, never lengthens them,
   which is the direction the rule about a far side's own number
   already runs in. The cap is set on the client the seam built, since
   the seam takes an address alone and a suite's replacement is
   entitled to build whatever client it likes. The suite's clock grew a
   hand for time spent inside an answer, because a mock transport
   answers instantly. Bite: with the budget dropped, the new case reads
   a thirty-second read bound off a poll made inside a six-second
   ceiling. The companion case (a transport that consumes the whole
   budget ends the burst) pins the property and says in its own
   docstring that it does not bite, since the check after an answer
   already stopped the burst there.

5. **P2: the identity case permitted the missing `Client-Id` it claimed
   to prohibit.** The poll sent the MAC alone, and the case accepted
   `{client_id, ""}`.

   *Resolution* (`be96626f`): the poll's headers are derived from the
   check-in's rather than written beside them, with the activation
   version added, which is what the firmware's own `Ota` does (one
   header block, every request under it) and what stops a second list
   being the first with something missing. The recording is held to the
   whole of it: every request of the ceremony carries exactly the
   derived MAC and exactly the derived client id. Bite: restoring the
   two-header poll fails at the second request, where the client id is
   the empty string.

6. **P2: the capability help said the firmware block is read and
   reported, and it was discarded.** The reviewer offered the fork
   explicitly; the maintainer's instruction named it too. **Modelling
   it was chosen over dropping the claim**, because decision 5 is about
   the table being true of a simulator that claims fidelity, the
   server's OTA reply does send the block on every check-in, and a real
   board reads it: that block is where a deployment says "you are up to
   date", by naming the version the board just reported with no URL, or
   offers an image by naming one. Dropping the claim would have made
   the simulator less faithful to buy the same honesty.

   *Resolution* (`f5e25013`): the block is modelled and read, and what
   crosses into the state is the two facts a board acts on rather than
   the strings, since what a board does with the block is decide and
   not display: an image was offered or it was not, and the version
   named back is this board's own or it is not. So a verdict says
   something true about it without repeating a word of it, and an
   address this simulator will never open is one it never holds. The
   table gains a supported row for the reading and keeps an unsupported
   row for the fetching, which is the half that stays false. `cli.md`
   and the spelling census follow the epilog. Bite: with the block
   discarded, three of the five rows of the new table report the wrong
   sentence.

One thing the round left behind rather than fixed, recorded because it
is a trap and not a defect: a prose row of the capability table whose
text begins `reading ` or `sending ` is read as a message row by the
both-ways pin, which tells the two halves of the wire apart by those
prefixes. The firmware row is worded around it with the reason beside
it.

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

After the review round, on `f5e25013`: `uv run ruff check .` clean;
`uv run pytest tests/unit -q -n auto --dist loadfile` 3904 passed, 19
skipped, nothing failed, the network-dependent case above included
this time; `uv run pytest tests/integration -q` 175 passed; `cli.md`
regenerated through the workflow's own procedure and the spelling
census regenerated, with `events.md`, `domain-config.md` and
`api-openapi.json` byte-identical, which is the closed move list this
milestone declared.
