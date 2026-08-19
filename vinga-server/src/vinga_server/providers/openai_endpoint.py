"""What every provider speaking the OpenAI dialect decides the same way.

Two stages now point an `openai` type at either OpenAI itself or a
server implementing the same endpoint: `openai` TTS at
`/v1/audio/speech`, `openai` ASR at `/v1/audio/transcriptions`. The
choice is made by one option, `base_url`, and three answers hang off
it: whether an API key is required, whether the type's own model rules
apply, and whether session data leaves the host. Keeping the answers
here is what stops the two stages from drifting apart on a question
neither of them owns.

The LLM stage joined late and shares less. `openai_compatible` requires
`base_url` rather than defaulting to OpenAI and resolves its own key,
so of the three answers it takes only the host: which host it reaches,
for the event a failed call emits, and whether that host is OpenAI's,
which decides whether the request may ask for token counts (#55).
"""

from urllib.parse import urlsplit

from vinga_server.providers.base import ProviderError
from vinga_server.providers.kit import resolve_api_key

DEFAULT_BASE_URL = "https://api.openai.com/v1"

# Whether an entry speaks to OpenAI is decided by the host, not by the
# spelling of the URL. The startup guarantees below hang off that
# answer, and comparing the raw string would hand them away to a
# trailing slash: an entry naming `https://api.openai.com/v1/` would
# boot keyless and then fail on its first request, which is the
# per-conversation failure building providers at startup avoids.
OPENAI_HOST = "api.openai.com"


def parse_base_url(label: str, base_url: str) -> bool:
    """Whether `base_url` names OpenAI itself, rejecting what is not a
    URL at all.

    The host decides, so every spelling of OpenAI's endpoint keeps the
    same startup guarantees: a trailing slash, an uppercased host, an
    explicit port. `urlsplit` lowercases the host for us and strips any
    port and userinfo. Anything whose host is not OpenAI's is a
    compatible endpoint, which is the safe direction to be wrong in:
    it asks for no key and enforces no model rules, and the endpoint
    answers for itself.

    A base_url that is not a URL fails here rather than at the first
    request, for the same reason everything else in a factory does."""
    parts = urlsplit(base_url)
    if not parts.scheme or not parts.hostname:
        raise ProviderError(
            f'{label}: option "base_url" must be a URL with a scheme and a host, '
            f'such as "{DEFAULT_BASE_URL}"; got "{base_url}"'
        )
    return parts.hostname == OPENAI_HOST


def endpoint_host(base_url: str) -> str | None:
    """The host an entry's `base_url` names, for the identity a
    provider is stamped with. None only for a URL that has none, which
    a built provider does not have: `parse_base_url` refuses those."""
    return urlsplit(base_url).hostname


def endpoint_api_key(label: str, type_name: str, api_key_env: str | None, is_openai: bool) -> str:
    """The key to give the client, refusing an entry that speaks to
    OpenAI without one.

    OpenAI itself always needs a key, and an unset variable should fail
    the boot rather than every conversation. A self-hosted endpoint
    usually wants no key at all, but the SDK insists on one, so it gets
    the same placeholder the `openai_compatible` LLM type uses."""
    api_key = resolve_api_key(label, api_key_env)
    if api_key is not None:
        return api_key
    if is_openai:
        raise ProviderError(
            f'{label}: type "{type_name}" needs an API key when it speaks to '
            f"{OPENAI_HOST}; name the environment variable holding it "
            f'with "api_key_env"'
        )
    return "unused"
