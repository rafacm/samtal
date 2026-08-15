"""What a failure inside a reply is taken for.

The reply body's outer catch reads the same moment in one of two ways: the
device went away, which is ordinary and stays silent, or something here is
broken, which is a bug and belongs in the log with its traceback. Issue #137
separated them. The device edge translates both of the transport's
vanished-device shapes into `DeviceGone`, so the body can catch that type
alone, and everything else, a provider's request failure included, reaches the
reporting arm instead of being read as a disconnect.

Two halves, per the plan. The first pins that a provider call failing mid-reply
is reported as a provider failure and never swallowed. The second is the half
that changed: a bare `RuntimeError` raised while speaking is now a bug on the
record, while a `DeviceGone` raised in the same place is still nothing at all.
"""

from typing import Any, cast

import pytest

from samtal_server.device.boundary import DeviceGone
from samtal_server.providers import ProviderCallError, ProviderCallTimeout
from tests.unit.test_session_events import reply_with
from tests.unit.test_session_tools import (
    POET_MAC,
    base_config,
    drive_reply,
    session_for,
)

# One frame of silence, which the mock ASR answers with "hello" whatever it
# holds: these tests are about how the reply ends, not what was said.
UTTERANCE = b"\x00\x00" * 320


class QuietSocket:
    """Enough websocket for a whole reply to run against. Everything sent
    goes nowhere and nothing fails, so the only failure in the run is the
    one the test raises."""

    async def send_text(self, text: str) -> None:
        return None

    async def send_bytes(self, data: bytes) -> None:
        return None

    async def close(self, code: int, reason: str) -> None:
        return None


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
    """Classification is by type now, not by class name. The taxonomy's
    timeout is a `TimeoutError`, which is the whole reason it inherits
    one, and the sentence says the symptom was a wait."""
    failed = await reply_with("tts", ProviderCallTimeout("elevenlabs timed out"), caplog)
    assert failed.error == "ProviderCallTimeout"
    assert "timed out" in failed.getMessage()


async def test_a_bug_while_speaking_is_reported_rather_than_swallowed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The catch half, and the one that changed. A bare `RuntimeError`
    reaching the reply body can only be a local bug now that the edge
    translates a vanished device, so it lands under "reply failed" with
    its traceback instead of returning silently."""
    await reply_broken_while_speaking(RuntimeError("the encoder is wedged"), caplog)
    assert "reply failed" in caplog.text
    assert "the encoder is wedged" in caplog.text


async def test_a_vanished_device_while_speaking_still_says_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The other side of the same catch. A device that goes away is not
    a failure of anything, and an operator reading "reply failed" with a
    traceback would go looking for a bug that is not there."""
    await reply_broken_while_speaking(DeviceGone("the device disconnected"), caplog)
    assert "reply failed" not in caplog.text
