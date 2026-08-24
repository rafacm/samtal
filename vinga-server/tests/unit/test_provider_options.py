"""What a provider type declares it accepts, and the refusal it gives.

Every case here runs in the ordinary lane. That is deliberate and it is
the review round's finding: the default install carries no optional
extras, so a suite that reached the faster-whisper engine would be
skipped in CI, and the contract these cases hold is not the engine's. It
is the model's, the sanitizer's and the registry's dispatch, none of
which needs a model file to be true. The engine plumbing stays in
`test_providers_faster_whisper.py` under its own guard.

Three things are pinned here.

Coercion parity, because the type had a hand-written reader before it
had a model, and a rewrite that quietly widened what a deployment may
write would be a compatibility change nobody decided on. The table below
is that reader's accepted-and-rejected set, call by call.

The refusal's shape, because naming the field is the whole point of the
issue and naming anything else is the thing this repository does not do:
the pointers are asserted exactly, and a credential planted in a key and
in a value is looked for through the whole exception chain.

And the dispatch, because a partially converted registry has two factory
shapes and the table decides which one a type gets.
"""

import json
import subprocess
import sys
import textwrap

import pytest
from pydantic import BaseModel

from vinga_server.config.loader import ConfigError
from vinga_server.config.models import ProviderConfig
from vinga_server.providers import ProviderError, registry
from vinga_server.providers.mock import MockAsr
from vinga_server.providers.options import (
    FasterWhisperOptions,
    OptionsRefused,
    VadParameters,
    checked_options,
    options_model,
)

# Not a credential, and shaped so a substring check for it cannot match
# by accident. Planted as a value and as a key, because a key is as good
# a place to paste one and better at hiding there.
SECRET = "sk-live-2f9d7c41-never-a-real-credential"

ENTRY = "providers.asr.ears"

HEADLINE = f"invalid {ENTRY}:"


def refuse(**options: object) -> OptionsRefused:
    """The refusal one set of options produces, or a failure saying it
    was accepted."""
    with pytest.raises(OptionsRefused) as caught:
        checked_options(HEADLINE, "asr", "faster_whisper", options)
    return caught.value


def accept(**options: object) -> FasterWhisperOptions:
    entry = checked_options(HEADLINE, "asr", "faster_whisper", options)
    assert isinstance(entry, FasterWhisperOptions)
    return entry


# What the type accepts, and what it refuses
#
# One row per option per rule, read off the `OptionsReader` calls the
# builder made before the model existed: `string`, `integer`, `boolean`,
# `number`, `numbers` and `mapping`, each with its own idea of what a
# value is. The two that are easy to get wrong in a rewrite are the ones
# Python is relaxed about: a bool is an int, and "5" converts to one.
PARITY: list[tuple[str, object, bool]] = [
    # string options: a string and nothing else.
    ("model", "medium", True),
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

PARITY_IDS = [f"{name}={value!r}-{'ok' if good else 'no'}" for name, value, good in PARITY]


@pytest.mark.parametrize(("name", "value", "accepted"), PARITY, ids=PARITY_IDS)
def test_the_model_takes_what_the_reader_took(
    name: str, value: object, accepted: bool
) -> None:
    if accepted:
        accept(**{name: value})
        return
    refuse(**{name: value})


def test_a_scalar_temperature_becomes_a_ladder_of_one() -> None:
    """The one coercion the reader performed, kept: the engine takes a
    sequence and an operator writes a number."""
    assert accept(temperature=0.4).temperature == [0.4]
    assert accept(temperature=[0.0, 0.2]).temperature == [0.0, 0.2]
    assert accept().temperature is None


def test_the_defaults_are_the_ones_the_builder_had() -> None:
    """The values a fragment that sets nothing gets. They are read by the
    builder rather than by the engine, so a change here changes what
    every existing deployment is running."""
    options = accept()

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
    assert accept().vad_parameters.model_dump(exclude_unset=True) == {}
    assert accept(vad_parameters={}).vad_parameters.model_dump(exclude_unset=True) == {}
    assert accept(
        vad_parameters={"min_silence_duration_ms": 500}
    ).vad_parameters.model_dump(exclude_unset=True) == {"min_silence_duration_ms": 500}
    assert accept(
        vad_parameters={"min_silence_duration_ms": None}
    ).vad_parameters.model_dump(exclude_unset=True) == {"min_silence_duration_ms": None}


def test_the_engines_own_vad_keys_still_travel() -> None:
    """The hatch this one nested model keeps open. faster-whisper's VAD
    takes more keys than the example documents, they have always been
    forwarded unread, and a deployment that wrote one must still boot."""
    options = accept(vad_parameters={"speech_pad_ms": 30, "threshold": 0.4})

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
# `providers/options.py`, and that module is inside a package whose
# `__init__` re-exports the whole provider layer. What must not happen is
# an engine loading: writing a faster-whisper entry on a server that has
# never transcribed anything must not import faster-whisper, numpy or
# any client library. In a subprocess, because this suite's own
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
    "implementations": sorted(
        name
        for name in sys.modules
        if name.startswith("vinga_server.providers.")
        and name not in ("vinga_server.providers.options", "vinga_server.providers.registry")
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
    # `base` and `world` ride in on the package's own `__init__`, which
    # is the cost this deferral does not remove; what it removes is the
    # implementation module of the type being written.
    assert "vinga_server.providers.faster_whisper" not in written["implementations"]
