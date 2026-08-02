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
| Waveshare ESP32-S3-Touch-LCD-1.54 | 240×240 LCD (ST7789), CST816 touch | ES8311 + ES7210 (AEC) | [product](https://www.waveshare.com/esp32-s3-lcd-1.54.htm) · [doc](https://docs.waveshare.com/ESP32-S3-Touch-LCD-1.54) | working with upstream prebuilt firmware |
| Waveshare ESP32-S3-ePaper-1.54 | 200×200 e-paper | ES8311, single mic | [product](https://www.waveshare.com/esp32-s3-epaper-1.54.htm) · [doc](https://docs.waveshare.com/ESP32-S3-ePaper-1.54) | planned |
| Waveshare ESP32-S3-Touch-AMOLED-2.16 | 480×480 AMOLED (CO5300), CST9220 touch | ES8311 + ES7210 (AEC) | [product](https://www.waveshare.com/esp32-s3-touch-amoled-2.16.htm) · [doc](https://docs.waveshare.com/ESP32-S3-Touch-AMOLED-2.16) | planned |

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
