"""The stored-secret envelope: round trips, rotation, and every refusal.

The refusals are the point of most of this file. A secret that cannot
be read has to say which entity and slot it belongs to, so the operator
knows what to set again, and it has to say that without carrying the
value anywhere a log or a bug report would pick it up: not in the
message, and not in the exception chain either.
"""

import pytest
from cryptography.fernet import Fernet, MultiFernet

from samtal_server.config import ConfigError
from samtal_server.config.secrets import (
    MASK,
    MASTER_KEY_ENV,
    SecretLocation,
    decrypt,
    encrypt,
    generate_key,
    is_envelope,
    load_keys,
    mask,
)

# Not a real credential, and shaped so that a substring check for it in
# an error message or a traceback cannot match by accident.
SECRET = "sk-test-4f8b2c9e-never-a-real-credential"

CLAUDE = SecretLocation.provider("llm", "claude", "api_key")
WEATHER = SecretLocation.mcp_server("weather", "headers.Authorization")


def _chain(exc: BaseException) -> str:
    """Every exception behind the one that was raised, rendered the way
    a traceback or a logged exc_info would render it."""
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts += [repr(current), str(current)]
        current = current.__cause__ or current.__context__
    return "\n".join(parts)


def test_a_secret_round_trips_through_its_envelope() -> None:
    keys = MultiFernet([Fernet(generate_key())])

    envelope = encrypt(CLAUDE, SECRET, keys)

    assert is_envelope(envelope)
    assert SECRET not in envelope["enc"]
    assert decrypt(CLAUDE, envelope, keys) == SECRET


def test_the_newest_key_encrypts_and_an_old_key_still_decrypts() -> None:
    """Rotation, as far as this release goes: a new key is prepended and
    everything written under the old one keeps opening."""
    old, new = generate_key(), generate_key()
    before = MultiFernet([Fernet(old)])
    after = MultiFernet([Fernet(new), Fernet(old)])

    written_before = encrypt(CLAUDE, SECRET, before)
    written_after = encrypt(CLAUDE, SECRET, after)

    assert decrypt(CLAUDE, written_before, after) == SECRET
    # Written under the newest key alone, which is what makes re-running
    # set-secret the interim rewrite path.
    assert decrypt(CLAUDE, written_after, MultiFernet([Fernet(new)])) == SECRET
    with pytest.raises(ConfigError):
        decrypt(CLAUDE, written_after, MultiFernet([Fernet(old)]))


def test_a_wrong_key_is_refused_without_leaking_the_secret() -> None:
    written = encrypt(CLAUDE, SECRET, MultiFernet([Fernet(generate_key())]))

    with pytest.raises(ConfigError) as caught:
        decrypt(CLAUDE, written, MultiFernet([Fernet(generate_key())]))

    assert CLAUDE.describe() in str(caught.value)
    assert MASTER_KEY_ENV in str(caught.value)
    assert SECRET not in _chain(caught.value)
    assert caught.value.__cause__ is None


def test_a_missing_key_is_refused_naming_the_location() -> None:
    written = encrypt(CLAUDE, SECRET, MultiFernet([Fernet(generate_key())]))

    with pytest.raises(ConfigError) as caught:
        decrypt(CLAUDE, written, None)

    assert CLAUDE.describe() in str(caught.value)
    assert MASTER_KEY_ENV in str(caught.value)
    assert SECRET not in _chain(caught.value)


def test_storing_a_secret_without_a_key_is_refused() -> None:
    with pytest.raises(ConfigError) as caught:
        encrypt(CLAUDE, SECRET, None)

    assert CLAUDE.describe() in str(caught.value)
    assert SECRET not in _chain(caught.value)


def test_a_token_moved_to_another_slot_is_refused() -> None:
    """The location travels inside the ciphertext, so a valid token
    copied into another entity's row does not open there."""
    keys = MultiFernet([Fernet(generate_key())])
    written = encrypt(CLAUDE, SECRET, keys)

    with pytest.raises(ConfigError) as caught:
        decrypt(WEATHER, written, keys)

    message = str(caught.value)
    assert WEATHER.describe() in message
    assert CLAUDE.describe() in message
    assert SECRET not in _chain(caught.value)


def test_the_same_slot_on_another_entity_is_still_another_slot() -> None:
    keys = MultiFernet([Fernet(generate_key())])
    written = encrypt(CLAUDE, SECRET, keys)
    sibling = SecretLocation.provider("llm", "claude-haiku", "api_key")

    with pytest.raises(ConfigError):
        decrypt(sibling, written, keys)

    # And a provider stage is part of the identity, not decoration.
    with pytest.raises(ConfigError):
        decrypt(SecretLocation.provider("tts", "claude", "api_key"), written, keys)


def test_a_malformed_envelope_is_refused() -> None:
    keys = MultiFernet([Fernet(generate_key())])

    for stored in ("not-an-envelope", {"enc": "not-a-token"}, {"enc": 7}, {}):
        with pytest.raises(ConfigError) as caught:
            decrypt(CLAUDE, stored, keys)
        assert CLAUDE.describe() in str(caught.value)
        assert caught.value.__cause__ is None


def test_masking_hides_ciphertext_and_shows_references() -> None:
    envelope = encrypt(CLAUDE, SECRET, MultiFernet([Fernet(generate_key())]))

    assert mask(envelope) == MASK
    # An environment reference names a variable; that is not a secret,
    # and hiding it would hide the one thing worth reading.
    assert mask("ANTHROPIC_API_KEY") == "ANTHROPIC_API_KEY"
    assert mask("$WEATHER_TOKEN") == "$WEATHER_TOKEN"
    assert not is_envelope({"enc": "abc", "extra": 1})


def test_masking_fails_closed_on_everything_that_is_not_a_reference() -> None:
    """A malformed value sitting in a secret slot may be a plaintext
    secret, so the display path masks whatever it does not positively
    recognize as an environment reference. Passing such a value
    through would make the recovery-oriented show path the leak."""
    sentinel = "sk-live-hunter2-credential"

    # A near-envelope: right key, extra baggage. Not valid ciphertext,
    # not a reference, and possibly carrying a secret in the baggage.
    assert mask({"enc": "token", "extra": sentinel}) == MASK
    # A bare string that is not reference-shaped: lowercase, dashes.
    assert mask(sentinel) == MASK
    assert MASK == "********"
    # Non-string oddities fail closed too.
    assert mask(None) == MASK
    assert mask(42) == MASK
    assert mask(["$WEATHER_TOKEN"]) == MASK


def test_keys_are_read_newest_first_from_the_environment() -> None:
    new, old = generate_key(), generate_key()

    assert load_keys({}) is None
    assert load_keys({MASTER_KEY_ENV: "  "}) is None

    keys = load_keys({MASTER_KEY_ENV: f" {new} , {old} "})
    assert keys is not None
    written = encrypt(CLAUDE, SECRET, keys)
    assert decrypt(CLAUDE, written, MultiFernet([Fernet(new)])) == SECRET
    assert decrypt(CLAUDE, encrypt(CLAUDE, SECRET, MultiFernet([Fernet(old)])), keys) == SECRET


def test_an_unusable_key_names_its_position_and_not_its_material() -> None:
    good, rubbish = generate_key(), "not-a-fernet-key"

    with pytest.raises(ConfigError) as caught:
        load_keys({MASTER_KEY_ENV: f"{good},{rubbish}"})

    message = str(caught.value)
    assert MASTER_KEY_ENV in message
    assert "entry 2 of 2" in message
    assert rubbish not in message
    assert good not in _chain(caught.value)
