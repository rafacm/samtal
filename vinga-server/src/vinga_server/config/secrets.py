"""The envelope a stored secret is written in, and the keys behind it.

A stored secret value has exactly two forms. The first is an
environment reference, carried over verbatim from the YAML file:
`api_key_env: ANTHROPIC_API_KEY` in a provider's options, a `$VAR`
value in an MCP server's env or headers. Those stay the models'
business, they are not secrets themselves, and they display as
themselves. The second is ciphertext, written only by
`vinga-server config provider secret set` and its MCP sibling, stored
as the JSON object
`{"enc": "<fernet token>"}` in the entity row's `secrets` column under
the credential slot it fills.

Fernet authenticates a token but says nothing about where it belongs,
so the encrypted payload here is not the bare secret: it is a small
JSON document carrying the secret together with its canonical location
(entity kind, identity, slot). Decryption verifies that location
against the slot being read and refuses a mismatch, so a token copied
into another row (an attacker-controlled MCP server's headers, say)
does not decrypt into a credential it was never set for. The
consequence is deliberate: moving a secret means setting it again,
there is no copy path for ciphertext.

`VINGA_MASTER_KEY` holds one or more Fernet keys, comma-separated,
newest first, wrapped in MultiFernet: encryption always uses the
newest, decryption tries them in order. This release supports adding a
key, not retiring one. Until a re-encrypt command exists, only newly
written secrets use a new key, so every old key must stay in the
variable for as long as any token written under it is still stored.

Every failure here raises ConfigError with a message that names the
location and the kind of failure and never embeds the value, and every
raise cuts the exception chain: a JSON decode error carries the
document it failed on, which in this module is the decrypted plaintext.
"""

import hashlib
import json
import os
import re
from collections.abc import Collection, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from vinga_server.config.entities import (
    SECRET_HOLDERS,
    addressed,
    descriptor,
    entity_location,
    provider_identity,
)
from vinga_server.config.loader import ConfigError
from vinga_server.config.models import MASK, resolve_env_references, spoken_identity

# `MASK` and `provider_identity` are re-exported rather than defined
# here. Both are facts about how an entity is named and displayed rather
# than about how a value is encrypted, and their definitions sit where
# the CLI can reach them without a key: the mask beside the predicate
# that decides which names are masked, the identity beside the
# addressing it flattens. The names stay in this module's vocabulary
# because its many server-side readers already ask it for them.

MASTER_KEY_ENV = "VINGA_MASTER_KEY"

# The single key of the stored envelope object. Short, and distinct
# from anything a model field is called, so a stored envelope is
# recognizable wherever one turns up.
ENVELOPE_KEY = "enc"

# Bumped only if the payload's shape changes, which would need a
# migration of every stored token; it exists so that such a change can
# be detected rather than misread.
_PAYLOAD_VERSION = 1

EntityKind = Literal["provider", "mcp_server"]


@dataclass(frozen=True)
class SecretLocation:
    """Where a stored secret belongs: an entity kind, that entity's
    identity, and the credential slot inside it.

    The triple is encrypted with the secret and checked on the way back
    out, so it is part of the ciphertext's meaning rather than
    bookkeeping around it."""

    kind: EntityKind
    identity: str
    slot: str

    @classmethod
    def provider(cls, stage: str, name: str, slot: str) -> "SecretLocation":
        """A provider is identified by its stage and its name together,
        everywhere it is named."""
        return cls(kind="provider", identity=provider_identity(stage, name), slot=slot)

    @classmethod
    def mcp_server(cls, name: str, slot: str) -> "SecretLocation":
        """The slot is a dotted path into the row's model-shaped half:
        env.API_ACCESS_TOKEN, headers.Authorization."""
        return cls(kind="mcp_server", identity=name, slot=slot)

    def describe(self) -> str:
        """How the location reads in a sentence an operator sees: the
        kind, the entity, and the slot. A refusal names it this way, and
        so does the acknowledgement a secret write answers with, which is
        why it is one string rather than a format each caller picks.

        A sentence rather than an address, so both halves leave through
        the door every spoken identity leaves through, and the fields
        keep what they are: `identity` and `slot` are what a lookup is
        made from and they are untouched, which is the same line
        `entity_location` draws (#381, #382, #414).

        Reachable rather than belt and braces, on thirteen refusals and
        four acknowledgements. `verify_secrets` opens every stored
        envelope at startup, so an entity or a slot named before the
        addressability rule reaches a boot's stderr through whichever of
        those sentences the envelope earns; and a slot is held to that
        rule at write time only, exactly as a name is.

        Split into the parameters that address the entity before each is
        said, which is what keeps the rule off the separator: an
        identity is a dotted join this repository owns, and asking the
        URL rule of the join rather than of its parts would let
        `llm.https://user:password@host/x` be read as a scheme of its
        own.
        """
        holder = SECRET_HOLDERS[self.kind]
        said = ".".join(
            spoken_identity(part) for part in addressed(holder, self.identity)
        )
        return f"{self.kind} {said} {spoken_identity(self.slot)}"


def load_keys(environ: Mapping[str, str] | None = None) -> MultiFernet | None:
    """The configured encryption keys, newest first, or None when
    VINGA_MASTER_KEY is unset or empty.

    None is a legitimate state, not an error: a deployment whose secrets
    are all environment references never needs a key, and the CLI has to
    keep running without one so it can repair a deployment whose key is
    wrong. Refusing to boot with ciphertext stored and no key is
    verify_secrets' job, at startup."""
    raw = (environ if environ is not None else os.environ).get(MASTER_KEY_ENV, "")
    entries = [entry.strip() for entry in raw.split(",")]
    entries = [entry for entry in entries if entry]
    if not entries:
        return None

    keys: list[Fernet] = []
    problem: str | None = None
    for position, entry in enumerate(entries, start=1):
        try:
            keys.append(Fernet(entry))
        except (ValueError, TypeError):
            # The entry is key material; the position is what identifies
            # it without quoting it. Recorded here and raised below,
            # because a refusal raised inside the handler keeps the
            # library's exception as its __context__, and what that one
            # was given is the key.
            problem = (
                f"{MASTER_KEY_ENV}: entry {position} of {len(entries)} is not a "
                f"Fernet key; each entry is a 32-byte urlsafe-base64 key, newest "
                f"first, comma-separated"
            )
            break
    if problem is not None:
        raise ConfigError(problem)
    return MultiFernet(keys)


def generate_key() -> str:
    """A fresh Fernet key, for the deployment notes and for tests."""
    return Fernet.generate_key().decode("ascii")


def is_envelope(value: object) -> bool:
    """Whether a stored value is ciphertext rather than a plain string."""
    return (
        isinstance(value, Mapping)
        and set(value) == {ENVELOPE_KEY}
        and isinstance(value[ENVELOPE_KEY], str)
    )


# The two environment-reference spellings, and nothing else, may be
# displayed. The $NAME form mirrors the models' _ENV_REFERENCE_RE; the
# bare form is the uppercase name an *_env field holds. Anything that
# matches neither may be a plaintext secret that ended up in a secret
# slot, so the display path fails closed rather than passing it on.
_DOLLAR_REFERENCE_RE = re.compile(r"^\$[A-Za-z_][A-Za-z0-9_]*$")
_BARE_REFERENCE_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def mask(value: object) -> object:
    """A stored secret-slot value as it may be displayed. Only a
    syntactically valid environment reference passes through, because a
    reference names a variable and that is not a secret. Everything
    else becomes the mask: valid ciphertext, malformed envelopes, and
    stray strings alike, since a malformed value in a secret slot may
    be a plaintext secret and showing it would make the display path
    the leak."""
    if isinstance(value, str) and (
        _DOLLAR_REFERENCE_RE.match(value) or _BARE_REFERENCE_RE.match(value)
    ):
        return value
    return MASK


def encrypt(location: SecretLocation, secret: str, keys: MultiFernet | None) -> dict[str, str]:
    """The envelope holding `secret` bound to `location`, under the
    newest configured key."""
    if keys is None:
        raise ConfigError(
            f"{location.describe()}: cannot store a secret without an encryption "
            f"key; set {MASTER_KEY_ENV} to one or more Fernet keys, newest first"
        )
    payload = json.dumps(
        {
            "v": _PAYLOAD_VERSION,
            "kind": location.kind,
            "identity": location.identity,
            "slot": location.slot,
            "secret": secret,
        }
    ).encode("utf-8")
    token: bytes | None = None
    try:
        token = keys.encrypt(payload)
    except Exception:
        # Nothing from the library reaches the caller: its exceptions
        # are raised with the payload in scope, so the refusal is raised
        # outside the handler rather than merely without a cause.
        pass
    if token is None:
        raise ConfigError(f"{location.describe()}: the secret could not be encrypted")
    return {ENVELOPE_KEY: token.decode("ascii")}


def decrypt(location: SecretLocation, envelope: object, keys: MultiFernet | None) -> str:
    """The secret inside `envelope`, which must have been written for
    `location`. Every refusal names the location and the kind of
    failure, and none of them carries the value."""
    if not is_envelope(envelope):
        raise ConfigError(
            f"{location.describe()}: the stored secret is not a valid envelope; "
            f"set it again with that entry's own secret set command"
        )
    if keys is None:
        raise ConfigError(
            f"{location.describe()}: an encrypted secret is stored but no "
            f"encryption key is configured; set {MASTER_KEY_ENV}"
        )

    token = envelope[ENVELOPE_KEY]  # type: ignore[index]
    payload: bytes | None = None
    try:
        payload = keys.decrypt(token.encode("ascii"))
    except (InvalidToken, ValueError, TypeError):
        pass
    if payload is None:
        raise ConfigError(
            f"{location.describe()}: the stored secret cannot be decrypted with "
            f"any key in {MASTER_KEY_ENV}; if a key was rotated, the key the "
            f"secret was written under must stay configured, or set the secret "
            f"again"
        )

    return _unwrap(location, payload)


def _unwrap(location: SecretLocation, payload: bytes) -> str:
    """The secret out of a decrypted payload, once the payload says it
    belongs here. Raised outside every handler here: a JSONDecodeError
    carries the document it failed on, which is the plaintext, and
    clearing the cause would leave it attached as the context."""
    document: object = None
    decoded = False
    try:
        document = json.loads(payload.decode("utf-8"))
        decoded = True
    except (UnicodeDecodeError, json.JSONDecodeError):
        pass
    if not decoded:
        raise ConfigError(
            f"{location.describe()}: the stored secret decrypted to something "
            f"that is not a valid payload; set it again with that entry's own "
            f"secret set command"
        )

    if not isinstance(document, dict) or document.get("v") != _PAYLOAD_VERSION:
        raise ConfigError(
            f"{location.describe()}: the stored secret has an unsupported "
            f"payload version"
        )

    fields = [document.get("kind"), document.get("identity"), document.get("slot")]
    if not all(isinstance(field, str) for field in fields):
        raise ConfigError(
            f"{location.describe()}: the stored secret's payload names no location"
        )

    stored = SecretLocation(kind=fields[0], identity=fields[1], slot=fields[2])
    if stored != location:
        # A valid token that belongs somewhere else. Both locations are
        # names of configuration entries, never values, so quoting them
        # is what makes the refusal actionable.
        raise ConfigError(
            f"{location.describe()}: the stored secret was written for "
            f"{stored.describe()}; a secret cannot be moved between slots, "
            f"set it again with that entry's own secret set command"
        )

    secret = document.get("secret")
    if not isinstance(secret, str):
        raise ConfigError(
            f"{location.describe()}: the stored secret's payload holds no secret"
        )
    return secret


class SecretStore:
    """The stored envelopes of one loaded snapshot, and the keys that
    open them.

    It rides beside the domain models and never inside them: a pydantic
    model carries neither an envelope nor a decrypted value, which is
    what lets the existing validators keep rejecting inline secrets
    exactly as they do for the YAML file. Nothing here holds plaintext
    either; a secret is decrypted when a provider or an MCP server is
    being built, and goes straight into the client or the child
    process.
    """

    def __init__(
        self,
        envelopes: Mapping[SecretLocation, object] | None = None,
        keys: MultiFernet | None = None,
    ) -> None:
        self._envelopes: dict[SecretLocation, object] = dict(envelopes or {})
        self._keys = keys

    def composed(
        self, previous: "SecretStore", live: Collection[EntityKind]
    ) -> "SecretStore":
        """This store's entries for the kinds a running server can apply
        now, and `previous`'s for the kinds that still wait for a
        restart.

        The operation a staged reload needs, and it is here rather than
        at the caller because everything it has to touch is deliberately
        private (#191). Envelopes, keys and fingerprints never leave this
        class, so a composition written outside it would have to be
        given one of the three; written inside, it is a store in and a
        store out, and a caller learns nothing it could not learn from
        either side on its own.

        `live` is the entity kinds whose stored credentials the apply
        actually uses. An MCP server's are read as its connection is
        made, which a reload makes again, so a rotation there is applied
        and must be carried; a provider's are read as the provider is
        built, which is still the boot, so carrying a rotation there
        would report an applied change that nothing has used. The half
        that stays behind is the half the running world was built from,
        which is exactly what the previous generation holds.

        The keys are this store's. Both loads read `VINGA_MASTER_KEY`
        out of one process's environment, so the two key sets are the
        same set read twice; taking the newer one keeps the composed
        store's keys the ones its own load verified against, and a key
        set that could really change under a running process is a
        restart rather than a reload.
        """
        kinds = frozenset(live)
        envelopes = {
            where: envelope
            for source in (previous, self)
            for where, envelope in source._envelopes.items()
            if (where.kind in kinds) is (source is self)
        }
        return SecretStore(envelopes, self._keys)

    def __len__(self) -> int:
        return len(self._envelopes)

    def __contains__(self, location: object) -> bool:
        return location in self._envelopes

    def locations(self) -> list[SecretLocation]:
        """Every stored slot, in a fixed order so output and error
        reporting do not depend on insertion."""
        return sorted(self._envelopes, key=lambda where: (where.kind, where.identity, where.slot))

    def slots_for(self, kind: EntityKind, identity: str) -> list[str]:
        """The slots stored for one entity, which is what `show` renders
        as masks and what MCP value resolution walks."""
        return [
            where.slot
            for where in self.locations()
            if where.kind == kind and where.identity == identity
        ]

    def secret(self, location: SecretLocation) -> str | None:
        """The plaintext for a slot, or None when nothing is stored for
        it. Decrypts on demand, and every failure names the location
        without carrying the value."""
        envelope = self._envelopes.get(location)
        if envelope is None:
            return None
        return decrypt(location, envelope, self._keys)

    def fingerprint(self, kind: EntityKind, identity: str) -> str:
        """An opaque mark of what is stored for one entity: which slots
        it has, and the ciphertext sitting in them.

        It answers one question, whether two loads hold the same stored
        secrets for the same entity, which is what decides whether
        something built from them has to be built again. A digest rather
        than the envelopes themselves so that nothing leaves this class
        that could end up in a response, a log line or an exception
        message: agreement is the whole of what a caller may learn, and
        no key is needed to ask.

        Setting a slot again to the same plaintext still changes the
        mark, since a Fernet token carries a timestamp and a fresh IV.
        Rebuilding then is the safe direction to be wrong in: the other
        one would mean deciding that two ciphertexts hold the same
        secret, which is a question this class has no business
        answering.
        """
        digest = hashlib.sha256()
        for where in self.locations():
            if where.kind != kind or where.identity != identity:
                continue
            # Length-prefixed, so that no two different sets of slots can
            # produce the same stream of bytes: a slot named "a" holding
            # "bc" would otherwise digest as a slot named "ab" holding
            # "c".
            for part in (where.slot, _canonical(self._envelopes[where])):
                digest.update(f"{len(part)}:".encode())
                digest.update(part.encode("utf-8"))
        return digest.hexdigest()


def _canonical(envelope: object) -> str:
    """One stored value as bytes to digest, in a form that does not
    depend on how the row was read: keys sorted, and anything JSON
    cannot carry rendered as text rather than refused, because a
    malformed value in a secret slot still has to be compared with the
    next load's."""
    return json.dumps(envelope, sort_keys=True, default=str)


@dataclass(frozen=True)
class ProviderSecrets:
    """The stored secrets of one provider entry, resolved on demand.

    A provider is identified by its stage and its name, which the
    factory building it does not know: it is handed a label and its
    configuration entry. So the identity is bound here, where the
    registry does know it."""

    stage: str
    name: str
    store: SecretStore | None = None

    def secret(self, slot: str) -> str | None:
        if self.store is None:
            return None
        return self.store.secret(SecretLocation.provider(self.stage, self.name, slot))


# The provider whose factory is running, if any. A context variable
# rather than a factory argument for the reason models.py has one for
# the YAML path: every provider factory has the same two-argument
# signature, the credential is needed inside one of them, and threading
# an argument through all twelve to be ignored by most would make the
# seam wider than it is. It is set for the duration of one construction
# call by build_provider and read only by the resolvers below.
_provider_secrets: ContextVar[ProviderSecrets | None] = ContextVar(
    "vinga_provider_secrets", default=None
)


@contextmanager
def provider_secrets_in_force(secrets: ProviderSecrets | None) -> Iterator[None]:
    """Make `secrets` the stored credentials for whatever is built
    inside the block."""
    token = _provider_secrets.set(secrets)
    try:
        yield
    finally:
        _provider_secrets.reset(token)


def stored_provider_secret(slot: str) -> str | None:
    """The stored credential for one slot of the provider being built,
    or None when there is none (which is every provider a deployment
    configures with environment references, the default)."""
    secrets = _provider_secrets.get()
    return None if secrets is None else secrets.secret(slot)


def resolve_mcp_values(
    server: str, group: str, values: Mapping[str, str], store: SecretStore | None
) -> dict[str, str]:
    """One MCP server's `env` or `headers`, as the spawned process or the
    request should see it.

    Literal values pass through and a `$VAR` is read from the server's
    own environment, exactly as before. A slot with a stored secret
    takes precedence over the reference written for the same key,
    because a secret write is the later and more deliberate act, and a slot
    with no key in the entity at all is added: a fragment cannot carry
    the value, so requiring it to carry a placeholder would mean
    inventing an environment variable nobody sets.

    `server` is a stored name, and the location it goes into is what a
    boot prints when a reference names a variable nobody set, so it is
    composed by `entity_location` like every other location over a
    stored identity rather than joined to its section here (#414).
    """
    stored: dict[str, str] = {}
    if store is not None:
        for slot in store.slots_for("mcp_server", server):
            written_group, _, key = slot.partition(".")
            if written_group != group or not key:
                continue
            secret = store.secret(SecretLocation.mcp_server(server, slot))
            if secret is not None:
                stored[key] = secret
    references = {key: value for key, value in values.items() if key not in stored}
    written_at = entity_location(descriptor("mcp-server"), server)
    resolved = resolve_env_references(f"{written_at}.{group}", references)
    resolved.update(stored)
    return resolved


__all__ = [
    "MASK",
    "provider_identity",
    "MASTER_KEY_ENV",
    "ProviderSecrets",
    "SecretLocation",
    "SecretStore",
    "decrypt",
    "encrypt",
    "generate_key",
    "is_envelope",
    "load_keys",
    "mask",
    "provider_secrets_in_force",
    "resolve_mcp_values",
    "stored_provider_secret",
]
