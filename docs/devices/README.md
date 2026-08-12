# Device guides

One guide per board samtal targets, describing the hardware in front of
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
The mode belongs to the device either way: the server is told which one
it is and cannot change it, while on the boards that have the
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
firmware, write your server's address into the device's NVS `wifi`
namespace under the key `ota_url` over USB, and provision WiFi from the
device's own captive portal. That one URL is the firmware's only tie to
a backend; the WebSocket endpoint, the device token, and everything
else arrive from your server at runtime, which is why pointing a board
somewhere else is a one-key change rather than a reflash.

The procedure in full, with the serial gotchas it has, is in
[`../xiaozhi-notes.md`](../xiaozhi-notes.md). Each guide links it
directly as well.

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
again a moment later is the remedy.

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
