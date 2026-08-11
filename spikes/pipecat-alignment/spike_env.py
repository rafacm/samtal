"""Environment the spike's entry points need before importing pipecat.

Measurement harness, not adapter code, and one line of it, but the
reason is a discovery worth keeping.

nltk (pulled in transitively by `pipecat.utils.string`, which the RTVI
observer imports, which `pipecat.pipeline` imports) installs a meta-path
finder that refuses any import nltk itself triggers when the module
resolves inside the current working directory, as CWE-427 hardening. A
uv project keeps its virtualenv inside the project, so every one of
pipecat's own dependencies resolves inside the working directory
whenever the spike is run from the spike directory (or from anywhere
above it), and `import pipecat` fails outright with "Blocked import of
regex from current working directory".

Running every command from an unrelated directory also avoids it. The
spike disables the hook instead, because its tree holds no untrusted
code and a harness whose commands only work from `/tmp` is a harness
nobody reruns. Setting it here, before any pipecat import, is what makes
`uv run python drive.py` work from the spike's own directory.
"""

import os

os.environ.setdefault("NLTK_DISABLE_IMPORT_SECURITY", "1")
