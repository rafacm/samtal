"""The device token scheme.

A token is what stands between the conversation socket and the open
internet, so the cases that matter here are the ones an attacker would
try: a token for another device, a token whose signature was edited, an
expired one, and every shape of garbage. `verify` answers all of them
with False and never with an exception, because a caller that has to
catch something will one day forget to.
"""

import time

import pytest

from vinga_server.auth import DeviceAuth

CLIENT = "6f1a2b3c-4d5e-6f70-8192-a3b4c5d6e7f8"
DEVICE = "aa:bb:cc:dd:ee:ff"
SECRET = "d3c2b1a0" * 8


@pytest.fixture
def auth() -> DeviceAuth:
    return DeviceAuth(SECRET, expire_s=2592000)


def test_an_issued_token_verifies_for_the_device_it_was_issued_to(auth: DeviceAuth) -> None:
    assert auth.verify(auth.issue(CLIENT, DEVICE), CLIENT, DEVICE)


def test_a_token_is_a_signature_and_a_timestamp(auth: DeviceAuth) -> None:
    signature, _, issued = auth.issue(CLIENT, DEVICE).partition(".")
    assert abs(int(issued) - int(time.time())) <= 2
    # Unpadded urlsafe base64 of a SHA-256 digest: 43 characters, and
    # nothing in it needs escaping in a header or in the device's NVS.
    assert len(signature) == 43
    assert set(signature) <= set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    )


def test_a_token_does_not_work_for_another_device(auth: DeviceAuth) -> None:
    token = auth.issue(CLIENT, DEVICE)
    assert not auth.verify(token, CLIENT, "11:22:33:44:55:66")


def test_a_token_does_not_work_for_another_client(auth: DeviceAuth) -> None:
    token = auth.issue(CLIENT, DEVICE)
    assert not auth.verify(token, "00000000-0000-0000-0000-000000000000", DEVICE)


def test_a_token_from_another_server_does_not_verify(auth: DeviceAuth) -> None:
    other = DeviceAuth("a different secret entirely", expire_s=2592000)
    assert not auth.verify(other.issue(CLIENT, DEVICE), CLIENT, DEVICE)


def test_an_edited_signature_does_not_verify(auth: DeviceAuth) -> None:
    signature, _, issued = auth.issue(CLIENT, DEVICE).partition(".")
    edited = ("B" if signature[0] != "B" else "C") + signature[1:]
    assert not auth.verify(f"{edited}.{issued}", CLIENT, DEVICE)


def test_a_moved_timestamp_does_not_verify(auth: DeviceAuth) -> None:
    """The timestamp is signed, so extending a token's life by editing
    it invalidates it instead."""
    signature, _, issued = auth.issue(CLIENT, DEVICE).partition(".")
    assert not auth.verify(f"{signature}.{int(issued) + 1}", CLIENT, DEVICE)


def test_a_token_past_its_expiry_does_not_verify() -> None:
    auth = DeviceAuth(SECRET, expire_s=60)
    assert auth.verify(auth.issue(CLIENT, DEVICE), CLIENT, DEVICE)
    # White-box for the three signatures in this file. What is under
    # test is a boundary in time: a token one second past its expiry,
    # one second inside it, and one whose timestamp was tampered with.
    # The issuer stamps "now" and offers no way to say when, so the
    # public route to a token a minute old is a test that sleeps for a
    # minute, and the boundary itself would still be untestable.
    old = int(time.time()) - 61
    assert not auth.verify(f"{auth._sign(CLIENT, DEVICE, old)}.{old}", CLIENT, DEVICE)


def test_the_expiry_boundary_is_inclusive() -> None:
    auth = DeviceAuth(SECRET, expire_s=60)
    exactly = int(time.time()) - 60
    assert auth.verify(f"{auth._sign(CLIENT, DEVICE, exactly)}.{exactly}", CLIENT, DEVICE)


def test_a_token_from_the_future_does_not_verify(auth: DeviceAuth) -> None:
    """A clock skew big enough to issue ahead of us is a skew big enough
    that the expiry means nothing."""
    ahead = int(time.time()) + 3600
    assert not auth.verify(f"{auth._sign(CLIENT, DEVICE, ahead)}.{ahead}", CLIENT, DEVICE)


@pytest.mark.parametrize(
    "token",
    [
        "",
        ".",
        "a",
        "a.b",
        "a.b.c",
        ".1700000000",
        "signature.",
        "signature.not-a-number",
        "signature.1.7e9",
        "signature.-1700000000",
        " ",
        "Bearer something",
    ],
)
def test_malformed_tokens_are_false_and_never_an_exception(
    auth: DeviceAuth, token: str
) -> None:
    assert auth.verify(token, CLIENT, DEVICE) is False


def test_two_tokens_for_the_same_device_both_verify(auth: DeviceAuth) -> None:
    """The firmware re-checks OTA on every boot and stores whatever it
    is handed, so the previous token staying valid is what keeps a
    reboot from being a lockout."""
    first = auth.issue(CLIENT, DEVICE)
    second = auth.issue(CLIENT, DEVICE)
    assert auth.verify(first, CLIENT, DEVICE)
    assert auth.verify(second, CLIENT, DEVICE)
