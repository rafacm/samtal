"""The stored-secret envelope: round trips, rotation, and every refusal.

The refusals are the point of most of this file. A secret that cannot
be read has to say which entity and slot it belongs to, so the operator
knows what to set again, and it has to say that without carrying the
value anywhere a log or a bug report would pick it up: not in the
message, and not in the exception chain either.
"""

import pytest
from cryptography.fernet import Fernet, MultiFernet

from vinga_server.config import ConfigError
from vinga_server.config.secrets import (
    MASK,
    MASTER_KEY_ENV,
    ProviderSecrets,
    SecretLocation,
    SecretStore,
    decrypt,
    encrypt,
    generate_key,
    is_envelope,
    load_keys,
    mask,
    provider_secrets_in_force,
    resolve_mcp_values,
    stored_provider_secret,
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


def _store(*locations: SecretLocation) -> tuple[SecretStore, MultiFernet]:
    keys = MultiFernet([Fernet(generate_key())])
    return SecretStore({where: encrypt(where, SECRET, keys) for where in locations}, keys), keys


def test_the_store_opens_what_it_holds_and_nothing_else() -> None:
    store, _ = _store(CLAUDE)

    assert store.secret(CLAUDE) == SECRET
    assert store.secret(WEATHER) is None
    assert CLAUDE in store
    assert len(store) == 1


def test_the_store_lists_its_locations_and_an_entity_slots() -> None:
    env_slot = SecretLocation.mcp_server("weather", "env.API_TOKEN")
    store, _ = _store(WEATHER, CLAUDE, env_slot)

    assert store.locations() == [env_slot, WEATHER, CLAUDE]
    assert store.slots_for("mcp_server", "weather") == ["env.API_TOKEN", "headers.Authorization"]
    assert store.slots_for("provider", "llm.claude") == ["api_key"]
    assert store.slots_for("provider", "llm.other") == []


def test_a_store_that_cannot_open_a_slot_refuses_naming_the_location() -> None:
    written = encrypt(CLAUDE, SECRET, MultiFernet([Fernet(generate_key())]))

    for keys in (MultiFernet([Fernet(generate_key())]), None):
        with pytest.raises(ConfigError) as caught:
            SecretStore({CLAUDE: written}, keys).secret(CLAUDE)
        assert CLAUDE.describe() in str(caught.value)
        assert SECRET not in _chain(caught.value)


def test_a_provider_credential_is_bound_to_its_stage_and_name() -> None:
    store, _ = _store(CLAUDE)

    assert ProviderSecrets("llm", "claude", store).secret("api_key") == SECRET
    assert ProviderSecrets("llm", "claude", store).secret("other_key") is None
    assert ProviderSecrets("tts", "claude", store).secret("api_key") is None
    # No store at all is the default deployment: environment references
    # only, and nothing to consult.
    assert ProviderSecrets("llm", "claude").secret("api_key") is None


def test_the_provider_seam_is_empty_outside_a_construction() -> None:
    store, _ = _store(CLAUDE)

    assert stored_provider_secret("api_key") is None
    with provider_secrets_in_force(ProviderSecrets("llm", "claude", store)):
        assert stored_provider_secret("api_key") == SECRET
    assert stored_provider_secret("api_key") is None


def test_mcp_values_resolve_literals_and_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WEATHER_TOKEN", SECRET)

    resolved = resolve_mcp_values(
        "weather", "env", {"REGION": "eu", "API_TOKEN": "$WEATHER_TOKEN"}, None
    )

    assert resolved == {"REGION": "eu", "API_TOKEN": SECRET}


def test_a_stored_mcp_secret_shadows_the_reference_written_for_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ciphertext wins for the same slot, and the reference it shadows is
    not even consulted: an unset variable behind it must not fail the
    boot the secret was set to fix."""
    monkeypatch.delenv("WEATHER_TOKEN", raising=False)
    store, _ = _store(SecretLocation.mcp_server("weather", "env.API_TOKEN"))

    resolved = resolve_mcp_values(
        "weather", "env", {"REGION": "eu", "API_TOKEN": "$WEATHER_TOKEN"}, store
    )

    assert resolved == {"REGION": "eu", "API_TOKEN": SECRET}


def test_a_stored_mcp_secret_needs_no_key_in_the_entity() -> None:
    """A fragment cannot carry the value, so it need not carry a
    placeholder for it either."""
    store, _ = _store(SecretLocation.mcp_server("weather", "headers.Authorization"))

    assert resolve_mcp_values("weather", "headers", {}, store) == {"Authorization": SECRET}
    # The groups do not bleed into each other.
    assert resolve_mcp_values("weather", "env", {}, store) == {}


def test_an_unusable_key_names_its_position_and_not_its_material() -> None:
    good, rubbish = generate_key(), "not-a-fernet-key"

    with pytest.raises(ConfigError) as caught:
        load_keys({MASTER_KEY_ENV: f"{good},{rubbish}"})

    message = str(caught.value)
    assert MASTER_KEY_ENV in message
    assert "entry 2 of 2" in message
    assert rubbish not in message
    assert good not in _chain(caught.value)


# The per-entity fingerprint
#
# What the MCP reload's diff compares to decide whether an entry's
# stored secrets are still the ones its running manager was built with.
# The properties that matter are that it changes when they change, that
# it does not change when they do not, and that it carries neither the
# plaintext nor the ciphertext to whoever compares it.


def test_the_fingerprint_of_one_entity_is_stable_across_loads() -> None:
    """Two loads of the same rows are the same world, and a diff that
    said otherwise would restart every server on every reload."""
    keys = MultiFernet([Fernet(generate_key())])
    envelopes = {WEATHER: encrypt(WEATHER, SECRET, keys), CLAUDE: encrypt(CLAUDE, SECRET, keys)}

    first = SecretStore(envelopes, keys)
    second = SecretStore(dict(reversed(list(envelopes.items()))), keys)

    assert first.fingerprint("mcp_server", "weather") == second.fingerprint(
        "mcp_server", "weather"
    )
    # And an entity with nothing stored has one too, equal to every
    # other empty one, so "no secrets" is not a third state a caller has
    # to handle.
    assert first.fingerprint("mcp_server", "nothing") == SecretStore().fingerprint(
        "mcp_server", "nothing"
    )


def test_the_fingerprint_changes_when_the_ciphertext_is_rotated() -> None:
    keys = MultiFernet([Fernet(generate_key())])
    before = SecretStore({WEATHER: encrypt(WEATHER, SECRET, keys)}, keys)
    after = SecretStore({WEATHER: encrypt(WEATHER, "another-value", keys)}, keys)

    assert before.fingerprint("mcp_server", "weather") != after.fingerprint(
        "mcp_server", "weather"
    )


def test_the_fingerprint_changes_when_a_slot_is_added_or_removed() -> None:
    keys = MultiFernet([Fernet(generate_key())])
    env_slot = SecretLocation.mcp_server("weather", "env.API_TOKEN")
    one = SecretStore({WEATHER: encrypt(WEATHER, SECRET, keys)}, keys)
    two = SecretStore(
        {WEATHER: encrypt(WEATHER, SECRET, keys), env_slot: encrypt(env_slot, SECRET, keys)},
        keys,
    )

    assert one.fingerprint("mcp_server", "weather") != two.fingerprint("mcp_server", "weather")
    assert SecretStore().fingerprint("mcp_server", "weather") != one.fingerprint(
        "mcp_server", "weather"
    )


def test_one_entity_s_fingerprint_says_nothing_about_another_s() -> None:
    """A rotation on one entry must rebuild that entry and no other."""
    keys = MultiFernet([Fernet(generate_key())])
    other = SecretLocation.mcp_server("home", "headers.Authorization")
    untouched = encrypt(WEATHER, SECRET, keys)
    before = SecretStore({WEATHER: untouched, other: encrypt(other, SECRET, keys)}, keys)
    after = SecretStore(
        {WEATHER: untouched, other: encrypt(other, "another-value", keys)}, keys
    )

    assert before.fingerprint("mcp_server", "weather") == after.fingerprint(
        "mcp_server", "weather"
    )
    assert before.fingerprint("mcp_server", "home") != after.fingerprint("mcp_server", "home")
    # And a provider of the same name is a different entity.
    assert before.fingerprint("provider", "weather") != before.fingerprint(
        "mcp_server", "weather"
    )


def test_the_fingerprint_carries_neither_the_plaintext_nor_the_envelope() -> None:
    """It is compared by code that has no business holding either, and
    it needs no key to take, so what it must not be is a channel."""
    keys = MultiFernet([Fernet(generate_key())])
    envelope = encrypt(WEATHER, SECRET, keys)
    store = SecretStore({WEATHER: envelope}, keys)

    mark = store.fingerprint("mcp_server", "weather")

    assert SECRET not in mark
    assert envelope["enc"] not in mark
    # Taken without the keys at all, and the same either way: comparing
    # is not opening.
    assert SecretStore({WEATHER: envelope}).fingerprint("mcp_server", "weather") == mark


# Composing two loads by regime
#
# A staged reload serves a world that is partly the store's and partly
# the one it was already serving, and the credentials have to follow the
# same line: an MCP server's are read as its connection is made, which a
# reload makes again, and a provider's are read as the provider is
# built, which is still the start. The composition is a method here
# because everything it touches (envelopes, keys, the digest) is
# deliberately private, and a caller doing it from outside would have to
# be handed one of the three.

PROVIDER = SecretLocation.provider("llm", "claude", "api_key")

ROTATED = "sk-test-9c4e2f81-also-never-a-real-credential"


def two_loads() -> tuple[SecretStore, SecretStore, MultiFernet]:
    """One store as this server was built from it, and one as the
    database holds it after both slots were rotated."""
    keys = MultiFernet([Fernet(generate_key())])
    running = SecretStore(
        {WEATHER: encrypt(WEATHER, SECRET, keys), PROVIDER: encrypt(PROVIDER, SECRET, keys)},
        keys,
    )
    stored = SecretStore(
        {
            WEATHER: encrypt(WEATHER, ROTATED, keys),
            PROVIDER: encrypt(PROVIDER, ROTATED, keys),
        },
        keys,
    )
    return running, stored, keys


def test_the_live_kinds_are_taken_from_the_candidate() -> None:
    """An MCP rotation is applied, because a reload connects again with
    what the store holds now."""
    running, stored, _ = two_loads()

    composed = stored.composed(running, {"mcp_server"})

    assert composed.secret(WEATHER) == ROTATED
    assert composed.fingerprint("mcp_server", "weather") == stored.fingerprint(
        "mcp_server", "weather"
    )


def test_the_rest_is_carried_over_from_the_previous_world() -> None:
    """A provider rotation stays pending: the provider holding the
    credential was built at start and is still the one running, so a
    composed world that carried the new mark would report an applied
    change nothing had used."""
    running, stored, _ = two_loads()

    composed = stored.composed(running, {"mcp_server"})

    assert composed.secret(PROVIDER) == SECRET
    assert composed.fingerprint("provider", "llm.claude") == running.fingerprint(
        "provider", "llm.claude"
    )
    assert composed.fingerprint("provider", "llm.claude") != stored.fingerprint(
        "provider", "llm.claude"
    )


def test_a_slot_the_store_no_longer_holds_goes_with_its_kind() -> None:
    """The live half is replaced rather than merged, which is what makes
    clearing an MCP secret something a reload applies."""
    running, stored, keys = two_loads()
    cleared = SecretStore({PROVIDER: encrypt(PROVIDER, ROTATED, keys)}, keys)

    composed = cleared.composed(running, {"mcp_server"})

    assert WEATHER not in composed
    assert PROVIDER in composed


def test_every_kind_live_is_the_candidate_store() -> None:
    """What the composition retires into once nothing is start-bound:
    naming both kinds answers exactly the candidate."""
    running, stored, _ = two_loads()

    composed = stored.composed(running, {"mcp_server", "provider"})

    assert composed.locations() == stored.locations()
    for where in stored.locations():
        assert composed.secret(where) == stored.secret(where)


def test_the_composition_carries_no_envelope_out_of_the_class() -> None:
    """The property the operation exists for: a store in and a store
    out, with nothing an answer or a log could pick up in between."""
    running, stored, _ = two_loads()

    composed = stored.composed(running, {"mcp_server"})

    written = repr(composed) + str(composed.locations()) + str(len(composed))
    for value in (SECRET, ROTATED):
        assert value not in written
    assert composed.fingerprint("mcp_server", "weather") not in written
