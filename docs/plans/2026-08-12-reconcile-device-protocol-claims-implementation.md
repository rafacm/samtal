# Reconcile PR #94's device protocol claims implementation

Companion to
[`2026-08-12-reconcile-device-protocol-claims.md`](2026-08-12-reconcile-device-protocol-claims.md).
One section per milestone, recording what was actually written, the
deviations from the plan, what the consistency pass found, and the
verification results as they came out.

## Milestone 1: reconcile the device-fact documentation

The three documents that describe how device facts reach the server
now agree with each other and with the code. No server code moved:
this is a documentation-only change, and the code was read only to
check the claims against it.

### What landed

**`docs/concepts.md`, the Observed facts bullet.** Rewritten to name
the arrival phases in wire order rather than collapsing them into one
arrival at hello: the OTA check-in carries the board model and the
firmware version; the hello carries the protocol version and a feature
map; after the hello, when the feature map advertises MCP, the server
asks the device for its own tool list over a separate background MCP
handshake, which a first utterance can beat; the first listen message
carries the listening mode, still the empirical echo-cancellation
signal; a `listen` `detect` message reports a fired wake word, by
word. The race gets one clause and a link to the protocol notes rather
than a second full description, which is the risk section's own
mitigation against paraphrase drift.

The retention sentence replaces "today these are parsed and dropped"
with the three tiers that actually exist. Board model and firmware
version cross the OTA-to-session boundary in the bounded in-memory
cache, which holds the latest report per device until it is
overwritten by the next check-in, evicted by the cache's bound, or
lost when the server restarts, and which a session reads into its
manifest when capture is enabled. The protocol version, the discovered
MCP tools and the listening mode are retained and consumed for the
life of the session, the protocol version also entering an enabled
capture's manifest. The wake-word report alone is merely debug-logged.
None of it enters a durable, queryable per-device record, which stays
planned as issue #96. The page's date header moved to 2026-08-12.

The bullet stayed one flat bullet with prose phases rather than
becoming a nested list, because no document under `docs/` uses nested
lists and this was not the place to introduce them.

**`docs/xiaozhi-notes.md`, the wake-word entry.** Split into the
constraint and the report. The constraint is unchanged in substance:
the wake word is spotted on the chip, its audio never reaches the
server, ESP-SR decides on-device, and the planned English wake word
(`wn9_hiesp`) is a custom build and nothing else. The addition is what
the code receives: the firmware sends `listen` `detect` with the fired
word in `text`, which samtal-server currently debug-logs
(`device/session.py`) and does not retain. The detection itself stays
something the server cannot hear, tune, or substitute for. The entry
is still a constraint entry and what owning the firmware would change
is untouched.

**`docs/glossary.md`, the Manifest entry.** "device, firmware" became
what `_manifest` in `device/session.py` actually writes: the device
identity (MAC and client ID), the board model and firmware version
cached from the last OTA check-in when they are available, and the
session's protocol version, alongside the verbatim provider entries
and the completeness flag the entry already named. The glossary's date
header moved in the same edit.

**`CHANGELOG.md`.** Four entries under the existing `## 2026-08-12`,
`### Fixed`: the hello that was said to carry the MCP tool list, the
retention that was flattened to "parsed and dropped", the protocol
notes' denial of the wake-word report, and the vague Manifest entry.

### Deviations from the plan

No deviations. The concepts bullet, the protocol-notes entry, the
glossary Manifest entry, the date bumps and the CHANGELOG entries are
what the plan's "The edits" and "Housekeeping" sections specify, with
the review round's amended wording (session-scoped retention named out
loud, the cache's real lifetime, discovery as a race whose completion
is not guaranteed, the wake-word report described as received,
debug-logged and not retained). Issue #96's Problem replacement is not
applied here: the plan assigns it to the orchestrator after the merge,
and subagents run no GitHub commands.

### What the consistency pass found

The pass is one command, run from the repository root:

```sh
grep -in 'hello\|mcp\|wake\|detect\|listen\|facts' \
  docs/concepts.md docs/xiaozhi-notes.md docs/glossary.md
```

Every hit it prints is read. The first run of it, before the PR review
round, concluded that no contradiction remained and that no further
edit was needed.

That conclusion was wrong, and the PR review found what it missed: the
protocol notes' pre-existing discovery-race entry promised that a first
utterance beating discovery "runs without device tools and the next one
has them", which guarantees a delivery the plan's own resolution says
is not guaranteed, since discovery is backgrounded precisely because a
board may never answer. The entry now says that a first utterance can
run without the tools and that later utterances have them if discovery
completes. See the PR review round below for the fix and its commit.

That first run also reported hit counts (30, 15 and 21) that were
eyeballed off the printed output rather than counted, and were simply
wrong. The pass was rerun after the two fixes, on the same command,
with the counts taken from the same expression under `grep -ic`: 33
matching lines in `docs/concepts.md`, 24 in `docs/xiaozhi-notes.md`,
25 in `docs/glossary.md`, 82 in total, which reproduces what the
reviewer counted. They are matching lines rather than matches, which
`grep -o` counts higher (41, 27 and 27). Every one of them was read
again, and this time nothing further contradicted another document or
the code. The two wake-word statements that survive elsewhere are
receipt claims rather than retention claims and stay correct: the
concepts page's "at most is told which word fired, after the fact" and
the glossary's Wake word entry, which says the same thing in the same
terms.

The entries that were verified and deliberately left alone:

- The concepts page's "The wake word wakes the device, not an agent"
  section already said the server "at most is told which word fired,
  after the fact", citing the protocol notes. That citation was the
  drift the notes edit removed; the section itself is now accurate as
  written.
- The glossary's Wake word entry says the same thing in the same
  terms, and its Listening modes entry ("the firmware picks realtime
  whenever AEC is on") matches the concepts bullet's AEC claim.
- The glossary's MCP entry ("the device publishes its own controls to
  the server over it") agrees with the concepts bullet's deferral,
  which is why the concepts page does not restate the race.
- The glossary's OTA endpoint entry describes the reply (WebSocket URL
  and auth token) and says nothing about what the request reports, so
  it neither agrees nor disagrees with the new phase list. Adding the
  reported facts there would duplicate the concepts bullet, so it was
  left as it is.

One discovery, out of scope and left for whoever implements #96: the
`DeviceFacts` docstring in `capture.py` says the facts are "kept until
the session it is about to open asks for it", which reads as consume
on read, while `DeviceFacts.get` copies the entry and leaves it in
place. The documents now describe the real lifetime (overwrite,
eviction, restart); the code comment is the one place that still
implies otherwise, and correcting it would have made this
documentation-only change touch server code.

### Verification

- Consistency pass: run, missed one contradiction the PR review found,
  and rerun after the fixes with every hit read again and nothing
  further found. Command, counts and outcome are recorded above.
- Code references spot-checked against the claims, all confirming the
  documented wording: `_receive_hello` (protocol version, transport,
  audio params, feature map; no tool list),
  `_start_device_discovery` (returns unless `features["mcp"] is True`,
  then creates a background task for `DeviceToolClient.discover`), the
  `listen` `start` arm (stores `_listen_mode`, logs the mode at info),
  the `listen` `detect` arm (a single `logger.debug` of the reported
  word, bound nowhere), `_manifest` (MAC, client ID, the spread of
  `DeviceFacts.get`, the protocol version), `DeviceFacts` in
  `capture.py` (`OrderedDict` bounded at 256, `record` overwrites and
  moves to end, `get` copies without removing), and `ota.py`'s
  `device_facts.record(mac, version, board)` call.
- Sentinel runs from `samtal-server/`, on a branch that changes no
  code, all green: `uv run ruff check .` reported "All checks
  passed!"; `uv run pytest tests/unit -q` reported 1130 passed, 15
  skipped in 132.34s; `uv run pytest tests/integration -q` reported 38
  passed in 104.07s.
- CI did not run and will not: the workflow triggers only on
  `samtal-server/**`, `docs/reference/**` and the workflow file, and
  this change touches none of them. The runs above are the substitute,
  and they are a sentinel rather than coverage of anything this change
  did.

### PR #110 review round

One external review of the pull request's diff (`origin/main...2b16556`):
codex CLI, model gpt-5.6-sol, read-only, 2026-08-12. Three findings,
two P2 and one P3, each fixed with its own commit.

1. **P2: the new wake-word sentence promised knowledge of the fired
   word.** The entry ended "So the word that fired is knowable", which
   is exactly the wording the plan's own review round rejected: the
   code binds the value only long enough to debug-log it, and the
   plan's risk section warns that promising the server "knows" the
   word overstates retention the same way "all dropped" understated
   it. Fixed in e7a9326: the entry stops at received, debug-logged,
   and not retained, keeps the sentence saying the detection is not
   something the server can hear, tune, or substitute for, and the
   implementation doc's paraphrase of it moved in the same commit.
2. **P2: the discovery-race entry guaranteed a delivery that is not
   guaranteed.** The pre-existing bullet said a first utterance that
   beats discovery "runs without device tools and the next one has
   them". `_start_device_discovery` backgrounds the handshake
   precisely because a board may never answer, and the plan's third
   review resolution says discovery may never complete. This is
   pre-existing text the consistency pass read and cleared, which is
   what makes it a finding against this PR rather than against #94.
   Fixed in c09a516: the first utterance may run without the tools,
   later utterances have them if discovery completes, and the entry
   keeps its constraint framing (a device that declared its tools in
   `hello` would remove the race). The consistency-pass section now
   records the miss.
3. **P3: the recorded consistency-pass hit counts did not
   reproduce.** The doc reported 30, 15 and 21 hits; the reviewer's
   own run counted 33, 24 and 25. The counts had been eyeballed off
   the printed output instead of counted, so they were wrong, and a
   verification record nobody can reproduce is worth nothing. Fixed in
   the commit carrying this section: the pass is recorded as the exact
   command it is, its counts come from `grep -ic` over the same
   expression and are named as matching lines rather than matches, and
   the pass was rerun over the two fixed documents with every hit read
   again, reproducing 33, 24 and 25 and finding nothing further.
