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
and two replicas sharing the secret accept each other's tokens. Nothing
here is a session identifier: the token says which device this is, and
the session is what the websocket makes of it.

Tokens are never logged, at any level.
"""

import base64
import hashlib
import hmac
import time


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
