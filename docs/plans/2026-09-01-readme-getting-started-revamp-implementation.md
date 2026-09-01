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


### PR review round

External review of the branch as pushed to PR #366, at `1b42b5be`
against `origin/main`: backend codex (codex-cli 0.151.0), model
gpt-5.6-terra, read-only sandbox, 2026-09-01, runtime 1m39s. The fast
tier by the tiering rule's own words, a documentation-only diff being
the case it names. One finding, P3, verdict as received: mergeable as
is. Condensed below as received, with its resolution and the commit that
landed it.

1. **P3: the implementation record misstates the PR's scope.** The
   document opened with "Six commits, touching four files", while
   `origin/main...HEAD` was 23 commits over seven files. Correct the
   counts or remove them, so a dated execution record reports what
   actually landed.

   *Resolution* (`953ec567`): confirmed, and worse than the finding
   said. The milestone's own branch is ten commits over seven files;
   the 23 is that plus the thirteen it inherits from the plan branch it
   stacks on. Both halves of the sentence were wrong, not just the
   arithmetic. Removed rather than corrected, per the finding's first
   option: the file list that replaces it says the same thing about
   scope and cannot be staled, where any number would have been staled
   again by this very round.

The finding is the same shape as the census trap recorded above, and
worth naming as one: a stacked branch makes every whole-diff count a
statement about two milestones, and a record written mid-milestone
describes a tree that no longer exists by the time it is pushed. What
survives an amendment is what a later reader can check against the tree,
which is the list of files, never a tally of commits.

## M2: Getting Started, executed

### What was done

The section reached by a reader who has nothing: a destination
paragraph, a Prerequisites list, a step 0 that pulls the model, step 3
rewritten as eight command lines, and the simulator block out. Plus the
inline spelling beside the one short-entity `-f` example in
`vinga-server/README.md`. What it touches: the root README, that server
README, `CHANGELOG.md`, this document, the plan and the census manifest.

The plan's milestone line said M2 "touches `vinga-server/README.md` and
the census manifest", which omitted the root README that carries the
whole section being rewritten. Read against the Page layout table two
sections above it, which assigns Getting Started to M2, the omission is
plainly a slip in the checklist rather than a scoping decision, and the
root README is edited.

### The walkthrough, as run

Every command below was executed, in order, against a deployment created
empty for the purpose: compose project `vingawalk`, its own volumes,
started after the development Postgres was stopped so nothing contended
for 5432. The stack ran `ghcr.io/rafacm/vinga-server:latest` at revision
`8eb7b553`, the head of `main`, and the CLI was installed by the
walkthrough's own step 2 from the same commit.

Step 0 `ollama pull qwen3:8b`, step 1 the compose fetch and the two
minted secrets, step 2 `uv tool install` and `vinga list` (which
answered an entirely empty configuration, confirming the starting
state), step 3 the eight lines, step 5 `vinga info`.

`vinga reload` took 11 to 15 seconds on the first run of a fresh data
volume, downloading the faster-whisper `small` weights and the
`en_US-lessac-medium` Piper voice inside it. The page says the first
reload is the slow one and no longer says how slow: the "few minutes"
the old step 3 claimed was not what a first run cost here, and a number
measured on one machine's network is not a promise the page can keep.

### The comparison against the preset

`vinga export` after the sequence, compared field by field against
`vinga-server/examples/presets/local-stack.yaml` with both documents
parsed rather than diffed as text, since the preset is mostly comments
and the export writes none. Empty containers the export emits for
sections nobody configured (`mcp_servers`, `prompt_fragments`,
`devices`) were treated as structure rather than content.

Three differences, where the plan's Tests section predicted two:

| Field | Preset | Stored |
| --- | --- | --- |
| `providers.llm.local.base_url` | `http://localhost:11434/v1` | `http://host.docker.internal:11434/v1` |
| `default_agent` | absent | `assistant` |
| `agents.assistant.prompt` | `...speakable: one or two sentences...` | `...speakable. One or two sentences...` |

The third is not a finding. The plan's own "The step 3 sequence" section
names exactly this prompt difference and gives the reason: a colon
followed by a space inside an unquoted `key=value` makes the value parse
as a YAML mapping rather than a string. The Tests section, written
later, counted two where its own plan had already described three. The
sequence is correct and the plan's count was wrong.

### Discoveries

**`host.docker.internal` reaches a loopback-only Ollama on macOS.** The
plan resolved that the base URL could name it but flagged that
resolution is not reachability. Measured rather than assumed: Ollama
listens on `127.0.0.1:11434` only, and a container on this host reaches
`http://host.docker.internal:11434/api/tags` with a 200 all the same,
because Docker Desktop's VM routes that name into the host's loopback.
The page says the macOS part of that plainly and claims nothing about
Linux, where a service bound to `127.0.0.1` behind
`host-gateway` is a different question.

**A stale `latest` is invisible.** `docker compose up` uses a locally
cached image if the tag resolves to one, so a machine that pulled
`ghcr.io/rafacm/vinga-server:latest` weeks ago keeps running that. This
cost an hour here: `vinga info` returned a 404 against a five-day-old
image while the CLI came from `main`, and the walkthrough looked broken
at step 5 when nothing was. `docker pull` first, then the whole
sequence, and the walkthrough above was re-run from empty on the correct
image. Not written into the page, which is addressed to a reader who has
no cached image at all, but recorded here because the failure looks
exactly like a bug in the CLI.

**The simulator refuses a board the server admits, when device auth is
off.** Filed as #369. The trial `.env` of step 1 sets
`VINGA_SERVER__AUTH__ENABLED=false`, and on that setting the simulator
reports "this board is not admitted" while the server's own log records
the same device resolving to `assistant`; its `--claim` and no-`--claim`
messages each advise the other. With the variable set to `true` and
nothing else changed, the same command holds a whole conversation. This
is independent of M2's edit: the simulator block leaves Getting Started
either way, and the Features bullet that keeps it is true on the default
setting, which is authentication on.

### Verified end to end

With device auth on, one conversation over the websocket, which is what
proves the step 3 sequence built a working deployment and not merely a
storable one:

```
saying: Hello, can you hear me?
heard: Hello, can you hear me?
said: I can hear you!
said: How can I assist you today?
reply: 44 frames, 19361 bytes, about 2640 ms of audio
the conversation reached: closed
```

That is faster-whisper transcribing, `qwen3:8b` answering over
`host.docker.internal`, and Piper speaking, all from the configuration
the eight published lines wrote.

The inline spelling added to `vinga-server/README.md` was executed too,
against the same deployment, and read back with `vinga provider show llm
claude`: it stores `type: anthropic`, `model: claude-sonnet-5` and
`api_key_env: ANTHROPIC_API_KEY`, which is what
`examples/llm-anthropic.yaml` holds. The model name in the first draft
of that line was invented and wrong; it was corrected against the
fragment before the command was run.

### Not verified here

**The board half was not walked.** No ESP32 was attached to the machine
this ran on (`ls /dev/cu.*` shows only the Bluetooth and debug consoles),
so steps 4, 6 and 7 (flash, NVS write, captive portal, pressing the
button and speaking) were not carried out, and the folded-in #308
obligation is not discharged. The PR carries them as unchecked boxes
rather than ticks. Everything the server side of that path depends on
was exercised through the simulator instead, which is a board's protocol
and not a board.

### PR review round

External review of the branch as pushed to PR #370, at `122f3a5c`
against `feature/readme-revamp-m1`: backend codex (codex-cli 0.151.0),
model gpt-5.6-sol, read-only sandbox, 2026-09-01, runtime 11m22s. Sol
rather than the fast tier despite a documentation-only diff, because the
commands this page publishes are load-bearing. Five findings, two P1,
three P2, verdict as received: mergeable after the listed fixes. All
five confirmed against the sources before being fixed; none rejected.

Two of them are the same failure, and it is worth naming: **a claim the
milestone made about itself, which its own record contradicts three
files away.** The plan's checklist said the board half was walked while
the implementation doc said it was not, and step 3's prose said the
writes may land in any order while the code refuses exactly that. Both
survived because each was written from what the sequence was meant to
be, and neither was checked against what it does.

1. **P1: M2 was ticked although its board walkthrough was not
   performed.** The checklist claimed every command ran and the
   Touch-LCD-1.54 was walked; this document says steps 4, 6 and 7 were
   not carried out, and `CHANGELOG.md` said "every command was
   executed".

   *Resolution*: the box is unticked, and says in the checklist itself
   that it stays unticked until a board has been walked. The changelog
   now names the steps that were executed (0 to 3, and 5) instead of
   claiming all of them. The tick was the more serious half: a
   checklist a fresh session resumes from is worth nothing if it can
   claim work nobody did.

2. **P1: the Linux Ollama path was left out, though the plan required
   it.** The plan's resolved question says the README states the tested
   path as tested and says in one sentence that a Linux host also needs
   Ollama listening beyond loopback, pointing at Ollama's own
   documentation. The page described only macOS. On a default Linux
   Ollama the pasted sequence reaches `vinga reload` with a name that
   resolves and a service that refuses.

   *Resolution*: the sentence is there, naming `OLLAMA_HOST`, linking
   Ollama's FAQ rather than inventing a procedure this project has not
   run, and saying plainly that the path is untested here.

3. **P2: "the seven lines may land in any order" is false.**
   `check_references` runs at write time, and its own docstring says
   refusing there "is what forces the natural creation order".

   *Resolution*: measured rather than reasoned about. Against an empty
   deployment, `vinga agent-defaults set` before any provider exists is
   refused, naming all four unresolved references, and `vinga
   default-agent set` before the agent exists is refused the same way.
   The page now says to type them in the order shown and says what the
   reload actually decouples, which is installation and not order.

4. **P2: `local-stack.yaml` is not the "same deployment".** It says
   `localhost` where the sequence says `host.docker.internal`, and sets
   no `default_agent`, so applying it unchanged reaches neither the host
   model nor a bound board.

   *Resolution*: described as the document form of the same deployment
   and explicitly not a drop-in replacement, with both differences
   named. These are two of the three the comparison above found; the
   third is the prompt's punctuation, which is a spelling of the same
   sentence rather than a difference a reader acts on.

5. **P2: the server README still led with `-f`.** The plan's resolution
   says line 2722 "is the one that leads with the inline fields, keeping
   `-f` beside it as the fragment alternative", and the first draft put
   them the other way round.

   *Resolution*: inline first, with `vinga reload` after it, and the
   `-f` form below as what a longer entry wants. The paragraph
   introducing the block no longer says the commands are run from a
   checkout directory, which was true only of the spelling that is now
   second.
