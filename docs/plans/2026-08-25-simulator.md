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

### 1a. The packaged utterance, chosen here rather than during implementation

An audio file committed to a public MIT repository is a licensing
decision, and the first draft left it as a fork ("the maintainer's own
recording, or a clip from a permissively licensed voice") to be resolved
by whoever implemented M2. That is the wrong place for it: one branch of
that fork puts a person's voice into a public repository, which the
project's own stance on what may be published rules out, and the other
was unverified. **It is chosen here, with the licence read rather than
assumed.**

**The source.** Synthesized with Piper from the voice
`en_US-ljspeech-high` in the `rhasspy/piper-voices` repository at tag
`v1.0.0`. Its `MODEL_CARD` names the LJ Speech Dataset as its data and
gives the licence as the exact string **`public domain`**, with no SPDX
identifier, no URL and no conditions. The dataset's own page says "This
dataset is in the public domain in the US (and most likely other
countries as well). There are no restrictions on its use" and, on
attribution, "As this work is in the public domain, you may use it
without attribution." The `piper-voices` repository card declares
`license: mit` over the weights themselves. Every link in that chain is
zero-obligation.

**The two alternatives, rejected with their licence strings.**
`en_US-lessac-medium`'s `MODEL_CARD` gives its licence as a URL to the
Blizzard 2013 Lessac agreement, which is a per-licensee, manually
verified, research-only click-through that excludes commercial use by
name and requires all copies to be destroyed on termination.
`en_US-libritts_r-medium` gives `CC BY 4.0`, which is usable but puts an
attribution obligation on everyone downstream of this repository, and
its card records that it was fine-tuned from the lessac checkpoint, so
it inherits the question above through its base.

**Why Piper's own licence does not reach the audio.** The GNU GPL FAQ's
`#WhatCaseIsOutputGPL` states that a program's licence does not cover
its output, with an exception for output that reproduces GPL-licensed
art or audio; what a Piper waveform reproduces is the voice model and
the input text, and the model is MIT weights over public-domain data,
so nothing GPL-licensed is copied into it. The question is narrower than
it looks in any case: the classic Piper engine is MIT, and only the
`piper1-gpl` rewrite behind today's `pip install piper-tts` is GPL-3.0,
because it embeds espeak-ng. This repository already carries `piper` as
an optional extra and the licensing rules already forbid it becoming a
core dependency; nothing here changes that, because the synthesis
happens once, offline, on a contributor's machine, and no Piper code
ships.

**No `THIRD_PARTY_LICENSES.md` entry is owed**, because no attribution
is required by any link in the chain, and that file is for notices that
must be preserved rather than for a record of everything ever used. The
provenance is recorded where it is useful instead: in the manifest
beside the asset and in M2's implementation section, as
`Synthesized with Piper using en_US-ljspeech-high (rhasspy/piper-voices,
MIT weights), trained on the LJ Speech Dataset (public domain).`

**The contract, fixed here so the asset cannot be regenerated into a
different thing.**

| | |
| --- | --- |
| the sentence | `Hello, can you hear me?` |
| sample rate | 16 000 Hz, which is the device side of `AudioParams` |
| channels | 1 |
| packet duration | 60 ms, which is `AudioParams.frame_duration` |
| codec | Opus, bare packets, no container |
| storage | a run of version-2 frames, walked by `framing.frames` (decision 1) |
| expected size | roughly 1.5 to 2.5 seconds, so 25 to 42 packets |

The sentence is deliberately neutral, short and self-describing: it
carries no personal detail, no place and no name, it is a plausible
thing to say to a board, and it is long enough for a real ASR to have
something to work with.

**The procedure is a checked-in tool, not a paragraph.**
`tests/tools/utterance.py` (beside `driver_times.py` and
`event_baseline.py`, which is where this repository keeps tools a person
runs by hand) downloads the pinned voice, synthesizes the sentence,
resamples to 16 kHz mono, encodes at 60 ms through `av`, and writes both
the asset and a manifest beside it. It runs on a contributor's machine
and never at install time, at import time or in a lane; `av` and `piper`
are dev-environment tools here, not tiers of anything.

**The manifest is committed with the asset** and records the packet
count, the total duration, the byte length and a SHA-256 of the file,
plus the provenance line above. A case asserts the asset matches its
manifest, so the asset cannot drift and cannot be replaced quietly.
The exact numbers are recorded there rather than guessed here; what is
fixed here is the contract they must satisfy, and the expected range,
so an asset that came out at four hundred packets is visible in review.

**And the packets are proven to be Opus, not merely well-framed bytes.**
M2 adds a case that feeds the packaged packets through the server's own
real decoder, which is `av` in the dev environment, and asserts they
decode to 16 kHz mono PCM of the manifest's duration. Without it the
whole audio path could ship carrying forty well-formed frames of
garbage, and every other case in the plan would still pass.

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

### 2a. The board's identity is two values, and both are derived

The MAC is half of a board's identity and the plan derived only that
half, saying `board.py` "owns the client id" and leaving what it is to
the implementation. That is not a detail: the OTA reply signs its token
for the MAC **and** the client id together, and `ws.py:103` verifies the
token against both, so a client id that differed between the check-in
and the handshake would produce a `bad_token` refusal with nothing on
either side saying why. The re-check-in of decision 3 makes it worse,
because the same value has to survive four requests.

So the client id is derived by a stated rule, from the one value that
already identifies this board: **a UUID version 5 over the normalized
MAC under a fixed namespace UUID this repository owns**, so it is a pure
function of the MAC, stable across invocations with nothing written
anywhere, and distinct for two simulated boards. Version 5 rather than a
random version 4 because the whole property wanted here is determinism,
and rather than a hand-rolled hash because a UUID is what the header
carries and `uuid.uuid5` is in the standard library.

Normalized first, so `AA:BB:...` and `aa:bb:...` are one board rather
than two, which is the same normalization `ws.signed_device_id` applies
before it verifies. The namespace is a constant beside the rule, so the
mapping is reproducible from the source and a reader can compute it.

One value flows through all four requests of the ceremony and the
handshake after them, and that is the property the cases assert.

### 3. Three credentials, two transport policies, one of them extracted

The recorded decision keeps two credentials distinct. **There are
three**, and the third is the one a plan written from the operator's
side forgets, because it is never typed by anybody: the OTA reply mints
a **device token** (`ota/reply.py:273`), and the websocket verifies it
against the MAC and the client id before accepting the socket
(`ws.py:103`). Only the first POST is credentialless. Everything after
it carries a bearer token this CLI was handed, which is a credential in
exactly the way the API token is, and it is the harder of the two to
notice leaking because nobody put it on a command line.

So the inventory is three, and each is named with what it is and what
may never be said about it:

| Credential | Where it comes from | Where it goes | What may be printed |
| --- | --- | --- | --- |
| the OTA URL | the positional | the check-in and the activation poll | the stand-in name, never the URL: the path segment is the deployment's own secret |
| the device token | the check-in reply | the websocket `Authorization` header | that one was issued, never its value and never its length |
| the API secret | the variable `server.api.secret_env` names | the `--claim` request alone | the variable's name in a missing-value sentence, never the value |

**Device-side: the first POST is credentialless, and it uses a
device-facing address policy.** The OTA endpoint is the token issuer,
so it cannot require a token; a board presents a MAC in a header and
nothing else. The address policy is therefore `doctor`'s and not the
configuration client's:
`http://` is permitted to any host, because that is exactly what a board
on a LAN is pointed at, whereas `config/cli.py`'s `_permitted` refuses
plain HTTP off loopback because the API bearer token crosses every
request. Two policies, two reasons, and neither is the other's default.

That policy exists once today, as `doctor._device_url` and
`doctor.SUPPLIED_ENDPOINT` (`doctor.py:65, 350-402`), and it is not the
whole of what the doctor knows about talking to such an address: the
request itself sits inside a boundary that quiets the request loggers,
builds the client inside the try, refuses a redirect, catches
construction, request and close failures, names an exception by its
class alone and raises after the handler so nothing walks a chain back
to the URL (`doctor._probed`, `doctor.py:404-480`). **A simulator that
copied the address rule and then rebuilt that boundary from memory would
be duplicating the one surface in this issue where a mistake is a leak.**

So the extraction is **the address AND the request lifecycle**, into a
new module `vinga_server/device_endpoint.py`, read by `doctor` and by
the simulator. It owns:

- **A parsed endpoint, not two strings.** The first draft's
  `Endpoint(reached, shown)` was justified as a base for "a single
  POST", which is wrong twice over: the activation poll POSTs
  repeatedly to a path with `/activate` appended, and a supplied URL may
  carry a query string, so a string type offers no safe rule for where
  the segment goes. The type keeps the parsed parts, and composition is
  an operation on it: `endpoint.activation()` appends the segment to the
  PATH and carries the query and the fragment through untouched, which
  is the same discipline `Address.endpoint(path)` uses on the API side
  and the same reason it exists there.
- The parse, the scheme and host check, the userinfo refusal, and the
  bound-and-printable display form, which is what every sentence names.
- The stand-in name a verdict uses instead of the URL.
- **The request lifecycle**: one request, no redirect followed, the
  logger quieting, the client built inside the boundary, the close
  reported rather than raised, and every failure a fixed sentence naming
  a class and never a value. The doctor's GET and the simulator's two
  POSTs are three callers of one boundary rather than two
  implementations of it.
- The websocket URL rule of the paragraph below, since it is the same
  question asked of a different address.

`config/cli.Address` is deliberately NOT reused, and the reason is
recorded so it is not rediscovered. Moving `Address` down out of
`cli.py` is #287's territory (the CLI's types stay behind the shapes a
generated client would emit), and nothing under `vinga_server` may
import `config.cli` except `main.py`. The two types stay separate
because their transport policies are opposites: one refuses plain HTTP
off loopback and the other requires it to be allowed.

The extraction is proven behavior-preserving before anything new is
added, by the existing `tests/unit/test_doctor.py` cases running
unchanged against the moved function. That is the pin-before-reshape
discipline applied to the one thing in this issue that moves.

**The device token's own transport policy, which is a second address
this CLI did not type.** The check-in's answer names a websocket URL,
and that URL is far-side input: it decides where a bearer token this
process is holding gets sent. It is therefore validated before it is
used, by the same module and to the same standard as the supplied
endpoint, plus one rule of its own:

- it parses, names `ws` or `wss`, and carries a host;
- **it carries no userinfo**, which is refused outright, for the reason
  `_permitted` refuses it on the API address: a credential in a URL
  reaches shell history, process lists and access logs;
- **it may not downgrade.** A `wss://` endpoint reached over `https://`
  may not answer with a `ws://` URL, which is the check `doctor` already
  makes and calls out as a failure of its own
  (`doctor._plain_websocket`). A device token crossing a plain socket
  from behind TLS is the same mistake the API client has no flag to
  make;
- and it is never printed. What a verdict names is the fixed stand-in,
  the way the supplied endpoint is named.

**Nothing the far side wrote reaches a sentence.** That covers the
websocket URL above, the peer's close code and close reason, the body
of any answer, and every exception a websocket library raises: the
close code is a number this side compares against a closed set and
reports by its own name, the close reason is read and discarded, and a
library exception is reported by its class alone, recorded inside the
handler and raised outside it, the way `doctor._probed` does it. The
`stt` text and the `tts` sentences are the exception that proves the
rule: they are the artifact the command exists to print, and everything
that is not that content is a fixed local sentence.

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
(`ota/poll.py:41-101`). `run` reproduces that cadence, bounded per
decision 3a, and reports which of the two answers it got.

**A 200 from the poll is not a token, and the ceremony has a fourth
step the first draft skipped.** An activating check-in's token is empty,
and **the only thing that mints a token is a check-in reply**: the poll
route answers a status and nothing else. So `run --claim` is four steps,
not three, and the fourth is the one that makes the other three worth
anything:

1. check in, and read `Activating`;
2. claim, through the existing act;
3. poll `/activate` until 200 or the bound;
4. **check in again, with the same MAC and the same client id**, and
   require `Admitted`. The websocket URL and the token used to open the
   socket are that second reply's, never the first's, which carried
   neither.

That is what the firmware does, and the notes say so:
`Application::CheckNewVersion` polls in bursts and then "re-runs the
whole OTA check and re-displays whatever code the fresh response
carries" (`docs/xiaozhi-notes.md:223`). A simulator that opened a socket
with the empty token from step 1 would be refused at the handshake with
`no_token`, which is the exact confusion the notes warn about from the
other side. The same client id across all four steps is load-bearing,
because the token is signed for the MAC and the client id together
(decision 2a).

### 3a. Every wait is bounded here, and a far-side number may only shorten one

The guide's rule is that every wait has a bound and a wait that does not
says why where the constant is. This command waits more than anything
else in the grammar, so the bounds are written out rather than left to
the implementer, and one of them is a trap the first draft walked into.

**A far-side `timeout_ms` is not a bound.** The draft derived the
activation wait from `activation.timeout_ms` in the reply, calling it
"the server's own envelope rather than a number invented here". That
reasoning is wrong twice. The value is untrusted remote input, and
nothing about a JSON number stops it being negative, boolean, a string,
or large enough to hang the command for a week. And the server constant
it usually carries is not an envelope at all: `ACTIVATION_TIMEOUT_MS`
is the FIRMWARE's own default sent back to it, and
`onboarding/unbound.py:25-38` says in as many words that the firmware
parses it into a member no other line reads. Deriving a client bound
from it is deriving a bound from a value nobody uses.

So the rule, stated once and applied to every remote number this command
reads: **remote input may shorten a wait and may never extend one.** The
field is validated strictly as a positive integer, anything else is
ignored rather than refused (a malformed hint is not a reason to fail a
ceremony that works without it), and the result is capped by a local
maximum this repository chose, with its reason beside the constant.

The bounds, each with the reason that picked it:

| Wait | Bound | Why |
| --- | --- | --- |
| connect, every request | 5 s | the doctor's, and for its reason: an address nothing listens on must say so in seconds |
| read, check-in and poll | 30 s | the doctor's, and for its reason: the network a device sits on is the thing being diagnosed, and a slow answer is still an answer |
| the whole activation ceremony | local ceiling, the reply's `timeout_ms` only where it is valid and smaller | above |
| the poll cadence | ten polls, 3 s apart | the firmware's, and it is a cadence rather than a bound: `xiaozhi-notes.md` records the burst, and reproducing it is part of being faithful |
| websocket open and the hello reply | the server's own hello bound | the far side gives up there, so waiting longer learns nothing |
| the reply, `tts start` to `tts stop` | a local ceiling with the reason beside it | a model and a TTS have no bound this side can derive, and an unbounded wait is what `server.limits.idle_timeout_s` exists on the server to end |

Nothing in this command waits without one of these, and the one wait in
the grammar that deliberately has no bound, `apply`, has nothing in
common with any of them.

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

| State | Reached when | What it means | Exit |
| --- | --- | --- | --- |
| `Activating` | a valid reply whose `activation` is not None and whose token is empty | unclaimed; the code, the message and the challenge, exactly as the screen would show them | 0 |
| `Admitted` | a valid reply whose token is a non-empty string | bound; the websocket URL, the token's presence (never its value) and the protocol version | 0 |
| `Unwelcome` | a valid reply whose `activation` is None and whose token is empty | it checked in and it may not speak: onboarding is off, or nothing resolves this MAC and no default agent covers it | 0 |
| `Refused` | the endpoint answered and this client will not read the answer as a reply | the fixed sentence for the category, and nothing quoted | 1 |

**Amended 2026-09-04 by
[`2026-09-04-simulator-tokenless-admission.md`](2026-09-04-simulator-tokenless-admission.md)
(#369).** `auth.enabled` appears nowhere in this plan, and a deployment
that has turned device authentication off answers a board it admits with
the same empty token a board it will not admit gets. The `Admitted` and
`Unwelcome` rows above are therefore reached differently now: the reply
carries a top-level `access` word (`token`, `open`, `denied`), and where
it carries one this client knows, the word decides. `open` reaches
`Admitted` with an empty token, which is a state that state absorbs
rather than a fifth one beside it. A reply with no word, or one this
client does not know, is read exactly as the table says, which is what
an older server image gets.

`Unwelcome` names the trap rather than reporting a success, and its
sentence says the two configurations that produce it, which is the
diagnosis the notes say nothing in the reply gives. A closed set at the
decision site is the standing lens; a boolean "did I get a token" would
have folded the fourth state into the first. (The sentence's causes were
re-enumerated from the decision sites by the 2026-09-04 plan; the
diagnosis is the same shape and the list is longer.)

**The states are the outcomes; how a reply reaches one is a schema and a
precedence, and both are written down.** The first draft defined the
normal combinations and left everything else to be discovered, which is
how a truthiness check ends up standing in for a seam.

The schema is strict, in the shape `config/responses.py` is read with:
the reply is an object; `websocket` is an object; `websocket.url` and
`websocket.token` are strings where present; `websocket.version` is an
integer this side knows; `activation` is either absent or an object
whose `code`, `message` and `challenge` are strings and whose
`timeout_ms` is read per decision 3a. Unknown fields are ignored, so a
newer server stays readable. Anything that fails the schema is
`Refused`, not a state.

The precedence, in the order it is checked, so two readers cannot
disagree about a contradictory reply:

1. **transport and status first.** A redirect is `Refused` and is not
   followed; so is any 3xx, 4xx or 5xx, each by its own fixed category
   sentence, and a status is a number this side prints because it is
   this side's reading rather than the far side's words.
2. **the body must parse and must meet the schema**, or `Refused`.
3. **contradiction is refused, not resolved.** An `activation` present
   BESIDE a non-empty token is a reply no vinga-server produces, and
   guessing which half to believe is how a client teaches itself to
   accept a shape the server never promised. It is `Refused` with the
   fixed sentence for it.
4. **then, and only then, `activation is not None`** decides
   `Activating` against the other two, written as `is not None` and not
   as truthiness, because `activation={}` is an object that is falsy and
   is not an absent key. That distinction is the honest-seam lens and it
   is the one this table exists to make unwritable.
5. **then the token**, a non-empty string, decides `Admitted` against
   `Unwelcome`. A token that is not a string at all is a schema failure
   at step 2 rather than an empty one here.

**Step 5 amended 2026-09-04 by
[`2026-09-04-simulator-tokenless-admission.md`](2026-09-04-simulator-tokenless-admission.md)
(#369).** The reply's own `access` word decides where it carries one this
client knows, and the token decides where it does not, so a server too
old to say why a token is empty is read exactly as this step says. Step 3
grows with it: the word contradicts what stands beside it in three
combinations no server decision site can emit (a credential named beside
an empty token, none named beside a real one, and admission claimed
beside an activation object), and all three are `Refused` before step 4
asks about activation.

Every outcome has one exit code, pinned in the table above and in a
case: the three valid states are all exit 0, because a simulated board
reporting the state it is in is a command that worked, and `Refused` is
the grammar's one sentence and exit 1.

### 5. What the simulator is honest about not being

The capability statement is a deliverable, in both directions, and it is
**derived rather than written twice**. `simulator/capabilities.py`
declares one closed table; the help epilog is rendered from it, the
committed `cli.md` renders that epilog, and the test reads the same
table. A claim that appears in prose and not in the table is impossible
because there is no prose.

**Every entry carries the milestone it becomes true in, and the table is
read through that.** This is not bookkeeping: every merge is releasable
and the image publishes on it, so a table that landed in M1 advertising
a websocket handshake M2 has not written would be help that lies for the
length of a milestone, which is the exact failure the honest-capability
decision exists to prevent. So the table has three sides rather than
two, and the third is temporary by construction:

- **supported**, which M1 may claim only of what M1 ships;
- **not supported**, which is permanent and carries a reason;
- **not available yet**, which carries the verb that will bring it and
  is empty after M2.

M1 therefore ships every conversation row as "not available yet: the
`run` verb is not in this version", and **M2 flips those rows to
supported in the same change that lands `run`**, atomically, so no
commit exists in which the table and the tree disagree. The
third side is asserted empty at the end of M2, which is what stops it
becoming a place to park a claim. `cli.md` and the both-ways tests
therefore move in both milestones, which decision 8's artifact move list
already says.

**Supported after M2**, and this is the half a reviewer should hold
against the code:

- the check-in POST, with the two headers the handler reads and the body
  shape the firmware sends **(M1)**;
- the four states of the reply, decision 4 **(M1)**;
- **no redirect is followed**, which is the firmware's own behavior and
  the reason every device-facing route serves the slashless spelling
  directly. `xiaozhi-notes.md` records redirect intolerance as "the one
  firmware behavior the simulator could not have shown"; that sentence
  is about the sdk-based test simulator, and this one can show it, so
  the note gains a clause **(M1)**;
- the activation poll at `Activation-Version: 1`, in the firmware's
  cadence, bounded per decision 3a **(M1)**;
- **everything below this line is M2's, and M1's table says so of each
  of them.**
- the websocket handshake with `Authorization`, `Device-Id`, `Client-Id`
  and `Protocol-Version`. The last is sent because the firmware sends
  it and **this server reads nothing from it** (`ws.py:89-112`), so no
  real-server case can prove it was sent; what proves it is the
  controlled peer of decision 7;
- the hello exchange, announcing whichever framing version the OTA reply
  named. **The hello is a websocket TEXT frame**, as every JSON control
  message is; `framing.wrap` applies to binary audio and to nothing else
  (`device/session.py:406` against `:1130`);
- `listen` in `manual` mode, `start` and `stop` states only;
- reading `stt`, `tts` in all three states, and binary reply frames:
  counted, size-checked and unwrapped, with the reply's duration
  computed from the frame count and the announced `frame_duration`;
- the close, reported by the code compared against the closed set this
  side knows and named in this side's own words. The peer's close reason
  is read and discarded, because it is far-side bytes.

**Not supported in v1**, each with the reason that keeps it off the
list rather than a shrug:

- **A real microphone and speakers.** They need PortAudio through
  `sounddevice` and a runtime encoder, which is `av`; a push-to-talk
  loop has no non-terminal path at all, which the determinism practice
  requires of any interactive affordance; and no CI runner has an audio
  device, so it would ship as a headline feature no lane can drive.
  Filed as its own issue by M2's implementation section, which is #301.
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

1. **Every message type is classified at state and mode granularity,
   not at type granularity.** A row per `(type, state, mode)` the
   protocol declares, because `listen` alone holds supported `start` and
   `stop` beside unsupported `detect`, and `manual` beside unsupported
   `auto` and `realtime`. A type-level pin would have called `listen`
   supported and published a claim that is two thirds false. The
   enumeration comes from the models' own `Literal` members (decision
   5a), so a fourth listening state added to the protocol appears in the
   simulator's help as unclassified rather than as silently supported.
2. Every message the server can SEND is classified the same way, off the
   same models, so the read side is closed exactly as the send side is.
3. Every entry renders into the help epilog, on the side it declares.
4. The unsupported half is non-empty and every entry carries a reason.
   An "honest" statement that lists nothing unsupported is the exact
   failure the decision exists to prevent, and nothing else in the suite
   would notice it.
5. **Nothing is claimed supported that this milestone did not ship.**
   Every row marked supported names a verb the registered tree has, and
   every row marked not-available-yet names a verb it does not. In M1
   that puts every conversation row on the third side; in M2 the third
   side is asserted empty, which is what retires it rather than leaving
   it as a parking space. This is the assertion finding 2 exists for and
   it is the one that would have caught the original plan.

### 5a. The server's half gets models, the conversation gets a machine

`protocol/messages.py` models the device-to-server half and builds the
server-to-server half as raw `json.dumps` calls (`messages.py:160-189`).
That asymmetry is fine while the only reader is the server, which never
parses what it just wrote. It stops being fine the moment something in
this repository has to READ those messages, and the simulator is the
first such reader.

Making `_MESSAGE_TYPES` public, which is all the first draft proposed,
does not help with that half at all: it would leave `conversation.py`
hand-rolling `data.get("type") == "tts"` and `data.get("state") ==
"sentence_start"`, which is a second encoding of the wire in the one
module whose whole justification was that it holds none.

So **`protocol/messages.py` gains the other half**, in M2, beside the
half it has:

- **Frozen models for what the server sends**: the server hello with its
  `session_id` and its validated `audio_params`, `stt`, `tts` with its
  state as a closed `Literal`, and the `mcp` envelope. Immutable, and
  `extra="ignore"` like their siblings, so a newer server stays readable.
- **A parser beside `parse_message`**, with the same boundary discipline:
  a refusal naming the message type, where it broke and which rule it
  broke, and nothing that arrived. `_refusal` is written to be read from
  both directions already, and the reason it gives is this issue's
  reason too: pydantic renders a `ValidationError` with `input_value=`
  in it, so a server that put a credential where a `session_id` belongs
  would otherwise put it into a sentence.
- **The builders derived from the models rather than written beside
  them.** `server_hello`, `stt_message` and `tts_message` become
  `model.model_dump_json()` over the new models, which is what stops the
  models and the wire from disagreeing. This is the one production
  change to the server's own path in this issue, and it is
  behavior-preserving by construction: a case transcribes the three
  existing builders' output and compares it byte for byte, the way the
  prompt assembler's move was proven.
- **The public message inventory** the capability pin reads: the types,
  and each type's states and modes, off the `Literal` members rather
  than restated.

`_MESSAGE_TYPES` still becomes public in M1, because M1's capability pin
needs the send-side inventory and M1 adds no models. A private name
reached from another module is a fact with no home, which is the
reasoning the re-cut's M2 deviation 3 applied to `untransportable`.

**And the conversation's ordering is stated, not implied.** A simulator
that reads messages in whatever order they arrive is a simulator that
cannot say what went wrong. `conversation.py` owns one explicit state
machine, and the states are the ones the protocol actually has:

`opened` → `hello sent` → `hello received` (or the fixed refusal for a
malformed one, or the bound expiring) → `listening` (`listen start`
sent, frames sent, `listen stop` sent) → `awaiting reply` → `speaking`
(`tts start` seen; `stt` may arrive before or after it, and
`sentence_start` any number of times) → `reply complete` (`tts stop`)
→ `closed`.

Two rules make it a machine rather than a list. A message that arrives
in a state that does not expect it is reported by its type and state in
this side's own words and does not advance anything, which is what the
firmware does with JSON it does not understand. And every transition
that waits has a bound (decision 3a), so no state can be waited in
forever.

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

**Two protocol properties need a peer the real server cannot be.** The
real-server lane proves compatibility, which is the thing that matters
most and the thing only a real server can say. It cannot prove two
claims this plan makes. The `Protocol-Version` header is read by nothing
on the server side, so a conversation that succeeds says nothing about
whether the header was sent; and every adversarial answer the no-leak
cases need (a malformed hello, a `tts stop` with no start, a truncated
binary frame, a close carrying credential-shaped bytes, a returned
websocket URL with a userinfo in it) is a thing a correct server will
never do.

So M2 adds a **controlled peer** in `tests/support/`: a websocket
endpoint that records every handshake header it was given and answers
whatever the case tells it to. It is not a second server and it makes no
compatibility claim; it is the instrument the adversarial cases need,
and the real-server lane stays exactly where it is beside it. The
handshake header case reads the four headers off the peer's recording,
which is the only place that fact is observable at all.

**The redirect claim gets a case in the milestone that makes it.** M1's
capability table advertises that no redirect is followed, and the first
draft tested it nowhere. M1 adds an HTTP endpoint answering `307` with a
counted target, and asserts: exactly one request arrives, the target is
never fetched, no `Location` value appears on any of the four surfaces,
and the command answers its own fixed refusal and exits 1. A claim in
the capability table with no case behind it is the failure mode the
whole honest-capability decision exists to prevent, so this is the
pattern for every future row: the milestone that claims it tests it.

**The tier lane gains a third environment, and the tier VOCABULARY gains
a third entry.** The first is the obvious half and the second is the one
that matters. `tests/support/tiers.py` is the single home both lanes
read a tier from, and `declared()` returns exactly two sets today
(`tiers.py:57`), while the wheel pins only the bare block and the
`serve` block (`test_cli_wheel.py:366`). A `sim` extra declared in
`pyproject.toml` and missing from the wheel's metadata would therefore
pass every lane, which is precisely the gap the wheel's own metadata
check was added to close for `serve`.

So the whole inventory learns the word:

- `declared()` returns three sets rather than two, and `SIM_MODULES`
  joins `SERVE_MODULES` with `websockets`' import name pinned by hand,
  for the reason that map is written out rather than derived.
- The wheel's `Requires-Dist` is asserted both ways for the `sim`
  marker, as it already is for the bare block and for `serve`, and the
  no-undeclared-extra case picks the new extra up for free because it
  reads the declaration.
- The bare install is asserted to carry no `websockets`, as a
  distribution and as an importable module, which is the negative half
  the `serve` tier already gets.
- A third environment built from `vinga-server[sim]` is asserted to be
  exactly the locked client closure plus `websockets` and nothing more,
  with `simulator run` answering from it. That is where the gated
  command is proven to work when its half is present, exactly as the
  serve environment proves `openapi` and `ota-url`.
- The contributor and workflow comments that name `[serve]` alone are
  updated, and the `dev` group names `vinga-server[serve,sim]` so the
  checkout door stays one command, which is settled decision 3 of the
  re-cut and is a proof rather than an edit.

### 8. Everything else that has to learn about this

Named here rather than discovered, because each is a place where a
change of this shape has gone quiet before.

- **`tests/unit/test_cli_import_weight.py`** pins the exact set of
  `vinga_server` modules `config.cli` imports at module scope. It grows
  by the simulator modules the grammar names concretely
  (`simulator.board` and `simulator.capabilities`, and the empty
  `simulator` package they sit in), `vinga_server.protocol` and its
  three modules, and `vinga_server.device_endpoint`. Every one of them
  is client-tier pure, and the widening is a review event with a name,
  which is what that test is for. `simulator.conversation` is NOT in the
  set and a case says so, which is the same fact the gate depends on:
  the `websockets` import lives inside that module, reached only from
  `run`'s own arm.
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

### 9. The API secret rides the environment, and that is now ruled

The recorded decision of 2026-08-24 reads: the operator-side convenience
"rides the config API with the API secret passed as a flag or taken from
the environment, the exact pattern the rest of the CLI uses."

**The two halves of that sentence do not describe the same thing.** The
pattern the rest of the CLI uses is: the API address is a flag
(`--api-url`), then an environment variable, then a derived default; the
API secret is never a flag, it is read from the variable
`server.api.secret_env` names. There is no `--api-secret` in this
grammar and adding one is refused by the practice "a credential is never
an argument", by clig 54, and by the reviewer checklist's question 7.
Arguments land in shell history and in the process list, where a value
cannot be taken back.

The plan raised that as a tension and **the maintainer ruled on it on
2026-08-25: the environment half is confirmed, and there is no
`--api-secret` flag.** So `--api-url` is a flag here and the secret is
not, by decision rather than by this plan's reading, and the question is
closed rather than reopenable. It stays written down because a future
command will meet the same sentence and should find the ruling beside
it.

## Module layout after the change

- `vinga_server/simulator/__init__.py`: **new (M1), and empty of
  re-exports.** It carries the package's docstring and nothing else. The
  grammar imports the concrete modules by name
  (`from vinga_server.simulator import board`), because a package
  `__init__` that forwards names is the pass-through the design guide
  deletes: it would add a name, hide nothing, and make a reader open two
  files where one would have done. It also has a second cost here, which
  is why the rule is worth stating rather than following silently: an
  `__init__` that re-exported `conversation` would drag `websockets`
  into every import of the package, which is the gate defeating itself.
- `vinga_server/simulator/board.py`: **new (M1).** The simulated board:
  its identity (the derived MAC and the client id derived from it,
  decision 2a), the system-info body
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
  is why nothing above imports this module eagerly. Deletion test: it
  holds the protocol state machine of decision 5a, eight states with
  their transitions, their bounds and their out-of-order rule, and
  inlining that into `cli.py` would put a conversation protocol inside
  the module that owns the command grammar, where a reader looking for
  what `run` does would have to read past it to find anything else. The
  first draft justified it by import placement instead, which does not
  survive the test: an inlined function can import lazily just as
  easily, so that was an argument about where a line goes rather than
  about what a module knows.
- `vinga_server/simulator/utterance.py`: **new (M2).** What the packaged
  utterance is: the package-data lookup, the frame walk, and the rate
  and duration the packets were encoded at, so the sender paces them.
  Deletion test: without it `conversation.py` holds an asset format
  beside a wire protocol, which are two responsibilities with two
  reasons to change.
- `vinga_server/simulator/data/`: **new (M2).** The packaged utterance
  and its manifest, carried into the wheel by a `force-include` entry
  beside the one that carries `examples/`.
- `vinga-server/tests/tools/utterance.py`: **new (M2).** How the asset
  was made, checked in so it can be made again: the pinned voice, the
  synthesis, the resample, the encode and the manifest. Run by hand,
  never by a lane.
- `vinga_server/device_endpoint.py`: **new (M1).** A device-facing
  address a person typed, and what it takes to make a request to one:
  the parsed `Endpoint` with its `activation()` composition, the policy,
  the display stand-in, the fixed refusals, and the request lifecycle
  (one request, no redirect, the logger quieting, the boundary and the
  close). Three callers, the doctor's GET and the simulator's two POSTs.
  Deletion test: inlining it puts one address policy and one request
  boundary in two modules, and the two would drift on exactly the
  surface the no-leak discipline governs.
- `vinga_server/doctor.py`: loses the policy and the stand-in, keeps its
  verdicts, imports both.
- `vinga_server/protocol/framing.py`: gains `frames(version, data)`
  (M2), the stream reader that is the only thing that knows a header's
  size.
- `vinga_server/protocol/messages.py`: the modelled message-type map
  becomes public (M1); frozen models for the server-to-device half, a
  parser beside `parse_message`, the three builders derived from those
  models, and the public state-and-mode inventory the capability pin
  reads (M2, decision 5a). Deepened rather than split: it is the module
  that owns what a control message is, and owning only one direction of
  that was the asymmetry a second reader exposed.
- `vinga_server/config/cli.py`: two rows, one `GROUPS` entry, one new
  `Invocation` field (`endpoint`) with `mac` and `agents` reused, the
  generalized gate (M2), and the simulator's own argument declarers.
- `vinga-server/pyproject.toml` and `uv.lock`: the `sim` extra (M2), the
  dev group naming `vinga-server[serve,sim]`, and the package-data
  include.
- `tests/support/tiers.py`: a third declared tier and a third import-name
  map (M2), because it is the one home both lanes read a tier from.
- `tests/support/deployment.py`: `check_in` reads the production board
  (M1).
- `docs/reference/cli.md`, `docs/xiaozhi-notes.md`, `README.md`,
  `CHANGELOG.md`: per decision 8.

## Tests

**M1.**

- The four check-in states, each driven against a real server in the
  state that produces it: unclaimed with onboarding on, bound, checked
  in with nothing servable and onboarding off, and the endpoint's own
  400 for a malformed `Device-Id`. The exit code of each is asserted,
  per decision 4's table.
- **The reader's precedence, table-driven against hostile replies a real
  server never sends**, because real-server cases cannot reach any of
  them: `activation={}` (the case a truthiness check passes and
  `is not None` fails), `activation` present beside a non-empty token,
  a token that is a number, a missing `websocket` object, a `websocket`
  that is a string, an unknown protocol version, invalid JSON, an empty
  body, a 500, and a 307. Each names the outcome it must reach and the
  exit code it must leave with, and the `activation={}` case is held to
  going red if the seam is written as truthiness.
- The capability table's four both-ways assertions (decision 5), each
  held to going red by removing one entry, by putting one on both sides,
  and by emptying the unsupported half.
- The board's identity, both halves (decision 2a): the default MAC is
  stable across runs and carries the locally-administered bit, a given
  `--mac` overrides it, the client id is a pure function of the
  normalized MAC so two spellings of one MAC give one id, two MACs give
  two ids, and **one client id is carried by every request of the
  ceremony and by the handshake**, asserted off the recorded requests
  rather than off the function that makes it.
- `--claim`: it performs the act `device pending claim` performs, sends
  no other request, and reads no API token when the flag is absent.
- No-leak, on all four surfaces the existing sentinel cases use (stdout,
  stderr, the collected log records rendered whole, and the exception
  chain), with one distinct credential-shaped sentinel per field so a
  leak names its own source. **The inventory is the three credentials
  plus every far-side value that reaches this process**, and it is
  exhaustive rather than representative: the supplied OTA URL carrying
  both a secret path segment and a query string, the API secret, the
  **device token the reply issued**, the **websocket URL the reply
  named** including a userinfo it is refused for, the `--mac` value,
  each `--claim` value, and the body the endpoint answered with. The
  device-token case is the one a review would otherwise not think to
  ask for, because nobody typed it.
- **The activation ceiling, table-driven over hostile `timeout_ms`
  values**: absent, zero, negative, `true`, a string, a float, and a
  number large enough to hang the command. Each is asserted to leave the
  ceremony bounded by the local ceiling, and a valid smaller value is
  asserted to shorten it, which is the only direction remote input moves
  a wait. The ten-poll, three-second cadence is asserted exactly, on a
  clock the test controls.
- The endpoint extraction proven behavior-preserving: every existing
  `test_doctor.py` case green against the moved function and the moved
  boundary, unchanged.
- **A supplied URL carrying both a secret path segment and a query
  string, driven through both requests**, with the exact request target
  asserted for each: the check-in POSTs to the path as given with the
  query preserved, and the poll POSTs to that path plus `/activate` with
  the query still after it and the segment still before it. Every
  retained surface is checked for both the segment and the query, which
  is the case a two-string type could not have been given.
- **The redirect case** of decision 7: a `307` from a counted endpoint,
  one request made, the target never fetched, no `Location` on any of
  the four surfaces, the fixed refusal, exit 1.
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
- **`run --claim` from `Activating`, end to end, and it starts there
  deliberately.** The harness's board is unbound and no default agent
  covers it, so the run traverses all four steps of decision 3: the
  first check-in reporting a code and an empty token, the claim, the
  poll answering 202 and then 200, the SECOND check-in returning
  `Admitted` with a token the first reply did not carry, and only then
  the handshake and the reply. Held to going red by removing the
  re-check-in, which turns the handshake into a `no_token` refusal. A
  case that started from an already servable agent would pass with the
  bug in place, which is why the starting state is part of the
  assertion.
- The three existing builders proven byte-identical after being derived
  from the new models, by a case that transcribes their current output
  first, which is the pin-before-reshape discipline applied to the one
  server-path change this issue makes.
- The state machine (decision 5a) driven off its happy path: a message
  arriving in a state that does not expect it is reported and advances
  nothing; a `tts stop` with no `tts start` before it, an `stt` after
  the reply completed, and a binary frame before the hello are each a
  case.
- The control channel proven to be text: the hello, `listen` and every
  message this side sends go out as websocket text frames, and
  `framing.wrap` is asserted to be reached only for audio.
- The gate: `run` from an environment without `websockets` prints the
  fixed sentence and exits 1, with a sentinel planted in the simulated
  `ImportError`'s message, since an import error's text is a module path
  and is the value most likely to be relayed by accident. The simulation
  is a meta-path finder, per `tests/unit/test_missing_server_half.py`,
  because a module resolved by name never reaches `builtins.__import__`.
- The fixture against its committed manifest (decision 1a):
  `framing.frames` walks it, every packet is non-empty, the packet
  count, the byte length and the SHA-256 match the manifest, and the
  count times 60 ms is the recorded duration.
- **The packets decoded by the server's own decoder**, to 16 kHz mono
  PCM of the manifest's duration. This is the case that separates real
  Opus from well-framed garbage, and without it the whole audio path
  could ship carrying neither.
- **The controlled peer** of decision 7, and the two things only it can
  say: the four handshake headers read off its recording, including the
  `Protocol-Version` no server reads; and every adversarial answer the
  cases below need, which a correct server will never produce.
- **The websocket half's own no-leak inventory**, driven against that
  peer, on the same four surfaces: the device token never printed and never logged; a returned
  websocket URL carrying a userinfo refused and not shown; a `ws://` URL
  returned from an `https://` endpoint refused as a downgrade; a
  malformed server hello answered with a fixed sentence naming no
  field's value; and a peer close reason of credential-shaped bytes read
  and never relayed. Each of the five plants its own sentinel, and every
  websocket library exception is asserted to reach a sentence by class
  name with no `__context__` behind it.
- The framing round trip at all three versions, through the server's own
  `wrap` and `unwrap`.
- Every wait bounded, each with its constant and its reason per decision
  3a, asserted against that table rather than against the code that
  implements it.
- The wheel lane's `GATED` set grown by one and its two-way completeness
  still exact.
- **The tier vocabulary grown by one**: `declared()` returning three
  sets, `SIM_MODULES` pinned, the wheel's `sim` marker asserted equal to
  the declaration both ways, `websockets` asserted absent from the bare
  install as a distribution and as a module, and the `[sim]` environment
  asserted to be exactly the locked client closure plus `websockets`. A
  bite case doctors the expected set in each direction, the way the tier
  lane's own does, so the comparison is proven to reject what it claims
  to.

## Risks

- **The packaged utterance's provenance.** Decided in decision 1a rather
  than left as a risk: a zero-obligation chain, read rather than
  assumed. What remains is that a `MODEL_CARD` can be edited upstream,
  so the residual mitigation is that the implementer re-reads the card
  at the pinned tag before committing the asset and stops to ask if the
  licence string is not the one 1a records.
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

- [x] **[M1: the board and its check-in](2026-08-25-simulator-implementation.md#m1-the-board-and-its-check-in)** (PR #299). Decisions 2, 2a, 3, 3a, 4, 5,
  7 and 8: the `simulator` noun with its `GROUPS` entry; `check-in` with
  its URL positional, `--mac` and its derived default, and `--claim`;
  the board's identity, both halves, with the client id a UUID5 over the
  normalized MAC; the four-state reader with its strict schema, its
  five-step precedence, its `activation is not None` seam and one exit
  code per outcome; the bounds table, with the activation ceiling local
  and a remote `timeout_ms` able only to shorten it;
  `device_endpoint.py` extracted from `doctor` with the request
  lifecycle as well as the address rule, pinned by doctor's own cases
  and given the parsed `Endpoint` with its `activation()` composition;
  the capability table with its THIRD side, every conversation row
  marked not available yet and naming `run`, rendered into the help and
  into `cli.md`, held by five both-ways assertions; the modelled
  message-type map made public; `--claim` performing the existing act
  through the existing address and token seams; the redirect `307` case
  behind the claim M1 makes; the hostile-reply table; the live lane's
  driven row, family refusal and exhaustive sentinels over three
  credentials and every far-side value; the wheel lane's driven row; the
  import inventory widened; `deployment.check_in` rewired to the
  production board; the closed artifact move list; `cli.md`'s simulator
  section, `xiaozhi-notes.md`'s redirect clause and the changelog entry.
  Design footprint: two new modules under `simulator/` (`board.py`,
  `capabilities.py`) in a package whose `__init__` carries a docstring
  and no re-exports, plus `device_endpoint.py`, which exists because one
  address policy and one request boundary with three callers may not
  have two homes; deepens `config/cli.py` by two rows and one
  `Invocation` field, and `protocol/messages.py` by making one fact
  public. No extra, nothing gated.
- [x] **[M2: the conversation](2026-08-25-simulator-implementation.md#m2-the-conversation)** (PR #302). Decisions 1, 1a, 5, 5a, 6 and 7: the
  `run` verb, including the four-step ceremony whose fourth step is the
  second check-in; the `sim` extra carrying `websockets`, the dev group
  naming `vinga-server[serve,sim]`, and the tier VOCABULARY grown by a
  third entry in `tests/support/tiers.py` with the wheel's own `sim`
  metadata pinned both ways; the packaged utterance, its committed
  manifest, its `force-include` and the checked-in tool that makes it;
  `framing.frames`; `conversation.py` and `utterance.py`; the
  server-to-device models, their parser, the three builders derived from
  them and pinned byte for byte first, and the public state-and-mode
  inventory; **the capability table's conversation rows flipped to
  supported in the same change that lands `run`, with its third side
  then asserted empty**; the generalized gate and its fixed sentence;
  the wheel lane's `GATED` set grown by one with two-way completeness
  intact; the tier lane's third environment; the controlled peer and the
  two properties only it can prove; the end-to-end `run --claim` lane
  starting from `Activating`; the decoder case that proves the packets
  are Opus; the microphone tier filed as its own issue; `cli.md`'s
  installation head, the root README's no-hardware paragraph and the
  changelog entry. Design footprint: two new modules under `simulator/`,
  one of which owns the eight-state protocol machine and holds the only
  `websockets` import anywhere in `src/`; deepens `protocol/framing.py`
  with the stream reader that keeps the header layout in one place,
  `protocol/messages.py` with the direction it did not model, and
  `config/cli.py`'s gate with its second caller rather than a copy of
  it.

## Plan review round

External review of commit `5d11d316`, 2026-08-25. Backend: codex CLI,
model `gpt-5.6-sol`, read-only sandbox. Verdict as received: ready after
the P1/P2 amendments. Twelve findings, four P1, seven P2, one P3. All
twelve are adopted as prescribed; each amendment is its own commit with
a resolution note under the finding, and where the maintainer chose
between alternatives the choice is recorded with its reason.

1. **P1: the device token is missing from the credential and no-leak
   design.** Only the initial OTA POST is credentialless. Its response
   issues a device bearer token (`ota/reply.py:273`) which the websocket
   verifies against the MAC and the client id (`ws.py:103`). The plan
   calls the device side credentialless throughout and names only the
   API token in its sentinel inventory. It also proposes printing peer
   close reasons, which are arbitrary far-side bytes. Treat the returned
   device token as the second credential; define websocket URL
   validation, userinfo refusal and a transport policy that at least
   refuses an HTTPS-to-WS downgrade the way the doctor does; never print
   the token or a raw close reason; contain websocket library exceptions
   outside their handlers; and plant distinct four-surface sentinels for
   the API token, the device token, the OTA URL, the returned websocket
   URL and its userinfo, a malformed server hello, and the peer close
   reason.

   *Resolution* (this commit): decision 3 is re-headed "Three
   credentials" and opens with the inventory as a table: the OTA URL,
   the device token the reply mints, and the API secret, each with what
   may be printed about it. The device token gains a transport policy of
   its own, because the websocket URL is far-side input that decides
   where a token this process holds gets sent: it must parse, name `ws`
   or `wss`, carry a host, carry no userinfo, and never downgrade a
   `wss` endpoint reached over `https` to a plain socket, which is the
   check `doctor._plain_websocket` already makes. A new paragraph states
   that nothing the far side wrote reaches a sentence, naming the four
   sources (the returned URL, the close code and reason, any answer
   body, and every websocket library exception, reported by class alone,
   recorded inside its handler and raised outside it) with the `stt` and
   `tts` content as the stated exception, since that content is the
   artifact the command exists to print. M1's sentinel inventory becomes
   the three credentials plus every far-side value, exhaustive rather
   than representative, and M2 gains a websocket no-leak inventory of
   five cases. The capability table's close row stops promising to
   report a peer's reason.

2. **P1: M1 would publish capability help claiming M2 features already
   work.** The supported table includes the websocket handshake, the
   hello, listening, Opus, TTS and close handling, but M1 ships that
   table with `check-in` alone and every merge is claimed releasable.
   M1's table must classify only the shipped check-in surface as
   supported and all conversation behavior as not yet available; M2
   updates the same table atomically when `run` lands. `cli.md` and the
   both-ways tests move in both milestones.

   *Resolution* (this commit): adopted exactly as prescribed. Decision 5
   gives the table a third side, "not available yet", carrying the verb
   that will bring it, and states the rule it exists for: every merge is
   releasable and the image publishes on it, so a table advertising what
   the next milestone will write is help that lies for the length of a
   milestone. Every supported row now carries the milestone it becomes
   true in; M1 ships every conversation row on the third side, and M2
   flips them in the same change that lands `run`, atomically, so no
   commit exists in which the table and the tree disagree. A fifth
   both-ways assertion joins the four: nothing is claimed supported
   whose verb the registered tree does not have, and the third side is
   asserted empty at the end of M2, which retires it rather than leaving
   it as a place to park a claim. Both milestones' test paragraphs and
   both milestone entries carry the flip, and decision 8's move list
   already had `cli.md` moving in both.

3. **P1: the simulator has no reusable model for the messages it must
   receive.** `protocol/messages.py` models device-to-server messages
   only; the server-to-device half is raw JSON builder functions
   (`messages.py:160`). Making `_MESSAGE_TYPES` public therefore leaves
   `conversation.py` hand-rolling parsing and state strings for hello,
   `stt`, `tts` and `mcp`. The type-level capability pin is also too
   coarse: `listen` holds supported `start` and `stop` beside
   unsupported `detect`, `auto` and `realtime`. And the plan says the
   hello goes through `framing.wrap`, which is wrong: JSON controls are
   websocket text frames and only audio is wrapped
   (`session.py:406, 1130`). Add public immutable server-message models
   and a safe parser, derive the builders and the inventories from them,
   define the conversation's ordering state machine explicitly,
   classify capabilities at state and mode granularity, and state the
   text-versus-binary rule.

   *Resolution* (this commit): new decision 5a. `protocol/messages.py`
   is deepened rather than split: it gains frozen models for the
   server-to-device half, a parser beside `parse_message` with the same
   boundary discipline and the same reason (`_refusal` exists because
   pydantic renders `input_value=` into a `ValidationError`), the three
   builders derived from those models so the models and the wire cannot
   disagree, and the public state-and-mode inventory the capability pin
   reads. That builder change is the one production change to the
   server's own path in this issue and it is pinned byte for byte before
   it moves. `_MESSAGE_TYPES` still becomes public in M1, because M1's
   pin needs the send-side inventory and M1 adds no models. Capability
   pin 1 is re-cut to classify at `(type, state, mode)` granularity off
   the models' own `Literal` members, since a type-level pin would have
   called `listen` supported and published a claim two thirds false.
   Decision 5a also writes the conversation's ordering out as an
   explicit state machine with its two rules, an unexpected message is
   reported and advances nothing, and every waiting transition is
   bounded. The capability list's framing claim is corrected: the hello
   is a websocket text frame, as every JSON control message is, and
   `framing.wrap` reaches audio and nothing else.

4. **P1: the claimed activation path never obtains a usable token.** An
   activating check-in has an empty token, and tokens are minted only by
   a check-in response. The plan claims, polls `/activate` to 200, and
   then opens the websocket, never naming the second check-in in
   between; `xiaozhi-notes.md:223` records that activation loops back
   through the whole OTA check. The proposed end-to-end case can miss
   this entirely by starting from an already servable agent. After a 200
   poll, repeat the check-in with the same MAC and client id, require
   `Admitted`, and use that fresh URL and token; add an end-to-end
   `run --claim` case that starts in `Activating`.

   *Resolution* (this commit): adopted as prescribed. Decision 3's
   activation paragraph gains the step it was missing and states the
   fact that makes it necessary: the poll route answers a status, and
   the only thing that mints a token is a check-in reply, so
   `run --claim` is four steps and the fourth is a second check-in with
   the same MAC and the same client id, required to return `Admitted`,
   whose URL and token are the ones the socket is opened with. The
   firmware's own loop is cited from `xiaozhi-notes.md:223`, and the
   failure a missing fourth step produces is named: a handshake refused
   with `no_token`, which is the confusion the notes warn about from the
   other side. M2's test list gains the end-to-end `run --claim` case,
   starting from `Activating` deliberately, held to going red by
   removing the re-check-in, with the note that a case starting from an
   already servable agent would pass with the bug in place.

5. **P2: `Endpoint(reached, shown)` is too shallow for the actual work.**
   It is justified as a base for "a single POST", but activation POSTs
   repeatedly to an appended path, and a supplied URL may carry a query
   string, so a two-string type offers no safe rule for inserting
   `/activate` before that query. The doctor's request boundary also
   quiets the request loggers, catches construction, request and close
   failures and strips exception context (`doctor.py:404`), and the plan
   would leave the simulator to duplicate all of it. Deepen the shared
   module to own parsed endpoint composition and the whole request
   lifecycle, give it an activation-target operation, and test a secret
   path plus a query against both requests with exact request targets
   and every retained surface.

   *Resolution* (this commit): adopted as prescribed, and the module's
   scope grows with it. `device_endpoint.py` owns a PARSED endpoint
   rather than two strings, with `activation()` as an operation on it
   that appends the segment to the path and carries the query and the
   fragment through, which is the discipline `Address.endpoint(path)`
   already uses on the API side; and it owns the request lifecycle as
   well as the address rule, so the doctor's GET and the simulator's two
   POSTs are three callers of one boundary rather than two
   implementations of it. The paragraph names what that boundary carries
   (one request, no redirect, the logger quieting, the client built
   inside the try, the close reported rather than raised, and a failure
   named by class alone and raised after its handler) and says why
   rebuilding it from memory is the one duplication in this issue where
   a mistake is a leak. M1's test list gains the secret-path-plus-query
   case driven through both requests with exact request targets and
   every retained surface asserted, which is the case a two-string type
   could not have been given. The module layout entry and its deletion
   test are rewritten to match.

6. **P2: a far-side `timeout_ms` is not a real bound.** The plan derives
   the activation wait from the response's own `activation.timeout_ms`,
   which is untrusted remote input and can be huge, negative, boolean or
   malformed; the server constant is only the firmware's default echoed
   back, and the firmware reads it nowhere (`unbound.py:31`). Validate
   the field strictly and cap it with a documented local maximum: remote
   input may shorten the wait and may never extend it past the CLI's own
   ceiling. Test zero, negative, boolean, wrong-type and excessive
   values, and the exact ten-poll cadence.

   *Resolution* (this commit): new decision 3a. The rule is stated once
   and applies to every remote number this command reads: remote input
   may shorten a wait and may never extend one. `timeout_ms` is
   validated strictly as a positive integer, anything else is ignored
   rather than refused (a malformed hint is no reason to fail a ceremony
   that works without it), and the result is capped by a local maximum
   with its reason beside the constant. The draft's justification is
   retracted with the evidence the finding gives: `ACTIVATION_TIMEOUT_MS`
   is the firmware's own default echoed back, and
   `onboarding/unbound.py:25-38` says the firmware parses it into a
   member no other line reads, so deriving a client bound from it was
   deriving one from a value nobody uses. The decision writes out all
   six waits with the reason that picked each. M1's tests gain a
   table-driven case over absent, zero, negative, boolean, string, float
   and excessive values, plus the exact ten-poll cadence on a clock the
   test controls; the earlier "derived from the server's envelope" test
   line is replaced. Two forward references elsewhere in the plan now
   point at 3a.

7. **P2: the four-state reader is underspecified at malformed and
   contradictory inputs.** The table defines only the normal
   `activation`/token combinations and a 4xx. It says nothing about
   redirects, 5xx, invalid JSON, missing or wrong-shaped websocket
   fields, unsupported protocol versions, or an `activation` present
   beside a non-empty token, and the tests drive real server responses
   only, so nothing would catch truthiness used where the `is not None`
   seam is required. Keep the four outcome states, define a strict
   schema and a precedence for reaching them, state `activation is not
   None` explicitly, route malformed, contradictory, redirect and
   transport outcomes through fixed refusals outside the valid-response
   set, pin an exit code per outcome, and add table-driven hostile
   responses including `activation={}`.

   *Resolution* (this commit): adopted as prescribed. The four outcome
   states are kept and the table gains an exit column, three zeros and
   one, pinned in a case: a simulated board reporting the state it is in
   is a command that worked. Beneath it decision 4 now states the strict
   schema, in the shape `config/responses.py` is read with and with
   unknown fields ignored so a newer server stays readable, and a
   five-step precedence: transport and status first with a redirect
   refused and not followed, then the schema, then contradiction refused
   rather than resolved (an `activation` beside a non-empty token is a
   shape no vinga-server produces, and guessing which half to believe
   teaches a client to accept what the server never promised), then
   `activation is not None` written as `is not None` and not as
   truthiness, then the token. `activation={}` is named as the case that
   separates the two, since it is falsy and is not an absent key. M1's
   tests gain the table-driven hostile replies, ten of them, each with
   its outcome and its exit code, because real-server cases cannot reach
   any of them.

8. **P2: the acceptance tests cannot prove two advertised properties.**
   The M2 case claims to prove the `Protocol-Version` handshake header,
   which the server deliberately reads nothing from (`ws.py:89`), and
   the capability table advertises redirect intolerance with no redirect
   case anywhere in M1. Keep the real-server conversation test for
   compatibility and add a controlled peer that captures every handshake
   header and can return adversarial hello, TTS, binary and close
   frames; add an endpoint answering 307 with a counted target and
   assert one request, no redirect followed, no `Location` printed, and
   only the fixed refusal.

   *Resolution* (this commit): adopted as prescribed, both halves.
   Decision 7 keeps the real-server conversation lane exactly where it
   is, as the compatibility claim only a real server can make, and adds
   a controlled peer in `tests/support/` beside it: a websocket endpoint
   that records every handshake header and answers whatever a case tells
   it to. It is named as an instrument rather than a second server, and
   the paragraph says what only it can prove, the `Protocol-Version`
   header the server reads nothing from, and every adversarial answer a
   correct server will never produce. The capability table's handshake
   row is corrected to say so rather than implying the conversation case
   proves it. The redirect claim gets its case in the milestone that
   makes the claim: M1 adds a `307` from a counted endpoint with one
   request made, the target never fetched, no `Location` on any of the
   four surfaces, the fixed refusal and exit 1, and the rule is stated
   for every future row, the milestone that claims it tests it.

9. **P2: the new extra does not join the wheel's real metadata
   inventories.** The plan names a third closure environment and grows
   the command `GATED` set but omits `tests/support/tiers.py`, whose
   `declared()` returns exactly the client and serve sets
   (`tiers.py:57`), and the wheel's own metadata pins, which cover the
   bare and serve requirement blocks alone (`test_cli_wheel.py:366`). A
   declared `sim` extra missing from the wheel metadata would therefore
   pass. Name the `tiers.py` changes, put `sim` in the tier inventory
   with its import name pinned, assert the wheel's `sim` marker equals
   the declaration both ways, assert the bare install excludes it,
   assert the `[sim]` environment is exactly the locked client-plus-
   websockets closure, and update the contributor and workflow comments
   that describe only `[serve]`.

    *Resolution* (this commit): adopted as prescribed. Decision 7's
    paragraph is re-headed to say the tier VOCABULARY gains an entry and
    not only that a fixture is added, which is the half that mattered:
    `tests/support/tiers.py` is the one home both lanes read a tier
    from, so `declared()` returns three sets and `SIM_MODULES` joins
    `SERVE_MODULES` with `websockets`' import name pinned by hand. The
    wheel's `Requires-Dist` is asserted both ways for the `sim` marker
    the way it already is for the bare block and for `serve`; the bare
    install is asserted to carry no `websockets` as a distribution and
    as a module; the third environment is asserted to be exactly the
    locked client closure plus `websockets`; and the contributor and
    workflow comments naming `[serve]` alone are updated, with the dev
    group naming `vinga-server[serve,sim]` so the checkout door stays
    one command. M2's test list and the module layout carry the same,
    including the bite case that doctors the expected set in each
    direction.

10. **P2: the simulated board's persistent client identity is left
    undecided.** The plan derives a stable MAC carefully and then says
    only that `board.py` owns "the client id", with no rule and no
    stability test, while the server signs and verifies the token
    against both. Define a deterministic client id derived from the
    normalized MAC under a fixed namespace, and test its stability
    across invocations, its distinctness between two simulated MACs, and
    its reuse across activation, re-check-in and the websocket
    handshake.

    *Resolution* (this commit): new decision 2a. The client id is a UUID
    version 5 over the normalized MAC under a fixed namespace constant
    this repository owns: a pure function of the MAC, stable across
    invocations with nothing written anywhere, distinct for two
    simulated boards, and reproducible by a reader from the source.
    Version 5 rather than version 4 because determinism is the whole
    property wanted, and a UUID rather than a hand-rolled hash because a
    UUID is what the header carries and `uuid.uuid5` is in the standard
    library. Normalized first, matching `ws.signed_device_id`, so two
    spellings of one MAC are one board. The decision states why it is not
    a detail: the token is signed for the MAC and the client id together
    and `ws.py:103` verifies against both, so a drifting client id
    produces a `bad_token` refusal with nothing on either side saying
    why, and decision 3's re-check-in makes the value survive four
    requests. M1's test line covers both halves and asserts the single
    id off the recorded requests rather than off the function that makes
    it.

11. **P2: the committed audio asset still has an unresolved licensing
    and wire-format fork.** The risk section leaves the source as either
    a maintainer recording or a permissively licensed voice, to be
    chosen during implementation, and never fixes the sentence, the
    sample rate, the channel count, the frame duration, the packet
    count or the provenance record. Choose the source before
    implementation; record the exact sentence, the ownership and
    licence, the 16 kHz mono and 60 ms contract, the creation
    procedure, the packet count, the duration and a committed checksum;
    and require the end-to-end server decoder test and the provenance
    entry in the same M2 change.

    *Resolution* (this commit): new decision 1a, and the maintainer
    chose the branch rather than leaving it open. It is synthesized, not
    recorded: a maintainer recording would put a person's voice into a
    public repository, which the project's stance on what may be
    published rules out, and it would block the milestone on one person.
    The source is Piper's `en_US-ljspeech-high` from `rhasspy/piper-
    voices` at `v1.0.0`, whose `MODEL_CARD` gives the licence as the
    exact string `public domain` over the LJ Speech Dataset, which says
    "There are no restrictions on its use" and "you may use it without
    attribution", under weights the repository card declares `license:
    mit`. The two alternatives are rejected in the decision with their
    own licence strings: lessac's is a research-only Blizzard 2013
    click-through excluding commercial use, and libritts_r's is CC BY
    4.0 with an attribution obligation and a lessac base. Piper's own
    licence is shown not to reach the output, per the GNU GPL FAQ's
    `#WhatCaseIsOutputGPL`, and the synthesis happens once, offline, on
    a contributor's machine with no Piper code shipped. **No
    `THIRD_PARTY_LICENSES.md` entry is owed**, because no attribution is
    required anywhere in the chain, and the decision says so with the
    evidence rather than adding a courtesy entry to a file meant for
    notices that must be preserved; the provenance line lives in the
    manifest and in M2's implementation section. The contract is fixed
    as a table: the sentence `Hello, can you hear me?`, 16 kHz mono,
    60 ms packets, bare Opus stored as version-2 frames, and an expected
    25 to 42 packets. The procedure is a checked-in tool,
    `tests/tools/utterance.py`, run by hand and never by a lane. The
    exact packet count, duration, byte length and SHA-256 go in a
    manifest committed beside the asset, asserted by a case, because
    those are measurements and a plan that invented them would be
    inventing them. And M2 gains the end-to-end decoder case the finding
    asks for, feeding the packets through the server's own decoder,
    without which the audio path could ship carrying forty well-formed
    frames of garbage with every other case still green.

12. **P3: two module deletion-test arguments do not survive the
    repository's own test.** `simulator/__init__.py` is described as
    "what the grammar reaches", which is forwarding and has no
    justification; the design guide rejects forwarding modules
    explicitly. `conversation.py` is justified by saying an inlined
    version would force a module-scope websocket import, which is false,
    since an inlined function can import lazily too. Do not use
    `__init__.py` as a re-export layer, have the grammar import concrete
    modules directly, and keep `conversation.py` on the independent
    state-machine responsibility it removes from `cli.py`.

    *Resolution* (this commit): adopted as prescribed. The
    `simulator/__init__.py` entry says it carries the package docstring
    and nothing else, and the grammar imports the concrete modules by
    name; the design guide's own words for what a forwarding module
    costs are given, plus the second cost that applies only here, an
    `__init__` re-exporting `conversation` would drag `websockets` into
    every import of the package, which is the gate defeating itself.
    `conversation.py` keeps its place on the responsibility it holds,
    the eight-state protocol machine of decision 5a with its
    transitions, its bounds and its out-of-order rule, and the
    import-placement argument is retracted in the entry itself as an
    argument about where a line goes rather than about what a module
    knows. Decision 8's import-weight bullet is rewritten to match: the
    inventory grows by the modules the grammar names concretely, and a
    case asserts `simulator.conversation` is not among them, which is
    the same fact the gate depends on.
