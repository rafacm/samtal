"""The `samtal-server config` command group.

The write path for the domain half of the configuration, and a
deliberate rehearsal of the REST API that will replace it: one noun per
entity kind, YAML fragments as the write payload in the shape the API
will take as JSON, secrets write-only with masked reads. Nothing here
decides anything about the configuration. Parsing, validation,
reference checks and secret handling all live in the repository, so
the day the CLI becomes a client of the API it changes its backend and
not its grammar.

Nothing plaintext is ever an argument: a secret arrives on stdin (not
echoed when the terminal is interactive) or from a named environment
variable, because arguments land in shell history and in the process
list. Every failure leaves as a ConfigError printed to stderr with exit
code 1, naming the location and the kind of failure without quoting the
value that caused it, and no traceback from pydantic, PyYAML,
SQLAlchemy or cryptography reaches the user.

Until the switchover lands, the server does not read what this writes,
so every mutating command says so.
"""

import argparse
import getpass
import os
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import NoReturn

import yaml

from samtal_server.config import docgen
from samtal_server.config.loader import CONFIG_ENV_VAR, ConfigError, load_config
from samtal_server.config.models import (
    PROVIDER_STAGES,
    AgentDefaults,
    McpServerConfig,
    ProviderConfig,
    is_mcp_secret_key,
    is_secret_option,
    normalize_mac,
)
from samtal_server.config.secrets import (
    MASK,
    EntityKind,
    SecretLocation,
    SecretStore,
    load_keys,
    mask,
)
from samtal_server.config.store import ConfigStore, DomainConfig, Snapshot
from samtal_server.db import open_database

# Printed after every mutating command until the switchover makes the
# database live, and removed by the same change that does. The window
# between them is a real deployment state (the image publishes from
# main), and a staged write that looks applied is the trap this exists
# to close.
STAGING_NOTICE = """\
------------------------------------------------------------------------
STAGING ONLY: the server does not read this database yet.
It still boots its whole configuration from the YAML file. This write is
staging for the switchover, which is when the domain half starts coming
from here. Nothing about the running server changed.
------------------------------------------------------------------------"""

# How a stored secret is introduced in `show` and `list`. Comment lines
# rather than a mapping: the mask is not a value that could be written
# back, and saying so in the document is more honest than rendering it
# as though it could.
SECRETS_HEADING = "# stored secrets, set with: samtal-server config set-secret"


def main(argv: Sequence[str] | None = None) -> int:
    """Run one config command. Returns the process exit code.

    Parsing is inside the boundary, so a mistake in the grammar answers
    the way a mistake in a fragment does: a sentence on stderr and exit
    1. --help still leaves through argparse's own exit 0, because asking
    for help is not a failure."""
    try:
        args = _parser().parse_args(argv)
        args.run(args)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


class _Parser(argparse.ArgumentParser):
    """A parser whose usage errors leave through the same door as every
    other failure.

    argparse writes to stderr and exits 2 from inside parse_args, which
    would make an unknown command or a missing argument the one failure
    that bypasses the ConfigError boundary and the documented exit
    codes. Subparsers inherit this class from the parser that creates
    them, so the whole grammar answers alike."""

    def error(self, message: str) -> NoReturn:
        raise ConfigError(_usage_problem(message))


def _usage_problem(message: str) -> str:
    if message.startswith("unrecognized arguments"):
        # Never the arguments themselves. A secret is never an argument
        # of this CLI, and the mistake that would put one there (typing
        # the value after `set-secret ... api_key`) lands exactly here,
        # where argparse would have echoed it back.
        return (
            "unrecognized extra arguments; run with --help for the grammar. Note that a "
            "secret is never given as an argument: set-secret reads it from stdin, or "
            "from the variable named with --from-env"
        )
    return f"{message}; run with --help for the grammar"


# The commands


def _set_provider(args: argparse.Namespace) -> None:
    fragment = _fragment(args.file)
    with _store(args) as store:
        store.set_provider(args.stage, args.name, fragment)
    _wrote(f"provider {args.stage}.{args.name}")


def _set_mcp_server(args: argparse.Namespace) -> None:
    fragment = _fragment(args.file)
    with _store(args) as store:
        store.set_mcp_server(args.name, fragment)
    _wrote(f"mcp-server {args.name}")


def _set_agent(args: argparse.Namespace) -> None:
    fragment = _fragment(args.file)
    with _store(args) as store:
        store.set_agent(args.name, fragment)
    _wrote(f"agent {args.name}")


def _set_agent_defaults(args: argparse.Namespace) -> None:
    fragment = _fragment(args.file)
    with _store(args) as store:
        store.set_agent_defaults(fragment)
    _wrote("agent-defaults")


def _delete_provider(args: argparse.Namespace) -> None:
    with _store(args) as store:
        store.delete_provider(args.stage, args.name)
    _wrote(f"provider {args.stage}.{args.name} deleted, with its stored secrets")


def _delete_mcp_server(args: argparse.Namespace) -> None:
    with _store(args) as store:
        store.delete_mcp_server(args.name)
    _wrote(f"mcp-server {args.name} deleted, with its stored secrets")


def _delete_agent(args: argparse.Namespace) -> None:
    with _store(args) as store:
        store.delete_agent(args.name)
    _wrote(f"agent {args.name} deleted")


def _delete_device(args: argparse.Namespace) -> None:
    with _store(args) as store:
        store.delete_device(args.mac)
    _wrote(f"device {_mac(args.mac)} deleted")


def _bind_device(args: argparse.Namespace) -> None:
    with _store(args) as store:
        store.bind_device(args.mac, args.agents)
    _wrote(f"device {_mac(args.mac)} bound to {', '.join(args.agents)}")


def _set_default_agent(args: argparse.Namespace) -> None:
    with _store(args) as store:
        store.set_default_agent(args.name)
    _wrote(f"default agent {args.name}")


def _clear_default_agent(args: argparse.Namespace) -> None:
    with _store(args) as store:
        store.clear_default_agent()
    _wrote("default agent cleared; the devices map is now the allowlist")


def _set_secret(args: argparse.Namespace) -> None:
    location = _secret_location(args)
    secret = _read_secret(args)
    with _store(args) as store:
        store.set_secret(location, secret)
    _wrote(f"secret for {location.describe()}")


def _clear_secret(args: argparse.Namespace) -> None:
    location = _secret_location(args)
    with _store(args) as store:
        store.clear_secret(location)
    _wrote(f"secret for {location.describe()} cleared")


def _list(args: argparse.Namespace) -> None:
    with _store(args) as store:
        snapshot = store.load()
    print(_summary(snapshot), end="")


def _show_all(args: argparse.Namespace) -> None:
    with _store(args) as store:
        snapshot = store.load()
    print(_show_everything(snapshot), end="")


def _show_provider(args: argparse.Namespace) -> None:
    snapshot = _loaded(args)
    entry = _provider_entry(snapshot.domain, args.stage, args.name)
    data = _provider_data(entry)
    # The entity's own keys are both what is shown and what a stored
    # secret may shadow, so the same mapping serves twice.
    _print_entity(data, snapshot.secrets, "provider", f"{args.stage}.{args.name}", data)


def _show_mcp_server(args: argparse.Namespace) -> None:
    snapshot = _loaded(args)
    entry = snapshot.domain.mcp_servers.get(args.name)
    if entry is None:
        raise ConfigError(f"mcp_servers.{args.name}: no such MCP server")
    _print_entity(_mcp_data(entry), snapshot.secrets, "mcp_server", args.name, _mcp_written(entry))


def _show_agent(args: argparse.Namespace) -> None:
    snapshot = _loaded(args)
    entry = snapshot.domain.agents.get(args.name)
    if entry is None:
        raise ConfigError(f"agents.{args.name}: no such agent")
    print(_yaml({"prompt": entry.prompt, **_layer_data(entry)}), end="")


def _show_agent_defaults(args: argparse.Namespace) -> None:
    print(_yaml(_layer_data(_loaded(args).domain.agent_defaults) or {}), end="")


def _schema(args: argparse.Namespace) -> None:
    """The JSON Schema of one entity kind, or of the whole domain
    configuration. Reads the models and nothing else: no database, no
    configuration file, no encryption key."""
    print(docgen.schema(args.entity), end="")


def _reference(args: argparse.Namespace) -> None:
    """The markdown reference, the same document CI diffs the committed
    copy against."""
    print(docgen.reference(), end="")


def _show_device(args: argparse.Namespace) -> None:
    snapshot = _loaded(args)
    bound = snapshot.domain.devices.get(_mac(args.mac))
    if bound is None:
        raise ConfigError(f"devices.{_mac(args.mac)}: no such device")
    print(_yaml({"agents": list(bound)}), end="")


# Rendering


def _show_everything(snapshot: Snapshot) -> str:
    """The whole domain configuration in one document, in the shape the
    YAML file has today, with the stored secrets listed as masks
    underneath it."""
    domain = snapshot.domain
    data = {
        "providers": {
            stage: {
                name: _provider_data(entry)
                for name, entry in sorted(getattr(domain.providers, stage).items())
            }
            for stage in PROVIDER_STAGES
        },
        "mcp_servers": {
            name: _mcp_data(entry) for name, entry in sorted(domain.mcp_servers.items())
        },
        "agent_defaults": _layer_data(domain.agent_defaults),
        "agents": {
            name: {"prompt": entry.prompt, **_layer_data(entry)}
            for name, entry in sorted(domain.agents.items())
        },
        "devices": {mac: list(bound) for mac, bound in sorted(domain.devices.items())},
        "default_agent": domain.default_agent,
    }
    notes = _all_secret_notes(snapshot)
    return _yaml(data) + ("\n" + "\n".join(notes) + "\n" if notes else "")


def _print_entity(
    data: dict[str, object],
    secrets: SecretStore,
    kind: EntityKind,
    identity: str,
    written: dict[str, object],
) -> None:
    notes = _secret_notes(secrets, kind, identity, written)
    rendered = _yaml(data)
    print(rendered + ("\n" + "\n".join(notes) + "\n" if notes else ""), end="")


def _all_secret_notes(snapshot: Snapshot) -> list[str]:
    """Every stored secret in the snapshot, each named by its location
    and marked when it shadows a reference written for the same slot."""
    written = _written_values(snapshot.domain)
    notes = [
        f"#   {location.describe()}: {MASK}"
        + _shadow_note(written.get((location.kind, location.identity), {}), location.slot)
        for location in snapshot.secrets.locations()
    ]
    return [SECRETS_HEADING, *notes] if notes else []


def _secret_notes(
    secrets: SecretStore, kind: EntityKind, identity: str, written: dict[str, object]
) -> list[str]:
    notes = [
        f"#   {slot}: {MASK}" + _shadow_note(written, slot)
        for slot in secrets.slots_for(kind, identity)
    ]
    return [SECRETS_HEADING, *notes] if notes else []


def _shadow_note(written: dict[str, object], slot: str) -> str:
    """What a stored secret displaces, when the entity also carries a
    reference for the same slot. Ciphertext wins, and making that
    visible is what keeps the precedence from being silent."""
    reference = written.get(_reference_key(slot))
    return f"  (used instead of {_reference_key(slot)}: {reference})" if reference else ""


def _reference_key(slot: str) -> str:
    """The key an environment reference for this slot is written under:
    `<slot>_env` on a provider, the dotted path itself on an MCP
    server."""
    return slot if "." in slot else f"{slot}_env"


def _written_values(domain: DomainConfig) -> dict[tuple[str, str], dict[str, object]]:
    """Every entity's own reference-carrying keys, by location, so a
    stored secret can be matched against what it shadows."""
    written: dict[tuple[str, str], dict[str, object]] = {}
    for stage in PROVIDER_STAGES:
        for name, entry in getattr(domain.providers, stage).items():
            written[("provider", f"{stage}.{name}")] = _provider_data(entry)
    for name, entry in domain.mcp_servers.items():
        written[("mcp_server", name)] = _mcp_written(entry)
    return written


def _mcp_written(entry: McpServerConfig) -> dict[str, object]:
    """An MCP server's env and headers under their dotted slot names,
    which is how a stored secret addresses them. Masked on the same
    rule as everywhere else these are displayed."""
    return {
        f"{group}.{key}": value
        for group, values in (("env", entry.env), ("headers", entry.headers))
        for key, value in _shown_values(values).items()
    }


def _shown_values(values: dict[str, str]) -> dict[str, object]:
    """An MCP server's env or headers as they may be displayed. The
    model already requires a $VAR for the secret-bearing keys, so this
    changes nothing for a valid entry; it is what stops a value that got
    in another way from being read back out."""
    return {
        key: mask(value) if is_mcp_secret_key(key) else value for key, value in values.items()
    }


def _summary(snapshot: Snapshot) -> str:
    """The tree `config list` prints: one line per entity, with the
    slots that hold a stored secret named but never their values."""
    domain = snapshot.domain
    lines = ["providers:"]
    for stage in PROVIDER_STAGES:
        lines.append(f"  {stage}:")
        entries = getattr(domain.providers, stage)
        lines += [
            f"    {name} ({entry.type})"
            + _slots(snapshot.secrets, "provider", f"{stage}.{name}")
            for name, entry in sorted(entries.items())
        ] or ["    (none)"]

    lines.append("mcp_servers:")
    lines += [
        f"  {name} ({entry.transport})" + _slots(snapshot.secrets, "mcp_server", name)
        for name, entry in sorted(domain.mcp_servers.items())
    ] or ["  (none)"]

    lines.append("agent_defaults: " + (_inline(_layer_data(domain.agent_defaults)) or "(none)"))

    lines.append("agents:")
    lines += [
        f"  {name}" + (f": {_inline(_layer_data(entry))}" if _layer_data(entry) else "")
        for name, entry in sorted(domain.agents.items())
    ] or ["  (none)"]

    lines.append("devices:")
    lines += [
        f"  {mac} -> {', '.join(bound)}" for mac, bound in sorted(domain.devices.items())
    ] or ["  (none)"]

    lines.append(f"default_agent: {domain.default_agent or '(none)'}")
    return "\n".join(lines) + "\n"


def _slots(secrets: SecretStore, kind: EntityKind, identity: str) -> str:
    stored = secrets.slots_for(kind, identity)
    return f"  [secrets: {', '.join(stored)}]" if stored else ""


def _inline(data: dict[str, object]) -> str:
    return " ".join(f"{key}={_short(value)}" for key, value in data.items())


def _short(value: object) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(str(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{...}"
    return str(value)


def _provider_data(entry: ProviderConfig) -> dict[str, object]:
    """One provider as it may be displayed. Whatever a secret-shaped key
    holds goes through the mask, which passes an environment reference
    through as itself and fails closed on everything else: nothing
    validates the shape of an api_key_env value, so an operator who
    pasted the key where its variable name belongs must not have it read
    back out by the command they would run to find the mistake."""
    data: dict[str, object] = {"type": entry.type}
    if entry.api_key_env is not None:
        data["api_key_env"] = mask(entry.api_key_env)
    if entry.egress is not None:
        data["egress"] = entry.egress
    data.update(
        {
            key: mask(value) if is_secret_option(key) else value
            for key, value in entry.options.items()
        }
    )
    return data


def _mcp_data(entry: McpServerConfig) -> dict[str, object]:
    data: dict[str, object] = {"transport": entry.transport}
    if entry.command is not None:
        data["command"] = entry.command
    if entry.args:
        data["args"] = list(entry.args)
    if entry.env:
        data["env"] = _shown_values(entry.env)
    if entry.url is not None:
        data["url"] = entry.url
    if entry.headers:
        data["headers"] = _shown_values(entry.headers)
    if entry.egress is not None:
        data["egress"] = entry.egress
    data["tool_timeout_s"] = entry.tool_timeout_s
    return data


def _layer_data(entry: AgentDefaults) -> dict[str, object]:
    data: dict[str, object] = {
        stage: getattr(entry, stage)
        for stage in PROVIDER_STAGES
        if getattr(entry, stage) is not None
    }
    if entry.mcp is not None:
        data["mcp"] = list(entry.mcp)
    if entry.filler is not None:
        data["filler"] = entry.filler.model_dump()
    return data


def _yaml(data: object) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)


# Input


def _fragment(path: str) -> object:
    """One entity's YAML fragment, from a file or from stdin. Parsed
    here and validated by the models in the repository, which is where
    the rule that a secret-bearing key may only name an environment
    variable already lives."""
    source = "the fragment on stdin" if path == "-" else path
    text = _stdin() if path == "-" else _file(path)
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        # Rendered from the problem and the mark rather than from
        # str(exc), which quotes the offending source line back, and
        # raised without a cause so the chain does not carry it either.
        detail = str(exc)
        if isinstance(exc, yaml.MarkedYAMLError) and exc.problem_mark is not None:
            mark = exc.problem_mark
            detail = f"{exc.problem} at line {mark.line + 1}, column {mark.column + 1}"
        raise ConfigError(f"invalid YAML in {source}: {detail}") from None


def _file(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"fragment file not found: {path}") from None
    except OSError as exc:
        raise ConfigError(f"cannot read fragment file {path}: {exc.strerror}") from None


def _stdin() -> str:
    return sys.stdin.read()


def _read_secret(args: argparse.Namespace) -> str:
    """The secret itself, from a named environment variable or from
    stdin. Never from an argument: arguments land in shell history and
    in the process list. An interactive terminal is read without echo;
    a pipe or a redirect is read plainly, which is what scripts use."""
    if args.from_env:
        secret = os.environ.get(args.from_env, "")
        if not secret:
            raise ConfigError(
                f"--from-env names {args.from_env}, but it is not set in the environment"
            )
        return secret

    if sys.stdin is not None and sys.stdin.isatty():
        secret = getpass.getpass("Secret (not echoed): ")
    else:
        secret = _stdin()
    # The trailing newline is the shell's, not the secret's.
    secret = secret.rstrip("\r\n")
    if not secret:
        raise ConfigError(
            "the secret is empty; pipe it in, type it at the prompt, or name the "
            "variable holding it with --from-env"
        )
    return secret


def _secret_location(args: argparse.Namespace) -> SecretLocation:
    if args.kind == "provider":
        return SecretLocation.provider(args.stage, args.name, args.slot)
    return SecretLocation.mcp_server(args.name, args.slot)


# The database, and the output around it


@contextmanager
def _store(args: argparse.Namespace) -> Iterator[ConfigStore]:
    engine = open_database(_database_dir(args))
    try:
        yield ConfigStore(engine, load_keys())
    finally:
        engine.dispose()


def _loaded(args: argparse.Namespace) -> Snapshot:
    with _store(args) as store:
        return store.load()


def _database_dir(args: argparse.Namespace) -> Path:
    """Where the server keeps its domain configuration, read through the
    settings machinery the server reads it with, so the two cannot
    disagree. No configuration file has to exist: without one the field
    default and the SAMTAL_ environment are the whole answer."""
    return load_config(args.config).server.database.dir


def _provider_entry(domain: DomainConfig, stage: str, name: str) -> ProviderConfig:
    if stage not in PROVIDER_STAGES:
        raise ConfigError(
            f'"{stage}" is not a provider stage; expected one of: ' + ", ".join(PROVIDER_STAGES)
        )
    entry = getattr(domain.providers, stage).get(name)
    if entry is None:
        raise ConfigError(f"providers.{stage}.{name}: no such provider")
    return entry


def _mac(mac: str) -> str:
    try:
        return normalize_mac(mac)
    except ValueError as exc:
        raise ConfigError(str(exc)) from None


def _wrote(what: str) -> None:
    print(f"wrote {what}")
    # Flushed first, so the notice lands after the line it is about
    # rather than ahead of it: stderr is unbuffered and stdout is not.
    sys.stdout.flush()
    print(STAGING_NOTICE, file=sys.stderr)


# The grammar


def _fragment_parser(
    kinds: argparse._SubParsersAction, name: str, parents: list[argparse.ArgumentParser]
) -> argparse.ArgumentParser:
    """One `set <kind>` command, whose help lists the fields its
    fragment may carry. The list is generated from the same
    Field(description=...) values the reference and the JSON Schema are
    rendered from, so the three cannot disagree and nobody has to
    remember to update a help string when a field changes."""
    return kinds.add_parser(
        name,
        parents=parents,
        epilog=docgen.fragment_help(name),
        # The epilog is laid out already; the default formatter would
        # reflow it into one paragraph.
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )


def _parser() -> argparse.ArgumentParser:
    config_help = (
        f"path to the YAML config file naming server.database.dir "
        f"(default: ${CONFIG_ENV_VAR})"
    )
    # Accepted before the command and after it, because both readings are
    # natural: `samtal-server --config path` is how the server takes it,
    # and options after their subcommand is how everything else does. The
    # per-command copy suppresses its default rather than defaulting to
    # None, or an option given before the command would be overwritten by
    # the command's own empty default.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config", metavar="PATH", default=argparse.SUPPRESS, help=config_help
    )
    fragment = argparse.ArgumentParser(add_help=False)
    fragment.add_argument(
        "-f",
        "--file",
        metavar="PATH",
        required=True,
        help="YAML fragment for this entity, or - to read it from stdin",
    )

    parser = _Parser(
        prog="samtal-server config",
        description=(
            "Read and write the domain half of the configuration: providers, "
            "MCP servers, agents, devices and their secrets."
        ),
    )
    parser.add_argument("--config", metavar="PATH", default=None, help=config_help)
    commands = parser.add_subparsers(dest="command", required=True)

    setter = commands.add_parser(
        "set", help="create or replace one entity from a YAML fragment"
    )
    kinds = setter.add_subparsers(dest="kind", required=True)
    entity = _fragment_parser(kinds, "provider", [common, fragment])
    entity.add_argument("stage", metavar="STAGE", help=", ".join(PROVIDER_STAGES))
    entity.add_argument("name", metavar="NAME")
    entity.set_defaults(run=_set_provider)
    entity = _fragment_parser(kinds, "mcp-server", [common, fragment])
    entity.add_argument("name", metavar="NAME")
    entity.set_defaults(run=_set_mcp_server)
    entity = _fragment_parser(kinds, "agent", [common, fragment])
    entity.add_argument("name", metavar="NAME")
    entity.set_defaults(run=_set_agent)
    entity = _fragment_parser(kinds, "agent-defaults", [common, fragment])
    entity.set_defaults(run=_set_agent_defaults)

    deleter = commands.add_parser("delete", help="delete one entity")
    kinds = deleter.add_subparsers(dest="kind", required=True)
    entity = kinds.add_parser("provider", parents=[common])
    entity.add_argument("stage", metavar="STAGE", help=", ".join(PROVIDER_STAGES))
    entity.add_argument("name", metavar="NAME")
    entity.set_defaults(run=_delete_provider)
    entity = kinds.add_parser("mcp-server", parents=[common])
    entity.add_argument("name", metavar="NAME")
    entity.set_defaults(run=_delete_mcp_server)
    entity = kinds.add_parser("agent", parents=[common])
    entity.add_argument("name", metavar="NAME")
    entity.set_defaults(run=_delete_agent)
    entity = kinds.add_parser("device", parents=[common])
    entity.add_argument("mac", metavar="MAC")
    entity.set_defaults(run=_delete_device)

    bind = commands.add_parser(
        "bind-device", parents=[common], help="bind a device to one or more agents"
    )
    bind.add_argument("mac", metavar="MAC")
    bind.add_argument("agents", metavar="AGENT", nargs="+")
    bind.set_defaults(run=_bind_device)

    default = commands.add_parser(
        "set-default-agent", parents=[common], help="the agent an unbound device reaches"
    )
    default.add_argument("name", metavar="NAME")
    default.set_defaults(run=_set_default_agent)

    cleared = commands.add_parser(
        "clear-default-agent",
        parents=[common],
        help="unset it, leaving the devices map as the allowlist",
    )
    cleared.set_defaults(run=_clear_default_agent)

    secret = commands.add_parser(
        "set-secret", help="store one credential, encrypted, read from stdin or a variable"
    )
    kinds = secret.add_subparsers(dest="kind", required=True)
    entity = kinds.add_parser("provider", parents=[common])
    entity.add_argument("stage", metavar="STAGE", help=", ".join(PROVIDER_STAGES))
    entity.add_argument("name", metavar="NAME")
    entity.add_argument("slot", metavar="SLOT", help="the option it fills, such as api_key")
    entity.add_argument("--from-env", metavar="VAR", help="read the value from this variable")
    entity.set_defaults(run=_set_secret)
    entity = kinds.add_parser("mcp-server", parents=[common])
    entity.add_argument("name", metavar="NAME")
    entity.add_argument("slot", metavar="SLOT", help="env.<KEY> or headers.<KEY>")
    entity.add_argument("--from-env", metavar="VAR", help="read the value from this variable")
    entity.set_defaults(run=_set_secret)

    clear = commands.add_parser("clear-secret", help="remove one stored credential")
    kinds = clear.add_subparsers(dest="kind", required=True)
    entity = kinds.add_parser("provider", parents=[common])
    entity.add_argument("stage", metavar="STAGE", help=", ".join(PROVIDER_STAGES))
    entity.add_argument("name", metavar="NAME")
    entity.add_argument("slot", metavar="SLOT")
    entity.set_defaults(run=_clear_secret)
    entity = kinds.add_parser("mcp-server", parents=[common])
    entity.add_argument("name", metavar="NAME")
    entity.add_argument("slot", metavar="SLOT")
    entity.set_defaults(run=_clear_secret)

    listing = commands.add_parser("list", parents=[common], help="a summary tree")
    listing.set_defaults(run=_list)

    # Read-only and local: these two render the models, so they take no
    # --config, open no database, and need no encryption key.
    schema = commands.add_parser(
        "schema", help="the JSON Schema of one entity, or of the whole domain half"
    )
    schema.add_argument(
        "entity",
        metavar="ENTITY",
        nargs="?",
        help=", ".join(docgen.entity_names()) + " (default: domain)",
    )
    schema.set_defaults(run=_schema)

    reference = commands.add_parser(
        "reference", help="the markdown reference, generated from the models"
    )
    reference.set_defaults(run=_reference)

    show = commands.add_parser("show", parents=[common], help="everything, or one entity")
    show.set_defaults(run=_show_all)
    kinds = show.add_subparsers(dest="kind")
    entity = kinds.add_parser("provider", parents=[common])
    entity.add_argument("stage", metavar="STAGE", help=", ".join(PROVIDER_STAGES))
    entity.add_argument("name", metavar="NAME")
    entity.set_defaults(run=_show_provider)
    entity = kinds.add_parser("mcp-server", parents=[common])
    entity.add_argument("name", metavar="NAME")
    entity.set_defaults(run=_show_mcp_server)
    entity = kinds.add_parser("agent", parents=[common])
    entity.add_argument("name", metavar="NAME")
    entity.set_defaults(run=_show_agent)
    entity = kinds.add_parser("agent-defaults", parents=[common])
    entity.set_defaults(run=_show_agent_defaults)
    entity = kinds.add_parser("device", parents=[common])
    entity.add_argument("mac", metavar="MAC")
    entity.set_defaults(run=_show_device)

    return parser


__all__ = ["STAGING_NOTICE", "main"]
