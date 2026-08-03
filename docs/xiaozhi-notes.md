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
- The v2.4.0 captive portal (WiFi provisioning AP `Xiaozhi-XXXX`,
  `http://192.168.4.1`) has **no OTA-URL field**, but the URL can be written
  directly to NVS over USB (partition at `0x9000`, size `0x4000`):

  ```csv
  # nvs_input.csv
  key,type,encoding,value
  wifi,namespace,,
  ota_url,data,string,http://<server-ip>:8003/xiaozhi/ota/
  ```

  ```sh
  python nvs_partition_gen.py generate nvs_input.csv nvs_new.bin 0x4000
  esptool.py write_flash 0x9000 nvs_new.bin
  ```

  Read the partition first (`read_flash 0x9000 0x4000`) if you want to
  preserve the existing device UUID (namespace `board`, key `uuid`).
  Regenerating replaces the whole partition, so carry over everything
  worth keeping: `wifi/ssid`, `wifi/password`, `board/uuid`,
  `display/theme`, `audio/output_volume`. The `phy` namespace can be
  dropped (the board recalibrates on the next boot and says so with a
  `phy_init: Saving new calibration data` line), and so can `websocket`,
  which the first OTA reply repopulates. Comparing the per-entry CRC32s
  before and after proves the carried values survived byte for byte.
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
- Interaction on this board: short-press PWR toggles the conversation;
  long-press powers off. Wake word in prebuilt builds is Chinese
  ("nǐ hǎo xiǎo zhì"); an English model (`wn9_hiesp`, "Hi ESP") is available
  when building from source.
- App logic lives in `main/application.cc`; protocol in `main/protocols/`;
  device exposes its own MCP tools (volume, brightness, etc.) to the server
  over the conversation channel.

### Driving the board from a terminal session

What a device checkpoint needs when `idf.py monitor` is unavailable (it
wants an interactive terminal). The port is `/dev/cu.usbmodem101` at
115200: the chip's native USB-serial-JTAG, not a UART bridge.

- **Reset with esptool**, which prints the MAC as a bonus:

  ```sh
  esptool.py --chip esp32s3 --port /dev/cu.usbmodem101 \
      --after hard_reset read_mac
  ```

  **Toggling RTS alone does nothing**, whatever the usual "RTS drives EN"
  advice says, because there is no reset pin behind this port. DTR and RTS
  are two bits of a single USB CDC `SET_CONTROL_LINE_STATE` request, and
  the USB-Serial-JTAG controller decodes the pair the way the classic
  auto-reset circuit does: EN goes low only when **RTS is high and DTR is
  low**. pyserial asserts both lines when it opens the port, so a bare
  `setRTS(True)` / `setRTS(False)` toggle moves (DTR=1, RTS=1) to (1, 0)
  and never passes through (0, 1). Measured on the board, one open port,
  each combination held for 200 ms:

  | DTR | RTS | result |
  | --- | --- | ---------- |
  | 1   | 1   | no reset   |
  | 1   | 0   | no reset   |
  | 0   | 1   | **reset**  |

  From pyserial, one line fixes it: `port.setDTR(False)` before the RTS
  toggle. esptool arrives at the same place by another road, which is why
  it works: its bootloader-entry sequence leaves both lines low, so the
  RTS toggle inside its `HardReset` lands on (0, 1). Replay that same
  `HardReset` from pyserial's freshly opened state and it resets nothing,
  which is the trap an earlier version of this note fell into.
- **Read the boot log** with pyserial from the ESP-IDF Python environment
  (`~/.espressif/python_env/idf*/bin/python`), not the system `python3`,
  which has no `serial` module. Reset and read in one process that holds
  the port open; reopening it races the boot output away.
- **Read and parse NVS** to prove what the device persisted from an OTA
  reply (`nvs_tool.py` lives in
  `components/nvs_flash/nvs_partition_tool/` in ESP-IDF):

  ```sh
  esptool.py --chip esp32s3 --port /dev/cu.usbmodem101 --baud 460800 \
      read_flash 0x9000 0x4000 nvs.bin
  nvs_tool.py -d written nvs.bin
  ```

  `-d written` matters: NVS is log-structured, so without it erased entries
  are listed beside live ones and read as though both were current.
- **A conversation still needs a human.** The board opens its websocket
  only on a PWR press or the wake word, so that one step cannot be
  scripted. Everything up to it can be: reset, boot log, the OTA exchange,
  and the agent the server resolved the device to.

## Device ↔ server protocol

- Device POSTs system info to the OTA URL (headers `Device-Id` = MAC,
  `Client-Id` = UUID); response JSON contains `websocket {url, token,
  version}` (and/or `mqtt {...}`), optional `firmware {version, url}` and
  optional `activation {...}` (omit it and no activation is ever required).
- **A successful OTA check does not mean the device is authorised.** A
  board whose MAC is missing from samtal-server's `devices:` allowlist
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

## Server (xinnan-tech/xiaozhi-esp32-server)

- Components: `xiaozhi-server` (Python 3.10, the conversation core) plus an
  optional Java/Vue management console. **Python-only mode needs no database
  and has no login/activation**: devices just connect; that's our reference
  mode, and the management layer is what samtal-server will reimplement in
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
    You are Samtal, a friendly and concise voice assistant.
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
