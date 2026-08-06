# PlantUML diagrams

Diagrams whose source is text in this repository, so a pipeline change and the picture of it move in the same commit and a reviewer can read the diff. The Excalidraw diagrams one directory up are the hand-drawn counterpart: better looking, but their editable original lives in a hosted workspace and the committed export can drift from it silently.

Each `.puml` file is the source. The rendered `.png` and `.svg` take their names from the `@startuml <name>` line, not from the filename.

## Rendering

```bash
brew install plantuml   # brings Graphviz, which the overview needs
cd docs/architecture/plantuml
PLANTUML_LIMIT_SIZE=16384 plantuml -tpng -failfast2 *.puml
PLANTUML_LIMIT_SIZE=16384 plantuml -tsvg -failfast2 *.puml
```

`-failfast2` makes a syntax error fail the command instead of writing an image with the error drawn into it. Rendering is local; nothing is sent to a PlantUML server.

## The diagrams

- [**architecture-overview.puml**](architecture-overview.puml): what runs where, and what leaves the host. Every provider is coloured by its `egress` class marking, which is the thing `server.local_only` is checked against at boot. Read this to answer "if I configure it this way, what goes over the internet".

- [**conversation-turn.puml**](conversation-turn.puml): one turn as a sequence, from the OTA boot exchange through the wake word to the spoken reply. A sequence diagram because the interesting part is ordering and overlap: the tool loop's rounds, the next sentence being synthesized while the current one plays, and which listening mode re-arms the microphone.

- [**barge-in-decision.puml**](barge-in-decision.puml): the gate an utterance must pass before it may cancel a reply already streaming. Its own diagram because it is branches rather than flow, and the conversation-turn diagram draws only the happy path. Sources: `Session._finish_utterance`, `Session._gate_barge_in`, and the ADR "replies cancel only on evidence of speech".

## Keeping them true

These describe the code, so they go stale the way comments do. The overview names provider types and their egress markings; the turn diagram names constants (`MAX_TOOL_ROUNDS`, the pipeline and output sample rates, the frame duration) and configuration keys; the barge-in diagram names both thresholds and every structured event a branch emits. Changing any of those is the moment to change the diagram, in the same commit.
