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

The verdict, with every number: gate 1 passed, recovering a known
echo's delay to 1.2 ms and its gain to 0.2 dB, but only through
`on_bot_turn_audio_data` recorded at the native output rate; the other
bot track pipecat offers is silently corrupted. Gate 2 did not pass on
size and did not fail on shape. The findings are the implementation
doc, not this file.

The serializer speaks xiaozhi **protocol v1** only, which is what
xiaozhi-sdk 0.5.1 exercises: bare Opus frames, no version negotiation,
no v2 or v3 binary headers.

## Running it

```bash
export PYTHONDONTWRITEBYTECODE=1   # AGENTS.md, the stale bytecode trap
uv sync
uv run python make_audio.py        # canned reply + utterance, via `say`
uv run python drive.py             # server + simulator, writes runs/<id>/
uv run python compose.py runs/<id> # both capture pairs
uv run python fidelity.py runs/<id>  # what each reference contains
```

`compose.py` writes two pairs, because the audio buffer processor
offers two bot tracks: `captures/` uses the delivered track, the only
one whose arrivals can be timestamped, and `captures-turn/` uses the
turn track, which is faithful but arrives once at the end of the turn.
The transport paces at real time on its own, so the adapter adds no
pacing of its own; an earlier version did, and measurement showed it
was redundant.

Then the analysis, with the repository's own unmodified scripts. The
control proves the pair is well formed; the injection is gate 1:

```bash
cd ../..
uv run --no-project --with numpy --with scipy \
    python scripts/echo_leakage_control.py \
    spikes/pipecat-alignment/runs/<id>/captures <id> --delay-ms 250
cd spikes/pipecat-alignment
uv run python inject.py runs/<id> --ref turn --delay-ms 250
```

`inject.py` passes `--max-lag-s 2.0` by default rather than the
script's own 1.2, because a 1500 ms echo falls outside a 1.2 s search
space entirely and would come back as a broken measurement instead of
a measured one.
