"""The plumbing every provider that reaches a network needs.

Five provider types make requests, and each of them used to answer the
same handful of questions in its own module: which credential to send,
how long to wait, how many attempts a failure is worth, how to hand a
stream of bytes on sample-aligned, and how a failed request should
look to the caller. Three of them imported the credential resolver from
the Anthropic provider, which made whichever provider happened to be
written first the home of a decision belonging to none of them, and the
timeout default was declared three times over (#137).

The taxonomy those providers raise is deliberately not here.
`ProviderCallError` and `ProviderCallTimeout` are contract rather than
plumbing: the pipeline classifies by them, so they live with the rest
of the provider contract in `base.py`. What this module adds is the one
place that turns an SDK's exception into one of them.
"""

import logging
import os
from collections.abc import AsyncIterator

import anthropic
import httpx
import openai

from samtal_server.config.secrets import stored_provider_secret
from samtal_server.providers.base import (
    ProviderCallError,
    ProviderCallTimeout,
    ProviderError,
)

logger = logging.getLogger(__name__)

# The credential slot every provider type here fills, and the name a
# stored secret is written under. The seam is `<slot>_env` in the
# configuration and `<slot>` in the store, which is what lets one
# resolver serve both.
API_KEY_SLOT = "api_key"

# Long enough for a slow answer on a long utterance or a slow first
# byte, short enough that a hung request does not hold a turn open for
# the whole conversation. It is a per-operation transport timeout rather
# than a wall-clock deadline for the whole call, and it is a real bound
# only because MAX_RETRIES is what it is.
DEFAULT_TIMEOUT_S = 30.0

# Spoken replies are short; this caps runaways, not conversation.
DEFAULT_MAX_TOKENS = 1024

# The SDKs retry twice by default, which would make a timeout a third of
# the truth: three attempts plus backoff, all of it inside one stage of
# a turn the user is waiting through in silence. A voice turn has no use
# for that. A request that fails should fail now, so the session can log
# it and the conversation moves on, rather than the user waiting a
# minute and a half for a result nobody wants any more. The ElevenLabs
# provider speaks raw httpx and has never retried, so this is also what
# makes every network provider here behave alike.
MAX_RETRIES = 0

# What a failed request arrives as, one tuple per SDK a provider can be
# holding. Both SDKs ride httpx and let a raw transport error through
# from a response iterator once the response has opened, and the
# ElevenLabs provider speaks httpx directly, so the httpx families are
# in every one of them; the vendor families are not shared. A provider
# that also caught the other vendor's errors would dress a miswired
# client (an Anthropic entry somehow holding an OpenAI one) as an
# ordinary failed request, and that is a bug this process should show
# rather than absorb.
#
# Deliberately narrow for the same reason: `CancelledError` is outside
# them (it is not even an Exception), and so is every genuine bug,
# because the taxonomy claims request failures rather than all failures.
HTTPX_FAILURES: tuple[type[Exception], ...] = (httpx.HTTPError,)
ANTHROPIC_FAILURES: tuple[type[Exception], ...] = (anthropic.APIError, *HTTPX_FAILURES)
OPENAI_FAILURES: tuple[type[Exception], ...] = (openai.APIError, *HTTPX_FAILURES)

# Which of the caught failures is a wait rather than an answer. One
# tuple for all of them, since classification only ever sees what a
# provider's own catch admitted. `TimeoutError` covers
# `asyncio.TimeoutError`, for the provider that runs a deadline of its
# own around a request.
REQUEST_TIMEOUTS: tuple[type[BaseException], ...] = (
    anthropic.APITimeoutError,
    openai.APITimeoutError,
    httpx.TimeoutException,
    TimeoutError,
)


def call_failure(label: str, exc: BaseException) -> ProviderCallError:
    """A failed request as the taxonomy error the pipeline classifies by,
    carrying trusted metadata and nothing else.

    Three facts go into the message: which provider was making the
    request, the SDK exception's class name, and the HTTP status code
    when the failure had one. Not the vendor's own message text, and not
    the vendor's response body, which is the whole point: an SDK
    exception's string can embed the body verbatim, a compatible
    endpoint is free to echo request content or a credential back in it,
    and the session renders `str(exc)` into the log line the
    observability ADR makes the retained surface. What the operator
    loses (the vendor's prose) they recover by re-running the request by
    hand; what the logs keep is the diagnosable part.

    The caller raises the result `from None` for the same reason: the
    SDK exception must not ride into a rendered exception chain
    either."""
    kind = ProviderCallTimeout if isinstance(exc, REQUEST_TIMEOUTS) else ProviderCallError
    status = _status_code(exc)
    if status is None:
        return kind(f"{label}: the request failed with {type(exc).__name__}")
    return kind(f"{label}: the request failed with HTTP {status} ({type(exc).__name__})")


def _status_code(exc: BaseException) -> int | None:
    """The HTTP status a failure carries, when it carries one. The SDKs
    put it on the exception (`APIStatusError`) and httpx puts it on the
    response it attached (`HTTPStatusError`); a connection that never
    got an answer has neither. Read defensively and typed-checked,
    because everything that leaves here goes into a log line."""
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    return status if isinstance(status, int) else None


def resolve_api_key(label: str, api_key_env: str | None) -> str | None:
    """The credential for the `api_key` slot of the provider being
    built, or None to leave resolution to the SDK.

    Two sources, in one place, because a provider must not care which
    one a deployment used: a secret stored in the configuration database
    for this entry's `api_key` slot, or the environment variable an
    `api_key_env` reference names. A named but unset variable fails the
    build, because at request time it would fail every conversation.

    Ciphertext wins, and the reference it shadows is not read at all:
    set-secret is the later and more deliberate act, and an unset
    variable left behind it must not fail the boot the stored secret was
    set to fix. The value goes straight into the client here and lands
    on no model on the way.

    The refusal names the entry and not the reference. `api_key_env` is
    operator input, and the mistake it is easiest to make with a field
    named for a variable is to put the credential in it; this sentence
    is printed to stderr by `main` and rendered into the logs, and the
    entry name is enough to find the line in a configuration file the
    operator wrote."""
    stored = stored_provider_secret(API_KEY_SLOT)
    if stored is not None:
        return stored
    if api_key_env is None:
        return None
    api_key = os.environ.get(api_key_env, "")
    if not api_key:
        raise ProviderError(f"{label}: api_key_env references an unset environment variable")
    return api_key


async def aligned_pcm(label: str, chunks: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    """One TTS provider's byte stream, yielded sample-aligned.

    HTTP chunk boundaries fall wherever the network puts them, so a
    response chunk can end on the first byte of a sample; the odd byte
    is carried into the next chunk rather than passed on, because
    everything downstream counts samples in pairs and would shift the
    rest of the reply by one byte.

    `label` names the provider in the warning a truncated stream
    produces, since this runs on behalf of whichever one is speaking."""
    remainder = b""
    async for chunk in chunks:
        chunk = remainder + chunk
        aligned = len(chunk) - len(chunk) % 2
        remainder = chunk[aligned:]
        if aligned:
            yield chunk[:aligned]
    if remainder:
        logger.warning(
            "%s: dropping %d trailing byte of an incomplete sample", label, len(remainder)
        )
