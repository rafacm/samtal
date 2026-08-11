"""Pydantic models for the samtal-server YAML configuration.

Top-level keys: server, providers, mcp_servers, agent_defaults, agents,
devices, default_agent, memory. Secrets are referenced by environment
variable name (for example api_key_env, or a $VAR value in an MCP
server's env and headers), never written inline.
"""

import os
import re
from collections.abc import Mapping
from contextvars import ContextVar
from pathlib import Path
from typing import Annotated, Literal, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from samtal_server.tools import names

_MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")

# Fragments that mark a provider option as secret-bearing. Keys ending in
# _env are the sanctioned pattern: they name an environment variable instead
# of holding the value.
_SECRET_KEY_FRAGMENTS = ("secret", "token", "password", "api_key", "apikey", "credential")

# The same rule for an MCP server's env and headers, where the key that
# carries a secret is as often called Authorization as it is token.
_MCP_SECRET_KEY_FRAGMENTS = (*_SECRET_KEY_FRAGMENTS, "auth")

# An environment reference in an MCP server's env or headers: the whole
# value is $NAME, which is resolved from the server's own environment at
# boot. A value that must begin with a literal $ is not supported.
_ENV_REFERENCE_RE = re.compile(r"^\$([A-Za-z_][A-Za-z0-9_]*)$")

PROVIDER_STAGES = ("llm", "asr", "tts", "vad")

# The logging level names, most to least verbose. NOTSET is left out: on
# the root logger it means WARNING, which is not what writing it says.
_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

# Identifiers (provider names, agent names, references between them) must
# survive stripping with at least one character.
NonBlankStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class AuthConfig(BaseModel):
    """Device authentication for the websocket endpoint.

    On by default: a deployment that forgets to configure a secret is
    refused at boot rather than quietly served open. Turning it off is
    one deliberate, visible flag, for a LAN trial.

    The secret is referenced by the name of the environment variable
    holding it, the same rule as every other secret in this file.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    secret_env: NonBlankStr = "SAMTAL_AUTH_SECRET"

    # How long an issued token stays valid. Thirty days, upstream's
    # default; the firmware re-checks OTA on every boot, so a device in
    # normal use is re-issued long before it gets near this.
    token_expire_s: int = Field(default=2592000, gt=0)


class LimitsConfig(BaseModel):
    """What one server will hold at once, and for how long.

    Three numbers rather than a framework. The firmware treats a close
    as the end of a conversation and reconnects on the next wake word,
    so both of the time bounds here are invisible in normal use.
    """

    model_config = ConfigDict(extra="forbid")

    # Concurrent conversations. Each one holds an ASR, an LLM stream, and
    # a TTS engine, so this is a resource bound and not a licence check.
    max_sessions: int = Field(default=8, ge=1)

    # One session's maximum life, in seconds. An hour by default.
    max_session_s: float = Field(default=3600.0, gt=0)

    # How long a realtime session may go without a conversation before
    # the server hangs up, in seconds, counted from the end of the last
    # utterance or the end of the last reply, whichever came later. Two
    # minutes by default: long enough to think, read something out, or
    # answer the door, short enough that walking away does not leave a
    # mic streaming for the rest of the hour.
    #
    # Only realtime sessions, because only they stream continuously; an
    # auto-mode device stops listening after each reply and re-arms per
    # turn, and is bounded by max_session_s as before. There is no off
    # switch: a deployment that wants none sets it near max_session_s.
    idle_timeout_s: float = Field(default=120.0, gt=0)


class CaptureConfig(BaseModel):
    """Recording sessions to disk for offline analysis.

    Off by default and off unless said otherwise, the same shape as
    `auth.enabled`. This writes room audio to disk, which is the
    opposite of what the rest of the project promises, so nothing here
    can turn it on by accident: the section has to exist and the flag
    has to say so.

    The flag rather than the section's presence is what switches it,
    because the field workflow is to record, then stop, and the
    directory and the budgets are worth keeping in the file across that.
    Turning capture off should not mean deleting the tuning that says
    where captures go and how much room they may have.

    It exists because acoustic defects cannot be reproduced in any test
    lane. A recording of the microphone against what the speaker was
    playing is what lets echo leakage be measured rather than guessed
    (#28).
    """

    model_config = ConfigDict(extra="forbid")

    # The switch. Off by default, so a section left in a config file
    # records nothing until somebody says it should.
    enabled: bool = False

    # Where captures are written. Must be on the data volume: a
    # deployment's container root is read-only. Required even when
    # disabled, so turning capture on is one word rather than one word
    # and remembering where it writes.
    dir: Path

    # Stop capturing a session after this many seconds. A bound on one
    # file, not on the conversation, which carries on uncaptured.
    max_session_s: float = Field(default=900.0, gt=0)

    # Total budget for the directory. Whole captures are pruned, oldest
    # first, when it is exceeded.
    max_total_mb: float = Field(default=2000.0, gt=0)

    # Refuse to start a capture when the volume has less free than this.
    # The byte budget above does not protect the volume on its own: the
    # model caches and agent memory share it and grow underneath.
    min_free_mb: float = Field(default=1000.0, ge=0)


class ServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = "0.0.0.0"
    port: int = Field(default=8003, ge=1, le=65535)

    # The websocket URL handed to devices by the OTA endpoint. Left unset it
    # is derived from the address the device reached the OTA endpoint on,
    # which is right for a plain LAN deployment; set it explicitly when the
    # server sits behind a proxy or a name the request headers do not carry.
    websocket_url: str | None = None

    # Where the OTA endpoint is served. It is the token issuer, so it cannot
    # itself require a token; an operator exposing the server publicly hides
    # it behind a long random segment (/xiaozhi/ota/8f3a.../) and writes that
    # URL into the device's NVS. The websocket path is fixed: the token is
    # what protects it.
    ota_path: str = "/xiaozhi/ota/"

    # Binary protocol version advertised to devices. The firmware defaults to
    # 1 (bare Opus frames); 2 and 3 add timestamp headers.
    protocol_version: int = Field(default=1, ge=1, le=3)

    # Minutes east of UTC, sent so the device can set its clock to local time.
    # Left unset the server's own current offset is used.
    timezone_offset_minutes: int | None = Field(default=None, ge=-1440, le=1440)

    # How the server logs. "text" is the human format; "json" is one object
    # per line, which is what the container image defaults to, and what
    # makes retained logs readable back as conversation transcripts.
    log_format: Literal["text", "json"] = "text"
    log_level: str = "INFO"

    auth: AuthConfig = Field(default_factory=AuthConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)

    # Absent, or present with enabled off, means no session is ever
    # recorded. Absent is the default.
    capture: CaptureConfig | None = None

    # Refuse to boot any provider that sends session data off this host.
    # Running without a cloud dependency is otherwise a documentation
    # property of a carefully chosen configuration; this makes it a
    # checked one. Boot-time, never runtime: a local_only server that
    # starts is a local_only server (#30).
    local_only: bool = False

    # Whether speech arriving while a reply is playing interrupts it. On
    # by default, because a device only streams its mic through playback
    # when its echo cancellation is on, and what arrives is then the
    # user's voice. Turn it off for a board whose cancellation leaks the
    # speaker back into the mic (a single-mic board), where the reply
    # would otherwise interrupt itself: conversations stay multi-turn,
    # and what arrives during a reply is dropped instead.
    barge_in: bool = True

    # The least endpointer-classified speech, in milliseconds, an
    # utterance needs before it may interrupt a reply. Noise blips and
    # playback bleed rarely sustain half a second of speech; a real
    # interjection does (#28).
    barge_in_min_speech_ms: float = Field(default=500.0, ge=0)

    # How long after a reply's first audio frame that interruptions are
    # ignored, covering the transient a device's echo cancellation lets
    # through at playback onset.
    barge_in_refractory_ms: float = Field(default=1000.0, ge=0)

    # How much audio from before the detected start of speech rides
    # along to ASR, so the first phoneme survives the trim. The rest of
    # the leading silence a continuously listening device piles up is
    # dropped before transcription (#14).
    utterance_pre_roll_ms: float = Field(default=300.0, ge=0)

    # How long the LLM may take to its first token before the round is
    # cancelled and retried once; a second timeout gives the round up.
    # Only the wait for the first token is bounded, because a long
    # generation that is streaming is healthy. Any stream activity
    # stops the clock: the adapters announce their first chunk off the
    # wire, so a round that streams only a buffered tool call is not
    # mistaken for a stall. The default is chosen
    # against field data: healthy first tokens cluster at 500 to 800 ms,
    # the worst spike that still answered sat at 8.9 s, and the stall
    # this exists for held the session for 17 s with nothing on the
    # wire (#68).
    llm_first_token_timeout_s: float = Field(default=10.0, gt=0)

    # How long a shutdown waits for conversations in flight to finish
    # speaking before the process goes. Twenty seconds sits inside the
    # thirty an orchestrator commonly allows between SIGTERM and SIGKILL;
    # `docker stop` needs its own timeout raised above this.
    drain_s: float = Field(default=20.0, ge=0)

    @field_validator("log_level")
    @classmethod
    def _check_log_level(cls, value: str) -> str:
        level = value.strip().upper()
        if level not in _LOG_LEVELS:
            raise ValueError(
                f'"{value}" is not a logging level; expected one of: '
                + ", ".join(_LOG_LEVELS)
            )
        return level

    @field_validator("ota_path")
    @classmethod
    def _check_ota_path(cls, value: str) -> str:
        path = value.strip()
        if not path.startswith("/") or not path.endswith("/"):
            raise ValueError(
                f'"{value}" is not a usable OTA path; it must start and end with '
                f'"/", for example /xiaozhi/ota/ or /xiaozhi/ota/8f3a9c2b.../'
            )
        return path

    @field_validator("websocket_url")
    @classmethod
    def _check_websocket_scheme(cls, value: str | None) -> str | None:
        if value is None:
            return value
        url = value.strip()
        if not url.startswith(("ws://", "wss://")):
            raise ValueError(
                f'"{value}" is not a websocket URL; it must start with ws:// or wss://'
            )
        return url


class ProviderConfig(BaseModel):
    """One provider entry. Options beyond `type` are passed through to the
    provider implementation, so extra keys are allowed here."""

    model_config = ConfigDict(extra="allow")

    type: NonBlankStr
    api_key_env: str | None = None

    # The operator's own egress assertion, honoured only for types whose
    # class-level marking is None because their configuration decides
    # (openai_compatible, where base_url can name localhost or a cloud
    # vendor). Declaring it on a type that knows its own egress is
    # rejected when the provider is built.
    egress: bool | None = None

    @model_validator(mode="after")
    def _reject_inline_secrets(self) -> "ProviderConfig":
        for key in self.model_extra or {}:
            lowered = key.lower()
            if lowered.endswith("_env"):
                continue
            if any(fragment in lowered for fragment in _SECRET_KEY_FRAGMENTS):
                raise ValueError(
                    f'"{key}" looks like an inline secret, which is not allowed; '
                    f"reference an environment variable instead, for example "
                    f"{key}_env: MY_PROVIDER_{key.upper()}"
                )
        return self

    @property
    def options(self) -> dict[str, object]:
        """Provider-specific options (everything beyond the declared fields)."""
        return dict(self.model_extra or {})


class ProvidersConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    llm: dict[NonBlankStr, ProviderConfig] = Field(default_factory=dict)
    asr: dict[NonBlankStr, ProviderConfig] = Field(default_factory=dict)
    tts: dict[NonBlankStr, ProviderConfig] = Field(default_factory=dict)
    vad: dict[NonBlankStr, ProviderConfig] = Field(default_factory=dict)


def _env_reference(value: str) -> str | None:
    """The variable name behind a `$VAR` value, or None for a literal."""
    match = _ENV_REFERENCE_RE.match(value.strip())
    return match.group(1) if match else None


def resolve_env_references(location: str, values: Mapping[str, str]) -> dict[str, str]:
    """A `$VAR` mapping with its secrets read from the server's own
    environment. Literal values for non-secret keys pass through. An
    unset variable raises, naming where it was written, because at call
    time it would fail every conversation that reaches the server.

    Kept out of the model so the parsed configuration never holds a
    secret: resolution happens at boot, where the value is used."""
    resolved: dict[str, str] = {}
    for key, value in values.items():
        name = _env_reference(value)
        if name is None:
            resolved[key] = value
            continue
        secret = os.environ.get(name, "")
        if not secret:
            raise ValueError(
                f"{location}.{key}: references ${name}, but it is not set in the environment"
            )
        resolved[key] = secret
    return resolved


class McpServerConfig(BaseModel):
    """One MCP server, named so agents can reference it.

    `transport` decides which of the two field groups applies: a stdio
    server is a command this server spawns, a streamable_http one is a
    URL it connects to. Naming a field of the other transport is an
    error rather than a silently ignored key, since the difference
    between "my headers are ignored" and "my headers are wrong" is a
    debugging afternoon.
    """

    model_config = ConfigDict(extra="forbid")

    transport: Literal["stdio", "streamable_http"]

    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)

    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)

    # Whether this server sends session data off the local network. Tool
    # arguments carry conversation-derived data, and neither transport
    # can tell on its own: a stdio command may proxy anywhere, a url may
    # name localhost. Under server.local_only every referenced entry
    # must therefore declare egress: false, the operator asserting that
    # whatever its command or URL reaches stays local (#30).
    egress: bool | None = None

    # How long one tool call on this server may take before the model is
    # told it timed out. Spoken silence is the cost, so it is short.
    tool_timeout_s: float = Field(default=15.0, gt=0)

    @model_validator(mode="after")
    def _check_transport_fields(self) -> "McpServerConfig":
        stdio_only = ("command", "args", "env")
        http_only = ("url", "headers")
        if self.transport == "stdio":
            required, foreign = "command", http_only
        else:
            required, foreign = "url", stdio_only

        problems: list[str] = []
        value = getattr(self, required)
        if value is None or not str(value).strip():
            problems.append(f'transport "{self.transport}" needs "{required}"')
        named = [field for field in foreign if field in self.model_fields_set]
        if named:
            problems.append(
                f'transport "{self.transport}" has no {", ".join(named)}; '
                f"that belongs to the other transport"
            )
        problems += self._secret_problems()
        if problems:
            raise ValueError("; ".join(problems))
        return self

    def _secret_problems(self) -> list[str]:
        """Secret-bearing env and header keys must name an environment
        variable, the same rule that keeps provider secrets out of the
        configuration file."""
        problems: list[str] = []
        for group, values in (("env", self.env), ("headers", self.headers)):
            for key, value in values.items():
                lowered = key.lower()
                if not any(fragment in lowered for fragment in _MCP_SECRET_KEY_FRAGMENTS):
                    continue
                if _env_reference(value) is None:
                    problems.append(
                        f'{group}.{key} looks like an inline secret, which is not '
                        f"allowed; reference an environment variable instead, for "
                        f"example {key}: $MY_SERVER_SECRET"
                    )
        return problems


class MemoryConfig(BaseModel):
    """Where the agents' remembered facts are kept.

    The whole section is optional: without it there is no `remember`
    tool and nothing is injected into any prompt.
    """

    model_config = ConfigDict(extra="forbid")

    dir: Path


class FillerConfig(BaseModel):
    """Masking reply latency with a pre-synthesized filled pause.

    Off by default. When enabled, the phrases are synthesized in the
    agent's own voice at boot and cached as PCM; a reply whose first
    audio has not started within `delay_ms` of the utterance being
    transcribed plays one, and the real reply queues behind its tail.
    A synthesis failure at boot logs a warning and leaves the feature
    off for that agent rather than failing the boot.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False

    # How long the user hears silence before the filler starts, counted
    # from the transcription (the `heard` event). Healthy replies get
    # their first audio out around 1.2 s in the field data, so 1800 ms
    # keeps ordinary turns filler-free while landing well before the
    # 2 to 3 s of dead air where users start asking whether anyone is
    # there.
    delay_ms: float = Field(default=1800.0, gt=0)

    # One or more phrases in the agent's own language; the player
    # rotates through them rather than always playing the same one.
    # Required when enabled: there is nothing to say otherwise.
    phrases: list[NonBlankStr] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_phrases(self) -> "FillerConfig":
        if self.enabled and not self.phrases:
            raise ValueError(
                "filler.enabled is on with no phrases; add at least one, "
                'for example "Hmm, let me see..."'
            )
        return self


class AgentDefaults(BaseModel):
    """Provider references every agent inherits unless it names its own.

    Deliberately no prompt: a persona's prompt is its identity, and
    inheriting one silently would make two agents the same agent.
    """

    model_config = ConfigDict(extra="forbid")

    llm: NonBlankStr | None = None
    asr: NonBlankStr | None = None
    tts: NonBlankStr | None = None
    vad: NonBlankStr | None = None

    # The MCP servers this agent talks to. None means inherit; a list
    # replaces rather than extends the inherited one, like the stage
    # fields, so an agent naming an empty list opts out of tools.
    mcp: list[NonBlankStr] | None = None

    # Latency masking with a pre-synthesized filler clip. None means
    # inherit; a section replaces the inherited one wholly, like the
    # stage fields, so an agent naming its own phrases names all of
    # them, and `filler: {enabled: false}` opts an agent out.
    filler: FillerConfig | None = None


class AgentConfig(AgentDefaults):
    """One persona: a prompt, plus whichever stages it overrides."""

    prompt: str = ""


def normalize_mac(value: str) -> str:
    """Normalize a MAC address to lowercase colon-separated form."""
    mac = value.strip().lower().replace("-", ":")
    if not _MAC_RE.match(mac):
        raise ValueError(
            f'"{value}" is not a MAC address; expected six colon-separated '
            f"hex pairs, for example aa:bb:cc:dd:ee:ff"
        )
    return mac


def check_mcp_entry_names(value: dict[str, McpServerConfig]) -> dict[str, McpServerConfig]:
    """An entry name becomes a tool-name prefix, so it has to be a legal
    tool name, and it may not be one the merged list already uses. That
    is what makes a namespace collision unrepresentable rather than
    something to resolve at merge time."""
    problems = [
        f"mcp_servers.{name}: not a usable entry name; it becomes a tool-name "
        f"prefix, so it must match [A-Za-z0-9_-]+ and must not be one of: "
        + ", ".join(names.RESERVED_ENTRY_NAMES)
        for name in value
        if not names.is_valid_entry_name(name)
    ]
    if problems:
        raise ValueError("\n".join(problems))
    return value


def normalize_device_bindings(value: object) -> object:
    """The devices mapping with every MAC in its canonical form and
    every binding a list. Anything that is not a mapping is left for
    pydantic to report."""
    if not isinstance(value, dict):
        return value
    normalized: dict[str, object] = {}
    for mac, bound in value.items():
        key = normalize_mac(str(mac))
        if key in normalized:
            raise ValueError(f'device "{mac}" appears more than once (as {key})')
        normalized[key] = _binding_as_list(key, bound)
    return normalized


class DomainSnapshot(Protocol):
    """The domain half of a configuration, whatever is holding it.

    The checks below run against the composed Config at boot and against
    the repository's own model at write time, so they are written
    against the attributes both of those have rather than against either
    class. Neither check needs the server half, which is why a snapshot
    is enough.
    """

    providers: ProvidersConfig
    mcp_servers: dict[str, McpServerConfig]
    agent_defaults: AgentDefaults
    agents: dict[str, AgentConfig]
    devices: dict[str, list[str]]
    default_agent: str | None


def check_references(snapshot: DomainSnapshot) -> list[str]:
    """Every reference in the snapshot resolving: agents and
    agent_defaults to providers and MCP servers, device bindings to
    agents, default_agent to an agent.

    Run at write time as well as at boot. A reference that does not
    resolve is a broken entity whenever it is written, and refusing it
    at the write is what forces the natural creation order (providers,
    MCP servers, agents, devices) rather than discovering the mistake at
    the next restart.
    """
    problems: list[str] = []

    if snapshot.default_agent is not None and snapshot.default_agent not in snapshot.agents:
        problems.append(
            f'default_agent "{snapshot.default_agent}" is not a defined agent'
            + (f" (defined: {', '.join(sorted(snapshot.agents))})" if snapshot.agents else "")
        )

    for mac, bound in snapshot.devices.items():
        for agent in bound:
            if agent not in snapshot.agents:
                problems.append(f'devices.{mac}: agent "{agent}" is not a defined agent')

    # Each layer's own references are checked where they are written,
    # so a wrong default is reported once as agent_defaults.llm rather
    # than once per agent that inherits it.
    sources: list[tuple[str, AgentDefaults]] = [("agent_defaults", snapshot.agent_defaults)]
    sources += [(f"agents.{name}", agent) for name, agent in snapshot.agents.items()]
    for source, layer in sources:
        for stage in PROVIDER_STAGES:
            ref = getattr(layer, stage)
            if ref is None:
                continue
            available = getattr(snapshot.providers, stage)
            if ref not in available:
                hint = (
                    f" (defined: {', '.join(sorted(available))})"
                    if available
                    else f"; no providers.{stage} entries are defined"
                )
                problems.append(f'{source}.{stage}: unknown {stage} provider "{ref}"{hint}')
        for entry in layer.mcp or []:
            if entry not in snapshot.mcp_servers:
                hint = (
                    f" (defined: {', '.join(sorted(snapshot.mcp_servers))})"
                    if snapshot.mcp_servers
                    else "; no mcp_servers entries are defined"
                )
                problems.append(f'{source}.mcp: unknown MCP server "{entry}"{hint}')

    return problems


def check_completeness(snapshot: DomainSnapshot) -> list[str]:
    """The rules about a runnable server rather than about a valid
    entity.

    Boot only. Enforcing this at write time would deadlock the natural
    creation order: the first agent cannot exist before default_agent
    names it, and default_agent cannot name it before it exists. A
    half-built configuration is a legitimate state of the database and
    an illegitimate state to serve from.
    """
    problems: list[str] = []

    # Omitting default_agent is how a deployment says "only these
    # devices": every unknown MAC then resolves to no agent, is issued
    # no token, and is turned away, so the devices map is the allowlist.
    # Omitting it with nothing bound either is the case that cannot be
    # meant, since no device could reach any agent.
    if snapshot.agents and snapshot.default_agent is None and not snapshot.devices:
        problems.append(
            "default_agent is required when agents are defined and no device is "
            "bound to one; set it to one of: " + ", ".join(sorted(snapshot.agents))
        )

    return problems


# The YAML file the settings source should read, set by the loader around
# instantiation. pydantic-settings has no init kwarg for a runtime-chosen
# path yet (pydantic-settings#259).
yaml_file_var: ContextVar[Path | None] = ContextVar("samtal_yaml_file", default=None)


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        extra="forbid",
        env_prefix="SAMTAL_",
        env_nested_delimiter="__",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            YamlConfigSettingsSource(settings_cls, yaml_file=yaml_file_var.get()),
            file_secret_settings,
        )

    server: ServerConfig = Field(default_factory=ServerConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    # Named like providers, and referenced by agents the same way. The
    # entry name becomes the prefix its tools are offered under.
    mcp_servers: dict[NonBlankStr, McpServerConfig] = Field(default_factory=dict)
    memory: MemoryConfig | None = None
    agent_defaults: AgentDefaults = Field(default_factory=AgentDefaults)
    agents: dict[NonBlankStr, AgentConfig] = Field(default_factory=dict)
    # One device may be bound to several agents; the value is written as a
    # single name or a list, and always stored as a list.
    devices: dict[str, list[NonBlankStr]] = Field(default_factory=dict)
    default_agent: NonBlankStr | None = None

    @field_validator("mcp_servers")
    @classmethod
    def _check_entry_names(
        cls, value: dict[str, McpServerConfig]
    ) -> dict[str, McpServerConfig]:
        return check_mcp_entry_names(value)

    @field_validator("devices", mode="before")
    @classmethod
    def _normalize_device_bindings(cls, value: object) -> object:
        return normalize_device_bindings(value)

    @model_validator(mode="after")
    def _check_domain(self) -> "Config":
        """Boot validates the whole domain snapshot: the completeness
        rules a runnable server needs and the references every write
        already had to satisfy. Both halves in one message, in the order
        they have always been reported."""
        problems = check_completeness(self) + check_references(self)
        if problems:
            raise ValueError("\n".join(problems))
        return self

    def provider_for_agent(self, agent: str, stage: str) -> tuple[str | None, str]:
        """The provider an agent uses for one stage, and the configuration
        location it came from: the agent's own entry when it names one,
        agent_defaults otherwise. The location is what error messages quote,
        so a mistake points at the layer that holds it."""
        own = getattr(self.agents[agent], stage)
        if own is not None:
            return own, f"agents.{agent}.{stage}"
        return getattr(self.agent_defaults, stage), f"agent_defaults.{stage}"

    def mcp_for_agent(self, agent: str) -> list[str]:
        """The MCP servers an agent talks to: its own list when it names
        one, agent_defaults otherwise. A list replaces rather than
        extends, so `mcp: []` is how an agent opts out of tools its
        siblings have."""
        own = self.agents[agent].mcp
        if own is not None:
            return list(own)
        return list(self.agent_defaults.mcp or [])

    def filler_for_agent(self, agent: str) -> FillerConfig | None:
        """The filler section that applies to an agent: its own when it
        names one, agent_defaults otherwise. A section replaces rather
        than merges, so an agent's own phrases are all of its phrases."""
        own = self.agents[agent].filler
        if own is not None:
            return own
        return self.agent_defaults.filler

    def referenced_mcp_servers(self) -> set[str]:
        """The entries some agent actually uses. Only these are
        connected at startup, the way only referenced providers are
        built."""
        return {entry for agent in self.agents for entry in self.mcp_for_agent(agent)}

    def agents_for_device(self, mac: str) -> list[str]:
        """The agents a device may talk to, the first of them the one a
        conversation starts on. Unknown devices fall back to default_agent;
        a device with no binding and no default_agent resolves to nothing,
        and is turned away."""
        bound = self.devices.get(normalize_mac(mac))
        if bound:
            return list(bound)
        return [self.default_agent] if self.default_agent is not None else []


def _binding_as_list(mac: str, bound: object) -> object:
    """A device binding written as one agent name or as a list, normalized
    to a list. Anything else is left for pydantic to report."""
    names = [bound] if isinstance(bound, str) else bound
    if not isinstance(names, list):
        return bound
    if not names:
        raise ValueError(f"devices.{mac}: bind the device to at least one agent")
    seen: set[str] = set()
    for name in names:
        if not isinstance(name, str):
            continue
        if name.strip() in seen:
            raise ValueError(f'devices.{mac}: agent "{name.strip()}" is listed more than once')
        seen.add(name.strip())
    return names
