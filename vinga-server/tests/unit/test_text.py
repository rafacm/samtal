"""Sentence assembly from streamed text deltas."""

from vinga_server.text import SentenceSplitter


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
