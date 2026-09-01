# vinga-esp32

Device firmware for Vinga, based on
[78/xiaozhi-esp32](https://github.com/78/xiaozhi-esp32).

The device side stays deliberately thin, which is
[a guideline the whole project is held to](../docs/architecture/guidelines.md#thin-device-smart-server):
the firmware's only hard link to a backend is one OTA/config URL, from
which the server delivers the WebSocket endpoint and everything else at
runtime. Planned customizations:

- Vinga server as the default OTA/config endpoint
- English wake word (`Hi ESP`, ESP-SR `wn9_hiesp`) instead of the default
  Chinese one
- English UI language

## Target hardware

The same three boards the [project README](../README.md#hardware) lists, in the same order. The Touch-LCD-1.54 is the board vinga is developed and tested on; the other two are targets.

| Board | Display | Audio | Links | Status |
|---|---|---|---|---|
| [Waveshare ESP32-S3-Touch-LCD-1.54](https://www.waveshare.com/esp32-s3-lcd-1.54.htm) | 240×240 LCD (ST7789), CST816 touch | ES8311 + ES7210 (AEC) | [guide](../docs/devices/waveshare-esp32-s3-touch-lcd-1.54.md) · [wiki](https://docs.waveshare.com/ESP32-S3-Touch-LCD-1.54) | [**working** (upstream firmware)](../docs/devices/waveshare-esp32-s3-touch-lcd-1.54.md) |
| [Waveshare ESP32-S3-Touch-AMOLED-2.16](https://www.waveshare.com/esp32-s3-touch-amoled-2.16.htm) | 480×480 AMOLED (CO5300), CST9220 touch | ES8311 + ES7210 (AEC) | [guide](../docs/devices/waveshare-esp32-s3-touch-amoled-2.16.md) · [wiki](https://docs.waveshare.com/ESP32-S3-Touch-AMOLED-2.16) | planned 🚧 |
| [Waveshare ESP32-S3-ePaper-1.54](https://www.waveshare.com/esp32-s3-epaper-1.54.htm) | 200×200 e-paper | ES8311, single mic | [guide](../docs/devices/waveshare-esp32-s3-epaper-1.54.md) · [wiki](https://docs.waveshare.com/ESP32-S3-ePaper-1.54) | planned 🚧 |

## Building

Upstream mainline requires ESP-IDF v6.0.x, target `esp32s3`:

```sh
idf.py set-target esp32s3
idf.py menuconfig   # Xiaozhi Assistant → Board Type → Waveshare ESP32-S3-Touch-LCD-1.54
idf.py build flash monitor
```

Until our fork lands here, the fastest path is the upstream prebuilt merged
binary flashed at offset `0x0`, with the OTA URL written to NVS; the
procedure is on the device guides'
[common page](../docs/devices/README.md#writing-the-servers-address-into-nvs),
and what the URL is for is in
[`../docs/xiaozhi-notes.md`](../docs/xiaozhi-notes.md#the-firmware-and-the-one-url-that-points-it-at-a-server).

## Using the device

How a board behaves in daily use now lives in
[`../docs/devices/`](../docs/devices/README.md), one guide per board:
which button starts and stops a conversation, how long to hold PWR to
power off, whether a wake word is enabled and which word it is, the
commands the device answers by voice, and how the display behaves. The
[common page](../docs/devices/README.md) there carries what every board
running the upstream firmware shares, including the listening,
onboarding, and WiFi network behavior this section used to describe.
The [Touch-LCD-1.54 guide](../docs/devices/waveshare-esp32-s3-touch-lcd-1.54.md)
carries that board's controls and power-saving behavior, completed
against the board support code.
