"""What every lane needs before it can build a server.

Device authentication is on by default, and an enabled auth with no
secret in the environment is a boot failure, so a lane that builds an
app needs a secret. Setting one here rather than turning auth off keeps
every lane running the way a real deployment does: real tokens issued
by the OTA endpoint and checked at the websocket handshake.

An already-exported secret wins, which is what lets the smoke lane point
at a container and sign with the secret that container was started with.

Set at import time rather than in a fixture: a test module that builds
the app while it is being imported (the boot test does) needs the
secret before collection, not before the first test runs.

The bytecode setting below is here for the same reason: it has to be in
place before pytest imports the first test module.
"""

import os
import shutil
import sys
from pathlib import Path

# CPython validates a cached `.pyc` against the source's size and its
# mtime truncated to whole seconds, so a rewrite that keeps the byte
# count and lands inside the same second is invisible, and a file
# restored with `mv` looks older than its own cache. Both shapes occur
# here: checking that a regression test really fails without its fix
# usually swaps two statements (same size, one scripted second), and
# restoring a file from a backup carries the backup's mtime. The result
# is a working tree that lies about what it is running.
#
# Writing no bytecode at all removes the cache, and with it the stale
# cache. It covers pytest's assertion-rewritten test bytecode too, which
# uses the same check and matters as much because test files are edited
# constantly. Measured cost on this package is under 10 ms per run: the
# expensive imports live in site-packages and keep their own bytecode.
sys.dont_write_bytecode = True

# The flag cannot cover this file: pytest writes a conftest's rewritten
# bytecode before it executes the body that sets the flag, so this run
# has already cached it. Removing the cache now means the next run finds
# nothing to read and rewrites from source, which is the same guarantee
# the flag gives everything else. Only ever this directory, and only
# ever derived files.
shutil.rmtree(Path(__file__).parent / "__pycache__", ignore_errors=True)

AUTH_SECRET_ENV = "SAMTAL_AUTH_SECRET"

# Not a secret: a fixed value, so a failing test is reproducible.
TEST_AUTH_SECRET = "test-secret-" + "0123456789abcdef" * 2

os.environ.setdefault(AUTH_SECRET_ENV, TEST_AUTH_SECRET)
