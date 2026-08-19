"""Assembling streamed LLM text into speakable sentences.

The LLM stage yields text in whatever deltas the model streams; TTS
wants whole sentences. The splitter cuts at sentence-ending punctuation
once the following whitespace has arrived (so "3.14" and "..." never
split mid-token), treats a newline as an ending of its own, and holds
very short fragments to prepend to the next sentence rather than
speaking them alone.
"""

SENTENCE_ENDINGS = ".!?…。！？"

# Below this many characters a piece rides along with the next sentence.
MIN_SENTENCE_CHARS = 4


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
        for index, char in enumerate(self._buffer):
            if char == "\n":
                return index + 1
            if char in SENTENCE_ENDINGS:
                if index + 1 < len(self._buffer) and self._buffer[index + 1].isspace():
                    return index + 1
        return None
