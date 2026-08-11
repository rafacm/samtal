"""Generate the spike's two audio files locally, with macOS `say`.

Measurement harness, not adapter code. Two clips, neither committed:

- `audio/utterance.wav`, 16 kHz mono: what the simulator speaks. Real
  speech, because Silero is a speech detector and the integration
  lane's 300 Hz sine would never trip it.
- `audio/reply.wav`, 24 kHz mono: the canned reply. Long (target: over
  two minutes) and deliberately non-repeating, because the measurement
  cross-correlates it against itself and repetition would create
  correlation ambiguity where the whole point is an unambiguous lag.

Run once: `uv run python make_audio.py`.
"""

import subprocess
import sys
import wave
from pathlib import Path

HERE = Path(__file__).parent
AUDIO = HERE / "audio"

UTTERANCE = "Hello there, could you tell me something about the weather today?"

# Non-repeating on purpose: no sentence, clause or phrase recurs, so a
# window of this audio correlates with exactly one place in the clip.
REPLY = """
The morning began with a thin grey light over the harbour, and the
cranes stood still against it like patient herons waiting for the tide.
Down at the fish market a woman argued cheerfully with a supplier about
the price of langoustines, while her son counted crates and lost his
place twice. Further inland, three streets of nineteenth century
apartments were slowly waking up: shutters folding back, a radio tuned
to a station that plays nothing but brass bands before eight in the
morning, and somebody practising scales on a clarinet with more
enthusiasm than accuracy. A delivery van reversed into a bollard that
had been there since nineteen seventy four, and the driver swore at it
as though the bollard had moved. In the bakery on the corner the ovens
had been running since four, and the smell of burnt sugar reached as
far as the tram stop, where a student was reading a borrowed textbook
about volcanic soils and understanding perhaps a third of it. The tram
itself arrived late, as it does on Thursdays, because a signal near the
university has needed replacing since spring and nobody has found the
budget. Two engineers discussed this on board, one of them insisting
the fault was in the relay and the other convinced it was corrosion in
a junction box nobody had opened in a decade. Neither was entirely
right. Meanwhile a cat that belongs to no one in particular crossed the
rails with the unhurried confidence of an animal that has never been in
danger, and settled under a bench to watch pigeons it had no intention
of chasing. By ten the clouds had thinned enough to throw a pale stripe
of sun across the square, and the chairs outside the cafe filled with
people pretending it was warmer than it was. An old man explained to
his granddaughter why the fountain has four spouts and only three of
them work, a story involving a mayor, a lawsuit, and a plumber who
emigrated to Argentina. She listened to about half and then asked
whether they could get ice cream instead. They could, and did, and the
rest of the morning passed without incident, unless you count the
seagull that stole a croissant from a table near the newsstand and
dropped it, uneaten, into the fountain, where it drifted around the
working spouts for the better part of an hour before somebody fished it
out with a rolled up newspaper and put it in the bin, muttering about
birds, and weather, and the general state of things.
"""


def say(text: str, rate: int, sample_rate: int, out: Path) -> None:
    aiff = out.with_suffix(".aiff")
    subprocess.run(
        ["say", "-v", "Samantha", "-r", str(rate), "-o", str(aiff), text],
        check=True,
    )
    subprocess.run(
        ["afconvert", "-f", "WAVE", "-d", f"LEI16@{sample_rate}", "-c", "1",
         str(aiff), str(out)],
        check=True,
    )
    aiff.unlink()
    with wave.open(str(out)) as w:
        secs = w.getnframes() / w.getframerate()
    print(f"{out.name}: {secs:.1f} s at {sample_rate} Hz")
    return secs


def main() -> None:
    AUDIO.mkdir(exist_ok=True)
    say(UTTERANCE, 175, 16000, AUDIO / "utterance.wav")
    secs = say(" ".join(REPLY.split()), 165, 24000, AUDIO / "reply.wav")
    if secs < 100:
        print("warning: reply is short for a 100-window run", file=sys.stderr)


if __name__ == "__main__":
    main()
