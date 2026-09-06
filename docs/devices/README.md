# Board guides

One guide per board vinga targets, describing the hardware in front of
you: which button starts a conversation, whether a wake word is
listening and which word it is, what you can ask the device itself to
do by voice, and what the display is telling you.

| Board | Guide | Status |
| --- | --- | --- |
| Waveshare ESP32-S3-Touch-LCD-1.54 | [guide](waveshare-esp32-s3-touch-lcd-1.54.md) | working (upstream firmware) |
| Waveshare ESP32-S3-ePaper-1.54 | [guide](waveshare-esp32-s3-epaper-1.54.md) | planned 🚧 |
| Waveshare ESP32-S3-Touch-AMOLED-2.16 | [guide](waveshare-esp32-s3-touch-amoled-2.16.md) | planned 🚧 |

The rest of this page is the behavior every board running the upstream
firmware shares, so that each guide can stay short and cover only what
is specific to its own board.

## On this page

- [What the device listens to, and when](#what-the-device-listens-to-and-when): the three listening modes, which boards use which, and what the microphone is doing between conversations.
- [Networks](#networks): the WiFi rules every board shares, including the 5 GHz trap that catches most provisioning.
- [Getting a board onto your server](#getting-a-board-onto-your-server): the one URL that ties a board to a backend, and the two ways it gets there.
- [Driving a board from a terminal session](#driving-a-board-from-a-terminal-session): resetting, reading the boot log and reading NVS back, with the reset behavior that is not what the usual advice says.
- [Talking to the device itself](#talking-to-the-device-itself): the controls a board publishes as tools, so an agent can turn it down.
- [What the wake word does, and does not, do](#what-the-wake-word-does-and-does-not-do): what waking is, what it is not, and why it never picks an agent.

## What the device listens to, and when

**While idle.** An idle device holds no connection to the server at
all, so nothing it hears can reach anything until a session opens. On a
board whose firmware has a wake word enabled, the microphone is
monitored on the board itself for that one phrase, and only that phrase
opens a session. A board with no wake word enabled listens for nothing
until a button opens the channel. Each guide says which of the two its
board is, and for which firmware.

**In conversation.** Once the channel is open, what the microphone does
follows the board's listening mode. Which mode a board starts in is
settled when its firmware is built, by whether echo cancellation is on.
The mode belongs to the device either way: the server is
[told which one it is and cannot change it](../xiaozhi-notes.md#the-device-owns-the-listening-mode-and-the-server-cannot-change-it),
while on the boards that have the
echo-cancellation gesture the user can, and switching it moves the
board between the two modes below. Each guide names the mode its board
starts in.

- **Realtime**, the mode on boards with echo cancellation. The
  microphone streams continuously for the whole session, silence
  included, and the device can still hear you while it is speaking,
  which is what makes interrupting a reply possible at all. Nothing in
  the firmware closes the channel when you stop talking. What normally
  closes it is the server's idle timeout, two minutes with no
  conversation by default, counted from the end of the last thing said
  by either side, so a pause inside a conversation never trips it. The
  other ways it closes are the conversation button, losing the network,
  the server's session cap, and powering off. Pressing the button when
  you are finished is still worth doing: it stops the streaming now
  rather than in two minutes.
- **Auto**, the mode on boards without echo cancellation. The
  microphone is stopped while the device speaks and re-armed for the
  next turn once the reply has been played, so a reply cannot be
  interrupted by talking over it, and the device stops sending when it
  hears you finish. The session itself stays open across all of that:
  it is the microphone that pauses and re-arms, not the connection. The
  server's idle timeout does not apply here, because an auto-mode board
  is not streaming a room to anybody between turns; what ends an
  auto-mode session is the conversation button, losing the network, the
  server's session cap, or powering off.
- **Manual** is push to talk. None of the boards above uses it.

No board here has a microphone mute, in hardware or in firmware. The
volume controls, where a board has them, act on the speaker only.

## Networks

A board holds up to ten WiFi networks and connects to whichever known
network is strongest when it scans, so adding one cannot disturb
another.

The ESP32-S3 has no 5 GHz radio. Phones commonly broadcast a hotspot on
5 GHz by default, where the board cannot see it at all however correct
the credentials are; on iOS the setting that forces 2.4 GHz is Personal
Hotspot, "Maximize Compatibility", and it can reset across OS updates.
Network names are matched byte for byte, which matters because hotspot
names often contain a typographic apostrophe (U+2019) rather than an
ASCII one, and the two are indistinguishable on screen.

## Getting a board onto your server

Onboarding is the same procedure on every board: flash the prebuilt
firmware ([how](flashing.md)), write your server's address into the
device's NVS `wifi` namespace under the key `ota_url` over USB, and
provision WiFi from the device's own captive portal. That one URL is the firmware's only tie to
a backend; the WebSocket endpoint, the device token, and everything
else arrive from your server at runtime, which is why pointing a board
somewhere else is a one-key change rather than a reflash.

Some builds let the portal do the whole job. Whether a board's captive
portal carries a Custom OTA URL field on its Advanced tab is a property
of the build it is running rather than a firmware-version threshold, so
each guide says what its own board was observed to have. Where the field
exists, a board can be pointed at a backend with no USB cable at all.
Where it is absent, or where a provisioned board offers no way back into
the portal at all, the USB route below is what works, and it is the
recovery path to count on rather than retyping the URL into a portal.

What the exchange behind all of this looks like on the wire, and why a
device-facing route may never answer a board with a redirect, is in
[`../xiaozhi-notes.md`](../xiaozhi-notes.md#the-firmware-and-the-one-url-that-points-it-at-a-server).

### Writing the server's address into NVS

Verified in hands-on use on the Touch-LCD-1.54 (2026-08-12/13, and
again on 2026-09-06 with the tooling below). The partition lives at
`0x9000`; its size is `0x4000` on the Touch-LCD-1.54 and `0x6000` on
the AMOLED-2.16 factory image, and the honest way to know is to read
the partition table at `0x8000` of the image actually flashed on the
board in front of you rather than assuming either.

The address to write is the one `vinga info` prints, key and all,
rather than a path typed by hand. WiFi credentials live in the same
namespace, so a board that has just been flashed takes both in one
write and never has to raise its captive portal:

```csv
# nvs_input.csv, which will hold a password: `umask 077` first
key,type,encoding,value
wifi,namespace,,
ssid,data,string,Your Network
password,data,string,your-wifi-password
ota_url,data,string,http://192.168.1.10:8003/x/AB2C4D5E/
```

```sh
uvx --with esp-idf-nvs-partition-gen python -m esp_idf_nvs_partition_gen \
    generate nvs_input.csv nvs_new.bin 0x4000

uvx --from esptool esptool --chip esp32s3 --port /dev/cu.usbmodem1101 \
    --baud 460800 write-flash 0x9000 nvs_new.bin
```

Then delete the CSV. Neither tool is installed here: both are GPL, so
this project shells out to them and `uvx` runs them from nowhere.
[Flashing a board](flashing.md) has the serial gotchas they share,
including why the baud rate is 460800 and how to find the port.

Read the partition first (`read-flash 0x9000 0x4000`) if you want to
preserve the existing device UUID (namespace `board`, key `uuid`).
Regenerating replaces the whole partition, so carry over everything
worth keeping: `wifi/ssid`, `wifi/password`, `board/uuid`,
`display/theme`, `audio/output_volume`. The `phy` namespace can be
dropped (the board recalibrates on the next boot and says so with a
`phy_init: Saving new calibration data` line), and so can `websocket`,
which the first OTA reply repopulates. Comparing the per-entry CRC32s
before and after proves the carried values survived byte for byte.

A key is read out of the namespace it is in, not by its name alone:
`password` exists in `wifi` and again in `mqtt`, and a namespace is
addressed by an index the partition assigns rather than by its name,
so anything reading credentials back has to resolve that index first
or it will hand you the wrong secret.

## Driving a board from a terminal session

Verified in hands-on use on the Touch-LCD-1.54 (2026-08-12/13). What is
board-specific here is the port name; the reset behavior below is the
ESP32-S3's own USB-serial-JTAG rather than anything one board does, so
it applies to every board on this page.

What a device checkpoint needs when `idf.py monitor` is unavailable (it
wants an interactive terminal). The port was `/dev/cu.usbmodem101` at
115200 on macOS: the chip's native USB-serial-JTAG, not a UART bridge.

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

## Talking to the device itself

The device publishes its own controls to the server as MCP tools over
the same conversation channel it sends audio on, so "set the volume to
40" is carried out by the device rather than answered as a request the
assistant cannot fulfil. Because it is one mechanism, it works the same
way on every board; which controls exist varies by board, and each
guide lists its own.

One honest caveat about timing: the server asks a device for its tool
list in a background exchange just after the session opens, deliberately,
so that a board which never answers cannot stall the conversation. A
request made in the first breath of a session can therefore arrive
before that discovery has finished, and discovery is not guaranteed to
finish at all. If a device command is ignored the first time, asking
again a moment later is the remedy. The exchange itself is in
[`../xiaozhi-notes.md`](../xiaozhi-notes.md#the-device-is-the-mcp-server-and-discovery-is-a-race).

## What the wake word does, and does not, do

The wake word wakes the *device*. It is spotted on the chip itself by a
fixed set of compiled models, so it is a property of the firmware a
board is running, not of the assistant you are talking to, and it
cannot be assigned per agent on stock firmware. When a session opens,
the device's default agent answers. A board whose wake word happens to
match the name of the agent that answers is a pleasing coincidence of
configuration, and it stops being true the moment a second agent is
bound to that device.

Each guide states the wake word for the firmware its board was observed
running, because "the upstream prebuilt" and "the vendor's shipped
image" are channels rather than versions, and they do not always carry
the same model.
