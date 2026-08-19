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
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

from cryptography.fernet import MultiFernet
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import Connection, Engine, Row, Table, delete, insert, select, update
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.sql.elements import ColumnElement

from samtal_server.config import entities
from samtal_server.config.entities import EntityDescriptor
from samtal_server.config.loader import (
    ConfigError,
    DatabaseBusyError,
    DeviceAlreadyBoundError,
    StorageError,
    UnknownEntityError,
)
from samtal_server.config.models import (
    DOMAIN_DESCRIPTIONS,
    PROMPT_FRAGMENT_NAME_RULE,
    PROVIDER_STAGES,
    UNRECOGNIZED_KEY_REFUSED,
    AgentConfig,
    AgentDefaults,
    FieldProblem,
    FieldProblemsError,
    McpServerConfig,
    NonBlankStr,
    PromptFragmentConfig,
    ProviderConfig,
    ProvidersConfig,
    check_mcp_entry_names,
    check_prompt_fragment_names,
    check_references,
    is_env_name,
    is_secret_option,
    is_valid_fragment_name,
    json_pointer,
    mcp_entry_fragment,
    normalize_device_bindings,
    normalize_mac,
    safe_location,
    url_credential,
)
from samtal_server.config.secrets import (
    MASK,
    EntityKind,
    SecretLocation,
    SecretStore,
    encrypt,
)
from samtal_server.db import schema

# The two groups of an MCP server's dotted secret slots. A slot is
# `env.<KEY>` or `headers.<KEY>`, which is where the value would have
# been written as a $VAR reference.
MCP_SECRET_GROUPS = ("env", "headers")

# What a credential offered to something that is not a credential slot
# is told, one fixed sentence per kind (#132). A slot is the second half
# of a secret's address and arrives the same way the first half does, in
# a URL path or on a command line, and the command that carries it is
# the one an operator pastes a credential into: a slot that failed this
# check is a value nothing here has validated, and it may be the
# credential itself, typed one argument early.
#
# The rules can be stated without it. The groups are declared above, so
# the MCP sentence is built from them and cannot come to disagree; a
# provider's slot is any secret-shaped option name, which is a rule
# rather than a list, so that sentence gives the rule and the usual
# name.
NOT_A_PROVIDER_SLOT = (
    "providers: a credential slot is the option name the credential fills, such as "
    "api_key. A name that is not secret-shaped is not one, and neither is a name "
    "ending in _env, which is where an environment variable is named rather than a "
    "credential stored"
)
NOT_AN_MCP_SLOT = (
    "mcp_servers: a credential slot is "
    + " or ".join(f"{group}.<KEY>" for group in MCP_SECRET_GROUPS)
    + ", for example headers.Authorization, which is where the value would "
    "otherwise have been written as a $VAR reference"
)

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
    prompt_fragments: dict[NonBlankStr, PromptFragmentConfig] = Field(
        default_factory=dict, description=DOMAIN_DESCRIPTIONS["prompt_fragments"]
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
class BoundDevice:
    """What a device write wrote: the canonical MAC of the row, and the
    agent names as they were stored.

    A write normalizes both, so the request's spelling and the row's are
    different strings, and everything said about the write afterwards
    (the line a caller prints, and whether the change needs a restart to
    reach the device) has to be about the row. Answering with it is what
    keeps a caller from normalizing a second time, differently.
    """

    mac: str
    agents: tuple[str, ...]


@dataclass(frozen=True)
class LiveBinding:
    """What a running server re-reads about one device: its binding, and
    the default agent standing behind it.

    Both together because they are one question (which agents may this
    device talk to) answered by two rows, and reading them apart would
    let a write between them produce an answer neither state ever had.
    An empty `agents` means the device has no row, which is different
    from a row that could not be read: that one never becomes a
    `LiveBinding` at all.
    """

    agents: tuple[str, ...]
    default_agent: str | None


# What a refusal about these two rows names. Not a single row's
# location, because the two are validated together, and the model that
# validates them names the field that failed inside this.
_LIVE_BINDING_LOCATION = "the stored device bindings"


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
        return self._read(_PROVIDER, _stage(stage), name)

    def read_mcp_server(self, name: str) -> Entity[McpServerConfig]:
        return self._read(_MCP_SERVER, name)

    def read_prompt_fragment(self, name: str) -> Entity[PromptFragmentConfig]:
        return self._read(_PROMPT_FRAGMENT, name)

    def read_agent(self, name: str) -> Entity[AgentConfig]:
        return self._read(_AGENT, name)

    def read_agent_defaults(self) -> Entity[AgentDefaults]:
        """The singleton, which always exists: an unwritten one is the
        empty entry rather than a missing entity."""
        return self._read(_AGENT_DEFAULTS)

    def _read(self, descriptor: EntityDescriptor, *identity: str) -> Entity:
        """One entity of one kind, or the refusal its kind answers a
        missing entry with.

        What comes back beside it is its stored-secret slots, and only
        the two kinds that can hold one have any: a fragment is prompt
        text, and an agent references providers and MCP servers whose
        credentials are stored on them.
        """
        with self._transaction() as connection:
            entry = _entry(_read_domain(connection), descriptor, identity)
            if entry is None:
                raise UnknownEntityError(_missing(descriptor))
            if descriptor.secret_slots is None:
                return Entity(entry=entry, secrets=())
            return self._with_secrets(
                connection, entry, descriptor.secret_slots, ".".join(identity)
            )

    def read_device(self, mac: str) -> Entity[list[str]]:
        """One device's binding, keyed by the canonical form of its MAC,
        so `AA-BB-...` and `aa:bb:...` read the same row."""
        normalized = _mac(mac)
        with self._transaction() as connection:
            bound = _read_domain(connection).devices.get(normalized)
            if bound is None:
                raise UnknownEntityError(_NO_SUCH_DEVICE)
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
        self._write(_PROVIDER, (_stage(stage), name), fragment)

    def delete_provider(self, stage: str, name: str) -> None:
        self._delete(_PROVIDER, _stage(stage), name)

    def set_mcp_server(self, name: str, fragment: object) -> None:
        self._write(_MCP_SERVER, (name,), fragment)

    def delete_mcp_server(self, name: str) -> None:
        self._delete(_MCP_SERVER, name)

    def set_prompt_fragment(self, name: str, fragment: object) -> None:
        """Create or replace `prompt_fragments.<name>` from a fragment in
        the same shape the section has: `{text: ...}`.

        The name is checked before the body is parsed, which is the one
        thing about this write that is not like its neighbours'
        (`_check_fragment_name` says why).
        """
        self._write(_PROMPT_FRAGMENT, (name,), fragment)

    def delete_prompt_fragment(self, name: str) -> None:
        """Refused while any layer still includes it, by the same
        reference pass every other write runs."""
        self._delete(_PROMPT_FRAGMENT, name)

    def set_agent(self, name: str, fragment: object) -> None:
        self._write(_AGENT, (name,), fragment)

    def delete_agent(self, name: str) -> None:
        """Refused while a device binding or default_agent still names
        it, by the same reference pass every other write runs."""
        self._delete(_AGENT, name)

    def set_agent_defaults(self, fragment: object) -> None:
        self._write(_AGENT_DEFAULTS, (), fragment)

    def _write(
        self, descriptor: EntityDescriptor, identity: tuple[str, ...], fragment: object
    ) -> None:
        """Create or replace one entity from a fragment in the same shape
        its section of the YAML file has.

        The order is every kind's: the name is made usable, then
        whatever the kind checks before a body is looked at, then the
        body read as a fragment, then the model that owns the shape,
        then whatever the kind checks about the parsed entry. Inside the
        transaction, the entry is put where the configuration would hold
        it and the reference pass runs against the state the write would
        leave, which is what makes an unresolvable write refusable
        before it lands. The columns the kind names are what is written,
        so the `secrets` column nobody named stays as it was.

        Two shapes, and the difference between them is one question: does
        this fragment depend on what is stored? A fragment that does not
        is parsed and checked before the write lock is asked for, so
        nothing a caller got wrong costs a lock. A fragment carrying the
        unchanged-value marker does depend on it, and the whole of that
        write happens under one lock: the row it resolves against is read
        inside the transaction that replaces it, so no other writer can
        change or delete the value between the resolution and the write.
        Resolving under a lock taken later would let a value that was
        gone by the time this write ran come back, which is an outcome no
        serial order of the two writes produces, and this repository's
        write is one transaction (see the module docstring).
        """
        if descriptor.addressing:
            name = _identifier(_location(descriptor, *identity[:-1]), identity[-1])
            identity = (*identity[:-1], name)
        location = _location(descriptor, *identity)
        if descriptor.before_parse is not None:
            descriptor.before_parse(*identity)
        data = _readable(location, fragment)
        marks = tuple(_masked_paths(data, descriptor.secret_key))
        if not marks:
            entry = _parsed(descriptor, identity, location, data)
            with self._transaction() as connection:
                _persist(connection, _read_domain(connection), descriptor, identity, entry)
            return
        with self._transaction() as connection:
            domain = _read_domain(connection)
            kept = _keep(descriptor, location, data, marks, _entry(domain, descriptor, identity))
            entry = _parsed(descriptor, identity, location, kept)
            _persist(connection, domain, descriptor, identity, entry)

    def _delete(self, descriptor: EntityDescriptor, *identity: str) -> None:
        """Remove one entity, by the identity that addresses it and
        nothing else. A row carries its own secrets column, so deleting
        the entity deletes its stored secrets with it."""
        table = _table(descriptor)
        where = [
            table.c[column] == value
            for column, value in _row_identity(descriptor, identity).items()
        ]
        with self._transaction() as connection:
            _delete_row(connection, table, where, _missing(descriptor))

    # Devices and the default agent

    def bind_device(self, mac: str, agents: Sequence[str]) -> BoundDevice:
        """Bind one device, and answer with what was written.

        The MAC and the names are normalized on the way in (canonical
        MAC spelling, surrounding whitespace off each name), so what a
        caller sent and what the row holds are different strings. The
        write is what a caller has to describe afterwards, in the line
        it prints and in whether it says a restart is needed, so the
        canonical form travels back rather than being re-derived by
        every caller from the request.
        """
        binding = _binding(mac, list(agents))
        with self._transaction() as connection:
            domain = _read_domain(connection)
            domain.devices.update(binding)
            _refuse_unresolved(domain)
            for normalized, bound in binding.items():
                _upsert(connection, schema.devices, {"mac": normalized}, {"agents": bound})
        # One binding in, one row out, so there is exactly one to
        # describe.
        written, names = next(iter(binding.items()))
        return BoundDevice(written, tuple(names))

    def claim_device(self, mac: str, agents: Sequence[str]) -> BoundDevice:
        """Bind a device that nothing has configured yet, or refuse.

        `bind_device` with a condition, and the condition is the whole
        of it: the row must not exist, and no default agent may be set,
        both read inside the same transaction as the write. That is what
        an activation code needs and what a MAC does not. A code is
        issued to a device the database had nothing to say about, and it
        then sits on a screen for minutes while anything may happen to
        the configuration underneath it: another operator binding the
        same board by its MAC, or a default agent being set that covers
        every board at once. An upsert would let the older decision
        replace the newer one, silently, and whoever made the newer one
        would have no reason to look.

        Refused rather than merged, because there is no merge to make:
        the two writes say different things about one device and only
        the person holding the board knows which is meant. What the
        refusal costs is one command, and the device is configured
        either way: it reaches its agent at its next check.
        """
        binding = _binding(mac, list(agents))
        written, names = next(iter(binding.items()))
        with self._transaction() as connection:
            domain = _read_domain(connection)
            if written in domain.devices:
                raise DeviceAlreadyBoundError(
                    f"devices.{written}: this device has been bound since it started "
                    f"showing that activation code, so the code binds nothing now. "
                    f"Nothing was changed, and the device reaches its agents at its "
                    f"next check. Run `samtal-server config show device {written}` to "
                    f"see what it is bound to, or bind it again by its MAC"
                )
            if domain.default_agent is not None:
                raise DeviceAlreadyBoundError(
                    f"devices.{written}: a default agent has been set since this device "
                    f"started showing that activation code, and it covers every device "
                    f"that has no binding of its own, so the code binds nothing now. "
                    f"Nothing was changed. To give this device an agent of its own, "
                    f"bind it by its MAC"
                )
            domain.devices.update(binding)
            _refuse_unresolved(domain)
            _upsert(connection, schema.devices, {"mac": written}, {"agents": names})
        return BoundDevice(written, tuple(names))

    def delete_device(self, mac: str) -> str:
        """Remove one device's binding, answering with the canonical MAC
        of the row that went, for the reason `bind_device` answers with
        one."""
        normalized = _mac(mac)
        with self._transaction() as connection:
            _delete_row(
                connection,
                schema.devices,
                (schema.devices.c.mac == normalized,),
                _NO_SUCH_DEVICE,
            )
        return normalized

    def set_default_agent(self, name: str) -> str:
        """Set the agent an unbound device reaches, answering with the
        name as it was stored."""
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
        return name

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
        """Remove one stored credential.

        A slot holding none is refused by the section, not by the
        location: an entity name and a slot name both arrive from a URL
        path or a command line, and this refusal travels out as a 404
        body and a printed line (#132).
        """
        with self._transaction() as connection:
            stored = dict(_stored_secrets(connection, location))
            if location.slot not in stored:
                raise UnknownEntityError(
                    f"{_secret_section(location)}: no secret is stored for that slot"
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


# Which of the methods above a caller with a descriptor and no idea
# which kind it holds should call. Unbound, so the caller passes the
# store it has; the identity is the kind's `addressing`, in order, and a
# write takes the fragment after it.
#
# Named rather than assembled out of the kind's name at the call site,
# because a method reached through a built string is a reference nothing
# checks, and because what a kind's own method does before it addresses
# anything belongs to the verb: `read_provider` makes the stage
# canonical first, and a generic read that skipped that would answer a
# different refusal for `PROVIDERS/llm`. The singleton has no delete, so
# it fills none.
entities.fill(
    "provider",
    read=ConfigStore.read_provider,
    write=ConfigStore.set_provider,
    delete=ConfigStore.delete_provider,
)
entities.fill(
    "mcp-server",
    read=ConfigStore.read_mcp_server,
    write=ConfigStore.set_mcp_server,
    delete=ConfigStore.delete_mcp_server,
)
entities.fill(
    "prompt-fragment",
    read=ConfigStore.read_prompt_fragment,
    write=ConfigStore.set_prompt_fragment,
    delete=ConfigStore.delete_prompt_fragment,
)
entities.fill(
    "agent",
    read=ConfigStore.read_agent,
    write=ConfigStore.set_agent,
    delete=ConfigStore.delete_agent,
)
entities.fill(
    "agent-defaults",
    read=ConfigStore.read_agent_defaults,
    write=ConfigStore.set_agent_defaults,
)


def read_live_binding(engine: Engine, mac: str) -> LiveBinding:
    """One device's binding and the default agent, read while the server
    runs, through the rules that govern every other read of them.

    The one read this module serves that is not the CLI's or the API's.
    It exists here rather than beside its caller for the reason the rest
    of the file does: what a stored row means is decided in one place. A
    reader of its own would have had to restate the rules that a binding
    is a non-empty list of non-blank names without duplicates, that the
    MAC key is canonical, and that `default_agent` is a name and not
    whatever JSON the column holds, and a restatement that drifted
    would answer a device differently from the boot that validated the
    same rows.

    Two differences from the reads above, both about where it runs. It
    takes the engine rather than a `ConfigStore`, because a device path
    reads through a deferred connection that never migrates and never
    takes the write lock (`db.read_engine`), and it reads two rows
    rather than the whole configuration, in one transaction, so a write
    landing between them cannot produce a state that never existed.

    Anything unreadable leaves as a `ConfigError`: a `StorageError` for a
    row that does not validate, the usual busy or storage failure for the
    database itself. The caller answers all of them the same way, by
    falling back to the configuration it booted with, which is the only
    safe reading of "this row cannot be understood".
    """
    normalized = _mac(mac)
    problem: ConfigError | None = None
    try:
        with engine.connect() as connection:
            return _live_binding(connection, normalized)
    except ConfigError:
        raise
    except SQLAlchemyError as exc:
        problem = _database_problem(exc)
    raise problem


def _live_binding(connection: Connection, mac: str) -> LiveBinding:
    bound = connection.execute(
        select(schema.devices.c.agents).where(schema.devices.c.mac == mac)
    ).scalar()
    default_agent = connection.execute(
        select(schema.domain_settings.c.value).where(
            schema.domain_settings.c.key == schema.DEFAULT_AGENT_KEY
        )
    ).scalar()
    # Assembled into the same model the whole snapshot is validated
    # through, so these two rows meet exactly the validators they met at
    # boot: the array check first, which is the one a string would slip
    # past (iterating it succeeds and yields its characters), then the
    # model.
    data: dict[str, object] = {}
    if bound is not None:
        data["devices"] = {mac: _list(f"devices.{mac}", "agents", bound)}
    if default_agent is not None:
        data["default_agent"] = default_agent
    live = _stored(DomainConfig, _LIVE_BINDING_LOCATION, data)
    return LiveBinding(tuple(live.devices.get(mac, ())), live.default_agent)


def stored_secrets(snapshot: Snapshot) -> tuple[StoredSecret, ...]:
    """Every stored secret in one snapshot, each with the key it
    displaces, in the fixed order the store lists its locations in.

    The whole-configuration read's half of what the entity reads return
    one entity at a time, through the same rule, so a slot cannot be
    said to shadow one key in a listing and another in a single read.
    """
    entries: dict[tuple[str, str], object] = {
        (descriptor.secret_slots, identity): entry
        for descriptor in _SECRET_HOLDERS
        for identity, entry in _identified(snapshot.domain, descriptor)
    }
    return tuple(
        StoredSecret(
            location=location,
            shadows=_shadowed(entries.get((location.kind, location.identity)), location.slot),
        )
        for location in snapshot.secrets.locations()
    )


def _identified(domain: DomainConfig, descriptor: EntityDescriptor) -> Iterator[tuple[str, object]]:
    """Every entry of one kind, by the identity its stored secrets are
    addressed under: the entry's name, or its group and its name
    together where the kind is addressed by two, which is what makes a
    provider's `llm.claude` the same string here as in a location."""
    section = getattr(domain, descriptor.moved_key)
    if len(descriptor.addressing) == 1:
        yield from section.items()
        return
    for group in type(section).model_fields:
        for name, entry in getattr(section, group).items():
            yield f"{group}.{name}", entry


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


# Rows and the kinds they hold
#
# One kind is one table, one row mapping and one location, and all three
# are the descriptor's. The default mapping is the model itself: every
# declared field is a column of the same name, so validating the row
# against the model is the whole of it. A hook is what a kind pays where
# its model asks for something a dump cannot say, which the inventory
# found for three of the five, and each of those is filled below beside
# the code it is.

_PROVIDER = entities.descriptor("provider")
_MCP_SERVER = entities.descriptor("mcp-server")
_PROMPT_FRAGMENT = entities.descriptor("prompt-fragment")
_AGENT = entities.descriptor("agent")
_AGENT_DEFAULTS = entities.descriptor("agent-defaults")

# The devices map is a setting rather than an entity, and its two
# refusals read their sentence off its descriptor for the reason the
# five kinds read theirs off theirs: one home per sentence.
_NO_SUCH_DEVICE = entities.setting("devices").missing

# The kinds a whole read walks one row per name. The provider is not one
# of them, because its rows are grouped by stage and the group is
# checked with a sentence of its own; neither is the singleton, which is
# one row and is read after the rest.
_KEYED_BY_NAME = tuple(
    descriptor for descriptor in entities.ENTITIES if descriptor.addressing == ("name",)
)

# The kinds a stored secret can hang on, which is the registry's own
# statement of the two members `secrets.EntityKind` admits: a kind that
# names no slot has no secrets column to read, no row for one to be
# written to, and no way for a location to address it.
_SECRET_HOLDERS = tuple(
    descriptor for descriptor in entities.ENTITIES if descriptor.secret_slots is not None
)
_HOLDER_OF = {descriptor.secret_slots: descriptor for descriptor in _SECRET_HOLDERS}


def _table(descriptor: EntityDescriptor) -> Table:
    """The table one kind is rowed in. The descriptor names it rather
    than holding it, since the registry is read by a command that has no
    database to open."""
    return getattr(schema, descriptor.table)


def _missing(descriptor: EntityDescriptor) -> str:
    """How one kind refuses an entry that is not there.

    Takes the descriptor and not the identity, which is the whole point
    (#132): the sentence names the section and the fact, and never what
    was addressed, because an identity that addresses nothing is a value
    nothing in this deployment has validated. The one kind carrying no
    sentence is the singleton, which has no missing case and never
    reaches here.
    """
    assert descriptor.missing is not None, f"{descriptor.name} has no missing entry"
    return descriptor.missing


def _location(descriptor: EntityDescriptor, *identity: str) -> str:
    """Where an entry is written in the configuration document, which is
    what every refusal about it names: the section it lives in, and the
    parameters that address one entry under it."""
    return ".".join((descriptor.moved_key, *identity))


def _from_row(descriptor: EntityDescriptor, row: Row) -> BaseModel:
    """One stored row as its model, through the kind's own mapping where
    it has one and through the model itself where it does not."""
    if descriptor.from_row is not None:
        return descriptor.from_row(row)
    identity = tuple(getattr(row, part) for part in descriptor.addressing)
    return _stored(
        descriptor.model,
        _location(descriptor, *identity),
        {name: getattr(row, name) for name in descriptor.model.model_fields},
    )


def _to_row(descriptor: EntityDescriptor, entry: BaseModel) -> dict[str, object]:
    """One entry as the columns that hold it, the same way round: the
    kind's own mapping where it has one, and the model's own dump where
    it does not."""
    if descriptor.to_row is not None:
        return descriptor.to_row(entry)
    return entry.model_dump()


def _row_identity(descriptor: EntityDescriptor, identity: Sequence[str]) -> dict[str, object]:
    """The columns that address one row: the parameters the kind is
    addressed by, under their own names, since a path parameter and the
    column it selects on are the same fact. A kind addressed by nothing
    is the singleton, whose one row is written under a fixed key."""
    if not descriptor.addressing:
        return {"id": schema.AGENT_DEFAULTS_ID}
    return dict(zip(descriptor.addressing, identity, strict=True))


def _section(
    domain: DomainConfig, descriptor: EntityDescriptor, identity: Sequence[str]
) -> object:
    """The mapping one entry of a kind is keyed in. Every addressing
    parameter but the last names a group inside the section: a provider
    is addressed by its stage and its name together, and the stage is
    the group."""
    section = getattr(domain, descriptor.moved_key)
    for group in identity[:-1]:
        section = getattr(section, group)
    return section


def _entry(
    domain: DomainConfig, descriptor: EntityDescriptor, identity: Sequence[str]
) -> object:
    """One entry out of a whole configuration, or None when it holds
    none of that identity. The singleton is never None: an unwritten one
    is the empty entry rather than a missing entity."""
    if not descriptor.addressing:
        return getattr(domain, descriptor.moved_key)
    return _section(domain, descriptor, identity).get(identity[-1])


def _place(
    domain: DomainConfig,
    descriptor: EntityDescriptor,
    identity: Sequence[str],
    entry: BaseModel,
) -> None:
    """The entry where the configuration would hold it, so that the
    reference pass runs against the state the write would leave."""
    if not descriptor.addressing:
        setattr(domain, descriptor.moved_key, entry)
        return
    _section(domain, descriptor, identity)[identity[-1]] = entry


def _parsed(
    descriptor: EntityDescriptor,
    identity: Sequence[str],
    location: str,
    data: Mapping[str, object],
) -> BaseModel:
    """One fragment through the model that owns its shape, and then
    through whatever its kind checks about the parsed entry.

    The same validators guard it as guard the YAML file, so a plaintext
    secret never enters through a file here either. Named rather than
    inlined because a write reaches it from two places now, and the
    checks a kind runs are part of parsing rather than of persisting.
    """
    entry = _load(descriptor.model, location, data)
    if descriptor.inside_write is not None:
        descriptor.inside_write(location, identity, entry)
    return entry


def _persist(
    connection: Connection,
    domain: DomainConfig,
    descriptor: EntityDescriptor,
    identity: Sequence[str],
    entry: BaseModel,
) -> None:
    """The end of every write, under the lock: the entry placed where the
    configuration would hold it, the reference pass run against the state
    the write would leave, and the row written.

    `domain` is passed in rather than read here, because the write that
    resolves an unchanged-value marker has already read it inside this
    same transaction and a second read would be a second answer to a
    question that must have one.
    """
    _place(domain, descriptor, identity, entry)
    _refuse_unresolved(domain)
    _upsert(
        connection,
        _table(descriptor),
        _row_identity(descriptor, identity),
        _to_row(descriptor, entry),
    )


# Reading rows


def _read_domain(connection: Connection) -> DomainConfig:
    providers: dict[str, dict[str, ProviderConfig]] = {stage: {} for stage in PROVIDER_STAGES}
    for row in connection.execute(select(_table(_PROVIDER))):
        if row.stage not in providers:
            # A stored row, not an argument: the same sentence the stage
            # check raises for a caller's typo, but nothing the caller
            # can do about it, so it is a storage failure here.
            raise StorageError(
                f'providers.{row.stage}.{row.name}: "{row.stage}" is not a provider '
                f"stage; expected one of: " + ", ".join(PROVIDER_STAGES)
            )
        providers[row.stage][row.name] = _from_row(_PROVIDER, row)

    # The rows are read one by one above and assembled here, and the
    # assembly validates too: an entry name, a MAC or a binding that
    # cannot be read is as much a stored-state failure as a column of
    # the wrong shape.
    #
    # The kinds keyed by a name come from the registry, in the order it
    # lists them, which is the order this document has always had and
    # the order a bad row's refusal has always come out in. Arguments
    # are evaluated left to right, so the providers are assembled first
    # and the devices last, exactly as when each kind was named here.
    domain: DomainConfig | None = None
    problem: str | None = None
    try:
        domain = DomainConfig(
            providers=ProvidersConfig(**providers),
            **{
                descriptor.moved_key: {
                    row.name: _from_row(descriptor, row)
                    for row in connection.execute(select(_table(descriptor)))
                }
                for descriptor in _KEYED_BY_NAME
            },
            devices={
                row.mac: _list(f"devices.{row.mac}", "agents", row.agents)
                for row in connection.execute(select(schema.devices))
            },
        )
    except ValidationError as exc:
        # The sentence only, for the reason `_stored` records: unreadable
        # stored rows are not the caller's fields to correct.
        problem, _ = _validation_problems(_UNREADABLE_ROWS, DomainConfig, exc)
    if domain is None:
        raise StorageError(problem)

    defaults = connection.execute(select(_table(_AGENT_DEFAULTS))).first()
    if defaults is not None:
        domain.agent_defaults = _from_row(_AGENT_DEFAULTS, defaults)
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
    for descriptor in _SECRET_HOLDERS:
        for row in connection.execute(select(_table(descriptor))):
            identity = tuple(getattr(row, part) for part in descriptor.addressing)
            for slot, envelope in _mapping(
                _location(descriptor, *identity), "secrets", row.secrets
            ).items():
                where = SecretLocation(
                    kind=descriptor.secret_slots, identity=".".join(identity), slot=slot
                )
                envelopes[where] = envelope
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
    # Left unset when the column is NULL, which is what a row written
    # before the column existed holds and what "no guidance" means.
    if row.instructions is not None:
        data["instructions"] = row.instructions
    # Read rather than defaulted, and read from the column rather than
    # rescued from NULL: the column is NOT NULL with a database-level
    # default, so a row written before it existed says false itself.
    data["use_server_instructions"] = bool(row.use_server_instructions)
    if row.inject_prompts is not None:
        data["inject_prompts"] = _list(location, "inject_prompts", row.inject_prompts)
    return _stored(McpServerConfig, location, data)


def _agent_from_row(row: Row) -> AgentConfig:
    location = f"agents.{row.name}"
    data = {"prompt": row.prompt or "", **_layer_data(location, row)}
    return _stored(AgentConfig, location, data)


def _defaults_from_row(row: Row) -> AgentDefaults:
    return _stored(AgentDefaults, "agent_defaults", _layer_data("agent_defaults", row))


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
    # The same rule as mcp, and the same reason for reading the column
    # rather than defaulting it: a row written before this column
    # existed holds NULL, which is inherit, and an empty list is an
    # agent that opted out.
    if row.prompt_includes is not None:
        data["prompt_includes"] = _list(location, "prompt_includes", row.prompt_includes)
    return data


# How a row of each kind is read. The three mappings a model demands are
# the ones above, unchanged: the MCP server's per-column omissions,
# which are what lets its transport validator read `model_fields_set`;
# the layer's tri-state, where None inherits and an empty list opts out;
# and the provider's split between its declared fields and the options
# extras. A prompt fragment names no mapping at all, because its model
# is the whole of it.
entities.fill("provider", from_row=_provider_from_row)
entities.fill("mcp-server", from_row=_mcp_from_row)
entities.fill("agent", from_row=_agent_from_row)
entities.fill("agent-defaults", from_row=_defaults_from_row)


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
        # Written as it was given: this is prompt text an operator wrote,
        # and its indentation and its blank lines are part of it.
        "instructions": entry.instructions,
        "use_server_instructions": entry.use_server_instructions,
        # None and an empty list are different configurations here as
        # everywhere else: one is "no prompts named", the other is a
        # list an operator emptied.
        "inject_prompts": (
            list(entry.inject_prompts) if entry.inject_prompts is not None else None
        ),
    }


def _layer_values(entry: AgentDefaults) -> dict[str, object]:
    values: dict[str, object] = {stage: getattr(entry, stage) for stage in PROVIDER_STAGES}
    # None means inherit and a list replaces rather than extends, so an
    # empty list and a null are different configurations and the column
    # has to keep them apart.
    # Each entry as it was written, which is also what makes the column
    # readable by a server running the code that predates the object
    # form: a plain string list is still a plain string list.
    values["mcp"] = (
        [mcp_entry_fragment(item) for item in entry.mcp] if entry.mcp is not None else None
    )
    values["filler"] = entry.filler.model_dump() if entry.filler is not None else None
    values["prompt_includes"] = (
        list(entry.prompt_includes) if entry.prompt_includes is not None else None
    )
    return values


def _agent_values(entry: AgentConfig) -> dict[str, object]:
    return {"prompt": entry.prompt, **_layer_values(entry)}


# How a row of each kind is written, the mirror of the mappings above. A
# prompt fragment names none, so it is written through its model: the
# one column it has is the text as it was given, whose indentation and
# blank lines are part of it, and a dump of the model that holds it is
# exactly that.
entities.fill("provider", to_row=_provider_values)
entities.fill("mcp-server", to_row=_mcp_values)
entities.fill("agent", to_row=_agent_values)
entities.fill("agent-defaults", to_row=_layer_values)


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
        # The kind's own missing sentence with the next step after it,
        # so this and the check that runs before it say one thing about
        # what is not there rather than two.
        raise UnknownEntityError(
            f"{_missing(_HOLDER_OF[location.kind])}; create it first with "
            f"samtal-server config set"
        )


def _secret_identity(descriptor: EntityDescriptor, location: SecretLocation) -> list[str]:
    """A location's identity back as the parameters the kind is
    addressed by. Split at the first separator only, so that a name
    holding one is still one name."""
    return location.identity.split(".", len(descriptor.addressing) - 1)


def _secret_section(location: SecretLocation) -> str:
    """The configuration section a stored secret hangs under, which is
    what a refusal about a slot names. The entity it hangs on is not: a
    location's identity is what the caller addressed."""
    return _HOLDER_OF[location.kind].moved_key


def _secret_row(location: SecretLocation) -> tuple[Table, list[ColumnElement[bool]]]:
    descriptor = _HOLDER_OF[location.kind]
    table = _table(descriptor)
    identity = _secret_identity(descriptor, location)
    return table, [
        table.c[column] == value
        for column, value in _row_identity(descriptor, identity).items()
    ]


def _check_slot(domain: DomainConfig, location: SecretLocation) -> None:
    """The entity exists and the slot is one it can have. Slots are
    defined, not arbitrary: a provider's is a secret-shaped option name,
    an MCP server's is a dotted env or headers path, which is where the
    value would otherwise have been written as a $VAR reference."""
    descriptor = _HOLDER_OF[location.kind]
    identity = _secret_identity(descriptor, location)
    if location.kind == "provider":
        stage, name = identity
        # The stage is an argument here rather than a stored value, so
        # it meets the same refusal a caller's typo meets anywhere else.
        if _entry(domain, descriptor, (_stage(stage), name)) is None:
            raise UnknownEntityError(_missing(descriptor))
        if location.slot.lower().endswith("_env") or not is_secret_option(location.slot):
            raise ConfigError(NOT_A_PROVIDER_SLOT)
        # A slot is addressed in a path of its own, so it obeys the same
        # rule a name does.
        _check_addressable(f"providers.{stage}.{name}", "slot", location.slot)
        return

    if _entry(domain, descriptor, identity) is None:
        raise UnknownEntityError(_missing(descriptor))
    group, _, key = location.slot.partition(".")
    if group not in MCP_SECRET_GROUPS or not key:
        raise ConfigError(NOT_AN_MCP_SLOT)
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


# What a provider addressed by a stage that is not one is told. The four
# stages are named because they are constants of this server; the word
# that was sent is not, for the reason every refusal here has stopped
# repeating one (#132). A stage is a path segment and a command
# argument, so it is a place a paste lands like any other, and a value
# that failed this check is one nothing has validated.
NOT_A_STAGE = "providers: the stage has to be one of " + ", ".join(sorted(PROVIDER_STAGES))


def _stage(stage: str) -> str:
    if stage not in PROVIDER_STAGES:
        raise ConfigError(NOT_A_STAGE)
    return stage


def _identifier(location: str, name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise ConfigError(f"{location}: the name is empty")
    _check_addressable(location, "name", cleaned)
    return cleaned


# The checks a kind runs around its own write, and the two moments they
# run at. A name is checked before the body is parsed; everything about
# the body is checked after it and before the transaction opens. Both
# take the same three arguments, so that a kind's check is named on its
# descriptor rather than found inside a write: the location the refusal
# will name, the parameters that address the entry, and the parsed entry
# itself. A check that does not need one of them says so by ignoring it.


def _check_fragment_name(name: str) -> None:
    """A fragment's name is checked at the write as well as on the
    loaded snapshot, for the reason an MCP entry name is: a write is
    where a name is chosen, and refusing it at the write is what keeps
    the stored state loadable. The refusal names the section and the
    rule and never the name, which is the whole difference from the
    entry-name check beside it.

    It is checked before the body is parsed, and the order is the whole
    of it. Every refusal about a body names the location it was written
    at, which is `prompt_fragments.<name>`, so a request that gets both
    wrong at once (a pasted credential in the path and a body that will
    not parse) would have been answered by a sentence about the body
    carrying the name it must not repeat. A name this rejects is never
    spoken of again.
    """
    if not is_valid_fragment_name(name):
        raise ConfigError(PROMPT_FRAGMENT_NAME_RULE)


def _check_entry_name(
    location: str, identity: tuple[str, ...], entry: McpServerConfig
) -> None:
    """An MCP server's name becomes the prefix its tools are published
    under, so the same rule the loaded snapshot applies is applied to
    the one name being written.

    Recorded inside the handler and raised outside it, as every refusal
    built from another exception here is: the ValueError carries the
    name, and the sentence this raises is the one that travels out.
    """
    problem: str | None = None
    try:
        check_mcp_entry_names({identity[0]: entry})
    except ValueError as exc:
        problem = str(exc)
    if problem is not None:
        raise ConfigError(problem)


def _check_no_url_credentials(
    location: str, _identity: tuple[str, ...], entry: ProviderConfig
) -> None:
    """A provider's address holds no credential.

    The secret-shaped-key rules above stop a secret written under a name
    that admits to being one. A URL is the shape that gets past them:
    `base_url: https://user:password@host/v1` has an innocent key, and
    what it holds is stored in the configuration, read back on every
    display path, and copied verbatim into the manifest of every capture
    and every conversation record made against it. So it is refused
    where it is chosen.

    Write time only, exactly like the addressability rule below and for
    the same reason: a row written before this rule still boots, still
    reads and is still deletable, and a deployment does not get a server
    that refuses to start over a value it can no longer edit. The record
    is defended on its own side as well, by building a manifest that
    strips this rather than by trusting that no row has it.

    The refusal names the option and the rule and never the value: what
    fails this check is a credential.
    """
    for key, value in entry.options.items():
        _refuse_url_credentials(f"{location}.{key}", value)


entities.fill("provider", inside_write=_check_no_url_credentials)
entities.fill("mcp-server", inside_write=_check_entry_name)
entities.fill("prompt-fragment", before_parse=_check_fragment_name)


def _refuse_url_credentials(path: str, value: object) -> None:
    """The same question at every depth, since an option can be a
    structure and `connection: {url: ...}` is as ordinary to write as
    `url: ...` is."""
    carried = url_credential(value)
    if carried == "userinfo":
        raise ConfigError(
            f'"{path}" is a URL carrying a user and password before its host, which '
            f"is not allowed: this value is stored as written, shown on every read, "
            f"and copied into the manifest of every capture and conversation record "
            f"made against this provider. Write the address on its own and name the "
            f"variable holding the credential, for example api_key_env: "
            f"MY_PROVIDER_KEY. The value is not quoted back"
        )
    if carried == "query":
        raise ConfigError(
            f'"{path}" is a URL carrying a credential as a query parameter, which is '
            f"not allowed, for the reason a user and password before the host is "
            f"not: this value is stored as written, shown on every read, and copied "
            f"into the manifest of every capture and conversation record made "
            f"against this provider. Name the variable holding the credential "
            f"instead, for example api_key_env: MY_PROVIDER_KEY. The value is not "
            f"quoted back"
        )
    if isinstance(value, Mapping):
        for key, nested in value.items():
            _refuse_url_credentials(f"{path}.{key}", nested)
    elif isinstance(value, (list, tuple)):
        for position, item in enumerate(value):
            _refuse_url_credentials(f"{path}.{position}", item)


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


def _readable(location: str, fragment: object) -> dict[str, object]:
    """A fragment as a mapping this repository can walk, or the refusal
    for one that is not.

    Everything before validation that is about the fragment being a
    fragment at all: an omitted body is the empty one, a body that is not
    a mapping of keys is refused naming its type and never its contents,
    and what JSON cannot carry is refused here rather than by the
    encoder. It runs before the unchanged-value marker below for a
    reason the marker's walk depends on: what comes back is a finite
    tree of string keys, so a walk over it terminates.
    """
    if fragment is None:
        fragment = {}
    if not isinstance(fragment, Mapping):
        raise ConfigError(
            f"invalid {location}: expected a mapping of keys, got {type(fragment).__name__}"
        )
    check_transportable(location, fragment)
    return dict(fragment)


# The unchanged-value marker
#
# A read masks whatever sits under a secret-shaped key, and not every
# masked value is a stored secret. A lowercase environment name in an
# `*_env` option and a whitespace-padded `$VAR` in an MCP server's env
# both validate on the way in and fail the display's reference test on
# the way out, so a read of such an entity shows the mask where a value
# the operator wrote is stored. A resubmission of that read therefore
# has to mean something, and it means: keep what is stored there (#192).
#
# The predicate is the kind's own `secret_key`, the descriptor fact the
# display masks by (#207), asked at every depth the display walks and
# stopping where the display stops. What a read hides and what a write
# restores are then one rule rather than two that can come to disagree.
#
# A mask with nothing stored behind it is refused rather than written.
# The mask is not a value: storing it would put eight asterisks in the
# row and read them back as a credential that is not there.
#
# Which paths carry a mark is a question about the fragment alone, so it
# is asked before the write lock; what to put in their place is a
# question about the row, so it is asked inside the transaction that
# replaces that row, and `_write` above says why.

# What is looked up and not found, distinct from a stored null, which is
# a field holding nothing and so is not a value to keep either.
_NOTHING = object()


def _keep(
    descriptor: EntityDescriptor,
    location: str,
    fragment: Mapping[str, object],
    marks: Sequence[Sequence[object]],
    stored: object,
) -> dict[str, object]:
    """The fragment with every unchanged-value marker resolved: the mask
    replaced by what the entity already holds at the same path, so that a
    read resubmitted whole validates exactly as if the operator had
    retyped the value the display would not show them.

    `stored` is the entry as the caller's own transaction reads it, and
    the caller holding that transaction is the whole of this function's
    correctness: the value put back is one this write is about to replace
    while nobody else can be replacing it.

    A mask with nothing stored under it is refused. A PUT that creates
    the entity is that case for every mark in it, since an entity that is
    not there yet holds nothing to keep.
    """
    kept = dict(fragment)
    missing: list[Sequence[object]] = []
    for path in marks:
        held = _held(stored, path)
        if held is _NOTHING:
            missing.append(path)
            continue
        kept = _substituted(kept, path, held)
    if missing:
        raise ConfigError(*_mask_refusal(location, descriptor.model, missing))
    return kept


def _masked_paths(
    value: object, secret_key: Callable[[str], bool], segments: tuple[object, ...] = ()
) -> Iterator[tuple[object, ...]]:
    """Every path in a fragment where a secret-shaped key holds the mask
    exactly.

    The same walk the display makes, in the same order and to the same
    depth: mappings and lists are walked into, and a secret-shaped key is
    not, because the display displaces whatever such a key holds and so
    nothing under one was ever shown to resubmit. A mask under a key the
    predicate does not match is not a marker at all, and meets validation
    as the string it is.
    """
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if secret_key(str(key)):
                if isinstance(nested, str) and nested == MASK:
                    yield (*segments, key)
                continue
            yield from _masked_paths(nested, secret_key, (*segments, key))
    elif isinstance(value, (list, tuple)):
        for position, item in enumerate(value):
            yield from _masked_paths(item, secret_key, (*segments, position))


def _held(stored: object, path: Sequence[object]) -> object:
    """What the stored entry holds at one path of a fragment, or
    `_NOTHING` where it holds nothing at all.

    The stored side is models where the fragment side is mappings, so
    the walk asks each shape its own way: a declared field by attribute,
    a pass-through model's extras and a mapping by key, a list by
    position. A null anywhere along the path is nothing rather than
    something, which is also why the display left the field out of the
    read this fragment came from.
    """
    reached: object = stored
    for segment in path:
        if isinstance(reached, BaseModel):
            fields = type(reached).model_fields
            reached = (
                getattr(reached, segment)
                if isinstance(segment, str) and segment in fields
                else (reached.model_extra or {}).get(segment, _NOTHING)
            )
        elif isinstance(reached, Mapping):
            reached = reached.get(segment, _NOTHING)
        elif isinstance(reached, (list, tuple)) and isinstance(segment, int):
            reached = reached[segment] if segment < len(reached) else _NOTHING
        else:
            return _NOTHING
        if reached is _NOTHING or reached is None:
            return _NOTHING
    return reached


def _substituted(
    fragment: Mapping[str, object], path: Sequence[object], kept: object
) -> dict[str, object]:
    """The fragment with `kept` where `path` reaches into it, copying
    only the containers along the way and leaving everything beside them
    the object it already was."""
    head, rest = path[0], path[1:]
    return {
        key: _inside(value, rest, kept) if key == head else value
        for key, value in fragment.items()
    }


def _inside(value: object, path: Sequence[object], kept: object) -> object:
    """The same substitution one level down, and `kept` itself once the
    path runs out."""
    if not path:
        return kept
    if isinstance(value, Mapping):
        return _substituted(value, path, kept)
    if isinstance(value, (list, tuple)):
        return [
            _inside(item, path[1:], kept) if position == path[0] else item
            for position, item in enumerate(value)
        ]
    return value


def _mask_refusal(
    location: str, model: type[BaseModel], paths: Sequence[Sequence[object]]
) -> tuple[str, tuple[FieldProblem, ...]]:
    """The refusal for a mask with nothing stored behind it, in both the
    renderings a refusal needs.

    What may be named is `safe_location`'s rule, the one every refusal
    built from a validation error already goes through: a field this
    repository declares is named, and a key the caller wrote is not, so
    the sentence and the pointer stop at the nearest place that can be
    named. A key holding the mask is as good a place to have pasted a
    credential as a value is, and the value itself is never in either
    rendering: there is nothing to say about it beyond that it is the
    mask.

    Two marks under one name are one problem, for the reason the MCP
    secret rule gives: the entries would be indistinguishable, and a
    refusal saying the same thing twice only suggests the second was
    about something else.
    """
    problems: list[FieldProblem] = []
    for path in paths:
        safe, dropped = safe_location(model, path)
        where = ".".join(str(part) for part in safe)
        problem = FieldProblem(json_pointer(safe), _nothing_kept(where, dropped))
        if problem not in problems:
            problems.append(problem)
    lines = [f"invalid {location}:"]
    lines += [_refusal_line("", problem.message) for problem in problems]
    return "\n".join(lines), tuple(problems)


def _nothing_kept(where: str, dropped: bool) -> str:
    """What one such mask is told, named as far as the rule above
    allows."""
    if dropped:
        place = f" in {where}" if where else ""
        return (
            f"a key{place} holds the mask {MASK}, which a write reads as keep the "
            f"stored value, and nothing is stored there; write the value it should "
            f"hold, or leave the key out. The key is not quoted back"
        )
    return (
        f'"{where}" holds the mask {MASK}, which a write reads as keep the stored '
        f"value, and nothing is stored there; write the value it should hold, or "
        f"leave the field out"
    )


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
    problems: tuple[FieldProblem, ...] = ()
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
        problem, problems = _validation_problems(f"invalid {location}:", model, exc)
    # The one place the structured half is filled: this is the refusal a
    # caller can act on, field by field, and the pairs the sentence was
    # rendered from are the pairs it carries.
    raise ConfigError(problem, problems)


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
    unwritable = _untransportable(data, numbers_only=True)
    if unwritable is not None:
        raise StorageError(f"{location}: {unwritable}; the row cannot be read as configuration")
    problem: str | None = None
    try:
        return model.model_validate(dict(data))
    except ValidationError as exc:
        # The sentence only. A 500 is not a form the caller can correct,
        # and the fields that failed are fields of a stored row rather
        # than of anything this request sent, so there is nothing for a
        # structured entry to attach to.
        problem, _ = _validation_problems(f"{location}: {_UNREADABLE_ROW}", model, exc)
    raise StorageError(problem)


def _untransportable(
    value: object,
    path: str = "",
    ancestors: frozenset[int] = frozenset(),
    *,
    numbers_only: bool = False,
) -> str | None:
    """What in `value` JSON cannot carry, said without quoting any of it,
    or None.

    One walk, asked two questions, because the second is the first's
    float branch and nothing else. A fragment somebody wrote is asked
    all of it: a YAML file can hold a date, a set, a key that is not a
    string, and a structure that contains itself, and none of those has
    a written form JSON reads back as what it says. A stored row is
    asked about the numbers only, and that is not a narrowing for
    tidiness: the row came out of a JSON column, so it cannot hold any
    of the rest, and it must be walked without a cycle rule because a
    row cannot refer to itself either.

    Cycle-safe by carrying the containers currently above this one
    rather than every container already seen: two keys pointing at the
    same anchored mapping is a shape JSON writes out twice and reads
    back correctly, so refusing it would refuse a legitimate YAML file.
    A container that is its own ancestor is the one that cannot be
    written at all.
    """
    if not numbers_only and id(value) in ancestors:
        return _RECURSIVE.format(where=path or "the fragment")
    if isinstance(value, Mapping):
        below = ancestors | {id(value)}
        for key, nested in value.items():
            if not numbers_only and not isinstance(key, str):
                return _NON_STRING_KEY.format(
                    where=path or "the fragment", kind=type(key).__name__
                )
            found = _untransportable(
                nested,
                f"{path}.{key}" if path else str(key),
                below,
                numbers_only=numbers_only,
            )
            if found is not None:
                return found
        return None
    if isinstance(value, (list, tuple)):
        below = ancestors | {id(value)}
        for position, item in enumerate(value):
            found = _untransportable(
                item,
                f"{path}.{position}" if path else str(position),
                below,
                numbers_only=numbers_only,
            )
            if found is not None:
                return found
        return None
    if isinstance(value, float) and not math.isfinite(value):
        # NaN and the infinities have no JSON spelling. A stored one is
        # serialized as null on the way out, which quietly turns a
        # configuration into a different one: the option disappears and
        # the provider falls back to its own default.
        return _NOT_FINITE.format(where=path or "the value")
    if numbers_only:
        return None
    # bool before int, and both before the refusal, because bool is a
    # subclass of int and neither needs naming twice.
    if value is None or isinstance(value, (str, bool, int, float)):
        return None
    return _NOT_TRANSPORTABLE.format(
        where=path or "the fragment", kind=type(value).__name__
    )


def _validation_problems(
    headline: str, model: type[BaseModel], exc: ValidationError
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
    """
    lines = [headline]
    problems: list[FieldProblem] = []
    for error in exc.errors():
        location, dropped = safe_location(model, error["loc"])
        where = ".".join(str(part) for part in location)
        prefix = json_pointer(location)
        for problem in _error_problems(error, dropped):
            lines += [_refusal_line(where, line) for line in problem.message.splitlines()]
            problems.append(FieldProblem(prefix + problem.path, problem.message))
    return "\n".join(lines), tuple(problems)


def _refusal_line(where: str, line: str) -> str:
    """One problem as a refusal prints it: an indented dash, and the
    place in front of it when there is one this repository may name.

    One home for the shape, because a refusal an operator reads is one
    vocabulary however it was produced: the marker above builds its
    sentence without a validation error behind it, and a second spelling
    of the indentation would be a golden that moves for no reason.
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


def _refuse_unresolved(domain: DomainConfig) -> None:
    problems = check_references(domain)
    if problems:
        raise ConfigError(
            "the change was refused; it would leave these references unresolved:\n"
            + "\n".join(f"  - {problem}" for problem in problems)
        )


__all__ = [
    "BoundDevice",
    "ConfigStore",
    "check_transportable",
    "DomainConfig",
    "Entity",
    "LiveBinding",
    "read_live_binding",
    "Snapshot",
    "StoredSecret",
    "stored_secrets",
    "verify_secrets",
]
