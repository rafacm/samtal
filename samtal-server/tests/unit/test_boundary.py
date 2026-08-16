"""The device-facing boundary as a contract.

Two protocols and one opaque handle. What is worth testing about them is
what the rest of the codebase relies on and a refactor could quietly
take away: that conformance is checkable at runtime (which is what lets
the wiring tests assert a real session is a real `DeviceOutput`), and
that a batch of playable audio behaves the way the send path uses it.
"""

from typing import Any

import pytest

from samtal_server.device.boundary import (
    PIPELINE_SAMPLE_RATE,
    DeviceGone,
    DeviceOutput,
    PlayableAudio,
    SessionInput,
)
from tests.support.configs import DEVICE_MAC, config_with_agent
from tests.support.sessions import (
    device_session,
)


@pytest.mark.parametrize("protocol", [SessionInput, DeviceOutput])
def test_the_protocols_are_runtime_checkable(protocol: Any) -> None:
    """`isinstance` against them has to work, because that is how the
    wiring asserts a session and a runtime really implement the
    boundary rather than merely being passed where one is expected."""
    assert isinstance(object(), protocol) is False


def test_a_device_gone_is_a_runtime_error() -> None:
    """Deliberate, and load-bearing: every site that swallows a vanished
    device catches RuntimeError broadly already, so translating the
    transport's disconnect into this wraps what they catch instead of
    narrowing it."""
    assert issubclass(DeviceGone, RuntimeError)


def test_an_empty_batch_is_falsy_and_a_full_one_is_not() -> None:
    """The one thing a runtime may ask of a batch: was there anything to
    play? The filler arbitration turns on the answer."""
    assert not PlayableAudio()
    assert not PlayableAudio([])
    assert PlayableAudio([b"one"])


def test_batches_concatenate_in_order() -> None:
    """The other thing a runtime may do: the filler builds its clip, its
    resampler tail and its encoder flush into one batch before it sends
    anything."""
    batch = PlayableAudio([b"a"]) + PlayableAudio([b"b", b"c"])
    assert batch.packets == (b"a", b"b", b"c")
    assert len(batch) == 3


def test_a_batch_is_not_a_queue() -> None:
    """Concatenation answers a new batch and leaves both operands alone,
    so a batch the filler holds can never pick up packets the reply fed
    the shared encoder in between."""
    filler = PlayableAudio([b"clip"])
    reply = PlayableAudio([b"sentence"])
    filler + reply
    assert filler.packets == (b"clip",)
    assert reply.packets == (b"sentence",)


def test_the_pipeline_rate_is_the_rate_devices_send() -> None:
    assert PIPELINE_SAMPLE_RATE == 16000


def test_a_device_session_is_a_device_output() -> None:
    """Conformance, asserted rather than assumed: the edge is what the
    runtime is handed, and a method missing from it would otherwise
    surface as an AttributeError mid-conversation."""
    session = device_session(config_with_agent(), DEVICE_MAC)
    assert isinstance(session, DeviceOutput)


def test_the_bespoke_runtime_is_a_session_input() -> None:
    """The other half: what the factory builds is what the edge feeds."""
    session = device_session(config_with_agent(), DEVICE_MAC)
    assert isinstance(session.runtime, SessionInput)
