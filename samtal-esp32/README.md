# samtal-esp32

Device firmware for Samtal, based on
[78/xiaozhi-esp32](https://github.com/78/xiaozhi-esp32).

The device side stays deliberately thin: the firmware's only hard link to a
backend is one OTA/config URL, from which the server delivers the WebSocket
endpoint and everything else at runtime. Planned customizations:

- Samtal server as the default OTA/config endpoint
- English wake word (`Hi ESP`, ESP-SR `wn9_hiesp`) instead of the default
  Chinese one
- English UI language

## Target hardware

| Board | Display | Audio | Links | Status |
|---|---|---|---|---|
| [Waveshare ESP32-S3-ePaper-1.54](https://www.waveshare.com/esp32-s3-epaper-1.54.htm) | 200×200 e-paper | ES8311, single mic | [wiki](https://docs.waveshare.com/ESP32-S3-ePaper-1.54) | planned |
| [Waveshare ESP32-S3-Touch-LCD-1.54](https://www.waveshare.com/esp32-s3-lcd-1.54.htm) | 240×240 LCD (ST7789), CST816 touch | ES8311 + ES7210 (AEC) | [wiki](https://docs.waveshare.com/ESP32-S3-Touch-LCD-1.54) | [working with upstream prebuilt firmware](#using-the-device) |
| [Waveshare ESP32-S3-Touch-AMOLED-2.16](https://www.waveshare.com/esp32-s3-touch-amoled-2.16.htm) | 480×480 AMOLED (CO5300), CST9220 touch | ES8311 + ES7210 (AEC) | [wiki](https://docs.waveshare.com/ESP32-S3-Touch-AMOLED-2.16) | planned |

## Building

Upstream mainline requires ESP-IDF v6.0.x, target `esp32s3`:

```sh
idf.py set-target esp32s3
idf.py menuconfig   # Xiaozhi Assistant → Board Type → Waveshare ESP32-S3-Touch-LCD-1.54
idf.py build flash monitor
```

Until our fork lands here, the fastest path is the upstream prebuilt merged
binary flashed at offset `0x0`, with the OTA URL written to NVS; see
[`../docs/xiaozhi-notes.md`](../docs/xiaozhi-notes.md).

## Using the device

How a board behaves in daily use. Everything here applies to any board
running the upstream firmware, except the last section, whose timings and
buttons are read from that board's own configuration.

### What it listens to, and when

While the board has power, the microphone is live for on-device wake-word
detection. That audio never leaves the board, and an idle device holds no
connection to the server at all.

Once the wake word or a button press opens the audio channel, a device in
realtime mode streams the microphone continuously to the server, silence
included, until the channel closes. Nothing in the firmware closes it when
you stop talking. The only things that do are a short press of the
conversation button, losing the network, the server's configured session
cap, or powering off.

A conversation left open therefore keeps streaming the room, and holds one
of the server's session slots until that cap expires, so ending
conversations deliberately is the habit worth forming. A shorter
server-side idle timeout is tracked in
[issue #20](https://github.com/rafacm/samtal/issues/20).

There is no microphone mute, in hardware or firmware.

### Networks

A board holds up to ten WiFi networks and connects to whichever known
network is strongest when it scans, so adding one cannot disturb another.

The ESP32-S3 has no 5 GHz radio. Phones commonly broadcast a hotspot on
5 GHz by default, where the board cannot see it at all however correct the
credentials are; on iOS the setting that forces 2.4 GHz is Personal
Hotspot, "Maximize Compatibility", and it can reset across OS updates.
Network names are matched byte for byte, which matters because hotspot
names often contain a typographic apostrophe (U+2019) rather than an ASCII
one, and the two are indistinguishable on screen.

### Waveshare ESP32-S3-Touch-LCD-1.54

| Action | Effect |
|---|---|
| Short press PWR | Toggles the conversation. While listening, this closes the channel and stops the microphone streaming. |
| Long press PWR (about 2 s) | Powers off. |
| Leave idle 5 minutes | Powers off by itself. |
| Click volume up or down | Changes volume by 10. |
| Hold volume up | Maximum volume. |
| Hold volume down | Mutes the speaker, not the microphone. |

This board is built with `PowerSaveTimer(-1, 60, 300)`: the screen dims
after 60 s idle and the board powers off after 300 s. Because the first
argument is `-1`, the dim step leaves the microphone and wake-word
detection running, unlike boards that pass a real CPU frequency and shut
the audio input down.

Both timers only run while the audio channel is closed, so neither happens
during an open conversation, however long the silence lasts. That is also
why an abandoned conversation can flatten a battery that would otherwise
have saved itself.

Automatic sleep can be disabled through the NVS flag `sleep_mode`
(namespace `wifi`, default true). Leaving it enabled is recommended: the
five-minute shutdown is currently the main protection against an abandoned
open session.
