"""Text nobody vouched for, on its way to a surface a command keeps.

Two kinds of string reach these commands from outside: a URL an operator
typed, which is where a credential gets pasted by mistake, and a string
some far side returned, which is whatever answered at an address.
Neither has been vouched for by anything, and both end up on a terminal,
in a shell history and in whatever collects stderr.

One door each. `parsed_url` splits a URL with the parser's own failures
kept inside the boundary, since both of them put the text they refused
into the exception. `shown_url` is the only way a parsed address may be
displayed: the host rebuilt from the parsed parts, the userinfo gone and
the secret-shaped query parameters taken out, so a refusal that names an
address is never the thing that publishes its credential. `printable`
bounds far-side text and replaces what a terminal would obey.

The display door is one function rather than strip primitives a caller
has to order correctly, which is what makes this the rule's home instead
of a toolbox beside it. What counts as a credential in a query is not
restated here either: `config/models.py` already owns that rule for the
configuration's own URL-shaped values (`is_url_credential_parameter`,
which `url_credential` and `without_url_credential` read too), and
`shown_url` filters through the same predicate.

Two callers, `config/cli.py` and `doctor.py`, and neither may import the
other: the doctor must not pull the config CLI's machinery, and the
config CLI reading URL hygiene out of the doctor would be backwards. So
this lives beside the failure type `parsed_url` raises and the predicate
it reuses, and stays light enough for a top-level command to import.
"""

from urllib.parse import SplitResult, parse_qsl, urlencode, urlsplit, urlunsplit

from vinga_server.config.loader import ConfigError
from vinga_server.config.models import is_url_credential_parameter

# How much of anything that arrived in a response may be repeated back.
# What a command reaches may be a proxy, a captive portal or anything
# else that answers, so the version it claims and the URL it names are
# attacker-controlled text: bounded and printable, or not printed. The
# rule is the one `onboarding.pending._fact` applies to what a device
# says about itself; the bound is its own, because what a stranger's
# server claims about itself is not what a board waiting to be claimed
# says, and the two have never had a reason to move together. The body
# itself is never repeated at all, bounded or otherwise.
GLIMPSE_LENGTH = 120


def parsed_url(url: str, source: str) -> SplitResult:
    """The URL, split, with the parser's own failures kept inside the
    boundary.

    `urlsplit` raises on a malformed IPv6 literal and `.port` raises on
    a port that is not a number, and both put the text they refused into
    the exception. Outside a handler that is a traceback out of main()
    with the address in it; inside one it is a fixed sentence. The
    address is not quoted even here: what a mistyped URL holds is
    whatever was being typed, and the one thing an operator is typing
    around these commands is a credential.

    Both are provoked deliberately rather than trusted to happen later:
    `.port` is read here so that its refusal belongs to this function
    rather than to whichever caller touches it first.
    """
    problem: str | None = None
    try:
        parsed = urlsplit(url)
        # Read rather than trusted to be read later: `.port` parses on
        # access, so this is where its refusal belongs rather than in
        # whichever caller touches it first.
        _ = parsed.port
        return parsed
    except ValueError:
        problem = (
            f"{source} is not a URL this client can read. It has to be an http:// or "
            f"https:// address with a host, and a port if it names one has to be a "
            f"number. It is not quoted back, because a mistyped address holds whatever "
            f"was being typed."
        )
    raise ConfigError(problem)


def shown_url(parsed: SplitResult) -> str:
    """The URL as it may be printed: its credentials taken out.

    A credential written into a URL is refused, and the refusal must not
    be the thing that publishes it. Both places one can be written are
    taken out, because both are places one really is written: the
    userinfo in front of the host, and a secret-shaped query parameter,
    which is the other form vendors accept. Which parameter names are
    secret-shaped is not decided here: it is the rule `config/models.py`
    already applies to the configuration's own URL-shaped values, read
    through the same predicate rather than restated.

    The host is rebuilt from the parsed parts rather than kept as
    written, which is what takes the userinfo off, and the fragment is
    dropped with it.
    """
    host = parsed.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    if parsed.port:
        host = f"{host}:{parsed.port}"
    kept = [
        (key, held)
        for key, held in parse_qsl(parsed.query, keep_blank_values=True)
        if not is_url_credential_parameter(key)
    ]
    return urlunsplit((parsed.scheme, host, parsed.path, urlencode(kept), ""))


def printable(value: str, limit: int | None = GLIMPSE_LENGTH) -> str:
    """Text that arrived in a response, bounded before it is printed.

    Truncated first and then made printable, so no answer can choose how
    long a command's output is or put a newline, an escape sequence or a
    terminal control code into it. Unprintable characters become a
    question mark rather than disappearing, because something that
    arrived mangled should read as mangled.

    `None` is no bound, and it is a different rule rather than a bigger
    number. The bound is right for a value quoted inside a sentence,
    where the sentence is what the reader came for; it is wrong for a
    value that IS what the reader came for, because a renderer that
    quietly cut one would make it lie about the one thing it exists to
    show. `config/cli.py` draws that line twice: `_block` prints a
    prompt whole with a rule of its own, since a prompt is written in
    newlines, and the onboarding URL is printed whole through here,
    since a URL that reaches a terminal with a newline in it is a URL on
    two lines. Nothing an answer carries steers a terminal either way,
    which is the half of this function that has no exceptions.
    """
    return "".join(
        character if character.isprintable() else "?" for character in value.strip()[:limit]
    )


__all__ = ["GLIMPSE_LENGTH", "parsed_url", "printable", "shown_url"]
