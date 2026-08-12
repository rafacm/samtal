# Waveshare ESP32-S3-Touch-AMOLED-2.16

> [!WARNING]
> **This board has not reached working status with samtal.** 🚧 It has
> been brought up far enough to learn the things below, and no
> further; this guide grows as the board does.

A 480x480 AMOLED with a capacitive touch layer, two microphones with
hardware echo cancellation, a speaker, a battery, one side button
marked BOOT, and a PWR button wired to the board's power-management
chip rather than to the processor.

Every section below says where its facts come from, in one of three
ways: **verified in hands-on use** with this board, **read from the
upstream board support code** and not verified on hardware, or **not
verified at all**, which is stated rather than guessed around.

What every board shares is on the [common page](README.md). Read from
the board support code: this board's build enables echo cancellation,
so it starts in **realtime** listening mode, where the microphone
streams for the whole session and a reply can be interrupted. The BOOT
double-click below moves it out of that mode.

## Controls

Verified in hands-on use:

| Action | Effect |
| --- | --- |
| Click the side BOOT button | Starts a conversation, or ends the one that is open. |
| Hold PWR for about 4 s | Cuts the power. This is the power-management chip acting on its own; the firmware never sees the button, so nothing on screen acknowledges it. |
| Touch the screen | Nothing. Touch does not start a conversation on this board. |

Read from the board support code, not verified on hardware:

- Clicking BOOT while the board is still starting up, before it has
  connected, enters WiFi provisioning without a reboot.
- Double-clicking BOOT while the board is idle toggles echo
  cancellation, which also drops the board out of realtime listening
  into auto mode and takes the ability to interrupt a reply with it.
  The gesture is ignored unless the board is idle, and it closes any
  open conversation.
- The board has no volume buttons. Volume is set by voice.
- The 4-second hold that powers the board down is written into the
  power-management chip by the firmware at startup, which is where the
  hands-on figure above comes from as well.

## Wake word

Verified in hands-on use, on the firmware Waveshare ships the board
with: the wake word is **"Sophia"**, a WakeNet model
(`wn9_sophia_tts`) compiled into that image. It has nothing to do with
any agent name configured on the server side; as everywhere,
[the wake word wakes the device](README.md#what-the-wake-word-does-and-does-not-do)
and the device's default agent answers.

Also verified: Waveshare's downloadable factory image for this board
(`ESP32-S3-Touch-AMOLED-2.16-FactoryOnly-260318.bin`) carries the
Chinese model `wn9_nihaoxiaozhi_tts` instead. Reflashing that image
therefore changes the wake word to "nǐ hǎo xiǎo zhì" without saying so
anywhere.

Not verified: which wake word upstream's own prebuilt image for this
board carries. This guide does not guess.

## Voice commands the device answers

Read from the upstream board support code, not verified on hardware.
The board publishes the firmware's usual set plus its own WiFi command:

- Current device state (volume, screen, battery, network).
- Setting the speaker volume, 0 to 100.
- Setting the screen brightness, 0 to 100.
- Switching the screen between the light and dark theme.
- Switching into WiFi provisioning, which closes the conversation and
  raises the board's access point rather than restarting it, and which
  the firmware asks the assistant to confirm with you first.

The availability caveat on the [common page](README.md#talking-to-the-device-itself)
applies here too: these work once the server has finished discovering
the device's tools in the background.

## Display

Not verified on hardware. Read from the board support code: recognized
speech and replies render as the conversation happens, as on every
board; the panel is driven at 480x480 and its brightness is a panel
command rather than a backlight pin; the board dims the screen after 60
seconds idle and powers itself off after 300 seconds, under the same
conditions as elsewhere (neither timer runs while a conversation
channel is open, both depend on the NVS `sleep_mode` flag, and the
board support code enables them when it notices the board running on
battery and disables them when external power returns).

The touch layer is initialized and registered as an input, but the
firmware defines no touch controls, which matches what hands-on use
showed.

## Getting this board onto your server

Write your server's OTA/config address into the device's NVS `wifi`
namespace under the key `ota_url` over USB, then provision WiFi from
the board's captive portal. The procedure is in
[`../xiaozhi-notes.md`](../xiaozhi-notes.md), with the size caveat in
the next section.

## Known quirks

- **The board can wedge during power-management initialization.**
  Verified in hands-on use: USB still enumerates and the port appears,
  but nothing responds and no software reset recovers it. The recovery
  is to remove *all* power, which means unplugging USB and
  disconnecting any battery, or holding PWR for about 4 seconds for the
  power-management chip's own power-off if it still responds, then
  waiting about ten seconds before restoring power. Unplug and replug
  on its own has only been verified on a setup with no battery
  attached; with a battery connected, USB removal does not remove
  power.
- **The NVS partition size is a property of the image, not of the
  board.** Verified in hands-on use: on the firmware Waveshare ships,
  the NVS partition is `0x6000`, where the walkthrough in
  [`../xiaozhi-notes.md`](../xiaozhi-notes.md) uses `0x4000`.
  Upstream's own images use `0x4000` as well, and put the OTA
  bookkeeping and the radio calibration data immediately behind it, so
  a `0x6000` partition written onto an upstream image would overwrite
  both. The size is not something to copy from this page or from the
  walkthrough: read the partition table of the image actually flashed
  on the board in front of you, and use the size it gives, before
  writing anything to flash.
