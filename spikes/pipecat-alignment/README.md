# pipecat alignment spike

Throwaway spike for issue #89. It builds the minimal pipecat pipeline
behind a xiaozhi frame serializer, drives it with the xiaozhi-sdk
device simulator, and runs the two gates from the issue: capture
alignment and adapter size. The plan is
`docs/plans/2026-08-11-pipecat-alignment-spike.md`; the findings are in
its companion implementation doc.

Nothing here is a supported surface. It is its own uv project so that
its dependencies never touch the server's, and its pipecat version is
pinned exactly because the measured numbers only mean something
against a named version.

## Running it

```bash
export PYTHONDONTWRITEBYTECODE=1   # AGENTS.md, the stale bytecode trap
uv sync
uv run python make_audio.py        # canned reply + utterance, via `say`
uv run python drive.py             # server + simulator, writes runs/<id>/
uv run python compose.py runs/<id> # the samtal-format capture pair
```

Then the analysis, with the repository's own unmodified scripts:

```bash
cd ../..
uv run --no-project --with numpy --with scipy \
    python scripts/echo_leakage_control.py \
    spikes/pipecat-alignment/runs/<id>/captures <id> --delay-ms 250
uv run --no-project --with numpy --with scipy \
    python spikes/pipecat-alignment/inject.py runs/<id> --delay-ms 250
```
