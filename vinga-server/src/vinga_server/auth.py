"""Device tokens: what proves a websocket connection is a known device.

The scheme is the one xinnan-tech/xiaozhi-esp32-server uses, kept
because it is already proven against stock firmware, which persists
whatever the OTA reply hands it and sends it back as
`Authorization: Bearer <token>` on the websocket handshake. A token is
`sig.ts`, where `sig` is unpadded urlsafe base64 of
HMAC-SHA256(secret, "client_id|device_id|ts") and `ts` is the issuing
time in whole seconds.

It is stateless on purpose. The server keeps no token table, so a
restart does not lock out every device holding an NVS-persisted token,
and any process sharing the secret accepts another's tokens. That is a
property of the scheme rather than topology support: one replica is the
supported topology (#316). Nothing
here is a session identifier: the token says which device this is, and
the session is what the websocket makes of it.

Tokens are never logged, at any level.
"""

import base64
import hashlib
import hmac
import os
import time

from vinga_server.config import Config, ConfigError


class DeviceAuth:
    """Issues and verifies device tokens against one shared secret."""

    def __init__(self, secret: str, expire_s: int) -> None:
        self._secret = secret.encode("utf-8")
        self._expire_s = expire_s

    def issue(self, client_id: str, device_id: str) -> str:
        """A token for this device, valid for `expire_s` from now."""
        issued = int(time.time())
        return f"{self._sign(client_id, device_id, issued)}.{issued}"

    def verify(self, token: str, client_id: str, device_id: str) -> bool:
        """Whether this token was issued by us, for this device, and is
        still inside its lifetime.

        Never raises: a malformed token is simply not a valid one, and
        the caller has one thing to do about it either way.
        """
        signature, separator, issued = token.rpartition(".")
        if not separator or not signature:
            return False
        try:
            issued_at = int(issued)
        except ValueError:
            return False
        age = int(time.time()) - issued_at
        # A token from the future is as wrong as an expired one: it means
        # a clock skew large enough that its expiry means nothing.
        if age < 0 or age > self._expire_s:
            return False
        return hmac.compare_digest(signature, self._sign(client_id, device_id, issued_at))

    def _sign(self, client_id: str, device_id: str, issued_at: int) -> str:
        message = f"{client_id}|{device_id}|{issued_at}".encode()
        digest = hmac.new(self._secret, message, hashlib.sha256).digest()
        # urlsafe and unpadded: the token travels in an HTTP header and is
        # persisted to the device's NVS, so it stays free of "+/=".
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def build_device_auth(config: Config) -> DeviceAuth | None:
    """The server's token issuer, or None when auth is turned off.

    Enabled auth with no secret in the environment is a boot failure,
    not a warning: a deployment that has forgotten its secret must not
    quietly serve every device that connects. The message carries the
    fix and the deliberate way out, because this is the error somebody
    meets at three in the morning on a first deploy.
    """
    auth = config.server.auth
    if not auth.enabled:
        return None
    secret = os.environ.get(auth.secret_env, "").strip()
    if not secret:
        raise ConfigError(
            f"device authentication is enabled but {auth.secret_env} is not set.\n"
            f"Generate a secret and put it in the environment:\n"
            f"  {auth.secret_env}=$(openssl rand -hex 32)\n"
            f"Or turn authentication off for a trial on a trusted network, with\n"
            f"server.auth.enabled: false in the config file, or\n"
            f"VINGA_SERVER__AUTH__ENABLED=false in the environment."
        )
    return DeviceAuth(secret, auth.token_expire_s)
