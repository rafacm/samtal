"""Assembling streamed LLM text into speakable sentences.

The LLM stage yields text in whatever deltas the model streams; TTS
wants whole sentences. The splitter cuts at sentence-ending punctuation
once the following whitespace has arrived (so "3.14" and "..." never
split mid-token), treats a newline as an ending of its own, and holds
very short fragments to prepend to the next sentence rather than
speaking them alone.

One thing it declines to cut inside: an open brace. A model that writes
a tool call into its own prose writes something like
`{"name":"remember","arguments":{"text":"Milk. And eggs"}}`, and the
punctuation rule above cuts that at the `. ` inside the argument, into
two fragments that are each ordinary text and neither of which is a
call any more. The guard that refuses to speak a leaked call
(`runtime/speech.py`) reads whole sentences, so a cut there is the leak
(#385). While a brace is open the punctuation rule therefore stands
down, which keeps a compact object in one piece for whoever reads it
next.

That suppression is bounded three ways over, because this runs in front
of live speech and a sentence held here is a sentence not yet being
synthesized. A newline still cuts, whatever is open, which is what
leaves a pretty-printed call as the fragments `runtime/speech.py`
documents as its stated bound. `flush` releases everything at the end
of the stream. And `MAX_HELD_FOR_A_BRACE` below caps the span, so an
ordinary unmatched `{` in prose, or a quotation mark that opens a
string nothing closes, costs a bounded delay rather than the rest of
the reply.

Braces inside a JSON string do not count, which is what the quoting
walk is for: `{"a":"}"}` closes once, not at the `}` in the middle of
its value. Nothing here knows what a tool is; what it knows is that an
open brace is a span worth keeping together.
"""

SENTENCE_ENDINGS = ".!?…。！？"

# Below this many characters a piece rides along with the next sentence.
MIN_SENTENCE_CHARS = 4

# How far past an unclosed `{` the punctuation rule stays down.
#
# Long enough for the thing it is for: a compact tool call with a
# sentence or two of arguments in it runs to a few hundred characters,
# and one that does not fit was never going to be spoken as one
# sentence anyway. Short enough that the worst case is a delay rather
# than a silence: prose that opens a brace and never closes it holds at
# most this much text back from the voice, once, and every ending after
# it cuts normally.
MAX_HELD_FOR_A_BRACE = 512


class SentenceSplitter:
    """Feed text deltas with `push`, which returns each sentence the
    moment it completes; `flush` returns whatever remains at the end."""

    def __init__(self) -> None:
        self._buffer = ""
        self._held = ""

    def push(self, delta: str) -> list[str]:
        self._buffer += delta
        sentences: list[str] = []
        while (cut := self._find_cut()) is not None:
            piece = self._buffer[:cut].strip()
            self._buffer = self._buffer[cut:]
            if not piece:
                continue
            if self._held:
                piece = f"{self._held} {piece}"
                self._held = ""
            if len(piece) < MIN_SENTENCE_CHARS:
                self._held = piece
            else:
                sentences.append(piece)
        return sentences

    def flush(self) -> str | None:
        """The remainder once the stream has ended, sentence or not."""
        text = f"{self._held} {self._buffer}".strip()
        self._buffer = ""
        self._held = ""
        return text or None

    def _find_cut(self) -> int | None:
        """Where the buffer's first sentence ends, or None while it has
        not ended yet.

        Walked from the start of the buffer every time rather than
        resumed from a kept position, which is what makes the answer
        depend on the buffer's text and on nothing about how it
        arrived: a delta per word and the whole string at once give the
        same sentences, and the brace state below is recomputed rather
        than carried.

        Every decision is local to the character being looked at, for
        the same reason. Whether a `.` may cut depends on its own
        distance from the brace that is open, never on how much text
        has arrived since, so a cut that is refused now is refused
        again when more of the reply has landed.
        """
        depth = 0
        opened = 0
        quoted = False
        escaped = False
        for index, char in enumerate(self._buffer):
            if quoted:
                # Inside a JSON string, where a brace is a character in
                # a value rather than a span, and a backslash makes the
                # next quote one too.
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char == "{":
                if depth == 0:
                    opened = index
                depth += 1
            elif char == "}" and depth > 0:
                depth -= 1
            if char == "\n":
                # Always, whatever is open. It is the splitter's other
                # ending and it is the outer bound on how long an open
                # brace can hold anything.
                return index + 1
            if char in SENTENCE_ENDINGS:
                if depth > 0 and index - opened < MAX_HELD_FOR_A_BRACE:
                    continue
                if index + 1 < len(self._buffer) and self._buffer[index + 1].isspace():
                    return index + 1
        return None
