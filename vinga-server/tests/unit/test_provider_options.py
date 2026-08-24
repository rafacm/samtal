"""What a provider type declares it accepts, and the refusal it gives.

Every case here runs in the ordinary lane. That is deliberate and it is
the review round's finding: the default install carries no optional
extras, so a suite that reached the faster-whisper engine would be
skipped in CI, and the contract these cases hold is not the engine's. It
is the model's, the sanitizer's and the registry's dispatch, none of
which needs a model file to be true. The engine plumbing stays in
`test_providers_faster_whisper.py` under its own guard.

Three things are pinned here.

Coercion parity, because every converted type had a hand-written reader
before it had a model, and a rewrite that quietly widened what a
deployment may write would be a compatibility change nobody decided on.
The tables below are those readers' accepted-and-rejected sets, call by
call, one per type.

The refusal's shape, because naming the field is the whole point of the
issue and naming anything else is the thing this repository does not do:
the pointers are asserted exactly, and a credential planted in a key and
in a value is looked for through the whole exception chain.

And the dispatch, because a partially converted registry has two factory
shapes and the table decides which one a type gets.
"""

import json
import re
import subprocess
import sys
import textwrap

import pytest
from pydantic import BaseModel

from vinga_server.config.loader import ConfigError
from vinga_server.config.models import PROVIDER_STAGES, ProviderConfig
from vinga_server.config.provider_options import (
    NONBLANK_PATTERN,
    PCM_FORMAT_PATTERN,
    PROVIDER_TYPES,
    RESERVED_REQUEST_FIELDS,
    ElevenlabsOptions,
    FasterWhisperOptions,
    OpenaiCompatibleOptions,
    OptionsRefused,
    VadParameters,
    VoiceSettings,
    checked_options,
    options_model,
)
from vinga_server.providers import ProviderError, registry
from vinga_server.providers.mock import MockAsr

# Not a credential, and shaped so a substring check for it cannot match
# by accident. Planted as a value and as a key, because a key is as good
# a place to paste one and better at hiding there.
SECRET = "sk-live-2f9d7c41-never-a-real-credential"

ENTRY = "providers.asr.ears"

HEADLINE = f"invalid {ENTRY}:"

# The types under test, as the pair that addresses one, and what a
# fragment of each has to carry before any other option can be judged: a
# case about `model` must not fail because a required field was missing
# somewhere else.
WHISPER = ("asr", "faster_whisper")

ELEVENLABS = ("tts", "elevenlabs")

OPENAI = ("llm", "openai_compatible")

BASE: dict[tuple[str, str], dict[str, object]] = {
    WHISPER: {},
    ELEVENLABS: {"voice_id": "voice-1"},
    OPENAI: {"base_url": "http://localhost:11434/v1", "model": "qwen3:8b"},
}


def refuse(pair: tuple[str, str] = WHISPER, **options: object) -> OptionsRefused:
    """The refusal one set of options produces, or a failure saying it
    was accepted."""
    with pytest.raises(OptionsRefused) as caught:
        checked_options(HEADLINE, *pair, {**BASE[pair], **options})
    return caught.value


def accept(pair: tuple[str, str] = WHISPER, **options: object) -> BaseModel:
    entry = checked_options(HEADLINE, *pair, {**BASE[pair], **options})
    assert entry is not None
    return entry


def whisper(**options: object) -> FasterWhisperOptions:
    entry = accept(WHISPER, **options)
    assert isinstance(entry, FasterWhisperOptions)
    return entry


def elevenlabs(**options: object) -> ElevenlabsOptions:
    entry = accept(ELEVENLABS, **options)
    assert isinstance(entry, ElevenlabsOptions)
    return entry


def openai_compatible(**options: object) -> OpenaiCompatibleOptions:
    entry = accept(OPENAI, **options)
    assert isinstance(entry, OpenaiCompatibleOptions)
    return entry


# What each type accepts, and what it refuses
#
# One row per option per rule, read off the `OptionsReader` calls the
# builder made before the model existed: `string`, `required_string`,
# `integer`, `boolean`, `number`, `numbers` and `mapping`, each with its
# own idea of what a value is. The two that are easy to get wrong in a
# rewrite are the ones Python is relaxed about: a bool is an int, and
# "5" converts to one.
#
# One table per converted type, joined below with the pair that
# addresses it, so a type's rows read as its own reader did and the case
# that runs them is one case.
WHISPER_PARITY: list[tuple[str, object, bool]] = [
    # string options: a string and nothing else, plus the two spellings
    # of absence the reader's `or <default>` used to swallow.
    ("model", "medium", True),
    ("model", "", True),
    ("model", None, True),
    ("device", "", True),
    ("device", None, True),
    ("compute_type", "", True),
    ("language_detect", "", True),
    ("vad_parameters", None, True),
    ("model", 5, False),
    ("model", True, False),
    ("language", "sv", True),
    ("language", None, True),
    ("language", 5, False),
    ("device", "cuda", True),
    ("device", 1, False),
    ("compute_type", "float16", True),
    ("download_dir", "/models", True),
    ("download_dir", None, True),
    ("language_fallback", "en", True),
    # integer options: an int, never a bool, never a numeric string.
    ("beam_size", 5, True),
    ("beam_size", "5", False),
    ("beam_size", True, False),
    ("beam_size", 1.0, False),
    ("cpu_threads", 3, True),
    ("cpu_threads", "3", False),
    ("cpu_threads", False, False),
    # boolean options: true or false, and not the words for them.
    ("vad_filter", True, True),
    ("vad_filter", "yes", False),
    ("vad_filter", 1, False),
    ("condition_on_previous_text", False, True),
    ("condition_on_previous_text", 0, False),
    # a number: an int or a float, never a bool, never a string.
    ("language_confidence_floor", 0.5, True),
    ("language_confidence_floor", 1, True),
    ("language_confidence_floor", "0.5", False),
    ("language_confidence_floor", True, False),
    # the ladder: a number, or a non-empty list of them.
    ("temperature", 0.4, True),
    ("temperature", [0.0, 0.2], True),
    ("temperature", [], False),
    ("temperature", [0.0, True], False),
    ("temperature", ["a"], False),
    ("temperature", True, False),
    ("temperature", None, True),
    # the nested mapping, which has to be one.
    ("vad_parameters", {"min_silence_duration_ms": 500}, True),
    ("vad_parameters", [], False),
    ("vad_parameters", "500", False),
    # the closed set.
    ("language_detect", "once", True),
    ("language_detect", "sometimes", False),
    # and the key that is not an option of this type at all.
    ("beem_size", 5, False),
]

ELEVENLABS_PARITY: list[tuple[str, object, bool]] = [
    # the required string: present and with something in it, which is
    # what `required_string` demanded of it.
    ("voice_id", "voice-2", True),
    ("voice_id", "", False),
    ("voice_id", "   ", False),
    ("voice_id", None, False),
    ("voice_id", 5, False),
    # string options: a string and nothing else. An explicit null where
    # a default sits is the tightening this conversion makes, and here
    # it replaces an assertion error rather than a quiet default.
    ("model", "eleven_multilingual_v2", True),
    ("model", 5, False),
    ("model", None, False),
    # And the blank that is NOT a spelling of absence here, unlike the
    # first converted type: this reader had no `or <default>` after it,
    # so an empty string travelled to the API as an empty model id and
    # still does.
    ("model", "", True),
    ("language_code", "sv", True),
    ("language_code", None, True),
    ("language_code", 5, False),
    # the format, which has a shape as well as a type.
    ("output_format", "pcm_16000", True),
    ("output_format", "pcm_44100", True),
    ("output_format", "mp3_44100_128", False),
    ("output_format", "pcm_", False),
    ("output_format", 24000, False),
    ("output_format", None, False),
    # Refused by the format rule then, refused by it now, and not a
    # spelling of absence for the same reason `model` is not.
    ("output_format", "", False),
    # a number: an int or a float, never a bool, never a string. Null
    # was refused by the reader too, since `number` measured whatever it
    # popped rather than falling back on it.
    ("timeout_s", 15, True),
    ("timeout_s", 12.5, True),
    ("timeout_s", "15", False),
    ("timeout_s", True, False),
    ("timeout_s", None, False),
    # the nested mapping, which has to be one.
    ("voice_settings", {}, True),
    ("voice_settings", {"stability": 0.5, "use_speaker_boost": True}, True),
    ("voice_settings", [], False),
    ("voice_settings", "0.5", False),
    # the one spelling of absence this type's reader swallowed: the same
    # `mapping()` call `vad_parameters` went through answered {} for a
    # key that was not there.
    ("voice_settings", None, True),
    # And the widening that came with sharing one notion of blank:
    # `mapping()` refused an empty string here, and it is a spelling of
    # absence now, exactly as it is under `vad_parameters`.
    ("voice_settings", "", True),
    # and its five keys, each with the rule the hand check gave it. A
    # null under one of them was skipped by that check and travelled, so
    # it still does.
    ("voice_settings", {"stability": 1}, True),
    ("voice_settings", {"stability": None}, True),
    ("voice_settings", {"stability": "high"}, False),
    ("voice_settings", {"stability": True}, False),
    ("voice_settings", {"similarity_boost": 0.75}, True),
    ("voice_settings", {"style": 0.2}, True),
    ("voice_settings", {"speed": 1.1}, True),
    ("voice_settings", {"speed": "fast"}, False),
    ("voice_settings", {"use_speaker_boost": False}, True),
    ("voice_settings", {"use_speaker_boost": 1}, False),
    ("voice_settings", {"use_speaker_boost": "yes"}, False),
    # a key the section does not have, which is the refusal
    # `read_voice_settings` existed for.
    ("voice_settings", {"stabilty": 0.5}, False),
    # and the key that is not an option of this type at all.
    ("speaker", "lessac", False),
]

OPENAI_PARITY: list[tuple[str, object, bool]] = [
    # the two required strings, which is what `required_string` made
    # them: present, a string, and with something in them.
    ("base_url", "https://api.vendor.example/v1", True),
    ("base_url", "", False),
    ("base_url", "   ", False),
    ("base_url", None, False),
    ("base_url", 5, False),
    ("model", "qwen3:14b", True),
    ("model", "", False),
    ("model", None, False),
    ("model", 5, False),
    # the integer, which `integer(...)` took as an int and nothing else:
    # never a bool, never a float, never the digits written as a string,
    # and never a null, since it measured whatever it popped rather than
    # falling back on it.
    ("max_tokens", 256, True),
    ("max_tokens", "256", False),
    ("max_tokens", 256.0, False),
    ("max_tokens", True, False),
    ("max_tokens", None, False),
    # And the row that inverts, which is the whole of this type. Every
    # key the builder did not read ended its build with `finish()`
    # naming it; this is the one type whose reason for existing is that
    # the server on the other end takes parameters this repository has
    # never heard of, so they are kept now and forwarded.
    ("top_p", 0.9, True),
    ("keep_alive", "30m", True),
    ("options", {"num_ctx": 8192}, True),
    # Except a name the request already carries. That is not an option
    # for the server, it is a rewrite of what is being asked.
    ("messages", [], False),
    ("stream", False, False),
    ("tools", [], False),
    ("tool_choice", "none", False),
    ("stream_options", {"include_usage": True}, False),
]

PARITY: list[tuple[tuple[str, str], str, object, bool]] = [
    *((WHISPER, *row) for row in WHISPER_PARITY),
    *((ELEVENLABS, *row) for row in ELEVENLABS_PARITY),
    *((OPENAI, *row) for row in OPENAI_PARITY),
]

PARITY_IDS = [
    f"{pair[1]}.{name}={value!r}-{'ok' if good else 'no'}"
    for pair, name, value, good in PARITY
]


@pytest.mark.parametrize(("pair", "name", "value", "accepted"), PARITY, ids=PARITY_IDS)
def test_the_model_takes_what_the_reader_took(
    pair: tuple[str, str], name: str, value: object, accepted: bool
) -> None:
    if accepted:
        accept(pair, **{name: value})
        return
    refuse(pair, **{name: value})


# The spellings of absence the reader swallowed, and what they read as
#
# Four options ended the ladder with `or <default>` and `vad_parameters`
# was read through a call that answered {} for a missing key, so an
# empty string and an explicit null were both ways of writing nothing.
# A deployment that wrote one boots today, so it boots after this: the
# parity rows above say they are accepted, and these say what they mean.

BLANK_DEFAULTED = [
    ("model", "small"),
    ("device", "cpu"),
    ("compute_type", "int8"),
    ("language_detect", "every_utterance"),
]


@pytest.mark.parametrize(("name", "expected"), BLANK_DEFAULTED, ids=[n for n, _ in BLANK_DEFAULTED])
@pytest.mark.parametrize("written", ["", None], ids=["empty", "null"])
def test_a_blank_option_reads_as_the_default(
    name: str, expected: object, written: object
) -> None:
    options = whisper(**{name: written})

    assert getattr(options, name) == expected
    # And as UNWRITTEN, not as the default written out, which is what
    # keeps one statement of each default and what `exclude_unset`
    # depends on elsewhere.
    assert name not in options.model_fields_set


def test_a_null_vad_section_reads_as_no_section() -> None:
    """The fifth spelling, whose consequence is not a value but a key
    the engine never sees: `mapping()` answered `{}` for a missing key
    and the builder passed `vad_parameters` only when it was truthy."""
    options = whisper(vad_parameters=None)

    assert options.vad_parameters.model_dump(exclude_unset=True) == {}
    assert "vad_parameters" not in options.model_fields_set


def test_a_null_voice_settings_section_reads_as_no_section() -> None:
    """The same spelling under the second converted type, and the only
    one it has: `voice_settings` went through the same `mapping()` call,
    and the builder put the key in the request body only when the
    mapping was truthy, so a null section and a missing one produced the
    same request.

    The empty string is the one widening: `mapping()` refused it and
    this takes it as unwritten, which is what sharing one notion of
    blank with `vad_parameters` costs. Deliberate, cheap and pinned
    here, since a mapping written as `voice_settings:` with nothing
    after it is a null in YAML rather than an empty string, so the
    spelling this widens is one nobody writes.

    Its neighbours in this type deliberately have no such case. `model`
    and `output_format` were read without the `or <default>` that made a
    blank mean nothing elsewhere, so an empty string there is a value
    and the parity rows say so.
    """
    for written in (None, ""):
        options = elevenlabs(voice_settings=written)

        assert options.voice_settings.model_dump(exclude_unset=True) == {}
        assert "voice_settings" not in options.model_fields_set
    assert elevenlabs(model="").model == ""


def test_a_scalar_temperature_becomes_a_ladder_of_one() -> None:
    """The one coercion the reader performed, kept: the engine takes a
    sequence and an operator writes a number."""
    assert whisper(temperature=0.4).temperature == [0.4]
    assert whisper(temperature=[0.0, 0.2]).temperature == [0.0, 0.2]
    assert whisper().temperature is None


def test_the_defaults_are_the_ones_the_builder_had() -> None:
    """The values a fragment that sets nothing gets. They are read by the
    builder rather than by the engine, so a change here changes what
    every existing deployment is running."""
    options = whisper()

    assert options.model == "small"
    assert options.language is None
    assert options.device == "cpu"
    assert options.compute_type == "int8"
    assert options.beam_size == 1
    assert options.download_dir is None
    assert options.cpu_threads == 0
    assert options.vad_filter is False
    assert options.condition_on_previous_text is True
    assert options.temperature is None
    assert options.language_detect == "every_utterance"
    assert options.language_fallback is None
    assert options.language_confidence_floor == 0.6


def test_only_what_the_fragment_set_reaches_the_engines_vad() -> None:
    """The nested model crosses a boundary, and what crosses it is what
    was written: an explicit null travels, an injected default does not,
    and a section nobody wrote is nothing rather than a mapping of
    Nones."""
    assert whisper().vad_parameters.model_dump(exclude_unset=True) == {}
    assert whisper(vad_parameters={}).vad_parameters.model_dump(exclude_unset=True) == {}
    assert whisper(
        vad_parameters={"min_silence_duration_ms": 500}
    ).vad_parameters.model_dump(exclude_unset=True) == {"min_silence_duration_ms": 500}
    assert whisper(
        vad_parameters={"min_silence_duration_ms": None}
    ).vad_parameters.model_dump(exclude_unset=True) == {"min_silence_duration_ms": None}


def test_the_engines_own_vad_keys_still_travel() -> None:
    """The hatch this one nested model keeps open. faster-whisper's VAD
    takes more keys than the example documents, they have always been
    forwarded unread, and a deployment that wrote one must still boot."""
    options = whisper(vad_parameters={"speech_pad_ms": 30, "threshold": 0.4})

    assert options.vad_parameters.model_dump(exclude_unset=True) == {
        "speech_pad_ms": 30,
        "threshold": 0.4,
    }
    assert VadParameters.model_config["extra"] == "allow"


def test_an_unknown_detection_mode_names_the_modes() -> None:
    """The subject the builder's own check had, through the model: the
    refusal is only useful if it says what the choices are."""
    sentence = str(refuse(language_detect="sometimes"))

    assert "language_detect" in sentence
    assert "every_utterance" in sentence
    assert "once" in sentence


# What the elevenlabs type is, beside its parity
#
# The two rules its builder used to hold by hand, and the nested section
# that was the last hand-rolled options ladder in the provider package.


def test_the_elevenlabs_defaults_are_the_ones_the_builder_had() -> None:
    """What a fragment that sets nothing but a voice gets. Read by the
    builder rather than by the API, so a change here changes what every
    existing deployment is sending."""
    options = elevenlabs()

    assert options.model == "eleven_flash_v2_5"
    assert options.output_format == "pcm_24000"
    assert options.sample_rate == 24000
    assert options.language_code is None
    assert options.timeout_s == 30.0
    assert options.voice_settings.model_dump(exclude_unset=True) == {}


def test_the_rate_is_read_off_the_format_the_validator_admitted() -> None:
    """`parse_sample_rate` did both jobs; the field's validator does the
    refusing now and this does the reading, which is why it can be a
    property with no failure of its own."""
    assert elevenlabs(output_format="pcm_16000").sample_rate == 16000
    assert elevenlabs(output_format="pcm_44100").sample_rate == 44100


def test_a_format_this_stage_cannot_stream_is_refused_by_its_rule() -> None:
    """The subject the builder's own check had. The rule is named and the
    format is not, which is the one thing that changed: an output format
    is a value, and a value refused for its shape is where a paste
    lands."""
    refusal = refuse(ELEVENLABS, output_format="mp3_44100_128")
    (problem,) = refusal.problems

    assert problem.path == "/output_format"
    assert "pcm_<rate>" in str(refusal)
    assert "mp3_44100_128" not in str(refusal)


def test_only_what_the_fragment_set_reaches_the_request_body() -> None:
    """The nested model crosses a boundary, and what crosses it is what
    was written: an explicit null travels, an injected default does not,
    and a section nobody wrote is nothing rather than five nulls the API
    would have to interpret."""
    assert elevenlabs(voice_settings={}).voice_settings.model_dump(exclude_unset=True) == {}
    assert elevenlabs(
        voice_settings={"stability": 0.4, "speed": 1.1}
    ).voice_settings.model_dump(exclude_unset=True) == {"stability": 0.4, "speed": 1.1}
    assert elevenlabs(
        voice_settings={"style": None}
    ).voice_settings.model_dump(exclude_unset=True) == {"style": None}


def test_an_unknown_voice_setting_is_refused() -> None:
    """`read_voice_settings`'s own subject, kept through the model: a
    typo the API would ignore is a knob that never took effect.

    The pointer is the section rather than the key, which is
    `safe_location`'s rule meeting a closed door: the repository declared
    `voice_settings`, so it may name it, and it did not declare what was
    written inside, so it may not repeat that.
    """
    refusal = refuse(ELEVENLABS, voice_settings={"stabilty": 0.5})
    (problem,) = refusal.problems

    assert problem.path == "/voice_settings"
    assert problem.message == "an unrecognized key is not permitted"
    assert "stabilty" not in str(refusal)


def test_a_declared_voice_setting_is_addressed_by_its_own_path() -> None:
    """And the other half of that rule: a name this repository chose is
    printed, at the path a fragment writes it at."""
    (problem,) = refuse(ELEVENLABS, voice_settings={"stability": "high"}).problems

    assert problem.path == "/voice_settings/stability"
    assert problem.message == "must be a number"


def test_the_voice_settings_door_is_shut_and_the_vad_one_is_not() -> None:
    """The two nested models differ in exactly one setting, and it is the
    difference between a vendor's fixed five and an engine's open
    tuning. Asserted together so that changing either is a deliberate
    act."""
    assert VoiceSettings.model_config["extra"] == "forbid"
    assert VadParameters.model_config["extra"] == "allow"


# What the openai_compatible type is, beside its parity
#
# The escape hatch. Its model is the one that declares three options and
# keeps everything else, so what is pinned here is both halves of that:
# that an undeclared key survives validation, and that it cannot be one
# of the names the request already carries. What happens to it after
# validation is not this file's business; the forwarding is pinned in
# `test_providers_llm.py`, against the wire.


def test_the_openai_compatible_defaults_are_the_ones_the_builder_had() -> None:
    """What a fragment that names an endpoint and a model gets. Read by
    the builder rather than by the endpoint, so a change here changes
    what every existing deployment is sending."""
    options = openai_compatible()

    assert options.base_url == "http://localhost:11434/v1"
    assert options.model == "qwen3:8b"
    assert options.max_tokens == 1024
    assert options.model_extra == {}


def test_an_omitted_name_is_refused_by_the_name_it_omitted() -> None:
    """The one shape the parity table cannot express.

    Its harness merges every case over a fragment carrying both required
    options, so that a case about `max_tokens` is not failed by a
    missing `base_url`; the price is that omitting one of them is
    unwritable as a row. Written here instead, against the sanitizer
    directly, for both of them rather than the one an older case
    happened to cover: `required_string` refused an absent key as
    loudly as a blank one, and a required field is the whole of what it
    left behind.
    """
    for name, present in (("model", "base_url"), ("base_url", "model")):
        with pytest.raises(OptionsRefused) as caught:
            checked_options(HEADLINE, *OPENAI, {present: "written"})

        (problem,) = caught.value.problems
        assert problem.path == f"/{name}"
        assert name in str(caught.value)


def test_the_only_open_door_among_the_declared_types() -> None:
    """The three declared types, and the one difference between them.

    Asserted together so that opening one of the other two, or closing
    this one, is a deliberate act rather than a setting that drifted. A
    type whose door is shut refuses what it does not declare; this one
    is the door itself.
    """
    assert OpenaiCompatibleOptions.model_config["extra"] == "allow"
    assert FasterWhisperOptions.model_config["extra"] == "forbid"
    assert ElevenlabsOptions.model_config["extra"] == "forbid"


def test_a_key_this_type_does_not_declare_is_kept_rather_than_refused() -> None:
    """The hatch, at the model. A server speaking this dialect takes
    parameters no other one does, and a model enumerating one vendor's
    would present them as the contract of all of them, so what is not
    declared is carried instead of judged.

    Carried as `model_extra`, which is the same place `ProviderConfig`
    keeps a provider's options, so the inline-secret rule that walks
    those keeps walking these.
    """
    options = openai_compatible(top_p=0.9, keep_alive="30m")

    assert options.model_extra == {"top_p": 0.9, "keep_alive": "30m"}


def test_a_passthrough_key_may_not_be_a_field_of_the_request() -> None:
    """The one thing the hatch may not do. `messages` is the
    conversation, and a key by that name is not an option for a server,
    it is a rewrite of what is being asked.

    The name is printed, which is the exception to the rule that a
    caller's key is not: these are the repository's own words, published
    in the schema on the model, so saying which one was written tells an
    operator what to remove without repeating anything they invented.
    The pointer addresses it for the same reason.
    """
    refusal = refuse(OPENAI, messages=[{"role": "user", "content": SECRET}])
    (problem,) = refusal.problems

    assert problem.path == "/messages"
    assert '"messages"' in str(refusal)
    assert SECRET not in str(refusal)


def test_every_reserved_name_is_refused_and_each_one_is_named() -> None:
    """The set as a set, so a field added to the request without being
    added here fails rather than becoming quietly overridable.

    Three of the seven are declared fields of the model, which is a
    stronger refusal rather than a gap: a passthrough key cannot take a
    name the model already has. They are in the set anyway because what
    the set states is which fields the request composes, and a reader of
    it should not have to know which of them the model happens to
    declare.
    """
    declared = set(OpenaiCompatibleOptions.model_fields)
    for name in sorted(RESERVED_REQUEST_FIELDS - declared):
        refusal = refuse(OPENAI, **{name: "whatever"})
        (problem,) = refusal.problems

        assert problem.path == f"/{name}"
        assert f'"{name}"' in str(refusal)

    assert declared & RESERVED_REQUEST_FIELDS == {"model", "max_tokens"}


def test_two_reserved_names_are_refused_as_two_problems() -> None:
    """A form marks both fields rather than the first one, which is what
    building the problems inside the validator is for."""
    refusal = refuse(OPENAI, messages=[], tools=[])

    assert [problem.path for problem in refusal.problems] == ["/messages", "/tools"]


def test_a_reserved_collision_carries_the_name_and_nothing_of_the_value() -> None:
    """The one refusal in this file that prints a key, held to printing
    only that.

    A reserved name is one of seven words this repository chose, so it
    travels; what was written under it is the caller's, and this is the
    third converted type's plant for that. The whole chain is read, for
    the reason the two above it are: a `ValidationError` holds the
    rejected mapping in its `errors()`.
    """
    refusal = refuse(OPENAI, messages=SECRET, stream=SECRET)

    rendered = "\n".join(
        [
            str(refusal),
            repr(refusal),
            repr(refusal.__cause__),
            repr(refusal.__context__),
            *(f"{problem.path} {problem.message}" for problem in refusal.problems),
        ]
    )

    assert SECRET not in rendered
    assert "messages" in rendered and "stream" in rendered
    assert refusal.__cause__ is None
    assert refusal.__context__ is None


def test_this_type_has_no_blank_spelling_of_an_absent_option() -> None:
    """The parity decision this type asked for, and its answer is none.

    The first two converted types each carried a list of options whose
    absence had a second spelling, read off the readers they replaced:
    an option ending in `or <default>` swallowed the empty string as
    well as the null, and a section read through `mapping()` answered an
    empty mapping for a key that was not there. This reader had neither.
    `base_url` and `model` were `required_string`, which refused a blank
    as loudly as an absent key, and `max_tokens` was `integer(key,
    default)`, which measured whatever it popped. So a blank is a value
    here, and refused as one, and there is no list for this type.
    """
    for written in ("", None):
        refuse(OPENAI, base_url=written)
        refuse(OPENAI, model=written)
    refuse(OPENAI, max_tokens=None)


def test_the_url_rule_is_not_this_models_and_the_credential_rule_is_not_either() -> None:
    """Where the work is divided, stated as what this model does NOT do.

    `required_string` is the whole of what the model replaces, so a
    `base_url` that is a non-blank string passes here and is refused
    where the endpoint question is asked: `parse_base_url`, at build,
    for all three stages that speak this dialect. A URL carrying a
    credential passes here too and is refused by the write path's own
    rule, which reads values at every depth rather than fields.

    Pinned as acceptance rather than argued in a comment, because the
    thing that would break silently is this model quietly gaining a rule
    that already has a home.
    """
    assert openai_compatible(base_url="not-a-url").base_url == "not-a-url"
    assert openai_compatible(base_url="https://user:pw@host/v1").base_url.endswith("@host/v1")


def test_this_types_two_required_names_publish_the_blank_rule_too() -> None:
    """The third type's half of the second one's review finding.

    Both required fields here are `Nonblank`, so the pattern that
    finding published reaches this component as well, and the two lines
    it adds to the committed document are read by this rather than only
    diffed. Written per type rather than as a walk over every declared
    model, which is how the case above it is written: what a walk would
    say is that annotations produce their own schema, and what these say
    is that this type's contract is stated in full where a client reads
    it.
    """
    from vinga_server.config import docgen

    published = [
        json.loads(docgen.schema("provider", "llm", "openai_compatible")),
        json.loads(docgen.openapi())["components"]["schemas"]["LlmOpenaiCompatibleOptions"],
    ]

    for schema in published:
        for name in ("base_url", "model"):
            assert schema["properties"][name]["pattern"] == NONBLANK_PATTERN
            assert schema["properties"][name]["description"]
        # And that both of them are required, which is the half the
        # parity table cannot state: its harness supplies them so that a
        # case about one option is not failed by the absence of another.
        assert schema["required"] == ["base_url", "model"]
        # And the door the type exists for, in the vocabulary a document
        # has for one.
        assert schema["additionalProperties"] is True

    for value, allowed in (("qwen3:8b", True), (" ", False), ("", False)):
        assert bool(re.search(NONBLANK_PATTERN, value)) is allowed
        if allowed:
            openai_compatible(model=value)
        else:
            refuse(OPENAI, model=value)


def test_the_published_schema_says_which_names_a_key_may_not_take() -> None:
    """The door, said in the vocabulary a document has for a door.

    `additionalProperties: true` says a key may be written and stops
    there, so a client generating from it would write `messages` and
    meet a refusal the document never mentioned. `propertyNames` is what
    constrains what a key may be CALLED, which is the shape this rule
    has, and the prose beside it says the same thing to a person.

    The set is exact rather than the whole of `RESERVED_REQUEST_FIELDS`,
    and the difference is the finding: two of those seven are options
    this model declares, so a key by one of those names is the option
    rather than a passthrough. Publishing them as forbidden would tell a
    client that `model` may not be written, which is the opposite of
    true, since it is required.
    """
    from vinga_server.config import docgen

    refused_names = list(OpenaiCompatibleOptions.refused_passthrough())
    assert refused_names == ["messages", "stream", "stream_options", "tool_choice", "tools"]

    published = [
        json.loads(docgen.schema("provider", "llm", "openai_compatible")),
        json.loads(docgen.openapi())["components"]["schemas"]["LlmOpenaiCompatibleOptions"],
    ]

    for schema in published:
        assert schema["propertyNames"] == {"not": {"enum": refused_names}}
        # The same set in words, because a reference is read by people
        # and a keyword is read by generators.
        for name in refused_names:
            assert name in schema["description"]

    # And the document is not describing a different rule from the one
    # that runs: every name it excludes is refused, and the two it does
    # not exclude are the options themselves.
    for name in refused_names:
        assert refuse(OPENAI, **{name: "whatever"}).problems[0].path == f"/{name}"
    assert openai_compatible(model="qwen3:8b", max_tokens=64).max_tokens == 64


# Where a refusal points
#
# Options are flat siblings of `type` in the fragment that is actually
# submitted, so these are the pointers into that fragment. The empty one
# is not a gap: it addresses the fragment itself, which is the nearest
# place this repository can name when the key is one the caller invented.


def test_a_declared_option_is_addressed_by_name() -> None:
    (problem,) = refuse(beam_size="5").problems

    assert problem.path == "/beam_size"


def test_a_declared_nested_field_is_addressed_through_its_parent() -> None:
    (problem,) = refuse(vad_parameters={"min_silence_duration_ms": "soon"}).problems

    assert problem.path == "/vad_parameters/min_silence_duration_ms"


def test_an_undeclared_option_is_addressed_at_the_fragment() -> None:
    """The rule `safe_location` has always kept, unchanged by the type
    knowing its own fields: a key the caller wrote is not printed, so the
    pointer falls back to the nearest declared parent, which for a
    top-level option is the fragment."""
    (problem,) = refuse(beem_size=5).problems

    assert problem.path == ""
    assert problem.message == "an unrecognized key is not permitted"


def test_a_refusal_names_no_key_and_no_value_anywhere_in_its_chain() -> None:
    """The plant, in both places one lands: a secret-shaped key and a
    secret-shaped value, looked for in the sentence, the repr, the cause,
    the context and the structured problems.

    The chain is the half a rendering test cannot see. A pydantic
    `ValidationError` holds the whole rejected mapping in its
    `errors()`, so a refusal that carried one as its `__cause__` or its
    `__context__` would put every option an operator wrote wherever that
    exception is printed.
    """
    refusal = refuse(**{SECRET: SECRET, "model": {"nested": SECRET}})

    rendered = "\n".join(
        [
            str(refusal),
            repr(refusal),
            repr(refusal.__cause__),
            repr(refusal.__context__),
            *(f"{problem.path} {problem.message}" for problem in refusal.problems),
        ]
    )

    assert SECRET not in rendered
    assert refusal.__cause__ is None
    assert refusal.__context__ is None


def test_a_refusal_about_a_nested_section_carries_nothing_of_it_either() -> None:
    """The same plant one level down, where the second converted type
    put a closed door.

    A nested model refuses twice over, once for a key it does not have
    and once for a value of the wrong shape, and pydantic reports both
    with the rejected input attached. Both are looked for here, in the
    same five places.
    """
    refusal = refuse(
        ELEVENLABS, voice_settings={SECRET: SECRET, "stability": SECRET}
    )

    rendered = "\n".join(
        [
            str(refusal),
            repr(refusal),
            repr(refusal.__cause__),
            repr(refusal.__context__),
            *(f"{problem.path} {problem.message}" for problem in refusal.problems),
        ]
    )

    assert SECRET not in rendered
    assert refusal.__cause__ is None
    assert refusal.__context__ is None


def test_the_registry_is_the_table_resolved() -> None:
    """The derivation, asserted as one: every entry of the table has a
    registration, every registration comes from an entry, and the model
    on it is the one the entry declares.

    This replaces a test that used to bridge two mappings. That test was
    the receipt for a topology written twice; there is one table now, so
    what is left to check is that nothing was dropped or invented while
    resolving it.
    """
    resolved = registry._registrations()

    assert {stage: sorted(types) for stage, types in resolved.items()} == {
        stage: sorted(types) for stage, types in PROVIDER_TYPES.items()
    }
    for stage, types in PROVIDER_TYPES.items():
        for type_name, declared in types.items():
            assert resolved[stage][type_name].options is declared.options


def test_the_table_covers_the_pipeline_and_nothing_else() -> None:
    """The stages a provider can be written under are the pipeline's,
    and a table keyed by a fifth would declare types no agent can
    reference."""
    assert set(PROVIDER_TYPES) == set(PROVIDER_STAGES)


def test_a_factory_is_named_rather_than_imported() -> None:
    """What keeps the table light, stated as a property of its entries
    rather than as a comment: every factory is two strings and an
    attribute lookup deferred to construction time."""
    for types in PROVIDER_TYPES.values():
        for declared in types.values():
            assert isinstance(declared.module, str)
            assert declared.path.startswith("vinga_server.providers.")


def test_a_type_with_no_model_is_not_checked_at_all() -> None:
    """Which is what makes converting the types one at a time a
    non-event: every other type behaves exactly as it did."""
    assert options_model("llm", "anthropic") is None
    assert checked_options(HEADLINE, "llm", "anthropic", {"anything": object()}) is None
    assert options_model("asr", "faster_whisper") is FasterWhisperOptions


def test_an_unknown_stage_or_type_declares_nothing() -> None:
    assert options_model("asr", "ghost") is None
    assert options_model("ghost", "faster_whisper") is None


# The dispatch
#
# A typed type's factory takes the validated instance and a model-less
# one does not, and which it is comes off the table rather than off a
# flag. Exercised with a fake factory so the case runs where no engine
# is installed, which is every lane CI has.


def _table(**entries: registry.Registration) -> dict[str, dict[str, registry.Registration]]:
    return {"asr": dict(entries)}


def test_a_typed_type_is_handed_the_validated_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handed: list[object] = []

    def factory(label: str, config: ProviderConfig, options: BaseModel) -> object:
        handed.append(options)
        return MockAsr(text="hello")

    monkeypatch.setattr(
        registry,
        "_registrations",
        lambda: _table(typed=registry.Registration(factory, FasterWhisperOptions)),
    )
    registry.construct_provider(
        "asr", "ears", ProviderConfig.model_validate({"type": "typed", "beam_size": 5})
    )

    (options,) = handed
    assert isinstance(options, FasterWhisperOptions)
    assert options.beam_size == 5
    # And the defaults came with it, which is the difference between
    # handing over a model and handing over the mapping that was written.
    assert options.compute_type == "int8"


def test_a_model_less_type_is_called_as_it_always_was(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handed: list[tuple[object, ...]] = []

    def factory(label: str, config: ProviderConfig) -> object:
        handed.append((label, config))
        return MockAsr(text="hello")

    monkeypatch.setattr(
        registry, "_registrations", lambda: _table(plain=registry.Registration(factory))
    )
    registry.construct_provider(
        "asr", "ears", ProviderConfig.model_validate({"type": "plain", "whatever": 5})
    )

    ((label, config),) = handed
    assert label == "providers.asr.ears"
    assert config.options == {"whatever": 5}


def test_a_refused_option_is_never_built_at_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ordering `finish()` used to hold, kept by the table: a bad
    option costs no construction, because after one there would be an
    object to let go of again (#191)."""
    built: list[object] = []

    def factory(label: str, config: ProviderConfig, options: BaseModel) -> object:
        built.append(options)
        return MockAsr(text="hello")

    monkeypatch.setattr(
        registry,
        "_registrations",
        lambda: _table(typed=registry.Registration(factory, FasterWhisperOptions)),
    )
    with pytest.raises(ProviderError) as caught:
        registry.construct_provider(
            "asr", "ears", ProviderConfig.model_validate({"type": "typed", "beam_size": "5"})
        )

    assert built == []
    assert "providers.asr.ears" in str(caught.value)
    assert "beam_size" in str(caught.value)
    assert caught.value.__cause__ is None


def test_the_build_refusal_carries_nothing_of_what_it_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The build path's half of the no-leak rule. The write path's is in
    `test_config_store.py`, and both wrap the same sanitizer."""
    monkeypatch.setattr(
        registry,
        "_registrations",
        lambda: _table(
            typed=registry.Registration(lambda *args: MockAsr(text=""), FasterWhisperOptions)
        ),
    )
    with pytest.raises(ProviderError) as caught:
        registry.construct_provider(
            "asr",
            "ears",
            ProviderConfig.model_validate({"type": "typed", "model": {"key": SECRET}}),
        )

    assert SECRET not in f"{caught.value!r}{caught.value}{caught.value.__cause__!r}"


# The selector
#
# One type's options as JSON Schema, which is what a client reads before
# writing the fragment that carries them.


def test_the_schema_selector_takes_a_stage_and_a_type() -> None:
    from vinga_server.config import docgen

    schema = json.loads(docgen.schema("provider", "asr", "faster_whisper"))

    assert schema["properties"]["beam_size"]["description"]
    # The nested model comes with it, which is what makes a leaf name
    # reachable rather than only its parent's.
    assert schema["$defs"]["VadParameters"]["properties"]["min_silence_duration_ms"]


def test_the_published_schema_says_what_the_validator_accepts() -> None:
    """The one field whose validator and annotation disagree on purpose,
    held to saying so in every surface that publishes a schema.

    A `BeforeValidator` widens what comes in and the annotation
    describes what comes out, so `temperature` takes a bare number and
    refuses an empty list while its declared type is a list. A schema
    generated from the annotation alone would tell a client to write the
    one form the ladder refuses and to omit the one an operator most
    often writes, which is a contradiction a document cannot carry.

    Asserted on the rendered surfaces rather than on the annotation,
    because what a client reads is the document.
    """
    from vinga_server.config import docgen

    published = [
        json.loads(docgen.schema("provider", "asr", "faster_whisper")),
        json.loads(docgen.openapi())["components"]["schemas"]["AsrFasterWhisperOptions"],
    ]

    for schema in published:
        branches = schema["properties"]["temperature"]["anyOf"]
        assert {"type": "number"} in branches
        assert {"type": "null"} in branches
        (array,) = [branch for branch in branches if branch.get("type") == "array"]
        assert array["minItems"] == 1
        assert array["items"] == {"type": "number"}

    # And the schema is not describing a different rule from the one that
    # runs: each branch it publishes is accepted, and the empty array it
    # excludes is refused.
    accept(temperature=0.4)
    accept(temperature=[0.0, 0.2])
    accept(temperature=None)
    refuse(temperature=[])


def test_the_published_schema_carries_the_two_string_rules_as_patterns() -> None:
    """The same claim for the second converted type, whose two rules are
    `AfterValidator`s rather than a widened input.

    A validator is code and a schema is not, so an annotation alone
    describes both of these as any string at all: a client generating
    from the document would write `voice_id: ""` or `output_format:
    mp3_44100_128` and meet a refusal the document never warned about.
    The pattern is what the document has to say a string rule with, so
    it says it, on the selector's output and on the component the
    provider PUT points at.
    """
    from vinga_server.config import docgen

    published = [
        json.loads(docgen.schema("provider", "tts", "elevenlabs")),
        json.loads(docgen.openapi())["components"]["schemas"]["TtsElevenlabsOptions"],
    ]

    for schema in published:
        assert schema["properties"]["voice_id"]["pattern"] == NONBLANK_PATTERN
        assert schema["properties"]["output_format"]["pattern"] == PCM_FORMAT_PATTERN
        # And the field descriptions survived the schema being stated
        # rather than derived, which is the thing `WithJsonSchema` is
        # easiest to lose.
        assert schema["properties"]["voice_id"]["description"]
        assert schema["properties"]["output_format"]["description"]

    # And the patterns are not describing a different rule from the one
    # that runs: what each matches is accepted and what it excludes is
    # refused, checked with the published patterns themselves so a drift
    # in either direction fails here.
    for value, allowed in (("voice-1", True), (" ", False), ("", False)):
        assert bool(re.search(NONBLANK_PATTERN, value)) is allowed
        if allowed:
            elevenlabs(voice_id=value)
        else:
            refuse(ELEVENLABS, voice_id=value)
    for value, allowed in (("pcm_24000", True), ("mp3_44100_128", False), ("pcm_", False)):
        assert bool(re.search(PCM_FORMAT_PATTERN, value)) is allowed
        if allowed:
            elevenlabs(output_format=value)
        else:
            refuse(ELEVENLABS, output_format=value)


def test_the_selector_needs_the_stage_because_a_type_name_is_not_unique() -> None:
    """`openai` is an ASR type and a TTS type and `mock` is all four, so
    a selector keyed on the type alone would address whichever the
    registry happened to answer with."""
    from vinga_server.config import docgen

    stages = {stage for stage, types in registry._registrations().items() if "openai" in types}
    assert stages == {"asr", "tts"}

    with pytest.raises(ConfigError) as caught:
        docgen.schema("provider", "tts", "openai")
    assert "asr faster_whisper" in str(caught.value)

    with pytest.raises(ConfigError):
        docgen.schema("provider", "", "faster_whisper")
    with pytest.raises(ConfigError):
        docgen.schema("agent", "asr", "faster_whisper")


# What a write costs to import
#
# `config/store.py` validates a written provider through
# `config/provider_options.py`. What must not happen is an engine
# loading, or the provider package with it: writing a faster-whisper
# entry on a server that has never transcribed anything must import
# neither faster-whisper nor numpy nor any client library, and the whole
# provider layer is what `test_onboarding_import_weight.py` holds the
# CLI to staying clear of. In a subprocess, because this suite's own
# `sys.modules` has the whole server in it already.
_WRITE = """
import json, sys, tempfile
from pathlib import Path

from vinga_server.config.store import ConfigStore
from vinga_server.db import open_database

with tempfile.TemporaryDirectory() as directory:
    engine = open_database(Path(directory) / "db")
    try:
        store = ConfigStore(engine)
        store.set_provider("asr", "whisper", {"type": "faster_whisper", "model": "small"})
        stored = store.load().domain.providers.asr["whisper"].type
    finally:
        engine.dispose()

print(json.dumps({
    "stored": stored,
    "engines": sorted(
        name
        for name in ("faster_whisper", "numpy", "torch", "ctranslate2", "openai", "anthropic")
        if name in sys.modules
    ),
    "providers": sorted(
        name for name in sys.modules if name.startswith("vinga_server.providers")
    ),
}))
"""


def test_writing_a_provider_loads_no_engine() -> None:
    finished = subprocess.run(
        [sys.executable, "-B", "-c", textwrap.dedent(_WRITE)],
        capture_output=True,
        text=True,
        check=True,
    )
    written = json.loads(finished.stdout)

    assert written["stored"] == "faster_whisper"
    assert written["engines"] == []
    # And not the provider layer either, which is what the models living
    # on the config side buys: a write validates against the type's own
    # contract without the package that builds the type being loaded at
    # all.
    assert written["providers"] == []
