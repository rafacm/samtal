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
  *Amended 2026-09-04 by
  [`2026-09-04-simulator-tokenless-admission.md`](2026-09-04-simulator-tokenless-admission.md)
  (#369).* The list was incomplete in both directions. It missed two
  configurations that reach the state (a deployment that could not read
  its own record of what is bound, and an unloaded agent named by
  `default_agent` rather than by a binding), and it named one reading
  that is not the state at all: a deployment with device authentication
  off admits the board and hands it the same empty token, which the
  reply now says outright and this half reads. The sentence is a named
  constant with the readings enumerated from the decision sites, and
  an interpretation that used to be `Unwelcome` is `Admitted`.
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

## M2: the conversation

PR #302.

### What landed

The websocket half of `vinga simulator`, in the order the commits tell
it: the framing reader first, then the wire's other direction, then the
audio, then the conversation and the verb that holds one, then the
tiers, then the lanes, then the documents.

- `protocol/framing.py` gained `frames(version, data)`, the reader for a
  run of frames stored as a file. Nothing on the wire needs it, because
  a websocket delivers one frame per message; the packaged utterance
  does, because a file of bare Opus packets has no boundaries at all.
  Version 1 is refused by a sentence of its own rather than by the
  unsupported-version one, since it is a version a single frame very
  much has and what it lacks is a length to walk by.
- `protocol/messages.py` gained the direction it only wrote: frozen
  models for the server hello, `stt` and `tts`, `parse_server_message`
  beside `parse_message` with the same boundary discipline, and the
  three builders derived from those models. The builders were
  transcribed byte for byte first, in a commit of their own, and are
  byte-identical after. `built(message)` became public with it, so the
  simulator's own device hello and `listen` are built by the module that
  owns what a control message is.
- `simulator/utterance.py` and `simulator/data/`: the packaged sentence
  as a run of version 2 frames with a manifest beside it, read on demand
  and never at import. `understood(manifest, asset)` sits under
  `packaged()` because they are two questions, which is also what lets
  the doctored-pair cases be written without reaching into the module.
- `tests/tools/utterance.py`: how the asset was made, checked in so it
  can be made again. It runs by hand and never in a lane.
- `simulator/conversation.py`: the eight-state machine with its
  transitions as a table, the two rules that make it a machine (an
  unexpected message is reported and advances nothing; every waiting
  transition is bounded), and the only `websockets` import anywhere in
  `src/`. The bounds are the server's own hello window for the open and
  the hello, read from `device/watchdog.py` rather than restated, and a
  local ceiling for the reply, which is the one wait with nothing on
  either side to derive it from.
- The `sim` extra, `websockets` and nothing else; the dev group naming
  `vinga-server[serve,sim]`; and the tier VOCABULARY grown to three in
  `tests/support/tiers.py`, which is the half that mattered: an extra
  missing from the wheel's own metadata would otherwise have passed
  every lane.
- The grammar: a second row on the noun, the same argument declarer as
  `check-in` (one URL, `--mac`, `--claim`), the firmware block reported
  in `run`'s own admitted block the way `_reported` reports it for
  `check-in`, and the gate deepened by its second caller. `_from_the_server_half` became
  `_from_an_installed_half(answered, missing)`, because the server half
  is somewhere you go and an extra is something you install, and a second
  copy of it would have been a second chance to get the ImportError
  containment wrong.
- The capability table's conversation rows flipped to supported in the
  same change that landed `run`, and the third side is now asserted
  EMPTY. The machinery stays, so a future row can be declared honestly
  rather than parked.
- The lanes. Unit: 23 conversation cases against a controlled peer,
  including the four handshake headers, the framing round trip at all
  three versions, the text-versus-binary rule, four out-of-order cases,
  both bounds, and the websocket half's no-leak inventory; 20 utterance
  cases including the manifest match and the decode through the server's
  own decoder; four new `run` cases beside the board's, for the two
  address rules and the states the two verbs disagree about; four new
  gate cases. Integration: the live lane drives `simulator run` against
  a real uvicorn for a real conversation, and
  `tests/integration/test_cli_simulator.py` starts at `Activating` and
  runs the whole four-step ceremony, with the bite that doctors the
  second reply's token back to empty and watches the server refuse the
  handshake with `no_token`. The wheel lane's `GATED` grew by one, and
  the tier lane gained the `[sim]` environment.

### Deviations

Four, each with the reason.

1. **No `force-include` for the asset.** The plan asks for a
   `force-include` entry beside the one that carries `examples/`. The
   asset lives at `src/vinga_server/simulator/data/`, INSIDE the package
   `[tool.hatch.build.targets.wheel] packages` already carries, and
   hatchling refuses the build outright with "A second file is being
   added to the wheel archive at the same path". `examples/` needs the
   entry because it is outside `src/`; this does not. What the entry
   would have declared is asserted instead, which is stronger: the wheel
   lane reads the built archive for both packaged names and hands the
   bytes to the same reader the command uses, and the tier lane's
   `[sim]` environment reads the asset through `packaged()` from an
   installed tier. A comment in `pyproject.toml` records the absence so
   the next reader does not re-derive it.
2. **The builders are `json.dumps` over `model_dump`, not
   `model_dump_json`.** The plan names the latter. Pydantic's serializer
   writes compact separators (`,` and `:`) and emits non-ASCII raw,
   so it would have rewritten every server-to-device message in the
   field while changing nothing about what any of them mean, and the
   byte-for-byte pin the same decision demands would have failed. The
   models are still the single home of the shape, which is the property
   the derivation was for; the encoder stays the one these messages have
   always been written by.
3. **The live lane's `simulator run` row holds a real conversation
   rather than reporting an unbound board.** Decision 7's table says it
   is "driven, against the same server, where it reports the unbound
   state and exits 0", on the premise that the shared server has no
   providers. That premise is about `tests/support/deployment.serving`'s
   own fixture; `test_cli_live.py` applies a document of mock providers
   and reloads it, and the case before this one claims the simulated
   board, so the board there is admitted and the deployment can answer
   it. Reporting an unbound board would also not have been exit 0: `run`
   was asked for a conversation, so the states `check-in` reports and
   exits 0 on are a refusal for this verb. The stronger case is the one
   that ran.
4. **The peer's cases are a unit suite, not an integration one.**
   Decision 7 puts the controlled peer in `tests/support/`, which is
   where it is, and does not say which lane drives it. It is driven from
   `tests/unit/test_simulator_conversation.py`, because it opens a
   loopback socket and nothing else: no database, no application, no
   providers. The integration lanes are the two that need a real
   vinga-server.

### Resolutions of what the plan left open

- **The asset's numbers**, which the plan deliberately did not invent:
  28 packets, 1680 ms, 10254 bytes, SHA-256
  `6edd0d5cb85ac5f983eb9275dad577ac01ed7e396fe2b6a43f42b66e44a50d77`.
  Inside the plan's expected range of 25 to 42 packets.
- **The licence string was re-read at the pinned tag before the asset
  was committed**, which is the residual mitigation the risk section
  asks for. `rhasspy/piper-voices` at `v1.0.0`,
  `en/en_US/ljspeech/high/MODEL_CARD`, gives the dataset licence as the
  exact string `public domain` over the LJ Speech Dataset. Unchanged
  from what decision 1a records, so no question was raised.
- **How the asset was produced**, so it can be produced again:
  `uv run python -m tests.tools.utterance` from `vinga-server/`. It
  downloads `en_US-ljspeech-high.onnx` and its `.json` from the pinned
  tag, runs Piper through `uvx --from piper-tts piper` (a transient
  environment, so a GPL-3.0 package enters no tier of anything),
  resamples 22 050 Hz to 16 000 through `av`, pads 120 ms of silence in
  front and at least 300 ms behind up to a whole packet boundary, and
  encodes through the server's own `OpusEncoder`.
- **The `sim` extra's install line was verified**, not assumed:
  `uvx --from "vinga-server[sim] @ git+<url>#subdirectory=vinga-server" vinga simulator run ...`
  was run against a `git+file://` URL of this branch and installed 23
  packages, the client closure plus `websockets`, and reached the
  network rather than the gate.

### Discoveries

- **A missing extra has to be simulated in two places.** The gate's own
  case passed alone and failed under `-n auto`, because another case in
  the same worker had already imported `simulator.conversation`. Evicting
  it from `sys.modules` was not enough either: `from
  vinga_server.simulator import conversation` reads the ATTRIBUTE the
  first import bound on the parent package when the cache is gone. Both
  have to go, and the suite says so where it does it.
- **The gate belongs in front of the request, not behind it.** The first
  shape ran the check-in first, so a bare install pointed at a real
  deployment would have checked in, claimed a board, sat through an
  activation poll and only then said it needed a websocket client. The
  gate now fires before anything goes out, which is also what lets the
  wheel lane drive the row at all.
- **The decoded utterance is a shade shorter than the encoded one.**
  libopus carries a few milliseconds of encoder lookahead that the
  decoder skips at the start of a stream, so the decode case's tolerance
  is one packet rather than a millisecond; a tighter comparison would
  have been a comparison to the codec's internals.
- **`websockets` is the one distribution here that arrives both ways.**
  It is a `sim` root and a transitive dependency of
  `uvicorn[standard]`, which is why the negative checks ask the
  interpreter as well as the metadata: a tiering mistake shows up as an
  importable module before it shows up as a declared one.

### The microphone tier

Not in scope, per the plan's unsupported list, and filed by this
milestone as **#301**. What that issue has to decide rather than assume
is written into it: which tier the audio stack lands in and what that
does to the three-set inventory in `tests/support/tiers.py`, what the
non-interactive path is for a thing that is inherently interactive, what
a runner with no audio device can prove, and whether decoding the reply
comes with it (it is on the permanent unsupported list for the same
reason, which a tier carrying `av` would remove). Barge-in stays out
whatever it decides. The reasons that keep the tier off the list are
recorded in `simulator/capabilities.py` and stay there.

### The rebase onto M1's fix round

M1 merged with a six-finding review round these nine commits were
written before, so they were rebased onto it rather than merged with it.
Four conflicts, each resolved by intent:

- **`capabilities.py` auto-merged and was checked rather than trusted.**
  The fix round added a SUPPORTED row for reading the reply's firmware
  block and re-worded the permanent unsupported row to be about fetching
  one. Neither was on the third side, so this milestone's flip is
  orthogonal to both, and the assertion that retires the third side
  still holds: after the flip the table is supported and unsupported and
  nothing else.
- **`config/cli.py`**: the fix round rewrote `_reported`'s signature to
  take the endpoint (activation fields now cross through
  `Endpoint.repeated`, a redaction door) and added `_firmware`. Both
  kept, with `run`'s own admitted block gaining the same firmware line,
  because a verdict that reported the block for one verb and dropped it
  for the other would be the help true of half the noun.
- **`tests/unit/test_simulator_board.py`**: the fix round turned
  `stopped_clock` from a list into a record with a `slept` field and
  added the poll-budget cases; this milestone appended a `run` section
  after the last case. Both kept, and the two stale assertion lines this
  milestone carried at the seam were dropped for the fix round's own
  copies of them.
- **`cli.md` and `command-spellings.txt`** are generated, so both were
  resolved by regenerating rather than by merging hunks: the reference
  tail through the workflow's own procedure at each conflicting commit,
  and the census once at the end.

Nothing in `board.py`, `device_endpoint.py`, `logs.py` or
`config/store.py` conflicted: this milestone touches none of them, and
`conversation.py` calls `logs.quieted` through the same signature the
RLock serialization kept.

### PR review round

External review of PR #302, 2026-08-25. Backend: codex CLI, model
`gpt-5.6-sol`, read-only sandbox, over `main...d239b5ec`. Verdict as
received: mergeable after the listed fixes. Seven findings, three P1 and
four P2. All seven were verified against the tree before being fixed and
all seven are adopted; each is its own commit, and each fix that is
about a leak or a bound carries the bite that proves it in its commit
body.

1. **P1: audio-send failures escaped as library tracebacks.** Confirmed:
   the utterance went out through a bare `socket.send`, the only send in
   the module outside the containment. Fixed by `_send_audio`, a second
   name for the same guard, so text and binary stay different calls. The
   bite is the shape of the leak: reverted, the case does not raise
   `ConfigError` at all but a `ConnectionClosedError` whose own message
   reads "received 1011 (internal error) sk-closereason-...", so the
   peer's close reason reaches the chain verbatim. Getting the failure
   onto an audio frame at all took two deliberate halves, both recorded
   in the case: the peer waits for the first binary frame before closing,
   because a peer that closed after greeting fails the `listen start`
   instead, and the pacing hook waits for that close to complete.
2. **P1: unexpected traffic could extend the hello wait forever.**
   Confirmed: the bound was on each READ, so a peer sending one frame
   just before each window came due restarted it every time. One
   deadline now, computed on entering the state, which is what the
   reply's own wait had done since it was written. The bite is timed
   rather than asserted about a value: reverted, the case takes 6.98
   seconds instead of 1.1 and ends with the wrong sentence. The peer's
   chatter is bounded rather than endless on purpose, because a bug that
   hangs is a bug a runner cannot report.
3. **P1: the logging guard still admitted library tracebacks.**
   Confirmed against the locked library:
   `websockets/sync/connection.py` calls `self.logger.error(...,
   exc_info=True)` from four reachable paths, and an ERROR record clears
   a WARNING floor. The connection is now GIVEN a disabled,
   non-propagating logger private to this module; the floor stays for
   what a logger cannot reach, which is anything the library says under
   its own module names outside a connection this module opened. The
   bite runs in both directions: the exact call the library makes reaches
   nothing through the connection's logger, and the same record on
   `websockets.client` INSIDE the old floor lands with its traceback, so
   a floor that stopped admitting it would fail as a stale bite rather
   than pass quietly.
4. **P2: a server hello without `audio_params` was accepted.**
   Confirmed, and the finding's own caution was answered before the
   field was required: `server_hello` takes the parameters as an
   argument rather than defaulting them and the server's only call site
   passes its own `OUTPUT_AUDIO`, so no vinga-server can send a hello
   this now refuses. The fields INSIDE the block keep their defaults,
   which is a different question and is asserted beside the fix. The
   least-valid payload moved with it and a controlled-peer case covers
   the omission.
5. **P2: audio before `tts start` was counted as reply audio.**
   Confirmed: `(awaiting reply, audio)` was in the table. Removed, so a
   frame from before the reply began is a surprise and reaches no count.
   Bite: with it back, the case reads 2 packets and 20 bytes where it
   should read 1 and 10, and records no surprise at all.
6. **P2: the packaged utterance was validated after network and claim
   side effects.** Confirmed: a build that could not speak still rebound
   the device and sat through the activation ceremony before finding
   out. The read moved up beside the extra's gate, which gives the
   command an order worth naming: what is installed, then what was
   typed, then what the network says. Bite: with the read back below the
   claim, the case records four requests instead of none and refuses
   about an activation code rather than about the missing asset.
7. **P2: the advertised `closed` state was unreachable.** Confirmed: the
   socket was closed and the state left at `reply complete`, and the
   case pinned the contradiction. The close is now a transition through
   the same table, from `reply complete` and from nowhere else, so a
   close on the way out of a refusal advances nothing. A close that will
   not complete gets an outcome of its own, `CLOSE_FAILED`: the reply
   survives whole, the machine does not advance, and how the connection
   ended is reported as not this side's to say rather than guessed from
   a code nobody set. Bite: with the close swallowing its failure and
   advancing anyway, the case reports `closed` and "the session ended
   normally" about a connection whose close never finished.

Nothing was declined. `cli.md` is byte-identical after the round, since
no help text moved; the census moved and was regenerated.

### Verification

From `vinga-server/` on the milestone head, after the rebase onto M1's
fix round: `uv run ruff check .` clean; `uv run pytest tests/unit -q -n
auto --dist loadfile` **3998 passed, 19 skipped** (3966 before the
rebase, 3990 after it and before the review round; the 24 are the M1 fix
round's own cases arriving under this branch, and the 8 are this round's
seven fixes plus the census row the record's own quoted command adds);
`uv run pytest tests/integration -q` **190 passed**; the
openapi, events and domain-config drift checks byte-clean, with `cli.md`
the only generated document that moved and the census regenerated. The
image and the smoke lane are CI's to prove, and no run has yet been made
against a real ASR: every ASR in every lane is a mock that transcribes
whatever it is handed, so the suite proves the wire and cannot prove
intelligibility.
