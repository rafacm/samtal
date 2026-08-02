# samtal-server v1 implementation notes

**Date:** started 2026-08-02

Companion to [`2026-08-02-samtal-server-v1.md`](2026-08-02-samtal-server-v1.md).
One section per milestone, appended in the same change that ticks the plan's
milestone checklist. Records deviations from the plan, resolutions of the
plan's open questions, and discoveries worth keeping. A milestone with no
deviations says so explicitly.

## M0 Skeleton (PR #1, merged 2026-08-02)

Deviations and additions relative to the plan:

- **Dev dependency `httpx` replaced by `httpx2`.** Starlette's test client
  (which FastAPI's `TestClient` re-exports) deprecated `httpx`; the suite now
  passes with deprecation warnings escalated to errors.
- **CI actions pinned newer than assumed.** GitHub deprecated Node 20
  actions, so the workflow uses `actions/checkout@v7` and
  `astral-sh/setup-uv@v9.0.0`. Note: setup-uv publishes no floating `v9`
  major tag; the exact tag is required.
- **Small unplanned additions**: a `/healthz` endpoint (gives the skeleton a
  testable contract) and a `samtal-server` console entry point reading
  `SAMTAL_HOST`/`SAMTAL_PORT`.
- **Process work rode along** (not part of the milestone as planned):
  AGENTS.md gained the small-commits rule, the PR verification task-list
  rule, and the plan milestone checklist; the repo logo was consolidated to
  a single transparent PNG.

Verified beyond the plan's acceptance criteria: a doc-only push to main
triggers no workflow run (path scoping observed working post-merge).

## M1 Config (PR #2, merged 2026-08-02)

Deviations and additions relative to the plan:

- **Reworked mid-PR to be library-based.** The first implementation
  hand-rolled env overrides and YAML loading; after researching best
  practices (summary in a PR #2 comment), the config became a
  pydantic-settings `BaseSettings`. Source priority follows the library's
  documented chain: init kwargs, then `SAMTAL_`-prefixed environment
  variables with `__` as the nesting delimiter, then the YAML file, then
  the secrets-directory source (inert until configured).
- **Env vars renamed.** `SAMTAL_HOST`/`SAMTAL_PORT` from M0 became the
  library-standard `SAMTAL_SERVER__HOST`/`SAMTAL_SERVER__PORT`, and every
  config key is now env-overridable, not just those two. Renamed while
  nothing was deployed.
- **`.env` support arrived early** (planned around M4/M7): read at startup
  via python-dotenv, with real environment variables taking priority.
  Gotcha: bare `load_dotenv()` searches from the installed package's
  directory, so `find_dotenv(usecwd=True)` is required; caught when the
  first CLI verification started the server instead of failing.
- **Runtime YAML path workaround.** pydantic-settings has no init kwarg
  for a runtime-chosen config file (pydantic-settings#259); the path from
  `--config`/`SAMTAL_CONFIG` reaches `YamlConfigSettingsSource` through a
  `ContextVar`.
- **Custom code kept deliberately**: cross-reference validation, MAC
  normalization, `ConfigError` formatting, and a pre-flight file check,
  because the library source silently skips a missing file and its parse
  errors do not reliably name line and column.
- **One review round, three findings, all fixed**: the inline-secret guard
  was broadened from an exact key list to fragment matching (`secret`,
  `token`, `password`, `api_key`, `apikey`, `credential`) with a `_env`
  suffix carve-out; blank identifiers (empty provider/agent names, empty
  provider `type`, `default_agent: ""`) are rejected via a shared
  `NonBlankStr` type; a README claim about mounted secret files was
  removed because provider secret resolution cannot read files until M4.
- **Example provider types are placeholders.** `config.example.yaml` names
  `sensevoice` and `piper` before the plan's open questions on ASR/TTS
  defaults are decided; M4 settles them.

Resolution of plan open questions: none (all four remain open for M4).

## M2 OTA endpoint (PR #3)

Deviations and additions relative to the plan:

- **One port, not two.** Upstream splits HTTP (8003) and WebSocket (8000);
  samtal-server is a single FastAPI app, so both endpoints share
  `server.port` (8003). The websocket URL handed to devices therefore names
  the same port they just POSTed to. Not a one-way door: the advertised URL
  is independent of the listening topology, so the two tiers can be split
  later by routing alone, with no code change. The tradeoffs, and what a
  reverse proxy in front has to get right, are documented in the
  samtal-server README; the parts the container image has to answer for are
  listed under the plan's Packaging and deployment.
- **Behind a proxy the derived URL is wrong, and quietly.** Uvicorn only
  trusts `X-Forwarded-Proto` from `--forwarded-allow-ips`, which defaults to
  `127.0.0.1` and so will not match a proxy's address; a TLS-terminating
  proxy therefore still derives `ws://` rather than `wss://`. The README
  tells proxied deployments to set `server.websocket_url` explicitly.
  Confirmed after the merge rather than taken from uvicorn's documentation:
  the same server, sent the same request with `X-Forwarded-Proto: https`,
  answers `wss://` when reached over loopback and `ws://` when reached over
  the host's LAN address, since only the first is in `forwarded_allow_ips`.
  What remains untested is a full deployment behind a real TLS-terminating
  proxy, which M7 makes possible. Revisit there: trusting proxy headers by
  configuration would let the derivation work unattended.
- **The websocket URL is derived, not required.** `server.websocket_url` is
  optional; unset, the reply is built from the address the device reached
  the OTA endpoint on (`ws://{Host}/xiaozhi/v1/`, `wss` under HTTPS). A LAN
  deployment then needs no configuration at all, and the value is correct
  behind a proxy that rewrites `Host`, which upstream's `get_local_ip()`
  is not. Setting the key explicitly still wins.
- **Two more `server` keys arrived with it**: `protocol_version` (default 1,
  matching the firmware's own default of bare Opus frames) and
  `timezone_offset_minutes` (default: the server's current offset). The
  device sets its clock from `server_time`, and the offset upstream defaults
  to is China's.
- **`create_app` now takes a `Config` and the CLI passes the app object.**
  Handlers need the config, and with an import string uvicorn would build a
  second app reading `SAMTAL_CONFIG`, so a path given with `--config` would
  be silently ignored. The module-level `app` an external ASGI server
  imports is built lazily through a module `__getattr__`, so importing
  `create_app` does not load the config twice as an import side effect.
- **Logging had to be turned on at all** (not part of the milestone as
  planned). Uvicorn configures only its own loggers, so everything
  samtal-server logged went to a handler-less root logger and vanished while
  uvicorn's request lines still appeared. The CLI now calls
  `logging.basicConfig`; M7 replaces it with structured logging.
- **Agent resolution is logged, not enforced.** The plan has unknown devices
  fall back to `default_agent`, which they do. A device that resolves to no
  agent at all (no binding and no `default_agent`) is still answered with a
  full configuration and logged as a warning: refusing a device belongs to
  the session that cannot serve it, not to a configuration fetch. M3 and M5
  own that rejection.
- **Malformed input is split by how much the reply depends on it.** Missing
  or non-MAC `Device-Id`, and missing `Client-Id`, are a 400. An unparseable
  body is not: only the reported firmware version comes from it, so the
  device is answered with `0.0.0` (never newer than anything, so never
  offered an update) rather than turned away. Upstream answers 200 with an
  error body in every case, which the firmware cannot distinguish from
  success.
- **No `activation` section, ever.** Omitting it is what keeps devices from
  being asked to activate, so it is asserted in the tests rather than left
  implicit.
- **`token` is sent as `""` rather than omitted.** The firmware writes every
  key of the `websocket` object into NVS, so sending an empty token clears
  one left behind by another server. Real tokens arrive in M7.

Resolution of plan open questions: the binary protocol version (question 4)
is now configurable and defaults to 1; the value to advertise is still M3's
call. The other three remain open for M4.

### Device checkpoint

Verified against the Waveshare ESP32-S3-Touch-LCD-1.54 on the desk
(MAC `28:84:85:49:8c:a8`), whose `ota_url` already pointed at port 8003, so
no NVS rewrite was needed:

- The board POSTs on boot and gets 200. Its log shows `Ota: Current is the
  latest version` and `Application: Activation done`, and no
  `No websocket section found!`, so the whole reply was accepted.
- The once-per-second `Display: System time is not set, tm_year: 70` warning
  stops after the first reply, so `server_time` sets the clock.
- Reading the NVS partition back (`esptool read_flash 0x9000 0x4000`, parsed
  with `nvs_tool.py -d written`) shows the live `websocket` namespace holding
  `url = ws://192.168.1.33:8003/xiaozhi/v1/` and `version = 1`, with the
  previous upstream `:8000` entry erased. No `token` key: the firmware only
  writes a value that differs, and an unset key already reads as empty.
- With the board's MAC bound to a non-default agent, the server logs
  `device 28:84:85:49:8c:a8 (esp32-s3-touch-lcd-1.54, firmware 2.4.0)
  resolved to agent kitchen`, so per-device binding works on real hardware.

- The board opens the websocket URL it was given. A short PWR press starts a
  conversation, and the server logs `192.168.1.59 - "WebSocket /xiaozhi/v1/"
  403`, three times per press as the firmware retries. **403, not 404**:
  Starlette closes an unmatched websocket scope before accepting it, and
  uvicorn turns that into 403 on the upgrade. The device plays its
  connection-failure tone and returns to idle, which is the correct outcome
  until M3 serves that path.

Note for future checkpoints: the board has a battery, so unplugging USB does
not power it off. Long-press PWR, or toggle RTS over the serial port.

## M3 Protocol handshake and audio loop (PR #4)

Deviations and additions relative to the plan:

- **The Opus bindings question was decided here, not in M4.** M3 cannot
  exist without a codec, so open question 3 resolved early: PyAV, over the
  opuslib that upstream and xiaozhi-sdk use. PyAV ships maintained binary
  wheels for every target including arm64 (opuslib is source-only,
  unmaintained since about 2018, and depends on ctypes finding a system
  libopus), and the same dependency covers M4's resampling and TTS-format
  decoding. Two codec discoveries worth keeping: FFmpeg's Opus decoders
  always emit 48 kHz, Opus's internal rate, so the decoder resamples to the
  promised 16 kHz internally (with a constant filter delay of about one
  millisecond); and the encoder's flush pads the final partial frame with
  silence rather than draining the codec, which keeps the encoder reusable
  across utterances at the cost of the few milliseconds of lookahead held
  inside.
- **The advertised binary protocol version stays 1** (open question 4, left
  as M3's call by M2). The version 2 timestamps exist for server-side AEC,
  which samtal-server does not do. All three framings are implemented and
  unit-tested byte-for-byte against the firmware's packed network-order
  structs, and the session serves whichever version the device's hello
  declares, so changing the advertised version is configuration only.
- **An energy endpointer arrived unplanned.** The board in auto listening
  mode never sends `listen stop`: it streams mic audio until the server
  decides the user finished. Without endpointing, the device checkpoint
  would connect and stream but never hear an echo. The detector (RMS
  threshold, 700 ms trailing silence, 10 s cap) has the same feed/reset
  shape Silero will take over in M4; its thresholds are module constants,
  not configuration, because it is not meant to outlive M4.
- **The echo announces 16 kHz out, not the plan's 24 kHz.** Echoing at the
  device's input rate needs no resampling; the real output rate belongs to
  M4's TTS.
- **The server hello is maximal, not minimal.** xiaozhi-sdk indexes
  `session_id` and every `audio_params` field without defaults and crashes
  when one is missing, although upstream's protocol document marks them
  optional. The hello builder therefore requires them all.
- **M3 took its share of the rejection M2 deferred.** A device whose
  `Device-Id` is not a MAC, or that resolves to no agent, is accepted and
  then closed with 1008 and a short reason, so both sides log something
  useful. M5 owns the rest (per-agent enforcement).
- **Realtime mode is treated as auto mode.** Its defining feature,
  listening while the server speaks, needs the pipeline; frames arriving
  during a reply are dropped. Revisit no earlier than M4.
- **Replies are paced.** Outgoing frames go out on monotonic deadlines at
  the frame cadence rather than as a burst, so a long echo cannot flood
  the device's playback queue; `abort` cancels the stream mid-reply and
  still sends `tts stop`.
- **`WEBSOCKET_PATH` moved from `ota.py` to the new `ws.py`.** The OTA
  endpoint points devices at the websocket endpoint, so the import now
  follows that direction.
- **Dev lane additions**: xiaozhi-sdk and pytest-asyncio joined the dev
  group. The sdk bundles its own libopus, so CI still needs no system
  packages, and because it encodes and decodes with opuslib, the
  integration run cross-validates the server's PyAV codec against an
  independent implementation.

Resolution of plan open questions: Opus bindings (question 3) resolved to
PyAV; the binary protocol version to advertise (question 4) resolved to 1.
The ASR and TTS defaults (questions 1 and 2) remain open for M4.

### Device checkpoint

Not required by the plan until M4, but run anyway with the board on the
desk (MAC `28:84:85:49:8c:a8`, NVS `ota_url` unchanged since M2):

- A short PWR press opens the websocket that answered 403 throughout M2.
  The server logs the accepted upgrade, then the completed hello: agent
  resolved, protocol v1, 16000 Hz 60 ms frames in.
- Speaking a short sentence and pausing produced the echo from the board's
  speaker after roughly the endpointer's trailing-silence window, with
  "(echo)" on the display. The server logged a 1.0 s utterance.
- The echo is noticeably quieter than synthesized speech will be: it is
  the board's own mic capture played back, so the level is the mic's, not
  a TTS engine's. Nothing to fix in M3; M4's TTS does not inherit it.
- After the reply the board returned to listening on its own (auto mode),
  and the session closed cleanly when the conversation ended on the
  device.

## M4 Conversation pipeline (PR #6)

Deviations and additions relative to the plan:

- **The local ASR default is faster-whisper** (open question 1). SenseVoice,
  what upstream uses, covers zh/en/ja/ko/yue and would have ruled out most
  European languages (Swedish is a wanted nice-to-have, not a requirement);
  faster-whisper is MIT, multilingual, runs acceptably on CPU with int8
  quantization, and ships CTranslate2 wheels for both x86_64 and aarch64.
  Heavier engines surveyed (Parakeet TDT v3 via NeMo, sherpa-onnx runtimes,
  Vosk) remain possible as future provider types behind the same interface.
- **The keyless TTS default is Piper, but its premise shifted** (open
  question 2). The plan weighed "Piper (local, permissive) vs edge-tts
  (GPL-3.0, unofficial API)"; between plan and milestone the MIT
  rhasspy/piper was archived (October 2025) and the maintained successor,
  piper1-gpl (PyPI `piper-tts`), is GPL-3.0-or-later. Piper still won on
  being local and officially packaged with wheels for every target, and it
  now simply gets the extras-only treatment the licensing rules always
  mandated for edge-tts, which stays unimplemented since nothing needs it.
- **Silero rides pysilero-vad as a core dependency, not an extra.** The
  official `silero-vad` package would have pulled torch and torchaudio into
  every install; pysilero-vad compiles the model and an ONNX runtime into
  one dependency-free abi3 wheel covering every deployment target. The
  endpointer keeps M3's feed/reset shape and bookkeeping, with the
  threshold and windows now provider options.
- **Pipeline completeness is enforced at startup, not in the schema.** The
  plan's "agents combine one provider per stage" became: the stage fields
  stay optional in the config model (validation-only uses keep working, and
  M5 may add per-agent defaulting), but `create_app` builds every
  referenced provider at boot and refuses an agent missing any of the four
  stages. Unknown types, bad or unknown options, missing extras, and an
  `api_key_env` naming an unset variable all fail the boot with the
  configuration entry named, instead of failing the first conversation.
- **The energy endpointer lives on as the mock VAD.** Real Silero would
  rightly refuse to call CI's synthetic tones speech, so the mock stage
  keeps the M3 energy logic; the mock ASR can embed the utterance duration
  in its transcript, which is how tests observe frame dropping now that no
  echo comes back. CI runs the whole pipeline on mocks: no keys, no model
  downloads, no network.
- **TTS output is 24 kHz** (the rate the plan named and M3 deferred): the
  server hello now announces it, and TTS output is resampled from the
  engine's native rate (Piper's medium voices speak 22.05 kHz) on the same
  PyAV that carries the codec.
- **Both LLM providers landed in core.** The `anthropic` and `openai` SDKs
  are light, unlike the ASR/TTS engines, so neither is an extra. The
  `openai_compatible` type requires `base_url` because its point is local
  and self-hosted endpoints (Ollama, LM Studio, gateways).
- **The reply-language trap is answered in the prompt.** The example agent
  prompt now states the reply language explicitly ("reply in the language
  the user spoke"), the lesson of the upstream reference server defaulting
  to Chinese; the ASR provider takes an optional `language` hint that pins
  transcription instead of per-utterance detection.
- **Deferred, deliberately**: OpenAI-compatible cloud ASR and TTS providers
  (nothing in v1 needs them before M7) and the edge-tts extra. Realtime
  mode is still treated as auto mode, unchanged from M3.
- **The local lane was verified on the dev machine** before hardware: the
  fully local pipeline (Silero + faster-whisper `small` + Ollama
  `gemma4:e4b` + Piper) driven by the xiaozhi-sdk simulator speaking a
  Piper-synthesized "What is the capital of Sweden?". The transcript came
  back exact, the reply ("The capital of Sweden is Stockholm.") was spoken
  back, and both engine downloads happened at server startup as designed.
  ASR took about 1.8 s for a 2.3 s utterance on CPU int8. That ad-hoc run
  was then committed as the plan's "second lane with Ollama": an opt-in
  `tests/local` lane (`SAMTAL_LOCAL_LANE=1`, never in CI, skips without
  the opt-in) holding the same conversation, with a pre-flight check that
  fails naming whatever is missing (extras, a reachable Ollama, a usable
  model) and the command that fixes it.

Resolution of plan open questions: the local ASR default (question 1)
resolved to faster-whisper and the keyless TTS default (question 2) to
Piper as a GPL extra, as above. All four of the plan's open questions are
now closed.

### Device checkpoint

The first real conversation, on the fully local pipeline, against the
Waveshare ESP32-S3-Touch-LCD-1.54 on the desk (MAC `28:84:85:49:8c:a8`,
NVS `ota_url` unchanged since M2). Server on the dev machine
(192.168.1.33:8003) with the gitignored `checkpoint.local.yaml`: Silero
VAD, faster-whisper `small` (CPU, int8), Ollama `gemma4:e4b` through the
`openai_compatible` provider, and the Piper `en_US-lessac-medium` voice.

- The board was reset over serial; it fetched its configuration from the
  server ("Current is the latest version", "Activation done") and resolved
  to the `assistant` agent.
- A short PWR press opened the websocket and completed the hello, the
  server now announcing 24 kHz output; the board accepted it.
- Speaking to the board ("Hi! Are you there? This is Rafael.", a 3.1 s
  utterance): Silero endpointed real room audio, whisper detected English
  (0.84) and transcribed the sentence exactly, and the reply came out of
  the board's speaker in the Piper voice. Transcription took about 1.9 s
  of the ASR-to-reply latency.
- After the reply the board went back to listening on its own (auto mode),
  ready for the next turn; the session stayed open throughout.
- One gap surfaced by the checkpoint: the session logged what it heard but
  not what it replied, so the reply text could not be quoted from the
  server log. Fixed in the same change as this note: the reply is now
  logged when its sentences have been spoken.

## M5 Agents and bindings (PR #7)

Implemented from the dedicated
[M5 plan](2026-08-02-m5-agents-and-bindings.md), whose nine commits are the
nine commits of the PR. Its structure and its decisions survived contact
unchanged: `agent_defaults` carries the four stage fields and no prompt, a
voice stays a TTS provider entry, device bindings became a list with the
first entry active at connect, the session gained an explicit active
agent, and the deferred items (realtime listening mode,
OpenAI-compatible cloud ASR and TTS, memory) stayed deferred. The v1
plan's open questions were all closed in M4 and none reopened.

Three deviations from the plan's letter, all found while building:

- **Only half of the validation moved to the effective view.** The plan
  said cross-reference validation and the boot-time completeness check
  both would, with errors naming the layer the bad reference came from.
  The completeness check does. Cross-reference validation does not:
  reading the effective view literally would report a wrong
  `agent_defaults.llm` once per agent inheriting it, so each layer's own
  references are checked where they are written instead. A wrong default
  is one error, a wrong override is one error, and both quote the place
  that holds the mistake, which is the outcome the plan actually wanted.
  `Config.provider_for_agent` returns the effective provider together
  with that location, and is what the completeness check reads.
- **The local lane identifies voices rather than contrasting them.** The
  plan asked the two-persona local test to assert "the voices are
  distinct". Pitch turned out not to separate them: `en_US-lessac-medium`
  and `en_US-amy-medium` measure about 180 Hz and 200 Hz, close enough
  that any threshold would be either flaky or meaningless (the first
  attempt failed on exactly that). The test instead re-speaks each
  device's actual reply locally in both configured voices and requires
  the received audio to resemble its own agent's, comparing a long-term
  average log spectrum. Since the words are identical in every
  comparison, the voice is the only difference left, and on the desk the
  margin is about fourteenfold (0.014 against 0.199). This is a stronger
  claim than the plan's: not "the two differ" but "each device was
  answered in the voice its agent names".
- **The turned-away device has no agents at all, not merely no
  `default_agent`.** The plan's fourth acceptance case was "a config
  lacking `default_agent`". M1's validation forbids that shape: defining
  agents requires a `default_agent`, so the only configuration that
  resolves a device to nothing is one with no agents whatsoever, and that
  is what the test uses. The behaviour under test is unchanged.

Discoveries and smaller decisions:

- **`AgentConfig` is `AgentDefaults` plus a prompt.** Subclassing keeps the
  two in step by construction and makes "the same four stages, and a
  prompt only an agent may have" the literal structure.
- **A tone frequency was enough to make two mock voices distinguishable.**
  The mock TTS gained `tone_hz` and the mock LLM a `{system}` placeholder,
  which together let the mock lane assert the two halves of a persona:
  the reply text derives from that agent's own prompt, and the audio
  carries that agent's own voice. The unit lane measures the tone with a
  single DFT bin (no numpy needed); the integration lane, which decodes
  through the sdk's opuslib, takes the dominant frequency of an FFT.
- **The 1008 rejection is asserted directly in the integration lane.** The
  xiaozhi-sdk reports a server-side close only indirectly, so that one
  case connects with a plain websockets client and reads the close code
  and reason.
- **The local lane's server runner and Piper synthesizer became fixtures**
  (`serve` and `speak` in `tests/local/conftest.py`), now that two local
  tests need them, which avoids cross-imports between test modules.

Verified on the dev machine, not on hardware: the plan requires no device
checkpoint for M5, and none was carried out. The local lane run had the
poet answer "Sweden's pride is Stockholm's view." in the Piper lessac
voice and the travel guide answer "The capital of Sweden is Stockholm."
in the amy voice, from one server, over one shared whisper and one shared
local model.

### Device checkpoint

Not required for M5 by the plan, and carried out anyway, because hearing
two personas is the whole point of the milestone. Waveshare
ESP32-S3-Touch-LCD-1.54 on the desk (MAC `28:84:85:49:8c:a8`, firmware
2.4.0, NVS `ota_url` unchanged since M2), server on the dev machine
(192.168.1.33:8003) with a gitignored `checkpoint.local.yaml` in the M5
shape: `agent_defaults` holding the shared pipeline (Ollama
`gemma4:e4b`, faster-whisper `small`, Silero), and two agents differing
in nothing but prompt and Piper voice, `storyteller` on
`en_US-amy-medium` and `assistant` on `en_US-lessac-medium`.

- Bound to the list `[storyteller, assistant]`, the board's boot OTA
  fetch logged `resolved to agent storyteller (also bound to assistant)`
  and its session opened on the same. That is M5's routing rule, first
  entry active, on real hardware.
- Asked for "Tell me a story about a fox in the snow" (a 3.2 s utterance,
  whisper detecting English at 0.80), the storyteller answered with a
  four-sentence bedtime story, spoken back in the amy voice: a different
  voice from the one this board used at the M4 checkpoint.
- The binding was then flipped to `[assistant, storyteller]` and the
  server restarted. The same board resolved to `assistant`, and "What's
  the capital of Sweden?" came back as "The capital of Sweden is
  Stockholm." in the lessac voice, about five seconds after the
  transcript. Same device, same MAC, other prompt and other voice, which
  is what rules out the voice being whatever the server happened to load.
- Not exercised on hardware: two devices on different agents at the same
  time, which the integration lane covers with two concurrent simulators,
  and moving between the two agents a device is bound to, which is M6.

## M6 Tools/MCP (PR #8)

Implemented from the dedicated
[M6 plan](2026-08-02-m6-tools-and-mcp.md), whose thirteen commits are the
thirteen commits of the PR. Its architecture survived contact intact: the
session owns the tool loop and providers stay translators, history stays
text-only with the structured turns living in a working copy inside one
reply, the tool namespace is structural rather than collision-handled, a
successful `switch_agent` ends the old agent's loop and the new one
greets, memory is per agent and reaches the model by injection, and a
dead MCP server warns instead of failing the boot. None of the plan's
"do not reopen" decisions were reopened.

Four deviations from the plan's letter, all found while building:

- **`ToolCall` grew a fourth field.** The plan's neutral model is
  `id`, `name`, `arguments`, and separately says that malformed argument
  JSON from a model "becomes an error tool result rather than an
  exception". Those two do not fit together: with only a `dict`, an
  empty `arguments` means both "the model asked for a tool that takes
  none" and "the model produced junk", and the session cannot tell
  which. `ToolCall` therefore carries an optional
  `malformed_arguments` holding the raw text, which the session turns
  into the error result and the log line. The alternative, typing
  `arguments` as `dict | None`, pushes a None check onto every reader
  to say less.
- **The secret-key rule for MCP entries is wider than M1's.** The plan
  says keys matching the M1 secret fragments must use the `$VAR` form,
  but the canonical header carrying a secret is `Authorization`, which
  matches none of `secret`, `token`, `password`, `api_key`, `apikey`,
  `credential`; the plan's own example (`Authorization: $WEATHER_TOKEN`)
  would have passed the guard unenforced. The MCP check adds `auth` to
  the fragments. The provider check is untouched, so nothing that
  validated before validates differently now.
- **`$VAR` resolution happens where the managers are built, not in the
  model.** The plan puts resolution "at boot", which the pydantic
  validator would also satisfy, but resolving there would leave the
  parsed configuration holding plaintext secrets for the life of the
  process, and `Config` is dumped in logs and error messages.
  `resolve_env_references` is a function in `config.models` called by
  `McpServers.build`, which `create_app` runs. One consequence worth
  stating: an unset `$VAR` in an entry no agent references does not
  fail the boot, because that entry is never built. That matches
  providers, where an unreferenced entry is likewise never built and
  its `api_key_env` never resolved.
- **Each MCP manager owns its own lifecycle task.** The plan has
  managers closing "via the lifespan's exit (`AsyncExitStack`)". The
  SDK's clients are async context managers over anyio task groups, and
  entering one in one task while exiting it in another is exactly what
  breaks their cancel scopes; background reconnects mean a manager's
  stack is routinely entered by a task that is not the lifespan's. Each
  manager therefore runs a task that enters the stack, publishes its
  tools, waits on a stop event, and exits the stack itself. The
  lifespan asks them all to stop, which is the same guarantee (no stdio
  child outlives the server) reached the only way the SDK allows.

Discoveries and smaller decisions:

- **Both xiaozhi-sdk traps the plan records are real, and were hit
  knowingly.** `initialize` without a `capabilities.vision` stanza
  raises a KeyError in the sdk (`mcp.py` indexes `params.capabilities.
  vision.url` and `.token` unconditionally), so the request carries one
  of empty strings; with it, discovery completes against the sdk in
  every integration conversation, which is how the handshake is
  covered without a board. The sdk also returns silently from
  `tools/call` for a name it does not know, so a device call that is
  never answered can only be a timeout, which is why the session bounds
  every call and the client discards its pending request when it fires.
- **xiaozhi-sdk JSON-encodes whatever a device tool returns.** A device
  tool answering `"72 percent"` arrives as `"\"72 percent\""`. The
  server does not unwrap it: that is the device's own encoding, the
  model reads it fine, and unwrapping would guess at which quotes are
  data. The real MCP SDK returns plain text, so a server tool's answer
  arrives unquoted, and the two integration assertions differ by
  exactly that.
- **A `tools/names.py` leaf module holds the namespace.** The plan
  describes the rules but not where they live. Configuration validates
  entry names against them, so they cannot sit anywhere that imports
  configuration; `names` imports nothing but `re`, and `tools/__init__`
  stays a docstring so importing a submodule cannot drag the package's
  re-exports (and the cycle back to `config`) along.
- **The mock LLM's scripted tool calling was enough for the whole
  loop.** `tool_when`, `tool_name`, `tool_arguments`, and a
  `{tool_result}` placeholder cover the acceptance, device tools,
  switching, refusal, memory, and the timeout path, all deterministic.
  The unit lane's harder cases (the round cap, the `tool_choice`
  sequence, two switches in one round) use a `ScriptedLlm` fake instead,
  because they need a different script per round.
- **Proving cross-conversation memory needed care.** Injection re-reads
  the file every round, so within one reply the fact a `remember` call
  just stored is already in the next round's prompt: a single
  conversation proves nothing about persistence. The integration test
  holds two conversations that remember the same fact and asserts the
  second one's reply quotes it twice. Only one of those copies is its
  own, so the other is the first conversation's, surviving the
  disconnect.
- **The integration lane grew a `conftest.py`.** The server runner, the
  device simulator, and the spectral helpers moved there now that two
  modules need them, and `test_two_personas.py` was rewired onto them.
  Same reasoning as M5's move of the local lane's `serve` and `speak`
  fixtures: it avoids cross-imports between test modules.
- **The namespace guarantee had a hole, found in review.** Configuration
  validates an `mcp_servers` entry name, and the device client
  sanitized its dotted names, but the tool names a server listed went
  out with only the prefix added. A third-party server may publish
  anything, and both LLM APIs refuse names outside `[A-Za-z0-9_-]` or
  longer than 64 characters, so one badly named tool nobody asked for
  would have failed every later request rather than itself. A name
  legal on its own could also overflow the cap once prefixed, which a
  check on the unprefixed name would have missed. Both sources now
  publish through one `tools/publish.py`: sanitize, drop what cannot be
  expressed (with the reason logged), keep a reverse map, keep the
  first of any collision. The lesson generalizes: "collisions are
  impossible by construction" held for the part of the name we choose
  and said nothing about the part the far side chooses.
- **The stdio test server is four functions of FastMCP.** `secret_word`,
  `add`, `slow_answer`, and `always_fails` cover the answer path, the
  timeout path, and the error path, spawned with `sys.executable` so CI
  needs nothing beyond the project's own dependencies. Two more are
  registered by hand, `weather.today/v2` and a 60 character name,
  because a Python function name cannot express what a real server is
  free to publish.

Verified on the dev machine: 308 unit tests and 18 integration tests
green, ruff clean, and the opt-in local lane green on real engines. The
lane's new tool-calling test had `qwen3:8b` answer "The secret word is
rhubarb." to "Ask the tool for the secret word, then tell me what it
is.", through Silero, faster-whisper `small`, Ollama, and Piper, with
the word itself existing nowhere but inside the stdio MCP server. A
real model handed these definitions does work out that it should call
one, which is the only thing the scripted mock cannot prove. The lane's
pre-flight checks Ollama's reported capabilities for the model it is
about to use, because not every local model can call tools; `qwen3:8b`
reports `tools` and was used unchanged. Verified on hardware too: see
the device checkpoint below.

### Device checkpoint

Not required for M6 by the plan, and carried out anyway, because a tool
milestone that has only ever run against a simulator has not been tested
where it matters. Waveshare ESP32-S3-Touch-LCD-1.54 on the desk (MAC
`28:84:85:49:8c:a8`, firmware 2.4.0, NVS `ota_url` unchanged since M2),
server on the dev machine with a gitignored `checkpoint.local.yaml` in
the M6 shape: Ollama `gemma4:e4b` (which reports the `tools`
capability), faster-whisper `small`, Silero, and two agents bound to the
board as `[assistant, storyteller]`, the assistant on `en_US-lessac-
medium` with `mcp: [tools]` and the storyteller on `en_US-amy-medium`
with `mcp: []`. The `tools` entry is the same stdio MCP server the test
lane spawns.

- **The device MCP channel works against firmware, not just the
  simulator.** Every session's handshake completed in about 35 ms and
  listed five real tools: `self_get_device_status`,
  `self_audio_speaker_set_volume`, `self_screen_set_brightness`,
  `self_screen_set_theme`, `self_system_reconfigure_wifi`. That is the
  vision stanza accepted by the real board as well as by xiaozhi-sdk,
  and the dotted names sanitized into something both LLM APIs accept.
- **A device tool ran.** "Set your volume to 30%." called
  `self_audio_speaker_set_volume` in 0.01 s and came back as "The volume
  is now set to 30 percent.", with the speaker audibly quieter. First
  real hardware control through the tool loop.
- **A server MCP tool ran.** "Ask the tools for the secret word." called
  `tools__secret_word` and answered "The secret word is rhubarb." The
  word exists nowhere but inside the spawned subprocess, so the board
  reached a process on the server through the model.
- **Memory survived a disconnect.** "Remember that I'm vegetarian."
  wrote `- The user is vegetarian` to `memory.local/assistant.md`, keyed
  by agent. A later conversation, on a new session with a new prompt,
  answered "What do you know about me?" with "I remember you are
  vegetarian, but I don't have any other personal information about you
  right now." and made no tool call in that round, which is what makes
  it injection rather than recall.
- **The handover works, prompt and voice together.** "Let me talk to the
  storyteller." logged `handed over from agent assistant to storyteller`
  1.8 s after the transcript, and the reply was "Hello there! I am the
  Storyteller, and it sounds like you are ready for a nice bedtime
  story. Would you like to hear about brave knights, or perhaps
  adventurous stars?", audibly in the amy voice rather than lessac. The
  assistant spoke nothing before switching, so the whole reply came from
  the new agent. This is the case M5's checkpoint recorded as "not
  exercised on hardware: moving between the two agents a device is bound
  to, which is M6".

One interruption worth recording, because it will happen again: the dev
machine's DHCP lease changed mid-checkpoint, and the board kept POSTing
to the address in its NVS. Nothing in samtal was at fault (the board
booted, joined wifi, and resolved the host correctly; the server
answered on every address it actually had), but recovering it cost more
time than the checkpoint itself. Reclaiming the old address as an
`ifconfig alias` did not work: macOS kept the /24 mask and installed a
reject route, and the alias never answered while the interface's primary
addresses did. Setting the interface's primary address instead
(`ipconfig set en7 MANUAL 192.168.1.33 255.255.255.0`) worked
immediately. A DHCP reservation for the dev machine would remove the
whole class of problem, and is worth doing before the M7 checkpoint.

Not exercised on hardware: a streamable-http MCP server (only stdio was
attached), a tool timing out or failing on the board, and two devices
holding tool conversations at once. The integration lane covers all
three.
