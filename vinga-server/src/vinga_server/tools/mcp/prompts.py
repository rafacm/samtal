"""What an MCP server ships about itself, captured under bounds.

The #122 guidance capture, whole: the `instructions` of a server's
initialize result and the prompts it publishes, walked, fetched,
rendered, bounded, and with this deployment's own materialized
values taken back out of them. A third party's words, so each is
captured only under a bound and none of it ever reaches a log.

Everything here takes the entry's name and its configuration as
arguments rather than reading a manager's fields: the manager
calls it at the points in its run where it always did, and what it
gets back is what to hold.
"""

import asyncio
import functools
import logging
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass

import mcp.types
from mcp import ClientSession

from vinga_server.config import McpServerConfig
from vinga_server.runtime.prompt import ServerPrompt

from .transport import CONNECT_TIMEOUT_S, _reason

logger = logging.getLogger(__package__)


# How long one prompt call may take: one page of `prompts/list`, or one
# `prompts/get`. Short, because this is optional guidance and every
# second of it is a second of the boot or of a reload.
PROMPT_CALL_TIMEOUT_S = 5.0

# And how long the whole discovery phase may take, listing and fetches
# together. Per-call bounds do not bound the phase: a server answering
# every page inside its bound, for ever, would hold a start open for as
# long as it cared to. Equal to the connect timeout, so a manager start
# is one connect timeout plus one of these plus small change, about 20 s,
# and a reload's envelope grows by the same one deadline and stays well
# inside the CLI's 60 s read timeout.
PROMPT_DISCOVERY_TIMEOUT_S = CONNECT_TIMEOUT_S

# How many pages of a prompt listing are walked. The backstop against a
# cursor that repeats itself, which no per-call bound and no aggregate
# deadline notices as anything other than a slow server.
PROMPT_PAGE_CAP = 20

# And how many listed prompts are looked at across the whole walk. The
# page cap bounds how many arrays arrive; nothing bounds how long one of
# them is, and a server that answers instantly with a million entries
# costs the loop that reads them rather than any of the timers.
PROMPT_LISTING_CAP = 2000

# How many messages one published prompt may render from. A prompt
# result is a third party's list too, and the size cap below cannot be
# reached without walking it.
PROMPT_MESSAGE_CAP = 200

# The size of a server-shipped block this server will inject, whichever
# channel it arrived on. A longer one is skipped whole rather than
# truncated: a truncated instruction block is half an instruction nobody
# reviewed, and an unbounded one is a third party filling the prompt
# budget an operator tunes.
SHIPPED_BLOCK_LIMIT = 4000

# What one of this deployment's own credentials becomes if a server
# writes it back into the guidance it ships.
REDACTED = "[redacted]"

# And how long a materialized value has to be before it is treated as
# one. Below this, an entry's env and headers hold things like a port, a
# locale or `true`, and replacing every occurrence of a three-character
# value would mangle the guidance without protecting anything: a secret
# that short is not one. Everything at or above it is replaced, secret
# or not, because whether a value is a credential is not knowable from
# here and a redacted setting costs an operator nothing.
REDACTION_FLOOR = 8

# The two channels a server ships guidance in, as the warnings name
# them.
INSTRUCTIONS_CHANNEL = "instructions"
PROMPT_CHANNEL = "prompt"

# Why a configured prompt is not injected, as fixed tokens this
# application owns. They stand where an exception type name stands in a
# connection's reason: the whole diagnosis in a line that must carry
# neither the server's bytes nor the operator's copy of its name.
NO_PROMPTS_CAPABILITY = "NoPromptsCapability"
NOT_LISTED = "NotListed"
REQUIRES_ARGUMENTS = "RequiresArguments"
NON_TEXT_CONTENT = "NonTextContent"
NOTHING_TO_INJECT = "NothingToInject"
DISCOVERY_DEADLINE = "DiscoveryDeadline"
PAGE_CAP = "PageCap"
LISTING_CAP = "ListingCap"
TOO_MANY_MESSAGES = "TooManyMessages"
TOO_LONG = "TooLong"


class _PromptsUnreadable(Exception):
    """Why a prompt call did not answer, as a token this application
    owns.

    Private, and it never leaves this module: it carries a reason the
    way `_reason` does, so that a discovery failure is logged as what
    kind of failure it was rather than as whatever the far side wrote.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# What `_redactor` answers with: text in, text out, and None passes
# through, because "the server shipped nothing" is a thing to say.
_Redactor = Callable[[str | None], str | None]


def _redactor(*groups: Mapping[str, str]) -> _Redactor:
    """A function that takes this deployment's own values back out of
    whatever a server hands it.

    The problem it answers is narrow and worth stating narrowly. An
    opted-in entry asks for a third party's words to reach a system
    prompt and a gated read, and that is the operator's decision; it is
    not a decision to let that server hand back the credential this
    deployment gave it, through a surface the rest of the API refuses to
    read a stored secret from. So the values that were materialized for
    this connection, and only those, are replaced.

    Longest first, so that a value which contains another is replaced
    whole rather than left holding a placeholder in the middle of it.
    """
    values = sorted(
        {
            value
            for group in groups
            for value in group.values()
            if len(value) >= REDACTION_FLOOR
        },
        key=len,
        reverse=True,
    )

    def redact(text: str | None) -> str | None:
        if text is None:
            return None
        for value in values:
            text = text.replace(value, REDACTED)
        return text

    return redact


@dataclass(frozen=True)
class _Rendering:
    """What one published prompt rendered to, or why it did not.

    `text` when there is a block to inject, and otherwise a `problem`
    token and the size the messages added up to, so the caller can say
    which rule refused it and how big it was without this function
    knowing anything about warnings.
    """

    text: str | None = None
    problem: str | None = None
    size: int = 0


def _rendered(fetched: mcp.types.GetPromptResult) -> _Rendering:
    """One published prompt as the block it is injected as, or None when
    it is not one.

    A prompt result is an ordered list of messages with roles and typed
    content, and what this feature injects is one block of standing
    guidance, so the rendering is defined rather than left to whatever
    the code happened to do: the text of each message in message order,
    joined by blank lines, roles dropped. A prompt that only makes sense
    as a dialogue to replay is a template this feature is not for.

    A message carrying anything but text makes the whole prompt
    unusable, the same visible rule as required arguments, rather than
    being rendered as the named placeholder a tool result gets: a tool
    result is spoken by an assistant that can say it got something it
    cannot use, and a system-prompt block has nobody to say that.

    Both bounds are applied before the join rather than after it. The
    message list is a third party's array, so it is refused past a fixed
    count; and the block's size is the sum of what the messages hold
    plus the separators between them, which is arithmetic over lengths
    the objects already carry, so a prompt that would be skipped for
    being oversized is skipped without a string of that size ever being
    built. Rendering first and measuring afterwards was the whole of the
    difference between a cap and an allocation a far side chooses.
    """
    messages = fetched.messages
    if len(messages) > PROMPT_MESSAGE_CAP:
        return _Rendering(problem=TOO_MANY_MESSAGES)
    size = 0
    for count, message in enumerate(messages, start=1):
        if not isinstance(message.content, mcp.types.TextContent):
            return _Rendering(problem=NON_TEXT_CONTENT)
        size += len(message.content.text) + (2 if count > 1 else 0)
    if size > SHIPPED_BLOCK_LIMIT:
        return _Rendering(problem=TOO_LONG, size=size)
    return _Rendering(
        text="\n\n".join(message.content.text for message in messages), size=size
    )


# Prompt discovery
#
# Everything below runs after the connect envelope has closed and
# the tools are published, so nothing in it can take a working
# server away. What it may do is take time, which is why it is
# bounded twice: once per call, and once over the phase.


async def _discovered(
    name: str,
    config: McpServerConfig,
    session: ClientSession,
    capabilities: mcp.types.ServerCapabilities,
    redact: _Redactor,
) -> tuple[ServerPrompt, ...]:
    """The prompts this entry named, fetched and rendered. Never
    raises, whatever the far side does or this code gets wrong: the
    caller is holding a published tool list, and optional guidance
    does not get to cost it."""
    try:
        return await _discover(name, config, session, capabilities, redact)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _skipped(name, _positions(config), _reason(exc))
        return ()


def _positions(config: McpServerConfig, first: int = 1) -> range:
    """The `inject_prompts` positions from one to the end, counted
    from one, which is how every line about them names them."""
    return range(first, len(config.inject_prompts or ()) + 1)


async def _discover(
    name: str,
    config: McpServerConfig,
    session: ClientSession,
    capabilities: mcp.types.ServerCapabilities,
    redact: _Redactor,
) -> tuple[ServerPrompt, ...]:
    """Listing first, then the fetches, under one deadline.

    The order is the whole design. Calling `prompts/get` and reading
    the failure would make every skip decision rest on interpreting
    an untrusted server's error, so the full listing is walked
    first and each configured name is judged against it: a name the
    listing does not carry, and a listed prompt that declares
    required arguments, are refused before anything is fetched. Only
    what the listing proves eligible is asked for, with no arguments.
    """
    wanted = list(config.inject_prompts or ())
    if not wanted:
        return ()
    if capabilities.prompts is None:
        # One line for the entry rather than one per name: the
        # server answered the handshake saying it publishes no
        # prompts at all, which is one fact about the entry.
        _skipped(name, _positions(config), NO_PROMPTS_CAPABILITY)
        return ()

    deadline = time.monotonic() + PROMPT_DISCOVERY_TIMEOUT_S
    try:
        listing = await _listing(session, deadline, frozenset(wanted))
    except _PromptsUnreadable as unreadable:
        # The listing is what every name is judged against, so a
        # listing this server could not read is every name skipped.
        _skipped(name, _positions(config), unreadable.reason)
        return ()

    captured: list[ServerPrompt] = []
    for position, listed_name in enumerate(wanted, start=1):
        listed = listing.get(listed_name)
        if listed is None:
            _skipped(name, [position], NOT_LISTED)
            continue
        if any(argument.required for argument in listed.arguments or ()):
            # A template cannot be rendered without the arguments it
            # declares, and this feature injects standing guidance
            # rather than filling templates in.
            _skipped(name, [position], REQUIRES_ARGUMENTS)
            continue
        try:
            fetched = await _bounded(
                functools.partial(session.get_prompt, listed_name), deadline
            )
        except _PromptsUnreadable as unreadable:
            if unreadable.reason == DISCOVERY_DEADLINE:
                _skipped(name, _positions(config, position), DISCOVERY_DEADLINE)
                break
            _skipped(name, [position], unreadable.reason)
            continue
        rendered = _rendered(fetched)
        if rendered.problem == TOO_LONG:
            _too_long(name, PROMPT_CHANNEL, rendered.size, position)
            continue
        if rendered.problem is not None:
            _skipped(name, [position], rendered.problem)
            continue
        text = _injectable(
            name, redact(rendered.text), PROMPT_CHANNEL, position
        )
        if text is None:
            continue
        # The configured name is redacted too. It is the operator's
        # own copy of a server-chosen string, echoed write-shaped by
        # the two surfaces that echo configuration, and a credential
        # pasted into it would ride out on both.
        captured.append(
            ServerPrompt(name, position, redact(listed_name) or listed_name, text)
        )
    return tuple(captured)


async def _listing(
    session: ClientSession, deadline: float, wanted: frozenset[str]
) -> dict[str, mcp.types.Prompt]:
    """Everything this server publishes under a name this entry
    named, walked cursor by cursor.

    Only the configured names are kept, since they are all that is
    judged and the listing itself is a third party's list of any
    length. The walk ends when the server says the listing has ended
    and not a page earlier: stopping as soon as every configured name
    had been seen would have been a fetch before the advertised
    listing was over, which is the one thing this order exists to
    prevent, and it would have made a name published twice under
    different arguments answer differently depending on where the
    first copy sat. A listing that has not ended by the page cap is a
    listing this server will not finish reading: a cursor that
    repeats itself looks like nothing else from here.

    The deadline is checked between pages as well as before each
    call, so a server that answers every page instantly and hands
    back a great many of them is bounded by the phase and not only
    by the cap.
    """
    listing: dict[str, mcp.types.Prompt] = {}
    seen = 0
    cursor: str | None = None
    for _page in range(PROMPT_PAGE_CAP):
        answered = await _bounded(
            functools.partial(session.list_prompts, cursor), deadline
        )
        for listed in answered.prompts:
            seen += 1
            if seen > PROMPT_LISTING_CAP:
                # A page is a third party's array and nothing bounds
                # its length, so the walk stops counting rather than
                # reading out an arbitrary one.
                raise _PromptsUnreadable(LISTING_CAP)
            if listed.name in wanted:
                listing.setdefault(listed.name, listed)
        cursor = answered.nextCursor
        if cursor is None:
            return listing
        if time.monotonic() >= deadline:
            raise _PromptsUnreadable(DISCOVERY_DEADLINE)
    raise _PromptsUnreadable(PAGE_CAP)


async def _bounded[T](call: Callable[[], Awaitable[T]], deadline: float) -> T:
    """One prompt call, inside both of the bounds that apply to it.

    The call is handed over unmade rather than as an awaitable, so
    that a phase which is already over creates no coroutine nobody
    awaits. Which bound expired decides what the caller does about
    it, so they are told apart: a call cut short by the phase's own
    deadline means everything remaining is over too, while one that
    used its whole per-call bound is this prompt's failure and
    nobody else's.
    """
    budget = min(PROMPT_CALL_TIMEOUT_S, deadline - time.monotonic())
    if budget <= 0:
        raise _PromptsUnreadable(DISCOVERY_DEADLINE)
    phase_bound = budget < PROMPT_CALL_TIMEOUT_S
    try:
        async with asyncio.timeout(budget):
            return await call()
    except asyncio.CancelledError:
        raise
    except TimeoutError:
        raise _PromptsUnreadable(
            DISCOVERY_DEADLINE if phase_bound else _reason(TimeoutError())
        ) from None
    except Exception as exc:
        # Chained from nothing, deliberately: the reason token is
        # what travels, and a far side's own words are not part of
        # what this server publishes about it.
        raise _PromptsUnreadable(_reason(exc)) from None


def _injectable(
    name: str, text: str | None, channel: str, position: int | None = None
) -> str | None:
    """One server-shipped block, or None when there is nothing to
    inject.

    The length is checked before anything walks the text, and the
    order is deliberate: `strip()` copies whatever it was given, so
    asking whether an oversized block is blank costs a second copy
    of a block this server has already decided not to keep. Over the
    cap is skipped whole rather than truncated, because half an
    instruction is an instruction nobody reviewed. Nothing at all
    and nothing but whitespace are then the same answer, since a
    heading with no words under it is not guidance.
    """
    if text is None:
        if position is not None:
            _skipped(name, [position], NOTHING_TO_INJECT)
        return None
    if len(text) > SHIPPED_BLOCK_LIMIT:
        _too_long(name, channel, len(text), position)
        return None
    if not text.strip():
        if position is not None:
            _skipped(name, [position], NOTHING_TO_INJECT)
        return None
    return text


def _too_long(name: str, channel: str, size: int, position: int | None) -> None:
    """Say that a block was past the cap, in sizes and positions.

    The entry, the channel and the size. Never the block: it is a
    third party's bytes, and this line goes to the retained logs,
    which keep metadata and nothing a far side wrote.
    """
    logger.warning(
        "mcp server %s: the %s block it shipped%s is %d characters, past the "
        "%d-character cap, so it is skipped whole rather than truncated",
        name,
        channel,
        "" if position is None else f" at inject_prompts position {position}",
        size,
        SHIPPED_BLOCK_LIMIT,
    )


def _skipped(name: str, positions: Iterable[int], rule: str) -> None:
    """Say what is not injected and why.

    The entry, the positions in `inject_prompts` counted from one,
    and the rule. Never the configured name: an MCP prompt name is a
    server-chosen identifier the operator copied, so nothing bounds
    what it holds, and this sentence lands in the JSON log this
    deployment collects.
    """
    listed = ", ".join(str(position) for position in positions)
    if not listed:
        return
    logger.warning(
        "mcp server %s: nothing is injected for inject_prompts position %s (%s)",
        name,
        listed,
        rule,
    )
