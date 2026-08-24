"""The provider types: what builds each one, and what it accepts.

A provider entry's options are everything it carries beyond `type`,
`api_key_env` and `egress`, and until this module existed they were
read key by key inside the type's own builder: a ladder of
`OptionsReader` calls that named a rule per key, refused the leftovers,
and was invisible to every surface that documents the configuration.
A type that declares a model here states the same contract in one
place, and the write path, the builder, the JSON Schema and the
refusals all read it from that place (#88).

Three things live here and nothing else. The model CLASSES; the
`PROVIDER_TYPES` table, which is the one statement of which types exist,
where each one is built and which of them declares a model; and the
SANITIZER, the one function that turns a stage, a type and a mapping
into either a validated instance or a value-free refusal, so the write
path, the read-back and the build path consult one implementation rather
than three.

One topology, and everything derives from it: `providers/registry.py`
builds its registrations by resolving this table's factory names, the
documentation renders its per-type sections out of the same entries, and
the refusal that lists a stage's known types counts the same keys. There
is no second mapping to hold against this one and no test bridging two.

It weighs pydantic and `config.models` and nothing else: no provider
package, no engine, no database driver, no cryptography. A factory is
named rather than imported, which is what lets the topology live at an
address the documentation can afford. Three committed pins depend on
that, and each of them is a promise this repository makes about where
its code can run.

- The reference and the JSON Schema render from the models alone, in a
  child interpreter with no database and no key
  (`test_config_docgen.py`). They document these options now, so this
  module is on that path.
- Rendering the OpenAPI document loads no part of a conversation
  (`test_onboarding_import_weight.py`). It carries these models as
  components now.
- `vinga-server config` loads no engine. It prints these fields in the
  epilog of `set provider`, built when the command table is.

A home inside the provider package could satisfy none of the three: the
package's `__init__` re-exports the whole provider layer, so importing
one pydantic module from it pulls in the engine base classes, the
provider world and, through the secret store, cryptography. Hence this
address. What lives on the provider side is what genuinely runs there:
resolving a name into a callable, putting an entry's secrets in force,
constructing, and the reading of a validated instance inside a builder.

Field descriptions carry the example fragment's factual sentence, which
is what makes the schema and the reference say what the fragment says.
The narrative prose (the measurements, the tuning ladders, the reasons
behind a default) stays in the fragment under `examples/`, per the
standing documentation decision.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationError,
)

from vinga_server.config.models import PROVIDER_STAGES, FieldProblem, validation_problems

# What a coercion rule says when it refuses.
#
# The reader these models replace accepted a narrow set and said so in a
# fixed sentence: a bool is not a number however happily Python treats
# it as one, "5" is not an integer, and an empty list is not a ladder.
# Ordinary pydantic fields are wider than that in lax mode, so the rules
# are stated as validators and the sentences are these, which name the
# rule and never the value: an option that fails one of them is as good
# a place for a pasted credential as any other.
NUMBER_RULE = "must be a number"

NUMBERS_RULE = "must be a number or a non-empty list of numbers"


def _as_number(value: object) -> object:
    """A number the way the reader took one: an int or a float, never a
    bool, normalized to a float so a builder gets one type."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(NUMBER_RULE)
    return float(value)


def _as_numbers(value: object) -> object:
    """A non-empty list of numbers, with a single number taken as a list
    of one, which is what the reader's `numbers()` accepted."""
    if value is None:
        return None
    if not isinstance(value, bool) and isinstance(value, (int, float)):
        return [float(value)]
    if (
        isinstance(value, list)
        and value
        and all(not isinstance(item, bool) and isinstance(item, (int, float)) for item in value)
    ):
        return [float(item) for item in value]
    raise ValueError(NUMBERS_RULE)


# The three shapes a declared option comes in, as annotations rather
# than as a rule repeated per field. The strict spellings are pydantic's
# own and their messages name the type they wanted; the two numeric ones
# are ours, because lax pydantic would take a bool for a number and a
# numeric string for an integer and the reader never did.
Number = Annotated[float, BeforeValidator(_as_number)]

Numbers = Annotated[list[float] | None, BeforeValidator(_as_numbers)]


class VadParameters(BaseModel):
    """The engine's own voice-activity tuning, forwarded as written.

    The one model in this file whose door stays open, and it is open on
    purpose. `vad_parameters` has always been handed to
    `WhisperModel.transcribe` unread, the example documents one key of
    it, and faster-whisper's VAD takes several more that a deployment
    may already have written. Closing the hatch on the evidence of one
    documented key would make a running deployment's valid setting
    unreadable on upgrade, so the model declares what vinga vouches for
    and says here that everything else still travels.

    What travels is what was written: the mapping is dumped with
    `exclude_unset=True` on the way to the engine, so an operator's
    explicit values (nulls included) reach it and an injected default
    does not.
    """

    model_config = ConfigDict(extra="allow")

    min_silence_duration_ms: StrictInt | None = Field(
        default=None,
        description=(
            "How much silence ends a speech segment, in milliseconds. Any other key "
            "written here is passed to the engine's VAD unread, which is what this "
            "section is for."
        ),
    )


class FasterWhisperOptions(BaseModel):
    """The options the `faster_whisper` ASR type accepts.

    The decode options mirror `WhisperModel.transcribe` arguments of the
    same name and keep the engine's defaults when unset, with one
    exception: `beam_size` defaults to greedy decoding, because beam
    search buys little accuracy on short spoken commands and costs a
    multiple of the CPU time (#19).
    """

    model_config = ConfigDict(extra="forbid")

    model: StrictStr = Field(
        default="small",
        description=(
            "Whisper model size (tiny, base, small, medium, large-v3, or a Hugging "
            "Face model id); weights download at server startup."
        ),
    )
    language: StrictStr | None = Field(
        default=None,
        description=(
            "Language hint (ISO 639-1, such as sv or en); omit to auto-detect per "
            "utterance. Detection costs a constant encoder pass per utterance, "
            "several seconds of it on a small CPU."
        ),
    )
    device: StrictStr = Field(
        default="cpu",
        description=(
            "Where the engine runs inference, in faster-whisper's own vocabulary "
            "(cpu, cuda, auto)."
        ),
    )
    compute_type: StrictStr = Field(
        default="int8",
        description=(
            "The quantization the weights are loaded with, in faster-whisper's own "
            "vocabulary (int8, int8_float16, float16, float32)."
        ),
    )
    beam_size: StrictInt = Field(
        default=1,
        description=(
            "Greedy decoding by default: beam search costs a multiple of the CPU "
            "time and buys little accuracy on short spoken commands."
        ),
    )
    download_dir: StrictStr | None = Field(
        default=None,
        description=(
            "Where the model weights are cached; unset leaves the engine its own "
            "cache location."
        ),
    )
    cpu_threads: StrictInt = Field(
        default=0,
        description=(
            "Threads for CPU inference. The engine sizes its pool from the host's "
            "core count and ignores container CPU quotas, so inside a limit set this "
            "to the quota (0 keeps the engine default)."
        ),
    )
    vad_filter: StrictBool = Field(
        default=False,
        description=(
            "Strip non-speech inside the ASR call before decoding. Cuts both latency "
            "and hallucinations on silence-padded utterances; recommended on."
        ),
    )
    vad_parameters: VadParameters = Field(
        default_factory=VadParameters,
        description=(
            "Tuning for the engine's own voice-activity filter, forwarded to it as "
            "written. Only what the fragment sets is sent."
        ),
    )
    condition_on_previous_text: StrictBool = Field(
        default=True,
        description=(
            "Feeding each window's text into the next is the documented cause of "
            "repetition loops; false is the standard mitigation."
        ),
    )
    temperature: Numbers = Field(
        default=None,
        description=(
            "Fallback ladder for failed decodes, as one number or a non-empty list "
            "of them. The engine's six-step default can retry one bad utterance six "
            "times over; a short ladder bounds worst-case latency, which a voice UI "
            "feels."
        ),
    )
    language_detect: Literal["every_utterance", "once"] = Field(
        default="every_utterance",
        description=(
            "Detection scope. every_utterance detects fresh on each turn; once "
            "detects until a confident answer arrives and then reuses that language "
            "for the rest of the session, so later turns skip the detection pass."
        ),
    )
    language_fallback: StrictStr | None = Field(
        default=None,
        description=(
            "The language to decode in when a detection falls below the confidence "
            "floor; unset means the low-confidence detection is used as it is."
        ),
    )
    language_confidence_floor: Number = Field(
        default=0.6,
        description=(
            "Below this detection confidence, distrust the guess: use "
            "language_fallback instead when one is set, and never lock a session to "
            "it. Misdetections cluster at low confidence, and a wrong language costs "
            "extra decode time on top of being wrong."
        ),
    )


class OptionsRefused(Exception):
    """One entry's options, refused, in the two renderings a refusal
    needs and in neither of the two a leak needs.

    Its `str` is the sentence, built from the field names the model
    declared and the rules they broke; `problems` is the same walk as
    JSON Pointers, which is what a form acts on. What it does not carry
    is the `ValidationError` it was built from, and that is the whole
    reason this type exists rather than the pydantic one travelling: an
    error's `errors()` hold the rejected input, and a rejected option is
    exactly where a pasted credential lands.

    Each caller wraps it in the refusal of its own surface: a
    `ConfigError` at the write, a `StorageError` on read-back, a
    `ProviderError` at build time. None of them chains this one, for the
    same reason.
    """

    def __init__(self, sentence: str, problems: tuple[FieldProblem, ...]) -> None:
        self.problems = problems
        super().__init__(sentence)


# The provider types, and the one place they are written down
#
# Two facts per type, and they are the two every surface in this
# repository asks about one: where the thing that builds it lives, and
# what it accepts. Both live here, in one table, because the alternative
# has now been tried twice and failed the same way each time. A factory
# table in the provider package with an options mapping beside it is two
# stage-and-type topologies held together by a test, which is the design
# guide's pending bug; putting the models here and leaving the factories
# there was the same shape with a shorter bridge. So there is one table,
# and both halves of a type are one entry of it.
#
# What makes that possible without dragging an engine behind it is that
# a factory is NAMED here rather than imported: `module` and `attribute`
# are strings, resolved by `providers/registry.py` at the moment a
# provider is constructed, which is the same laziness the per-type
# factory functions used to spell out one closure at a time. Importing
# this module therefore costs pydantic and `config.models`, exactly as
# it did when it held models alone, and the three pins that depend on
# that are undisturbed.
_IMPLEMENTATIONS = "vinga_server.providers"


@dataclass(frozen=True)
class ProviderType:
    """One provider type: what builds it, and what it accepts.

    `module` is a name under `vinga_server.providers` and `attribute` is
    the callable in it, so nothing is imported until something is built.
    `extra` is the optional dependency whose absence has to be explained
    rather than raised as an ImportError from the middle of a request,
    and None for a type the core install can always build. `options` is
    the model the type declares, and None for one that declares none,
    which is the ordinary case while the conversion runs type by type
    (#88).
    """

    module: str
    attribute: str = "build"
    options: type[BaseModel] | None = None
    extra: str | None = None

    @property
    def path(self) -> str:
        """The importable name of the module holding the factory."""
        return f"{_IMPLEMENTATIONS}.{self.module}"


# Keyed by stage and then by type because that is how a provider is
# addressed: `openai` is an ASR type and a TTS type, `mock` is all four,
# and a type name on its own addresses nothing in particular.
PROVIDER_TYPES: dict[str, dict[str, ProviderType]] = {
    "llm": {
        "mock": ProviderType("mock", "build_llm"),
        "anthropic": ProviderType("anthropic_llm"),
        "openai_compatible": ProviderType("openai_llm"),
    },
    "asr": {
        "mock": ProviderType("mock", "build_asr"),
        "faster_whisper": ProviderType(
            "faster_whisper", options=FasterWhisperOptions, extra="faster-whisper"
        ),
        # No extra to guard, for the reason the openai TTS type has none:
        # the openai client is a core dependency and transcription is a
        # method on it.
        "openai": ProviderType("openai_asr"),
    },
    "tts": {
        "mock": ProviderType("mock", "build_tts"),
        # No extra to guard: the provider speaks the API over httpx,
        # which the core install already carries.
        "elevenlabs": ProviderType("elevenlabs_tts"),
        # No extra to guard: the openai client is a core dependency,
        # carried for the openai_compatible LLM type, and speech is a
        # method on it.
        "openai": ProviderType("openai_tts"),
        "piper": ProviderType("piper_tts", extra="piper"),
    },
    "vad": {
        "mock": ProviderType("mock", "build_vad"),
        "silero": ProviderType("silero"),
    },
}


def provider_type(stage: str, type_name: str) -> ProviderType | None:
    """What the table says about one stage's type, or None for a stage
    or a type it does not have."""
    return PROVIDER_TYPES.get(stage, {}).get(type_name)


def options_model(stage: str, type_name: str) -> type[BaseModel] | None:
    """The options model one stage's type declares, or None for a type
    that declares none.

    A type with no model is the ordinary case while the conversion runs
    type by type: the caller falls back to what it did before.
    """
    declared = provider_type(stage, type_name)
    return declared.options if declared is not None else None


def declared_options() -> tuple[tuple[str, str, type[BaseModel]], ...]:
    """Every declared model as stage, type and model, grouped by stage
    in the pipeline's own order and by type name under it.

    The enumeration every rendering reads: the reference's per-type
    tables, the OpenAPI components, the `set provider` epilog and the
    sentence that says which types are declared. Ordered here rather
    than at each of them, because four renderings sorting for themselves
    is four chances for a committed document to move on a dictionary's
    insertion order.
    """
    return tuple(
        (stage, type_name, declared.options)
        for stage in PROVIDER_STAGES
        for type_name, declared in sorted(PROVIDER_TYPES.get(stage, {}).items())
        if declared.options is not None
    )


def component_name(stage: str, type_name: str) -> str:
    """What one type's options are called where a document names its
    shapes: `AsrFasterWhisperOptions`.

    Built from the pair rather than from the class, so the name a reader
    meets carries the two things that address the model and cannot
    collide across stages the way a class name could.
    """
    words = (stage, *type_name.split("_"))
    return "".join(word[:1].upper() + word[1:] for word in words) + "Options"


def checked_options(
    headline: str, stage: str, type_name: str, options: Mapping[str, object]
) -> BaseModel | None:
    """One entry's options as its type's own model, or None where the
    type declares none.

    The one gate. The write funnel, the read-back and the build path all
    call this, so what a stored entry may hold and what a written one
    may hold cannot come apart, and there is one place where the
    sentence an operator reads is composed.

    `headline` is the first line of that sentence, which is the calling
    surface's business: a write says `invalid providers.asr.ears:`, a
    read-back says the row cannot be read. Under it comes one indented
    line per problem, naming the field and the rule.

    Built inside the handler and raised outside it, the rule every
    refusal in this repository is built by: an exception raised inside
    an `except` arm keeps the one being handled as its `__context__`,
    and a `ValidationError`'s errors carry the whole rejected mapping.
    """
    model = options_model(stage, type_name)
    return None if model is None else validated(headline, model, options)


def validated(
    headline: str, model: type[BaseModel], options: Mapping[str, object]
) -> BaseModel:
    """One mapping through one model, refused in this repository's
    words.

    Taken as the model rather than looked up, for the caller that has
    already resolved it: the provider registry holds the model on the
    registration it is about to build with, and resolving it a second
    time from a stage and a type would be a second lookup that can
    answer differently from the first.
    """
    sentence: str | None = None
    problems: tuple[FieldProblem, ...] = ()
    entry: BaseModel | None = None
    try:
        entry = model.model_validate(dict(options))
    except ValidationError as exc:
        sentence, problems = validation_problems(headline, model, exc)
    if entry is None:
        raise OptionsRefused(str(sentence), problems)
    return entry


__all__ = [
    "PROVIDER_TYPES",
    "NUMBERS_RULE",
    "NUMBER_RULE",
    "FasterWhisperOptions",
    "OptionsRefused",
    "VadParameters",
    "checked_options",
    "ProviderType",
    "component_name",
    "declared_options",
    "options_model",
    "provider_type",
    "validated",
]
