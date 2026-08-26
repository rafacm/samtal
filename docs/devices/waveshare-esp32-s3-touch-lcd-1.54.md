# Waveshare ESP32-S3-Touch-LCD-1.54

A 240x240 LCD with a capacitive touch layer, two microphones with
hardware echo cancellation, a speaker, a battery, and three buttons:
PWR on one side, volume up and volume down on the other. This is the
board vinga is developed against, and the only one currently at
working status.

What every board shares, and this guide does not repeat, is on the
[common page](README.md): what the microphone does while idle and in
conversation, the WiFi network rules, and how the device's own controls
reach the assistant. This board starts in **realtime** listening mode,
the mode described there for boards with echo cancellation: while a
conversation is open the microphone streams continuously, and the
assistant can be interrupted while it is speaking. The
echo-cancellation gesture below is the one thing that moves it out of
that mode.

Unless a section says otherwise, what follows describes the board
running upstream's prebuilt firmware, which is what vinga uses today.

## Controls

| Action | Effect |
| --- | --- |
| Short press PWR | Starts a conversation, or ends the one that is open. While the assistant is speaking, it stops the reply immediately. |
| Long press PWR (about 2 s) | Powers the board off. |
| Double-click PWR | Turns the screen off, and turns it back on at half brightness. The board keeps running either way. |
| Triple-click PWR | Switches into WiFi provisioning without restarting: the board drops its connection, raises its own access point, and serves the captive portal. |
| Click volume up or volume down | Changes the volume by 10, showing the new level on the screen. |
| Hold volume up | Maximum volume. |
| Hold volume down | Mutes the speaker. It never mutes the microphone; there is no microphone mute on this board. |
| Double-click volume down, while idle only | Toggles echo cancellation. See the warning below. |
| Leave idle 5 minutes | Powers off by itself, under the conditions in [Display](#display). |

The volume gestures work at any time. The PWR gestures are armed the
first time the button is released after the board has started, so a
board that has been running for any length of time simply has them.

**The echo-cancellation toggle changes more than the audio.** With echo
cancellation off, the firmware drops out of realtime listening into
auto mode, and in auto mode the microphone is stopped while the
assistant speaks: interrupting a reply by talking over it stops working
until the toggle is set back. It also closes any conversation that is
open at the time, which is why the gesture is accepted only while the
board is idle. The screen shows a notification saying which way it went.
Unless you are deliberately testing without echo cancellation, leave
this one alone.

## Wake word

The upstream prebuilt firmware this board was tested on, version 2.4.0,
ships the Chinese wake word, "nǐ hǎo xiǎo zhì", and it is enabled:
saying it opens a session without touching a button. An English model
("Hi ESP", ESP-SR `wn9_hiesp`) exists in the firmware sources, and no
prebuilt image inspected for this project has carried it, so reaching
it means building the firmware; vinga's own build will use it. 🚧

The wake word wakes the device, and never a particular agent. It is
spotted on the chip by a model compiled into the firmware, the server
plays no part in the decision, and the device's default agent is what
answers. The whole story is on the [common page](README.md#what-the-wake-word-does-and-does-not-do),
and the reasoning behind it is in [`../concepts.md`](../concepts.md).

What the server does and does not learn is worth stating exactly,
because an always-on microphone deserves a precise answer:

- While the device is idle there is no connection to the server at all,
  so nothing that is heard before the wake word can reach anything.
- After the wake word fires, the device opens the channel and reports
  the word that fired, after the fact, as a message naming it.
  vinga debug-logs that message and does not retain it; the report is
  described in
  [`../xiaozhi-notes.md`](../xiaozhi-notes.md#the-wake-word-is-spotted-on-the-chip-and-the-server-takes-no-part-in-it).
- One thing is unsettled, and worth knowing rather than glossing over.
  The firmware has a build setting, on by default in its sources, that
  also sends the short span of audio it had buffered around the trigger
  phrase as the conversation's first audio, so that the assistant can
  react to whatever was said in the same breath as the wake word.
  Whether the prebuilt image this board runs was built that way has not
  been checked on the wire, and the protocol notes linked above record
  it as open for exactly that reason, so treat the exact extent of what
  leaves the board at the moment of waking as an open question until it
  has been.

## Voice commands the device answers

These are the device's own controls, published to the server as MCP
tools and carried out by the board itself. Phrasings are examples;
the assistant maps what you say to the control, so anything equivalent
works.

- **"What is the volume?", "how much battery is left?", "are you
  online?"** Reports the current state of the board: speaker volume,
  screen, battery, and network.
- **"Set the volume to 40."** Sets an exact level from 0 to 100. The
  buttons step by 10; the voice command is how you land on a number
  between the steps.
- **"Set the screen brightness to 30."** Also 0 to 100.
- **"Switch to the dark theme", "use the light theme."** Changes the
  screen theme.
- **"Reconfigure the WiFi."** Switches the board into WiFi
  provisioning, the same place the PWR triple-click leads and by the
  same route: it closes the conversation and raises the access point
  rather than restarting the board. The firmware asks the assistant to
  confirm with you before doing it, so expect to be asked.

As on every board, these become available once the server has finished
discovering the device's tools in the background, which a request made
in the very first moment of a session can beat; asking again a moment
later is the remedy. See
[the common page](README.md#talking-to-the-device-itself).

Firmware updates over the air are deliberately not in this list. The
firmware publishes them as a separate class of tool that is kept out of
the set offered to the language model, so no assistant can decide to
reflash the board.

## Display

Recognized speech and the assistant's replies render on the screen as
the conversation happens, above a status line that carries the
connection and battery state, with notifications (a volume change, for
instance) appearing briefly over it.

The interface language of upstream's prebuilt firmware is Chinese, and
the language is compiled in rather than configured, so it stays Chinese
until the board runs a build made with another one. An English
interface is part of vinga's planned firmware build. 🚧 The language of
the *conversation* is a server-side setting and is unaffected by this.

The touch layer is initialized and registered as an input, but the
firmware's screens define no touch controls on this board, so tapping
the display does nothing. Every control is a button.

**Dimming and self power-off.** When the board is left alone it dims
the screen after 60 seconds and powers itself off after 300 seconds.
Four conditions qualify that:

- Neither timer runs while a conversation channel is open, however long
  the silence in it lasts, and neither runs while audio is still
  playing. This is why the server's idle timeout matters to battery
  life and not only to privacy: the board cannot start counting down to
  its own shutdown until the server hangs up.
- Both timers apply only while automatic sleep is enabled. The NVS
  `sleep_mode` flag in the `wifi` namespace turns it off; it defaults to
  on, and leaving it on is recommended.
- The board also follows its power state: the board support code
  enables the timers when it notices the board has started running on
  battery, and disables them when external power comes back. A board
  that has been on external power since it booted still has them
  enabled. This is read from the board support code; the resulting
  behavior in each power state has not been verified on hardware.
- The dim step on this board leaves the microphone and wake-word
  detection running, so a dimmed board still wakes when you say the
  wake word. Boards that shut their audio input down during the dim
  configure the timer differently.

What the idle screen shows between conversations (a standby line, a
neutral face, and no conversation text) is read from the firmware
sources and has not been verified against the physical board.

## Getting this board onto your server

Verified in hands-on use (2026-08-12/13): the captive portal of the
upstream prebuilt firmware this board was tested on, version 2.4.0,
carries no Custom OTA URL field, so the server's address goes in over
USB rather than through the portal.

Write it into the device's NVS `wifi` namespace under the key
`ota_url` over USB, then provision WiFi from the board's captive
portal. The procedure, including how to preserve what the partition
already holds, is on the
[common page](README.md#writing-the-servers-address-into-nvs); this
board's NVS partition is `0x4000`. Resetting the board, reading its
boot log and reading its NVS back are
[there too](README.md#driving-a-board-from-a-terminal-session).

## Known quirks

- **Stopping a reply with the PWR button is a local stop.** The board
  aborts the reply itself, immediately, rather than going through the
  server's decision about whether it heard you interrupt. It is the
  reliable way to cut a reply short, and it is not the same event as
  talking over the assistant.
- **The PWR gestures need one release to arm.** The board registers the
  click, long-press, double-click and triple-click handlers the first
  time PWR is released after boot. Read from the board support code and
  not verified on hardware; if it is visible at all it would be as a
  first press after boot that appears to do nothing.
- Nothing else board-specific has been validated. The 5 GHz hotspot
  trap and the network rules that catch most provisioning problems are
  on the [common page](README.md#networks), because they are not
  specific to this board.
