"""Constructing providers from their configuration entries.

Each stage maps type names to a `Registration`: how the type is built,
and the options model it declares, which is the one table every other
surface reads that question off. Heavyweight implementations
import their engine inside the factory, so the core install only pays
for what the configuration references, and a missing optional
dependency becomes an error naming the extra to install rather than an
ImportError from the middle of a request.

Construction only. What owns the objects, checks them and lets go of
them again is `providers/world.py`, which is the half that cannot run in
a worker thread: refusing a provider that already exists means closing
it (#191).
"""

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel

from vinga_server.config import ConfigError
from vinga_server.config.models import ProviderConfig
from vinga_server.config.secrets import (
    ProviderSecrets,
    SecretStore,
    provider_secrets_in_force,
)
from vinga_server.providers.base import (
    AsrProvider,
    LlmProvider,
    Provider,
    ProviderError,
    TtsProvider,
    VadProvider,
)
from vinga_server.providers.options import OptionsRefused, checked_options


class OptionsReader:
    """Typed access to a provider's options, with errors that name the
    configuration entry. Providers reject options they do not know, so
    a typo fails at startup instead of silently configuring nothing."""

    def __init__(self, label: str, config: ProviderConfig) -> None:
        self._label = label
        self._pending = config.options

    def string(self, key: str, default: str | None = None) -> str | None:
        value = self._pending.pop(key, default)
        if value is None or isinstance(value, str):
            return value
        raise ProviderError(f'{self._label}: option "{key}" must be a string')

    def required_string(self, key: str) -> str:
        value = self.string(key)
        if value is None or not value.strip():
            raise ProviderError(f'{self._label}: option "{key}" is required')
        return value

    def number(self, key: str, default: float) -> float:
        value = self._pending.pop(key, default)
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ProviderError(f'{self._label}: option "{key}" must be a number')
        return float(value)

    def optional_number(self, key: str) -> float | None:
        """A number that has no default, None when absent: the provider
        leaves the knob out of the request rather than guessing at the
        API's own default."""
        value = self._pending.pop(key, None)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ProviderError(f'{self._label}: option "{key}" must be a number')
        return float(value)

    def integer(self, key: str, default: int) -> int:
        value = self._pending.pop(key, default)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ProviderError(f'{self._label}: option "{key}" must be an integer')
        return value

    def boolean(self, key: str, default: bool) -> bool:
        value = self._pending.pop(key, default)
        if not isinstance(value, bool):
            raise ProviderError(f'{self._label}: option "{key}" must be true or false')
        return value

    def numbers(self, key: str) -> list[float] | None:
        """A non-empty list of numbers, with a single number taken as a
        list of one; None when absent."""
        value = self._pending.pop(key, None)
        if value is None:
            return None
        if not isinstance(value, bool) and isinstance(value, int | float):
            return [float(value)]
        if (
            isinstance(value, list)
            and value
            and all(
                not isinstance(item, bool) and isinstance(item, int | float)
                for item in value
            )
        ):
            return [float(item) for item in value]
        raise ProviderError(
            f'{self._label}: option "{key}" must be a number or a non-empty list of numbers'
        )

    def mapping(self, key: str) -> dict[str, object]:
        """A nested option written as a YAML mapping, empty when absent."""
        value = self._pending.pop(key, None)
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ProviderError(f'{self._label}: option "{key}" must be a mapping')
        return {str(name): item for name, item in value.items()}

    def finish(self) -> None:
        """Refuse whatever the provider never asked about.

        Called before the provider is constructed, in every factory, and
        that order is the contract rather than a habit: an unknown
        option refused after a model had loaded would be a refusal with
        an object to let go of, on a path whose whole promise is that it
        touched nothing (#191).
        """
        if self._pending:
            unknown = ", ".join(sorted(self._pending))
            raise ProviderError(f"{self._label}: unknown option(s): {unknown}")


Factory = Callable[..., object]


@dataclass(frozen=True)
class Registration:
    """What one stage's type name is: how it is built, and what it
    accepts.

    One table rather than two. The options model used to have nowhere to
    live but a second stage-and-type mapping beside this one, and two
    mappings that must agree about which types exist are one mapping
    with a bug pending; construction, write-time validation, read-back
    and the documentation all read this.

    The factory comes in two shapes, and which one is which is decided
    by `options` rather than by a flag: a type that declares a model is
    called `(label, config, options)` and reads attributes off a
    validated instance, and a type that declares none is called
    `(label, config)` and reads its own `OptionsReader` ladder, exactly
    as every type did before the conversion started. That is what lets
    the types convert one at a time (#88) without a partially converted
    registry meaning anything unusual.
    """

    factory: Factory
    options: type[BaseModel] | None = None


def _silero(label: str, config: ProviderConfig) -> object:
    from vinga_server.providers import silero

    return silero.build(label, config)


def _faster_whisper(label: str, config: ProviderConfig) -> object:
    try:
        from vinga_server.providers import faster_whisper
    except ImportError as exc:
        raise ProviderError(
            f'{label}: type "faster_whisper" needs the faster-whisper extra; '
            f"install it with: uv sync --extra faster-whisper"
        ) from exc
    return faster_whisper.build(label, config)


def _openai_asr(label: str, config: ProviderConfig) -> object:
    # No extra to guard, for the reason the openai TTS type has none:
    # the openai client is a core dependency and transcription is a
    # method on it.
    from vinga_server.providers import openai_asr

    return openai_asr.build(label, config)


def _anthropic(label: str, config: ProviderConfig) -> object:
    from vinga_server.providers import anthropic_llm

    return anthropic_llm.build(label, config)


def _openai_compatible(label: str, config: ProviderConfig) -> object:
    from vinga_server.providers import openai_llm

    return openai_llm.build(label, config)


def _elevenlabs(label: str, config: ProviderConfig) -> object:
    # No extra to guard: the provider speaks the API over httpx, which
    # the core install already carries.
    from vinga_server.providers import elevenlabs_tts

    return elevenlabs_tts.build(label, config)


def _openai_tts(label: str, config: ProviderConfig) -> object:
    # No extra to guard: the openai client is a core dependency, carried
    # for the openai_compatible LLM type, and speech is a method on it.
    from vinga_server.providers import openai_tts

    return openai_tts.build(label, config)


def _piper(label: str, config: ProviderConfig) -> object:
    try:
        from vinga_server.providers import piper_tts
    except ImportError as exc:
        raise ProviderError(
            f'{label}: type "piper" needs the piper extra; '
            f"install it with: uv sync --extra piper"
        ) from exc
    return piper_tts.build(label, config)


def _registrations() -> dict[str, dict[str, Registration]]:
    # Imported here rather than at module top because the implementation
    # modules import the OptionsReader above; the table itself is tiny.
    # `options` is imported at module scope instead, which is the whole
    # point of that module being pydantic and nothing else: reading this
    # table for what a type accepts costs no engine.
    from vinga_server.providers import mock

    return {
        "llm": {
            "mock": Registration(mock.build_llm),
            "anthropic": Registration(_anthropic),
            "openai_compatible": Registration(_openai_compatible),
        },
        "asr": {
            "mock": Registration(mock.build_asr),
            "faster_whisper": Registration(_faster_whisper),
            "openai": Registration(_openai_asr),
        },
        "tts": {
            "mock": Registration(mock.build_tts),
            "elevenlabs": Registration(_elevenlabs),
            "openai": Registration(_openai_tts),
            "piper": Registration(_piper),
        },
        "vad": {"mock": Registration(mock.build_vad), "silero": Registration(_silero)},
    }


def registration(stage: str, type_name: str) -> Registration | None:
    """What the table says about one stage's type, or None for a stage
    or a type it does not have.

    The read every other surface goes through. `construct_provider`
    below asks it what to build with; `providers/options.py` asks it
    which model to validate against, which is how the write path, the
    read-back and the build path share one answer.
    """
    return _registrations().get(stage, {}).get(type_name)


def declared_options() -> tuple[tuple[str, str, type[BaseModel]], ...]:
    """Every type that declares an options model, as stage, type and
    model, in the table's own order.

    The enumeration the documentation renders from, so that what a
    schema, a reference table or a help page lists is what the registry
    dispatches on rather than a second list of converted types.
    """
    return tuple(
        (stage, type_name, entry.options)
        for stage, types in _registrations().items()
        for type_name, entry in types.items()
        if entry.options is not None
    )


def construct_provider(
    stage: str,
    name: str,
    config: ProviderConfig,
    secrets: SecretStore | None = None,
) -> Provider:
    """Construct the provider behind `providers.<stage>.<name>`, raising
    ProviderError for an unknown type, a bad option, a missing extra, or
    anything the provider itself raises while constructing. Every one of
    them names the entry.

    Construction and nothing else, which is what lets this run in a
    worker thread: the checks that come after an object exists are the
    owner's (`providers/world.py`), because refusing one means closing
    it and a close is a coroutine (#191). What comes before it is the
    same rule read from the other end: a type that declares an options
    model has it validated here, before its factory is called at all,
    and a type that declares none reads its options to the end and
    finishes them inside its own factory, so either way a bad option is
    a refusal with nothing to let go of.

    `secrets` is the store a snapshot was loaded with, or None for a
    deployment whose credentials are all environment references. This is
    the one place that knows the stage and the name a stored credential
    is keyed by, so it is where the entry's secrets are put in force for
    the construction call."""
    label = f"providers.{stage}.{name}"
    entry = registration(stage, config.type)
    if entry is None:
        known = ", ".join(sorted(_registrations()[stage]))
        raise ProviderError(
            f'{label}: unknown {stage} provider type "{config.type}" (known types: {known})'
        )
    # What the type says it accepts, checked before anything is built,
    # which is the ordering `finish()` established and this keeps: an
    # option a type does not declare is a refusal that must not cost a
    # model load, and after the load there would be an object to let go
    # of again (#191).
    #
    # Recorded and raised outside the handler, and the refusal it raises
    # chains nothing: what the sanitizer caught holds the rejected
    # options, and this sentence is printed to an operator as it is.
    options: BaseModel | None = None
    refused: str | None = None
    try:
        options = checked_options(f"invalid {label}:", stage, config.type, config.options)
    except OptionsRefused as exc:
        refused = str(exc)
    if refused is not None:
        raise ProviderError(refused)
    # Every other failure in this function names the entry that caused
    # it, which is what makes a bad configuration a five-second fix.
    # Construction was the exception: a local engine fetching its
    # weights can fail on a blocked host, a full volume, a corrupt
    # cache or a name the hub does not have, and each of those arrived
    # as a traceback from inside somebody else's library with no
    # mention of which entry was being built. Survivable while a
    # configuration had one provider per stage; a deployment running
    # language-locked agents has three ASR entries and three TTS
    # entries that differ only in a pinned language and a voice.
    #
    # ProviderError passes through untouched, so the messages the rest
    # of this module composes keep their exact wording, and `from exc`
    # keeps the traceback for whoever wants it. ConfigError passes
    # through for the same reason and one more: a stored credential that
    # will not decrypt is a configuration problem whose message already
    # names the entity and the slot to set again, and wrapping it as a
    # failure to build a provider would bury that under the wrong
    # heading.
    #
    # What the library said is deliberately not in the sentence. This
    # message is printed to an operator as it is (it is a boot failure,
    # and the entry point answers one with a single line), and the text
    # arriving here is whatever a third-party client raised while being
    # handed this entry's options: an SDK that cannot reach its endpoint
    # quotes the URL, one that will not authenticate quotes what it was
    # given, and a copy of either into a printed line puts it wherever
    # that line is kept. The same rule the device bindings' failed reads
    # follow (`device/bindings.py`): the class name is said, the message
    # is not, and the exception itself is this one's `__cause__` for
    # whoever has a debugger. Options are validated by our own reader and
    # raise `ProviderError` above, so the case this loses nothing on is
    # the common one.
    try:
        with provider_secrets_in_force(ProviderSecrets(stage, name, secrets)):
            provider = (
                entry.factory(label, config)
                if options is None
                else entry.factory(label, config, options)
            )
    except (ProviderError, ConfigError):
        raise
    except Exception as exc:
        raise ProviderError(
            f"{label}: the {config.type} provider would not build "
            f"({type(exc).__name__}). What it said is not repeated here, because a "
            f"library failing to start can quote the endpoint or the credential this "
            f"entry names; check this entry's options and the service it points at"
        ) from exc
    if not isinstance(provider, Provider):
        # Unwritable from a factory in this package, and refused rather
        # than assumed: everything downstream owns what it is handed,
        # and a lifecycle cannot be promised for an object that is not
        # one of these.
        raise ProviderError(
            f'{label}: type "{config.type}" built {type(provider).__name__}, which is '
            f"not a provider; every factory answers one"
        )
    return provider


@dataclass(frozen=True)
class AgentProviders:
    """The four engines a session holds a conversation as one agent
    with.

    Exactly the providers, and deliberately not the agent's prompt as
    well: that used to ride here as a boot-time copy of
    `agents.<name>.prompt`, and two sources for one string is how the
    pipeline and an inspection surface come to disagree about what the
    model was sent. `Config.prompt_for_agent` is the one source.
    """

    llm: LlmProvider
    asr: AsrProvider
    tts: TtsProvider
    vad: VadProvider
