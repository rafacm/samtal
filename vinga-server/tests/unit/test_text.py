"""Sentence assembly from streamed text deltas."""

from vinga_server.text import MAX_HELD_FOR_A_BRACE, SentenceSplitter


def split_all(deltas: list[str]) -> list[str]:
    splitter = SentenceSplitter()
    sentences: list[str] = []
    for delta in deltas:
        sentences.extend(splitter.push(delta))
    tail = splitter.flush()
    if tail is not None:
        sentences.append(tail)
    return sentences


def test_sentences_come_out_as_their_trailing_whitespace_arrives() -> None:
    splitter = SentenceSplitter()
    assert splitter.push("Hello there.") == []  # no whitespace after the dot yet
    assert splitter.push(" How are") == ["Hello there."]
    assert splitter.push(" you? ") == ["How are you?"]
    assert splitter.flush() is None


def test_the_flush_returns_an_unterminated_remainder() -> None:
    assert split_all(["No punctuation at all"]) == ["No punctuation at all"]


def test_decimals_do_not_split_and_ellipses_split_only_after_the_last_dot() -> None:
    assert split_all(["Pi is 3.14159 rounded. Hmm... quite."]) == [
        "Pi is 3.14159 rounded.",
        "Hmm...",
        "quite.",
    ]


def test_newlines_end_a_sentence_on_their_own() -> None:
    assert split_all(["First line\nsecond line."]) == ["First line", "second line."]


def test_tiny_fragments_ride_along_with_the_next_sentence() -> None:
    assert split_all(["No. I would rather not. "]) == ["No. I would rather not."]


def test_a_tiny_fragment_at_the_end_still_comes_out() -> None:
    assert split_all(["Ok. "]) == ["Ok."]


def test_cjk_endings_split_too() -> None:
    assert split_all(["今天天气很好。 明天见了朋友。 "]) == ["今天天气很好。", "明天见了朋友。"]


def test_word_by_word_streaming_matches_whole_string_splitting() -> None:
    text = "One two three. Four five? Six."
    words = [w if i == 0 else " " + w for i, w in enumerate(text.split(" "))]
    assert split_all(words) == split_all([text])


# --- what an open brace holds together -------------------------------
#
# A model that writes a tool call into its own prose writes a compact
# object with prose inside it, and the punctuation rule cut that into
# fragments that were each ordinary text. The guard downstream reads
# whole sentences, so the cut was the leak (#385). These are the rule
# that keeps the object in one piece and the three bounds on it.


LEAKED_CALL = '{"name":"remember","arguments":{"text":"Milk. And eggs"}}'


def test_punctuation_inside_an_open_brace_does_not_cut() -> None:
    assert split_all([LEAKED_CALL]) == [LEAKED_CALL]


def test_the_object_and_what_follows_it_are_told_apart() -> None:
    """The suppression ends where the brace does, so an answer after a
    leaked call is still its own sentence."""
    assert split_all([f"{LEAKED_CALL} Done. Anything else?"]) == [
        f"{LEAKED_CALL} Done.",
        "Anything else?",
    ]


def test_a_brace_inside_a_string_is_not_a_span() -> None:
    """Counted with the quoting walk, so a closing brace inside a value
    does not end the span early and let the next `.` cut."""
    assert split_all(['{"a":"}. not the end"} Fine.']) == ['{"a":"}. not the end"} Fine.']


def test_a_newline_still_ends_a_sentence_inside_a_brace() -> None:
    """The outer bound, and the reason a pretty-printed call is still
    the stated bound of the guard rather than something this closes."""
    assert split_all(['{"name":"remember",\n "arguments": {}}']) == [
        '{"name":"remember",',
        '"arguments": {}}',
    ]


def test_an_unclosed_brace_holds_a_bounded_amount_and_then_lets_go() -> None:
    """Prose that opens a brace and never closes it costs a delay
    rather than the rest of the reply."""
    filler = "x" * MAX_HELD_FOR_A_BRACE
    assert split_all([f"An open {{ and {filler}. Then this. And this."]) == [
        f"An open {{ and {filler}.",
        "Then this.",
        "And this.",
    ]


def test_an_unclosed_brace_is_released_by_the_flush() -> None:
    """The other bound, and the one that always holds: the end of the
    stream says everything."""
    assert split_all(['Half a call {"name": "rem']) == ['Half a call {"name": "rem']


def test_word_by_word_streaming_matches_whole_string_splitting_through_a_call() -> None:
    """The incremental property, over the text the new rule is about.
    Every decision is local to the character it is made at, so a cut
    refused while a brace is open is refused the same way however the
    text arrived."""
    text = f'Sure. {LEAKED_CALL} Done.'
    words = [w if i == 0 else " " + w for i, w in enumerate(text.split(" "))]
    assert split_all(words) == split_all([text])
    assert split_all(list(text)) == split_all([text])
