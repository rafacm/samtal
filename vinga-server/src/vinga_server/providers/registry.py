"""Constructing providers from their configuration entries.

Every provider type is written down once, in
`config/provider_options.py`: where its factory lives and what it
accepts. This module turns that into `Registration`s, resolving a
factory name into a callable at the moment a provider is built, so the
core install only pays for the engines a configuration references and a
missing optional dependency becomes an error naming the extra to
install rather than an ImportError from the middle of a request.

Construction only. What owns the objects, checks them and lets go of
them again is `providers/world.py`, which is the half that cannot run in
a worker thread: refusing a provider that already exists means closing
it (#191).
"""

from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from types import ModuleType

from pydantic import BaseModel

from vinga_server.config import ConfigError
from vinga_server.config.entities import provider_label
from vinga_server.config.models import ProviderConfig
from vinga_server.config.provider_options import (
    PROVIDER_TYPES,
    OptionsRefused,
    ProviderType,
    validated,
)
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
    """What one stage's type name is, on this side of the boundary: a
    callable that builds it, and the model it accepts.

    Derived rather than declared. Both halves come out of one entry of
    `config.provider_options.PROVIDER_TYPES`, which is where the types
    are written down: this resolves that entry's factory name into
    something callable and carries its model along unchanged. There is
    no second table here to hold against that one, which is the whole
    point of the shape: two stage-and-type mappings that must agree
    about which types exist are one mapping with a bug pending, and a
    test bridging them is the bug's receipt rather than its cure.

    Which side owns what follows from what each side may weigh. The
    types are documented by surfaces that must load neither an engine
    nor a database driver, and reaching this module means importing the
    provider package, so the table lives where the light readers can
    reach it and the resolving happens here, where the engines are.

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


def _resolved(type_name: str, declared: ProviderType) -> Factory:
    """One table entry's factory name as something callable.

    The import happens when a provider is built and not before, which is
    the laziness the per-type factory functions used to spell out one
    closure each: the core install pays for the engine a configuration
    references and for no other.

    A missing optional dependency is explained rather than raised. What
    reaches an operator names the extra to install, and it names it from
    the table's own `extra` rather than from a sentence written per
    type, so a type that gains an extra gains the message. The refusal
    is raised after the handler has closed and chains nothing: an
    ImportError carries the module search path and a traceback through
    somebody else's package, and this sentence is printed to an operator
    as it is.
    """

    def factory(label: str, config: ProviderConfig, *rest: object) -> object:
        missing: str | None = None
        module: ModuleType | None = None
        try:
            module = import_module(declared.path)
        except ImportError:
            if declared.extra is None:
                # Nothing to explain: a core module that will not import
                # is a packaging fault, and the generic wrapper below
                # names the class without repeating what it said.
                raise
            missing = declared.extra
        if module is None:
            raise ProviderError(
                f'{label}: type "{type_name}" needs the {missing} extra; '
                f"install it with: uv sync --extra {missing}"
            )
        return getattr(module, declared.attribute)(label, config, *rest)

    return factory


def _registrations() -> dict[str, dict[str, Registration]]:
    """The table this module builds from, derived whole.

    Rebuilt per call, as it has always been: it is a dozen closures over
    strings, nothing is imported to make one, and a test that needs a
    type of its own replaces this function rather than mutating a
    global.
    """
    return {
        stage: {
            type_name: Registration(_resolved(type_name, declared), declared.options)
            for type_name, declared in types.items()
        }
        for stage, types in PROVIDER_TYPES.items()
    }


def registration(stage: str, type_name: str) -> Registration | None:
    """What the table says about one stage's type, or None for a stage
    or a type it does not have.

    The read the build path goes through: `construct_provider` below
    asks it what to build with, and what to validate against on the way.
    A surface that wants only the contract asks
    `config.provider_options` instead, which is the module this reads
    from and the one a document can afford to load.
    """
    return _registrations().get(stage, {}).get(type_name)


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
    # The same two readings of one entry the owner keeps: the label is
    # what every refusal below names it, said through the strip (#413),
    # and `name` is what the stored credential is filed under, which is
    # why the secrets below are put in force with it rather than with
    # what the label shows.
    label = provider_label(stage, name)
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
    if entry.options is not None:
        try:
            options = validated(f"invalid {label}:", entry.options, config.options)
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
    # of this module composes keep their exact wording. ConfigError
    # passes through for the same reason and one more: a stored
    # credential that will not decrypt is a configuration problem whose
    # message already names the entity and the slot to set again, and
    # wrapping it as a failure to build a provider would bury that under
    # the wrong heading.
    #
    # What the library said is deliberately not in the sentence, and
    # neither is the library's exception. This message is printed to an
    # operator as it is (it is a boot failure, and the entry point
    # answers one with a single line), and what arrives here is whatever
    # a third-party client raised while being handed this entry's
    # options: an SDK that cannot reach its endpoint quotes the URL, one
    # that will not authenticate quotes what it was given, and either
    # reaches a stream through the message, through a rendered traceback
    # or through anything that walks a chain.
    #
    # The class name alone survives, captured inside the handler, and
    # the refusal is raised after the handler has closed so that it
    # carries neither a cause nor a context. That reverses this line's
    # own earlier decision, which kept the original as `__cause__` "for
    # whoever has a debugger" (#188): the discipline every refusal in
    # the configuration package has been held to since is that a chain
    # is a rendering surface like any other, and a debugger reads the
    # log, which records the class and the entry.
    failed: str | None = None
    provider: object | None = None
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
        failed = type(exc).__name__
    if failed is not None:
        raise ProviderError(
            f"{label}: the {config.type} provider would not build "
            f"({failed}). What it said is not repeated here, because a "
            f"library failing to start can quote the endpoint or the credential this "
            f"entry names; check this entry's options and the service it points at"
        )
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
