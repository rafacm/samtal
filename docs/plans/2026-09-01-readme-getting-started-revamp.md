# The Getting Started revamp and a focused front page

Plan for [#346](https://github.com/rafacm/vinga/issues/346), with the
verification obligation [#308](https://github.com/rafacm/vinga/issues/308)
folded into it. Implementation notes land in the companion
`2026-09-01-readme-getting-started-revamp-implementation.md`, one section
per milestone, appended in the change that ticks the milestone here.

## Goal

A front page that passes the five-minute test. A reader arriving at the
repository should learn what vinga is in two paragraphs, see that the
board on the table is one this project actually runs, and reach a
spoken conversation by copying commands in order without downloading a
configuration file, editing a URL by hand, or guessing which of two
apply spellings installs anything.

The section is rewritten against the CLI as it is after
[#341](https://github.com/rafacm/vinga/issues/341), not as it was during
the 2026-08-29 walkthrough that filed this issue: `vinga info` serves the
onboarding URL over the API, `apply` reloads by default, and every entity
write takes `key=value` arguments inline. Those three landed; the front
page has not caught up with any of them.

## The issue's decisions, restated

Settled on the issue and not re-litigated here.

- The opening paragraph of Getting Started is replaced by a couple of
  sentences saying what the reader is about to do.
- A step 0 sets up Ollama and pulls `qwen3:8b`, linking onward to the
  LLM configuration guide.
- A Prerequisites subsection lists what must be installed, and states
  which systems local runs have actually been tested on.
- Step 3 becomes a linear sequence of `vinga` commands rather than a
  `curl` of the preset document followed by `apply`.
- The simulator block leaves the section.
- "What is vinga?" loses the architecture diagram and the "whole
  picture at a glance" paragraph that summarizes it.
- Features is revamped around end-user value rather than an
  implementation inventory.
- Project Layout is removed; `docs/README.md` owns orientation.
- A Documentation section near the end points into `docs/`.
- Credits gains a link to `docs/related-projects.md`.
- Both READMEs lead with the `key=value` spelling for short entities.
- From the 2026-09-01 amendment: the hardware table leads with the
  Waveshare ESP32-S3-Touch-LCD-1.54, then the AMOLED-2.16 and the
  ePaper-1.54, both `planned 🚧`; the LLM guide is
  [#364](https://github.com/rafacm/vinga/issues/364) and is not written
  here; verification is walked on the Touch-LCD-1.54.

## Open questions, resolved

### Where the device photo goes, and what the page shows above the fold

The issue says a picture of an actual device replaces the architecture
diagram. The photo supplied (`IMG_4732.jpg`, 2026-08-29) shows the
Touch-LCD-1.54 in its white case on a garden rail, and the screen is
displaying upstream's captive-portal state: 配网模式, and the access
point name with 浏览器访问 along the bottom.

**Resolved: it becomes the hero image in "What is vinga?", where the
issue asks for it.** Two reasons. The literal ask is a picture of an
actual device, and this is one, photographed well. And the alternative
of leaving that section with no image at all makes the front page worse
in exactly the dimension this issue is trying to improve: a landing page
whose first two screens are unbroken prose is not a focused front page,
it is a wall.

The screen's provisioning state is honest rather than awkward. The
warning three lines above it already says the loop runs on stock
upstream firmware, and the
[compatibility floor](../architecture/product-promises.md#stock-xiaozhi-firmware-is-the-compatibility-floor)
is a promise this project makes on purpose, not something the front page
should hide. What the photo shows is the moment step 6 describes.

**The alternative, recorded so it is a one-line change rather than a
re-plan:** place it at step 6, which is literally what it depicts, and
leave the hero slot for a later photograph of the same board mid
conversation with a transcript on screen. If that photograph arrives,
it takes the hero slot and this one moves to step 6; nothing else in
the plan changes.

**Preparation, which is not optional, and is a no-leak surface.** The
original file carries EXIF GPS coordinates, the capture timestamp and
the phone model. Publishing it as received would put the photographer's
location in a public repository, so M1 treats the asset the way the
no-leak lens treats a retained message: what it may not carry is stated,
and the absence is verified rather than assumed.

The committed file is `assets/vinga-touch-lcd-1.54.jpg`, 1600px wide,
quality 88, about 133 KB. It is produced by auto-orienting **before**
metadata removal, which is load-bearing rather than tidy: stripping the
EXIF orientation tag from a file that relied on it publishes a rotated
hero, and the rotation is invisible in any check that reads metadata
instead of pixels. The verification is in the Tests section, and it
includes looking at the result.

The visible four hex characters of the board's MAC in the access point
name are the SSID upstream's firmware broadcasts while provisioning, and
are left as they are.

### Whether the agent write can be a `key=value` one-liner

The issue asks for `key=value` where it fits and `-f` for agents with
prompts. Taken literally that would make the shortest path to a talking
server include downloading a file, which is the friction this issue
exists to remove.

**Resolved: the agent is written inline, with a prompt that contains no
`: ` sequence.** A `key=value` value "reads as one YAML scalar"
(`vinga agent set --help`), and a plain YAML scalar containing a colon
followed by a space is a mapping, not a string. The preset's own prompt
trips this exactly once:

```
>>> yaml.safe_load("Keep replies short, plain, and speakable: one or two sentences")
{'Keep replies short, plain, and speakable': 'one or two sentences'}
```

So the README's prompt says "speakable. One or two sentences" rather
than "speakable: one or two sentences", which reads no worse and parses
as the string it looks like. This is a constraint on the prose, not a
bug to fix in the CLI: the scalar rule is the documented behavior, and
`-f` remains the answer for a prompt long enough to want a file.

M2 executes this exact line rather than reasoning about it, and records
what the server stored.

### What the reader types for the LLM base URL

The walkthrough's worst trap was a `base_url` of `localhost` resolving
inside the server's container ([#340](https://github.com/rafacm/vinga/issues/340)).
Today's step 3 hands the reader a preset with `localhost` in it and a
comment telling them to edit it.

**Resolved: the README's command carries `host.docker.internal` from
the start**, so there is no edit step and no trap to explain. This is
sound on every platform because the committed compose file declares
`extra_hosts: - "host.docker.internal:host-gateway"`
(`docker-compose.yml:208-215`), which is what makes the name resolve on
Linux as well as on macOS. One sentence says why the name is what it is,
for the reader running the server some other way.

### Whether the generated CLI recipes are part of the `key=value` sweep

**Resolved: no, and the plan says so to stop a reviewer asking.** The
recipes region of `docs/reference/cli.md` is generated, and it is
generated by reading the command lines quoted inside the example files
themselves (`docgen._quoted`, matched by program-name prefix). A recipe
naming `-f <the file it was read from>` is the mechanism working as
designed, not an example that failed to catch up. The sweep is
therefore a change to hand-written prose in the two READMEs, and touches
no generator and no generated region.

The inventory behind that: `vinga-server/README.md` contains exactly two
such examples, at lines 1442 (`vinga apply -f examples/presets/cloud-stack.yaml`)
and 2722 (`vinga provider set llm claude -f examples/llm-anthropic.yaml`).
The root README's is step 3. There is no third file.

Only one of those two can gain an inline alternative. `apply` takes a
whole document and has no `key=value` form at all, and the issue keeps
`-f` for documents on purpose, so line 1442 is left exactly as it is.
Line 2722 is a short entity and is the one that leads with the inline
fields, keeping `-f` beside it as the fragment alternative.

## Smaller decisions

- **The anchors `#getting-started` and `#credits` must survive.** Three
  pages link into them from outside: `vinga-server/README.md:2686`,
  `docs/reference/cli.md:138` and `docs/related-projects.md:177`. The
  section headings that carry them do not change wording.
- **`#project-layout` has no inbound links** other than the root
  README's own navigation line, so removing the section breaks nothing.
  Verified by grep across every tracked Markdown file.
- **Removing the architecture diagram orphans nothing.** The same
  render is used by `docs/system-overview.md:26` and indexed by
  `docs/architecture/diagrams/README.md:18`. The asset stays where it
  is; only the root README stops embedding it.
- **The navigation line is regenerated by hand in M1** to match the
  sections that exist: Project Layout out, Documentation in.
- **The simulator keeps its Features bullet.** Dropping the block from
  Getting Started is the issue's decision; dropping the capability from
  the page is not, and "try it without hardware" is end-user value of
  exactly the kind the Features revamp is supposed to lead with.
- **Features gets a stated rule rather than a rewrite by taste**: at
  most seven bullets, each answering what the reader can do or why this
  beats the obvious alternative, and no bullet promoting a 🚧 item to
  sound shipped. The thin-fork bullet, which is an implementation fact
  carrying a 🚧, is the one that leaves.
- **Step 5 is already correct** and is left alone beyond wording: the
  `vinga info` collapse the issue asked for landed with #341.

## The step 3 sequence

The literal commands the rewritten step 3 publishes, which M2 executes
before it publishes them:

```bash
vinga provider set llm local type=openai_compatible base_url=http://host.docker.internal:11434/v1 model=qwen3:8b egress=false
vinga provider set asr whisper type=faster_whisper model=small vad_filter=true
vinga provider set tts voice type=piper voice=en_US-lessac-medium
vinga provider set vad ears type=silero
vinga agent-defaults set llm=local asr=whisper tts=voice vad=ears
vinga agent set assistant "prompt=You are a helpful voice assistant. Keep replies short, plain, and speakable. One or two sentences, no lists, no markdown. Always reply in the language the user spoke."
vinga default-agent set assistant
vinga reload
```

The first six lines reproduce what `examples/presets/local-stack.yaml`
holds, entity for entity: four providers, the four agent defaults naming
them, and the one agent. The seventh adds what the preset deliberately
omits and says it omits, because it is the one thing a preset cannot
know: which agent an unbound board reaches. The eighth installs all of
it, and is the only line that reloads, because entity writes do not
(only `apply` carries a reload, and this sequence does not use it).
`default-agent set` needs no reload either way, since it is read as a
device asks for it.

Two differences from the preset, both deliberate. The `base_url` says
`host.docker.internal` rather than `localhost`, for the reason in the
resolved question above. And the prompt says "speakable. One or two
sentences" rather than the preset's "speakable: one or two sentences",
for the scalar reason in the resolved question above.

Every value was parsed as a lone YAML scalar before this was written:
`openai_compatible`, `http://host.docker.internal:11434/v1`, `qwen3:8b`,
`small`, `piper`, `silero`, `faster_whisper` and `en_US-lessac-medium`
all come back as strings (`qwen3:8b` because a colon makes a mapping
only when a space follows it), `true` and `false` as booleans, and the
colon-free prompt as one string.

## Page layout

Neither milestone adds a page. The front page's section order after
both milestones, with what each is for:

| Section | What changes | Milestone |
| --- | --- | --- |
| Header, badges, navigation | Navigation line follows the sections that exist | M1 |
| Early-development warning | Unchanged | |
| What is vinga? | Diagram and its summary paragraph out, device photo in | M1 |
| Features | Rewritten to the rule above | M1 |
| Hardware | Table reordered, Touch-LCD-1.54 first | M1 |
| Getting Started | Destination paragraph, Prerequisites, step 0, step 3 rewritten, simulator block out | M2 |
| Project Layout | Removed | M1 |
| Documentation | New, pointing into `docs/` | M1 |
| Credits | Gains the `docs/related-projects.md` link | M1 |
| Changelog, License | Unchanged | |

`vinga-server/README.md` gains the inline spelling at its one short
entity example in M2, and keeps `-f` beside it: showing the pair is what
stops a reader reaching for a heredoc. Its whole-deployment example does
not change, because `apply` has no inline form.

## Design footprint

This is a documentation change and deepens no module, adds no seam and
writes no code. Stated explicitly rather than left implied, because the
milestone template asks for it.

The one design-shaped claim in it is locality: step 3 stops being a
second copy of `examples/presets/local-stack.yaml` and starts being the
shortest command sequence that reaches the same state. Where the two
would disagree about a field's meaning, the README links
`docs/reference/domain-config.md` rather than explaining it again.

## Documentation footprint

- **M1** falsifies nothing outside the root README. The hardware table's
  new order must agree with `vinga-esp32/README.md`, whose table lists
  the same boards and moves with it (AGENTS.md, "Keep in sync").
  Verified in the milestone rather than assumed.
- **M2** touches `vinga-server/README.md` in the sweep. It states the
  step-3 sequence as the front page's own path and does not restate
  field semantics, which belong to the generated reference.
- **Neither milestone edits a generated page.** `docs/reference/` changes
  only through its generators, and no generator changes here.
- **Step 0's onward link** is `docs/reference/domain-config.md` plus the
  example fragments until #364 lands, and #364 rewires it to the guide.
  M2 does not write that guide.

## Tests

There is no unit test for prose. What stands in for one:

- `uv run python scripts/check_doc_links.py` (the `docs` workflow's own
  check) for every internal link and anchor, including the three inbound
  anchors listed above.
- `uv run pytest tests/unit/test_command_spellings.py -q` from
  `vinga-server/`, which sweeps every tracked file. Both milestones move
  command text, so both run it, and a stale manifest is regenerated with
  `uv run python -m tests.unit.test_command_spellings`, never by hand.
- **The committed photograph carries no metadata**, verified in M1 and
  recorded in the implementation doc:
  `magick identify -verbose assets/vinga-touch-lcd-1.54.jpg` matches no
  line for EXIF, GPS, XMP, IPTC, a colour profile or a capture time
  (ImageMagick's own `date:create` and `date:modify` are read from the
  filesystem, not from the file, and do not count), the same probe on
  the source file matches 66 such lines, `%[orientation]` is
  `Undefined`, the geometry is 1600x767, and the rendered image was
  looked at rather than merely measured, since a rotation is invisible
  to every check above.
- **Every command in the rewritten section is executed**, in order, from
  an empty deployment, against a stack started under its own compose
  project name so it collides with nothing already running. What the
  server actually stored is read back with `vinga agent show assistant`
  and `vinga provider show llm local`, and the reload's own listing is
  the evidence the engines were built. A command that is not executed is
  not published.
- The board half (flash, NVS write, captive portal, speaking) is walked
  on the Touch-LCD-1.54 per the folded-in #308 obligation. Anything the
  implementing session cannot carry out stays an unchecked box on the PR
  with a note saying why, per the unverified-claims practice; it is not
  ticked on the strength of the pieces having worked separately.

The standing review lenses, dispositioned rather than skipped: **no-leak
applies, to exactly one surface**, the committed photograph, whose
verification is the bullet above; it does not apply anywhere else,
because nothing here writes a message, a field or an exception.
Pin-before-reshaping does not apply, since no behavior moves;
closed sets and honest seams do not apply, since no code changes.
Inventories by tooling does apply and is the reason every count in this
plan (two `-f` examples in the server README, three inbound anchors, one
orphaned diagram reference) came from grep rather than from reading.

## Risks

- **A published command that was never run.** The whole point of the
  section is that it works when copied. Mitigation: the execution
  requirement above, and an unchecked box rather than a tick for
  anything that could not be run.
- **The prompt scalar.** Resolved above, but it is the kind of thing
  that returns if the prose is edited later. Mitigation: M2 records the
  parse rule in the implementation doc, so the next editor meets the
  reason rather than rediscovering the symptom.
- **`host.docker.internal` for a reader not using the compose file.**
  Mitigation: one sentence naming why the name resolves, pointing at the
  server README's container section for the general case.
- **Model download time on the first reload.** Several minutes on a cold
  data volume. Mitigation: the step says so, as it does today.
- **The photo decision reversing after M1 merges.** Mitigation: the
  alternative is recorded above as a one-line move, and the asset is
  committed at a name that describes the board rather than the slot.
- **The two hardware tables drifting.** Mitigation: named in M1's
  documentation footprint and checked in the same commit.

## Milestones

- [ ] **M1: the front page around Getting Started.** The hardware table
      reordered to lead with the Touch-LCD-1.54; "What is vinga?" losing
      the diagram and its summary paragraph and gaining the device
      photo; Features rewritten to the stated rule; Project Layout
      removed; a Documentation section added; Credits gaining its
      `docs/related-projects.md` link; the navigation line following the
      sections that now exist; `vinga-esp32/README.md`'s table moved to
      match. Touches the root README, that firmware README and one new
      asset, so the `docs` workflow is the lane that runs.
- [ ] **M2: Getting Started, executed.** The destination paragraph, the
      Prerequisites subsection, step 0 for Ollama and `qwen3:8b`, step 3
      as a linear `key=value` sequence ending in one `vinga reload`, the
      simulator block removed, and the inline spelling added beside the
      one short-entity `-f` example in `vinga-server/README.md`
      (line 2722; the `apply` example at 1442 is left alone). Every command run
      before it is published, and the board half walked on the
      Touch-LCD-1.54. Touches `vinga-server/README.md`, so the server
      workflow is the lane that runs.

## Plan review round

External review of commit d8bda771: backend codex (codex-cli 0.151.0),
model gpt-5.6-sol, sandbox read-only, 2026-09-01, runtime 456 seconds.
Verdict: ready after the P1/P2 amendments. Findings condensed but
faithful; resolutions appended per amendment.

1. **P1: the plan never states the command sequence it claims to
   verify.** The resolved question says M2 executes "this exact line",
   and the milestone says "a linear `key=value` sequence", but no
   sequence appears anywhere in the plan. The preset defines four
   providers, four defaults and one agent; materially different and
   potentially wrong implementations fit the milestone text as written.
   The plan should carry the literal sequence, say that it reproduces
   the preset's entities, and say that it deliberately adds the default
   agent the preset omits.

   *Resolution*: adopted. The plan gains a section of its own, "The step
   3 sequence", carrying all eight lines verbatim, saying which six
   reproduce the preset, which one adds the default agent the preset
   cannot know, and which one installs. It also records the two
   deliberate differences from the preset and the scalar parse of every
   value in it.

2. **P1: one of the two promised server-README inline alternatives
   cannot exist.** The plan requires the inline spelling beside both
   `-f` examples, but the first is `vinga apply -f ...`, and `apply`
   takes a document through `-f` and has no `key=value` form; the issue
   itself keeps `-f` for documents. The whole-deployment example at
   `vinga-server/README.md:1442` should be left unchanged, and only the
   short provider at line 2722 should lead with inline fields while
   keeping `-f` as the fragment alternative.

   *Resolution*: adopted, and the plan was simply wrong. `apply` takes a
   document and has no inline form, so the whole-deployment example at
   line 1442 is now explicitly left unchanged, and the sweep is one
   example rather than two, in the resolved question, the page layout
   and M2's own description.

3. **P1: the photo is a no-leak surface, and the plan excludes it from
   no-leak verification.** The plan states the source carries GPS
   coordinates, a capture timestamp and the phone model, then says
   no-leak does not apply because nothing here writes a message or a
   field. Neither named check inspects JPEG metadata. The plan should
   apply the lens to M1 and require recorded verification that the
   committed file carries no EXIF, GPS, XMP, capture-time,
   device-model or thumbnail metadata, that the image was auto-oriented
   before metadata removal so stripping the orientation tag cannot
   publish a rotated hero, and that the result was inspected visually.

   *Resolution*: adopted, and the contradiction was real: the plan said
   the file carries GPS coordinates and then disposed of the lens that
   covers it. No-leak now applies to M1 at exactly one surface, the
   photo section says why auto-orienting has to precede stripping, and
   the Tests section carries a named probe whose result is recorded in
   the implementation doc, including looking at the image, because a
   rotation is invisible to a metadata check.

4. **P2: the readback does not prove field-for-field equivalence.**
   Reading back `agents.assistant` and `providers.llm.local` alone would
   miss an omitted `asr.whisper.vad_filter`, the wrong Piper voice, a
   malformed VAD entry or incorrect agent defaults, and a successful
   reload proves only that the stored world can be built, not that its
   values match the preset. The plan should compare the whole stored
   domain against `local-stack.yaml`, with only the intended `base_url`
   substitution and the added `default_agent`.

5. **P2: both CI-lane claims omit the command-spellings manifest and
   are wrong.** The census stores file and line and fails on any drift.
   M1 moves the root README's command lines and M2 changes both command
   blocks, so both regenerate `vinga-server/tests/unit/command-spellings.txt`,
   which is under `vinga-server/**` and triggers the server workflow;
   both also touch non-ignored root and docs files, so the docs workflow
   runs too. The manifest also has no entries for the newly committed
   plan itself. The plan should name the manifest in both milestone
   footprints, regenerate it for the plan commit and each milestone, and
   state that both workflows run for both milestones.

6. **P2: the plan explicitly leaves the changelog unchanged** while
   AGENTS.md requires a `CHANGELOG.md` entry with every notable change.
   The plan should include a dated entry in each milestone, or say how
   each independently releasable PR records its notable user-facing
   change.

7. **P2: `host.docker.internal` resolution is overclaimed as
   cross-platform reachability.** The `extra_hosts` alias proves address
   resolution, not that Ollama is listening on an interface reachable
   through the host gateway, and the compose feature record says Linux
   was never exercised. The plan should limit the claim to the tested
   macOS path; if the walkthrough is to support Linux, step 0 must
   include and verify the platform-appropriate Ollama listener
   configuration rather than equating hostname resolution with service
   reachability.

8. **P2: the hardware section is reordered but not fully rescoped.**
   The introduction still says all three boards are ones vinga "targets
   and tests" although two rows are planned, and M1 names only the
   reorder. The plan should also rewrite that introduction to
   distinguish targets from the one tested board, and apply the
   `planned 🚧` status and the same row order to both hardware tables.

9. **P2: the documented link-check command exits with a usage error.**
   `scripts/check_doc_links.py` requires a `<repo-root>` argument and
   exits 2 without one. The plan should use the workflow's actual
   command, `python3 scripts/check_doc_links.py .`.

10. **P3: the proposed prerequisites are not exhaustive for the copied
    walkthrough.** Step 1 invokes `curl` and `openssl` directly and step
    2 installs from a `git+https` URL, while the plan names only Docker
    Compose, uv, Ollama and a board. The plan should list `curl`, an
    `openssl` command and Git, or state that the tested macOS setup
    assumes those system-provided tools.
