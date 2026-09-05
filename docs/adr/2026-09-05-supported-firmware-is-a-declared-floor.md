# Supported firmware is a declared floor

**Status:** Accepted (recorded 2026-09-05, deciding part 2 of
[#399](https://github.com/rafacm/vinga/issues/399)).

## Context

vinga speaks a wire contract it does not own. There is no published
specification of the xiaozhi protocol: every maintained protocol fact
in [`docs/xiaozhi-notes.md`](../xiaozhi-notes.md) was read out of an
upstream source at a pinned commit, and the page's
[currency section](../xiaozhi-notes.md#upstream-currency) records which
commit each half was read at. The
[stock-firmware promise](../architecture/product-promises.md#stock-xiaozhi-firmware-is-the-compatibility-floor)
already says what those readings are for: its version target is the
firmware actually running on boards in the field, not upstream's HEAD.

What has actually been observed on hardware is two images, both on
2026-08-12/13: **2.2.4**, the Waveshare factory image on the
AMOLED-2.16, and **2.4.0**, upstream's prebuilt image on the
Touch-LCD-1.54. Those two are what the maintained sections were read
against, and the second is the one the promise is read against today.

What was missing is the rule. "Support different firmware versions"
without a named set is an open-ended fear rather than a commitment:
nothing says which versions are in, so nothing can say whether a given
upstream change matters. The drift watch landed alongside this record
(the manifest [`upstream-watch.yaml`](../upstream-watch.yaml),
`scripts/upstream_watch.py`, and
[the weekly workflow](../../.github/workflows/upstream-drift.yml)) now
reports upstream movement under the watched paths, which sharpens the
gap rather than closing it: a report is a list of files and commit
subjects, and reading it takes a question to ask of each one.

## Decision

**vinga promises to speak the firmware releases observed on boards in
the field, and this record enumerates them.** Today that set is
exactly two:

| Firmware | Image | Observed |
| --- | --- | --- |
| 2.2.4 | Waveshare factory image, [ESP32-S3-Touch-AMOLED-2.16](../devices/waveshare-esp32-s3-touch-amoled-2.16.md) | 2026-08-12/13 |
| 2.4.0 | Upstream prebuilt image, [ESP32-S3-Touch-LCD-1.54](../devices/waveshare-esp32-s3-touch-lcd-1.54.md) | 2026-08-12/13 |

That set, and nothing wider, is what "the compatibility floor" names.

**Every drift-report triage asks one question of the report: does this
change move anything inside the floor?** An upstream commit that
rewrites a watched file but reaches no image in the table above is
news, not work; the notes get re-read when a board arrives running the
version that carries it. This is the question the watch exists to be
answered with, and it is why the report deliberately carries file
names and commit subjects rather than patch text.

**The floor widens when a new firmware version is observed on a board
and the notes are re-read against it.** Both halves are required: an
observation nobody has re-read the maintained sections against is a
device fact, not a promise. Widening is recorded as a dated addendum
to this record naming the version, the board it was observed on and
what the re-read moved, the way the
[database floor](2026-08-20-database-upgrades-have-a-compatibility-floor.md)
records its own exits, because records here are immutable and the
enumeration grows by addendum rather than by a rewritten list.

**The floor narrows only by a recorded decision superseding this one.**
Dropping a version vinga has promised to speak is a product decision
with a written reason, never a side effect of a refactor that found the
old shape inconvenient.

## Consequences

What it buys: bounded, testable support in place of an open-ended
fear. Two versions is a set a person can hold in their head, a
question a triage can answer in a minute, and a claim someone outside
the project can falsify with a board. It also gives the drift watch
its terminating condition, so a quiet week and a loud week both end in
a decision rather than in unease.

The enumerated set becomes the query target for the fleet-version
record once [#96](https://github.com/rafacm/vinga/issues/96) lands:
knowing which versions the fleet actually runs is how the observation
half of the widening rule stops depending on someone remembering to
look. That is part 3 of #399 and not this record's work; this record
is what makes the query worth running.

What it costs, stated plainly:

- **A version nobody's board runs gets no promise.** A release
  upstream cut last week is outside the floor until a board arrives
  with it, however easy absorbing it might have been.
- **A board arriving with an unobserved version is outside the floor
  until the notes catch up.** The device may well work; what it does
  not have is a promise that it does, and closing that gap is a
  re-read, not a patch.

Both costs are visible rather than silent, which is the trade this
record makes with the watch: the manifest names what the notes were
read from, the weekly report says when that moved, and this table says
what the reading was for.

This record needs no separate reconsideration trigger. The widening
rule inside the Decision is one, and it is meant to fire routinely: a
new board is the ordinary way the floor moves. What would reopen the
record itself is a decision to narrow it, which the Decision already
routes to a superseding record.
