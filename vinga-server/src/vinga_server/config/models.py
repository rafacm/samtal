"""Pydantic models for the vinga-server configuration.

The configuration has two halves. `FileConfig` is the half the YAML file
holds, `server`, with the VINGA_ environment overrides
pydantic-settings gives it. The domain half (providers, mcp_servers,
agent_defaults, agents, devices, default_agent) lives in the database
and is written with `vinga-server config`. `Config` is the composition
of the two, which is what the server boots from and what every call site
reads.

Secrets are referenced by environment variable name (for example
api_key_env, or a $VAR value in an MCP server's env and headers), never
written inline; the other form is a value encrypted in the database,
which no model here ever carries.
"""

import os
import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from contextvars import ContextVar
from pathlib import Path
from types import UnionType
from typing import Annotated, Literal, NamedTuple, Protocol, Union, get_args, get_origin
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
    InitSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

# The block shape the assembler owns, imported rather than restated for
# the reason `tools/mcp.py` imports `Guidance` from there: what an
# agent's fragments are is this module's business, and what a prompt
# does with them is that one's. It is a leaf import, over a module that
# holds pure text functions and reads only `tools.names`.
from vinga_server.runtime.prompt import Fragment
from vinga_server.tools import names

_MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")

# Fragments that mark a provider option as secret-bearing. Keys ending in
# _env are the sanctioned pattern: they name an environment variable instead
# of holding the value.
_SECRET_KEY_FRAGMENTS = ("secret", "token", "password", "api_key", "apikey", "credential")

# The same rule where the name is not one this repository or a provider
# type declared, and so is whatever somebody else called it: an MCP
# server's env and headers, where the key that carries a secret is as
# often called Authorization as it is token, and a URL's query, where
# the parameter is named by the vendor whose endpoint it addresses. One
# tuple and not two, because a word that marks a secret in a header
# marks one in a query parameter as well.
_UNDECLARED_SECRET_KEY_FRAGMENTS = (*_SECRET_KEY_FRAGMENTS, "auth")

# The names the narrow scan above must not reach, and the whole of what
# earns one a place here: it contains a fragment as a substring, it is a
# declared option a builder reads, and it is not a credential. All three,
# because the first without the second is a key nobody consumes and the
# second without the third is a credential with a reader.
#
# One entry, and the census that found it says why there is not a
# second: `max_tokens` is the only provider-option key in this codebase
# containing a fragment as a substring but not as a word. It is the cap
# on one reply's length, read by the `anthropic` and `openai_compatible`
# builders, and the fragment scan refused it on every surface, so the
# option could never be installed and the builders' default silently
# always won (#277).
#
# The wider tuple is deliberately not narrowed by this. An MCP server's
# env or headers key and a URL's query parameter are named by somebody
# else, so no name there is a declared option a builder reads, and the
# second condition above can never hold.
_SECRET_KEY_EXEMPT_NAMES = ("max_tokens",)

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
# How a command of this grammar is spelled in anything this repository
# generates: the CLI reference and its recipes, the export header, the
# domain reference these field descriptions render into, and the OpenAPI
# document beside it. One constant, because a generated document may no
# more vary with the invocation than with the terminal.
#
# Here rather than beside the descriptors that carry the command
# strings, because the field descriptions below name commands too and
# this module is the one every renderer already reaches through:
# `entities` imports it, `docgen` imports both, and `cli` imports all
# three. `entities` re-exports it, so there is one string and not two.
PROGRAM = "vinga"

# And how the same command is spelled by a sentence a SERVER composes:
# a boot refusal, a runtime refusal, an event message. A server runs
# inside the image, where `vinga-server` is what a shell answers to and
# `config` is the word that dispatches away from serving, so that is
# what a sentence composed there tells an operator to type. The two
# names are the same grammar reached two ways, and which of them a
# surface uses is decided by who composes it rather than by what it
# says.
SERVER_PROGRAM = "vinga-server config"


API_MOUNT_PATH = "/api"

# Where the onboarding short route is served: /x/<key>/, the alias of the
# OTA endpoint an operator types into a device's captive portal. It lives
# here for the same reason the API mount path does: ota_path's validator
# has to reserve it, since a configured OTA path under this prefix would
# collide with the onboarding router.
ONBOARDING_MOUNT_PATH = "/x"

# The two health probes, and where they are served. Here for the reason
# the two paths above are here: `ota_path`'s validator has to reserve
# them. They are registered before the OTA router, and each answers both
# spellings of its path, so an OTA endpoint configured at either would
# never be reached at all. `app.py` registers them from these, so the
# reservation and the routes are one fact.
HEALTH_PATH = "/healthz"
READY_PATH = "/readyz"
PROBE_PATHS = (HEALTH_PATH, READY_PATH)

# The alphabet a pinned onboarding key may be written in, and how long it
# is. Base32 because A-Z2-7 has no 0/O and no 1/I/l, the pairs a person
# misreads off a small display, and eight characters because that is what
# the derivation truncates to.
_ONBOARDING_KEY_RE = re.compile(r"^[A-Z2-7]{8}$")

# The logging level names, most to least verbose, and the one home of
# that set: `log_level`'s description names them from here, its refusal
# lists them from here, and the reason NOTSET is not among them is on
# the field where an operator meets it.
#
# Public, for the reason `docgen.nested_model` is: the generated server
# reference publishes this set as a rule an operator is held to, and the
# test that holds the page to naming every one of them is a caller. A
# test reaching an underscore for it would be pinning a detail; this is
# the name it reaches instead.
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

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
            "secret_env: VINGA_API_SECRET"
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
        default="VINGA_API_SECRET",
        description=(
            "The name of the environment variable holding the API's bearer token, "
            "never the token itself. A variable name and nothing else: letters, "
            "digits and underscores, not starting with a digit, so a pasted token "
            "is refused where it was written rather than at the lookup that would "
            "never have found it. The server refuses to boot when the variable it "
            "names is unset or blank. The token grants everything the API can do, "
            "secret writes included, so it belongs on a loopback connection or "
            "behind TLS and nowhere else."
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

    enabled: bool = Field(
        default=True,
        description=(
            "Whether the websocket handshake checks a device token. On by default, "
            "and a server started with it on and nothing in the variable "
            "`secret_env` names refuses to boot, so a deployment never quietly "
            "serves every device that connects. Turning it off is the one "
            "deliberate flag for a trial on a trusted network, and with no secret "
            "there is no key to derive, so the short onboarding route is then "
            "served keyless."
        ),
    )
    secret_env: EnvName = Field(
        default="VINGA_AUTH_SECRET",
        description=(
            "The name of the environment variable holding the device-auth secret, "
            "never the secret itself. A variable name and nothing else: letters, "
            "digits and underscores, not starting with a digit, so a pasted secret "
            "is refused where it was written rather than at the lookup that would "
            "never have found it. The same secret is what the short onboarding "
            "key is derived from, which is why rotating it moves that key."
        ),
    )

    token_expire_s: int = Field(
        default=2592000,
        gt=0,
        description=(
            "How long an issued device token stays valid, in seconds. Thirty days, "
            "which is upstream's default; the firmware re-checks OTA on every boot, "
            "so a device in normal use is re-issued long before it gets near this."
        ),
    )


class OnboardingConfig(BaseModel):
    """The short onboarding path, `/x/<key>/`, an alias of the OTA endpoint.

    Onboarding a stock board means typing its backend URL into a captive
    portal on a phone, with no feedback on a typo, so the string has to
    be short and its alphabet unambiguous. Left to itself, which is the
    normal case, the key is derived from the device-auth secret the
    deployment already has: nothing about it is stored, it is stable
    across restarts, and it moves only when that secret does.

    `key` below is the one exception, and the reason it exists: it pins
    a key into this file so that the one a rotation would have retired
    keeps working. Provisioned boards go on reaching the URL they were
    given while the new secret takes over everything else.

    On by default. The legacy path keeps working beside it, and a
    deployment that wants only the legacy one turns this off.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=True,
        description=(
            f"Whether the short onboarding route is served at "
            f"`{ONBOARDING_MOUNT_PATH}/<key>/`. On by default. The legacy "
            f"`server.ota_path` keeps working beside it, and a deployment that "
            f"wants only that one turns this off; turning this off with "
            f"`server.ota_path` null as well is refused at boot, since no device "
            f"could then fetch its configuration from this server at all."
        ),
    )

    key: str | None = Field(
        default=None,
        description=(
            "The onboarding key, pinned, rather than derived from the device-auth "
            "secret. Eight base32 characters (A-Z and 2-7), the shape the "
            "derivation produces, written in any case and normalized to upper. Its "
            "one use is a secret rotation: the derivation follows the secret, so "
            "pinning the previous key keeps provisioned boards reaching the same "
            "URL while the new secret takes over everything else. Left unset, "
            "which is the normal case, the key is derived and nothing about it is "
            "stored. It stands in front of the endpoint that issues device tokens, "
            "so it is better injected from the environment than written into a "
            "file."
        ),
    )

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

    max_sessions: int = Field(
        default=8,
        ge=1,
        description=(
            "How many conversations this server holds at once. Each one holds an "
            "ASR, an LLM stream and a TTS engine, so this is a resource bound and "
            "not a licence check. A device refused for capacity reconnects on its "
            "next wake word."
        ),
    )

    max_session_s: float = Field(
        default=3600.0,
        gt=0,
        description=(
            "One session's maximum life, in seconds. An hour by default. The "
            "firmware treats a close as the end of a conversation and reconnects "
            "on the next wake word, so a session closed here is invisible in "
            "normal use."
        ),
    )

    idle_timeout_s: float = Field(
        default=120.0,
        gt=0,
        description=(
            "How long a realtime session may go without a conversation before the "
            "server hangs up, in seconds. Counted from the end of the last "
            "utterance or the end of the last reply, whichever came later, so "
            "arriving audio does not reset it. Two minutes by default: long enough "
            "to think, read something out, or answer the door, short enough that "
            "walking away does not leave a mic streaming for the rest of the hour. "
            "Realtime sessions only, because only they stream continuously; an "
            "auto-mode device stops listening after each reply and re-arms per "
            "turn, and is bounded by `max_session_s` as before. There is no off "
            "switch: a deployment that wants none sets this near `max_session_s`."
        ),
    )


class CaptureConfig(BaseModel):
    """Recording sessions to disk for offline analysis.

    Off by default and off unless said otherwise. This writes room audio
    to disk, which is the opposite of what the rest of the project
    promises, so nothing here can turn it on by accident: the section
    has to exist and the flag has to say so. Writing the section is not
    consent, and neither is leaving it in place.

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

    enabled: bool = Field(
        default=False,
        description=(
            "Whether sessions are recorded to disk. Off by default, so a section "
            "left in a configuration file records nothing until somebody says it "
            "should. With it on, every session writes three files (a stereo WAV "
            "with the microphone on channel 0 and the reply on channel 1, a JSONL "
            "decision track whose offsets index into it, and a JSON manifest of "
            "what the capture was made against), a warning says so at startup, and "
            "each recorded session names its file."
        ),
    )

    dir: Path = Field(
        description=(
            "Where captures are written. It has to be on the data volume, since a "
            "deployment's container root is read-only. Required even when capture "
            "is disabled, so turning capture on is one word rather than one word "
            "and remembering where it writes."
        ),
    )

    max_session_s: float = Field(
        default=900.0,
        gt=0,
        description=(
            "Stop capturing a session after this many seconds. A bound on one "
            "file, not on the conversation, which carries on uncaptured."
        ),
    )

    max_total_mb: float = Field(
        default=2000.0,
        gt=0,
        description=(
            "Total budget for the capture directory, in megabytes. Whole captures "
            "are pruned, oldest first, when it is exceeded. Stereo 16 kHz is "
            "64 kB/s, so 2000 MB is around nine hours."
        ),
    )

    min_free_mb: float = Field(
        default=1000.0,
        ge=0,
        description=(
            "Refuse to start a capture when the volume has less free than this, in "
            "megabytes. The byte budget above does not protect the volume on its "
            "own: the model caches share it and grow underneath, and capture must "
            "not be what fills it."
        ),
    )


# How the environment names a key of the file half.
#
# The prefix and the delimiter are `FileConfig`'s own settings
# configuration below, read from here rather than written there as two
# literals: what `VINGA_SERVER__PORT` means is the scheme itself, and a
# scheme spelled once in the settings model and again in every sentence
# about it is two spellings that must agree.
ENV_PREFIX = "VINGA_"
ENV_NESTING = "__"

# And the prefix every key of the server half carries, which is the
# scheme applied to `FileConfig`'s one field. The word is written out
# because that field is the root this page and the refusals are about;
# everything below it is joined by the delimiter.
SERVER_ENV_PREFIX = f"{ENV_PREFIX}SERVER{ENV_NESTING}"

# The database section's own environment spellings, declared beside the
# model they name the fields of.
#
# One key, one variable, and the variable is the short one. The generic
# `VINGA_SERVER__DATABASE__HOST` would work by accident of the nesting
# scheme, and letting it would give every connection fact two names: the
# compose file feeds the Postgres image from the short spellings, which
# is the whole point of having them, and two spellings that must agree
# are one spelling with a bug pending. `loader._check_database_environment`
# is what refuses the generic one.
#
# Outside the section scheme rather than inside it, the way
# `VINGA_MASTER_KEY` is: what these name is where the server's own state
# lives, which a deployment sets beside its credentials rather than in
# the file it edits.
#
# Here rather than in `loader.py`, where they were, and rather than in
# `vinga_server.db`, where the two credential-only names were, for the
# reason `PROGRAM` is here: three readers and one of them below the
# other two. The loader applies them, the database package reads the two
# with no key, and the generated server reference publishes all six, and
# a renderer that restated them as literals would keep passing across a
# rename. This module imports neither of the other two, so there is no
# cycle to pay for it.
DATABASE_SECTION = "database"

DATABASE_ENV_PREFIX = f"{ENV_PREFIX}DB_"

# The four the YAML also carries, by the field of `DatabaseConfig` each
# one overrides. The password and the whole-URL override have no YAML
# key at all and are read where the URL is built (`vinga_server.db`), so
# they are deliberately not in this table: it is what maps a field onto
# its variable, and neither of those is a field.
DATABASE_ENV_NAMES: dict[str, str] = {
    "host": f"{DATABASE_ENV_PREFIX}HOST",
    "port": f"{DATABASE_ENV_PREFIX}PORT",
    "name": f"{DATABASE_ENV_PREFIX}NAME",
    "user": f"{DATABASE_ENV_PREFIX}USER",
}

# And the two that are environment-only, with the reasoning on
# `DatabaseConfig` below: a credential in a configuration file is what
# the no-secrets-in-YAML stance exists to prevent, and a URL carries one
# in its authority and can carry another in its query.
DATABASE_PASSWORD_ENV = f"{DATABASE_ENV_PREFIX}PASSWORD"
DATABASE_URL_ENV = f"{DATABASE_ENV_PREFIX}URL"

# The generic spelling of a database key, which is refused in favour of
# the short one above. Derived from the scheme rather than written out,
# so it cannot come to name a prefix the scheme would not produce.
DATABASE_GENERIC_ENV_PREFIX = f"{SERVER_ENV_PREFIX}{DATABASE_SECTION.upper()}{ENV_NESTING}"


class DatabaseConfig(BaseModel):
    """Which Postgres database this server keeps its state in.

    Four discrete facts rather than one connection string, because
    naming a host is what a deployment does with a database and
    assembling a URL is not. `VINGA_DB_HOST`, `VINGA_DB_PORT`,
    `VINGA_DB_NAME` and `VINGA_DB_USER` override these four from the
    environment, and those are the documented spellings: the compose
    file feeds the image's own `POSTGRES_*` names from the same four, so
    one `.env` flows into both sides of the development loop. The
    generic `VINGA_SERVER__DATABASE__*` spelling is refused with a
    sentence naming the short one, so a fact does not grow two names.

    The password has no key here at all. It is `VINGA_DB_PASSWORD` and
    only that: a credential in a config file is what the
    no-secrets-in-YAML stance exists to prevent, and a field on this
    model would be a value that every configuration read, diff and
    generated reference would then have to remember not to print.
    `VINGA_DB_URL` has no key here either, for the same reason and one
    more: it carries a password in its authority and can carry another
    in its query.

    The defaults are the development instance `docker compose up -d
    --wait` starts, so a checkout runs with no configuration at all. A
    deployment names its own, and the deployment documentation says
    that the shipped password default is a loopback-only convenience.
    """

    model_config = ConfigDict(extra="forbid")

    host: str = Field(
        default="127.0.0.1",
        description=(
            f"The host the Postgres instance is reached on. "
            f"`{DATABASE_ENV_NAMES['host']}` "
            f"overrides it, and that is the documented spelling. The default is the "
            f"development instance `docker compose up -d --wait` starts from the "
            f"repository root, so a checkout runs with no configuration at all."
        ),
    )
    port: int = Field(
        default=5432,
        ge=1,
        le=65535,
        description=(
            f"The port the Postgres instance listens on. "
            f"`{DATABASE_ENV_NAMES['port']}` overrides "
            f"it, and that is the documented spelling."
        ),
    )
    name: str = Field(
        default="vinga",
        description=(
            f"The database this server keeps both of its halves in. "
            f"`{DATABASE_ENV_NAMES['name']}` "
            f"overrides it, and that is the documented spelling. The domain "
            f"configuration lives in this database's `domain` schema, the "
            f"conversation record in a `record` schema and each agent's memory in a "
            f"`memory` schema beside it, so one instance is one deployment's whole "
            f"state."
        ),
    )
    user: str = Field(
        default="vinga",
        description=(
            f"The role the server connects as. `{DATABASE_ENV_NAMES['user']}` "
            f"overrides it, and "
            f"that is the documented spelling. The password has no key on this "
            f"model at all: it is `{DATABASE_PASSWORD_ENV}` and only that."
        ),
    )


class ConversationsConfig(BaseModel):
    """Recording what was said into a database that can be queried.

    Off by default and off unless said otherwise, the shape
    `capture.enabled` has and for the same kind of reason: this keeps
    conversation text on disk, so nothing here can turn it on by
    accident. The section has to exist and the flag has to say so.

    No connection of its own. The record lives in the `record` schema of
    the database `database` above names, beside the domain half's
    `domain` schema: the same instance, the same backup and the
    same credentials, with a read-only role scoped to this schema alone
    so that an analyst reads what was said without reaching the stored
    secrets next door.

    The two storage switches under the flag are independent, and every
    combination is a supported configuration: metrics without text is the
    stricter setting, and text without metrics keeps the conversation
    record without the behavioural telemetry. They are deployment-wide,
    which is the only policy layer this release has; per-user and
    per-agent controls are a stricter filter above this one when they
    arrive, never a replacement for it (#120).

    `resumption` is the third switch and the only one that is not about
    storage: it decides whether a past thread can be found and picked up
    again by voice. It reads what the other two wrote, so it is refused
    at boot in the two combinations where there would be nothing to read
    (#190).
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=False,
        description=(
            "Whether what was said is recorded to the database. Off by default, so "
            "a section left in a configuration file records nothing until somebody "
            "says it should: with this off no writer is started and no row is ever "
            "written. The tables exist either way, because the schema is migrated "
            "at every boot. Audio never enters it; `server.capture` is the "
            "recording, and this is the record."
        ),
    )

    metrics: bool = Field(
        default=True,
        description=(
            "Store the structured events and every measured number: durations, "
            "token counts, timings. With this off, no events rows land and the "
            "numeric columns on turns and tool invocations are null."
        ),
    )

    text: bool = Field(
        default=True,
        description=(
            "Store conversation text, and tool names, arguments and results. With "
            "this off, rows still land with the content columns null, so timing "
            "analysis survives the stricter setting."
        ),
    )

    # The default is the number the store itself defaults to, which its
    # own tests pin: this model and that one may not drift apart.
    retention_days: int = Field(
        default=90,
        ge=0,
        description=(
            "Prune sessions older than this many days, whole sessions at a time, "
            "at startup and at each session close. 0 keeps everything, which is a "
            "deliberate choice rather than a default: a store with no policy "
            "retains forever."
        ),
    )

    resumption: bool = Field(
        default=False,
        description=(
            "Whether an agent can find one of its own past threads by description "
            "and carry on with it. Off by default, so a deployment that records "
            "conversations does not thereby start reading them back: what a device "
            "says next is answered out of the session it is in unless somebody "
            "asks for more. It reads what the two switches above wrote, so it is "
            "refused at boot with `enabled` off or with `text` off."
        ),
    )

    resumption_budget_tokens: int = Field(
        default=6000,
        ge=512,
        description=(
            "How much of a resumed thread is rebuilt into the model's context, in "
            "tokens. Approximate by design: what the hydrator counts is characters "
            "over a fixed estimate, and what it drops is whole stored turns, "
            "oldest first, so a reply is never rebuilt without the utterance it "
            "answered."
        ),
    )

    @model_validator(mode="after")
    def _check_resumption(self) -> "ConversationsConfig":
        """Refuse the two combinations in which resumption could only
        pretend.

        Both pointers name the switch to turn on rather than the one to
        turn off, which is the `FillerConfig` precedent applied here: the
        sentence names both keys and both ways out, and the pointer names
        the field a form would take an operator to. Two problems where
        both are off, because both are true.
        """
        problems = [
            FieldProblem(json_pointer((field,)), message)
            for field, wanted, message in (
                ("enabled", self.enabled, RESUMPTION_NEEDS_RECORDING),
                ("text", self.text, RESUMPTION_NEEDS_TEXT),
            )
            if self.resumption and not wanted
        ]
        if problems:
            raise FieldProblemsError(problems)
        return self


# What a resumption that could not work is refused with. Fixed sentences
# naming the two keys and the two ways out, and no value: every word of
# them is this repository's own.
RESUMPTION_NEEDS_RECORDING = (
    "conversations.resumption is on with conversations.enabled off; there is nothing "
    "to resume where nothing is recorded, so switch conversations.enabled on or "
    "conversations.resumption off"
)
RESUMPTION_NEEDS_TEXT = (
    "conversations.resumption is on with conversations.text off; a thread cannot be "
    "rebuilt from text that was never stored, so switch conversations.text on or "
    "conversations.resumption off"
)

# And what a server no device could reach its configuration on is
# refused with. Beside the two above rather than inline in the validator
# for the same reason they are here: a boot refusal is a fixed sentence
# this repository owns, and the page that publishes what a deployment is
# refused for has to read the sentence the validator raises rather than
# a second copy of it.
#
# The path is in a code span because this sentence has two readers. A
# terminal prints the backticks and loses nothing; a browser reading the
# generated reference would otherwise take `<key>` for a tag and drop it,
# leaving the route the operator is sent to spelled `/x//`.
NOTHING_DISCOVERABLE = (
    "server.ota_path is null and server.onboarding.enabled is false, so "
    "no device could fetch its configuration from this server at all. "
    "Keep one of the two: an ota_path for the boards already provisioned "
    "with it, or onboarding enabled for the short `/x/<key>/` route"
)


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
    """How this process runs: the `server:` section of the YAML file.

    The server half of the configuration, and the whole of what the file
    holds. Everything about what the server says and to whom (providers,
    MCP servers, prompt fragments, agent defaults, agents, devices, the
    default agent) is the domain half, which lives in the database this
    section names and is written through the configuration API.

    Read once at start and never re-read by a running process: the port,
    the paths, the limits, the barge-in tuning and the storage switches
    are what one server is serving until the next one starts. That is
    the line between the two halves. A change here is a restart; a
    change to the domain half is an apply, except for the two kinds a
    running server re-reads as a device asks for them, `devices` and
    `default_agent`, which reach a board at its next check-in with
    nothing asked of the server at all.

    Any key of it can be overridden from the environment as
    `VINGA_SERVER__<PATH>`, with `__` joining the nesting
    (`VINGA_SERVER__PORT`, `VINGA_SERVER__ONBOARDING__KEY`); environment
    beats file beats these defaults. The `database` section is the one
    recorded exception, with four short spellings of its own.

    No credential is ever written here. The API's bearer token, the
    device-auth secret and the database password are named rather than
    held: a key that carries one holds the name of the environment
    variable it is read from, and the two values that are
    environment-only, the database password and the whole database URL,
    have no key on any of these models at all.

    Two path fields are the exception to that, and they are worth
    naming because they do not look like secrets. A stock board can
    present no credential at its first OTA call, so the token issuer is
    protected by its own path: `ota_path` carries a long random segment
    on a publicly exposed deployment, and `onboarding.key` pins the
    short segment serving the same endpoint. Both are as sensitive as
    what they stand in front of, which is why neither is ever quoted
    back in a refusal, and both are better injected as
    `VINGA_SERVER__OTA_PATH` and `VINGA_SERVER__ONBOARDING__KEY` than
    committed into a file.
    """

    model_config = ConfigDict(extra="forbid")

    host: str = Field(
        default="0.0.0.0",
        description=(
            "The address the server listens on. 0.0.0.0 accepts connections on "
            "every interface, which is what a device elsewhere on the network "
            "needs; 127.0.0.1 keeps the server to this host, which is what a "
            "deployment behind a proxy on the same machine wants."
        ),
    )
    port: int = Field(
        default=8003,
        ge=1,
        le=65535,
        description=(
            "The port the server listens on. Everything it serves is on this one "
            f"port: the websocket channel, the OTA endpoint, the short onboarding "
            f"route, the configuration API at `{API_MOUNT_PATH}` and the health "
            f"probes."
        ),
    )

    websocket_url: str | None = Field(
        default=None,
        description=(
            "The websocket URL the OTA endpoint hands to devices. A `ws://` or "
            "`wss://` URL that names a host and carries no `user:password`, since "
            "this value is read back out verbatim by the OTA endpoint's own GET, "
            "which anyone holding the onboarding URL can reach. Left unset it is "
            "derived from the address the device reached the OTA endpoint on, "
            "which is right for a plain LAN deployment; set it explicitly when the "
            "server sits behind a proxy or a name the request headers do not carry."
        ),
    )

    public_url: str | None = Field(
        default=None,
        description=(
            "The origin devices reach this server on, written exactly as a person "
            "would type it. An `http://` or `https://` origin, with an optional "
            "path prefix when a proxy serves the server under one, and with no "
            "`user:password`, no query and no fragment: this is an origin rather "
            "than a whole URL, and it is printed at startup and handed to somebody "
            "to type. Its only job is to say the onboarding URL out loud at "
            "startup and on the OTA GET. Unset, the origin is derived from "
            "`websocket_url`. Failing that, the OTA GET answers with the address "
            "the request arrived on, and the startup line, which has no request to "
            "read, guesses from the listen address and says it is a guess."
        ),
    )

    ota_path: str | None = Field(
        default="/xiaozhi/ota/",
        description=(
            "Where the OTA endpoint is served, or null to unmount it. It must "
            f"start and end with `/`, and four places are reserved: "
            f"`{API_MOUNT_PATH}/` and anything under it, where the OTA route would "
            f"be found before the configuration API is mounted and would answer a "
            f"request its token gate never saw; `{ONBOARDING_MOUNT_PATH}/` and "
            f"anything under it, which serves this same endpoint at "
            f"`{ONBOARDING_MOUNT_PATH}/<key>/`; and the health probes "
            f"`{HEALTH_PATH}` and `{READY_PATH}`, which are registered first and "
            f"each answer both spellings of their own path, so an OTA endpoint "
            f"served at either would never be reached. It is the token issuer, so "
            f"it cannot itself require a token: an operator exposing the server "
            f"publicly hides it behind a long random segment "
            f"(`/xiaozhi/ota/8f3a.../`) and writes that whole URL into the "
            f"device's NVS. The websocket path never moves, since the token is "
            f"what protects it. Null unmounts the route, which a deployment does "
            f"once every board it serves has moved to the onboarding path, and "
            f"unmounting it with `onboarding.enabled` false as well is refused at "
            f"boot."
        ),
    )

    onboarding: OnboardingConfig = Field(
        default_factory=OnboardingConfig,
        description=(
            "The short onboarding alias of the OTA endpoint, the URL a person "
            "types into a board's captive portal. On by default."
        ),
    )

    protocol_version: int = Field(
        default=1,
        ge=1,
        le=3,
        description=(
            "Binary protocol version advertised to devices. The firmware defaults "
            "to 1, which is bare Opus frames; 2 and 3 add timestamp headers."
        ),
    )

    timezone_offset_minutes: int | None = Field(
        default=None,
        ge=-1440,
        le=1440,
        description=(
            "Minutes east of UTC, sent so the device can set its clock to local "
            "time. Left unset the server's own current offset is used."
        ),
    )

    log_format: Literal["text", "json"] = Field(
        default="text",
        description=(
            'How the server logs. "text" is the human format; "json" is one object '
            "per line, which is what the container image defaults to, and what a "
            "collector groups by session to measure the pipeline. The records are "
            "metadata; what was said is in the conversation record."
        ),
    )
    log_level: str = Field(
        default="INFO",
        description=(
            "How much the server logs, as one of: "
            + ", ".join(LOG_LEVELS)
            + ". Written in any case and normalized to upper; anything else refuses "
            "the boot. NOTSET is deliberately not accepted: on the root logger it "
            "means WARNING, which is not what writing it says."
        ),
    )

    auth: AuthConfig = Field(
        default_factory=AuthConfig,
        description=(
            "Device authentication for the websocket endpoint. On by default, and "
            "a server started with it on and no secret in the environment refuses "
            "to boot rather than quietly serving open."
        ),
    )

    api: ApiConfig = Field(
        default_factory=ApiConfig,
        description=(
            f"The configuration REST API, mounted at `{API_MOUNT_PATH}` on the "
            f"port above. Always on and always behind a bearer token, so the "
            f"section exists only to name the variable that token comes from."
        ),
    )

    limits: LimitsConfig = Field(
        default_factory=LimitsConfig,
        description="What one server will hold at once, and for how long.",
    )

    database: DatabaseConfig = Field(
        default_factory=DatabaseConfig,
        description=(
            "The Postgres database this server keeps both of its halves in: the "
            "domain configuration written through the configuration API and read "
            "at each start, and the conversation record below."
        ),
    )

    capture: CaptureConfig | None = Field(
        default=None,
        description=(
            "Recording room audio to disk for offline analysis. Absent, or present "
            "with `enabled` off, means no session is ever recorded, and absent is "
            "the default."
        ),
    )

    conversations: ConversationsConfig | None = Field(
        default=None,
        description=(
            "Recording what was said into a database that can be queried. Absent, "
            "or present with `enabled` off, means no session is ever recorded and "
            "no writer is started, and absent is the default. The schema is "
            "migrated at boot either way, because a deployment that recorded last "
            "month and records nothing today still has to be able to read what it "
            "kept, and empty tables are not a recording."
        ),
    )

    local_only: bool = Field(
        default=False,
        description=(
            "Refuse to boot any provider that sends session data off this host. "
            "Running without a cloud dependency is otherwise a documentation "
            "property of a carefully chosen configuration; this makes it a checked "
            "one. Boot-time, never runtime: a local_only server that starts is a "
            "local_only server (#30)."
        ),
    )

    barge_in: bool = Field(
        default=True,
        description=(
            "Whether speech arriving while a reply is playing interrupts it. On by "
            "default, because a device only streams its mic through playback when "
            "its echo cancellation is on, and what arrives is then the user's "
            "voice. Turn it off for a board whose cancellation leaks the speaker "
            "back into the mic (a single-mic board), where the reply would "
            "otherwise interrupt itself: conversations stay multi-turn, and what "
            "arrives during a reply is dropped instead."
        ),
    )

    barge_in_min_speech_ms: float = Field(
        default=500.0,
        ge=0,
        description=(
            "The least endpointer-classified speech, in milliseconds, an utterance "
            "needs before it may interrupt a reply. Noise blips and playback bleed "
            "rarely sustain half a second of speech; a real interjection does (#28)."
        ),
    )

    barge_in_refractory_ms: float = Field(
        default=1000.0,
        ge=0,
        description=(
            "How long after a reply's first audio frame interruptions are ignored, "
            "in milliseconds. It covers the transient a device's echo cancellation "
            "lets through at playback onset."
        ),
    )

    utterance_pre_roll_ms: float = Field(
        default=300.0,
        ge=0,
        description=(
            "How much audio from before the detected start of speech rides along "
            "to ASR, in milliseconds, so the first phoneme survives the trim. The "
            "rest of the leading silence a continuously listening device piles up "
            "is dropped before transcription (#14)."
        ),
    )

    llm_first_token_timeout_s: float = Field(
        default=10.0,
        gt=0,
        description=(
            "How long the LLM may take to its first token, in seconds, before the "
            "round is cancelled and retried once; a second timeout gives the round "
            "up. Only the wait for the first token is bounded, because a long "
            "generation that is streaming is healthy, and any stream activity "
            "stops the clock: the adapters announce their first chunk off the "
            "wire, so a round that streams only a buffered tool call is not "
            "mistaken for a stall. The default is chosen against field data: "
            "healthy first tokens cluster at 500 to 800 ms, the worst spike that "
            "still answered sat at 8.9 s, and the stall this exists for held the "
            "session for 17 s with nothing on the wire (#68)."
        ),
    )

    drain_s: float = Field(
        default=20.0,
        ge=0,
        description=(
            "How long a shutdown waits for conversations in flight to finish "
            "speaking before the process goes, in seconds. Twenty seconds sits "
            "inside the thirty an orchestrator commonly allows between SIGTERM and "
            "SIGKILL; `docker stop` needs its own timeout raised above this, since "
            "its default is ten."
        ),
    )

    @field_validator("log_level")
    @classmethod
    def _check_log_level(cls, value: str) -> str:
        level = value.strip().upper()
        if level not in LOG_LEVELS:
            # Value-free, like the reserved-path refusals below and for
            # the same reason: what a validator is handed is whatever
            # was written under the key, and a refusal that echoes its
            # input is one typo away from echoing the wrong key's (#289,
            # #291). The five levels are this repository's own words and
            # are what a reader needs.
            raise ValueError(
                "is not a logging level; expected one of: "
                + ", ".join(LOG_LEVELS)
                + ". What was set is not quoted back"
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
                'is not a usable OTA path; it must start and end with "/", for '
                "example /xiaozhi/ota/ or /xiaozhi/ota/8f3a9c2b.../. What was set is "
                "not quoted back"
            )
        # Never quoting the value, here or above: an operator who
        # exposes the server publicly hides the OTA endpoint behind a
        # long random segment, and that segment is the closest thing
        # this key has to a secret. The refusal above used to quote it,
        # which is exactly the case a rule with an exception in it does
        # not cover: a path typed one slash wrong is still that path.
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
        if path.rstrip("/") in PROBE_PATHS:
            # Named rather than quoted, like the two above, even though
            # what would be quoted here is one of these two fixed
            # strings: what the refusal is about is the rule, and this
            # key's value is never repeated back on principle.
            raise ValueError(
                f"{HEALTH_PATH} and {READY_PATH} are this server's health probes. They "
                f"are registered before the OTA router and each answers both spellings "
                f"of its own path, so an OTA endpoint served at either would never be "
                f"reached. Serve it somewhere else, for example /xiaozhi/ota/"
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
                f"https://voice.example/vinga; the value is not quoted back here, "
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
            raise ValueError(NOTHING_DISCOVERABLE)
        return self


class BootRefusal(NamedTuple):
    """One combination of server-half keys that is refused at boot,
    carried as data so the page that publishes it reads the validator's
    own sentence.

    A cross-field rule is the one thing a table of fields cannot show: it
    is true of two keys at once, so it belongs to neither row. Writing it
    out in prose beside the tables would be the second copy of a sentence
    this repository already owns, and a second copy is what goes stale
    the day a validator is reworded.

    `provoked_by` is what makes a row more than an assertion that a
    string exists. It is a mapping of field values that triggers exactly
    this refusal on `model`, so the suite can validate it and compare
    what was raised against `sentence`: a row whose provocation no longer
    refuses, or refuses with something else, fails rather than rendering
    a rule the server does not enforce. `validator` names the
    model-level validator the row is claiming, which is what lets the
    reverse direction be mechanized: a sweep over every reachable model's
    declared model validators asserts each of them is claimed here, so a
    new cross-field rule cannot arrive unpublished.
    """

    model: type[BaseModel]
    validator: str
    sentence: str
    provoked_by: Mapping[str, object]


# Every cross-field refusal the server half has, in the order the
# reference publishes them: the two resumption combinations, then the
# server that no device could reach.
#
# Below the models rather than beside the sentences above, because a row
# names the model it is a rule of and a model has to exist before it can
# be named. The sentences themselves stay where they were declared, and
# this registry holds no text of its own.
BOOT_REFUSALS: tuple[BootRefusal, ...] = (
    BootRefusal(
        model=ConversationsConfig,
        validator="_check_resumption",
        sentence=RESUMPTION_NEEDS_RECORDING,
        provoked_by={"enabled": False, "text": True, "resumption": True},
    ),
    BootRefusal(
        model=ConversationsConfig,
        validator="_check_resumption",
        sentence=RESUMPTION_NEEDS_TEXT,
        provoked_by={"enabled": True, "text": False, "resumption": True},
    ),
    BootRefusal(
        model=ServerConfig,
        validator="_check_something_is_discoverable",
        sentence=NOTHING_DISCOVERABLE,
        provoked_by={"ota_path": None, "onboarding": {"enabled": False}},
    ),
)


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
    was handed, and the empty string is the fragment itself. It carries
    only names this repository declared and positions in a list, which
    `safe_location` below is the rule for: a key the caller wrote is not
    addressed, and the pointer stops at the nearest enclosing place that
    can be named. `message` is the sentence, which is the same text the
    refusal's own prose carries for this problem: one computation, two
    renderings, so the two cannot come to disagree.

    It carries neither a value nor a caller's key. Every message here
    names a place and a rule, because a key that fails one of these
    rules most likely holds the credential, and so, often enough, does
    the key.
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


# What a refusal may name, in one place.
#
# A location segment inside a declared model is a name this repository
# chose, so printing it publishes nothing. Every other segment is a key
# the caller wrote: an unrecognized key on a closed model, an option on
# a pass-through one, an entry of a mapping field such as an MCP
# server's `env`. A key is as good a place to paste a credential as a
# value is, and better at hiding there, so none of them reaches a
# refusal's sentence, its pointers, its messages or a log line. The rule
# lived here already, applied by hand to one refusal (`_grant_location`
# below, and the comment on `_UNRECOGNIZED_KEY`); this is that same rule
# with every renderer reading it from one place.
UNRECOGNIZED_KEY_REFUSED = "an unrecognized key is not permitted"


def safe_location(
    model: type[BaseModel], location: Sequence[object], *, stored: bool = False
) -> tuple[tuple[object, ...], bool]:
    """The longest prefix of a pydantic error location this repository
    may say, and whether anything was dropped.

    Walked against the model rather than matched against a list of
    words, because what makes a segment safe is that the schema has it:
    a declared field descends into its own annotation, a position
    descends into a list's item type, and everything else stops the
    walk. Truncating rather than substituting per segment is the
    conservative reading: once a segment is the caller's, everything
    under it is addressed relative to a key that cannot be printed, so
    the honest answer is the nearest parent this repository can name.

    `stored` says where the location came from, and it is the whole of
    what decides a mapping key (#382). A caller's fragment is text
    somebody typed a moment ago and nothing has accepted, so a key in it
    is not said. A location in the STORED half addresses rows the
    repository is holding, and the key of an entity map there is the
    identity a write already accepted and the store's own refusals
    already print in full: `agents.<name>` is the vocabulary the write,
    the API and this deployment's documents all speak, and a boot
    refusal saying less about a stored world than the write that stored
    it is the incoherence this parameter removes.

    Every segment goes out through `spoken_identity`, the one door for
    an identity a refusal says (#381, #382, #414): a name written before
    the addressability rule can carry a credential and a control
    character, and a refusal is printed, logged and kept. It is applied
    to every segment rather than to the ones believed to need it,
    because that is the shape that stays right when a new kind of
    segment is admitted; on a name this repository declared it is the
    identity function.
    """
    safe: list[object] = []
    reached: object = model
    for part in location:
        reached = _declared(reached, part, stored=stored)
        if reached is None:
            return tuple(safe), True
        safe.append(spoken_identity(part) if isinstance(part, str) else part)
    return tuple(safe), False


def _declared(annotation: object, part: object, *, stored: bool) -> object | None:
    """What one location segment reaches inside an annotation when the
    segment is one this repository may say, and None when it is not."""
    annotation = _unwrapped(annotation)
    origin = get_origin(annotation)
    if origin is UnionType or origin is Union:
        # A union's error locations carry a branch tag pydantic builds
        # from the branch's core schema (`constrained-str`,
        # `McpGrant`). Those are not field names, so a tag stops the
        # walk like any other unknown segment: the location falls back
        # to the position or the field above it, which is the last thing
        # both branches agree on anyway.
        for branch in get_args(annotation):
            found = _declared(branch, part, stored=stored)
            if found is not None:
                return found
        return None
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        field = annotation.model_fields.get(part) if isinstance(part, str) else None
        return field.annotation if field is not None else None
    if origin in (list, tuple, set, frozenset) and isinstance(part, int):
        arguments = get_args(annotation)
        return arguments[0] if arguments else None
    if stored and isinstance(part, str):
        # A mapping's key, in a location that came out of the store,
        # which is the one segment provenance decides (#382).
        return _entity_valued(annotation)
    # A mapping's key otherwise, and anything a scalar cannot be indexed
    # into. Note which side of the line a mapping falls on when nothing
    # has accepted its keys: `env`, `headers` and the entity maps are
    # keyed by whatever was written, so a key there is request bytes
    # even when it names something real.
    return None


def _entity_valued(annotation: object) -> object | None:
    """What one entry of a mapping holds, when the mapping is a section
    of stored entities rather than a body somebody filled in.

    Read off the declaration rather than kept as a second list of
    section names, which would be one more structure to hold in step
    with the models. What tells the two apart is what the mapping is
    declared to HOLD: a section holds entities whose shape this
    repository wrote down (`agents`, `mcp_servers`, `prompt_fragments`,
    `providers.<stage>`) or a device's list of bindings, and its keys
    are therefore the identities every write, every read and every URL
    addresses one by. A mapping of strings to strings is a body the
    caller filled in, keys and all: an MCP server's `env` and its
    `headers` are named by whoever the tool belongs to, which is why a
    key there is never said.
    """
    if get_origin(annotation) not in (dict, Mapping):
        return None
    arguments = get_args(annotation)
    if len(arguments) != 2:
        return None
    values = _unwrapped(arguments[1])
    if isinstance(values, type) and issubclass(values, BaseModel):
        return arguments[1]
    if get_origin(values) in (list, tuple, set, frozenset):
        return arguments[1]
    return None


def _unwrapped(annotation: object) -> object:
    """An annotation without its `Annotated` metadata, which is where
    `NonBlankStr` and the other constrained aliases keep theirs."""
    return getattr(annotation, "__origin__", annotation) if _annotated(annotation) else annotation


def _annotated(annotation: object) -> bool:
    return getattr(annotation, "__metadata__", None) is not None


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


def validation_problems(
    headline: str, model: type[BaseModel], exc: ValidationError, *, stored: bool = False
) -> tuple[str, tuple[FieldProblem, ...]]:
    """One failed validation in both the renderings a refusal needs: the
    sentence an operator reads, and the field problems a form acts on.

    Walked once, so the two cannot come to disagree about how many
    things were wrong or what was said about each. The sentence keeps
    the dotted spelling of the location, because that is how an operator
    reads their own file; the problems carry the JSON Pointer, which is
    what a reader can act on.

    Every location is put through `safe_location` against the model
    first, so a segment the caller invented (an unrecognized key, an
    option of a pass-through model, an entry of `env` or `headers`)
    reaches neither rendering: a key is as good a place to paste a
    credential as a value, and this sentence is printed by the CLI,
    answered by the API and, for a stored row, written to the boot log.
    `error["input"]` is never read either, here least of all: it is the
    whole rejected fragment, inline secret and all.

    `stored` is passed through to that walk and says nothing more than
    where the location came from. Every renderer of a validation
    refusal in this package is now this one, the boot composition of
    the stored half included (#382), and the two halves differ in that
    one word rather than in a renderer each.

    It lives beside `safe_location` rather than in the repository that
    used to hold it because a second caller arrived that is not the
    repository: a provider type's own options model is validated by
    `config/provider_options.py`, on a path that must not import a database
    driver to reach one function. One walk, one wording, both callers.
    """
    lines = [headline]
    problems: list[FieldProblem] = []
    for error in exc.errors():
        location, dropped = safe_location(model, error["loc"], stored=stored)
        where = ".".join(str(part) for part in location)
        prefix = json_pointer(location)
        for problem in _error_problems(error, dropped):
            lines += [refusal_line(where, line) for line in problem.message.splitlines()]
            problems.append(FieldProblem(prefix + problem.path, problem.message))
    return "\n".join(lines), tuple(problems)


def refusal_line(where: str, line: str) -> str:
    """One problem as a refusal prints it: an indented dash, and the
    place in front of it when there is one this repository may name.

    One home for the shape, because a refusal an operator reads is one
    vocabulary however it was produced: the unchanged-value marker's own
    refusal builds its sentence without a validation error behind it,
    and a second spelling of the indentation would be a golden that
    moves for no reason.
    """
    return f"  - {where}: {line}" if where else f"  - {line}"


def _error_problems(
    error: Mapping[str, object], dropped: bool
) -> tuple[FieldProblem, ...]:
    """What one pydantic error stands for, decomposed as far as it can
    be.

    A validator that knows its semantic field says so by raising
    `FieldProblemsError`, and pydantic carries the exception object in
    the error's context, which is the only place that knowledge
    survives: a model-level validator's error is located at the model,
    so several problems arrive as one error at one location. Everything
    else is one problem at its own location, with the prefix pydantic
    puts on a validator's ValueError stripped back off.

    One error type is rendered in this repository's words rather than
    pydantic's: an unrecognized key, whose location was the key itself
    and is now the parent it was written under, so pydantic's sentence
    would be left pointing at the wrong thing. The type is the decision
    site because it is a closed token, unlike the message. Every other
    message pydantic writes here is built from the error type and the
    field's own constraints rather than from the input, which is what
    the planted-key tests check rather than assume.
    """
    context = error.get("ctx")
    raised = context.get("error") if isinstance(context, Mapping) else None
    if isinstance(raised, FieldProblemsError):
        return raised.problems
    if dropped and error.get("type") == "extra_forbidden":
        return (FieldProblem("", UNRECOGNIZED_KEY_REFUSED),)
    message = str(error["msg"]).removeprefix("Value error, ")
    return (FieldProblem("", message),)


def secret_option_fragment(name: str) -> str | None:
    """Which of the closed fragments above an option name matched, or
    None.

    The fragment and not the name, because a refusal has to say what was
    wrong with a key it may not print: the fragments are this
    repository's own six words, so naming the one that matched tells an
    operator which key to look at without publishing what they called
    it.

    The exemption is here, inside the one function every consumer of the
    rule calls, so the write refusal, the slot check, the display mask,
    the unchanged-value marks and the record path agree by construction
    rather than by five sites being kept in step. Anywhere narrower
    would wedge the round trip: writable but masked means a resubmitted
    mask becomes a keep marker with nothing stored behind it, which
    `store._keep` refuses.

    The compare is exact and case-sensitive, on the name as it was
    written and before the lowering below. Option names are
    case-sensitive everywhere they are declared and read, so `MAX_TOKENS`
    and `Max_Tokens` are spellings nothing declares, and exempting them
    would hand the open-doors type a passthrough field this never meant
    to admit."""
    if name in _SECRET_KEY_EXEMPT_NAMES:
        return None
    lowered = name.lower()
    return next((fragment for fragment in _SECRET_KEY_FRAGMENTS if fragment in lowered), None)


def is_secret_option(name: str) -> bool:
    """Whether an option name is secret-shaped.

    One rule, three readers: it is what makes an inline value in a
    fragment an error, what decides which option names are credential
    slots a secret may be stored under, and what the display path masks
    the value of."""
    return secret_option_fragment(name) is not None


# What a value stored under such a name renders as, wherever a read
# shows one. Beside the predicate that decides which names those are,
# because the two are one rule: what is masked and what the mask looks
# like are read by the display, by the API's own examples, and by the
# write path that reads a resubmitted mask as keep-what-is-stored.
#
# Fixed rather than derived from the value: a mask whose length tracks
# the secret's is a length oracle.
MASK = "********"


def is_url_credential_parameter(name: str) -> bool:
    """Whether a URL query parameter's name says it carries a credential.

    The one home of that question, and it has three readers by design:
    `url_credential` below refuses such a URL where one is written,
    `without_url_credential` beside it takes the parameter out of
    anything built from a value that got in before the rule, and
    `printing.shown_url` is the display door. What is refused, what a
    record strips and what may be printed have to be one set, or the
    first of them is decoration.

    The undeclared fragments rather than a provider option's, which is
    the whole of what this fixes: a query parameter is named by the
    vendor whose endpoint it addresses, so `?auth=` and
    `?authorization=` are as ordinary a spelling of a credential as
    `?token=` is, and neither of them matched the narrower set (#279).
    """
    lowered = name.lower()
    return any(fragment in lowered for fragment in _UNDECLARED_SECRET_KEY_FRAGMENTS)


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
    if any(
        is_url_credential_parameter(key)
        for key, _ in parse_qsl(parts.query, keep_blank_values=True)
    ):
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
        if not is_url_credential_parameter(key)
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


# What no identity may carry: the C0 and C1 control characters and DEL.
# A slash is refused separately, because a slash is the one character
# whose presence changes what a path means rather than what it looks
# like.
#
# One home for the character class, with two readers that have to agree
# about it: `store._check_addressable` refuses one at a write, and
# `spoken_identity` below escapes one that a write never saw. A write
# that refused a set a refusal did not escape would be the rule and its
# defence disagreeing about which characters the rule is about (#414).
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def holds_control_character(value: str) -> bool:
    """Whether a name or a slot carries what the addressability rule
    refuses, which is the question the write path asks of it."""
    return _CONTROL_RE.search(value) is not None


def spoken_identity(value: str) -> str:
    """One stored identity as a sentence about it may say it: without
    what a URL of it carries, and with every control character escaped.

    The door for an identity that is SAID rather than shown. A refusal,
    a warning and a log message reach an operator as themselves, on a
    server's stderr as it fails to start and in a text-format log line,
    and nothing between the composition and that stream escapes
    anything. `store._check_addressable` refuses a control character in
    a name for exactly that reason, that it "does not survive a header
    or a log line intact", and it refuses it at WRITE time only, so a
    row written before the rule still boots and still names itself in
    every one of those sentences (#414).

    Escaped rather than replaced or withheld, because of what the
    operator holding the sentence has to do next. Such a row is
    reachable: a control character percent-encodes losslessly, unlike
    the slash a credential-bearing name holds, so `%1b` in a path
    fetches, renames and deletes it. `\\x1b` is the byte, so the
    sentence is a recipe for addressing the row; a fixed mark is not,
    and it would print two different broken names alike. Withholding
    the name leaves an operator who cannot see which row is broken.

    Only the control characters are escaped, which is what keeps every
    lawful name byte-identical: this is the identity function on
    anything a write would accept today, exactly as the credential strip
    is. The backslash is deliberately NOT escaped beside them, so a
    lawful name holding the six characters `\\x1b` renders like a name
    holding the one character. That ambiguity is a reading of two names
    rather than an addressing of either, and closing it would change how
    every lawful name holding a backslash is printed, which is the
    property the whole chain exists to keep.

    Stripped first and escaped second, and the order is the rule rather
    than a preference. `url_credential` reads the value exactly as the
    write path read it; escaping first would put backslashes into a
    value that is about to be parsed as a URL, and `urlsplit` deletes a
    tab, a carriage return and a newline before it looks for the `@`, so
    `https:/<CR>/user:password@host/x` is a credential to the strip and
    is not one once the carriage return has been spelled out. The cost
    of this order is measured and small: a control character INSIDE a
    credential-bearing name can be swallowed by that same deletion, and
    such a name is being rewritten anyway.

    Not on the DISPLAY surfaces, which is the line this rule stops at
    and the one thing measured before it was drawn. A view hands an
    identity back as a document key, and what writes that document
    escapes a control character already and losslessly: JSON as
    `\\u001b`, YAML as `\\e`, and the CLI's terminal door as `?`
    (`printing.printable`). Escaping there would mangle rather than
    render, because a read is a fragment a write of it accepts back: an
    export naming `bad\\x1bname` imports as a lawful eleven-character
    agent that nothing meant, where today the same export is refused by
    the rule that says what a name may hold.
    """
    return _CONTROL_RE.sub(_escaped, without_url_credential(value))


def _escaped(character: re.Match[str]) -> str:
    """One control character as two hex digits behind `\\x`, which is
    the spelling Python's own `repr` uses for the same set."""
    return f"\\x{ord(character.group()):02x}"


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


def check_no_inline_secrets(name: str, value: object, *, declared: bool = False) -> None:
    """A secret-shaped key holds no value, at any depth inside a
    provider's options.

    Depth is the point. A provider entry passes every option beyond the
    declared ones through to its implementation, so an option can be a
    structure, and `connection: {api_key: ...}` is as ordinary a shape
    to write as `api_key: ...` is. Checking only the top level would
    accept the nested one, store it, and read it back verbatim on every
    display path, which is exactly what the flat rule exists to prevent.

    `declared` says whether `name` is a field this model declares, and
    it decides what the refusal may say. A declared field is a name this
    repository chose, so it is named: `api_key_env` is printed, and the
    pointer addresses it. An option is a key the caller wrote, and so is
    every key under it, so the refusal names the closed fragment the key
    matched instead, and the pointer addresses the nearest place this
    repository can name, which for a top-level option is the fragment
    itself. Neither form ever carries the value: a key that fails one of
    these rules most likely holds the credential, and so, often enough,
    does the key.
    """
    _check_no_inline_secrets((name,), value, named=declared)


def _check_no_inline_secrets(
    segments: tuple[object, ...], value: object, *, named: bool
) -> None:
    """The walk itself, carrying the segments so the dotted sentence and
    the pointer stay one fact, and `named` so that going one key deeper
    cannot make a caller's key printable."""
    leaf = str(segments[-1])
    path = ".".join(str(segment) for segment in segments)
    # Named or not, the pointer is the same decision: the segments this
    # repository may address, which here is all of them or none of them.
    pointer = json_pointer(segments) if named else ""
    if leaf.lower().endswith("_env"):
        if value is not None and not is_env_name(value):
            if named:
                message = (
                    f'"{path}" must hold the name of an environment variable, and '
                    f"what it holds does not look like one; a pasted value belongs "
                    f"nowhere in this file, so name the variable holding it, for "
                    f"example {path}: MY_PROVIDER_KEY"
                )
            else:
                message = (
                    "a key ending in _env must hold the name of an environment "
                    "variable, and what this one holds does not look like one; a "
                    "pasted value belongs nowhere in this file, so name the variable "
                    "holding it. The key is not quoted back"
                )
            raise FieldProblemsError([FieldProblem(pointer, message)])
        return
    fragment = secret_option_fragment(leaf)
    if fragment is not None:
        if named:
            message = (
                f'"{path}" looks like an inline secret, which is not allowed; '
                f"reference an environment variable instead, for example "
                f"{path}_env: MY_PROVIDER_{leaf.upper()}"
            )
        else:
            message = (
                f'a key containing "{fragment}" looks like an inline secret, which is '
                f"not allowed; reference an environment variable instead, in a key of "
                f"the same name ending in _env. The key is not quoted back"
            )
        raise FieldProblemsError([FieldProblem(pointer, message)])
    if isinstance(value, Mapping):
        for key, nested in value.items():
            _check_no_inline_secrets((*segments, key), nested, named=False)
    elif isinstance(value, (list, tuple)):
        for position, item in enumerate(value):
            _check_no_inline_secrets((*segments, position), item, named=False)


def mcp_secret_fragment(name: str) -> str | None:
    """The same question for an MCP server's env and headers, where the
    key carrying a secret is as often called Authorization as token, and
    the same answer: which fragment matched, so a refusal can say so
    without printing a key the caller invented."""
    lowered = name.lower()
    return next(
        (fragment for fragment in _UNDECLARED_SECRET_KEY_FRAGMENTS if fragment in lowered), None
    )


def is_mcp_secret_key(name: str) -> bool:
    return mcp_secret_fragment(name) is not None


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
            f"`{PROGRAM} provider secret set` fills the same slot and takes precedence."
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
        check_no_inline_secrets("api_key_env", self.api_key_env, declared=True)
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
            f"guidance, and `{PROGRAM} agent preview <agent>` reports it "
            f"under "
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
        configuration file.

        Both maps are keyed by whatever was written, so a key here is
        request bytes: the refusal names the group, which is a declared
        field, and the closed fragment the key matched, and stops there.
        Two keys in one group that matched the same fragment are one
        problem, since the entries would be indistinguishable and a
        refusal that said the same thing twice would only suggest the
        second was about something else.
        """
        problems: list[FieldProblem] = []
        for group, values in (("env", self.env), ("headers", self.headers)):
            for key, value in values.items():
                fragment = mcp_secret_fragment(key)
                if fragment is None or _env_reference(value) is not None:
                    continue
                problem = FieldProblem(
                    json_pointer((group,)),
                    f'a key in {group} containing "{fragment}" looks like an inline '
                    f"secret, which is not allowed; reference an environment variable "
                    f"instead, for example $MY_SERVER_SECRET. The key is not quoted "
                    f"back",
                )
                if problem not in problems:
                    problems.append(problem)
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
            f"block costs is reported by `{PROGRAM} agent preview "
            f"<agent>`, and the "
            "operator is the one who knows what their model tolerates."
        )
    )


class FillerConfig(BaseModel):
    """Masking reply latency with a pre-synthesized filled pause.

    Off by default. When enabled, the phrases are synthesized in the
    agent's own voice ahead of time and cached as PCM; a reply whose
    first audio has not started within `delay_ms` of the utterance being
    transcribed plays one, and the real reply queues behind its tail.
    "Ahead of time" is the server start, and a reload after it: an edit
    here is synthesized again while the process runs and reaches the
    next conversation, since a conversation already open keeps the clips
    it opened with. A synthesis failure logs a warning and leaves the
    feature off for that agent rather than failing the boot or refusing
    the reload.
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


# The sentence a terminally failed reply says. Fixed configuration and
# never anything a provider answered with: what reaches this arm is a
# failure from the far side of a network, and its message is the one
# thing about a broken turn that must not be spoken out loud.
DEFAULT_FALLBACK_PHRASE = (
    "I ran into a problem and could not answer. The server log has the details."
)


class FallbackConfig(BaseModel):
    """What the user hears when a reply fails outright.

    A terminally failed reply used to be a silent turn: the failure was
    logged and nothing reached the speaker or the display, so from the
    couch a broken pipeline was indistinguishable from a slow one
    (#384). This is the short fixed phrase that ends that silence,
    synthesized in the agent's own voice at the server start and cached
    as PCM exactly the way a filler clip is, spoken from the failure arm
    and shown on the display.

    On by default, which is the one place this differs from the filler
    beside it: the silent turn is at its worst during onboarding, where
    misconfiguration is likeliest and nobody has a log open. A
    deployment that would rather have silence turns it off.

    Cached rather than synthesized when the failure happens, for the two
    reasons the filler gives and one more: synthesis at failure time
    would add latency to a turn that has already gone wrong, and the TTS
    provider may itself be what failed. A phrase that will not
    synthesize degrades rather than disappears, since the display half
    needs no audio at all: the failure still shows the sentence and
    still closes with the `tts stop` a device waits on, and only the
    audio is lost.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=True,
        description=(
            "Whether a failed reply says so out loud and on the display. On by "
            "default, because a silent turn is indistinguishable from a slow one; a "
            "deployment that would rather have silence sets this to false."
        ),
    )

    # One phrase rather than a rotation, unlike the filler's list: a
    # filler is heard on ordinary turns and a repeated one grates, while
    # this is heard when something is broken, and variety there would be
    # decoration on a diagnostic.
    phrase: NonBlankStr = Field(
        default=DEFAULT_FALLBACK_PHRASE,
        description=(
            "What a failed reply says, written in the agent's own language. Fixed "
            "configuration and never the failure's own words, which arrive from the "
            "far side of a network and are not this server's to speak."
        ),
    )


class MemoryPolicy(BaseModel):
    """Whether an agent remembers anything at all.

    On by default, because every deployment has the schema behind it and
    an assistant that cannot remember is the exception rather than the
    shape. Switched off, the switch is the whole thing: the agent is
    offered none of the memory tools and its prompt carries none of the
    scope blocks, its device's included, so an agent that may not
    remember cannot read what its siblings on the same board accrued
    either. A half-off agent, told what the room knows and unable to
    write it down, would be a worse answer than either whole one.

    What the switch does not do is delete anything. Rows already stored
    under the agent's name stay stored and stay visible to the operator
    surface, and switching the section back on is an agent that
    remembers what it remembered before. Erasing is `vinga memory
    delete`, which is a different act with a different door.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=True,
        description=(
            "Whether this agent may remember anything. On by default. Off withholds "
            "the whole family at once: the memory tools are not offered and no "
            "remembered facts, device notes or conversation state are injected into "
            "the prompt, so the agent can neither write memory nor read it. Nothing "
            "already stored is deleted."
        ),
    )


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
    publishing rule, it is what `vinga mcp-server status` shows and what
    the model calls, so an operator writes down the name they read.
    What a server listed before the rule got to it never appears on a
    vinga surface, and cannot be granted by.
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
            f"is the name `{PROGRAM} mcp-server status` shows. Leaving it out grants "
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
# out of the request and may be anything at all. This was the first
# refusal held to that rule; `safe_location` is the rule itself, and
# this is now one of its readers.
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
    safe, dropped = safe_location(McpGrant, location)
    parts = [str(part) for part in safe]
    if dropped:
        parts.append(_UNRECOGNIZED_KEY)
    return ".".join(parts)


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
            "siblings have. The builtin tools are outside this list: the memory "
            "family is offered wherever the memory section leaves it on, and "
            "switch_agent appears under a structural condition (a device bound to "
            "more than one agent) rather than by grant."
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

    # What a failed reply says. None means inherit, and a section
    # replaces the inherited one wholly exactly as `filler` does, so
    # `fallback: {enabled: false}` opts an agent out. On where neither
    # layer names it, which is the opposite default to the filler's and
    # is the issue's own asymmetry: masking latency is an enhancement,
    # and saying that a turn broke is the difference between a
    # diagnosable deployment and a silent one.
    fallback: FallbackConfig | None = Field(
        default=None,
        description=(
            "What a failed reply says out loud and on the display. Unset inherits the "
            "agent_defaults section, and an agent under neither speaks the declared "
            "default phrase, which is the default; naming a section replaces the "
            "inherited one wholly rather than merging with it, so "
            "`fallback: {enabled: false}` opts an agent out and leaves its failed "
            "turns silent."
        ),
    )

    # Whether this layer remembers anything. None means inherit, and a
    # section replaces the inherited one wholly exactly as `filler`
    # does, so `memory: {enabled: false}` opts an agent out. On where
    # neither layer names it, which is what keeps every deployment
    # written before this field behaving as it did.
    memory: MemoryPolicy | None = Field(
        default=None,
        description=(
            "Whether this layer may remember anything. Unset inherits the "
            "agent_defaults section, and an agent under neither may remember, which "
            "is the default; naming a section replaces the inherited one wholly "
            "rather than merging with it, so `memory: {enabled: false}` opts an "
            "agent out of the memory tools and the injected scope blocks together."
        ),
    )

    # The shared fragments this layer's prompt carries. None means
    # inherit; a list replaces rather than extends, exactly like `mcp`,
    # so a layer naming an empty list opts out of the fragments its
    # siblings share.
    #
    # `AgentConfig` re-declares this field for its description alone.
    # The two layers are in two regimes: an agent's own list is what a
    # reload applies, and this one is what every agent's effective value
    # is inherited THROUGH, which a reload deliberately does not move
    # (`config/reload.py`). One description covering both would be right
    # about one layer and wrong about the other, which is the same trap
    # the agent write's notice exists to close.
    prompt_includes: list[NonBlankStr] | None = Field(
        default=None,
        description=(
            "The shared prompt fragments every agent's system prompt carries unless the "
            "agent names a list of its own, each by the name it is defined under in "
            "prompt_fragments, injected in the order listed and directly after the "
            "agent's own prompt. An agent naming a list replaces this one rather than "
            "extending it, so an empty list there opts that agent out of the fragments "
            "its siblings share. Every name has to be a fragment that exists, since the "
            "fragment is in this same database, and a name listed twice is refused. "
            "A reload applies this list, along with an agent's own and the text of a "
            "fragment either layer names, so a change here reaches every agent that "
            "inherits it at that agent's next activation, which is a new session or an "
            "agent switch."
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

    # Re-declared from `AgentDefaults` for its description and nothing
    # else: same type, same default, same validator (which is bound by
    # field name and inherited). The two rows describe two different
    # things, an agent's own list against the layer every agent that
    # names none inherits, and each is read in its own section of the
    # generated reference by somebody editing that entity. They used to
    # describe two regimes as well; they no longer do, and the sentence
    # each carries about when an edit lands says the same thing.
    prompt_includes: list[NonBlankStr] | None = Field(
        default=None,
        description=(
            "The shared prompt fragments this agent's system prompt carries, each by "
            "the name it is defined under in prompt_fragments, injected in the order "
            "listed and directly after the agent's own prompt. Unset inherits the "
            "agent_defaults list; naming a list replaces the inherited one rather than "
            "extending it, so an empty list opts this agent out of the fragments its "
            "siblings share. Every name has to be a fragment that exists, since the "
            "fragment is in this same database, and a name listed twice is refused. "
            "A reload applies this list, so an edit here reaches a conversation at its "
            "next activation, which is a new session or an agent switch. Leaving it "
            "unset inherits the agent_defaults list, whose edits reach this agent at "
            "the same moment."
        ),
    )

    prompt: str = Field(
        default="",
        description=(
            "The instruction this agent replies under, sent as the system "
            "prompt on every turn. State the reply language explicitly: a model "
            "otherwise picks one by its training bias."
        ),
    )


# What something that is not a MAC is told: the rule, and never the
# value that failed it (#205).
#
# The same reasoning the entity misses and the stage and slot refusals
# were fixed under (#132), one step earlier. This check runs on a value
# the caller typed, before anything is looked up, and the places it is
# typed are a URL path segment, a command-line argument and a device's
# own header, which is where a paste lands. A value that failed this
# check is by definition one nothing here has validated, and it travels
# out as an API body, as a printed line, and into whatever the caller
# keeps.
#
# No section prefix, unlike the store's own refusals. Every surface that
# renders this supplies its own location: the loader names the field the
# unusable MAC was written under, and a prefix here would be said twice.
# The rule itself has one home, which is this constant, so no caller
# restates it.
NOT_A_MAC = "a MAC address is six colon-separated hex pairs, for example aa:bb:cc:dd:ee:ff"


def normalize_mac(value: str) -> str:
    """Normalize a MAC address to lowercase colon-separated form.

    The refusal is `NOT_A_MAC`, fixed: it carries the rule and not the
    value, so a caller may hand this exception's message to any surface
    without reading it first.
    """
    mac = value.strip().lower().replace("-", ":")
    if not _MAC_RE.match(mac):
        raise ValueError(NOT_A_MAC)
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


# What a rejected MCP entry name is told, wherever it is rejected. The
# sibling of `PROMPT_FRAGMENT_NAME_RULE` below and now the same shape:
# it names the section and the rule and never the name, because a name
# that fails the charset is exactly the string that must not be echoed,
# and this sentence travels out as a CLI line, an HTTP 422 body and a
# boot log.
#
# The reserved words are quoted, and they may be: they are constants of
# this server rather than anything a request wrote.
MCP_ENTRY_NAME_RULE = (
    "mcp_servers: an entry name becomes a tool-name prefix, so it must match "
    "[A-Za-z0-9_-]+ and must not be one of: "
    + ", ".join(names.RESERVED_ENTRY_NAMES)
    + ". The name is not quoted back: what fails this rule is the kind of string "
    "that must not be echoed"
)


def check_mcp_entry_names(value: dict[str, McpServerConfig]) -> dict[str, McpServerConfig]:
    """An entry name becomes a tool-name prefix, so it has to be a legal
    tool name, and it may not be one the merged list already uses. That
    is what makes a namespace collision unrepresentable rather than
    something to resolve at merge time.

    One sentence however many names fail it, and never the names: a
    section keyed by what the operator wrote has no position to point at
    the way a list does, so what is left to say is the rule. Two
    identical lines would only suggest the second was about something
    else.
    """
    if any(not names.is_valid_entry_name(name) for name in value):
        raise ValueError(MCP_ENTRY_NAME_RULE)
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

    It names the section and the rule and never the name, because a name
    that fails the charset is exactly the string that must not be
    echoed: what was written there may be a pasted credential, and a
    valid name is the only kind any surface here prints. This was the
    first of the two name rules written that way and
    `check_mcp_entry_names` above, which used to interpolate what it
    rejected, has joined it.
    """
    if any(not is_valid_fragment_name(name) for name in value):
        raise ValueError(PROMPT_FRAGMENT_NAME_RULE)
    return value


def normalize_device_bindings(value: object) -> object:
    """The devices mapping with every MAC in its canonical form, and
    every binding held to the rules a list of agent names has to
    satisfy. Anything that is not a mapping is left for pydantic to
    report."""
    if not isinstance(value, dict):
        return value
    normalized: dict[str, object] = {}
    for mac, bound in value.items():
        key = normalize_mac(str(mac))
        if key in normalized:
            raise ValueError(f'device "{mac}" appears more than once (as {key})')
        _check_binding(key, bound)
        normalized[key] = bound
    return normalized


# What each domain section is, read by every rendering of the domain
# half: the generated reference, the JSON Schema a client reads before
# writing a fragment, and the CLI help. Written here rather than on the
# model below so that `Setting`, which describes a domain-level field
# that is not an entity, reads the same prose from the same place.
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


# The domain half, declared once and subclassed by `Config` below,
# which adds the file half and the boot-time whole-snapshot validator.
# The repository validates a write against this class and never against
# that one, which is why the two are related by inheritance rather than
# by a second copy of these seven fields: `check_completeness` is a rule
# about a runnable server, and running it at write time would refuse the
# first `set agent` into an empty database.
#
# The docstring below is output, and nothing checks it. `config schema`
# prints this model's JSON Schema, where a pydantic model's docstring
# becomes the schema's `description`, so editing that docstring changes
# what the command prints, silently: no committed artifact carries this
# rendering and no test asserts it, unlike `api-openapi.json` and
# `domain-config.md`, which CI regenerates and diffs. #242 verified the
# schema byte for byte by hand at every commit, and made the trip once
# before splitting the two: what is said about this class to a reader of
# the code is said here in a comment, and what is said to a reader of
# the document is said in the docstring. An editor of either should know
# which one they are writing.
#
# Nothing here may become an after-validator, now or later.
# `store._read_domain` assembles the keyed sections through this model
# and then assigns `agent_defaults` and `default_agent` onto the
# instance it got back, so a model validator would run before those two
# rows are in place and judge a half-read snapshot that never existed.
# The rules about a whole domain half are `check_references` and
# `check_completeness` below, run by the store at write time and by
# `Config` at boot.
class DomainConfig(BaseModel):
    """The domain half of a configuration, as the database holds it.

    The same entity models the YAML file is validated through, in the
    same shape, so nothing about a loaded snapshot is a second dialect
    of the configuration. What it does not hold is secrets: those ride
    beside it in a SecretStore.
    """

    model_config = ConfigDict(extra="forbid")

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
    agent_defaults: AgentDefaults = Field(
        default_factory=AgentDefaults, description=DOMAIN_DESCRIPTIONS["agent_defaults"]
    )
    agents: dict[NonBlankStr, AgentConfig] = Field(
        default_factory=dict, description=DOMAIN_DESCRIPTIONS["agents"]
    )
    # One device may be bound to several agents; the value is a list of
    # agent names, the first of them the one a conversation starts on.
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


class DomainSnapshot(Protocol):
    """The domain half of a configuration, whatever is holding it.

    The checks below are written against the attributes a domain half
    has rather than against the class above, because what they judge is
    a set of sections and their references. The store passes a
    `DomainConfig`, boot passes the `Config` that subclasses it, and the
    suites that exercise the rules themselves pass a stand-in holding
    the same seven sections, which is the interface this states. Neither
    check needs the server half, which is why a snapshot is enough.
    """

    providers: ProvidersConfig
    mcp_servers: dict[str, McpServerConfig]
    prompt_fragments: dict[str, PromptFragmentConfig]
    agent_defaults: AgentDefaults
    agents: dict[str, AgentConfig]
    devices: dict[str, list[str]]
    default_agent: str | None


def defined(what: str, names: Collection[str]) -> str:
    """What could have been meant, which is the names this deployment
    has.

    The half of a reference refusal that may be quoted, and the reason
    the other half does not have to be: a name that did not resolve was
    written by whoever wrote the entity, and the names that DID resolve
    were written by this deployment. One helper because five refusals
    say it and a fifth spelling would be a fifth shape for one fact.

    Written by this deployment is not the same as written under today's
    rules. A stored name can predate the addressability rule and carry a
    credential or a control character, and this sentence travels out as
    a CLI line, an HTTP 422 body and a boot log, so each name leaves
    through the door every spoken identity leaves through (#381, #414).
    Sorted on the name as it is STORED and shortened afterwards, for the
    reason `views._shown_mapping` gives: sorting after the strip would
    let what a name hides decide where it appears.
    """
    if not names:
        return f"; no {what} are defined"
    shown = ", ".join(spoken_identity(name) for name in sorted(names))
    return f" (defined: {shown})"


def check_references(snapshot: DomainSnapshot) -> list[str]:
    """Every reference in the snapshot resolving: agents and
    agent_defaults to providers and MCP servers, device bindings to
    agents, default_agent to an agent.

    Run at write time as well as at boot. A reference that does not
    resolve is a broken entity whenever it is written, and refusing it
    at the write is what forces the natural creation order (providers,
    MCP servers, agents, devices) rather than discovering the mistake at
    the next restart.

    Not one of these refusals quotes the name it could not resolve, and
    that is the whole shape of them. A reference is written beside
    prompt text and provider options, so it is a place a paste lands as
    much as any other; the sentence travels out as a CLI line, an HTTP
    422 body and a boot log; and the charset rules do not close it,
    since a credential can be written in [A-Za-z0-9_-]. So each names
    the FIELD PATH, which says which entry to look at, and the names
    that do exist, which say what could have been meant, and both of
    those are written by this deployment rather than by the request.
    `prompt_includes` was given that shape when it was added (#178) and
    the other four have joined it, so that one kind of mistake has one
    vocabulary however it was reached.
    """
    problems: list[str] = []

    if snapshot.default_agent is not None and snapshot.default_agent not in snapshot.agents:
        problems.append(
            "default_agent: names no agent that exists, and the name is not quoted "
            "back" + defined("agents", snapshot.agents)
        )

    # The MAC is the path here and is quoted: it is not a name somebody
    # chose, it is the canonical form of an address the binding was
    # normalized to, and a value that is not one never gets this far.
    # What the entry holds is a name, so it is named by its position.
    for mac, bound in snapshot.devices.items():
        for position, agent in enumerate(bound, start=1):
            if agent not in snapshot.agents:
                problems.append(
                    f"devices.{mac}: entry {position} names no agent that exists, and "
                    f"the name is not quoted back" + defined("agents", snapshot.agents)
                )

    # Each layer's own references are checked where they are written,
    # so a wrong default is reported once as agent_defaults.llm rather
    # than once per agent that inherits it.
    #
    # The agent's own name is the location here, which is the store's
    # vocabulary and stays (#382); what a name written before the
    # addressability rule can carry does not, so it leaves through the
    # one door a spoken identity leaves through (#381, #414).
    sources: list[tuple[str, AgentDefaults]] = [("agent_defaults", snapshot.agent_defaults)]
    sources += [
        (f"agents.{spoken_identity(name)}", agent) for name, agent in snapshot.agents.items()
    ]
    for source, layer in sources:
        for stage in PROVIDER_STAGES:
            ref = getattr(layer, stage)
            if ref is None:
                continue
            available = getattr(snapshot.providers, stage)
            if ref not in available:
                problems.append(
                    f"{source}.{stage}: names no {stage} provider that exists, and the "
                    f"name is not quoted back"
                    + defined(f"providers.{stage} entries", available)
                )
        # Both entry forms name a server, so both are checked here: an
        # allow list on a server that does not exist is the same broken
        # reference as a bare name that does not.
        for position, entry in enumerate(layer.mcp or [], start=1):
            server = as_mcp_grant(entry).server
            if server not in snapshot.mcp_servers:
                problems.append(
                    f"{source}.mcp: entry {position} names no MCP server that exists, "
                    f"and the name is not quoted back"
                    + defined("mcp_servers entries", snapshot.mcp_servers)
                )
        # An include is checked here rather than deferred the way a
        # grant's tool allow list is: the referent is a row in this same
        # database, so nothing about it waits for a live connection.
        for position, include in enumerate(layer.prompt_includes or [], start=1):
            if include not in snapshot.prompt_fragments:
                problems.append(
                    f"{source}.prompt_includes: entry {position} names no prompt "
                    f"fragment that exists, and the name is not quoted back"
                    + defined("prompt_fragments entries", snapshot.prompt_fragments)
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
            "bound to one; set it to one of: "
            # The names this deployment stored, through the door
            # `defined` above sends the same list through and for the
            # same reason.
            + ", ".join(spoken_identity(name) for name in sorted(snapshot.agents))
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


# The file half's YAML, already read and already parsed, set by the
# loader around instantiation. A ContextVar because pydantic-settings
# has no init kwarg for a runtime-chosen source yet
# (pydantic-settings#259).
#
# The mapping rather than the path it came from, which is the whole
# point (#291). A path here meant the file was opened and parsed twice,
# once by the loader's no-leak boundary and once by
# `YamlConfigSettingsSource` behind it, and the second read answered to
# nobody: a file deleted between the two booted the defaults in silence,
# one that turned malformed left as the parser's own `ScannerError` with
# the path and the offending line in it, one that turned into bytes left
# as a `UnicodeDecodeError`, and the second read did not even name an
# encoding. What the settings machinery gets now is the object the
# boundary already validated, so there is one read, one parse and one
# refusal.
yaml_data_var: ContextVar[Mapping[str, object] | None] = ContextVar(
    "vinga_yaml_data", default=None
)


class FileConfig(BaseSettings):
    """The half of the configuration the YAML file holds.

    `server` alone: the domain half moved to the database, and a file
    that still names it is refused by the loader with the command that
    writes it instead. `memory:` was here too until remembered facts
    moved into the database as well (#314), and a file that still
    carries it is refused the same way. The VINGA_ environment
    overrides are unchanged for what is left (VINGA_SERVER__PORT keeps
    working), which is why this is still a settings model and `Config`
    is not.
    """

    model_config = SettingsConfigDict(
        extra="forbid",
        env_prefix=ENV_PREFIX,
        env_nested_delimiter=ENV_NESTING,
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
            # In the position the YAML source held, so the priority is
            # unchanged: a VINGA_ variable still beats the file and the
            # file still beats the defaults. `YamlConfigSettingsSource`
            # is this class plus a read, which is the read that is gone.
            InitSettingsSource(settings_cls, dict(yaml_data_var.get() or {})),
            file_secret_settings,
        )

    server: ServerConfig = Field(default_factory=ServerConfig)


class Config(DomainConfig):
    """The whole configuration one server boots on: the file half plus
    the domain half the database holds.

    Composed rather than loaded, since its two halves come from two
    places, and it keeps its name, its attribute paths, its helper
    methods and its boot-time validator so that everything downstream of
    it reads the configuration exactly as it did when one file held all
    of it.

    A subclass rather than a second declaration of the domain half: the
    seven sections and their three field validators are `DomainConfig`'s,
    and what a whole configuration adds is `server`, the accessors, and
    the model validator that judges the snapshot at boot.
    A subclass declares its own fields after the ones it inherits, so the
    domain sections come first here and the file half last, which no
    caller reads: this model is composed by keyword and never rendered,
    and the reference and the JSON Schema are `DomainConfig`'s.
    """

    server: ServerConfig = Field(default_factory=ServerConfig)

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
        so a mistake points at the layer that holds it.

        Which makes the location a place an identity leaves this model
        by, so the agent's name goes through the one door every spoken
        identity goes through (#381, #382, #414). The provider name
        beside it does not: that half is an address, read straight back
        out of `providers.<stage>` by everything this answers, and
        rewriting it would be a lookup of a row nothing wrote.
        """
        own = getattr(self.agents[agent], stage)
        if own is not None:
            return own, f"agents.{spoken_identity(agent)}.{stage}"
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

    def fallback_for_agent(self, agent: str) -> FallbackConfig:
        """What a failed reply says for this agent: its own section when
        it names one, agent_defaults' otherwise, and the declared
        default when neither does. A section replaces rather than
        merges, so an agent's own phrase is all of its section.

        A section rather than None where nothing is written, the shape
        `memory_for_agent` takes and for the same reason: an absent
        `filler` section and one that is off mean the same thing, and an
        absent `fallback` section means the opposite of an off one.
        Answering with the model's own defaults keeps that difference
        where the field declares it rather than at every call site.
        """
        own = self.agents[agent].fallback
        if own is not None:
            return own
        inherited = self.agent_defaults.fallback
        return inherited if inherited is not None else FallbackConfig()

    def memory_for_agent(self, agent: str) -> MemoryPolicy:
        """Whether this agent may remember anything: its own section when
        it names one, agent_defaults otherwise, and the declared default
        when neither does.

        A section rather than None where nothing is written, which is the
        one place this differs from `filler_for_agent`: an absent filler
        section and one that is off mean the same thing, and an absent
        memory section means the opposite of an off one. Answering with
        the model's own defaults keeps that difference where the field
        declares it rather than at every call site.
        """
        own = self.agents[agent].memory
        if own is not None:
            return own
        inherited = self.agent_defaults.memory
        return inherited if inherited is not None else MemoryPolicy()

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


def _check_binding(mac: str, bound: object) -> None:
    """The two rules a binding written as a list has to satisfy that its
    type cannot state: at least one agent, and no agent named twice.

    Anything not written as a list is left alone here and reported by
    pydantic against the field's own type, which is where the shape a
    binding has to have is declared. That leaves a sequence pydantic's
    lax mode would coerce to a list, a tuple or a set, passing these two
    rules unchecked; no transport can deliver one (JSON has no tuple,
    YAML no set, and the database stores a JSON array), so the gap is
    reachable only by constructing the model in-process and is left as
    it was rather than closed with a refusal path this milestone did not
    come to add.

    The duplicate refusal points at positions rather than at what is in
    them, the rule every list in this file follows: an agent name is
    written on a command line and in a document beside prompt text, so
    it is a place a paste lands, and this sentence leaves the boundary
    as a printed CLI line, an HTTP 422 body and a boot log. The MAC is
    quoted because it is not a name somebody chose: it is the canonical
    form the binding was normalized to, and a value that is not one
    never reaches here.
    """
    if not isinstance(bound, list):
        return
    if not bound:
        raise ValueError(f"devices.{mac}: bind the device to at least one agent")
    # Compared as they will be stored, which is trimmed, so that
    # `sam` and ` sam ` are the one name they will become.
    named = [name.strip() for name in bound if isinstance(name, str)]
    repeated = _repeated_positions(named)
    if repeated:
        raise ValueError(
            f"devices.{mac}: one agent is named at more than one position "
            f"({repeated}); name each agent once, and the name is not quoted back"
        )
