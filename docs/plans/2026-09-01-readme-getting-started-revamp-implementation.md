# The Getting Started revamp and a focused front page: implementation

Companion to
[`2026-09-01-readme-getting-started-revamp.md`](2026-09-01-readme-getting-started-revamp.md).
One section per milestone, recording what was actually written, the
deviations from the plan, what building it turned up, and the
verification results as they came out. A milestone with no deviations
says so explicitly.

## M1: the front page around Getting Started

### What was done

No code, no generator, no generated page, and not one line of the
Getting Started section, which is M2's. What it touches: the root
README, `vinga-esp32/README.md`, whose hardware table moves with it, one
new asset, `CHANGELOG.md`, this plan and this document, and the census
manifest. A count of commits is deliberately not given: this branch
stacks on the plan's, so the pull request's diff against `main` carries
the plan's commits too, and any number written here goes stale at the
next amendment.

**Hardware.** The introduction said these were the boards vinga
"targets and tests", which was true of a list whose rows had all been
attempted and false of a list whose second and third rows are
`planned 🚧`. It now says that any board xiaozhi-esp32 supports can
work, that the three rows are not in the same state, and that the
Touch-LCD-1.54 is the one board vinga is developed and tested on while
the other two are targets with a guide each and no hands-on run behind
them. Both tables were reordered to Touch-LCD-1.54, Touch-AMOLED-2.16,
ePaper-1.54, and `vinga-esp32/README.md`'s statuses were brought to the
project README's wording: `[**working** (upstream firmware)]` and
`planned 🚧` rather than a bare `planned`.

**What is vinga?** The architecture diagram embed and the "That is the
whole picture at a glance" paragraph that summarized it are gone. The
section opens instead with `assets/vinga-touch-lcd-1.54.jpg`, a
Touch-LCD-1.54 in its white case on a garden rail, its screen in the
provisioning mode stock upstream firmware boots into. Alt text
describes the board, the setting and what the screen is showing,
including that the line along the bottom is an access point name and an
instruction to open it in a browser, since a reader who cannot see the
image should not be left with unexplained characters. One italic
caption line under it says what the board is waiting for.

**Features.** Nine bullets became seven, against the plan's rule. The
thin-fork bullet left, as the plan said it would. Self-hosting and the
absence of an account were two bullets making one point and are now
one. The local pipeline and the freedom to swap any stage of it were
spread over three bullets ("Pluggable LLM", "Pluggable voice", and the
local half of "Self-hosted end to end") and are now two: one for the
loop that needs no API key at all, one for substituting any stage. The
simulator kept its bullet, per the plan. The section's opening
paragraph about a thin device and a smart server is unchanged.

**Project Layout** is removed. **Documentation** is the new section in
its place, before Credits: the `docs/` index and the four doors into it
that a reader arrives for (`system-overview.md`, `devices/`,
`reference/`, `architecture/`), plus one sentence pointing at the other
two READMEs. It restores the onward link to `docs/system-overview.md`
that left with the diagram.

**Credits** gains a paragraph pointing at
[`docs/related-projects.md`](../related-projects.md), keeping that
page's own posture rather than inventing a comparison the front page
would have to maintain.

**The navigation line** now reads What is it? / Features / Hardware /
Getting Started / Documentation / Credits / Changelog. The headings
carrying `#getting-started` and `#credits` were not touched, so the
three inbound links keep working.

**`CHANGELOG.md`** gains a `## 2026-09-01` section with two `### Changed`
entries, the front page's structure and the two hardware tables.

### Deviations from the plan

Three, all small, all recorded because each is a choice the plan left
open rather than one it made.

**1. The photo sits above the epigraph, not below it.** The plan says
"hero image in What is vinga?" without saying where in the section. It
went directly under the heading, before the *Be Kind Rewind* quote,
for two reasons: that is the earliest point at which it breaks the wall
of prose the plan objects to, and the quote reads as the opening of the
paragraph that follows it ("You take what you like" into "We took two
projects we liked"), so pushing a full-width image between them would
have cost more than it bought.

**2. The photo carries a one-line caption.** The plan does not ask for
one. Without it a sighted reader meets Chinese characters on a screen
with no explanation, and the plan's own reasoning for keeping the
provisioning state visible is that it is honest rather than awkward.
The caption says what the board is waiting for and does not cite a step
number, so M2's renumbering cannot stale it.

**3. `vinga-esp32/README.md`'s table gained a one-sentence
introduction.** It had none: the heading was followed straight by the
table, so applying the plan's "same status wording" would have left
that page's three rows reordered with nothing saying why the first one
is different. The sentence links the project README's Hardware section
rather than restating it, which is what a maintained map may do.

Everything else in M1's description was implemented as written.

### Decisions

**The hardware introduction does not claim the walkthrough.** A first
draft said the Touch-LCD-1.54 is "the board every step below was walked
on". That walk is M2's obligation and had not happened when this
milestone was written, so the sentence would have been an unverified
claim on the front page. It says "developed and tested on" instead,
which the board's own `working` status already supports.

**The transport limitation survived the Features rewrite.** The old
self-hosting bullet ended with "WebSocket is the only transport for v1;
upstream's MQTT+UDP alternative may follow", which is an inventory
sentence of exactly the kind the rule rejects. Rather than drop the
fact, it is now a clause inside the bullet ("over a WebSocket, the one
transport v1 carries"), so the page keeps the bound without spending a
sentence on it.

**Nothing was orphaned by the two removals.** The architecture render is
still used by `docs/system-overview.md` and indexed by
`docs/architecture/diagrams/README.md`; only the root README stopped
embedding it. `#project-layout` had no inbound link but the navigation
line above it, re-confirmed by grep before the section was deleted (the
only other hit in the tree is the plan's own sentence saying so).

### Verification

**Internal links and anchors.** From the repository root, the docs
workflow's own invocation:

```
$ python3 scripts/check_doc_links.py .
checked 173 files, 0 failures
```

**The photograph carries no metadata.** `magick` 7 via Homebrew.

```
$ magick identify -verbose assets/vinga-touch-lcd-1.54.jpg \
    | grep -inE 'exif|gps|xmp|iptc|profile|DateTime|date:'
81:    date:create: 2026-09-01T06:23:08+00:00
82:    date:modify: 2026-09-01T06:23:08+00:00
83:    date:timestamp: 2026-09-01T06:24:21+00:00
```

The three `date:` lines are ImageMagick's own, read from the
filesystem rather than from the file, and the plan says they do not
count. No line matches EXIF, GPS, XMP, IPTC, a colour profile or a
capture time. The same probe on the source, with the `date:` lines
excluded, matches the 66 the plan predicted:

```
$ magick identify -verbose ~/Downloads/IMG_4732.jpg \
    | grep -icE 'exif|gps|xmp|iptc|profile|DateTime'
66
$ magick identify -verbose assets/vinga-touch-lcd-1.54.jpg \
    | grep -icE 'exif|gps|xmp|iptc|profile|DateTime'
0
```

Geometry and orientation are what the plan specifies, and the source's
own orientation was `TopLeft`, so auto-orienting was a no-op here
rather than a rotation the strip would have discarded:

```
$ magick identify -format '%wx%h %[orientation]' assets/vinga-touch-lcd-1.54.jpg
1600x767 Undefined
$ magick identify -format '%wx%h %[orientation]' ~/Downloads/IMG_4732.jpg
2557x1225 TopLeft
```

**The image was looked at**, not only measured, because a rotation is
invisible to every check above. It renders upright: the board stands on
a weathered wooden rail running left to right across the lower third,
the garden behind it is blurred green, and the screen's three lines
(the status row with the WiFi glyph and 配网模式, the figure in the
middle, and the access point name with 浏览器访问 along the bottom)
read the right way up.

**Lint**, from `vinga-server/`, which this milestone does not touch and
which is run to prove it:

```
$ uv run ruff check .
All checks passed!
```

**The command-spellings census**, from `vinga-server/`, against the
development Postgres already running as compose project `vinga346`.
The manifest records file and line, and this milestone moves command
lines in `README.md` and `CHANGELOG.md` without changing a single
command, so it failed before regeneration exactly as the plan warned:

```
$ PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/unit/test_command_spellings.py -q
F..................................                                      [100%]
E       AssertionError: assert '# Every comm...rver status\n' == '# Every comm...rver status\n'
E         - NGELOG.md:55  historical  vinga memory list agent
E         + NGELOG.md:28  historical  vinga memory list agent
1 failed, 34 passed in 14.04s
```

Regenerated with the generator, as the last edit of the milestone, and
re-run green:

```
$ PYTHONDONTWRITEBYTECODE=1 uv run python -m tests.unit.test_command_spellings
wrote .../vinga-server/tests/unit/command-spellings.txt
$ PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/unit/test_command_spellings.py -q
...................................                                      [100%]
35 passed in 4.09s
```

The regeneration moved 118 lines and changed no command: every one of
them is the same spelling at a new line number, which is the failure
mode the plan predicted. This implementation doc contributes no entry
of its own to the manifest, so writing it does not stale the file
again.

**Not verified here.** Nothing in M1 is a runtime claim, so no server
was started and no board was flashed; the walkthrough and the device
half belong to M2 and are its obligation, not a box this milestone
could tick.

**The census counts this document too, including the failure output
quoted in it.** The block above pastes two lines of a census diff, and
those lines carry a command spelling, so pasting them added two entries
to the manifest and staled it again. That is not a quirk of this
milestone: any implementation doc that quotes census output does it, and
the loop only terminates because the second regeneration's output is not
pasted anywhere. The rule the plan already states, regenerate last,
covers it only if "last" means after the implementation doc is written
and not merely after the code is. The manifest committed here was
regenerated after this paragraph existed.

