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
    is_secret_option,
    normalize_device_bindings,
    normalize_mac,
)
from samtal_server.config.secrets import SecretLocation, SecretStore, encrypt
from samtal_server.db import schema

# The two groups of an MCP server's dotted secret slots. A slot is
# `env.<KEY>` or `headers.<KEY>`, which is where the value would have
# been written as a $VAR reference.
MCP_SECRET_GROUPS = ("env", "headers")


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
            domain = _read_domain(connection)
            entries = getattr(domain.providers, stage)
            if name not in entries:
                raise UnknownEntityError(f"providers.{stage}.{name}: no such provider")
            del entries[name]
            _refuse_unresolved(domain)
            # The row carries its own secrets column, so deleting the
            # entity deletes its stored secrets with it.
            connection.execute(
                delete(schema.providers).where(
                    schema.providers.c.stage == stage, schema.providers.c.name == name
                )
            )

    def set_mcp_server(self, name: str, fragment: object) -> None:
        name = _identifier("mcp_servers", name)
        entry = _parse(McpServerConfig, f"mcp_servers.{name}", fragment)
        try:
            check_mcp_entry_names({name: entry})
        except ValueError as exc:
            raise ConfigError(str(exc)) from None
        with self._transaction() as connection:
            domain = _read_domain(connection)
            domain.mcp_servers[name] = entry
            _refuse_unresolved(domain)
            _upsert(connection, schema.mcp_servers, {"name": name}, _mcp_values(entry))

    def delete_mcp_server(self, name: str) -> None:
        with self._transaction() as connection:
            domain = _read_domain(connection)
            if name not in domain.mcp_servers:
                raise UnknownEntityError(f"mcp_servers.{name}: no such MCP server")
            del domain.mcp_servers[name]
            _refuse_unresolved(domain)
            connection.execute(
                delete(schema.mcp_servers).where(schema.mcp_servers.c.name == name)
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
            domain = _read_domain(connection)
            if name not in domain.agents:
                raise UnknownEntityError(f"agents.{name}: no such agent")
            del domain.agents[name]
            _refuse_unresolved(domain)
            connection.execute(delete(schema.agents).where(schema.agents.c.name == name))

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
            domain = _read_domain(connection)
            if normalized not in domain.devices:
                raise UnknownEntityError(f"devices.{normalized}: no such device")
            del domain.devices[normalized]
            _refuse_unresolved(domain)
            connection.execute(delete(schema.devices).where(schema.devices.c.mac == normalized))

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
        it is never quoted and the exception chain is cut."""
        try:
            with self._engine.begin() as connection:
                yield connection
        except ConfigError:
            raise
        except SQLAlchemyError as exc:
            raise _database_problem(exc) from None


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

    domain = DomainConfig(
        providers=ProvidersConfig(**providers),
        mcp_servers={
            row.name: _mcp_from_row(row) for row in connection.execute(select(schema.mcp_servers))
        },
        agents={
            row.name: _agent_from_row(row) for row in connection.execute(select(schema.agents))
        },
        devices={
            row.mac: _list(f"devices.{row.mac}", "agents", row.agents)
            for row in connection.execute(select(schema.devices))
        },
    )
    defaults = connection.execute(select(schema.agent_defaults)).first()
    if defaults is not None:
        domain.agent_defaults = _load(
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
    return _load(ProviderConfig, location, data)


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
    return _load(McpServerConfig, location, data)


def _agent_from_row(row: Row) -> AgentConfig:
    location = f"agents.{row.name}"
    data = {"prompt": row.prompt or "", **_layer_data(location, row)}
    return _load(AgentConfig, location, data)


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
        return

    if location.identity not in domain.mcp_servers:
        raise UnknownEntityError(f"mcp_servers.{location.identity}: no such MCP server")
    group, _, key = location.slot.partition(".")
    if group not in MCP_SECRET_GROUPS or not key:
        raise ConfigError(
            f'"{location.slot}" is not a credential slot on an MCP server; a slot is '
            f"env.<KEY> or headers.<KEY>, for example headers.Authorization"
        )


# Arguments and fragments


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
    return cleaned


def _mac(mac: str) -> str:
    try:
        return normalize_mac(mac)
    except ValueError as exc:
        raise ConfigError(str(exc)) from None


def _binding(mac: str, agents: Sequence[str]) -> dict[str, list[str]]:
    try:
        binding = normalize_device_bindings({mac: list(agents)})
    except ValueError as exc:
        raise ConfigError(str(exc)) from None
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
    return _load(model, location, dict(fragment))


def _load[Model: BaseModel](
    model: type[Model], location: str, data: Mapping[str, object]
) -> Model:
    try:
        return model.model_validate(dict(data))
    except ValidationError as exc:
        # Rendered from the error locations and messages only, never
        # from str(exc), which quotes the rejected input back; and
        # raised without a cause, so the chain does not carry it either.
        raise ConfigError(_validation_problems(location, exc)) from None


def _validation_problems(location: str, exc: ValidationError) -> str:
    lines = [f"invalid {location}:"]
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
    "DomainConfig",
    "Snapshot",
    "verify_secrets",
]
