# Waveshare ESP32-S3-ePaper-1.54

> [!WARNING]
> **This board has not reached working status with vinga.** 🚧 No part
> of this guide has been verified on the hardware; every statement in
> it is read from the upstream board support code. It grows, and gains
> hands-on facts, as the board does.

A 200x200 e-paper display, a single microphone with no echo
cancellation, a speaker, a battery, and two buttons: BOOT and PWR.

Every section below says where its facts come from. For this board the
answer is currently the same everywhere: **read from the upstream board
support code**, or **not verified at all**, which is stated rather than
guessed around. Nothing here is hands-on.

What every board shares is on the [common page](README.md). Read from
the board support code: this board has one microphone and no echo
cancellation, so it runs in **auto** listening mode, where the
microphone is stopped while the device speaks and re-armed for the next
turn. That means a reply cannot be interrupted by talking over it on
this board.

## Controls

Read from the board support code, not verified on hardware:

| Action | Effect |
| --- | --- |
| Click BOOT | Starts a conversation, or ends the one that is open. |
| Click BOOT during startup, before the board has connected | Enters WiFi provisioning without a reboot. |
| Long press PWR | Shows "OFF" on the display and then cuts power to the audio, the display, and the battery rail. |

The board sets no long-press threshold of its own, so the PWR
long-press takes the shared button helper's default, the same default
the Touch-LCD-1.54 board takes and where about two seconds is what
hands-on use of *that* board shows. The threshold has not been measured
on this one.

There are no volume buttons and no touch layer on this board. Volume is
set by voice.

## Wake word

**Not verified.** Which wake word, if any, is enabled in the prebuilt
firmware published for this board has not been checked, and this guide
does not guess. What is known from the sources is only the shape of the
answer: the firmware's default choice for an ESP32-S3 with PSRAM is an
on-chip WakeNet model, and which model an image carries is a property
of that build.

Whatever the answer turns out to be, it does not change what a wake
word is: it wakes the device, never a particular agent. See the
[common page](README.md#what-the-wake-word-does-and-does-not-do).

## Voice commands the device answers

Read from the upstream board support code, not verified on hardware:

- Current device state (volume, screen, battery, network).
- Setting the speaker volume, 0 to 100. On a board with no volume
  buttons this is the only way to change it.
- Switching into WiFi provisioning, which closes the conversation and
  raises the board's access point rather than restarting it. Two
  warnings about this one. The board registers the command with a
  description written in Chinese, so ask for it in plain terms
  ("reconfigure the WiFi"). And unlike the other two boards, this one's
  command carries no instruction to confirm first, so an assistant may
  act on it straight away: expect the conversation to end without being
  asked whether you meant it.
- **No screen brightness command.** The board declares no backlight,
  which is what the brightness command is registered against, so it
  does not exist here.
- A light and dark screen theme command is registered by the shared
  display code this board's display is built on, so it is expected to
  be present. What a light or dark theme means on an e-paper panel has
  not been checked, and neither has whether the command survives to the
  published image.

The availability caveat on the [common page](README.md#talking-to-the-device-itself)
applies here too: these work once the server has finished discovering
the device's tools in the background.

## Display

Read from the board support code, not verified on hardware. Recognized
speech and replies render as the conversation happens, as on every
board, on a 200x200 e-paper panel that supports partial refreshes for
the parts of the screen that change.

Between conversations the shared firmware clears the conversation text
and leaves a standby line and a neutral face, with the status icons for
network and battery above it. That is what the shared application code
does on every board; what an e-paper panel actually settles on, and how
much of the previous screen it keeps until the next refresh, has not
been checked here.

There is no backlight, so there is no dimming, and this board registers
no automatic power-saving timer at all: unlike the LCD and AMOLED
boards, it does not dim after a minute or power itself off after five.
An idle board therefore sits on that screen indefinitely. Powering it
off is the PWR button's job.

## Getting this board onto your server

Write your server's OTA/config address into the device's NVS `wifi`
namespace under the key `ota_url` over USB, then provision WiFi from
the board's captive portal. The procedure is on the
[common page](README.md#writing-the-servers-address-into-nvs).
Whether this board's portal carries a Custom OTA URL field of its own,
which would make the USB step unnecessary, has not been checked.

## Known quirks

- **There are two prebuilt images for this board, for two hardware
  revisions.** Read from the upstream build configuration: one is built
  for a 4 MB flash part and one for an 8 MB part, with different
  partition tables. Flashing the wrong one is not a subtle failure, but
  it is an easy mistake to make from a file listing, so check the
  revision of the board in front of you first.
- Nothing else is known. Hardware-specific quirks arrive here when the
  board is brought up; the network traps that catch most provisioning
  problems are on the [common page](README.md#networks) and are not
  board-specific.
