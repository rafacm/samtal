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

# A cached `.pyc` records the source's size and its mtime in whole
# seconds, and CPython accepts the cache when both are *equal* to the
# source's current values. So any edit that keeps the byte count and
# leaves the mtime on the second it was compiled on is invisible. Two
# ordinary operations here do exactly that:
#
#   - Checking a regression test really fails without its fix. Reverting
#     usually swaps two statements, which preserves the byte count, and
#     a scripted revert-run-restore finishes inside one second.
#   - Restoring a file from a backup, which carries the backup's mtime
#     rather than the current time, landing back on the compiled second.
#
# The result is a tree that lies about what it is running. It cost half
# an hour on #13, where a restored fix ran as its pre-fix version.
#
# Writing no bytecode removes the cache, and with it the stale cache. It
# covers pytest's assertion-rewritten test bytecode too, which uses the
# same check and matters as much because test files are edited
# constantly. Measured cost is noise: the expensive imports live in
# site-packages and keep their own bytecode.
sys.dont_write_bytecode = True

# The flag stops writes, not reads: a cache that already exists is still
# consulted, and is now never refreshed, which would leave a stale one
# stale forever. Caches do get written outside pytest, by `uv run
# samtal-server` or a bare `python -c "import samtal_server..."`, and
# every tree that predates this file has a full set. So clear them, once,
# before the first import of anything under test.
#
# This also covers the one file the flag cannot: pytest writes a
# conftest's rewritten bytecode *before* it executes the body that sets
# the flag, so by now this run has already cached this file. Clearing
# leaves the next run nothing stale to read.
#
# Only these two trees, never `.venv`: site-packages bytecode is
# legitimate, expensive to rebuild, and its sources do not get edited.
_ROOT = Path(__file__).resolve().parent.parent
for _tree in (_ROOT / "samtal_server", _ROOT / "tests"):
    for _cache in _tree.rglob("__pycache__"):
        shutil.rmtree(_cache, ignore_errors=True)

AUTH_SECRET_ENV = "SAMTAL_AUTH_SECRET"

# Not a secret: a fixed value, so a failing test is reproducible.
TEST_AUTH_SECRET = "test-secret-" + "0123456789abcdef" * 2

os.environ.setdefault(AUTH_SECRET_ENV, TEST_AUTH_SECRET)
