"""The one rule behind the local-first promise (#30, #136).

A fully local deployment is first-class, and the product promise makes
that a guarantee enforced rather than documented
(`docs/architecture/product-promises.md`): everything that can
carry session data off the machine declares whether it does, and
`server.local_only` refuses at startup to build one that does. The
enforcement used to exist twice, once in the provider registry and once
in the MCP build path, each with its own semantics, wording and
exception type, and the provider half defaulted an undeclared type to
egress, so a type that forgot to declare merely looked declared enough
to boot. A guarantee spread over two implementations and a default is
one nobody can read in a sitting, so both live here (#136), and neither
caller decides anything: they translate.

Translation is all they do because the exception type is each surface's
own contract. This module raises `EgressRefusal` carrying the finished
sentence; `build_provider` re-raises it as `ProviderError` and
`_managers_for` as `McpConfigError`, with the message untouched.
Importing either of those here is what a module below both callers
cannot do, and the reason this one sits beside the packages it serves
rather than inside one of them.

The sentences are operator-facing and value-free: they name the
configuration entry, the type and the key to write, never the value that
was read. The provider refusals say "off this host" and the MCP ones
"off this network", which is a distinction rather than drift: a provider
is a library running in this process, while an MCP entry's command or
URL reaches whatever the machine can.
"""

from vinga_server.config.models import McpServerConfig, ProviderConfig


class EgressRefusal(Exception):
    """One entry the egress rule refuses, carrying the finished
    sentence. The wording belongs to this module and the exception type
    to whichever surface asked, which is why the callers re-raise rather
    than let this out."""


# Tells a class that declared `egress = None` from one that declared
# nothing: None is a marking here, and the difference between the two is
# the whole point of the check.
_UNDECLARED = object()


def check_provider(
    label: str, config: ProviderConfig, provider: object, local_only: bool
) -> None:
    """Enforce the egress rules for one built provider (#30).

    The class-level marking is authoritative; the configuration's
    `egress` key exists only for types that cannot know their own (an
    openai_compatible base_url decides), and declaring it on a type that
    knows is refused in any mode.
    """
    marked = _marking(label, config, provider)
    if marked is not None and config.egress is not None:
        raise EgressRefusal(
            f'{label}: "egress" is decided by type "{config.type}" and cannot '
            f"be declared in the configuration; remove the key"
        )
    if not local_only:
        return
    egress = marked if marked is not None else config.egress
    if egress is None:
        raise EgressRefusal(
            f'{label}: server.local_only is on, and whether type "{config.type}" '
            f"sends session data off this host depends on its base_url; declare "
            f'"egress: false" on this entry to assert the endpoint stays local'
        )
    if egress:
        raise EgressRefusal(
            f'{label}: server.local_only is on, but type "{config.type}" sends '
            f"session data off this host"
        )


def _marking(label: str, config: ProviderConfig, provider: object) -> bool | None:
    """What the built provider's own class declares, refused if it
    declared nothing or declared something that is not a marking (#136).

    Read out of the concrete class's namespace rather than through
    `getattr`, which walks the MRO: a subclass that declares nothing
    would otherwise ride its parent's marking, which is the silent
    default again one level down. Validated by identity rather than
    truthiness, so `egress = 0` fails the build instead of passing for
    local.

    Both refusals fire whatever the mode, because neither is a
    deployment's choice to make: a type that cannot answer the question
    is a hole in the guarantee wherever it runs, and the operator who
    meets it cannot fix it in their configuration. The class name is a
    code identifier rather than a configured value, so naming it keeps
    the message value-free while pointing at the file to edit.
    """
    kind = type(provider)
    marking = vars(kind).get("egress", _UNDECLARED)
    if marking is _UNDECLARED:
        raise EgressRefusal(
            f'{label}: type "{config.type}" builds {kind.__name__}, which declares '
            f'no "egress" of its own; every provider class states whether it sends '
            f"session data off this host"
        )
    if not (marking is True or marking is False or marking is None):
        raise EgressRefusal(
            f'{label}: type "{config.type}" builds {kind.__name__}, whose "egress" '
            f"is none of true, false or null; correct the declaration on the class"
        )
    return marking


def check_mcp_server(label: str, entry: McpServerConfig) -> None:
    """Enforce server.local_only for one referenced MCP server (#30).

    Tool arguments carry conversation-derived data, and no transport
    knows its own egress (a stdio command may proxy anywhere, a url may
    name localhost), so unlike providers there is nothing class-level to
    consult: every referenced entry needs the operator's declaration.

    Called only when local_only is on, which is where the caller's guard
    stays: an entry that declares nothing is an ordinary entry in every
    other mode.

    Handed the label rather than the name, exactly as `check_provider`
    above is, and for the reason that made this the odd one of the two:
    it joined `mcp_servers.` to the name itself, which is one location
    spelled twice inside one build, since the caller composes the same
    string for the refusal it raises beside this one. The caller
    composes it once now, through `entity_location`, the one home for
    where an entry is written (#420).
    """
    if entry.egress is False:
        return
    if entry.egress is None:
        raise EgressRefusal(
            f"{label}: server.local_only is on, and whether an MCP "
            f"server sends session data off this network cannot be known from "
            f'its transport; declare "egress: false" on this entry to assert '
            f"that whatever its command or URL reaches stays local"
        )
    raise EgressRefusal(
        f"{label}: server.local_only is on, but this entry declares "
        f"that it sends session data off this network"
    )
