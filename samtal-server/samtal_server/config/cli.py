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

The configuration is read once at boot, so a write here applies at the
next server start. Every mutating command says so: an edit that
silently waits for a restart is the operational trap of a boot-time
snapshot, and the one place to close it is where the edit is made.
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

from samtal_server.config import docgen, views
from samtal_server.config.loader import CONFIG_ENV_VAR, ConfigError, load_file_config
from samtal_server.config.models import PROVIDER_STAGES, normalize_mac
from samtal_server.config.secrets import (
    MASK,
    EntityKind,
    SecretLocation,
    SecretStore,
    load_keys,
)
from samtal_server.config.store import ConfigStore, Snapshot
from samtal_server.db import open_database

# Printed after every mutating command. The configuration is a
# boot-time snapshot by design, and a write that quietly waits for a
# restart is the one thing about that design an operator can be caught
# by, so the write itself says when it takes effect.
RESTART_NOTICE = (
    "This applies at the next server start: the configuration is read once at boot."
)

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
    with _store(args) as store:
        _print_entity(views.provider(store.read_provider(args.stage, args.name)))


def _show_mcp_server(args: argparse.Namespace) -> None:
    with _store(args) as store:
        _print_entity(views.mcp_server(store.read_mcp_server(args.name)))


def _show_agent(args: argparse.Namespace) -> None:
    with _store(args) as store:
        _print_entity(views.agent(store.read_agent(args.name)))


def _show_agent_defaults(args: argparse.Namespace) -> None:
    with _store(args) as store:
        _print_entity(views.agent_defaults(store.read_agent_defaults()))


def _schema(args: argparse.Namespace) -> None:
    """The JSON Schema of one entity kind, or of the whole domain
    configuration. Reads the models and nothing else: no database, no
    configuration file, no encryption key."""
    print(docgen.schema(args.entity), end="")


def _reference(args: argparse.Namespace) -> None:
    """The markdown reference, the same document CI diffs the committed
    copy against."""
    print(docgen.reference(), end="")


def _openapi(args: argparse.Namespace) -> None:
    """The configuration API's OpenAPI document, the other artifact CI
    diffs its committed copy against. Rendered from the routes, so it
    opens no database and needs no token: the application is built, its
    document is taken, and nothing of it is served."""
    print(docgen.openapi(), end="")


def _show_device(args: argparse.Namespace) -> None:
    with _store(args) as store:
        _print_entity(views.device(store.read_device(args.mac)))


# Rendering


def _show_everything(snapshot: Snapshot) -> str:
    """The whole domain configuration in one document, in the shape the
    YAML file has today, with the stored secrets listed as masks
    underneath it."""
    document = views.config(snapshot)
    notes = _all_secret_notes(document)
    return _yaml(document["config"]) + ("\n" + "\n".join(notes) + "\n" if notes else "")


def _print_entity(envelope: dict[str, object]) -> None:
    """One entity's envelope as YAML: the masked body, and its stored
    slots as comment lines. Comments rather than a mapping, because the
    mask is not a value that could be written back, and saying so in the
    document is more honest than rendering it as though it could."""
    body = envelope["entity"]
    notes = _secret_notes(body, envelope["secrets"])
    print(_yaml(body) + ("\n" + "\n".join(notes) + "\n" if notes else ""), end="")


def _all_secret_notes(document: dict[str, object]) -> list[str]:
    """Every stored secret in the whole-configuration view, each named by
    its location and marked when it shadows a reference written for the
    same slot."""
    bodies = _bodies(document["config"])
    notes = [
        f"#   {stored['kind']} {stored['identity']} {stored['slot']}: {MASK}"
        + _shadow_note(bodies.get((stored["kind"], stored["identity"]), {}), stored["shadows"])
        for stored in document["secrets"]
    ]
    return [SECRETS_HEADING, *notes] if notes else []


def _secret_notes(body: dict[str, object], secrets: dict[str, object]) -> list[str]:
    notes = [
        f"#   {slot}: {MASK}" + _shadow_note(body, marks["shadows"])
        for slot, marks in secrets.items()
    ]
    return [SECRETS_HEADING, *notes] if notes else []


def _shadow_note(body: dict[str, object], shadows: str | None) -> str:
    """What a stored secret displaces, when the entity also carries a
    reference for the same slot. Ciphertext wins, and making that
    visible is what keeps the precedence from being silent."""
    reference = views.reference_value(body, shadows) if shadows else None
    return f"  (used instead of {shadows}: {reference})" if reference else ""


def _bodies(config: dict[str, object]) -> dict[tuple[str, str], dict[str, object]]:
    """The masked body of every entity that can hold a stored secret,
    keyed the way a secret location names it."""
    bodies = {
        ("provider", f"{stage}.{name}"): body
        for stage, entries in config["providers"].items()
        for name, body in entries.items()
    }
    bodies.update(
        (("mcp_server", name), body) for name, body in config["mcp_servers"].items()
    )
    return bodies


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

    defaults = _inline(views.layer_body(domain.agent_defaults))
    lines.append("agent_defaults: " + (defaults or "(none)"))

    lines.append("agents:")
    lines += [
        f"  {name}" + (f": {_inline(views.layer_body(entry))}" if views.layer_body(entry) else "")
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
    # Rendered from the problem and the mark rather than from str(exc),
    # which quotes the offending source line back, and recorded rather
    # than raised inside the handler: a PyYAML mark holds the whole
    # buffer it was parsing, which here is the fragment, and an
    # exception raised inside a handler keeps the one being handled as
    # its __context__.
    problem: str | None = None
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        detail = str(exc)
        if isinstance(exc, yaml.MarkedYAMLError) and exc.problem_mark is not None:
            mark = exc.problem_mark
            detail = f"{exc.problem} at line {mark.line + 1}, column {mark.column + 1}"
        problem = f"invalid YAML in {source}: {detail}"
    raise ConfigError(problem)


def _file(path: str) -> str:
    problem: str | None = None
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        problem = f"fragment file not found: {path}"
    except OSError as exc:
        problem = f"cannot read fragment file {path}: {exc.strerror}"
    raise ConfigError(problem)


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


def _database_dir(args: argparse.Namespace) -> Path:
    """Where the server keeps its domain configuration, read through the
    settings machinery the server reads it with, so the two cannot
    disagree. No configuration file has to exist: without one the field
    default and the SAMTAL_ environment are the whole answer."""
    return load_file_config(args.config).server.database.dir


def _mac(mac: str) -> str:
    problem: str | None = None
    try:
        return normalize_mac(mac)
    except ValueError as exc:
        problem = str(exc)
    raise ConfigError(problem)


def _wrote(what: str) -> None:
    print(f"wrote {what}")
    # Flushed first, so the notice lands after the line it is about
    # rather than ahead of it: stderr is unbuffered and stdout is not.
    sys.stdout.flush()
    print(RESTART_NOTICE, file=sys.stderr)


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

    # Read-only and local: these three render the models and the API's
    # own routes, so they take no --config, open no database, and need
    # no encryption key. Keep it that way: the documentation lane runs
    # `config reference` and `config openapi` from a plain sync, with no
    # database and no key anywhere.
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

    openapi = commands.add_parser(
        "openapi", help="the configuration API's OpenAPI document, generated from its routes"
    )
    openapi.set_defaults(run=_openapi)

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


__all__ = ["RESTART_NOTICE", "main"]
