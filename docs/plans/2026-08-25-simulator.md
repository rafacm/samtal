# Put a simulated board in the grammar

Issue #248, the last entry of the #265 CLI chain, planned against the
surface the #223 re-cut left behind. Deviations, resolutions and
discoveries land in the companion
`2026-08-25-simulator-implementation.md`, one section per milestone,
appended in the change that ticks the milestone.

## Goal

Trying vinga needs a board. The protocol a board speaks is HTTP and a
websocket, and every part of it is already written down in this
repository: `docs/xiaozhi-notes.md` records the exchange, and
`vinga_server/protocol/` models it. So the thing standing between an
operator and a conversation is not knowledge, it is hardware.

`vinga simulator` removes that. Two verbs join the noun-verb tree:

```bash
vinga simulator check-in https://host/x/AB2C4D5E/
vinga simulator run https://host/x/AB2C4D5E/
```

The first performs the device-side authentication alone and prints what
a board at that address would be handed. The second does that and then
holds a conversation: the websocket handshake, the hello exchange, one
utterance of packaged Opus, and the transcript and the reply sentences
printed as they arrive.

Both are rows of `cli.COMMANDS`, so both spellings resolve, both help
pages are the committed reference, and both join the acceptance lanes
the chain built. Neither is a second encoding of the protocol: the
messages, the framing and the message vocabulary come from
`vinga_server.protocol`, which is the module the server itself reads.

## The settled decisions, restated

Ruled by the maintainer on 2026-08-24 (issue #248, two comments) and by
the chain around it. Restated because a plan a fresh session reads has
to carry its own premises, not reopened here.

1. **The spelling is `vinga simulator`, not `vinga sim`.** Expressive
   over terse, per #285's naming stance. Under noun-verb the noun is
   `simulator` and its verbs are this plan's to shape.
2. **The simulator lives inside the CLI package.** One distribution,
   which is #223's ruling; the simulator is a subcommand of it and not a
   separate artifact.
3. **The help states what it supports AND what it does not**, both
   directions, on the help surface and therefore on the committed
   reference the drift lanes cover, so nobody debugs the simulator
   believing it is a board.
4. **It runs against a remotely deployed server, and two credentials are
   kept distinct.** Device-side: the real OTA check-in, whose primary
   input is the OTA URL, the same argument `vinga-server doctor` takes,
   and a plain `http://` address on a LAN is as ordinary here as it is
   there. Operator-side: connecting the simulated board to an agent
   rides the configuration API with the API secret taken the way the
   rest of this CLI takes it.
5. **The OTA URL alone still works**, up to the unbound activation-code
   state, which is itself a useful thing to simulate.
6. **The thin install stays thin.** Whatever the simulator needs is
   behind an extra or avoided; the default install remains the
   configuration client. The choice between a `[sim]` extra with a real
   encoder and pre-encoded bare-Opus fixture frames is left open by the
   issue and decided below, with reasons.
7. **The pipeline is the standing one**: committed plan, external plan
   review on the sol tier, per-milestone subagents in stacked worktrees,
   a PR per milestone with its own review round, merge-on-green.

## What the substrate already provides

Measured on `main` at `e4cfabca`, because a plan that guesses at the
tree it builds on is a plan that discovers its own premises during
implementation.

- **The protocol package is already client-tier pure.**
  `vinga_server/protocol/` is four modules, 524 lines, importing `json`,
  `struct`, `dataclasses`, `collections.abc` and pydantic and nothing
  else. `messages.py` models the device hello, `listen`, `abort` and
  `mcp`, and builds the server hello, `stt` and `tts`; `framing.py` owns
  the three binary framings; `mcp.py` owns the JSON-RPC envelopes. A
  simulator that reads them is not importing the server half, and a
  simulator that does not read them is a second encoding of the wire.
- **Framing version 1 is bare Opus.** `framing.wrap(1, payload)` returns
  the payload untouched (`protocol/framing.py:73`), which is what makes
  a pre-encoded packet playable with no codec at all. Versions 2 and 3
  prefix a packed header, `>HHIII` and `>BBH`, whose `payload_size`
  field is the only thing needed to walk a stream of them.
- **The check-in is one POST.** `OTA_PATH = "/xiaozhi/ota/"`
  (`ota/router.py:27`), registered on both the trailing-slash and
  slashless spellings because the firmware follows no redirect
  (`router.py:35-56`). The handler reads exactly two headers, `Device-Id`
  and `Client-Id` (`ota/reply.py:144-145`); it reads
  `application.version` and `board.type` out of a tolerantly parsed body
  and nothing else (`reply.py:120-137, 379-398`). The 200 carries
  `server_time`, `firmware`, `server`, `websocket {url, token, version}`
  and, for an unclaimed board, `activation {message, code, challenge,
  timeout_ms}` (`reply.py:253-284`, `onboarding/unbound.py:132-151`).
- **The websocket is four headers and a hello.** `WEBSOCKET_PATH =
  "/xiaozhi/v1/"` (`device/boundary.py:63`). `ws.py:89-112` reads
  `authorization`, `device-id` and `client-id`; **`Protocol-Version` is
  read by nothing**, and the framing version comes from the hello's
  `version` field alone (`device/session.py:395`). A bad token closes
  before accept, which uvicorn answers as HTTP 403 (`ws.py:119-132`).
- **The server's whole outbound vocabulary is four messages**: the
  server hello, `stt`, `tts` in three states, and `mcp` envelopes. There
  is no `llm`, no `system`, no `alert` and no `goodbye` anywhere in
  `src/`. Omitting `features.mcp` from the device hello means no `mcp`
  traffic happens at all (`session.py:839-848`).
- **`doctor` is the shape to copy for the device-side half.** It is a
  client of a device-facing address that opens no database, needs no key
  and sends no credential; it refuses to print the URL it was given,
  because that URL can be the deployment's secret `ota_path`; and
  `vinga-server doctor <url>` is deliberately NOT gated on the server
  half, while deriving a URL with no argument is (the #296 fix round).
  That division is the precedent the simulator inherits.
- **Two device-faking stacks already exist in tests, and neither can
  ship.** `tests/support/wire.py` hand-rolls the device with the
  server's own headers, hello, framing and `OpusEncoder`; the encoder is
  `av`, which is the `serve` tier. `tests/integration/conftest.py`'s
  `converse` and the six suites around it drive `xiaozhi-sdk`, a dev
  dependency whose own closure is fourteen distributions including `av`,
  `numpy`, `pillow`, `pydub`, `sounddevice` and `soundfile`. Neither is
  a candidate for a shipped command; both are evidence that the
  protocol risk is retired.
- **No pre-encoded audio exists anywhere in the tree.** A search for
  `*.opus`, `*.ogg`, `*.wav`, `*.pcm` and `*.raw` across the whole
  worktree returns zero files. Every suite synthesizes a 300 Hz sine in
  code and every ASR that hears it is a mock.
- **The lanes have named holes for a new row.**
  `tests/integration/test_cli_wheel.py:157` carries `GATED =
  frozenset({("openapi",), ("ota-url",)})` and asserts two-way
  completeness against `cli.COMMANDS`, so every new row needs a
  disposition. `test_cli_live.py:1228` holds one refusal per family
  against the same table, where a family is `row.words[:-1]` for a
  nested row. `tests/unit/test_cli_import_weight.py` pins the exact set
  of `vinga_server` modules `config.cli` pulls in.
  `tests/support/deployment.py` owns the shared live server and a
  `check_in` helper that is a hand-written copy of the POST this issue
  is about to ship.

## Decisions

### 1. An extra, and it is a websocket client, not a codec

The issue poses the packaging question as fixture frames against a
`[sim]` extra, on the premise that the audio stack is the only new
weight. **The premise does not hold, and measuring it is what decides
the question.** The client tier is `httpx`, `pydantic`,
`pydantic-settings`, `python-dotenv`, `pyyaml` and `typer`. None of them
speaks a websocket, and `httpx` has no websocket support at any version.
So a simulator that holds a conversation needs a new distribution
whatever it does about audio, and "no new extra" was never on the table
for the `run` verb.

Given that, the two halves of the issue's dichotomy are not
alternatives. They are two separate questions with two separate answers.

**The transport: a `[sim]` extra carrying `websockets` and nothing
else.** `websockets` 16.1.1 has zero dependencies, ships a
`py3-none-any` wheel of 174 kB, and is already in `uv.lock` because
`uvicorn[standard]` reaches it, so the tiering costs no new resolution.
It is the smallest new root this protocol can be spoken over. The
alternatives, in the order they were checked: `aiohttp` (a whole HTTP
stack for one upgrade), `httpx-ws` (a second httpx-shaped dependency
plus `wsproto`), and hand-rolling RFC 6455 over a socket (a masking and
fragmentation implementation nobody asked for, in a repository whose
whole posture is that the wire has one home).

**The audio: pre-encoded bare-Opus packets shipped as package data, and
no codec in any tier.** Framing version 1 sends the packet untouched,
so a packet encoded once at build time is a packet the wire accepts.
Adding `av` instead would be adding the single heaviest distribution in
the `serve` tier to a client command, for the sole purpose of encoding
a fixed sentence that never changes between runs; it would also make
the audio the simulator sends vary with the FFmpeg build on the
machine, which the determinism practice forbids of anything an artifact
carries. Pre-encoded packets are byte-identical on a laptop and on a
runner, which is what makes an audio path testable at all.

**What this costs, stated rather than discovered.** The simulator cannot
say anything but the one packaged sentence; that is on the honest
unsupported list (decision 5) with a follow-up. It cannot decode the
reply either, so what it reports about reply audio is arithmetic over
frames rather than sound. And the extra is a real tier: decision 8 names
every lane that has to learn about it.

**Where the fixture's packet boundaries come from.** A file of bare Opus
packets has no boundaries, because that is exactly what "bare" means.
Rather than inventing a container, the fixture is stored as a run of
**version-2 frames**, which are the framing this repository already
defines with a `payload_size` field, and `protocol/framing.py` gains
`frames(version, data)` to walk one. That module is the only place the
header layout lives today, and a simulator that unpacked `>HHIII`
itself would be the second home for it. The packets go back out under
whatever version the session negotiated, through the same
`framing.wrap` the server uses.

### 2. The grammar: one noun, two verbs, one positional

```
vinga simulator check-in URL [--mac MAC] [--claim AGENT]...
vinga simulator run      URL [--mac MAC] [--claim AGENT]...
```

**`simulator` is a noun with verbs, which is the ruled spelling, and it
is reconciled with the flat rule rather than excused from it.** The
guide seats a verb flat when it "acts on the whole deployment, or on
nothing stored at all", and its second group is document renderers that
have "no stored subject to be a verb of". Each of those is one act. The
simulator has a subject, the simulated board, which persists across
invocations as its MAC and which more than one verb asks about; two
flat words (`simulate`, `check-in`) would be exactly the top-level list
of things-and-actions that noun first exists to remove. Singular,
because each invocation is one board.

**The positional is the OTA URL, and there is exactly one of it.** The
maintainer's decision names it as the primary input and points at
`doctor`, which takes it the same way. The guide's identity rule does
not apply, because there is no address here to mirror: the check-in
carries the board's MAC in a header, not in a path, and the URL is what
names the deployment rather than a row of it. The precedent inside this
grammar is `schema provider asr faster_whisper`, whose positionals are
likewise not path parameters, on a command that reaches no
configuration API. Homogeneity holds: one positional, one kind, and
everything heterogeneous is a flag.

**There is no derivation behind the URL, deliberately.** The resolution
order the guide requires is flag, then environment, then a default
derived from configuration this deployment already has, and the third
step here would be `onboarding.origin`, which is the import that gates
`ota-url` and the doctor's no-URL branch on the server half. Inheriting
that gate would make the simulator's headline command refuse on the
very install it exists for. So the URL is required, and the help says
where it comes from: `vinga ota-url` inside the image, or the address
already written into the board's NVS. This is `doctor <url>` ungated
beside `doctor` with no URL gated, which #296's fix round already
decided; the simulator takes the ungated half and never grows the
other.

**`--mac` is derived, not persisted.** The issue's original shape
generated a fake MAC on first run and wrote it to disk so a binding
would survive restarts. This grammar holds no state between runs (clig
68, Adopted) and rejects a home-directory location outright (clig 78,
Rejected), so a state file is refused. The same property is bought by
derivation instead: the default is the fixed, documented
`02:00:00:00:00:01`, whose leading octet sets the locally-administered
bit, which is precisely what an address that was never assigned to
hardware should carry. It is the same every run, so a binding sticks; a
second simulated board is `--mac 02:00:00:00:00:02`; and nothing is
written anywhere. A MAC is not a credential (it is printed on the box
and broadcast in the clear in every Wi-Fi frame,
`docs/xiaozhi-notes.md`), and `device bind <mac>` already takes one on
the command line.

**Verb names, and what was rejected.** `check-in` is this repository's
own word for the act: `tests/support/deployment.py` calls the helper
`check_in`, the events are `ota_check`, `activation_complete` and
`activation_pending`, and `xiaozhi-notes.md` says "OTA check-in"
throughout. The guide's noun rule ("a noun is the configuration's own
word for the thing") applied to the verb slot is what produced
`preview` in the re-cut, and it produces this. It is a verb phrase, and
it is hyphenated only because a command word may not carry a space,
which Heroku 7 licenses. `probe`, `connect` and `identify` were
considered and rejected as words this repository does not use for this
act. `run` is the plainest verb for holding a session and it is what a
person says; the core set (`set`, `show`, `export`, `delete`, `list`)
does not reach either act, which is the case the guide makes for
noun-specific verbs.

**No third verb for the capability listing.** The recorded decision puts
the honest capability statement on the help surface, and help is where
it stays: a `simulator capabilities` verb would be a second way to read
a page `--help` already prints, which is two words for one act.

**Neither verb destroys anything**, so neither row carries `destroys`.
`--claim` is a rebinding, and decision 7c of the re-cut plan already
ruled that rebinding is not destructive: it is an overwrite the API
acknowledges, reversible by rebinding, with `export` holding what was
there. Reviewer question 11 is answered by that ruling rather than by a
fresh judgement.

**Global options.** `--config` and `--api-url` apply, because `--claim`
reaches the configuration API. `--force` and `--no-input` are offered
because they are offered everywhere (M1 deviation 3 of the re-cut) and
are inert here, since neither verb prompts and neither destroys.
`--version` is the root's alone, as it is for every row.

### 3. Two credentials, two transport policies, one of them extracted

The recorded decision keeps the two credentials distinct. The plan
keeps their transport policies distinct too, because they are genuinely
different rules and blurring them is how one of them gets weakened.

**Device-side: no credential at all, and a device-facing address
policy.** The OTA endpoint is the token issuer, so it cannot require a
token; a board presents a MAC in a header and nothing else. The address
policy is therefore `doctor`'s and not the configuration client's:
`http://` is permitted to any host, because that is exactly what a board
on a LAN is pointed at, whereas `config/cli.py`'s `_permitted` refuses
plain HTTP off loopback because the API bearer token crosses every
request. Two policies, two reasons, and neither is the other's default.

That policy exists once today, as `doctor._device_url` and
`doctor.SUPPLIED_ENDPOINT` (`doctor.py:65, 350-402`). A second copy in
the simulator would be a duplication of exactly the surface the no-leak
discipline governs, so **it is extracted into a new module,
`vinga_server/device_endpoint.py`**, read by `doctor` and by the
simulator. It owns: the parse, the scheme and host check, the userinfo
refusal, the bound-and-printable display form, the stand-in name a
verdict uses instead of the URL, and a small frozen `Endpoint(reached,
shown)`.

`config/cli.Address` is deliberately NOT reused, and the reason is
recorded so it is not rediscovered. Moving `Address` down out of
`cli.py` is #287's territory (the CLI's types stay behind the shapes a
generated client would emit), nothing under `vinga_server` may import
`config.cli` except `main.py`, and the two types answer different
questions anyway: `Address` carries a query the API client composes
paths onto, while an `Endpoint` carries a device-facing base a single
POST is made to.

The extraction is proven behavior-preserving before anything new is
added, by the existing `tests/unit/test_doctor.py` cases running
unchanged against the moved function. That is the pin-before-reshape
discipline applied to the one thing in this issue that moves.

**Operator-side: `--claim`, and it calls the act the grammar already
has.** With `--claim assistant` the simulator, after a check-in that
produced an activation code, performs the claim; the API address
resolves through `_address` (`--api-url`, then `VINGA_API_URL`, then
loopback on the port the file half names) and the bearer token through
`_token`, from the variable `server.api.secret_env` names, which
defaults to `VINGA_API_SECRET`. **It sends no new request.** It performs
`ADD_DEVICE`, the same `Act` that backs `vinga device pending claim`, so
there is no second encoding of the claim, no new path, no new body and
no new row in the contract check's covered set.

Three properties of that seam are load-bearing and each is a case:

- **Without `--claim`, no API token is read and no API request is
  made.** The device-side half never touches the operator-side
  credential, which is what "kept distinct" has to mean to be worth
  saying.
- **The credential is never an argument.** There is no `--api-secret`
  flag and there will not be one; see the tension recorded in decision
  9.
- **Nothing happens implicitly.** Without the flag, the simulator prints
  the code and names the command to run, which is clig 26 and TW 4 as
  this grammar already answers them. A simulator that bound itself by
  default would be a command that writes to the configuration store as a
  side effect of pretending to be a board.

**The activation poll is the device's, not the operator's.** After a
code is displayed, a real board polls `<ota_url>/activate` in bursts of
ten, three seconds apart, with `Activation-Version: 1` and a body of
`{}`, which is what a consumer board with no eFuse serial number sends
and what upstream's manager-api reads nothing of. The server answers 202
until the MAC resolves to a servable agent and 200 once it does
(`ota/poll.py:41-101`). `run` reproduces that cadence, bounded by the
server's own `activation.timeout_ms` (30 000 ms,
`onboarding/unbound.py:38`) rather than by a number invented here, and
reports which of the two answers it got.

### 4. The check-in's answer is a closed set of four states

The decision site the whole device-side half turns on is "what was this
board told", and the reply does not say it in one field. The notes
record why this matters more than it looks: a board whose MAC is not
bound still gets `200 OK` with an empty `websocket.token`, "so a board
that provisions perfectly and then never speaks is this, not a network
fault", and the advice is to treat an empty token as a hard provisioning
error rather than connecting anyway.

So `simulator/board.py` reads the reply into a closed tagged result and
the two verbs branch on it. Four states, and the fourth is the one that
costs people an evening:

| State | The reply | What it means |
| --- | --- | --- |
| `Activating` | `activation` present, token empty | unclaimed; the code, the message and the challenge, exactly as the screen would show them |
| `Admitted` | token non-empty | bound; the websocket URL, the token's presence (never its value) and the protocol version |
| `Unwelcome` | no `activation`, token empty | it checked in and it may not speak: onboarding is off, or nothing resolves this MAC and no default agent covers it |
| `Refused` | 4xx with the endpoint's fixed sentence | the request itself was rejected |

`Unwelcome` names the trap rather than reporting a success, and its
sentence says the two configurations that produce it, which is the
diagnosis the notes say nothing in the reply gives. A closed set at the
decision site is the standing lens; a boolean "did I get a token" would
have folded the fourth state into the first.

### 5. What the simulator is honest about not being

The capability statement is a deliverable, in both directions, and it is
**derived rather than written twice**. `simulator/capabilities.py`
declares one closed table; the help epilog is rendered from it, the
committed `cli.md` renders that epilog, and the test reads the same
table. A claim that appears in prose and not in the table is impossible
because there is no prose.

**Supported**, and this is the half a reviewer should hold against the
code:

- the check-in POST, with the two headers the handler reads and the body
  shape the firmware sends;
- the four states of the reply (decision 4);
- **no redirect is followed**, which is the firmware's own behavior and
  the reason every device-facing route serves the slashless spelling
  directly. `xiaozhi-notes.md` records redirect intolerance as "the one
  firmware behavior the simulator could not have shown"; that sentence
  is about the sdk-based test simulator, and this one can show it, so
  the note gains a clause;
- the activation poll at `Activation-Version: 1`, in the firmware's
  cadence, bounded by the server's own envelope;
- the websocket handshake with `Authorization`, `Device-Id`, `Client-Id`
  and `Protocol-Version` (sent because the firmware sends it, and
  recorded as read by nothing);
- the hello exchange, at whichever framing version the OTA reply named,
  through `framing.wrap`;
- `listen` `start` and `stop` in `manual` mode;
- reading `stt`, `tts` in all three states, and binary reply frames:
  counted, size-checked and unwrapped, with the reply's duration
  computed from the frame count and the announced `frame_duration`;
- the close codes and the reasons the session closes for.

**Not supported in v1**, each with the reason that keeps it off the
list rather than a shrug:

- **A real microphone and speakers.** They need PortAudio through
  `sounddevice` and a runtime encoder, which is `av`; a push-to-talk
  loop has no non-terminal path at all, which the determinism practice
  requires of any interactive affordance; and no CI runner has an audio
  device, so it would ship as a headline feature no lane can drive.
  Filed as its own issue by M2's implementation section.
- **Anything but the one packaged sentence.** There is no way to supply
  your own audio in v1; `-f` is the fragment flag and giving it a second
  meaning here would be worse than the limit.
- **The wake word.** ESP-SR decides on the chip and the server takes no
  part in it; the simulator never sends `listen` `state=detect`.
- **Echo cancellation, barge-in and realtime mode.** The board's AEC
  quality is the number the whole barge-in gate stack is built around
  and it is invisible from the server. A simulator with no playback has
  nothing to cancel, so it can neither reproduce barge-in nor measure
  it, and realtime mode is the only mode barge-in exists in.
- **`auto` mode.** The device owns the listening mode; `auto` re-arms
  itself after each `tts stop` and that loop is a second turn-taking
  design.
- **`abort`.** It is what a PWR press sends mid-reply, and there is no
  interactive path to trigger it from.
- **The device's own MCP tools.** The hello omits `features.mcp`, so the
  server publishes no device tools for this board and sends no `mcp`
  envelopes; a simulated board has no volume, no screen and no battery
  to act on.
- **Firmware update.** The reply's `firmware` block is read and reported
  and never fetched; there are no partitions here.
- **MQTT and UDP.** vinga implements the websocket transport and
  promises no other, which is a bound of the compatibility promise
  itself.
- **`Activation-Version: 2` and its HMAC.** There is no eFuse key to
  compute one with, which is also true of every consumer board.
- **Decoding or playing the reply audio.** Decision 1.
- **The display, the captive portal and NVS.** The simulator is pointed
  at a URL rather than provisioned into one.

**The both-ways pin**, which is what makes the statement a test rather
than a paragraph:

1. Every message type in `protocol.messages`' modelled set appears in
   the table exactly once, on exactly one side. That set is
   `_MESSAGE_TYPES` today and becomes public in M1: a private name
   reached from another module is a fact with no home, which is the
   reasoning the re-cut's M2 applied to `untransportable`. A new
   device-to-server message the server learns to model therefore fails
   this test until the simulator classifies it.
2. Every message builder the protocol package exposes is likewise
   classified, so the read side is closed the same way the send side is.
3. Every entry renders into the help epilog, on the side it declares.
4. The unsupported half is non-empty and every entry carries a reason.
   An "honest" statement that lists nothing unsupported is the exact
   failure the decision exists to prevent, and nothing else in the suite
   would notice it.

### 6. Two milestones, and the device half goes first

Every merge to `main` is releasable, because the image publishes on
every push. Both orders satisfy that, so the cut is decided on what each
milestone leaves behind.

**M1 is the board and its check-in.** The noun, the `check-in` verb, the
MAC derivation, the endpoint extraction, the four states, the capability
table and its help, `--claim`, and every lane. It introduces no extra
and gates nothing, so a bare `uvx --from git+...` install gets all of it
and the wheel lane drives it for real.

**M2 is the conversation.** The `run` verb, the `[sim]` extra, the
packaged utterance, the websocket half, the gate and its sentence, and
the end-to-end lane.

The device half first, for three reasons.

- **It is the half that is releasable alone.** `simulator check-in`
  answers a question nothing in the tree can ask today: `doctor` GETs a
  device-facing address and reports what it is, with no MAC and no
  answer about a particular board. Shipping it is a real command, not
  half of one.
- **It carries both credential seams.** The operator-side seam is the
  delicate one and it belongs where the sentinels are being written
  anyway, not bolted onto the milestone that is busy with a websocket.
- **The extra is isolated to one milestone.** M2's diff is a
  dependency, a gate and a state machine; M1's is a command. Landing
  them together means a reviewer reads a tier change and a protocol
  implementation in one diff, which is the mistake the re-cut's decision
  1 recorded from the other side.

The counter-argument, recorded: the headline of the issue is a
conversation, and M1 does not hold one. Accepted. A milestone is the
unit of delay, and the alternative is a milestone that cannot be
reviewed in one sitting.

### 7. The lanes each new row joins, and the disposition of every one

The chain's own rule: the wheel lane's completeness is two-way, so a new
row is either driven or gated, and nothing is silently neither.

| Row | Live lane (`test_cli_live.py`) | Wheel lane (`test_cli_wheel.py`) | Tier lane (`test_tier_closure.py`) |
| --- | --- | --- | --- |
| `simulator check-in` | **driven**, against the shared live server, plus one refusal for the new `("simulator",)` family and the new sentences' sentinels | **driven** as the installed binary, ungated | driven in the client environment, because it is thin |
| `simulator run` | **driven**, against the same server, where it reports the unbound state and exits 0 | **gated**: joins `GATED`, asserted to print the fixed sentence and exit 1 from the bare wheel | driven in the new `sim` environment, where it answers |

Three things follow, and each is a deliverable rather than an
observation.

**The live lane is the security lane and it gets the sentinels.** It
runs client and server in one process so `Watched` reads log records,
unformatted arguments, extra attributes and exception chains, which is
where refusal leaks live. Every new sentence this issue adds is proven
there, on all four surfaces.

**The full conversation is proven in a lane of its own.** The shared
server in `tests/support/deployment.py` boots fileless with an empty
database and has no providers, so no agent is servable and every board
is unbound; that is correct for the CLI lanes and useless for a
conversation. M2 adds `tests/integration/test_cli_simulator.py` beside
the existing `test_device_simulator.py`, using the mock-provider harness
in `tests/integration/conftest.py` (`booted`, `running_app`), and drives
`simulator run` end to end: check-in, handshake, hello, the packaged
utterance, an `stt`, `tts` in three states, reply frames, and a clean
close. The live lane's own case remains the unbound one, which is a real
outcome and is what its completeness recording holds.

**The tier lane gains a third environment.** It holds an installed
environment to the exact recursive closure of `uv.lock` from each tier's
roots, in both directions. A `sim` extra that no fixture installs would
be an extra nothing proves, so a third environment is built from
`vinga-server[sim]` and asserted to be the client closure plus
`websockets` and nothing else, with `simulator run` answering from it.
That is where the gated command is proven to work when its half is
present, exactly as the serve environment proves `openapi` and
`ota-url`.

### 8. Everything else that has to learn about this

Named here rather than discovered, because each is a place where a
change of this shape has gone quiet before.

- **`tests/unit/test_cli_import_weight.py`** pins the exact set of
  `vinga_server` modules `config.cli` imports at module scope. It grows
  by `vinga_server.simulator` and its submodules,
  `vinga_server.protocol` and its three modules, and (M1)
  `vinga_server.device_endpoint`. Every one of them is client-tier pure,
  and the widening is a review event with a name, which is what that
  test is for. The `websockets` import lives inside `run`'s own arm and
  is not in the set.
- **The gate.** `cli._from_the_server_half` records an `ImportError`
  inside its handler and raises a fixed sentence outside it, so nothing
  relays a module path. It is generalized to take its sentence and gains
  a second caller rather than being copied, which is deepening an
  existing function instead of adding a pass-through beside it. The new
  sentence names the extra, quotes nothing, and is a fixed constant like
  every other sentence in this grammar.
- **`tests/support/deployment.py`'s `check_in`** is a hand-written copy
  of the POST M1 ships. It is rewritten to call the production board, so
  the lane's board and the shipped board are one structure. The other
  helper, `tests/support/checkin.py`, stays: its job is driving the
  route with hand-built and deliberately malformed bodies, which a
  production client would never send, and that is a different question.
- **The generated artifacts.** `cli.md` moves in both milestones,
  because the tree gains rows. `events.md`, `domain-config.md` and
  `api-openapi.json` must NOT move: the simulator emits no event, adds
  no model field and adds no route. That move list is asserted as a
  closed set at each milestone's drift run, the way the re-cut asserted
  its own.
- **The spelling census.** New `respell` matches appear for `vinga
  simulator ...` wherever the documentation quotes one, and the manifest
  regenerates. Every such spelling must name a command the registered
  tree has, which is the standing guard.
- **The contract check** is untouched by design: `--claim` performs
  `ADD_DEVICE`, an act already in the covered set, and the covered and
  excluded sets do not move. A case asserts that, so a future `--claim`
  that grew its own request fails the check rather than passing it.
- **The documentation.** M1: `cli.md` gains a simulator section under
  its hand-written head, `cli-guide.md` gains nothing (no rule changes,
  and the plan is held to that), `xiaozhi-notes.md`'s redirect sentence
  gains its clause, and `CHANGELOG.md` gets the entry. M2: the
  installation head names the `sim` extra as the third extra and says
  who it is for, the root README gains the no-hardware paragraph the
  issue's verification list asks for, and `CHANGELOG.md` gets the
  second entry. The rule is the re-cut's: a milestone ships the
  documentation for what it changed, because the image publishes on its
  merge.

### 9. One tension with a recorded decision, stated rather than resolved

The maintainer's decision reads: the operator-side convenience "rides
the config API with the API secret passed as a flag or taken from the
environment, the exact pattern the rest of the CLI uses."

**The two halves of that sentence do not describe the same thing, and
this plan implements the second.** The pattern the rest of the CLI uses
is: the API address is a flag (`--api-url`), then an environment
variable, then a derived default; the API secret is never a flag, it is
read from the variable `server.api.secret_env` names. There is no
`--api-secret` in this grammar and adding one is refused by the practice
"a credential is never an argument", by clig 54, and by the reviewer
checklist's question 7. Arguments land in shell history and in the
process list, where a value cannot be taken back.

So `--api-url` is a flag here, the secret is not, and the sentence's
"flag" reading is not taken. It is recorded rather than quietly dropped:
if the intent was literally a `--api-secret` flag, this is the decision
to reopen, and reopening it means reopening the practice.

## Module layout after the change

- `vinga_server/simulator/__init__.py`: **new (M1).** The package doc
  and what the grammar reaches. Imports nothing that is not client tier.
- `vinga_server/simulator/board.py`: **new (M1).** The simulated board:
  its identity (the derived MAC, the client id), the system-info body
  and headers a check-in sends, the POST, and the closed four-state
  reading of the reply. Deletion test: inlined into `cli.py` it would
  put the OTA protocol inside the grammar module and give two verbs two
  copies of one exchange; `tests/support/deployment.check_in` becomes
  its second reader in the same milestone, which is the proof it is not
  a pass-through.
- `vinga_server/simulator/capabilities.py`: **new (M1).** The closed
  capability table and its rendering. Deletion test: it is the one
  structure the help, the committed reference and the both-ways test all
  read; inlined, the claim would exist twice, once as prose and once as
  behavior, which is the duplication the whole decision exists to
  prevent.
- `vinga_server/simulator/conversation.py`: **new (M2).** The websocket
  half: the handshake headers, the hello exchange, the listen and frame
  sequence, reading until the reply ends or a bound expires, and the
  closed transcript it produces. The `websockets` import is here, which
  is why nothing above imports this module eagerly. Deletion test: its
  only other home is `cli.py`, which would then import a websocket
  client at module scope and end the thin install.
- `vinga_server/simulator/utterance.py`: **new (M2).** What the packaged
  utterance is: the package-data lookup, the frame walk, and the rate
  and duration the packets were encoded at, so the sender paces them.
  Deletion test: without it `conversation.py` holds an asset format
  beside a wire protocol, which are two responsibilities with two
  reasons to change.
- `vinga_server/simulator/data/`: **new (M2).** The packaged utterance,
  one file, carried into the wheel by a `force-include` entry beside the
  one that carries `examples/`.
- `vinga_server/device_endpoint.py`: **new (M1).** A device-facing
  address a person typed: the policy, the display stand-in, the fixed
  refusals and the `Endpoint` type. Two readers, `doctor` and the
  simulator.
- `vinga_server/doctor.py`: loses the policy and the stand-in, keeps its
  verdicts, imports both.
- `vinga_server/protocol/framing.py`: gains `frames(version, data)`
  (M2), the stream reader that is the only thing that knows a header's
  size.
- `vinga_server/protocol/messages.py`: the modelled message-type map
  becomes public (M1).
- `vinga_server/config/cli.py`: two rows, one `GROUPS` entry, one new
  `Invocation` field (`endpoint`) with `mac` and `agents` reused, the
  generalized gate (M2), and the simulator's own argument declarers.
- `vinga-server/pyproject.toml` and `uv.lock`: the `sim` extra (M2),
  the dev group naming it, and the package-data include.
- `tests/support/deployment.py`: `check_in` reads the production board
  (M1).
- `docs/reference/cli.md`, `docs/xiaozhi-notes.md`, `README.md`,
  `CHANGELOG.md`: per decision 8.

## Tests

**M1.**

- The four check-in states, each driven against a real server in the
  state that produces it: unclaimed with onboarding on, bound, checked
  in with nothing servable and onboarding off, and the endpoint's own
  400 for a malformed `Device-Id`.
- The capability table's four both-ways assertions (decision 5), each
  held to going red by removing one entry, by putting one on both sides,
  and by emptying the unsupported half.
- The MAC derivation: the default is stable across runs, carries the
  locally-administered bit, and a given `--mac` overrides it.
- `--claim`: it performs the act `device pending claim` performs, sends
  no other request, and reads no API token when the flag is absent.
- No-leak, on all four surfaces the existing sentinel cases use (stdout,
  stderr, the collected log records rendered whole, and the exception
  chain), with one distinct credential-shaped sentinel per field so a
  leak names its own source: the supplied URL including a secret path
  segment and a query string, the `--mac` value, each `--claim` value,
  the API token, and the body the endpoint answered with. The URL case
  is the load-bearing one, because the OTA path segment is itself the
  deployment's secret.
- The endpoint extraction proven behavior-preserving: every existing
  `test_doctor.py` case green against the moved function, unchanged.
- The live lane's new family refusal, its driven row, and the wheel
  lane's driven row.
- The import inventory widened, deliberately, with the new set written
  out.
- The closed artifact move list: `cli.md` moved, the other three
  byte-identical.

**M2.**

- The end-to-end conversation against the mock-provider harness: the
  handshake, the hello at the negotiated framing version, the utterance,
  an `stt`, `tts` start, `sentence_start` and stop, reply frames that
  unwrap, and a clean close.
- The gate: `run` from an environment without `websockets` prints the
  fixed sentence and exits 1, with a sentinel planted in the simulated
  `ImportError`'s message, since an import error's text is a module path
  and is the value most likely to be relayed by accident. The simulation
  is a meta-path finder, per `tests/unit/test_missing_server_half.py`,
  because a module resolved by name never reaches `builtins.__import__`.
- The fixture: `framing.frames` walks it, every packet is non-empty, the
  count and the announced frame duration multiply to the stated
  duration, and the bytes are identical on every machine.
- The framing round trip at all three versions, through the server's own
  `wrap` and `unwrap`.
- Every wait bounded, each with its constant and its reason, and the
  activation poll's bound derived from the server's own
  `activation.timeout_ms` rather than restated.
- The wheel lane's `GATED` set grown by one and its two-way completeness
  still exact; the tier lane's third environment.

## Risks

- **The packaged utterance's provenance.** It is audio committed to an
  MIT repository, and a synthesized clip inherits its voice model's
  licence. Mitigation: the utterance is the maintainer's own recording,
  or a clip from a permissively licensed voice recorded in
  `THIRD_PARTY_LICENSES.md`, and the choice is made before the file is
  committed rather than after.
- **A green lane does not prove a real deployment converses.** Every ASR
  in every lane is a mock that transcribes whatever it is handed, and
  the tree's synthesized audio is a 300 Hz sine. So the suite can prove
  the wire and cannot prove intelligibility. Mitigation: the packaged
  utterance is real speech rather than a tone, and the PR's verification
  list leaves the against-a-real-ASR box unchecked with a note until a
  human runs it. An unchecked box is information.
- **`websockets` is a new public dependency of a shipped command.** Its
  16.x API is stable and its closure is empty, but a client library is a
  client library. Mitigation: one call site, inside
  `conversation.py`, and a declared floor.
- **The extra multiplies the tier lane's environments.** M2 adds a third
  `uv sync --frozen` fixture, which the re-cut measured at roughly
  fifteen seconds each. Mitigation: one environment built once per
  module, and the measured cost recorded in the implementation doc the
  way M2 of the re-cut recorded its own.
- **The simulator can drift from the protocol it claims to speak.**
  Mitigation: it holds no copy of the protocol. The messages, the
  framing and the vocabulary are `vinga_server.protocol`, and the
  capability table is held against that package's own public sets, so a
  new message type breaks the help before it breaks a conversation.
- **Two sentences that must not leak are added to a surface that has
  leaked twice** (#289 and #290 were both found by holding the guide
  against `cli.py`). Mitigation: fixed constants only, exhaustive
  per-field sentinels, and the URL case written first because the OTA
  path segment is the one value in this issue that is a secret by
  design.
- **`--claim` is a write behind a command named "simulator".**
  Mitigation: it is opt-in by name, it performs an existing act, it is
  not destructive by the re-cut's own ruling, and a case asserts that no
  API request is made without it.

## Milestones

- [ ] **M1: the board and its check-in.** Decisions 2, 3, 4, 5 and 8:
  the `simulator` noun with `GROUPS` entry; `check-in` with its URL
  positional, `--mac` and its derived default, and `--claim`; the
  four-state reading of the reply; `device_endpoint.py` extracted from
  `doctor` and pinned by doctor's own cases; the capability table with
  its four both-ways assertions rendered into the help and into
  `cli.md`; the modelled message-type map made public; `--claim`
  performing the existing act with the existing address and token seams;
  the live lane's driven row, family refusal and exhaustive sentinels;
  the wheel lane's driven row; the import inventory widened;
  `deployment.check_in` rewired to the production board; the closed
  artifact move list; `cli.md`'s simulator section,
  `xiaozhi-notes.md`'s redirect clause and the changelog entry. Design
  footprint: two new modules under `simulator/` (`board.py`,
  `capabilities.py`), each with its deletion-test justification above,
  plus `device_endpoint.py`, which exists because one policy with two
  readers may not have two homes; deepens `config/cli.py` by two rows
  and one `Invocation` field, and `protocol/messages.py` by making one
  fact public. No extra, nothing gated.
- [ ] **M2: the conversation.** Decisions 1, 5, 6 and 7: the `run` verb;
  the `sim` extra carrying `websockets` and the dev group naming it; the
  packaged utterance and its `force-include`; `framing.frames`;
  `conversation.py` and `utterance.py`; the generalized gate and its
  fixed sentence; the wheel lane's `GATED` set grown by one with two-way
  completeness intact; the tier lane's third environment; the end-to-end
  conversation lane against the mock-provider harness; the microphone
  tier filed as its own issue; `cli.md`'s installation head, the root
  README's no-hardware paragraph and the changelog entry. Design
  footprint: two new modules under `simulator/`, one of which holds the
  only `websockets` import anywhere in `src/`; deepens
  `protocol/framing.py` with the stream reader that keeps the header
  layout in one place, and `config/cli.py`'s gate with its second
  caller rather than a copy of it.
