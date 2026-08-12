# Device guides plan

## Goal

Implement issue #93: one user-facing markdown guide per supported
board under `docs/devices/`, linked from the hardware tables in the
root `README.md` and `samtal-esp32/README.md`, so that something
samtal-owned tells a user how to operate the device in front of
them: which button starts and stops a conversation, how long to
hold PWR to power off, whether a wake word is active and which word
it is, what the device itself answers by voice, and how the display
behaves. The guides are also the designated knowledge source for
the planned built-in help agent (issue #21), which is why they are
written as reviewable markdown rather than prompt text.

The companion implementation doc,
[`2026-08-12-device-guides-implementation.md`](2026-08-12-device-guides-implementation.md),
records what the milestone actually did, deviations from this plan,
and discoveries; a milestone with no deviations says so explicitly.

## The issue's decisions, restated for reference

Settled by issue #93; this plan does not re-litigate them.

1. **Three guides, with these exact filenames** under
   `docs/devices/`:
   `waveshare-esp32-s3-epaper-1.54.md`,
   `waveshare-esp32-s3-touch-lcd-1.54.md`,
   `waveshare-esp32-s3-touch-amoled-2.16.md`.
2. **Each guide covers**: controls (button semantics with actual
   timings, touch where present); the wake word (whether the
   shipped firmware has one enabled, which word, and that the wake
   word wakes the device, never a particular agent); the voice
   commands the device itself provides through its MCP tools; what
   the display shows in conversation and when idle; an onboarding
   pointer to the standard procedure (the NVS `ota_url` entry), not
   a duplicate of it; and the board's known quirks.
3. **Guides are user-facing and stand alone**: no issue or PR
   references inside them.
4. **Only the Touch-LCD-1.54 guide is written in full now**, from
   validated experience. The other two start as stubs marked 🚧 and
   grow as those boards reach working status, matching their
   entries in the hardware table.
5. **Both hardware tables link the guides**, and the two tables
   move together, per repository convention.

## Decisions this plan makes

### One milestone, one PR, through the full pipeline

AGENTS.md permits documentation-only changes straight to `main`,
and this plan does not use that permission: the guides are the
future help agent's knowledge source, so the review round is worth
having. One milestone covers everything, because the pieces cannot
land separately without breaking links: the tables' guide links
need the guides to exist, and the guides' shared-behavior links
need the common page. The plan, its review round, and the guides
all ride one PR on `feature/device-guides`.

CI does not run on this PR: it touches none of the workflow's
filtered paths (`samtal-server/**`, `docs/reference/**`, or the
workflow file itself). The same filter means the merge to `main`
runs no workflow and publishes no image. The verification section
defines what stands in for CI.

### A common page carries what every board shares

`docs/devices/README.md` indexes the three guides and holds the
behavior that is identical on every board running the upstream
firmware, so it is written once instead of three times:

- What the device listens to, and when, described honestly by
  case rather than as one universal story. While idle: no server
  connection at all, and on boards with a wake word enabled the
  microphone is monitored on-device for it; a board without one
  listens for nothing until a button opens the channel. In
  conversation, behavior follows the listening mode the firmware
  chose for the board: realtime (boards with echo cancellation)
  streams the microphone continuously, silence included, until
  something closes the channel, and the server's idle timeout is
  the closer you normally meet; auto (boards without echo
  cancellation) stops microphone input while the device is
  speaking and re-arms after each reply, so there is no barge-in;
  manual is push-to-talk. The idle-timeout and privacy notes are
  scoped to the modes they apply to, and each guide names the mode
  its board actually uses. No board has a microphone mute in
  hardware or firmware. The section the firmware README loses
  claimed all of this was universal; the move corrects that claim
  rather than carrying it.
- Networks: up to ten stored WiFi networks, the 2.4 GHz-only
  radio, the 5 GHz phone-hotspot trap, byte-for-byte SSID matching
  and the typographic-apostrophe trap.
- Onboarding: the pointer to the standard procedure (the NVS
  `ota_url` entry, described in
  [`../xiaozhi-notes.md`](../xiaozhi-notes.md)), shared because it
  is the same procedure on every board.
- The voice-command model: the device publishes its own controls
  as MCP tools over the conversation channel, so "set the volume
  to 40" works on every board; which controls exist varies by
  board and is listed in each guide.

Each per-board guide links to the common page early and covers
only what is specific to its board: controls, wake word, its own
voice-command list, display behavior, quirks. This is a judgment
call between self-containment and sync burden: three copies of the
networks section would drift the way the two hardware tables
already threaten to, and the issue's own onboarding bullet ("a
pointer, not a duplicate") establishes that a guide may point
rather than repeat. "Stand alone" in the issue forbids issue and
PR references, not links to sibling documentation. The future help
agent loads the common page plus the guide matching the device
model; both are ordinary committed markdown.

### The guides become canonical; the firmware README points to them

`samtal-esp32/README.md` currently holds a "Using the device"
section containing exactly the content the guides are for: the
generic listening and networks story, and the Touch-LCD-1.54
button table with its power-save timings. That content moves: the
generic sections into `docs/devices/README.md`, the board-specific
section into the Touch-LCD-1.54 guide. The firmware README keeps
its hardware table (with guide links) and its building
instructions, and gains a one-paragraph pointer to `docs/devices/`
where the section used to be, so existing deep links to the README
still land somewhere useful. Leaving the section in place would
create two canonical descriptions of the same board on day one.

The root `README.md` table's Status cell for the Touch-LCD-1.54
currently links to that README section; it is repointed as
described next.

### The tables link guides from the Links column

Both hardware tables gain the guide link in the existing Links
cell, as `guide · wiki`, samtal's own documentation ahead of the
vendor's. No new column: the tables are already five columns wide
and render tight. The Status cell for the Touch-LCD-1.54 keeps its
"working (upstream firmware)" text but its link moves to the
board's guide, since the section it pointed to has moved there.
Stub boards link their stub guides from day one; the stub itself
carries the 🚧 marking, matching the table's "planned 🚧" status.

### What the stubs contain

A stub is not an empty file: it is the guide's full section
skeleton (controls, wake word, voice commands, display,
onboarding, quirks) under a 🚧 warning saying the board has not
reached working status with samtal and the guide grows as it does.
Sections state what is known, with provenance, and say "not yet
verified on hardware" where that is the truth:

- **ePaper-1.54**: everything is read from the upstream board
  support code, none of it validated on hardware, and the stub
  says so. BOOT toggles the conversation (and enters WiFi
  provisioning when pressed during startup); PWR long-press powers
  off. Single microphone and no echo cancellation, which means the
  device cannot listen while it speaks, so there is no barge-in on
  this board. No backlight, so the screen-brightness voice command
  does not exist here. The wake word status of the prebuilt
  firmware for this board is unverified, and the stub says exactly
  that rather than guessing.
- **AMOLED-2.16**: hands-on facts from bringing the board up,
  verified on hardware even though the board is not yet at working
  status in the table. BOOT (the side button) toggles the
  conversation; the PWR button is wired to the power-management
  chip and is a hardware power-off on a hold of about 4 seconds,
  invisible to the firmware. The wake word in Waveshare's shipped
  firmware is "Sophia" (a compiled WakeNet model, `wn9_sophia_tts`);
  Waveshare's downloadable factory image carries the Chinese
  "nǐ hǎo xiǎo zhì" model instead, so reflashing it silently
  changes the wake word. Known quirk: the board can wedge during
  power-management init so that USB still enumerates but nothing
  responds; no software reset recovers it, a full power cycle
  (unplug, wait about ten seconds, replug) does.

Device identifiers (MAC addresses, client UUIDs) and deployment
specifics stay out of the guides; they are user-facing public
documentation.

### The Touch-LCD-1.54 guide, content inventory

Written from validated experience plus the upstream board support
code, organized under the standard skeleton:

- **Controls**: the existing button table moves here as-is (short
  press PWR toggles the conversation and is the deliberate way to
  stop streaming; long press of about 2 s powers off; idle 5
  minutes powers off; volume click, hold-for-max, hold-for-mute
  semantics, with mute being the speaker, never the microphone).
  The milestone verifies from the upstream board code what the
  touch layer actually does on this board and documents that,
  which may honestly be "nothing you need".
- **Wake word**: the upstream prebuilt firmware ships the Chinese
  wake word ("nǐ hǎo xiǎo zhì"); an English model ("Hi ESP") exists
  only in source builds, and the planned samtal build 🚧 will use
  it. The section states plainly that the wake word wakes the
  device and never a particular agent: it is spotted on-chip and
  the trigger audio never leaves the board; what the server may
  receive is an after-the-fact report naming the fired word, which
  it currently only debug-logs and does not retain. This matches
  [`../concepts.md`](../concepts.md) and
  [`../xiaozhi-notes.md`](../xiaozhi-notes.md), and the
  consistency pass holds the guides to it.
- **Voice commands**: what this board's firmware publishes as MCP
  tools, phrased as things to say: current status including volume
  and battery, setting the volume (0 to 100; the buttons step by
  10, the voice command sets an exact level), screen brightness (0
  to 100), and the light or dark screen theme. Worded to survive
  tool-name changes: names like `self.audio_speaker.set_volume`
  stay out of the user-facing text.
- **Display**: recognized speech and replies render as the
  conversation happens; what the idle screen shows; the dim after
  60 s idle and self power-off after 300 s, with the note that
  neither timer runs while a conversation channel is open and that
  the dim leaves wake-word detection running on this board. The
  idle-screen description is verified on hardware during the
  milestone, not asserted from memory.
- **Onboarding**: one sentence pointing at the common page's
  pointer to the standard procedure.
- **Known quirks**: the 5 GHz hotspot trap lives in the common
  page's networks section; this section carries whatever is truly
  board-specific and validated, and says so honestly if that list
  is currently empty.

### Housekeeping in the same PR

- `docs/README.md` gains a Device guides section pointing at
  `docs/devices/`.
- `CHANGELOG.md`: an entry under `## 2026-08-12`, `### Added`.
- `docs/concepts.md` is verified for consistency (its Device
  section already names the per-board guides as the help agent's
  prose source) and edited only if actual drift is found.
- The implementation doc section, written in the change that ticks
  the milestone below.

## Files touched

```
docs/devices/README.md                                  new
docs/devices/waveshare-esp32-s3-epaper-1.54.md          new, stub
docs/devices/waveshare-esp32-s3-touch-lcd-1.54.md       new, full
docs/devices/waveshare-esp32-s3-touch-amoled-2.16.md    new, stub
README.md                                               hardware table links
samtal-esp32/README.md                                  table links; section moves out
docs/README.md                                          index section
CHANGELOG.md
docs/plans/2026-08-12-device-guides.md
docs/plans/2026-08-12-device-guides-implementation.md
```

## Verification

CI does not trigger, so the checks are run and reported by hand,
honestly:

- Every claim in the Touch-LCD-1.54 guide is either validated
  experience already recorded in the repository (the moved README
  section), verified against the upstream board support code in
  `vendor/xiaozhi-esp32` (button wiring, power-save timings, MCP
  tool set), or verified on the physical board during the
  milestone (the idle-screen description, touch behavior). The
  implementation doc records which claims got which treatment.
- The two hardware tables are diffed against each other for the
  agreed shared content, and every link in the new and edited
  files is resolved (a scripted relative-link check over the
  touched files).
- A consistency pass against `docs/concepts.md` and
  `docs/xiaozhi-notes.md` for every mention of wake word,
  listening, idle timeout, and MCP tools in the new files,
  confirming no sentence contradicts them.
- `grep` for em-dashes and for issue or PR references over
  `docs/devices/`, both must come back empty.
- `uv run ruff check .` and both test lanes from `samtal-server/`
  still pass, as a no-code-changed sentinel rather than as
  meaningful coverage.

## Risks and mitigations

- **A second description of firmware behavior drifts from the
  first.** The guides overlap `docs/xiaozhi-notes.md` (onboarding,
  wake word) and the READMEs. Mitigation: the guides point at the
  notes for procedures instead of duplicating them, the README
  section moves rather than being copied, and the consistency pass
  is part of the milestone's acceptance.
- **Wake-word claims are firmware-version facts, stated as board
  facts.** Upstream prebuilts and Waveshare factory images differ
  today and can change under us. Mitigation: each wake-word
  section names the firmware it describes (upstream prebuilt,
  Waveshare shipped, samtal build 🚧) rather than claiming a
  board-eternal truth.
- **Stubs read as more verified than they are.** Mitigation: the
  stub template separates read-from-source claims from
  hardware-verified ones in so many words, and the 🚧 warning
  states the board's actual status.
- **Voice-command wording rots when upstream renames tools.**
  Mitigation: guides describe what to say and what happens, never
  tool identifiers; the tool inventory lives in code and in the
  notes, not in user-facing prose.

## Plan review round

One external review of the plan as first committed (49c3714): codex
CLI 0.147.0, model gpt-5.6-sol, read-only against this repository
with the vendor clones present and the body of issue #93 supplied,
2026-08-12. Verdict: ready after the P1/P2 amendments. Findings as
received, condensed; each carries its resolution once the amendment
addressing it lands.

1. **P1: the wake-word wording contradicts the newly reconciled
   protocol documentation.** The plan says the server only learns
   that a session opened, while `docs/concepts.md`,
   `docs/xiaozhi-notes.md`, and the firmware
   (`application.cc`) agree the firmware may send a `listen`
   `detect` report naming the fired word, which the server
   debug-logs and does not retain. Say that detection and trigger
   audio stay on-device, and the server may receive an
   after-the-fact word report it currently only debug-logs.
   *Resolution*: adopted. The Touch-LCD wake-word inventory now
   separates the unreachable trigger audio from the reported word
   (received, debug-logged, not retained), cites both reconciled
   documents, and the consistency pass holds the guides to that
   wording. The issue's parenthetical ("the server only learns
   that a session opened") predates the reconciliation; its
   substance, that the wake word wakes the device and never an
   agent, is unchanged.

2. **P1: the proposed common listening description is not common
   to all three boards.** The plan makes idle wake-word monitoring
   and continuous open-channel streaming universal, while itself
   admitting the ePaper board has no AEC and an unknown wake-word
   status. The firmware selects realtime mode only when AEC is on,
   auto mode otherwise; the glossary documents the distinction.
   Make wake-word monitoring conditional on a wake word being
   enabled, describe streaming by listening mode (realtime streams
   continuously; auto stops microphone input during playback and
   re-arms per turn; manual is push-to-talk), and do not present
   the idle-timeout and privacy story as identical for all modes.
   *Resolution*: adopted. The common-page listening bullet is
   rewritten by case: idle behavior conditional on a wake word
   being enabled, in-conversation behavior described per listening
   mode (realtime streams continuously; auto stops microphone
   input during playback and re-arms per turn, with no barge-in;
   manual is push-to-talk), the idle-timeout and privacy notes
   scoped to the modes they apply to, and each guide naming its
   board's actual mode. The moved README section's universality
   claim is corrected in the move rather than carried.

3. **P2: the Touch-LCD controls inventory omits implemented
   button actions.** The board code gives PWR double-click a
   screen off/on action, PWR triple-click WiFi provisioning, and
   volume-down double-click an AEC toggle while idle, which also
   changes the listening mode and barge-in behavior. Require all
   implemented gestures documented and hardware-checked, with
   state restrictions and side effects; and state the ePaper PWR
   long-press threshold instead of a bare "long-press", since the
   issue requires actual timings.

4. **P2: the MCP inventories omit board-specific commands and
   overpromise availability.** All three boards also publish a
   WiFi-reconfiguration tool from their `InitializeTools`; the
   notes say background discovery can lose the first-utterance
   race or never complete. Derive each board's model-visible
   inventory from `AddCommonTools` plus the board's
   `InitializeTools`, exclude user-only tools, include WiFi
   reconfiguration, and qualify voice commands as available only
   once MCP discovery completes.

5. **P2: verification covers only the full guide and leaves both
   stubs' claims unreproducible.** The stubs make detailed
   assertions, `vendor/` is an uncommitted checkout, and "upstream
   prebuilt" and "Waveshare shipped" are not versions. Require a
   claim-by-claim provenance matrix for all three guides, record
   the exact upstream commit, and record the tested firmware
   version or factory-image filename for wake-word claims; the
   implementation doc distinguishes source-derived,
   hardware-verified, and still-unverified facts per board.

6. **P2: the per-board onboarding sections use a pointer to a
   pointer.** The issue requires each guide to contain an
   onboarding pointer to the standard NVS `ota_url` procedure; the
   plan routes the reader through the common page, which then
   points to the notes. Each guide should link the canonical
   procedure directly and name `wifi/ota_url` in one sentence.

7. **P2: the navigation work omits two existing entry points that
   become misleading.** The root README's Getting Started ends
   with the board-ambiguous "press the button" (BOOT on ePaper,
   PWR on Touch-LCD), and the docs index's introduction says
   user-facing documentation lives in the READMEs, which becomes
   false once the guides exist. Update Getting Started to send
   readers to their board guide for the final control, and update
   the docs-index introduction to name `docs/devices/`.

8. **P2: automatic dim and shutdown behavior is stated without
   its runtime conditions.** The power-save timer is disabled when
   the NVS `sleep_mode` flag is false, and the Touch-LCD board
   also gates it on charging state transitions. State that the
   timers apply only while power saving is enabled for the current
   power state, and verify battery and externally powered behavior
   rather than only the constructor arguments.

9. **P2: the AMOLED recovery procedure is not a full power cycle
   when a battery is attached.** The board has battery-aware PMIC
   support, so USB removal need not remove power. Say remove all
   power including any battery, or use the PMIC's four-second
   hardware power-off if it still works; limit "unplug and replug"
   to a verified no-battery setup.

10. **P2: the no-leak rule does not cover verification
    artifacts.** The plan excludes identifiers only from the
    guides while requiring hardware evidence in the implementation
    doc, and the NVS procedure can expose WiFi credentials, UUIDs,
    persisted WebSocket data, and deployment endpoints. Prohibit
    raw NVS dumps, boot logs, OTA responses, credentials, tokens,
    real URLs and IPs, MACs, and UUIDs from every committed file
    and the PR description, and add a full-diff identifier scan to
    verification.

## Milestones

- [ ] **Write the device guides and link them from the hardware
  tables**: the common page and three guides under `docs/devices/`
  with the section skeleton above (one full, two stubs); the
  "Using the device" content moved out of `samtal-esp32/README.md`
  with a pointer left behind; both hardware tables linking the
  guides as `guide · wiki` with the Touch-LCD status link
  repointed; `docs/README.md` index section; CHANGELOG entry; the
  implementation doc section. Accept: the verification list above
  passes, with the hardware-verified claims called out in the
  implementation doc.
