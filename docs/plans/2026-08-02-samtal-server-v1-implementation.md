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
