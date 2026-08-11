"""The repository over the domain tables: rows to models and back.

Every semantic decision about the domain configuration lives here and
not in the code that calls it: parsing a fragment through the existing
pydantic models, refusing a write that would leave a reference
unresolved, keeping stored secrets out of the models and out of entity
replacement. The CLI is one caller; the REST API will be the other, and
it is meant to mount this object behind HTTP rather than restate any of
it.

A write is one transaction. The engine begins every transaction with
BEGIN IMMEDIATE (see `samtal_server.db`), so the write lock is taken
before the snapshot is read: two concurrent writers cannot each
validate against the state before the other's change and then persist
over one another. A lock that does not arrive inside the busy timeout
fails the command with a retryable error rather than half-applying it.

Reads run under the same transaction, and so take the same lock. A
deferred read path was considered and left out: a load is a handful of
small selects, the only readers are a booting server and a CLI
invocation, and a second connection configuration would be a second
thing to keep true for a contention these never produce.

Write-time validation is the reference half only. Completeness (a
runnable server's rules) belongs to boot, because enforcing it here
would deadlock the natural creation order: providers, MCP servers,
agents, devices, and default_agent last.
"""

import math
import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

from cryptography.fernet import MultiFernet
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import Connection, Engine, Row, Table, delete, insert, select, update
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.sql.elements import ColumnElement

from samtal_server.config.loader import (
    ConfigError,
    DatabaseBusyError,
    StorageError,
    UnknownEntityError,
)
from samtal_server.config.models import (
    DOMAIN_DESCRIPTIONS,
    PROVIDER_STAGES,
    AgentConfig,
    AgentDefaults,
    McpServerConfig,
    NonBlankStr,
    ProviderConfig,
    ProvidersConfig,
    check_mcp_entry_names,
    check_references,
    is_env_name,
    is_secret_option,
    normalize_device_bindings,
    normalize_mac,
)
from samtal_server.config.secrets import EntityKind, SecretLocation, SecretStore, encrypt
from samtal_server.db import schema

# The two groups of an MCP server's dotted secret slots. A slot is
# `env.<KEY>` or `headers.<KEY>`, which is where the value would have
# been written as a $VAR reference.
MCP_SECRET_GROUPS = ("env", "headers")

# What no identity may carry: the C0 and C1 control characters and DEL.
# A slash is refused separately, because a slash is the one character
# whose presence changes what a path means rather than what it looks
# like.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")

# An HTTP header name, RFC 9110's token production. The key half of a
# `headers.` slot names the header a request would carry, so what a
# request could never carry is not a slot.
_HEADER_TOKEN_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")

# What a stored row that will not validate is told about. The same
# sentence the column-shape refusals end with, because it is the same
# situation: the request was fine and the stored state is not.
_UNREADABLE_ROW = "the row cannot be read as configuration:"
_UNREADABLE_ROWS = "the stored configuration cannot be read:"

# NaN and the infinities are not JSON, whatever a YAML parser accepts.
# The message names where the value sits and the rule, which is all
# there is to say about it.
_NOT_FINITE = (
    "{where} is not a finite number, and NaN and infinity cannot be written as JSON, "
    "so a reader of this configuration would be given null in its place"
)

# The rest of what YAML can express and JSON cannot. Each names where it
# is and what kind of thing it is, and never the value: a fragment
# refused here is one nobody has validated yet, so it may hold anything.
_NOT_TRANSPORTABLE = (
    "{where} is a {kind}, which JSON has no way to write, so this configuration "
    "could not be stored or read back as what it says"
)

_RECURSIVE = (
    "{where} contains itself. A configuration that refers to itself has no written "
    "form, so it cannot be stored or read back"
)

_NON_STRING_KEY = (
    "{where} has a key that is a {kind} rather than a string. JSON names every key "
    "with a string, so such a key would silently become one and a reader would be "
    "given a key nobody wrote"
)


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
    mcp_servers: dict[NonBlankStr, McpServerConfig] = Field(
        default_factory=dict, description=DOMAIN_DESCRIPTIONS["mcp_servers"]
    )
    agent_defaults: AgentDefaults = Field(
        default_factory=AgentDefaults, description=DOMAIN_DESCRIPTIONS["agent_defaults"]
    )
    agents: dict[NonBlankStr, AgentConfig] = Field(
        default_factory=dict, description=DOMAIN_DESCRIPTIONS["agents"]
    )
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


@dataclass(frozen=True)
class Snapshot:
    """One load: the domain models, and the stored secrets beside them."""

    domain: DomainConfig
    secrets: SecretStore


@dataclass(frozen=True)
class StoredSecret:
    """One stored secret, named rather than read: where it is, and what
    its presence displaces.

    `shadows` is the precedence rule made visible. A stored secret wins
    over a reference written for the same slot, and a read that showed
    only the reference would say the opposite of what the server does,
    so the read names the key the ciphertext takes the place of.
    """

    location: SecretLocation
    shadows: str | None


@dataclass(frozen=True)
class Entity[Entry]:
    """One entity as a read returns it: its model-shaped half, and the
    slots holding a stored secret beside it.

    Never the secrets themselves. A read is masked by design, and what a
    caller needs that the entity cannot carry is which slots are filled
    from the database rather than from the environment.
    """

    entry: Entry
    secrets: tuple[StoredSecret, ...]


def verify_secrets(secrets: SecretStore) -> None:
    """Every stored secret opens under the configured keys, or the
    server refuses to start naming the entity and the slot.

    Startup only, and deliberately not part of opening the database: a
    missing key, a wrong key or a corrupt token is exactly when the CLI
    is the recovery tool, and a check that ran on open would take the
    recovery tool away along with the server.

    Exhaustive rather than lazy, because the alternative is discovering
    a rotation mistake on the first conversation that needs the third
    provider. The plaintext is discarded as it is produced: this is a
    check that the keys are right, not a place secrets live.
    """
    for location in secrets.locations():
        secrets.secret(location)


class ConfigStore:
    """Reads and writes the domain configuration in one database."""

    def __init__(self, engine: Engine, keys: MultiFernet | None = None) -> None:
        self._engine = engine
        self._keys = keys

    # Loading

    def load(self) -> Snapshot:
        """The whole domain configuration, plus its stored secrets."""
        with self._transaction() as connection:
            return Snapshot(
                domain=_read_domain(connection),
                secrets=_read_secrets(connection, self._keys),
            )

    # Reading one entity
    #
    # Existence is semantics, so it is decided here and not by each
    # caller: `config show`, the API's GET and the recovery path all meet
    # the same refusal in the same words, and a caller that answers with
    # a status code can tell it from the others by its type. What each
    # read returns beside the entity is its stored-secret slots, which is
    # the one fact a masked read exists to convey and the one thing the
    # model-shaped half can never carry.

    def read_provider(self, stage: str, name: str) -> Entity[ProviderConfig]:
        stage = _stage(stage)
        with self._transaction() as connection:
            entry = getattr(_read_domain(connection).providers, stage).get(name)
            if entry is None:
                raise UnknownEntityError(f"providers.{stage}.{name}: no such provider")
            return self._with_secrets(connection, entry, "provider", f"{stage}.{name}")

    def read_mcp_server(self, name: str) -> Entity[McpServerConfig]:
        with self._transaction() as connection:
            entry = _read_domain(connection).mcp_servers.get(name)
            if entry is None:
                raise UnknownEntityError(f"mcp_servers.{name}: no such MCP server")
            return self._with_secrets(connection, entry, "mcp_server", name)

    def read_agent(self, name: str) -> Entity[AgentConfig]:
        with self._transaction() as connection:
            entry = _read_domain(connection).agents.get(name)
            if entry is None:
                raise UnknownEntityError(f"agents.{name}: no such agent")
            # An agent holds no credential of its own: it references
            # providers and MCP servers, and theirs are stored on them.
            return Entity(entry=entry, secrets=())

    def read_agent_defaults(self) -> Entity[AgentDefaults]:
        """The singleton, which always exists: an unwritten one is the
        empty entry rather than a missing entity."""
        with self._transaction() as connection:
            return Entity(entry=_read_domain(connection).agent_defaults, secrets=())

    def read_device(self, mac: str) -> Entity[list[str]]:
        """One device's binding, keyed by the canonical form of its MAC,
        so `AA-BB-...` and `aa:bb:...` read the same row."""
        normalized = _mac(mac)
        with self._transaction() as connection:
            bound = _read_domain(connection).devices.get(normalized)
            if bound is None:
                raise UnknownEntityError(f"devices.{normalized}: no such device")
            return Entity(entry=list(bound), secrets=())

    def read_default_agent(self) -> str | None:
        """The agent an unbound device reaches, or None. Unset is a
        configuration rather than a missing entity, so there is nothing
        here to refuse."""
        with self._transaction() as connection:
            return _read_domain(connection).default_agent

    def _with_secrets[Entry](
        self, connection: Connection, entry: Entry, kind: EntityKind, identity: str
    ) -> Entity[Entry]:
        secrets = _read_secrets(connection, self._keys)
        return Entity(entry=entry, secrets=_stored_slots(entry, kind, identity, secrets))

    # Entities

    def set_provider(self, stage: str, name: str, fragment: object) -> None:
        """Create or replace `providers.<stage>.<name>` from a fragment
        in the same shape the YAML section has.

        The row's model-shaped half is what is replaced; its stored
        secrets are not touched. A fragment cannot carry ciphertext by
        design, so a whole-row replacement would silently erase every
        stored secret on an ordinary edit.
        """
        stage = _stage(stage)
        name = _identifier(f"providers.{stage}", name)
        entry = _parse(ProviderConfig, f"providers.{stage}.{name}", fragment)
        with self._transaction() as connection:
            domain = _read_domain(connection)
            getattr(domain.providers, stage)[name] = entry
            _refuse_unresolved(domain)
            _upsert(
                connection,
                schema.providers,
                {"stage": stage, "name": name},
                _provider_values(entry),
            )

    def delete_provider(self, stage: str, name: str) -> None:
        stage = _stage(stage)
        with self._transaction() as connection:
            # The row carries its own secrets column, so deleting the
            # entity deletes its stored secrets with it.
            _delete_row(
                connection,
                schema.providers,
                (
                    schema.providers.c.stage == stage,
                    schema.providers.c.name == name,
                ),
                f"providers.{stage}.{name}: no such provider",
            )

    def set_mcp_server(self, name: str, fragment: object) -> None:
        name = _identifier("mcp_servers", name)
        entry = _parse(McpServerConfig, f"mcp_servers.{name}", fragment)
        problem: str | None = None
        try:
            check_mcp_entry_names({name: entry})
        except ValueError as exc:
            problem = str(exc)
        if problem is not None:
            raise ConfigError(problem)
        with self._transaction() as connection:
            domain = _read_domain(connection)
            domain.mcp_servers[name] = entry
            _refuse_unresolved(domain)
            _upsert(connection, schema.mcp_servers, {"name": name}, _mcp_values(entry))

    def delete_mcp_server(self, name: str) -> None:
        with self._transaction() as connection:
            _delete_row(
                connection,
                schema.mcp_servers,
                (schema.mcp_servers.c.name == name,),
                f"mcp_servers.{name}: no such MCP server",
            )

    def set_agent(self, name: str, fragment: object) -> None:
        name = _identifier("agents", name)
        entry = _parse(AgentConfig, f"agents.{name}", fragment)
        with self._transaction() as connection:
            domain = _read_domain(connection)
            domain.agents[name] = entry
            _refuse_unresolved(domain)
            _upsert(
                connection,
                schema.agents,
                {"name": name},
                {"prompt": entry.prompt, **_layer_values(entry)},
            )

    def delete_agent(self, name: str) -> None:
        """Refused while a device binding or default_agent still names
        it, by the same reference pass every other write runs."""
        with self._transaction() as connection:
            _delete_row(
                connection,
                schema.agents,
                (schema.agents.c.name == name,),
                f"agents.{name}: no such agent",
            )

    def set_agent_defaults(self, fragment: object) -> None:
        entry = _parse(AgentDefaults, "agent_defaults", fragment)
        with self._transaction() as connection:
            domain = _read_domain(connection)
            domain.agent_defaults = entry
            _refuse_unresolved(domain)
            _upsert(
                connection,
                schema.agent_defaults,
                {"id": schema.AGENT_DEFAULTS_ID},
                _layer_values(entry),
            )

    # Devices and the default agent

    def bind_device(self, mac: str, agents: Sequence[str]) -> None:
        binding = _binding(mac, list(agents))
        with self._transaction() as connection:
            domain = _read_domain(connection)
            domain.devices.update(binding)
            _refuse_unresolved(domain)
            for normalized, bound in binding.items():
                _upsert(connection, schema.devices, {"mac": normalized}, {"agents": bound})

    def delete_device(self, mac: str) -> None:
        normalized = _mac(mac)
        with self._transaction() as connection:
            _delete_row(
                connection,
                schema.devices,
                (schema.devices.c.mac == normalized,),
                f"devices.{normalized}: no such device",
            )

    def set_default_agent(self, name: str) -> None:
        name = _identifier("default_agent", name)
        with self._transaction() as connection:
            domain = _read_domain(connection)
            domain.default_agent = name
            _refuse_unresolved(domain)
            _upsert(
                connection,
                schema.domain_settings,
                {"key": schema.DEFAULT_AGENT_KEY},
                {"value": name},
            )

    def clear_default_agent(self) -> None:
        """Back to the devices map as the allowlist, which is a
        configuration rather than a degenerate state. The row is deleted
        rather than nulled, so there is one way to say it."""
        with self._transaction() as connection:
            connection.execute(
                delete(schema.domain_settings).where(
                    schema.domain_settings.c.key == schema.DEFAULT_AGENT_KEY
                )
            )

    # Secrets

    def set_secret(self, location: SecretLocation, secret: str) -> None:
        """Store one credential, encrypted under the newest configured
        key. The only write that needs a key at all."""
        _secret_value(location, secret)
        with self._transaction() as connection:
            domain = _read_domain(connection)
            _check_slot(domain, location)
            envelope = encrypt(location, secret, self._keys)
            stored = dict(_stored_secrets(connection, location))
            stored[location.slot] = envelope
            _write_secrets(connection, location, stored)

    def clear_secret(self, location: SecretLocation) -> None:
        with self._transaction() as connection:
            stored = dict(_stored_secrets(connection, location))
            if location.slot not in stored:
                raise UnknownEntityError(
                    f"{location.describe()}: no secret is stored for this slot"
                )
            del stored[location.slot]
            _write_secrets(connection, location, stored)

    @contextmanager
    def _transaction(self) -> Iterator[Connection]:
        """One BEGIN IMMEDIATE around the read, the check and the
        persist, with every database failure normalized: the library's
        own message carries the statement and its bound parameters, so
        it is never quoted, and the refusal is raised outside the
        handler so that the exception holding them is not attached to
        it either."""
        problem: ConfigError | None = None
        try:
            with self._engine.begin() as connection:
                yield connection
        except ConfigError:
            raise
        except SQLAlchemyError as exc:
            problem = _database_problem(exc)
        if problem is not None:
            raise problem


def stored_secrets(snapshot: Snapshot) -> tuple[StoredSecret, ...]:
    """Every stored secret in one snapshot, each with the key it
    displaces, in the fixed order the store lists its locations in.

    The whole-configuration read's half of what the entity reads return
    one entity at a time, through the same rule, so a slot cannot be
    said to shadow one key in a listing and another in a single read.
    """
    entries: dict[tuple[str, str], object] = {
        ("provider", f"{stage}.{name}"): entry
        for stage in PROVIDER_STAGES
        for name, entry in getattr(snapshot.domain.providers, stage).items()
    }
    entries.update(
        (("mcp_server", name), entry) for name, entry in snapshot.domain.mcp_servers.items()
    )
    return tuple(
        StoredSecret(
            location=location,
            shadows=_shadowed(entries.get((location.kind, location.identity)), location.slot),
        )
        for location in snapshot.secrets.locations()
    )


def _stored_slots(
    entry: object, kind: EntityKind, identity: str, secrets: SecretStore
) -> tuple[StoredSecret, ...]:
    return tuple(
        StoredSecret(
            location=SecretLocation(kind=kind, identity=identity, slot=slot),
            shadows=_shadowed(entry, slot),
        )
        for slot in secrets.slots_for(kind, identity)
    )


def _shadowed(entry: object, slot: str) -> str | None:
    """The entity key a stored secret in this slot displaces, or None
    when the entity writes no reference for it.

    A provider's reference key is `<slot>_env`, an MCP server's is the
    dotted slot itself: both name where the value would have been
    written as an environment reference had it not been stored.
    """
    if isinstance(entry, McpServerConfig):
        group, _, key = slot.partition(".")
        written = getattr(entry, group, None) if group in MCP_SECRET_GROUPS else None
        return slot if isinstance(written, Mapping) and key in written else None
    if isinstance(entry, ProviderConfig):
        key = f"{slot}_env"
        if key == "api_key_env":
            return key if entry.api_key_env is not None else None
        return key if key in entry.options else None
    return None


def _database_problem(exc: SQLAlchemyError) -> ConfigError:
    """The busy lock told from everything else, by type as well as by
    message: one is worth retrying and the other is not, and a caller
    that answers with a status code cannot be made to read the
    sentence. The sentences themselves are unchanged."""
    detail = str(getattr(exc, "orig", "")) or type(exc).__name__
    if isinstance(exc, OperationalError) and ("locked" in detail or "busy" in detail):
        return DatabaseBusyError(
            "the configuration database is busy: another process holds the write "
            "lock. Nothing was changed; run the command again."
        )
    return StorageError(f"the configuration database could not be read or written: {detail}")


# Reading rows


def _read_domain(connection: Connection) -> DomainConfig:
    providers: dict[str, dict[str, ProviderConfig]] = {stage: {} for stage in PROVIDER_STAGES}
    for row in connection.execute(select(schema.providers)):
        if row.stage not in providers:
            # A stored row, not an argument: the same sentence the stage
            # check raises for a caller's typo, but nothing the caller
            # can do about it, so it is a storage failure here.
            raise StorageError(
                f'providers.{row.stage}.{row.name}: "{row.stage}" is not a provider '
                f"stage; expected one of: " + ", ".join(PROVIDER_STAGES)
            )
        providers[row.stage][row.name] = _provider_from_row(row)

    # The rows are read one by one above and assembled here, and the
    # assembly validates too: an entry name, a MAC or a binding that
    # cannot be read is as much a stored-state failure as a column of
    # the wrong shape.
    domain: DomainConfig | None = None
    problem: str | None = None
    try:
        domain = DomainConfig(
            providers=ProvidersConfig(**providers),
            mcp_servers={
                row.name: _mcp_from_row(row)
                for row in connection.execute(select(schema.mcp_servers))
            },
            agents={
                row.name: _agent_from_row(row) for row in connection.execute(select(schema.agents))
            },
            devices={
                row.mac: _list(f"devices.{row.mac}", "agents", row.agents)
                for row in connection.execute(select(schema.devices))
            },
        )
    except ValidationError as exc:
        problem = _validation_problems(_UNREADABLE_ROWS, exc)
    if domain is None:
        raise StorageError(problem)

    defaults = connection.execute(select(schema.agent_defaults)).first()
    if defaults is not None:
        domain.agent_defaults = _stored(
            AgentDefaults, "agent_defaults", _layer_data("agent_defaults", defaults)
        )
    default_agent = connection.execute(
        select(schema.domain_settings.c.value).where(
            schema.domain_settings.c.key == schema.DEFAULT_AGENT_KEY
        )
    ).scalar()
    if default_agent is not None:
        if not isinstance(default_agent, str):
            raise StorageError(
                f"domain_settings.{schema.DEFAULT_AGENT_KEY}: the value column does not "
                f"hold a string; the row cannot be read as configuration"
            )
        domain.default_agent = default_agent
    return domain


def _read_secrets(connection: Connection, keys: MultiFernet | None) -> SecretStore:
    envelopes: dict[SecretLocation, object] = {}
    for row in connection.execute(select(schema.providers)):
        location = f"providers.{row.stage}.{row.name}"
        for slot, envelope in _mapping(location, "secrets", row.secrets).items():
            envelopes[SecretLocation.provider(row.stage, row.name, slot)] = envelope
    for row in connection.execute(select(schema.mcp_servers)):
        for slot, envelope in _mapping(
            f"mcp_servers.{row.name}", "secrets", row.secrets
        ).items():
            envelopes[SecretLocation.mcp_server(row.name, slot)] = envelope
    return SecretStore(envelopes, keys)


def _provider_from_row(row: Row) -> ProviderConfig:
    # api_key_env is a declared model field with its own column, never
    # an options key: options holds exactly the model extras.
    location = f"providers.{row.stage}.{row.name}"
    data: dict[str, object] = {
        "type": row.type,
        "egress": row.egress,
        **_mapping(location, "options", row.options),
    }
    if row.api_key_env is not None:
        data["api_key_env"] = row.api_key_env
    return _stored(ProviderConfig, location, data)


def _mcp_from_row(row: Row) -> McpServerConfig:
    # Fields belonging to the other transport are left unset rather than
    # loaded as None or empty: McpServerConfig reads model_fields_set to
    # tell "my headers are ignored" from "my headers are wrong", so
    # naming them would make every stdio row fail its own validator.
    location = f"mcp_servers.{row.name}"
    data: dict[str, object] = {"transport": row.transport, "tool_timeout_s": row.tool_timeout_s}
    if row.egress is not None:
        data["egress"] = row.egress
    for key, value in (("command", row.command), ("url", row.url)):
        if value is not None:
            data[key] = value
    args = _list(location, "args", row.args)
    if args:
        data["args"] = args
    for key, value in (("env", row.env), ("headers", row.headers)):
        mapping = _mapping(location, key, value)
        if mapping:
            data[key] = mapping
    return _stored(McpServerConfig, location, data)


def _agent_from_row(row: Row) -> AgentConfig:
    location = f"agents.{row.name}"
    data = {"prompt": row.prompt or "", **_layer_data(location, row)}
    return _stored(AgentConfig, location, data)


def _layer_data(location: str, row: Row) -> dict[str, object]:
    """The override columns agent_defaults and agents share."""
    data: dict[str, object] = {
        stage: getattr(row, stage) for stage in PROVIDER_STAGES if getattr(row, stage) is not None
    }
    # None means inherit and an empty list opts out, so neither column
    # can be normalized through the missing-value default: only their
    # container shape is checked.
    if row.mcp is not None:
        data["mcp"] = _list(location, "mcp", row.mcp)
    if row.filler is not None:
        data["filler"] = _mapping(location, "filler", row.filler)
    return data


def _mapping(location: str, column: str, value: object) -> dict[str, object]:
    """A JSON column that has to hold an object.

    SQLite enforces no shape on a JSON column, so a hand-edited or
    half-restored row can hold a string or a list where a mapping
    belongs. Every reader below would then raise a TypeError or an
    AttributeError, which is not a database error and not a validation
    error, so it would travel straight through the sanitized boundary
    and reach the operator as a traceback."""
    if value is None:
        return {}
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise StorageError(_shape_problem(location, column, "an object with string keys"))
    return dict(value)


def _list(location: str, column: str, value: object) -> list[object]:
    """A JSON column that has to hold an array. A string here is the
    dangerous one: iterating it succeeds and yields its characters."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise StorageError(_shape_problem(location, column, "an array"))
    return list(value)


def _shape_problem(location: str, column: str, expected: str) -> str:
    # The column and the row, never the value: a column that holds the
    # wrong shape may hold anything, including something secret that
    # was pasted into it.
    return (
        f"{location}: the {column} column does not hold {expected}; the row cannot be "
        f"read as configuration"
    )


# Writing rows


def _provider_values(entry: ProviderConfig) -> dict[str, object]:
    return {
        "type": entry.type,
        "api_key_env": entry.api_key_env,
        "egress": entry.egress,
        "options": dict(entry.options),
    }


def _mcp_values(entry: McpServerConfig) -> dict[str, object]:
    return {
        "transport": entry.transport,
        "command": entry.command,
        "args": list(entry.args),
        "env": dict(entry.env),
        "url": entry.url,
        "headers": dict(entry.headers),
        "egress": entry.egress,
        "tool_timeout_s": entry.tool_timeout_s,
    }


def _layer_values(entry: AgentDefaults) -> dict[str, object]:
    values: dict[str, object] = {stage: getattr(entry, stage) for stage in PROVIDER_STAGES}
    # None means inherit and a list replaces rather than extends, so an
    # empty list and a null are different configurations and the column
    # has to keep them apart.
    values["mcp"] = list(entry.mcp) if entry.mcp is not None else None
    values["filler"] = entry.filler.model_dump() if entry.filler is not None else None
    return values


def _delete_row(
    connection: Connection,
    table: Table,
    where: Sequence[ColumnElement[bool]],
    missing: str,
) -> None:
    """Delete one entity, then check what is left.

    The order is the point, and it is not an optimization. Reading the
    whole domain first meant validating every row before deleting any,
    so a row that cannot be loaded (a hand-edited JSON column, a value
    its model refuses) could not be deleted at all: the load failed on
    the way to removing the very thing that was failing. That turns the
    break-glass path into one that cannot open the glass, in exactly the
    situation it exists for.

    So the row goes first, by identity, and what is validated afterwards
    is the configuration that remains. Both happen inside the one BEGIN
    IMMEDIATE this runs in, so a deletion the remaining references refuse
    is rolled back with the row still there: the check has the same force
    it had, and the difference is only which state it is asked about.

    Deleting by identity is also what keeps a name no new write could
    create deletable, since nothing about the row has to be understood to
    remove it.

    When the remaining configuration cannot be read at all, the check is
    skipped rather than turned into a refusal, and the reason it is safe
    is an ordering one: a delete removes a row and can never make a
    readable domain unreadable, so an unreadable remainder was already
    unreadable before this delete. The invariant the check protects (a
    server can always load what is stored) is broken by that other row,
    not by this deletion, and refusing here would only mean that one
    unreadable row makes every other entity undeletable, which is the
    deadlock the break-glass path exists to avoid.
    """
    deleted = connection.execute(delete(table).where(*where))
    if deleted.rowcount == 0:
        raise UnknownEntityError(missing)
    remaining = _readable_domain(connection)
    if remaining is not None:
        _refuse_unresolved(remaining)


def _readable_domain(connection: Connection) -> DomainConfig | None:
    """The remaining configuration, or None when it cannot be read as
    configuration at all. Every such failure is a StorageError by
    construction, which is what makes "cannot be read" a condition this
    can ask about rather than a guess."""
    try:
        return _read_domain(connection)
    except StorageError:
        return None


def _upsert(
    connection: Connection,
    table: Table,
    identity: Mapping[str, object],
    values: Mapping[str, object],
) -> None:
    """Create or replace an entity's model-shaped columns, leaving every
    column the caller did not name (the `secrets` column, above all) as
    it was."""
    where = [table.c[column] == value for column, value in identity.items()]
    keys = [table.c[column] for column in identity]
    if connection.execute(select(*keys).where(*where)).first() is None:
        connection.execute(insert(table).values(**identity, **values))
    else:
        connection.execute(update(table).where(*where).values(**values))


# Stored secrets


def _stored_secrets(connection: Connection, location: SecretLocation) -> Mapping[str, object]:
    table, where = _secret_row(location)
    stored = connection.execute(select(table.c.secrets).where(*where)).scalar()
    return _mapping(f"{location.kind} {location.identity}", "secrets", stored)


def _write_secrets(
    connection: Connection, location: SecretLocation, stored: Mapping[str, object]
) -> None:
    table, where = _secret_row(location)
    result = connection.execute(update(table).where(*where).values(secrets=dict(stored)))
    if result.rowcount == 0:
        raise UnknownEntityError(
            f"{location.describe()}: no such entity; create it first with "
            f"samtal-server config set"
        )


def _secret_row(location: SecretLocation) -> tuple[Table, list[ColumnElement[bool]]]:
    if location.kind == "provider":
        stage, _, name = location.identity.partition(".")
        return schema.providers, [
            schema.providers.c.stage == stage,
            schema.providers.c.name == name,
        ]
    return schema.mcp_servers, [schema.mcp_servers.c.name == location.identity]


def _check_slot(domain: DomainConfig, location: SecretLocation) -> None:
    """The entity exists and the slot is one it can have. Slots are
    defined, not arbitrary: a provider's is a secret-shaped option name,
    an MCP server's is a dotted env or headers path, which is where the
    value would otherwise have been written as a $VAR reference."""
    if location.kind == "provider":
        stage, _, name = location.identity.partition(".")
        if name not in getattr(domain.providers, _stage(stage)):
            raise UnknownEntityError(f"providers.{stage}.{name}: no such provider")
        if location.slot.lower().endswith("_env") or not is_secret_option(location.slot):
            raise ConfigError(
                f'"{location.slot}" is not a credential slot on a provider; a slot is '
                f"the option name the credential fills, such as api_key"
            )
        # A slot is addressed in a path of its own, so it obeys the same
        # rule a name does.
        _check_addressable(f"providers.{stage}.{name}", "slot", location.slot)
        return

    if location.identity not in domain.mcp_servers:
        raise UnknownEntityError(f"mcp_servers.{location.identity}: no such MCP server")
    group, _, key = location.slot.partition(".")
    if group not in MCP_SECRET_GROUPS or not key:
        raise ConfigError(
            f'"{location.slot}" is not a credential slot on an MCP server; a slot is '
            f"env.<KEY> or headers.<KEY>, for example headers.Authorization"
        )
    # The key half names where the value would have been written as a
    # reference: a variable for env, a header for headers. Neither can
    # be spelled with a slash, so this is also what makes the dotted
    # slot addressable.
    if group == "env" and not is_env_name(key):
        raise ConfigError(
            f"mcp_servers.{location.identity}: the key after env. has to be the name "
            f"of an environment variable, since that is what the value would "
            f"otherwise have referenced, for example env.API_ACCESS_TOKEN"
        )
    if group == "headers" and not _HEADER_TOKEN_RE.match(key):
        raise ConfigError(
            f"mcp_servers.{location.identity}: the key after headers. has to be an "
            f"HTTP header name, since that is the header the request would carry, "
            f"for example headers.Authorization"
        )


# Arguments and fragments


def _secret_value(location: SecretLocation, secret: object) -> None:
    """The one thing the repository has to know about a secret itself:
    that it is a non-empty string.

    Checked here rather than trusted from the annotation, because what
    an annotation does not stop is a caller handing this something else:
    a null, a number, a JSON object out of a request body. Any of them
    would be encrypted into an envelope whose payload fails verification
    at the next boot, which is a refusal to start earned by a write that
    answered "wrote". The CLI keeps its friendlier wording in front of
    this for the stdin case; this is the floor under every caller.

    The value is never quoted back, here least of all: what fails this
    check is by definition something that arrived where a credential
    goes.
    """
    if not isinstance(secret, str) or not secret:
        raise ConfigError(
            f"{location.describe()}: a secret has to be a non-empty string; nothing "
            f"was stored. The value is not quoted back."
        )


def _stage(stage: str) -> str:
    if stage not in PROVIDER_STAGES:
        raise ConfigError(
            f'"{stage}" is not a provider stage; expected one of: ' + ", ".join(PROVIDER_STAGES)
        )
    return stage


def _identifier(location: str, name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise ConfigError(f"{location}: the name is empty")
    _check_addressable(location, "name", cleaned)
    return cleaned


def _check_addressable(location: str, what: str, value: str) -> None:
    """A name or a slot has to survive a URL path.

    An entity is addressed by putting its identity in a path segment,
    so a name holding a slash cannot be fetched, replaced or deleted
    over the API at all: routing would read it as two segments. Spaces,
    percent signs and characters outside ASCII stay legal, because they
    percent-encode and decode losslessly; a control character does not
    survive a header or a log line intact and has no business in a name
    either.

    Write time only. The load path does not run this, so a row written
    before the rule still boots, still appears in a whole-configuration
    read, and is still deletable, which goes by membership rather than
    by this check. The refusal names the rule and the kind of character,
    never the value: what lands in a slot argument by mistake is a
    credential.
    """
    if "/" in value:
        raise ConfigError(
            f"{location}: the {what} contains a slash, and it has to be one URL path "
            f"segment, which is how it is addressed over the configuration API. "
            f"Spaces, percent signs and characters outside ASCII are fine"
        )
    if _CONTROL_RE.search(value):
        raise ConfigError(
            f"{location}: the {what} contains a control character, and it has to be "
            f"one URL path segment, which is how it is addressed over the "
            f"configuration API. Spaces, percent signs and characters outside ASCII "
            f"are fine"
        )


def _mac(mac: str) -> str:
    # Recorded here and raised outside the handler, the rule this
    # codebase settled on: `from None` clears the cause and leaves the
    # context, so the rejected value would still be reachable on the
    # exception that travels out.
    problem: str | None = None
    try:
        return normalize_mac(mac)
    except ValueError as exc:
        problem = str(exc)
    raise ConfigError(problem)


def _binding(mac: str, agents: Sequence[str]) -> dict[str, list[str]]:
    binding: dict[str, list[str]] | None = None
    problem: str | None = None
    try:
        binding = normalize_device_bindings({mac: list(agents)})
    except ValueError as exc:
        problem = str(exc)
    if binding is None:
        raise ConfigError(problem)
    return {key: [str(agent).strip() for agent in bound] for key, bound in binding.items()}


def _parse[Model: BaseModel](model: type[Model], location: str, fragment: object) -> Model:
    """A fragment through the model that owns its shape. The same
    validators guard it as guard the YAML file, so a plaintext secret
    never enters through a file here either."""
    if fragment is None:
        fragment = {}
    if not isinstance(fragment, Mapping):
        raise ConfigError(
            f"invalid {location}: expected a mapping of keys, got {type(fragment).__name__}"
        )
    check_transportable(location, fragment)
    return _load(model, location, dict(fragment))


def check_transportable(location: str, fragment: object) -> None:
    """A fragment refused if JSON cannot carry it as it is.

    The repository applies this to every fragment it parses; the CLI
    runs the same check before a fragment travels as a request body.
    One rule, one wording, met by whichever caller sees the value first,
    because the alternative is what the encoder does on its own: a
    TypeError, a ValueError or a RecursionError with a traceback, in
    place of the sentence this file exists to produce.

    YAML is the wider language, which is the whole reason this is
    needed. `!!timestamp` produces a date, `!!binary` bytes and `!!set`
    a set, none of which JSON has; an anchor can make a structure that
    contains itself; and a non-string mapping key is the quiet one,
    because JSON would not refuse it at all, it would stringify it and
    hand a reader a key nobody wrote.
    """
    problem = _untransportable(fragment)
    if problem is not None:
        raise ConfigError(f"invalid {location}: {problem}")


def _load[Model: BaseModel](
    model: type[Model], location: str, data: Mapping[str, object]
) -> Model:
    problem: str | None = None
    try:
        return model.model_validate(dict(data))
    except ValidationError as exc:
        # Rendered from the error locations and messages only, never
        # from str(exc), which quotes the rejected input back; and
        # recorded rather than raised here, because an exception raised
        # inside a handler carries the one being handled as its
        # __context__, and a ValidationError's errors() hold the whole
        # rejected fragment, inline secret and all. Raising after the
        # handler leaves neither a cause nor a context.
        problem = _validation_problems(f"invalid {location}:", exc)
    raise ConfigError(problem)


def _stored[Model: BaseModel](
    model: type[Model], location: str, data: Mapping[str, object]
) -> Model:
    """One stored row through the model that owns its shape.

    The same validation a fragment gets, and a different refusal. A
    caller reading a row did nothing wrong, and there is nothing it can
    do about what is stored, so a row that will not validate is a
    storage failure (the 500 the API answers) rather than a rejection of
    the request (422). The message names the row and the fields that
    failed and never their values, and is built inside the handler and
    raised outside it, since a ValidationError holds the whole row.
    """
    where = _nonfinite(data)
    if where is not None:
        raise StorageError(
            f"{location}: {_NOT_FINITE.format(where=where)}; the row cannot be read "
            f"as configuration"
        )
    problem: str | None = None
    try:
        return model.model_validate(dict(data))
    except ValidationError as exc:
        problem = _validation_problems(f"{location}: {_UNREADABLE_ROW}", exc)
    raise StorageError(problem)


def _untransportable(
    value: object, path: str = "", ancestors: frozenset[int] = frozenset()
) -> str | None:
    """What in `value` JSON cannot carry, said without quoting any of it,
    or None.

    Cycle-safe by carrying the containers currently above this one
    rather than every container already seen: two keys pointing at the
    same anchored mapping is a shape JSON writes out twice and reads
    back correctly, so refusing it would refuse a legitimate YAML file.
    A container that is its own ancestor is the one that cannot be
    written at all.
    """
    if id(value) in ancestors:
        return _RECURSIVE.format(where=path or "the fragment")
    if isinstance(value, Mapping):
        below = ancestors | {id(value)}
        for key, nested in value.items():
            if not isinstance(key, str):
                return _NON_STRING_KEY.format(
                    where=path or "the fragment", kind=type(key).__name__
                )
            found = _untransportable(nested, f"{path}.{key}" if path else key, below)
            if found is not None:
                return found
        return None
    if isinstance(value, (list, tuple)):
        below = ancestors | {id(value)}
        for position, item in enumerate(value):
            found = _untransportable(
                item, f"{path}.{position}" if path else str(position), below
            )
            if found is not None:
                return found
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return _NOT_FINITE.format(where=path or "the value")
    # bool before int, and both before the refusal, because bool is a
    # subclass of int and neither needs naming twice.
    if value is None or isinstance(value, (str, bool, int, float)):
        return None
    return _NOT_TRANSPORTABLE.format(
        where=path or "the fragment", kind=type(value).__name__
    )


def _nonfinite(value: object, path: str = "") -> str | None:
    """Where the first value that is a number but not a finite one sits,
    or None.

    NaN and the infinities have no JSON spelling. A stored one is
    serialized as null on the way out, which quietly turns a
    configuration into a different one: the option disappears and the
    provider falls back to its own default. So a fragment carrying one
    is refused where every other fragment rule is applied, and a row
    holding one reports that it cannot be read rather than answering
    with a value nobody wrote.
    """
    if isinstance(value, float) and not math.isfinite(value):
        return path or "the value"
    if isinstance(value, Mapping):
        for key, nested in value.items():
            found = _nonfinite(nested, f"{path}.{key}" if path else str(key))
            if found is not None:
                return found
    elif isinstance(value, (list, tuple)):
        for position, item in enumerate(value):
            found = _nonfinite(item, f"{path}.{position}" if path else str(position))
            if found is not None:
                return found
    return None


def _validation_problems(headline: str, exc: ValidationError) -> str:
    lines = [headline]
    for error in exc.errors():
        where = ".".join(str(part) for part in error["loc"])
        message = error["msg"].removeprefix("Value error, ")
        for line in message.splitlines():
            lines.append(f"  - {where}: {line}" if where else f"  - {line}")
    return "\n".join(lines)


def _refuse_unresolved(domain: DomainConfig) -> None:
    problems = check_references(domain)
    if problems:
        raise ConfigError(
            "the change was refused; it would leave these references unresolved:\n"
            + "\n".join(f"  - {problem}" for problem in problems)
        )


__all__ = [
    "ConfigStore",
    "check_transportable",
    "DomainConfig",
    "Entity",
    "Snapshot",
    "StoredSecret",
    "stored_secrets",
    "verify_secrets",
]
