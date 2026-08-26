# Diagrams

Every diagram vinga keeps, indexed by the question it answers. The
prose that walks through the first two of them is
[`system-overview.md`](../../system-overview.md); this page is for
finding a picture and knowing what it is for.

A directory per authoring tool, so a third tool can join without
either one's files having to be picked out of a shared folder.
[`excalidraw/`](excalidraw/) holds the hand-drawn pair, whose
editable originals live in a hosted workspace.
[`plantuml/`](plantuml/) holds the three whose source is text in this
repository; [`plantuml/README.md`](plantuml/README.md) says what each
one is for and how to render them.

## The system at a glance

[`excalidraw/vinga-architecture-overview.png`](excalidraw/vinga-architecture-overview.png):
a human, an ESP32-S3 device, one WebSocket to your server, and the
providers you configured. The friendly top-level picture, and the one
the root README leads with, so it answers "what talks to what" before
any vocabulary has been introduced.

## What leaves the host

[`plantuml/vinga-architecture-overview.png`](plantuml/vinga-architecture-overview.png):
the same boxes drawn for a different question. Every provider is
coloured by its declared `egress` class, which is the thing
`server.local_only` is checked against at boot, so this is the
picture to read before answering "if I configure it this way, what
goes over the internet".

The two overview diagrams share a name and not a purpose, and neither
replaces the other. The Excalidraw one is drawn to be understood at a
glance by somebody meeting the system; the PlantUML one is generated
from text and answers an audit question about a configuration.

## One conversation turn, in detail

[`excalidraw/vinga-conversation-flow-detailed.png`](excalidraw/vinga-conversation-flow-detailed.png):
the teaching picture, read top to bottom, with flow 1 (blue) carrying
speech up to the language model and flow 2 (green) carrying the reply
back down. [`system-overview.md`](../../system-overview.md) walks it
step by step.

## One conversation turn, in sequence

[`plantuml/vinga-conversation-turn.png`](plantuml/vinga-conversation-turn.png):
the same turn drawn for ordering and overlap, from the boot exchange
through the wake word to the spoken reply: the tool loop's rounds,
the next sentence being synthesized while the current one plays, and
which listening mode re-arms the microphone.

## The barge-in decision

[`plantuml/vinga-barge-in-decision.png`](plantuml/vinga-barge-in-decision.png):
the gate an utterance must pass before it may cancel a reply that is
already streaming. Branches rather than flow, which is why it is not
folded into the turn diagram, and the one picture here that names the
structured event each branch emits.

## Keeping them true

The PlantUML sources live in this repository, so a pipeline change
and the picture of it move in the same commit and a reviewer reads
the diff.

The Excalidraw pair cannot be checked that way. Their editable
originals are scenes of the same names in the team Excalidraw
workspace (vinga collection); the committed `.excalidraw` files are
those scenes' exports and the `.png` files their renders, kept in
sync by hand. That is the catch: nothing in the repository can tell
you an export has drifted from its scene, so flag them when a
pipeline change makes them stale.
