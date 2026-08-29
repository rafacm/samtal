"""What a program does when whoever was reading it stops.

`vinga-server events reference` prints a document long enough to outrun a
pipe buffer, so `| head` is an ordinary thing to do with it; `vinga
events tail` prints until it is told to stop, so `| head -n 1` is the
ordinary way to wait for one event. Neither may answer that with a
traceback, and what keeps them from doing so is subtle enough that two
copies of it would be one pending bug: the exit status is a convention,
and the redirection below is a trap nobody guesses twice.

The module is stdlib only, deliberately. It is imported by the events
group, which reaches no server, and by the configuration CLI, whose
module-scope reach is an inventory held by a test
(`tests/unit/test_cli_import_weight.py`): a shared home that dragged the
event catalog into the client tier would have cost more than the
duplication it saved.
"""

import os
import signal
import sys

# What a process that was cut off reports, by the convention a shell
# already understands: the signal that would have killed it, offset by
# 128. `head -n 1` is not an error to report; it is a reader who has read
# enough.
BROKEN_PIPE_STATUS = 128 + signal.SIGPIPE


def reader_stopped_reading() -> int:
    """A consumer closed the pipe, which is not a failure to report.

    Two things have to happen for it to stay unreported. The status is
    the shell's own for a process cut off by SIGPIPE, so a pipeline reads
    the way a pipeline does. And the file descriptor behind `sys.stdout`
    is replaced with the null device before returning, because the
    interpreter flushes its streams on the way out and a flush to a pipe
    nobody is reading raises a second time, after this function is out of
    the way: Python would print `Exception ignored on flushing
    sys.stdout` to stderr, which is the traceback this exists to prevent
    wearing different words.
    """
    try:
        empty = os.open(os.devnull, os.O_WRONLY)
        os.dup2(empty, sys.stdout.fileno())
    except OSError:
        # Whatever stdout has become cannot be redirected. There is
        # nothing further to do about it and nothing to say about it
        # either, since saying it is what this avoids.
        pass
    return BROKEN_PIPE_STATUS


__all__ = ["BROKEN_PIPE_STATUS", "reader_stopped_reading"]
