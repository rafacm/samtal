"""What a failure inside a reply is taken for.

The reply body's outer catch reads the same moment in one of two ways: the
device went away, which is ordinary and stays silent, or something here is
broken, which is a bug and belongs on the record. Issue #137 separated them.
The device edge translates both of the transport's vanished-device shapes into
`DeviceGone`, so the body can catch that type alone, and everything else, a
provider's request failure included, reaches the reporting arm instead of being
read as a disconnect.

Two halves, per the plan. The first pins that a provider call failing mid-reply
is reported as a provider failure and never swallowed. The second is the half
that changed: a bare `RuntimeError` raised while speaking is now a bug on the
record, while a `DeviceGone` raised in the same place is still nothing at all.

What that record may say is the third thing pinned here. The arm that reports
now catches everything a provider raises, so it names the exception's class and
stops there: no traceback and no message text, neither of which is ours to
trust once it has been anywhere near a response body.
"""

import logging
from typing import Any, cast

import pytest

from samtal_server.device.boundary import DeviceGone
from samtal_server.logs import TEXT_FORMAT, JsonFormatter
from samtal_server.providers import ProviderCallError, ProviderCallTimeout
from tests.support.configs import POET_MAC, base_config
from tests.support.sessions import drive_reply, reply_with, session_for
from tests.support.sockets import QuietSocket

# One frame of silence, which the mock ASR answers with "hello" whatever it
# holds: these tests are about how the reply ends, not what was said.
UTTERANCE = b"\x00\x00" * 320

# Planted where a real secret plausibly ends up: in the message of a failure
# that came back from a network, and in the message of the failure behind it.
SENTINEL = "sk-test-1d0c7a6b-never-a-real-credential"


def reply_failure(caplog: pytest.LogCaptureFixture) -> logging.LogRecord:
    """The one record the generic arm emitted."""
    matching = [record for record in caplog.records if "reply failed" in record.getMessage()]
    assert len(matching) == 1, f"expected one reply failure, got {len(matching)}"
    return matching[0]


def rendered(record: logging.LogRecord) -> str:
    """One record through both formats a deployment can be running, since
    what a secret must stay out of is whatever is written down. The JSON
    one is the container default and the retained surface; the text one
    is what a terminal shows."""
    return JsonFormatter().format(record) + logging.Formatter(TEXT_FORMAT).format(record)


def a_bug_carrying_a_secret() -> RuntimeError:
    """A local failure with the sentinel in its own message and another
    copy in the failure behind it, which is the shape a wrapped vendor
    error has: `raise` inside an `except` leaves the first reachable
    from the second."""
    behind = ValueError(f"the endpoint answered {SENTINEL}")
    bug = RuntimeError(f"the encoder is wedged on {SENTINEL}")
    bug.__cause__ = behind
    return bug


async def reply_broken_while_speaking(
    exc: BaseException, caplog: pytest.LogCaptureFixture
) -> None:
    """One whole reply whose speaking step raises `exc`.

    Speaking is where a local bug in a reply actually lives (a resampler,
    an encoder, a framing helper), it sits inside the reply body's `try`,
    and it is past every provider, so nothing reports the failure before
    the outer catch decides what to make of it."""
    session = session_for(base_config(), POET_MAC, websocket=cast(Any, QuietSocket()))

    async def speak(synthesis: Any, resampler: Any, into: list[str]) -> None:
        synthesis.cancel()
        await synthesis.wait_cancelled()
        raise exc

    session.runtime._speak = speak  # type: ignore[method-assign]
    with caplog.at_level("INFO"):
        await drive_reply(session, UTTERANCE)


async def test_a_provider_call_error_from_the_tts_is_reported_as_one(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The taxonomy half. A provider's request failure is not a
    `RuntimeError` and never was a vanished device; it reaches the
    operator as the provider failure it is, with the stage and the
    taxonomy class in the record."""
    failed = await reply_with("tts", ProviderCallError("elevenlabs request failed"), caplog)
    assert failed.stage == "tts"
    assert failed.error == "ProviderCallError"
    assert "failed" in failed.getMessage()
    # And it was not swallowed on the way out: the reply ended in the
    # arm that reports, not in the one that returns.
    assert "reply failed" in caplog.text


async def test_a_provider_call_timeout_is_worded_as_a_wait(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The taxonomy's timeout is a `TimeoutError`, which is the whole
    reason it inherits one, and the sentence says the symptom was a
    wait."""
    failed = await reply_with("tts", ProviderCallTimeout("elevenlabs timed out"), caplog)
    assert failed.error == "ProviderCallTimeout"
    assert "timed out" in failed.getMessage()


class ApiTimeoutError(Exception):
    """A name that says timeout, on a class that is not one. The shape
    is real: `openai.APITimeoutError` is an `APIConnectionError`, and
    `httpx.TimeoutException` inherits from neither `TimeoutError` nor
    that."""


class DeadlineExceeded(TimeoutError):
    """The other way round: a wait whose name says nothing of the kind.
    `FirstTokenTimeout` happens to be named for what it is, but nothing
    makes that a rule."""


async def test_the_wording_follows_the_type_and_not_the_class_name(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """What the two tests above cannot show, because both taxonomy
    classes are named after what they are: the sentence is chosen by
    `isinstance` and the deleted substring match would disagree with
    it on both of these.

    A vendor class named for a timeout that is not one now reads
    "failed". Nothing regresses by it, because no provider hands the
    pipeline an SDK exception any more: the five wrap their SDKs'
    timeouts into `ProviderCallTimeout`, which is a `TimeoutError` by
    inheritance rather than by spelling."""
    named = await reply_with("tts", ApiTimeoutError("the name says timeout"), caplog)
    assert named.error == "ApiTimeoutError"
    assert "failed" in named.getMessage()
    assert "timed out" not in named.getMessage()

    caplog.clear()
    typed = await reply_with("tts", DeadlineExceeded("the type says timeout"), caplog)
    assert typed.error == "DeadlineExceeded"
    assert "timed out" in typed.getMessage()


async def test_a_bug_while_speaking_is_reported_rather_than_swallowed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The catch half, and the one that changed. A bare `RuntimeError`
    reaching the reply body can only be a local bug now that the edge
    translates a vanished device, so it lands under "reply failed"
    instead of returning silently."""
    await reply_broken_while_speaking(RuntimeError("the encoder is wedged"), caplog)
    assert reply_failure(caplog).getMessage().endswith("reply failed: RuntimeError")


async def test_a_reported_failure_says_the_class_and_nothing_else(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The report is the class name. Everything else a failure carries
    reached this arm from somewhere, and since the catch narrowed, that
    somewhere includes every provider on the far side of a network: a
    message that quotes a response body, and a chain of causes behind
    it that a traceback would print in full. Neither is written down,
    in either format."""
    await reply_broken_while_speaking(a_bug_carrying_a_secret(), caplog)

    failed = reply_failure(caplog)
    assert failed.getMessage().endswith("reply failed: RuntimeError")
    assert failed.exc_info is None
    assert SENTINEL not in caplog.text
    assert all(SENTINEL not in str(record.__dict__) for record in caplog.records)
    assert all(SENTINEL not in rendered(record) for record in caplog.records)


async def test_a_vanished_device_while_speaking_still_says_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The other side of the same catch. A device that goes away is not
    a failure of anything, and an operator reading "reply failed" with a
    traceback would go looking for a bug that is not there."""
    await reply_broken_while_speaking(DeviceGone("the device disconnected"), caplog)
    assert "reply failed" not in caplog.text
