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
from collections.abc import Iterable, Mapping, Sequence
from contextvars import ContextVar
from pathlib import Path
from typing import Annotated, Literal, NamedTuple, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

# The block shape the assembler owns, imported rather than restated for
# the reason `tools/mcp.py` imports `Guidance` from there: what an
# agent's fragments are is this module's business, and what a prompt
# does with them is that one's. It is a leaf import, over a module that
# holds pure text functions and reads only `tools.names`.
from samtal_server.runtime.prompt import Fragment
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

# Where the configuration API is mounted on the server's own port. It
# lives here, and not only in config/api.py, because ota_path's
# validator has to reserve it: the OTA route is registered before the
# API is mounted, so a route matching under this prefix would be found
# first and would answer a request the API's token gate never saw.
API_MOUNT_PATH = "/api"

# Where the onboarding short route is served: /x/<key>/, the alias of the
# OTA endpoint an operator types into a device's captive portal. It lives
# here for the same reason the API mount path does: ota_path's validator
# has to reserve it, since a configured OTA path under this prefix would
# collide with the onboarding router.
ONBOARDING_MOUNT_PATH = "/x"

# The alphabet a pinned onboarding key may be written in, and how long it
# is. Base32 because A-Z2-7 has no 0/O and no 1/I/l, the pairs a person
# misreads off a small display, and eight characters because that is what
# the derivation truncates to.
_ONBOARDING_KEY_RE = re.compile(r"^[A-Z2-7]{8}$")

# The logging level names, most to least verbose. NOTSET is left out: on
# the root logger it means WARNING, which is not what writing it says.
_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

# Identifiers (provider names, agent names, references between them) must
# survive stripping with at least one character.
NonBlankStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def _check_written_text(value: str) -> str:
    """Text an operator wrote for the model to read, checked without
    being changed.

    Deliberately not `NonBlankStr`, which strips: that is right for an
    identifier and wrong for prompt text. Leading indentation and a
    trailing blank line are things somebody wrote on purpose, and what
    this field promises is that the model is given exactly what was
    written. So the emptiness check runs on a stripped copy and the
    original is returned untouched.

    The refusal names the rule and not the value, the boundary's rule
    everywhere else: a fragment refused here has been validated by
    nobody yet.
    """
    if not value.strip():
        raise ValueError(
            "this field holds only whitespace, which the model would read as nothing; "
            "write the guidance, or leave the key out"
        )
    return value


# Text stored and injected byte for byte: non-blank, and otherwise
# exactly as it was written.
VerbatimStr = Annotated[str, AfterValidator(_check_written_text)]


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


class OnboardingConfig(BaseModel):
    """The short onboarding path, /x/<key>/, an alias of the OTA endpoint.

    Onboarding a stock board means typing its backend URL into a captive
    portal on a phone, with no feedback on a typo, so the string has to
    be short and its alphabet unambiguous. The key is derived from the
    device-auth secret the deployment already has, never stored and never
    written here: it is stable across restarts and rotates only when the
    secret does.

    On by default. The legacy path keeps working beside it, and a
    deployment that wants only the legacy one turns this off.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True

    # The derived key, pinned. Its one use is a secret rotation: the
    # derivation follows the secret, so pinning the previous key keeps
    # provisioned boards reaching the same URL while the new secret takes
    # over everything else. Left unset, which is the normal case, the key
    # is derived and nothing about it is stored.
    key: str | None = None

    @field_validator("key")
    @classmethod
    def _check_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        key = value.strip().upper()
        if not _ONBOARDING_KEY_RE.match(key):
            # Not quoted back: a pinned key is a path segment that stands
            # in front of the token issuer, so it belongs in no message
            # this refusal can reach.
            raise ValueError(
                "this key must be eight base32 characters (A-Z and 2-7), the shape "
                "the derivation produces; the value is not quoted back here, since "
                "it is the segment the OTA endpoint is served under. Leave it unset "
                "to derive it from the device-auth secret, and pin it only to keep a "
                "previous key alive across a secret rotation"
            )
        return key


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


class ConversationsConfig(BaseModel):
    """Recording what was said into a database that can be queried.

    Off by default and off unless said otherwise, the same shape as
    `capture.enabled` and for the same kind of reason: this keeps
    conversation text on disk, so nothing here can turn it on by
    accident. The section has to exist and the flag has to say so.

    No directory of its own. `conversations.db` lands beside samtal.db in
    `database.dir` above, because it is the same data volume, the same
    backup and the same access control, and a second path key would be a
    second thing to point somewhere writable.

    The two storage switches under the flag are independent, and every
    combination is a supported configuration: metrics without text is the
    stricter setting, and text without metrics keeps the conversation
    record without the behavioural telemetry. They are deployment-wide,
    which is the only policy layer this release has; per-user and
    per-agent controls are a stricter filter above this one when they
    arrive, never a replacement for it (#120).
    """

    model_config = ConfigDict(extra="forbid")

    # The switch. Off by default, so a section left in a config file
    # records nothing until somebody says it should.
    enabled: bool = False

    # Store the structured events and every measured number (durations,
    # token counts, timings). With this off, no events rows land and the
    # numeric columns on turns and tool invocations are null.
    metrics: bool = True

    # Store conversation text, and tool names, arguments and results.
    # With this off, rows still land with the content columns null, so
    # timing analysis survives the stricter setting.
    text: bool = True

    # Prune sessions older than this many days, whole sessions at a time.
    # 0 keeps everything, which is a deliberate choice rather than a
    # default: a store with no policy retains forever. The same number
    # the store itself defaults to, which its own tests pin.
    retention_days: int = Field(default=90, ge=0)


def url_problem(url: str, schemes: tuple[str, ...]) -> str | None:
    """What makes this URL unusable as an address a device is given, or
    None when nothing does.

    The answer is never the value itself: both callers render a refusal
    that must not repeat what it was handed. That is also why the parse
    happens here rather than at each call site. `urlsplit` defers the
    IPv6 bracket check and the port check to parse and attribute access
    respectively, and the ValueError each raises quotes the input in its
    own message, so an unhandled one would print exactly what the
    refusal is careful not to.

    Reading the port here is what keeps the rest of the server total: a
    value that parses at load is a value the banner can take a hostname
    and a port from without raising.
    """
    try:
        parts = urlsplit(url)
        netloc, hostname = parts.netloc, parts.hostname
    except ValueError:
        return (
            "it cannot be read as a URL; check the host, and the brackets around it "
            "if it is an IPv6 address"
        )
    if parts.scheme not in schemes:
        return "it must start with " + " or ".join(f"{scheme}://" for scheme in schemes)
    if not hostname:
        return "it names no host"
    if "@" in netloc:
        return (
            "it carries a user:password, which cannot be written here: this value is "
            "printed back to whoever asks the server what it serves"
        )
    try:
        _ = parts.port
    except ValueError:
        return "its port is not a whole number in 1 to 65535"
    return None


class ServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = "0.0.0.0"
    port: int = Field(default=8003, ge=1, le=65535)

    # The websocket URL handed to devices by the OTA endpoint. Left unset it
    # is derived from the address the device reached the OTA endpoint on,
    # which is right for a plain LAN deployment; set it explicitly when the
    # server sits behind a proxy or a name the request headers do not carry.
    websocket_url: str | None = None

    # The origin devices reach this server on, written exactly as a person
    # would type it: scheme, host, optional port, and an optional path
    # prefix when a proxy serves the server under one. Its only job is to
    # say the onboarding URL out loud at startup and on the OTA GET.
    # Unset, the origin is derived from websocket_url, and failing that
    # guessed from the listen address, which is a guess that says so.
    public_url: str | None = None

    # Where the OTA endpoint is served. It is the token issuer, so it cannot
    # itself require a token; an operator exposing the server publicly hides
    # it behind a long random segment (/xiaozhi/ota/8f3a.../) and writes that
    # URL into the device's NVS. The websocket path is fixed: the token is
    # what protects it.
    #
    # Null unmounts it, which a deployment does once every board it serves
    # has been moved to the onboarding path below.
    ota_path: str | None = "/xiaozhi/ota/"

    # The short onboarding alias of the OTA endpoint. On by default.
    onboarding: OnboardingConfig = Field(default_factory=OnboardingConfig)

    # Binary protocol version advertised to devices. The firmware defaults to
    # 1 (bare Opus frames); 2 and 3 add timestamp headers.
    protocol_version: int = Field(default=1, ge=1, le=3)

    # Minutes east of UTC, sent so the device can set its clock to local time.
    # Left unset the server's own current offset is used.
    timezone_offset_minutes: int | None = Field(default=None, ge=-1440, le=1440)

    # How the server logs. "text" is the human format; "json" is one object
    # per line, which is what the container image defaults to, and what a
    # collector groups by session to measure the pipeline. The records are
    # metadata; what was said is in the conversation store.
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

    # Absent, or present with enabled off, means nothing is written to
    # the conversation store and no conversations.db is created. Absent
    # is the default. An existing file is still migrated at boot, because
    # a deployment that recorded last month and records nothing today
    # still has to be able to read what it kept.
    conversations: ConversationsConfig | None = None

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
    def _check_ota_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        path = value.strip()
        if not path.startswith("/") or not path.endswith("/"):
            raise ValueError(
                f'"{value}" is not a usable OTA path; it must start and end with '
                f'"/", for example /xiaozhi/ota/ or /xiaozhi/ota/8f3a9c2b.../'
            )
        # Never quoting the value here, unlike the refusal above: an
        # operator who exposes the server publicly hides the OTA
        # endpoint behind a long random segment, and that segment is
        # the closest thing this key has to a secret.
        if path.startswith(f"{API_MOUNT_PATH}/"):
            raise ValueError(
                f"{API_MOUNT_PATH}/ is reserved for the configuration API, so the OTA "
                f"endpoint cannot be served there or anywhere under it: the OTA route "
                f"is registered before the API is mounted, so it would be found first "
                f"and would answer a request the API's token gate never saw. Serve it "
                f"somewhere else, for example /xiaozhi/ota/"
            )
        if path.startswith(f"{ONBOARDING_MOUNT_PATH}/"):
            raise ValueError(
                f"{ONBOARDING_MOUNT_PATH}/ is reserved for the short onboarding route, "
                f"which serves the same endpoint at {ONBOARDING_MOUNT_PATH}/<key>/, so "
                f"the OTA endpoint cannot also be served there or anywhere under it. "
                f"Serve it somewhere else, for example /xiaozhi/ota/"
            )
        return path

    @field_validator("websocket_url")
    @classmethod
    def _check_websocket_url(cls, value: str | None) -> str | None:
        """A ws or wss URL, with a host, a readable port, and no
        credentials.

        This value is handed to every device and rendered verbatim by the
        OTA endpoint's own GET, which anyone holding the onboarding URL
        can reach, so a `user:password@host` written here would be read
        back out by whoever asks. Refused rather than stripped, and never
        quoted back: the same posture public_url holds.

        The host and port are checked here rather than where they are
        read, because the startup banner derives its origin from them: a
        value that only fails at that point would fail as a traceback
        during boot instead of as a configuration refusal.
        """
        if value is None:
            return value
        url = value.strip()
        problem = url_problem(url, ("ws", "wss"))
        if problem is not None:
            raise ValueError(
                f"this is not a usable websocket URL: {problem}. Write the address "
                f"devices should connect to, for example "
                f"ws://192.168.1.10:8003/xiaozhi/v1/ or "
                f"wss://voice.example/xiaozhi/v1/; the value is not quoted back here, "
                f"since it may carry a credential"
            )
        return url

    @field_validator("public_url")
    @classmethod
    def _check_public_url(cls, value: str | None) -> str | None:
        """An http or https origin, optionally with a path prefix, and
        nothing that a log line must not carry.

        This one value is printed at startup and handed to a person to
        type, so userinfo is refused rather than stripped: a URL carrying
        a password is a mistake worth naming, and printing it with the
        password quietly removed would hide it. The rejected value is
        never quoted back, for the same reason. The shared check is what
        refuses a host that cannot be read and a port that is not a
        number, both of which would otherwise be republished verbatim by
        the banner and by the OTA GET.
        """
        if value is None:
            return None
        url = value.strip()
        problem = url_problem(url, ("http", "https"))
        if problem is None:
            # Safe to parse: the shared check answers None only for a
            # value `urlsplit` read without raising.
            parts = urlsplit(url)
            if parts.query or parts.fragment:
                problem = (
                    "it carries a query or a fragment, and this is an origin with an "
                    "optional path prefix rather than a whole URL"
                )
        if problem is not None:
            raise ValueError(
                f"this is not a usable public URL: {problem}. Write the origin devices "
                f"reach this server on, for example https://voice.example or "
                f"https://voice.example/samtal; the value is not quoted back here, "
                f"since it may carry a credential"
            )
        # The trailing slash goes, so the paths appended to this (the
        # onboarding route, the OTA path) join it without doubling it.
        return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))

    @model_validator(mode="after")
    def _check_something_is_discoverable(self) -> "ServerConfig":
        """A server no device can reach its configuration on is a
        misconfiguration rather than a choice: unmounting the legacy path
        is how a deployment moves to the short one, so unmounting it with
        the short one off leaves nothing serving."""
        if self.ota_path is None and not self.onboarding.enabled:
            raise ValueError(
                "server.ota_path is null and server.onboarding.enabled is false, so "
                "no device could fetch its configuration from this server at all. "
                "Keep one of the two: an ota_path for the boards already provisioned "
                "with it, or onboarding enabled for the short /x/<key>/ route"
            )
        return self


class FieldProblem(NamedTuple):
    """One thing wrong with one field of a fragment, said where the
    field is known.

    Declared here, beside the validators that produce it, because the
    only place that knows which field a rule is about is the rule. A
    model-level validator is located by pydantic at the model, so a
    refusal that named the field only inside its sentence would leave a
    reader, and a form, to parse prose for it. This is that fact carried
    as data instead: `loader.ConfigError` takes a tuple of these and the
    API answers them as its problem document's `errors`.

    `path` is an RFC 6901 JSON Pointer into the fragment the validator
    was handed, so the empty string is the fragment itself and a key
    holding a dot or a slash is unambiguous, which a dotted spelling
    cannot be. `message` is the sentence, which is the same text the
    refusal's own prose carries for this problem: one computation, two
    renderings, so the two cannot come to disagree.

    It never carries a value. Every message here names a path and a
    rule, because a key that fails one of these rules most likely holds
    the credential itself.
    """

    path: str
    message: str


class FieldProblemsError(ValueError):
    """The problems one validator found, raised as one exception.

    A ValueError because that is what pydantic collects from a
    validator, and a subclass because that is what lets the walk over
    `ValidationError.errors()` recognize its own structure again: the
    exception object travels in the error's `ctx`, so the fields survive
    the trip that flattens everything else to a location and a sentence.

    Its `str` is the messages one per line, which is what pydantic puts
    in `msg` and what every renderer that has only the sentence (the
    boot path's, in `loader.py`) prints. A validator that found three
    problems therefore reads as three lines wherever it is rendered.
    """

    def __init__(self, problems: Iterable[FieldProblem]) -> None:
        self.problems: tuple[FieldProblem, ...] = tuple(problems)
        super().__init__("\n".join(problem.message for problem in self.problems))


def json_pointer(segments: Iterable[object]) -> str:
    """The RFC 6901 pointer addressing a path of keys and array
    positions, from the segments themselves.

    Escaping is the whole reason this exists rather than a join: `~`
    becomes `~0` and `/` becomes `~1`, in that order, so a key named
    `a/b` is one segment rather than two and a key named `a.b` is not
    nesting. An empty sequence is the empty pointer, which addresses the
    whole fragment.
    """
    return "".join(
        "/" + str(segment).replace("~", "~0").replace("/", "~1") for segment in segments
    )


def is_secret_option(name: str) -> bool:
    """Whether an option name is secret-shaped.

    One rule, three readers: it is what makes an inline value in a
    fragment an error, what decides which option names are credential
    slots a secret may be stored under, and what the display path masks
    the value of."""
    lowered = name.lower()
    return any(fragment in lowered for fragment in _SECRET_KEY_FRAGMENTS)


def url_credential(value: object) -> str | None:
    """Which credential a URL-shaped value carries, or None.

    The one shape that holds a secret without a secret-shaped name to
    give it away: `base_url: https://user:password@host/v1` names nothing
    suspicious, passes every rule above, and is stored, displayed and
    recorded verbatim. So the value is examined rather than its key, at
    every depth, and only a value that really is a URL (a scheme and a
    host) is examined at all, which keeps ordinary prose holding an
    address out of it.

    Two answers, because they are two different mistakes: `userinfo` is
    the credential written before the host, and `query` is a credential
    passed as a parameter, which is the other place vendors accept one.
    """
    if not isinstance(value, str) or "://" not in value:
        return None
    try:
        parts = urlsplit(value)
    except ValueError:
        # Unreadable as a URL, so it is not one of these; whatever else
        # is wrong with it is not this rule's business.
        return None
    if not parts.scheme or not parts.netloc:
        return None
    if "@" in parts.netloc:
        return "userinfo"
    if any(is_secret_option(key) for key, _ in parse_qsl(parts.query, keep_blank_values=True)):
        return "query"
    return None


def without_url_credential(value: str) -> str:
    """The same URL with what `url_credential` finds taken out.

    Defence in depth rather than the rule: the write path refuses such a
    URL, and this is what keeps a value that arrived before that rule, or
    through an environment override, out of a record made from it. The
    host is kept exactly as written (brackets, port and all) by cutting
    at the last `@` rather than by reassembling it from parts.
    """
    if url_credential(value) is None:
        return value
    parts = urlsplit(value)
    kept = [
        (key, held)
        for key, held in parse_qsl(parts.query, keep_blank_values=True)
        if not is_secret_option(key)
    ]
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc.rpartition("@")[2],
            parts.path,
            urlencode(kept),
            parts.fragment,
        )
    )


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


def check_no_inline_secrets(path: str, value: object) -> None:
    """A secret-shaped key holds no value, at any depth inside a
    provider's options.

    Depth is the point. A provider entry passes every option beyond the
    declared ones through to its implementation, so an option can be a
    structure, and `connection: {api_key: ...}` is as ordinary a shape
    to write as `api_key: ...` is. Checking only the top level would
    accept the nested one, store it, and read it back verbatim on every
    display path, which is exactly what the flat rule exists to prevent.

    Refusals name the dotted path and never the value: a key that fails
    either check most likely holds the credential itself. The sentence
    keeps the dotted spelling, which is how an operator reads their own
    file; the `FieldProblem` beside it carries the same place as a JSON
    Pointer, which is what a reader can act on. Both are derived from
    the segments walked to here, so they cannot name different keys.
    """
    _check_no_inline_secrets((path,), value)


def _check_no_inline_secrets(segments: tuple[object, ...], value: object) -> None:
    """The walk itself, carrying the segments rather than a joined path
    so that the two spellings above stay one fact."""
    path = ".".join(str(segment) for segment in segments)
    leaf = str(segments[-1])
    if leaf.lower().endswith("_env"):
        if value is not None and not is_env_name(value):
            raise FieldProblemsError(
                [
                    FieldProblem(
                        json_pointer(segments),
                        f'"{path}" must hold the name of an environment variable, and '
                        f"what it holds does not look like one; a pasted value belongs "
                        f"nowhere in this file, so name the variable holding it, for "
                        f"example {path}: MY_PROVIDER_KEY",
                    )
                ]
            )
        return
    if is_secret_option(leaf):
        raise FieldProblemsError(
            [
                FieldProblem(
                    json_pointer(segments),
                    f'"{path}" looks like an inline secret, which is not allowed; '
                    f"reference an environment variable instead, for example "
                    f"{path}_env: MY_PROVIDER_{leaf.upper()}",
                )
            ]
        )
    if isinstance(value, Mapping):
        for key, nested in value.items():
            _check_no_inline_secrets((*segments, key), nested)
    elif isinstance(value, (list, tuple)):
        for position, item in enumerate(value):
            _check_no_inline_secrets((*segments, position), item)


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
        check_no_inline_secrets("api_key_env", self.api_key_env)
        for key, value in (self.model_extra or {}).items():
            check_no_inline_secrets(key, value)
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
            "transport is an error rather than a silently ignored key. An "
            "SSE-only endpoint is configured as a stdio server behind an "
            "mcp-proxy bridge, since the specification deprecated SSE in favour "
            "of streamable_http and there is no native arm for it here."
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

    # What the operator wants the model to know about using this
    # server's tools, injected into the system prompt of every agent
    # granted the entry. Verbatim, and whole-entry rather than per-tool
    # (#122).
    instructions: VerbatimStr | None = Field(
        default=None,
        description=(
            "Guidance for the model about using this server's tools, injected into the "
            "system prompt of every agent this entry is granted to, under a heading "
            "naming the prefix its tools carry. It is written for the whole entry "
            "rather than per tool, so guidance about a tool an agent's allow list "
            "withholds is noise the operator avoids by writing about the granted "
            "surface. The grant is the whole condition: it is injected whether or not "
            "the server is connected and whatever its tools turn out to be, and an "
            "agent with `mcp: []` never sees it. It is stored and injected as written, "
            "its indentation and its own blank lines included; the only bytes trimmed "
            "are whitespace at the two ends of the whole assembled prompt, which is "
            "also what the inspection surface reports. Editing it does not "
            "restart the connection, so a reload reports the entry as `unchanged`, and "
            "the new text reaches a conversation at its next activation: a new session "
            "or an agent switch, never a reply of a session already running."
        ),
    )

    # The first of the two channels a server ships guidance in: the
    # `instructions` of its initialize result. Off by default, because
    # a third party's words steering the agent is a decision the
    # operator takes per entry and per channel (#122).
    use_server_instructions: bool = Field(
        default=False,
        description=(
            "Whether to inject the guidance this server ships about itself, the "
            "`instructions` field of its initialize result, into the system prompt of "
            "every agent this entry is granted to. Off by default, and deliberately: "
            "the entry's own `instructions` is what your operator wrote, while this is "
            "a third party's text steering the agent, so consuming it is an explicit "
            "opt-in taken per entry and per channel. What a server ships is captured on "
            "every connect whatever this says, so turning it on applies at the next "
            "reload without restarting the connection, and turning it off stops the "
            "injection at the next activation. A block longer than 4000 characters is "
            "skipped whole rather than truncated. The block sits after this entry's own "
            "guidance, and `samtal-server config prompt <agent>` reports it under "
            "`server_instructions:<entry>`, so an operator can see whose words they are "
            "reading."
        ),
    )

    # The second channel: the prompts the server publishes, named one
    # by one because the specification defines them as user-controlled
    # templates and a server may publish dozens.
    # Typed with the verbatim string rather than NonBlankStr, which
    # strips: a published prompt's name is an exact identifier the
    # server chose, and a stripped copy of it addresses a different
    # prompt or none at all.
    inject_prompts: list[VerbatimStr] | None = Field(
        default=None,
        description=(
            "The prompts this server publishes that are injected into the system prompt "
            "of every agent this entry is granted to, each by the name the server lists "
            "it under and in the order listed here. Unset means none, which is the "
            "default: a third party's text steering the agent is an opt-in per entry "
            "and per channel, and the specification defines prompts as user-controlled "
            "templates, so the operator who read the server's documentation names the "
            "ones that are standing guidance rather than invocable templates. Every "
            "name is validated against the server's own paginated prompt listing before "
            "anything is fetched, and a name the listing does not carry, one whose "
            "prompt declares required arguments, one that renders anything but text, "
            "and a rendered block longer than 4000 characters are each skipped with a "
            "warning naming this entry and the position in this list, never the name "
            "itself, since a server-chosen name is not this server's to print. Editing "
            "this list changes what a connect fetches, so unlike the other two prompt "
            "fields it restarts the connection when a reload applies it. A name listed "
            "twice is refused. Each name is stored and looked up exactly as written, "
            "surrounding whitespace included, because it is an identifier the server "
            "chose rather than a word this server may tidy: a stripped copy of it "
            "addresses a different prompt or none at all."
        ),
    )

    @field_validator("inject_prompts")
    @classmethod
    def _check_inject_prompts(cls, value: list[str] | None) -> list[str] | None:
        """One entry per prompt. Naming a prompt twice would fetch it
        twice and inject it twice, which is a thing to say once if it is
        meant at all.

        The refusal points at positions and never at what is in them, the
        rule this list follows everywhere else: a prompt name is a
        server-chosen string the operator copied, so nothing bounds what
        it holds, and this sentence leaves the boundary as a printed CLI
        line, an HTTP 422 body and a boot log.
        """
        if value is None:
            return value
        repeated = _repeated_positions(value)
        if repeated:
            raise ValueError(
                f"inject_prompts names one prompt at more than one position "
                f"({repeated}); list each prompt once"
            )
        return value

    @model_validator(mode="after")
    def _check_transport_fields(self) -> "McpServerConfig":
        stdio_only = ("command", "args", "env")
        http_only = ("url", "headers")
        if self.transport == "stdio":
            required, foreign = "command", http_only
        else:
            required, foreign = "url", stdio_only

        problems: list[FieldProblem] = []
        value = getattr(self, required)
        if value is None or not str(value).strip():
            problems.append(
                FieldProblem(
                    json_pointer((required,)),
                    f'transport "{self.transport}" needs "{required}"',
                )
            )
        named = [field for field in foreign if field in self.model_fields_set]
        if named:
            # The whole fragment, because this problem is about a
            # combination rather than about one key: the fix is either
            # the transport or the fields, and the sentence names them
            # all. The empty pointer is what RFC 6901 gives that.
            problems.append(
                FieldProblem(
                    "",
                    f'transport "{self.transport}" has no {", ".join(named)}; '
                    f"that belongs to the other transport",
                )
            )
        problems += self._secret_problems()
        if problems:
            # One entry per problem, and therefore one line per problem
            # wherever this is rendered. It used to be one `; `-joined
            # line, which is a sentence no reader can decompose back
            # into the fields it names (#192).
            raise FieldProblemsError(problems)
        return self

    def _secret_problems(self) -> list[FieldProblem]:
        """Secret-bearing env and header keys must name an environment
        variable, the same rule that keeps provider secrets out of the
        configuration file."""
        problems: list[FieldProblem] = []
        for group, values in (("env", self.env), ("headers", self.headers)):
            for key, value in values.items():
                if not is_mcp_secret_key(key):
                    continue
                if _env_reference(value) is None:
                    problems.append(
                        FieldProblem(
                            json_pointer((group, key)),
                            f"{group}.{key} looks like an inline secret, which is not "
                            f"allowed; reference an environment variable instead, for "
                            f"example {key}: $MY_SERVER_SECRET",
                        )
                    )
        return problems


class PromptFragmentConfig(BaseModel):
    """One named block of prompt text, shared by the agents that include
    it.

    A mapping with one field rather than a bare string. Every entity in
    this configuration travels the same path (a stored row, a read
    envelope, a written fragment), and all three of those want a mapping;
    a one-field mapping also leaves room for a second field later without
    changing the shape a client writes, which is why a grant took its
    object form too.
    """

    model_config = ConfigDict(extra="forbid")

    text: VerbatimStr = Field(
        description=(
            "The text injected into the system prompt of every agent whose "
            "prompt_includes names this fragment, as written: its indentation and its "
            "own blank lines are part of it, and nothing is added around it, not even a "
            "heading, since this is prompt text the operator wrote and a heading would "
            "editorialize. The only bytes trimmed are whitespace at the two ends of the "
            "whole assembled prompt, which is also what the inspection surface reports. "
            "It sits after the agent's own prompt and before any MCP guidance, in the "
            "order the including layer lists it. There is no length cap: what each "
            "block costs is reported by `samtal-server config prompt <agent>`, and the "
            "operator is the one who knows what their model tolerates."
        )
    )


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
            # The pointer names the field to fill in, under whatever
            # layer holds this filler block; the sentence keeps naming
            # the switch that made it required.
            raise FieldProblemsError(
                [
                    FieldProblem(
                        json_pointer(("phrases",)),
                        "filler.enabled is on with no phrases; add at least one, "
                        'for example "Hmm, let me see..."',
                    )
                ]
            )
        return self


class McpGrant(BaseModel):
    """One `mcp` list entry in its object form: a server, and which of
    its tools the layer may reach.

    A second spelling of the same list entry rather than a different
    thing. A plain string names the whole server, and this names part of
    one, so an agent that should switch the lights but not unlock the
    door does not need a second server to say it in.

    Tools are named by the published name without its entry prefix
    (`turn_on_light` grants `home__turn_on_light`), matched exactly. That
    identifier is this application's own: it has been through the
    publishing rule, it is what `samtal-server config status` shows and
    what the model calls, so an operator writes down the name they read.
    What a server listed before the rule got to it never appears on a
    samtal surface, and cannot be granted by.
    """

    model_config = ConfigDict(extra="forbid")

    server: NonBlankStr = Field(
        description=(
            "The MCP server this grant is about, by the name it is defined under in "
            "mcp_servers."
        )
    )
    # The two rules the validator below enforces are declared on the
    # type as well, where a client generator and a schema validator read
    # them: a contract looser than the code is one a client builds the
    # wrong request from. They are not pydantic constraints, because a
    # constraint would answer the empty list with its own sentence and
    # the one below is the one that says how to grant nothing.
    tools: (
        Annotated[
            list[NonBlankStr],
            Field(json_schema_extra={"minItems": 1, "uniqueItems": True}),
        ]
        | None
    ) = Field(
        default=None,
        description=(
            "Which of that server's tools this layer may reach, by the published name "
            "without its entry prefix (turn_on_light for home__turn_on_light), which "
            "is the name `samtal-server config status` shows. Leaving it out grants "
            "the whole server, exactly as naming the server as a plain string does. A "
            "name that matches nothing the server published is not an error at write "
            "time, since only a live connection knows the list; it is logged when the "
            "server publishes and visible under grants on the status surface."
        ),
    )

    @field_validator("tools")
    @classmethod
    def _check_tools(cls, value: list[str] | None) -> list[str] | None:
        """An allow list that allows nothing, and one that says a name
        twice, are both spellings of something else said plainly.

        Both refusals point at positions rather than at what is in them.
        A rejected fragment may be a pasted credential, and these
        sentences travel out through the store as a CLI line and an HTTP
        422 body; the position says which entry to look at without this
        server repeating a word of it.
        """
        if value is None:
            return value
        if not value:
            raise ValueError(
                'tools is empty, which grants nothing at all; leave "tools" out to '
                'grant the whole server, or write "mcp: []" on the layer to give it '
                "no servers"
            )
        repeated = _repeated_positions(value)
        if repeated:
            raise ValueError(
                f"tools names one tool at more than one position ({repeated}); list "
                f"each tool once"
            )
        return value


def _repeated_positions(values: Sequence[str]) -> str:
    """Where a list says the same thing twice, as positions counted from
    one and never as the thing itself."""
    return ", ".join(
        str(position)
        for position, value in enumerate(values, start=1)
        if values.count(value) > 1
    )


# What a grant's own refusal may name. A location inside a declared
# model is a field this repository chose, so it is safe to print; a
# location for a key the model does not declare is that key, which came
# out of the request and may be anything at all.
_GRANT_FIELDS = ("server", "tools")
_UNRECOGNIZED_KEY = "an unrecognized key"


def read_mcp_entry(index: int, item: object) -> object:
    """One written `mcp` list entry, with the object form parsed here
    rather than by the field's union.

    The union would report both of its branches, so the first thing an
    operator read about a grant with an empty `tools` was that a mapping
    is not a string, and the sentence about the mistake came second.
    Parsing the mapping here makes the refusal the grant's own. Anything
    that is neither form is passed through for the union to report, so
    a number in the list still reads as one.

    What the refusal may say is bounded the way the rest of this
    boundary is bounded: the position in the list, the declared field
    that failed, and the rule it failed. Never a value, and never a key
    the caller invented, because this sentence is what the CLI prints
    and what the API answers 422 with, and the fragment it is about may
    be a pasted credential.
    """
    if isinstance(item, str | McpGrant) or not isinstance(item, Mapping):
        return item
    problem: str
    try:
        return McpGrant.model_validate(dict(item))
    except ValidationError as exc:
        problem = "; ".join(
            f"{where}: {rule}" if (where := _grant_location(error["loc"])) else rule
            for error in exc.errors()
            if (rule := error["msg"].removeprefix("Value error, "))
        )
    raise ValueError(f"entry {index + 1}: {problem}")


def _grant_location(location: Sequence[object]) -> str:
    """Where inside a grant something failed, made of declared field
    names and positions only."""
    return ".".join(
        str(part)
        if isinstance(part, int) or part in _GRANT_FIELDS
        else _UNRECOGNIZED_KEY
        for part in location
    )


def as_mcp_grant(entry: "str | McpGrant") -> McpGrant:
    """One `mcp` list entry as the grant it means. The string form is
    the whole server, which is a grant with no allow list."""
    return McpGrant(server=entry) if isinstance(entry, str) else entry


def mcp_entry_fragment(entry: "str | McpGrant") -> str | dict[str, object]:
    """One `mcp` list entry in the shape it was written in, which is
    what a read of it has to show and what a row has to hold.

    Each form serializes as itself and invents no keys: a string stays a
    string, so every row written before the object form existed loads
    and is written back unchanged, and an object that granted the whole
    server does not grow a `tools: null` it never had.
    """
    if isinstance(entry, str):
        return entry
    body: dict[str, object] = {"server": entry.server}
    if entry.tools is not None:
        body["tools"] = list(entry.tools)
    return body


class AgentDefaults(BaseModel):
    """Provider references every agent inherits unless it names its own.

    Deliberately no prompt: an agent's prompt is its identity, and
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
    mcp: list[NonBlankStr | McpGrant] | None = Field(
        default=None,
        description=(
            "The MCP servers whose tools this layer offers the model. An entry is "
            "either the entry name on its own, which is the whole server, or an "
            "object naming the server and the tools of it this layer may reach "
            "({server: home, tools: [turn_on_light]}), where a tool is named by its "
            "published name without the entry prefix. Unset inherits the "
            "agent_defaults list; naming a list replaces the inherited one rather "
            "than extending it, so an empty list opts an agent out of the tools its "
            "siblings have. The builtin tools are outside this model: switch_agent "
            "and remember appear under a structural condition (a device bound to "
            "more than one agent, memory configured) and random_number under none "
            "at all, rather than by grant."
        ),
    )

    @field_validator("mcp", mode="before")
    @classmethod
    def _read_mcp(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        return [read_mcp_entry(index, item) for index, item in enumerate(value)]

    @field_validator("mcp")
    @classmethod
    def _check_mcp(
        cls, value: list[str | McpGrant] | None
    ) -> list[str | McpGrant] | None:
        """One entry per server, whichever form each is written in. Two
        entries for one server are two answers to a question with one:
        which of its tools this layer reaches.

        The refusal names the positions and not the server, for the
        reason the grant's own refusals do: it leaves this boundary as a
        printed line and an HTTP body."""
        if value is None:
            return value
        repeated = _repeated_positions([as_mcp_grant(entry).server for entry in value])
        if repeated:
            raise ValueError(
                f"mcp names one server at more than one position ({repeated}); one "
                f"entry per server, listing every tool it grants"
            )
        return value

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

    # The shared fragments this layer's prompt carries. None means
    # inherit; a list replaces rather than extends, exactly like `mcp`,
    # so a layer naming an empty list opts out of the fragments its
    # siblings share.
    prompt_includes: list[NonBlankStr] | None = Field(
        default=None,
        description=(
            "The shared prompt fragments this layer's system prompt carries, each by "
            "the name it is defined under in prompt_fragments, injected in the order "
            "listed and directly after the agent's own prompt. Unset inherits the "
            "agent_defaults list; naming a list replaces the inherited one rather than "
            "extending it, so an empty list opts an agent out of the fragments its "
            "siblings share. Every name has to be a fragment that exists, since the "
            "fragment is in this same database, and a name listed twice is refused. "
            "Fragments are part of the boot-time snapshot, so a change here reaches a "
            "conversation at the next server start rather than at a reload."
        ),
    )

    @field_validator("prompt_includes")
    @classmethod
    def _check_prompt_includes(cls, value: list[str] | None) -> list[str] | None:
        """One entry per fragment. Naming a fragment twice would inject
        it twice, which is a thing to say once if it is meant at all.

        The refusal points at positions and never at what is in them, the
        rule the grant's own refusals follow: a rejected name may be a
        pasted credential, and this sentence leaves the boundary as a
        printed CLI line, an HTTP 422 body and a boot log.
        """
        if value is None:
            return value
        repeated = _repeated_positions(value)
        if repeated:
            raise ValueError(
                f"prompt_includes names one fragment at more than one position "
                f"({repeated}); list each fragment once"
            )
        return value


class AgentConfig(AgentDefaults):
    """One agent: a prompt, plus whichever stages it overrides."""

    prompt: str = Field(
        default="",
        description=(
            "The instruction this agent replies under, sent as the system "
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


# What a device may say about itself on a retained log line, and how
# much of it.
#
# The content-and-telemetry ADR, as amended on 2026-08-17, admits
# bounded device-descriptor metadata onto the events: what a device says
# about ITSELF at check-in is an identifier-class fact about the
# endpoint, unlike what a person said through it. "Bounded" is the whole
# of the permission, so the bound lives here, beside `normalize_mac`,
# which is this codebase's other answer to "a device sent this and the
# server owns what it becomes". Every one of these values arrives on an
# unauthenticated request, so the limits are what a real board reports
# with room to spare rather than what a header could hold.
#
# The limits are restated in the event registry, which enforces them
# again where the field is emitted; the conformance test holds the two
# statements equal.
BOARD_LIMIT = 64
FIRMWARE_LIMIT = 32
CLIENT_ID_LIMIT = 64


def bounded_descriptor(value: str, limit: int) -> str:
    """One device-reported descriptor, cut down to what a log line may
    carry: printable characters only, trimmed, and no longer than
    `limit`.

    Unprintables go first and by class rather than by list: a newline
    would split one retained record into two, and a terminal escape
    would let whoever sent it paint an operator's screen. `str.isprintable`
    is false for every control character, for the separators, and for
    the non-ASCII spaces, which is exactly the set that has to go.

    Trimmed twice, before and after the cut, because a truncation can
    leave the trailing space that was in the middle of the value.

    The empty string is a possible answer, for a value that was nothing
    but unprintables. Each caller decides what an empty descriptor means
    there, because the two sites disagree: an absent board is already
    "unknown" by the time it reaches here, and an unreadable client id
    is a null field.
    """
    printable = "".join(character for character in value if character.isprintable())
    return printable.strip()[:limit].strip()


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


# What a rejected fragment name is told, wherever it is rejected. One
# sentence, because there is one rule and because it must be possible to
# say it before anything else has looked at the write: it names the
# section and the rule and never the name, so a caller can refuse a name
# without having parsed the body it arrived with.
PROMPT_FRAGMENT_NAME_RULE = (
    "prompt_fragments: a fragment name has to match [A-Za-z0-9_-]+, and this one does "
    "not. The name is not quoted back: what fails this rule is the kind of string that "
    "must not be echoed"
)


def is_valid_fragment_name(name: object) -> bool:
    """Whether a fragment name is written in the safe charset.

    The same rule an `mcp_servers` entry name follows, for the same
    reason and not for its reason: an entry name has to be a legal tool
    prefix, and a fragment name has to be safe on a terminal, in a log
    line and in a provenance token (`fragment:<name>`). The reserved
    names do not apply, since a fragment is in no tool list.

    A predicate rather than a check that raises, because the first thing
    a write does with a name is decide whether it may be spoken about at
    all: a caller that has to parse a body before it can ask this ends
    up naming the rejected name in the refusal about the body.
    """
    return isinstance(name, str) and names.TOOL_NAME_PATTERN.match(name) is not None


def check_prompt_fragment_names(
    value: dict[str, PromptFragmentConfig],
) -> dict[str, PromptFragmentConfig]:
    """Every name in the section, checked as a snapshot is validated.

    The refusal is deliberately not `check_mcp_entry_names`', which
    interpolates the name it rejected: a name that fails the charset is
    exactly the string that must not be echoed, because what was written
    there may be a pasted credential. So it names the section and the
    rule, and a valid name is the only kind any surface here prints.
    """
    if any(not is_valid_fragment_name(name) for name in value):
        raise ValueError(PROMPT_FRAGMENT_NAME_RULE)
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
        "of the names the merged tool list already uses. What a tool answers with "
        "reaches the model as speakable text, since the reply is spoken; content of "
        "any other kind is named as a placeholder rather than dropped. Carrying "
        "structured content to a device is work for the display protocol, once the "
        "display path can render more than speech, rather than for the tool loop."
    ),
    "prompt_fragments": (
        "The shared blocks of prompt text agents include by name, keyed by fragment "
        "name. A fragment is written once and injected verbatim into the system "
        "prompt of every agent whose prompt_includes names it, which is how household "
        "facts or a house style stay in one place instead of being copied into every "
        "persona prompt and drifting apart. The name appears in the provenance the "
        "assembled prompt is reported under (fragment:<name>), so it must match "
        "[A-Za-z0-9_-]+."
    ),
    "agent_defaults": (
        "What every agent uses unless it names something else. One entry for the "
        "whole deployment, and deliberately without a prompt: a prompt is what "
        "makes an agent that agent, so inheriting one silently would make two "
        "agents the same one."
    ),
    "agents": (
        "The agents this deployment serves, keyed by name. An agent is a "
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

# The sections that live in the database rather than in the file, in the
# order they are written and read, which is also the order they have to
# be created in: nothing may reference what does not exist yet. Derived
# from the descriptions above rather than restated, so a section cannot
# be documented and then forgotten by the composition (or the other way
# round).
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
    prompt_fragments: dict[str, PromptFragmentConfig]
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
        # Both entry forms name a server, so both are checked here: an
        # allow list on a server that does not exist is the same broken
        # reference as a bare name that does not.
        for entry in layer.mcp or []:
            server = as_mcp_grant(entry).server
            if server not in snapshot.mcp_servers:
                hint = (
                    f" (defined: {', '.join(sorted(snapshot.mcp_servers))})"
                    if snapshot.mcp_servers
                    else "; no mcp_servers entries are defined"
                )
                problems.append(f'{source}.mcp: unknown MCP server "{server}"{hint}')
        # An include is checked here rather than deferred the way a
        # grant's tool allow list is: the referent is a row in this same
        # database, so nothing about it waits for a live connection.
        #
        # The one below is the only reference refusal in this function
        # that does not quote what it could not resolve, and the
        # difference is deliberate. A fragment name is written beside
        # prompt text, an operator pastes things there, and this sentence
        # travels out as a CLI line, an HTTP 422 body and a boot log. The
        # charset rule does not close that on its own, since a credential
        # can be written in [A-Za-z0-9_-]. So the position says which
        # entry to look at, and the fragments that do exist say what
        # could have been meant, both of them written by this deployment.
        for position, include in enumerate(layer.prompt_includes or [], start=1):
            if include not in snapshot.prompt_fragments:
                hint = (
                    f" (defined: {', '.join(sorted(snapshot.prompt_fragments))})"
                    if snapshot.prompt_fragments
                    else "; no prompt_fragments entries are defined"
                )
                problems.append(
                    f"{source}.prompt_includes: entry {position} names no prompt "
                    f"fragment that exists, and the name is not quoted back{hint}"
                )

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
    # The shared prompt text agents compose their own with, keyed by the
    # name a layer's prompt_includes references.
    prompt_fragments: dict[NonBlankStr, PromptFragmentConfig] = Field(
        default_factory=dict, description=DOMAIN_DESCRIPTIONS["prompt_fragments"]
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

    @field_validator("prompt_fragments")
    @classmethod
    def _check_fragment_names(
        cls, value: dict[str, PromptFragmentConfig]
    ) -> dict[str, PromptFragmentConfig]:
        return check_prompt_fragment_names(value)

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

    def prompt_for_agent(self, agent: str) -> str:
        """The persona an agent replies under, and the only source of
        it.

        A method rather than a field read at each call site because the
        persona is one of the pieces the assembled prompt is made of,
        and two places holding it is how a pipeline and an inspection
        surface come to disagree about what the model was sent. There is
        deliberately no inheritance here, unlike the provider stages: a
        prompt is what makes an agent that agent, which is why
        `agent_defaults` refuses to carry one.
        """
        return self.agents[agent].prompt

    def fragments_for_agent(self, agent: str) -> list[Fragment]:
        """The shared fragments an agent's prompt carries, resolved and
        in the order they are injected: its own list when it names one,
        `agent_defaults` otherwise. A list replaces rather than extends,
        so `prompt_includes: []` is how an agent opts out of the
        fragments its siblings share.

        The lookup cannot miss. Every include is checked against
        `prompt_fragments` at write time and again by this model's own
        validator, so a `Config` that exists is one whose includes all
        resolve.
        """
        own = self.agents[agent].prompt_includes
        included = own if own is not None else self.agent_defaults.prompt_includes or []
        return [Fragment(name, self.prompt_fragments[name].text) for name in included]

    def mcp_for_agent(self, agent: str) -> list[McpGrant]:
        """The MCP servers an agent talks to and how much of each: its
        own list when it names one, agent_defaults otherwise. A list
        replaces rather than extends, so `mcp: []` is how an agent opts
        out of tools its siblings have.

        Every entry answers as a grant, whichever form it was written
        in, so nothing downstream of here has to know that the whole
        server has a shorter spelling."""
        own = self.agents[agent].mcp
        entries = own if own is not None else self.agent_defaults.mcp or []
        return [as_mcp_grant(entry) for entry in entries]

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
        built.

        A server an agent reaches one tool of is referenced as much as
        one it reaches all of: an allow list narrows the tool list, not
        whether the connection is made."""
        return {grant.server for agent in self.agents for grant in self.mcp_for_agent(agent)}

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
