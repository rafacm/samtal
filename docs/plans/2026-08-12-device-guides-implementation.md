# Device guides: implementation notes

Companion to [`2026-08-12-device-guides.md`](2026-08-12-device-guides.md).
One section per milestone, appended in the change that ticks it.

## Write the device guides and link them from the hardware tables

Delivered: `docs/devices/README.md` (the common page and index), the
full `waveshare-esp32-s3-touch-lcd-1.54.md` guide, the
`waveshare-esp32-s3-epaper-1.54.md` and
`waveshare-esp32-s3-touch-amoled-2.16.md` stubs, the "Using the device"
move out of `samtal-esp32/README.md` with a pointer left behind, both
hardware tables linking the guides as `guide · wiki` with the
Touch-LCD status link repointed, the root README's Getting Started
final step, the `docs/README.md` index section and introduction, and
the CHANGELOG entry.

### Deviations from the plan

Three, all of them the plan's factual expectations meeting the board
support code at the recorded commit.

1. **The ePaper board does publish a screen-theme command.** The plan
   says its inventory is "WiFi re-provisioning but no brightness or
   theme, having no backlight". Brightness is right: the brightness
   tool is registered only when the board returns a backlight, and this
   board returns none. The theme tool is not gated on a backlight; it
   is registered whenever the display has a theme, and this board's
   display derives from the shared LCD display class whose constructor
   loads one. The stub therefore says the command is expected to be
   present, and that what a light or dark theme means on an e-paper
   panel is unverified.

2. **The ePaper board registers no power-saving timer at all.** The
   plan's stub description does not mention one either way, but the
   common expectation set by the other two boards is a dim after 60 s
   and a self power-off after 300 s. This board constructs no
   `PowerSaveTimer`, so neither happens; powering it off is the PWR
   button's job. Stated in the stub's display section.

3. **The Touch-LCD wake-word section does not say the trigger audio
   never leaves the board.** The plan's amended wording (review finding
   1) has the section separate "the unreachable trigger audio" from the
   reported word. The firmware sources contradict "unreachable": see
   the discovery below. The section states what is certain (an idle
   board holds no connection, the detection is on-chip and the server
   plays no part, the word report is debug-logged and not retained) and
   presents the extent of what is sent at the moment of waking as an
   open question, which is what the evidence supports.

Everything else follows the plan, including its review-round
resolutions.

### Discoveries

- **The firmware may send the wake-word audio itself.** At the recorded
  vendor commit, `CONFIG_SEND_WAKE_WORD_DATA` defaults to `y` for AFE
  wake-word builds, and `Application::ContinueWakeWordInvoke` under
  that flag drains an Opus-encoded copy of the audio cached around the
  trigger into `SendAudio` before sending the `listen` `detect` report.
  `AfeAudioEngine::EncodeWakeWordData` is what fills that cache. No
  board config in this repository's three boards overrides the flag.

  This contradicts `docs/concepts.md`, `docs/glossary.md` and
  `docs/xiaozhi-notes.md`, which all say the wake-word audio never
  reaches the server. None of the three was edited here: the reading is
  source-derived and nothing has been observed on the wire, and a
  protocol claim that three documents agree on should be corrected with
  evidence rather than with a code read. The guide presents it as
  unsettled; confirming it on the wire is follow-up work, and it is
  listed under the unverified claims below.

- **The Touch-LCD power-save timers follow the charging state, and not
  in the obvious direction.** `GetBatteryLevel` calls
  `SetEnabled(discharging)` on every transition, so the timers are
  enabled when the board starts running on battery and *disabled* when
  external power returns. A board that has been externally powered
  since boot still has them enabled, because no transition has
  happened. The AMOLED board does the same. The guides state the
  mechanism and say the resulting behavior per power state is not
  verified.

- **The PWR gestures on the Touch-LCD are armed late.** The board
  registers the click, long-press, double-click and triple-click
  handlers inside a one-shot press-up handler that then unregisters
  itself, so they exist only after PWR has been released once since
  boot. Recorded in the guide's quirks as source-derived.

- **Neither board's display layer registers touch handlers.** Touch is
  initialized and added to LVGL on both touch boards, but no display
  code registers a click or press event, which is the source-side
  explanation for the hands-on observation that touch does nothing on
  the AMOLED board.

- **The ePaper board ships as two prebuilt variants**, for a 4 MB and
  an 8 MB flash part with different partition tables. Recorded in that
  stub's quirks.

### Provenance matrix

Source-derived claims were read from the `vendor/xiaozhi-esp32`
checkout at commit `dd99da00dc4c89ed4ab07fcec038c03f13f4de50`
("Add M5Stack CoreP4 board support.", 2026-07-29). `vendor/` is not
committed, so that commit is the reproducible reference.

Hardware-verified claims come from this project's recorded bring-up
sessions, described rather than pasted: no NVS dumps, boot logs or OTA
responses appear in any committed file.

Firmware identity behind the wake-word claims:

| Claim | Firmware it was observed on |
| --- | --- |
| Touch-LCD-1.54 wake word is Chinese ("nǐ hǎo xiǎo zhì") and enabled | upstream prebuilt merged binary, firmware version 2.4.0 |
| Touch-LCD-1.54 interface language is Chinese | the same 2.4.0 prebuilt |
| An English model ("Hi ESP", `wn9_hiesp`) exists only in source builds | firmware sources at the recorded commit; no prebuilt observed carrying it |
| AMOLED-2.16 wake word is "Sophia" (`wn9_sophia_tts`) | the firmware Waveshare shipped on the board, as received; no version string recorded |
| Waveshare's factory image carries `wn9_nihaoxiaozhi_tts` instead | the downloadable image `ESP32-S3-Touch-AMOLED-2.16-FactoryOnly-260318.bin` |
| ePaper-1.54 wake word | none: not observed on any firmware, and the stub says so |

#### Common page (`docs/devices/README.md`)

| Claim | Provenance |
| --- | --- |
| Idle device holds no connection to the server | source-derived (`application.cc`: the channel opens on wake or button), and consistent with the repository's protocol notes |
| Wake-word monitoring happens on-board, only where a wake word is enabled | source-derived |
| Listening mode is build-time, device-owned, server cannot change it | source-derived (`GetDefaultListeningMode`, `listen` is device to server only) and already recorded in `docs/xiaozhi-notes.md` |
| Realtime streams continuously, allows interrupting a reply | source-derived; matches `docs/glossary.md` |
| Auto stops the microphone during playback and re-arms per turn, no barge-in | source-derived (`HandleStateChangedEvent` disables voice processing when speaking outside realtime) |
| Idle timeout, two minutes by default, counted from the last thing said | samtal-server behavior, already documented in the repository |
| No microphone mute on any board | source-derived (no such control in any of the three board files) |
| Ten stored networks, 2.4 GHz only, 5 GHz hotspot trap, byte-for-byte SSID matching | hardware-verified: carried unchanged from the firmware README section this page absorbs, which was written from hands-on provisioning |
| Onboarding is `wifi/ota_url` plus the captive portal | hardware-verified (the validated end-to-end procedure) |
| Device controls reach the server as MCP tools | source-derived plus hardware-verified end to end |
| Tool discovery is a background race that may never complete | source-derived, and already recorded in `docs/xiaozhi-notes.md` |
| Wake words are a fixed compiled set, so they cannot be per-agent | source-derived; matches `docs/concepts.md` |

#### Touch-LCD-1.54 guide

| Claim | Provenance |
| --- | --- |
| Short press PWR toggles the conversation | hardware-verified (validated in hands-on use; also `ToggleChatState` in the board code) |
| Short press PWR while the assistant speaks stops the reply immediately, locally | hardware-verified |
| Long press PWR (about 2 s) powers off | hardware-verified for the behavior and the rough timing; the board sets no explicit threshold, so the figure is the observed one, not a source constant |
| Volume click steps by 10, hold up is maximum, hold down mutes the speaker | hardware-verified (the existing README table, validated as written) and source-derived |
| Double-click PWR turns the screen off and back on at half brightness | source-derived, **still unverified on hardware** |
| Triple-click PWR reboots into WiFi provisioning | source-derived, **still unverified on hardware** |
| Double-click volume-down toggles echo cancellation, idle only, closes the open channel, changes the listening mode | source-derived (`SetAecMode`, `GetDefaultListeningMode`, and the build config that enables device AEC in this board's prebuilt), **still unverified on hardware** |
| PWR gestures arm on the first release after boot | source-derived, **still unverified on hardware** |
| The board runs in realtime listening mode | source-derived (`CONFIG_USE_DEVICE_AEC=y` in the board's build config, and AEC on selects realtime) |
| Chinese wake word active in the 2.4.0 prebuilt | hardware-verified |
| English "Hi ESP" model exists only in source builds | source-derived |
| The wake word wakes the device, the default agent answers | decided semantics, recorded in `docs/concepts.md` |
| The word report is sent after the fact and only debug-logged | source-derived plus samtal-server code, already documented |
| The default build also sends the buffered trigger audio | source-derived, **still unverified on the wire**, and stated in the guide as an open question |
| Voice commands: status, volume, brightness, theme, WiFi re-provisioning | source-derived (`AddCommonTools` plus this board's `InitializeTools`, user-only tools excluded) |
| Firmware upgrade is kept out of the model-visible tool set | source-derived, already documented in `docs/xiaozhi-notes.md` |
| Interface language is Chinese and compile-time | hardware-verified |
| Speech and replies render as the conversation happens | hardware-verified (the validated end-to-end demo) |
| Touch does nothing on this board | source-derived (touch is registered with LVGL; no display code registers touch events), **still unverified on hardware** |
| Dim at 60 s, self power-off at 300 s | source-derived (`PowerSaveTimer(-1, 60, 300)`); the timings were in the README before this change, **the behavior is still unverified on hardware** |
| Neither timer runs while the channel is open or audio is playing | source-derived (`CanEnterSleepMode`) |
| Both timers depend on the NVS `sleep_mode` flag, default true | source-derived (`PowerSaveTimer::SetEnabled`) |
| The timers follow charging-state transitions | source-derived, **still unverified on hardware**, and the tested power state is therefore recorded as: none |
| The dim leaves wake-word detection running on this board | source-derived (the `-1` CPU frequency argument skips the audio-input shutdown) |
| What the idle screen shows | source-derived (`HandleStateChangedEvent`), **still unverified on hardware**, and the guide says so |

#### ePaper-1.54 stub

Nothing in this stub is hardware-verified; the stub says that in its
warning. Everything below is source-derived at the recorded commit
unless marked otherwise.

| Claim | Provenance |
| --- | --- |
| BOOT click toggles the conversation | source-derived |
| BOOT click while starting enters WiFi provisioning without a reboot | source-derived |
| PWR long press shows "OFF" and cuts audio, display and battery rails | source-derived |
| The long-press threshold is the shared helper's default | source-derived that the board sets none; the "about two seconds" figure is borrowed from hands-on use of the Touch-LCD board and the stub says the threshold is unmeasured here |
| No volume buttons, no touch layer | source-derived |
| Single microphone, no echo cancellation, auto listening mode, no barge-in | source-derived (the board's codec construction and build config) |
| Wake word status | **not verified at all**, and the stub says so instead of guessing |
| Voice commands: status, volume, WiFi re-provisioning (Chinese description) | source-derived |
| No brightness command | source-derived (the board returns no backlight) |
| A theme command is expected to exist | source-derived; see deviation 1 |
| 200x200 e-paper with partial refresh, no backlight | source-derived |
| No automatic dim or self power-off | source-derived; see deviation 2 |
| Two prebuilt variants, 4 MB and 8 MB | source-derived (the board's build configuration) |

#### AMOLED-2.16 stub

| Claim | Provenance |
| --- | --- |
| Side BOOT click toggles the conversation | hardware-verified |
| Touch does not start a conversation | hardware-verified, and source-derived (no touch event handlers anywhere in the display layer) |
| PWR is wired to the power-management chip; hold about 4 s is a hardware power-off invisible to the firmware | hardware-verified, and source-derived (the firmware writes the 4-second hold into the chip at startup) |
| BOOT click while starting enters WiFi provisioning | source-derived, **still unverified on hardware** |
| BOOT double-click toggles echo cancellation while idle | source-derived, **still unverified on hardware** |
| No volume buttons | source-derived |
| Realtime listening mode | source-derived (device AEC on in the board's build config) |
| Wake word is "Sophia" on the shipped firmware | hardware-verified |
| The factory image carries the Chinese model instead | hardware-verified |
| Upstream prebuilt's wake word | **not verified at all**, and the stub says so |
| Voice commands: status, volume, brightness, theme, WiFi re-provisioning | source-derived, **still unverified on hardware** |
| 480x480 panel, brightness is a panel command | source-derived |
| Dim at 60 s, self power-off at 300 s with the same conditions | source-derived, **still unverified on hardware** |
| The power-management init wedge, and the recovery | hardware-verified; the recovery by unplug and replug was verified on a setup with no battery attached, which the stub states |
| The NVS partition on this board is `0x6000` | hardware-verified |

### Verification

CI does not trigger on this branch (it filters on `samtal-server/**`,
`docs/reference/**`, and the workflow file), so these were run by hand.

- **Relative-link resolution** over every markdown file the branch adds
  or edits, resolving both file targets and heading anchors in the
  files they point at: 88 relative links across 10 files, all
  resolving. The only failure seen during the milestone was the plan's
  link to this file, before this file existed.
- **Em-dash grep** over `docs/devices/`: empty. Also empty over the
  whole branch diff.
- **Issue and PR reference grep** over `docs/devices/`: empty.
- **Identifier and secret scan** over the full branch diff: no MAC
  addresses, no UUIDs, no IP addresses, no credential-shaped strings.
  The only absolute URLs added are the vendor product and wiki pages
  already present in the hardware tables.
- **Hardware-table diff**: both tables list the same three boards in
  the same order, each with the same guide link and the same status in
  substance, and the Touch-LCD status in both now points at that
  board's guide. The differences that remain are the ones the two
  tables already had before this change: the firmware README names
  components and the root README describes them, and the root README
  marks planned boards with 🚧.
- **Consistency pass** against `docs/concepts.md` and
  `docs/xiaozhi-notes.md` for every mention of the wake word,
  listening, the idle timeout and MCP tools in the new files: agreeing
  everywhere except the wake-word audio question recorded under
  Discoveries, which the guide presents as unsettled rather than
  asserting against them. `docs/concepts.md` needed no edit: its Device
  section already names the per-board guides as the help agent's prose
  source.
- **`uv run ruff check .`** from `samtal-server/`: all checks passed.
- **`uv run pytest tests/unit -q`** from `samtal-server/`: 1130 passed,
  15 skipped.
- **`uv run pytest tests/integration -q`** from `samtal-server/`: 38
  passed. This branch changes no code, so both lanes are a sentinel and
  nothing more.

### Claims left unverified, for the PR's checklist

No hardware was available during this milestone, so every claim the
plan expected to be checked on the physical board is still unchecked.
Listed here so the PR can carry them as honest unchecked boxes rather
than quietly dropping them:

1. Touch-LCD: PWR double-click turns the screen off and back on.
2. Touch-LCD: PWR triple-click reboots into WiFi provisioning.
3. Touch-LCD: volume-down double-click toggles echo cancellation, is
   ignored unless idle, closes the open conversation, and takes
   barge-in away with it.
4. Touch-LCD: the PWR gestures arm only after the first release
   following boot.
5. Touch-LCD: what the idle screen actually shows.
6. Touch-LCD: that tapping the screen does nothing.
7. Touch-LCD: the dim at 60 s and the self power-off at 300 s, in each
   power state (externally powered, on battery, and across a
   transition), which is what the charging-state gating makes
   non-obvious. No power state was tested.
8. Touch-LCD: whether the prebuilt firmware sends the buffered
   wake-word audio to the server, which is the open question in that
   guide's wake-word section and the discrepancy with the repository's
   protocol documentation.
9. AMOLED: everything marked source-derived in its matrix row above,
   in particular the startup-time WiFi provisioning click and the
   echo-cancellation double-click.
10. ePaper: everything. No part of that board has been powered on for
    this project.
