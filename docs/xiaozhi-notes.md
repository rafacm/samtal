# Xiaozhi research notes

Findings from studying the upstream projects and getting a working end-to-end
demo (2026-08-01). Reference clones live in `vendor/` (not committed):

```sh
git clone --depth 1 https://github.com/78/xiaozhi-esp32.git vendor/xiaozhi-esp32
git clone --depth 1 https://github.com/xinnan-tech/xiaozhi-esp32-server.git vendor/xiaozhi-esp32-server
```

## Device firmware (78/xiaozhi-esp32)

- Board support is compile-time: `main/boards/<vendor>/<board>/`, selected via
  Kconfig (`Xiaozhi Assistant → Board Type`). Our board:
  `waveshare/esp32-s3-touch-lcd-1.54` (ST7789 240×240 LCD, CST816S touch,
  ES8311 codec + ES7210 mic ADC with device-side AEC, 16 MB flash / 8 MB
  PSRAM).
- Mainline requires **ESP-IDF v6.0.x**; releases ship prebuilt merged binaries
  per board, flashable at offset `0x0`:
  `esptool.py --chip esp32s3 write_flash 0x0 merged-binary.bin`.
- **The only tie to a backend is the OTA URL.** Priority: NVS namespace
  `wifi`, key `ota_url`, falling back to compile-time `CONFIG_OTA_URL`
  (default `https://api.tenclass.net/xiaozhi/ota/`). Everything else
  (WebSocket URL, token, protocol version, firmware updates) is delivered by
  the OTA response and persisted to NVS.
- The captive portal (WiFi provisioning AP `Xiaozhi-XXXX`,
  `http://192.168.4.1`) may or may not have a **Custom OTA URL field**
  on its Advanced tab, and the presence is vendor-build-dependent, not
  a firmware-version threshold: the Waveshare factory image on the
  AMOLED-2.16 has the field at firmware 2.2.4, while stock 2.4.0 on
  the Touch-LCD-1.54 has none (both observed on hardware,
  2026-08-12/13; an earlier revision of this bullet inferred "added
  after v2.4.0" from those same boards, wrongly). Where the field
  exists, a board can be pointed at a backend with no USB cable at
  all. Where it is absent, or where a provisioned board offers no way
  back into its portal, the URL is written directly to NVS over USB,
  which is the route that works on every board:
  [the device guides' common page](devices/README.md#writing-the-servers-address-into-nvs)
  carries that procedure, and each guide says what its own board's
  portal was observed to carry.
- **The portal may save the URL without its trailing slash** (or the
  operator may type it that way), and that is field-observed rather
  than hypothetical. The device then POSTs to the
  slashless path; a server that answers with a redirect, even a
  method-preserving 307, bricks the check-in loop, because the
  firmware's OTA HttpClient does not follow redirects: the board
  shows `code=307` and restarts over and over. vinga-server
  therefore serves the slashless spelling directly on every
  device-facing route. A device-facing endpoint can never rely on a
  redirect.
- **An HTTPS backend needs no firmware certificate work.** The firmware
  trusts the ESP-IDF certificate bundle and sets
  `CONFIG_MBEDTLS_CERTIFICATE_BUNDLE_CROSS_SIGNED_VERIFY=y`, which is
  exactly what a Let's Encrypt host needs today: the bundle carries
  `ISRG Root X1` and `X2` but not the newer `ISRG Root YR`, and such
  hosts serve `Root YR` cross-signed by `X1`, so the chain validates
  through the cross-sign. The boot log confirms it with
  `esp-x509-crt-bundle: Certificate validated`. Pin nothing: `X1` is the
  anchor that actually works, `Root YR` on its own is not self-signed and
  fails strict path building, and the leaf rotates roughly every 90 days.
- App logic lives in `main/application.cc`; protocol in `main/protocols/`;
  device exposes its own MCP tools (volume, brightness, etc.) to the server
  over the conversation channel.
- What a particular board does with all of this, its buttons, its wake
  word, its display and its own voice commands, is in
  [the device guides](devices/README.md), and so are the procedures
  that involve the board in front of you: writing NVS over USB,
  resetting it, and reading its boot log back.

## Device ↔ server protocol

- Device POSTs system info to the OTA URL (headers `Device-Id` = MAC,
  `Client-Id` = UUID); response JSON contains `websocket {url, token,
  version}` (and/or `mqtt {...}`), optional `firmware {version, url}` and
  optional `activation {...}` (omit it and no activation is ever required).
- **A successful OTA check does not mean the device is authorised.** A
  board whose MAC is missing from vinga-server's `devices:` allowlist
  still gets `200 OK`, with `websocket.token` empty; the refusal comes
  later, at the WebSocket handshake, as a `403` logged as `auth_rejected`
  with reason `no_token`. Nothing in the OTA response says the device is
  unwelcome, so a board that provisions perfectly and then never speaks
  is this, not a network fault. Treat an empty token as a hard
  provisioning error rather than connecting anyway. One POST from a
  laptop, sending the board's MAC as `Device-Id` and its UUID as
  `Client-Id`, answers the question before touching the hardware. A
  plain `GET` on the same URL returns a human-readable line naming the
  WebSocket URL, which smoke-tests routing without revealing anything.
- **Probing the WebSocket route with `curl` needs `--http1.1`.** curl
  negotiates HTTP/2 by default, where a WebSocket upgrade is not a valid
  handshake, so the request arrives as an ordinary `GET` and a WS-only
  route correctly answers `404`, which reads like a broken route. With
  `--http1.1` and the `Connection`, `Upgrade` and `Sec-WebSocket-*`
  headers, a `403` from an unauthenticated probe is the success signal:
  the route is alive and device auth is enforced.
- WebSocket handshake headers: `Authorization: Bearer <token>`,
  `Protocol-Version`, `Device-Id`, `Client-Id`. Then a JSON `hello` exchange;
  audio is binary **Opus**, device→server 16 kHz mono 60 ms frames,
  server→device at the rate announced in the server hello (24 kHz typical).
- JSON control message types: `hello`, `listen`, `abort`, `tts`, `stt`, `llm`
  (emotion), `mcp` (tool calls), `system`, `alert`. Documented upstream in
  `docs/websocket.md` and `docs/mcp-protocol.md`.

### Activation, the 6-digit code ceremony

How an unbound device gets claimed on xiaozhi.me, reconstructed from the
vendored sources because no public spec exists: `main/ota.cc` cites a
Feishu wiki that answers 404 anonymously, and upstream's `docs/` never
describes the OTA HTTP exchange. Device side: `main/ota.cc` and
`main/application.cc` in `vendor/xiaozhi-esp32`; server side:
`OTAController.java` and `DeviceServiceImpl.java` in the manager-api of
`vendor/xiaozhi-esp32-server`. Issue #40 builds vinga's onboarding on
this ceremony.

- The OTA response may carry an optional `activation {message, code,
  challenge, timeout_ms}` object. Omitting it, which is what
  vinga-server does today, means no activation is ever required and the
  device proceeds straight to the websocket.
- When it is present, the device shows `message` on screen with the
  activation jingle and speaks `code` digit by digit, each digit an OGG
  clip from the firmware's compiled language assets
  (`Application::ShowActivationCode`). Upstream sets `message` to the
  frontend host, a newline, then the code, and the screen renders it
  exactly that way.
- The device then POSTs `<ota_url>/activate` with
  `{algorithm: "hmac-sha256", serial_number, challenge, hmac}`, the HMAC
  computed over `challenge` with an eFuse-burned key and announced with
  `Activation-Version: 2`. A board with no serial number burned, which
  is what consumer boards like ours are, sends `Activation-Version: 1`
  and a body of `{}`, so the server can identify the poll only by its
  `Device-Id` header; upstream's manager-api does exactly that and never
  reads the body. `202` means keep waiting, `200` means activated,
  anything else counts as a failure.
- **Send `challenge` or the poll is slow.** Without one the firmware
  fails `Activate()` immediately and waits 10 s between attempts instead
  of 3 s. Upstream sets `challenge` to the device's MAC.
- **A device waiting on activation re-checks OTA in a loop.** Activation
  is not a dead end: `Application::CheckNewVersion` polls `/activate` in
  bursts of ten, 3 s apart, then re-runs the whole OTA check and
  re-displays whatever code the fresh response carries, indefinitely.
  Binding a device server-side therefore takes effect within seconds,
  with no power cycle and no button press; only a bound, idle board
  waits for its next boot to re-check OTA. Two consequences: codes can
  be short-lived on the server, since the device fetches and displays a
  fresh one within a couple of minutes, and losing server-side pending
  state costs nothing but a changed number on the screen.
- **The whole ceremony is validated on hardware against vinga-server**
  (2026-08-13, the issue #40 checkpoint), on both available boards. The
  Waveshare factory AMOLED-2.16 (firmware 2.2.4), given the short
  onboarding URL through its portal, showed the server host over a
  6-digit code, polled in the documented 3-second bursts, and went from
  code-on-screen to activated in 36 seconds after a live
  `config device pending claim`, with no server restart, no power cycle, and no
  button press. The stock-firmware Touch-LCD-1.54 (2.4.0), pointed at
  the same server over USB-written NVS, ran the identical ceremony
  through the restart flow (its agent was created after boot). The one
  firmware behavior the sdk-based test simulator could not have shown is
  the redirect intolerance recorded in the captive-portal bullet above;
  `vinga simulator check-in` can, and does. It follows no redirect at
  all, answers one of its own fixed refusals when it meets one, and its
  own lane asserts that exactly one request is made and the target is
  never fetched (issue #248).
- **The OTA response cannot set the device language.** The parser reads
  exactly `activation`, `mqtt`, `websocket`, `server_time` and
  `firmware`; screen chrome, jingle and digit voices are all compiled
  language assets. The device announces its build language in
  `Accept-Language` (`zh-CN` factory, `en-US` ours), so a server can
  localize `message`, but a factory board titles the screen 激活设备 and
  speaks Chinese digits regardless. The one runtime lever is the
  user-only MCP tool `self.assets.set_download_url` plus a reboot, which
  swaps the whole assets bundle and requires hosting one per board.

## What running stock firmware costs the server

vinga-server implements the server half of the protocol above and changes
nothing about it: the device runs stock xiaozhi firmware, `vinga-esp32/`
ships no code, and every message named above is upstream's. That is the
right trade for v1, and it has a price, paid in server-side machinery that
exists only because the device cannot be asked to behave differently.

This is the list to revisit when the device side is tackled (the
`esp_xiaozhi` component spike under "Later versions" in the v1 plan). Each
entry is a constraint, the workaround it forced, and what owning the
firmware would change. Add to it whenever a feature turns out to be shaped
by the device rather than by the problem.

- **The device owns the listening mode, and the server cannot change it.**
  `listen` travels device to server only; vinga-server sends none. Manual
  mode ends an utterance with `listen stop`, auto mode re-arms itself after
  each `tts stop`, and realtime mode asks once and then streams for the rest
  of the connection. Barge-in therefore exists only in realtime, because
  that is the only mode where the microphone is still open during a reply.
  Owning the firmware would let the mode be a server or per-agent decision
  instead of a build-time one.

- **Nothing in the firmware ever closes a realtime audio channel.** This is
  the whole reason for `server.limits.idle_timeout_s`. Worse, the board
  cannot sleep while an audio channel is open (`CanEnterSleepMode` refuses),
  so an abandoned realtime conversation keeps the microphone streaming and
  the board awake until a server-side timer guesses that nobody is there.
  A device that noticed its own silence would not need the guess.

- **Echo cancellation is the device's, and its quality is invisible from
  here.** How much of the assistant's own voice survives the board's AEC and
  reaches the endpointer is the number the entire barge-in gate stack is
  built around: the minimum speech floor, the refractory window, the
  transcribe-to-confirm step, and the `server.barge_in` off switch for
  boards that leak too much. It is also why session capture exists at all,
  since no test lane can produce the number. Owning the firmware means the
  playback reference is available on the device side, where cancelling it is
  a signal-processing problem rather than a statistical one.

- **The wake word is spotted on the chip, and the server takes no part in
  it.** ESP-SR decides on-device, and no server work changes that: the
  planned English wake word (`wn9_hiesp`) is a custom build and nothing
  else. The detection itself is not something the server can hear, tune,
  or substitute for. What the server does get is an after-the-fact report:
  the firmware sends `listen` `detect` with the fired word in `text`, which
  vinga-server currently debug-logs (`device/session.py`) and does not
  retain.

  The trigger audio is a build-time question rather than a settled one.
  `CONFIG_SEND_WAKE_WORD_DATA` defaults to `y` for AFE wake-word builds,
  and under it `ContinueWakeWordInvoke` drains an Opus-encoded copy of the
  audio cached around the trigger into `SendAudio` before sending the
  `detect` report, so such a build does send that span as the
  conversation's first audio. None of our three boards overrides the flag,
  and nothing has been observed on the wire from the prebuilt images we
  actually run, so what those do is open (#112). Earlier versions of this
  note said flatly that the audio never reaches the server; that was read
  from the wrong end of this code path.

- **The device is the MCP server, and discovery is a race.** Tools are
  fetched in a background task after `hello`, deliberately so, because the
  conversation must not wait on a board that may never answer. A first
  utterance that beats discovery runs without device tools; if discovery
  completes, later utterances have them, possibly only from the second one
  on. A device that declared its tools in `hello` would remove the race.

- **The OTA URL is the only field an operator can put a secret into.**
  Every request identifies the board by `Device-Id`, a MAC printed on
  the box and broadcast in the clear in every Wi-Fi frame, and a stock
  board can present no other credential at its first OTA call. The
  token issuer therefore has to be protected by the URL path itself,
  which is why `server.ota_path` carries a long random segment and why
  onboarding means typing a secret into a captive portal. Activation
  (#40) changes what an unknown MAC receives, a claim code instead of
  an empty token, but a bound MAC presented by anyone still gets that
  device's real token, so the path stays load-bearing. Owning the
  firmware would let a device hold a real per-device credential
  instead.

- **We serve configuration through OTA and never images**, but that is our
  choice rather than a limit: the update channel is fully built on the
  device and merely unused. See the next section, which is the one entry
  here that is an unclaimed capability instead of a constraint.

### Updating firmware over the air, once there is firmware

The endpoint vinga-server calls "the OTA endpoint" is an over-the-air
*update* endpoint that xiaozhi also overloads for configuration. We use
only the configuration half. Everything needed for the other half is
already on the board, so the work when the device side starts is signing
and hosting, not plumbing.

- **The partition layout is already A/B.** `partitions/v2/16m.csv`, the
  default for our board, carries `otadata` plus `ota_0` and `ota_1` at
  about 4 MB each (and an 8 MB `assets` SPIFFS). Nothing has to be
  reflashed later to enable updates; the slots are sitting there unused.

- **Path one, at boot.** If the OTA reply carries `firmware: {version,
  url}`, the device compares the offered version against its own
  (`ParseVersion` splits on dots into integers) and, when the offer is
  newer, closes the audio channel and streams the URL straight into the
  inactive slot through `esp_ota_begin`/`write`/`end`, sets the boot
  partition and reboots. `firmware.force = 1` skips the comparison
  entirely, which is the downgrade and re-pin escape hatch. The version it
  compares against is `app_desc->version`, the ESP-IDF project version
  baked into the app descriptor, so shipping an update means bumping that.

- **Path two, mid-session.** The device registers an
  `self.upgrade_firmware(url)` MCP tool, so an upgrade can be triggered
  over the live WebSocket without waiting for a reboot. It is registered
  with `AddUserOnlyTool`, and `GetToolsList` takes a separate
  `list_user_only_tools` flag, so it is kept out of the tool list handed to
  the model: the server can call it deliberately, but no language model can
  decide to reflash a board. Preserve that separation.

- **Rollback is already enabled.** `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y`
  is in `sdkconfig.defaults`, and a new image is marked valid only once it
  has booted *and* completed an OTA check that found nothing newer
  (`esp_ota_mark_app_valid_cancel_rollback`). An image that cannot get that
  far is rolled back by the bootloader, so a bad build costs a reboot
  rather than a bricked board.

- **Nothing verifies who built the image.** This is the part to get right
  before anything ships. `esp_ota_end` validates the image's integrity, its
  checksum and magic, and says nothing about its authorship; the download
  is a plain HTTP GET into the OTA partition, and neither Secure Boot nor
  flash encryption appears in `sdkconfig.defaults`. It matters more here
  than in most projects because the endpoint that would name the firmware
  URL is also the token issuer, and is deliberately protected by nothing
  but a secret path segment. Anyone who can reach it and answer with a
  `firmware.url` owns the board. Serve images over HTTPS at the very least,
  and turn on Secure Boot v2 with signed images before a device leaves a
  network you control.

## Server (xinnan-tech/xiaozhi-esp32-server)

- Components: `xiaozhi-server` (Python 3.10, the conversation core) plus an
  optional Java/Vue management console. **Python-only mode needs no database
  and has no login/activation**: devices just connect; that's our reference
  mode, and the management layer is what vinga-server will reimplement in
  Python if needed.
- Ports: WebSocket **8000** (`ws://host:8000/xiaozhi/v1/`), HTTP **8003**
  (OTA `http://host:8003/xiaozhi/ota/`, vision API).
- Config: base `config.yaml` + override file `data/.config.yaml` (only your
  overrides). Providers are chosen via `selected_module` (VAD/ASR/LLM/VLLM/
  TTS/Memory/Intent), each mapping to a named entry whose `type` selects the
  implementation in `core/providers/`.
- Working zero-key local pipeline: `SileroVAD` + `FunASR` (SenseVoiceSmall,
  ~936 MB `model.pt` from ModelScope into `models/SenseVoiceSmall/`) +
  `OllamaLLM` + `EdgeTTS`. There is an OpenAI-compatible provider
  (`type: openai` with `base_url`) but no native Anthropic provider.
- **Language gotcha**: the reply language is injected into the system prompt
  from `TTS.<selected>.language` and **defaults to Chinese**
  (`core/utils/prompt_manager.py`). Set `language: English` on the TTS entry
  or the LLM answers in Chinese and an English-only TTS voice returns
  "No audio was received".
- Runtime deps: Python 3.10, `libopus`, `ffmpeg`. The pinned `vosk` package
  has no macOS arm64 wheel; safe to skip (it's an alternative ASR backend).
- Working demo override (`data/.config.yaml`):

  ```yaml
  server:
    websocket: ws://<server-ip>:8000/xiaozhi/v1/
  selected_module:
    VAD: SileroVAD
    ASR: FunASR
    LLM: OllamaLLM
    TTS: EdgeTTS
    Memory: nomem
    Intent: function_call
  LLM:
    OllamaLLM:
      type: ollama
      model_name: llama3.1:latest
      base_url: http://localhost:11434
  TTS:
    EdgeTTS:
      type: edge
      voice: en-US-JennyNeural
      language: English
      output_dir: tmp/
  prompt: |
    You are Vinga, a friendly and concise voice assistant.
    Always answer in English, in one or two short sentences, since your
    answers are spoken aloud.
  ```

- Server-side MCP: external MCP servers are attached via
  `data/.mcp_server_settings.json` (stdio/SSE/streamable-http); their tools
  join the LLM's function-calling list alongside the device's own MCP tools.

## Licensing notes

- Both upstream repos are MIT; reuse, modification, and redistribution are
  fine with attribution and preserved license notices
  (see `THIRD_PARTY_LICENSES.md`).
- `edge-tts` (Python package) is GPL-3.0 and uses an unofficial Microsoft
  endpoint; keep TTS engines as optional pluggable providers.
- ESP-SR wake-word models are Espressif-licensed (Espressif chips only);
  model weights (SenseVoice, Silero) are downloaded at deploy time, not
  redistributed.
