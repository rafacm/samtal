"""Pydantic models for the samtal-server configuration.

The configuration has two halves. `FileConfig` is the half the YAML file
holds, `server` and `memory`, with the SAMTAL_ environment overrides
pydantic-settings gives it. The domain half (providers, mcp_servers,
agent_defaults, agents, devices, default_agent) lives in the database
and is written with `samtal-server config`. `Config` is the composition
of the two, which is what the server boots from and what every call site
reads.

Secrets are referenced by environment variable name (for example
api_key_env, or a $VAR value in an MCP server's env and headers), never
written inline; the other form is a value encrypted in the database,
which no model here ever carries.
"""

import os
import re
from collections.abc import Mapping
from contextvars import ContextVar
from pathlib import Path
from typing import Annotated, Literal, Protocol

from pydantic import (
    AfterValidator,
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

# What a key ending in _env may hold: the name of an environment
# variable, and nothing else. The same shape as the reference above
# without its $, since both name a variable the server looks up.
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

PROVIDER_STAGES = ("llm", "asr", "tts", "vad")

# The logging level names, most to least verbose. NOTSET is left out: on
# the root logger it means WARNING, which is not what writing it says.
_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

# Identifiers (provider names, agent names, references between them) must
# survive stripping with at least one character.
NonBlankStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def _check_env_name(value: str) -> str:
    """A key that names an environment variable holds a name and nothing
    else.

    A bare non-blank string would accept a pasted credential, which
    never worked (the name is looked up in the environment, and no
    lookup of a pasted key succeeds) and which the failure that follows
    would print: the boot error quotes the variable name it tells the
    operator to set. So the refusal happens here, at parse time, and it
    says what the key must hold and shows an example rather than quoting
    what was written, exactly as the provider validator does for a key
    ending in _env.
    """
    if not is_env_name(value):
        raise ValueError(
            "this key must hold the name of an environment variable, and what it "
            "holds does not look like one; a pasted value belongs nowhere in this "
            "file, so name the variable holding it, for example "
            "secret_env: SAMTAL_API_SECRET"
        )
    return value


# The name of an environment variable, for the keys whose whole job is
# to name one. Shared, because the mistake it catches is the same
# mistake wherever a secret is referenced by variable name.
EnvName = Annotated[str, AfterValidator(_check_env_name)]


class ApiConfig(BaseModel):
    """The configuration REST API, mounted at /api on the server's port.

    Always on, and always behind a bearer token: there is deliberately
    no enabled flag, because an admin surface that can be switched off
    by forgetting a key is a surface that ships unprotected. The token
    itself is never in this file, only the name of the environment
    variable holding it, and a server started without that variable set
    refuses to boot the way enabled device auth without a secret does.
    """

    model_config = ConfigDict(extra="forbid")

    secret_env: EnvName = Field(
        default="SAMTAL_API_SECRET",
        description=(
            "The name of the environment variable holding the API's bearer token, "
            "never the token itself. The server refuses to boot when the variable "
            "it names is unset or blank."
        ),
    )


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
    secret_env: EnvName = "SAMTAL_AUTH_SECRET"

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


class DatabaseConfig(BaseModel):
    """Where the domain half of the configuration is stored.

    The directory rather than the file is the key, mirroring memory.dir
    and capture.dir, so database-adjacent artifacts later need no second
    path key. One SQLite file named samtal.db lives inside it.

    The default is the generic one, not what any particular deployment
    runs with: a container image whose writable volume is elsewhere
    points this at that volume, and a development machine that cannot
    write to /var/lib either gets an error naming this key.
    """

    model_config = ConfigDict(extra="forbid")

    dir: Path = Path("/var/lib/samtal")


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

    # The configuration API mounted at /api. Always on, so the section
    # exists only to name the variable its bearer token comes from.
    api: ApiConfig = Field(default_factory=ApiConfig)

    limits: LimitsConfig = Field(default_factory=LimitsConfig)

    # Where `samtal-server config` writes the domain configuration, and
    # where the server reads it at each start.
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)

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


def is_secret_option(name: str) -> bool:
    """Whether an option name is secret-shaped.

    One rule, three readers: it is what makes an inline value in a
    fragment an error, what decides which option names are credential
    slots a secret may be stored under, and what the display path masks
    the value of."""
    lowered = name.lower()
    return any(fragment in lowered for fragment in _SECRET_KEY_FRAGMENTS)


def is_env_name(value: object) -> bool:
    """Whether a value is the name of an environment variable rather
    than something else, which for a key ending in _env is the only
    thing it may be.

    The check exists because nothing else stops a credential from being
    pasted where its variable name belongs: the value would then sit
    unencrypted in the configuration, and it never worked either, since
    the name is looked up in the environment and no lookup of a pasted
    key succeeds."""
    return isinstance(value, str) and _ENV_NAME_RE.match(value) is not None


def is_mcp_secret_key(name: str) -> bool:
    """The same question for an MCP server's env and headers, where the
    key carrying a secret is as often called Authorization as token."""
    lowered = name.lower()
    return any(fragment in lowered for fragment in _MCP_SECRET_KEY_FRAGMENTS)


class ProviderConfig(BaseModel):
    """One provider entry. Options beyond `type` are passed through to the
    provider implementation, so extra keys are allowed here."""

    model_config = ConfigDict(extra="allow")

    type: NonBlankStr = Field(
        description=(
            "The provider implementation this entry configures, such as anthropic, "
            "openai_compatible, faster_whisper, openai, piper, elevenlabs or silero. "
            "Every key beyond the ones listed here is an option for that "
            "implementation."
        )
    )
    api_key_env: str | None = Field(
        default=None,
        description=(
            "The name of the environment variable holding this provider's "
            "credential, never the credential itself. Left unset for a local engine "
            "or a keyless self-hosted endpoint. A credential stored with "
            "`config set-secret` fills the same slot and takes precedence."
        ),
    )

    # The operator's own egress assertion, honoured only for types whose
    # class-level marking is None because their configuration decides
    # (openai_compatible, where base_url can name localhost or a cloud
    # vendor). Declaring it on a type that knows its own egress is
    # rejected when the provider is built.
    egress: bool | None = Field(
        default=None,
        description=(
            "Whether this entry sends session data off the host, asserted by the "
            "operator for the types whose configuration decides it rather than "
            "their name (openai_compatible, and the openai ASR and TTS types, whose "
            "base_url may be local or a vendor). Under server.local_only such an "
            "entry must declare egress: false; a type that knows its own egress "
            "rejects the key."
        ),
    )

    @model_validator(mode="after")
    def _reject_inline_secrets(self) -> "ProviderConfig":
        # The declared field and the pass-through extras are the same
        # question: a key ending in _env names a variable, and any other
        # secret-shaped key is a value that does not belong here.
        entries: list[tuple[str, object]] = [("api_key_env", self.api_key_env)]
        entries += list((self.model_extra or {}).items())
        for key, value in entries:
            if key.lower().endswith("_env"):
                if value is not None and not is_env_name(value):
                    # Never the value: a key that fails this check most
                    # likely holds the credential itself, so the message
                    # says what the key must hold and shows an example
                    # rather than quoting what was written.
                    raise ValueError(
                        f'"{key}" must hold the name of an environment variable, and '
                        f"what it holds does not look like one; a pasted value belongs "
                        f"nowhere in this file, so name the variable holding it, for "
                        f"example {key}: MY_PROVIDER_KEY"
                    )
                continue
            if is_secret_option(key):
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
    """The provider entries of each pipeline stage, keyed by the name
    agents and agent_defaults reference them by."""

    model_config = ConfigDict(extra="forbid")

    llm: dict[NonBlankStr, ProviderConfig] = Field(
        default_factory=dict,
        description=(
            "The language model providers, keyed by the name that agents and "
            "agent_defaults reference."
        ),
    )
    asr: dict[NonBlankStr, ProviderConfig] = Field(
        default_factory=dict,
        description=(
            "The speech recognition providers, keyed by the name that agents and "
            "agent_defaults reference."
        ),
    )
    tts: dict[NonBlankStr, ProviderConfig] = Field(
        default_factory=dict,
        description=(
            "The speech synthesis providers, keyed by the name that agents and "
            "agent_defaults reference. A voice is a provider entry, so two agents "
            "that should sound different reference two entries."
        ),
    )
    vad: dict[NonBlankStr, ProviderConfig] = Field(
        default_factory=dict,
        description=(
            "The voice activity detection providers, keyed by the name that agents "
            "and agent_defaults reference."
        ),
    )


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

    transport: Literal["stdio", "streamable_http"] = Field(
        description=(
            "Which field group applies: stdio spawns `command` as a subprocess, "
            "streamable_http connects to `url`. Naming a field of the other "
            "transport is an error rather than a silently ignored key."
        )
    )

    command: str | None = Field(
        default=None,
        description=(
            "The executable a stdio server is spawned as. Required for that "
            "transport, and rejected for streamable_http."
        ),
    )
    args: list[str] = Field(
        default_factory=list,
        description="The arguments the stdio command is spawned with, one per entry.",
    )
    env: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Environment variables for the spawned stdio command. A value of $NAME "
            "is read from the server's own environment at startup, and any "
            "secret-bearing key (token, api_key, authorization, ...) must use that "
            "form."
        ),
    )

    url: str | None = Field(
        default=None,
        description=(
            "The endpoint a streamable_http server is reached at. Required for that "
            "transport, and rejected for stdio."
        ),
    )
    headers: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Headers sent with every streamable_http request. A value of $NAME is "
            "read from the server's own environment at startup, and any "
            "secret-bearing key must use that form."
        ),
    )

    # Whether this server sends session data off the local network. Tool
    # arguments carry conversation-derived data, and neither transport
    # can tell on its own: a stdio command may proxy anywhere, a url may
    # name localhost. Under server.local_only every referenced entry
    # must therefore declare egress: false, the operator asserting that
    # whatever its command or URL reaches stays local (#30).
    egress: bool | None = Field(
        default=None,
        description=(
            "Whether this server sends session data off the local network. Tool "
            "arguments carry conversation-derived data and neither transport can "
            "tell on its own, since a stdio command may proxy anywhere and a url "
            "may name localhost, so under server.local_only every referenced entry "
            "must declare it."
        ),
    )

    # How long one tool call on this server may take before the model is
    # told it timed out. Spoken silence is the cost, so it is short.
    tool_timeout_s: float = Field(
        default=15.0,
        gt=0,
        description=(
            "How long one tool call on this server may take, in seconds, before the "
            "model is told it timed out. The device hears silence meanwhile, so "
            "keep it short."
        ),
    )

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
                if not is_mcp_secret_key(key):
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

    enabled: bool = Field(
        default=False,
        description=(
            "Whether a filled pause is played while a slow reply is prepared. Off "
            "by default."
        ),
    )

    # How long the user hears silence before the filler starts, counted
    # from the transcription (the `heard` event). Healthy replies get
    # their first audio out around 1.2 s in the field data, so 1800 ms
    # keeps ordinary turns filler-free while landing well before the
    # 2 to 3 s of dead air where users start asking whether anyone is
    # there.
    delay_ms: float = Field(
        default=1800.0,
        gt=0,
        description=(
            "How long the user hears silence before the filler starts, in "
            "milliseconds, counted from the transcription of their utterance."
        ),
    )

    # One or more phrases in the agent's own language; the player
    # rotates through them rather than always playing the same one.
    # Required when enabled: there is nothing to say otherwise.
    phrases: list[NonBlankStr] = Field(
        default_factory=list,
        description=(
            "The phrases to play, written in the agent's own language; the player "
            "rotates through them rather than always playing the same one. At least "
            "one is required when the feature is enabled."
        ),
    )

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

    llm: NonBlankStr | None = Field(
        default=None,
        description=(
            "The language model, by the name it is defined under in providers.llm. "
            "An agent that leaves it unset inherits the agent_defaults entry."
        ),
    )
    asr: NonBlankStr | None = Field(
        default=None,
        description=(
            "The speech recognizer, by the name it is defined under in "
            "providers.asr. An agent that leaves it unset inherits the "
            "agent_defaults entry."
        ),
    )
    tts: NonBlankStr | None = Field(
        default=None,
        description=(
            "The voice, by the name it is defined under in providers.tts. An agent "
            "that leaves it unset inherits the agent_defaults entry."
        ),
    )
    vad: NonBlankStr | None = Field(
        default=None,
        description=(
            "The voice activity detector, by the name it is defined under in "
            "providers.vad. An agent that leaves it unset inherits the "
            "agent_defaults entry."
        ),
    )

    # The MCP servers this agent talks to. None means inherit; a list
    # replaces rather than extends the inherited one, like the stage
    # fields, so an agent naming an empty list opts out of tools.
    mcp: list[NonBlankStr] | None = Field(
        default=None,
        description=(
            "The MCP servers whose tools this layer offers the model, by entry "
            "name. Unset inherits the agent_defaults list; naming a list replaces "
            "the inherited one rather than extending it, so an empty list opts an "
            "agent out of the tools its siblings have."
        ),
    )

    # Latency masking with a pre-synthesized filler clip. None means
    # inherit; a section replaces the inherited one wholly, like the
    # stage fields, so an agent naming its own phrases names all of
    # them, and `filler: {enabled: false}` opts an agent out.
    filler: FillerConfig | None = Field(
        default=None,
        description=(
            "Latency masking with a pre-synthesized filled pause. Unset inherits the "
            "agent_defaults section; naming one replaces it wholly rather than "
            "merging with it, so `filler: {enabled: false}` opts an agent out."
        ),
    )


class AgentConfig(AgentDefaults):
    """One persona: a prompt, plus whichever stages it overrides."""

    prompt: str = Field(
        default="",
        description=(
            "The persona instruction this agent replies under, sent as the system "
            "prompt on every turn. State the reply language explicitly: a model "
            "otherwise picks one by its training bias."
        ),
    )


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


# What each domain section is, written once because two models carry
# these fields: the composed Config the server boots from, and the
# DomainConfig the repository loads a database into. The generated
# reference and the CLI help both read them from whichever model they
# render, so a single source is what keeps the two renderings equal.
DOMAIN_DESCRIPTIONS: dict[str, str] = {
    "providers": (
        "The provider entries agents reference, one group per pipeline stage "
        "(llm, asr, tts, vad)."
    ),
    "mcp_servers": (
        "The MCP servers agents may be given tools from, keyed by entry name. The "
        "name becomes the prefix its tools are offered to the model under "
        "(home__turn_on_light), so it must match [A-Za-z0-9_-]+ and must not be one "
        "of the names the merged tool list already uses."
    ),
    "agent_defaults": (
        "What every agent uses unless it names something else. One entry for the "
        "whole deployment, and deliberately without a prompt: a prompt is what "
        "makes an agent that agent, so inheriting one silently would make two "
        "agents the same one."
    ),
    "agents": (
        "The personas this deployment serves, keyed by agent name. An agent is a "
        "prompt plus whichever stages it overrides, and every stage must resolve to "
        "a provider, here or in agent_defaults, for the server to start."
    ),
    "devices": (
        "Which agents each device may talk to, keyed by MAC address as the "
        "Device-Id header sends it. The first name in a list is the agent a "
        "conversation starts on and the rest are the ones it may be switched to."
    ),
    "default_agent": (
        "The agent an unknown device reaches. Leaving it unset makes the devices "
        "map an allowlist: a device with no binding is then turned away."
    ),
}

# The six sections that live in the database rather than in the file, in
# the order they are written and read. Derived from the descriptions
# above rather than restated, so a section cannot be documented and then
# forgotten by the composition (or the other way round).
DOMAIN_KEYS: tuple[str, ...] = tuple(DOMAIN_DESCRIPTIONS)


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


def domain_fields(snapshot: DomainSnapshot) -> dict[str, object]:
    """The six domain sections of a snapshot, by name.

    What composition passes to `Config`: the models themselves rather
    than a dump of them, because a round trip through a dump would set
    fields the entity deliberately left unset (an McpServerConfig reads
    `model_fields_set` to tell "my headers are ignored" from "my headers
    are wrong")."""
    return {key: getattr(snapshot, key) for key in DOMAIN_KEYS}


# The YAML file the settings source should read, set by the loader around
# instantiation. pydantic-settings has no init kwarg for a runtime-chosen
# path yet (pydantic-settings#259).
yaml_file_var: ContextVar[Path | None] = ContextVar("samtal_yaml_file", default=None)


class FileConfig(BaseSettings):
    """The half of the configuration the YAML file holds.

    `server` and `memory` only: the domain half moved to the database,
    and a file that still names it is refused by the loader with the
    command that writes it instead. The SAMTAL_ environment overrides are
    unchanged for what is left (SAMTAL_SERVER__PORT keeps working), which
    is why this is still a settings model and `Config` is not.
    """

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
    memory: MemoryConfig | None = None


class Config(BaseModel):
    """The whole configuration one server boots on: the file half plus
    the domain half the database holds.

    Composed rather than loaded, since its two halves come from two
    places, and it keeps its name, its attribute paths, its helper
    methods and its boot-time validator so that everything downstream of
    it reads the configuration exactly as it did when one file held all
    of it.
    """

    model_config = ConfigDict(extra="forbid")

    server: ServerConfig = Field(default_factory=ServerConfig)
    providers: ProvidersConfig = Field(
        default_factory=ProvidersConfig, description=DOMAIN_DESCRIPTIONS["providers"]
    )
    # Named like providers, and referenced by agents the same way. The
    # entry name becomes the prefix its tools are offered under.
    mcp_servers: dict[NonBlankStr, McpServerConfig] = Field(
        default_factory=dict, description=DOMAIN_DESCRIPTIONS["mcp_servers"]
    )
    memory: MemoryConfig | None = None
    agent_defaults: AgentDefaults = Field(
        default_factory=AgentDefaults, description=DOMAIN_DESCRIPTIONS["agent_defaults"]
    )
    agents: dict[NonBlankStr, AgentConfig] = Field(
        default_factory=dict, description=DOMAIN_DESCRIPTIONS["agents"]
    )
    # One device may be bound to several agents; the value is written as a
    # single name or a list, and always stored as a list.
    devices: dict[str, list[NonBlankStr]] = Field(
        default_factory=dict, description=DOMAIN_DESCRIPTIONS["devices"]
    )
    default_agent: NonBlankStr | None = Field(
        default=None, description=DOMAIN_DESCRIPTIONS["default_agent"]
    )

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
