"""One turn's record, assembled while the reply that produces it runs.

The sibling of [`turntaking.py`](turntaking.py), and the two are told
apart by what they are about: this module records what a turn
contained, `turntaking.py` decides who is speaking.

The pipeline holds the whole of a turn in hand exactly once, at the end
of the reply: the transcript and its language fields, what each agent
said, every call the model issued and what became of it, and the numbers
no event carries on its own (the summed token counts, the ASR elapsed,
the reply's first audio). Correlating that back out of the events later
would mean re-deriving it from JSON on every query, which is fragile
against exactly the field evolution the store exists to survive. So it
is accumulated here as it happens and handed over once.

Two things live here rather than in `pipeline.py`. The accumulator, so
that a reply path already busy with audio does not also carry a dozen
running totals; and the tool classifier, because "where did this name
come from" is asked twice (by the record now, by the narrowed
`tool_call` event later) and one answer is the point.

Nothing here talks to a store: it builds `conversations.records` values,
which is a leaf module both sides import and neither owns.
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass, field, replace

from vinga_server.conversations.records import ToolInvocation, TurnLeg, TurnRecord
from vinga_server.tools import names

# Where a tool name comes from, as a closed set. The same four tokens
# the `tool_invocations.source` column is constrained to, spelled here
# so that classifying a call does not make the runtime import the
# storage layer; the schema and this tuple are asserted equal in the
# tests, which is what keeps one from drifting off the other.
BUILTIN = "builtin"
DEVICE = "device"
MCP = "mcp"
UNKNOWN = "unknown"
TOOL_SOURCES = (BUILTIN, DEVICE, MCP, UNKNOWN)


def tool_source(
    name: str, device_tools: Collection[str], owner: str | None
) -> tuple[str, str | None]:
    """Where a call's name comes from, and which MCP entry owns it.

    The routing rules the dispatch applies, hoisted to one function and
    consulted before anything runs, so that every call the model issues
    is classified: the ones the dispatch never reaches as such (a
    malformed call, a name nobody publishes) and the handover it never
    sees at all get the same treatment as the rest.

    Names, not outcomes. A builtin whose feature is switched off (asking
    to `remember` where no memory is configured) is still a builtin
    asked for: the source says which namespace the model reached into,
    and whether the call then ran is what the result and the duration
    say. Builtins are checked first, which is also what makes the
    namespace's own precedence visible in one place.
    """
    if name in names.BUILTIN_TOOL_NAMES:
        return BUILTIN, None
    if name in device_tools:
        return DEVICE, None
    if owner is not None:
        return MCP, owner
    return UNKNOWN, None


@dataclass
class TurnUnderway:
    """The turn being assembled, one reply long.

    Mutable and deliberately dumb: every field is written by the reply
    path at the moment it learns the value, and `record` is the one read.
    A reply that was cancelled or failed hands over what this holds when
    its `finally` runs, exactly as `replied` reports what was spoken.

    `None` means not measured throughout, never zero. A round that never
    finished contributes nothing, so `rounds`, `llm_ms` and the token
    sums always describe the same set of rounds: the ones that produced
    an `llm_round` event.

    The two leading fields are the exception to "written by the reply
    path": they are the pair that owns the turn, taken where the turn
    begins and never written again. A handover changes who is speaking
    partway through, and the record is assembled in the reply's
    `finally`, so a turn that read the current pair there would be
    attributed to the thread it handed over to. Held here rather than
    passed to `record` because the reply path is where it would have to
    be remembered, and remembering it in half a dozen places is the
    mistake this removes. Neither of the two is optional, because the
    store materializes a thread row out of exactly them and refuses a
    turn it cannot attribute.
    """

    conversation: str
    agent: str
    at: float | None = None
    heard: str | None = None
    heard_duration_s: float | None = None
    language: str | None = None
    language_confidence: float | None = None
    asr_ms: int | None = None
    first_token_ms: int | None = None
    llm_ms: int | None = None
    tts_first_audio_ms: int | None = None
    rounds: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    _legs: list[TurnLeg] = field(default_factory=list)
    _said: list[str] = field(default_factory=list)
    _tools: list[ToolInvocation] = field(default_factory=list)
    # The current agent's share of the totals above, reset at each
    # handover, because a turn's totals blend agents that may run
    # different models and a blended number attributes nothing.
    _leg_input: int | None = None
    _leg_output: int | None = None
    _syntheses: int = 0

    def heard_utterance(
        self,
        at: float,
        transcript: str,
        duration_s: float,
        language: str | None,
        confidence: float | None,
    ) -> None:
        """The utterance this turn answers, stamped with the reading its
        `heard` event carries, which is what puts the turn on the same
        timeline as the events and the capture's audio."""
        self.at = at
        self.heard = transcript
        self.heard_duration_s = duration_s
        self.language = language
        self.language_confidence = confidence

    def round_done(
        self,
        elapsed_ms: int,
        first_token_ms: int | None,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> None:
        """One finished generation, counted where its event is emitted.

        `first_token_ms` is the reply's first, not the latest: later
        rounds resume a reply the user is already listening to, and what
        the number answers is how long the silence before it lasted."""
        self.rounds += 1
        self.llm_ms = (self.llm_ms or 0) + elapsed_ms
        if self.first_token_ms is None and first_token_ms is not None:
            self.first_token_ms = first_token_ms
        if input_tokens is not None:
            self.input_tokens = (self.input_tokens or 0) + input_tokens
            self._leg_input = (self._leg_input or 0) + input_tokens
        if output_tokens is not None:
            self.output_tokens = (self.output_tokens or 0) + output_tokens
            self._leg_output = (self._leg_output or 0) + output_tokens

    def leg_ended(self, agent: str | None, said: str | None) -> None:
        """One agent's share of the reply is over, because it handed the
        conversation on.

        Recorded whether or not that agent spoke: a leg that only asked
        for a handover still spent tokens, and a leg with no text is what
        says so."""
        self._legs.append(
            TurnLeg(
                agent=agent,
                text=said,
                input_tokens=self._leg_input,
                output_tokens=self._leg_output,
            )
        )
        if said is not None:
            self._said.append(said)
        self._leg_input = None
        self._leg_output = None

    def synthesis_started(self) -> int:
        """Which synthesis of this reply is beginning, counted from
        zero, so the first audio can be attributed to the first request
        rather than to whichever one answered first."""
        index = self._syntheses
        self._syntheses += 1
        return index

    def first_audio(self, index: int, elapsed_ms: int) -> None:
        """The reply's first synthesis produced its first bytes. Later
        syntheses report too and are ignored here: they were started
        against playback that was already happening, which is what the
        lookahead exists for, so their wait was never silence."""
        if index == 0:
            self.tts_first_audio_ms = elapsed_ms

    def reserve(self, invocation: ToolInvocation) -> int:
        """Keep this turn's place for a call the model issued, and answer
        where it is.

        Taken the moment the model's calls are known, before anything
        between there and the dispatch can end the reply: a synthesis
        that fails while the round's last sentence is spoken, or a
        barge-in landing mid-execution, would otherwise take every call
        of that round off the record with it. What the reservation says
        is already true at that point (which name, from which namespace,
        with which arguments); what happened to it is filled in later,
        and a call that never ran keeps the nulls it was reserved with,
        which is exactly what "issued but not executed" looks like.
        """
        self._tools.append(invocation)
        return len(self._tools) - 1

    def reserved(self, slot: int) -> ToolInvocation:
        """The classification kept at `slot`.

        For the `tool_call` event, which describes the same call the row
        does and must not classify it a second time: one answer read
        twice cannot disagree with itself, and between the reservation
        and the execution an MCP reload can move which entry owns a
        name."""
        return self._tools[slot]

    def executed(
        self, slot: int, result: str | None, is_error: bool, duration_ms: int | None
    ) -> None:
        """What became of the call reserved at `slot`. The entry is
        replaced rather than mutated because the record is frozen, and
        its position in the list is what makes it findable without
        matching on anything the model chose."""
        self._tools[slot] = replace(
            self._tools[slot],
            result=result,
            is_error=is_error,
            duration_ms=duration_ms,
        )

    def record(self, speaking: str | None, spoken: Sequence[str]) -> TurnRecord | None:
        """The finished record, or None where there is no turn to record.

        No transcript means no turn, which mirrors the events: an
        utterance nobody could transcribe produced no `heard` and
        produces no row.

        `spoken` is what the agent talking now said, the same sentences
        `replied` reports. Joined onto the legs before it, so `reply`
        holds the whole of what the user heard while `legs` keeps who
        said which part of it.

        `speaking` is that agent, and it is deliberately not what the
        record is attributed to: the last leg is whoever finished the
        reply, while the turn belongs to the pair it started with. The
        two are the same agent in every reply no handover split.
        """
        if self.at is None or self.heard is None:
            return None
        tail = " ".join(spoken) if spoken else None
        legs: tuple[TurnLeg, ...] = ()
        if self._legs:
            legs = (
                *self._legs,
                TurnLeg(
                    agent=speaking,
                    text=tail,
                    input_tokens=self._leg_input,
                    output_tokens=self._leg_output,
                ),
            )
        parts = [*self._said, *([tail] if tail is not None else [])]
        return TurnRecord(
            at=self.at,
            conversation=self.conversation,
            agent=self.agent,
            heard=self.heard,
            heard_duration_s=self.heard_duration_s,
            language=self.language,
            language_confidence=self.language_confidence,
            reply=" ".join(parts) if parts else None,
            legs=legs,
            asr_ms=self.asr_ms,
            first_token_ms=self.first_token_ms,
            llm_ms=self.llm_ms,
            tts_first_audio_ms=self.tts_first_audio_ms,
            rounds=self.rounds,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            tools=tuple(self._tools),
        )


__all__ = ["BUILTIN", "DEVICE", "MCP", "TOOL_SOURCES", "UNKNOWN", "TurnUnderway", "tool_source"]
