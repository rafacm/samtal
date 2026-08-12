# Reconcile PR #94's device protocol claims plan

## Goal

Implement issue #109: bring the three documents that describe how
device facts reach the server back into agreement with the code and
with each other. PR #94's concepts page compressed the arrival
phases into one ("everything arrives at hello") and flattened the
retention story into "all dropped"; the canonical protocol notes
still deny the wake-word report that `device/session.py` receives
and logs. The protocol notes are required reading for protocol
work, so the divergence is the wrong starting point for whoever
implements #96.

This is a documentation-only change: no server code moves, and the
truth being documented is already established in the issue and
verified against the code (references below). The work is careful
wording, not investigation.

The companion implementation doc,
[`2026-08-12-reconcile-device-protocol-claims-implementation.md`](2026-08-12-reconcile-device-protocol-claims-implementation.md),
records what the milestone actually did, deviations from this plan,
and discoveries; a milestone with no deviations says so explicitly.

## The issue's findings, restated for reference

Settled by issue #109 and verified against the code; this plan does
not re-litigate them.

1. **`hello` does not carry the MCP tool list.** The hello carries
   the protocol version, the audio parameters, and the feature map
   (`session.py`, `_receive_hello`). When the feature map advertises
   MCP, the server starts a separate asynchronous MCP handshake in a
   background task (`_start_device_discovery`) and fetches the tool
   list afterward; a first utterance can beat discovery.
   `docs/xiaozhi-notes.md` already describes this race accurately,
   and stays the canonical description of it.
2. **Not all observed facts are dropped.** Board model and firmware
   version are retained in the bounded in-memory `DeviceFacts`
   cache (`capture.py`), written at OTA check-in (`ota.py`) and
   read by the following session's capture manifest. The durable,
   enriched per-device record is #96 and remains unimplemented, but
   "all parsed and dropped" is not the current baseline.
3. **The fired wake word does reach the server, as a report.** The
   trigger audio never leaves the chip (ESP-SR decides on-device),
   but the firmware reports the fired word in a `listen` `detect`
   message, which `device/session.py` receives and debug-logs.
   `docs/xiaozhi-notes.md` still says the server learns only that a
   session opened.

## Decisions this plan makes

### One milestone, one PR, through the full pipeline

AGENTS.md permits documentation-only changes straight to `main`,
and this plan deliberately does not use that permission. The issue
exists because a merged PR's claims did not survive a post-merge
review; the review round is the point, so the change goes through
the standard branch, PR, and external review pipeline. One
milestone covers everything: the edits are three small regions of
prose that must land together to be consistent, and splitting them
would create exactly the interim divergence the issue exists to
remove. The plan, its review round, and the doc edits all ride the
one PR on `feature/reconcile-device-protocol-claims`.

CI does not run on this PR: the workflow triggers only on
`samtal-server/**` and on the workflow file itself. The
verification section defines what stands in for it.

### The issue #96 edit happens after the merge, with wording fixed here

Refreshing issue #96's Problem section is a GitHub write, and
subagents run no GitHub commands, so the orchestrating session
applies it. It happens after the docs PR merges, not before: the
issue text should describe what `main` says, and editing the issue
first would leave it citing docs that do not exist yet if the PR
round changes anything. The replacement text is committed in this
plan (below), so it passes through the same review as the docs; the
orchestrator applies it verbatim.

### The glossary is verified, not assumed to need edits

PR #94 already corrected the glossary's wake-word entry ("at most
told which word fired, after the fact"), and its Manifest entry
already reflects what `DeviceFacts` feeds the capture manifest. The
milestone verifies the glossary against the final wording of the
other two documents and edits it only where actual drift is found,
rather than rewording entries that are already right. The same
applies to the concepts page's "wake word wakes the device" section,
whose citation of the protocol notes becomes accurate the moment the
notes are fixed.

## The edits

### `docs/concepts.md`: the Observed facts bullet, by phase

The "Observed facts" bullet under Device currently reads as one
arrival ("protocol version, a feature map, and the device's own MCP
tool list arrive at hello") followed by "Today these are parsed and
dropped". It is rewritten to name the phases in wire order, each
with what it carries:

- **OTA check-in**: board model and firmware version.
- **hello**: protocol version and the feature map.
- **after hello**: when the feature map advertises MCP, the server
  asks the device for its tool list over a separate background MCP
  handshake; a first utterance can beat that discovery (the race
  the protocol notes describe).
- **first listen**: the listening mode, the empirical
  echo-cancellation signal, since the firmware chooses realtime
  exactly when AEC is on (unchanged claim).
- **listen detect**: a fired wake word, reported by word.

The retention sentence distinguishes what actually survives
instead of flattening it: board model and firmware version cross
the OTA-to-session boundary in the bounded in-memory cache;
protocol version, the discovered MCP tools, and the listening mode
are retained and consumed for the life of the session, with the
protocol version also entering an enabled capture's manifest; the
wake-word report alone is merely debug-logged. None of it enters a
durable, queryable per-device record; the record that would keep
all of it is planned (issue #96). The page's date header moves to
2026-08-12.

### `docs/xiaozhi-notes.md`: the wake-word entry separates audio from report

The entry under "What running stock firmware costs the server"
currently reads: "The wake word is spotted on the chip and never
reaches the server. ESP-SR decides; the server learns only that a
session opened." The correction keeps the constraint (detection is
on-device and its audio is unreachable) and adds the report the
code actually receives, in substance:

- The wake word is spotted on the chip, and the trigger audio never
  reaches the server: ESP-SR decides on-device, and no server work
  changes that. The planned English wake word (`wn9_hiesp`) is a
  custom build and nothing else (unchanged claim).
- What the server does get is an after-the-fact report: the
  firmware sends `listen` `detect` with the fired word in `text`,
  which samtal-server currently debug-logs (`device/session.py`).
  The server can know which word opened the session; it cannot
  hear, tune, or substitute for the detection.

The entry stays a constraint entry: what owning the firmware would
change is unaffected.

### Issue #96's Problem section, replacement text

Applied verbatim by the orchestrator after the merge (paragraphs
unwrapped because GitHub renders issue bodies with the `breaks`
extension). Only the Problem section changes; Proposal and
Sequencing stand.

```markdown
## Problem

The server already sees most of what there is to know about a device, and keeps almost none of it. The OTA request reports identity and build facts (`Device-Id` = MAC, `Client-Id` = UUID, `application.version`, `board.type`), parsed in `ota.py`. Board model and firmware version already survive that request: they are kept in a bounded in-memory cache (`DeviceFacts` in `capture.py`), keyed by MAC, until the session that follows reads them into its capture manifest. That cache is the only survival there is: in-memory (gone on restart), bounded, and read only by capture; nothing else can ask what a device last reported.

The `hello` message carries the protocol version and a features map; when the features advertise MCP, the device's tool list arrives shortly after over a separate MCP handshake and says which controls (volume, brightness) this board actually has. The listening mode arrives with the first `listen` message and is the empirical echo-cancellation signal, since the firmware picks realtime exactly when AEC is on. A fired wake word is reported as `listen` `detect` with the word in `text`, currently debug-logged in `device/session.py`. All of these survive at best for the life of the session that carried them: the protocol version, the discovered tool list, and the listening mode are held and consumed while the session runs (the protocol version also enters an enabled capture's manifest), and the wake-word report is only debug-logged. None of it enters a durable, queryable per-device record, and the OTA exchange and the WebSocket session are joined only by `Device-Id`.
```

### Housekeeping in the same PR

- `CHANGELOG.md`: an entry under `## 2026-08-12`, `### Fixed`,
  naming the three corrected claims in one line each.
- The implementation doc section, written in the change that ticks
  the milestone below.

## Files touched

```
docs/concepts.md                 the Observed facts bullet; date header
docs/xiaozhi-notes.md            the wake-word constraint entry
docs/glossary.md                 only if the consistency pass finds drift
docs/plans/2026-08-12-reconcile-device-protocol-claims.md
docs/plans/2026-08-12-reconcile-device-protocol-claims-implementation.md
CHANGELOG.md
```

## Verification

CI does not trigger, so the checks are run and reported by hand,
honestly:

- A consistency pass over the three documents reading every
  mention of hello, MCP discovery, wake word, listening mode, and
  device facts (`grep -in` for `hello`, `mcp`, `wake`, `detect`,
  `listen`, `facts` across the three files), confirming no sentence
  contradicts another document or the code.
- The code references spot-checked against the claims:
  `session.py` `_receive_hello` and `_start_device_discovery`,
  `capture.py` `DeviceFacts`, `ota.py`'s `record` call, and the
  `listen` `detect` match arm.
- `uv run ruff check .` and both test lanes from `samtal-server/`
  still pass, as a no-code-changed sentinel rather than as
  meaningful coverage.

## Risks and mitigations

- **The rewrite reintroduces divergence in new words.** The three
  documents describe the same mechanism at different depths, and
  paraphrase is how the last divergence got in. Mitigation: the
  consistency pass above is part of the milestone's acceptance, and
  the concepts page defers to the protocol notes for the race
  rather than describing it a second time in full.
- **Overcorrecting the wake-word story.** The report is a debug-log
  today, not a feature; wording that promises the server "knows"
  the wake word would overstate retention the same way "all
  dropped" understated it. Mitigation: the wording above says
  reported and debug-logged, and the retention tiers in the
  concepts page keep the fact in the dropped tier.
- **Issue #96 drifts again once #96 is implemented.** Its Problem
  section describes a baseline that its own implementation will
  change. Accepted: that is what implementing an issue does to its
  Problem section, and no mitigation is needed.

## Plan review round

One external review of the plan as first committed (3790677): codex
CLI 0.147.0, model gpt-5.6-sol, read-only against this repository
with the bodies of issues #109 and #96 supplied, 2026-08-12.
Verdict: ready after the P1/P2 amendments. Findings as received,
condensed; each carries its resolution once the amendment
addressing it lands.

1. **P1: "parsed and dropped" still misstates facts retained or
   used by the session.** The proposed concepts wording and issue
   replacement classify everything except board and firmware as
   dropped. In fact the protocol version is retained on the
   session, logged, and written to enabled capture manifests
   (`session.py`); discovered MCP tools remain callable during the
   session; the listening mode is retained and controls behavior.
   Only the wake-word text is merely debug-logged. Say instead:
   none of the WebSocket observations enters a durable, queryable
   per-device record; protocol version, MCP capability and tools,
   and listening mode are retained or consumed during the live
   session; board and firmware additionally cross the
   OTA-to-session boundary through `DeviceFacts`.
   *Resolution*: adopted. The concepts retention wording and the
   issue #96 replacement now say session-scoped retention out
   loud: protocol version, discovered MCP tools, and listening
   mode are held and consumed for the life of the session, the
   protocol version also enters an enabled capture's manifest,
   the wake-word report alone is merely debug-logged, and what
   nothing gets is a durable, queryable per-device record.
2. **P2: the proposed cache lifetime and manifest read are false.**
   The replacement says facts remain "until the session that
   follows reads them into its capture manifest", but
   `DeviceFacts.get` does not remove the entry, so later sessions
   can read it until overwrite, eviction, or restart; and a session
   does not read it at all when capture is disabled, which is the
   default. Say instead: the cache retains the latest board and
   firmware per MAC until overwritten, evicted by its 256-entry
   bound, or lost at restart; when capture is enabled, a session
   copies the available facts into its manifest.
3. **P2: MCP discovery is not guaranteed to deliver a list
   "shortly after".** Discovery is deliberately backgrounded
   because the board may never answer, and the protocol notes
   promise only a race. Say instead: the server starts a separate
   background handshake and requests the list; if discovery
   completes, the tools become available, possibly after the first
   utterance.
4. **P2: "joined only by `Device-Id`" is too broad.** OTA
   authentication signs both client ID and MAC, and WebSocket
   authentication verifies both. Scope the claim to the facts
   correlation: the cached board and firmware reach the following
   session only through the normalized `Device-Id`; do not
   generalize to the whole OTA-to-WebSocket relationship.
5. **P2: the glossary contains relevant drift, so its edit cannot
   remain optional.** The Manifest entry vaguely lists "device,
   firmware" and omits both the cached board model and the
   per-session protocol version, although both appear in the
   manifest (`session.py`). Require a Manifest-entry edit naming
   device identity, cached OTA board and firmware when available,
   and the session protocol version, with the glossary date bumped
   in the same edit; the other named entries stay
   verification-only.
6. **P2: the wake-word wording contradicts the plan's own
   retention safeguard.** The proposed protocol note says "the
   server can know which word opened the session" while the risk
   section warns against wording that promises the server "knows"
   the word; the code binds the value only long enough to
   debug-log it. Say instead: the server receives which word fired
   and currently debug-logs it; it does not retain the report.
7. **P3: the CI path-filter inventory is incomplete.** The
   workflow also triggers on `docs/reference/**`, not only
   `samtal-server/**` and the workflow file. The conclusion holds
   for the planned file set; state the full filter, and note the
   same filter means this documentation-only merge to `main`
   publishes no image.

## Milestones

- [ ] **Reconcile the device-fact documentation** (PR TBD): the
  concepts page's Observed facts bullet rewritten by phase with the
  two retention tiers; the protocol notes' wake-word entry
  separating unreachable trigger audio from the reported word; the
  glossary and the concepts wake-word section verified for
  consistency and edited only on found drift; CHANGELOG entry; the
  implementation doc section. Accept: the consistency pass finds no
  contradiction among the three documents and the code references;
  lint and both test lanes still green as a sentinel. After the
  merge, the orchestrator applies the issue #96 Problem replacement
  verbatim and closes #109.
