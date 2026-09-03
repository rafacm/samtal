# The server a seeding script writes through, started and stopped by it.
#
# `vinga-server config` writes through the configuration API, so seeding
# a database means having a server to write to. Each seeding script
# starts one of its own inside the seeding container, against the same
# data volume the real container will read, configures it over loopback
# with the image's own CLI, and stops it again. The volume then holds the
# seeded database, and the seeding has exercised the shipped artifact
# including its API rather than a fixture.
#
# Starting on an empty domain half is a valid boot: the completeness
# rules (every stage of every agent resolving, a default agent when
# nothing is bound) are checked only when agents exist, so a fresh
# database serves no agents and is otherwise a running server.
#
# Sourced rather than copied into each script, because process lifecycle
# is exactly where three near-identical copies drift apart.
#
# The server needs the environment a server needs: VINGA_AUTH_SECRET and
# VINGA_API_SECRET. It needs no provider credential, because with an
# empty domain half there is no provider to build.

# The port to reach the server on, overridable so that a test run and a
# CI container can each pick a free one. Exported rather than only read,
# so that the server binds it, the CLI resolves it (an environment
# override beats the mounted file) and the poll below waits on it: three
# readers, one value, no drift between the file's port and this one.
VINGA_SEED_PORT="${VINGA_SERVER__PORT:-8003}"
export VINGA_SERVER__PORT="$VINGA_SEED_PORT"

# And where the CLI sends the writes, set rather than left to be
# resolved. The CLI gives an inherited VINGA_API_URL precedence over
# the port, which is right for an operator and wrong here: an ambient
# one, left over in a shell or set in a CI job for another deployment,
# would take the seeding writes and the bearer token with them while
# the server started below stayed empty. The seeding writes to the
# server the seeding started, and to nothing else.
export VINGA_API_URL="http://127.0.0.1:${VINGA_SEED_PORT}/api"
VINGA_SEED_LOG="${TMPDIR:-/tmp}/vinga-seed-server.log"
VINGA_SEED_PID=""

server_ready() {
    # Readiness rather than liveness, because what this waits for is a
    # server that can be worked with rather than one that is merely
    # running, and because it is what the name of this function has
    # always meant.
    #
    # Not curl: the image carries a Python interpreter and deliberately
    # not much else, which is what the container healthcheck already
    # relies on.
    python - "$VINGA_SEED_PORT" <<'PY' 2>/dev/null
import sys
import urllib.request

urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/readyz", timeout=2).read()
PY
}

start_server() {
    vinga-server > "$VINGA_SEED_LOG" 2>&1 &
    VINGA_SEED_PID=$!
    # Bounded, and it gives up rather than hanging: a server that exited
    # on a configuration it will not accept is the failure this has to
    # report, and it reports it by letting the exit trap print the log.
    i=0
    while [ "$i" -lt 120 ]; do
        if ! kill -0 "$VINGA_SEED_PID" 2>/dev/null; then
            echo "the seeding server exited before it was ready" >&2
            return 1
        fi
        if server_ready; then
            return 0
        fi
        sleep 0.5
        i=$((i + 1))
    done
    echo "the seeding server never became ready on 127.0.0.1:$VINGA_SEED_PORT" >&2
    return 1
}

stop_server() {
    [ -n "$VINGA_SEED_PID" ] || return 0
    # SIGTERM, which is what runs the drain, rather than a kill that
    # would leave the database mid-write.
    kill "$VINGA_SEED_PID" 2>/dev/null || true
    wait "$VINGA_SEED_PID" 2>/dev/null || true
    VINGA_SEED_PID=""
}

# Cleanup runs once, on EXIT, and is the only handler that touches the
# server. The signal handlers exist to give EXIT a status to work with:
# a script killed by a signal has no exit status of its own until
# something sets one, and `$?` inside a handler shared between EXIT and
# a signal reads whatever the last command happened to return, which for
# an interrupt during a successful write is zero. A seeding step that
# was interrupted must not look like one that finished.
#
# The traps are cleared before the final exit, so a handler cannot run
# twice and the cleanup cannot be re-entered while it is running.
on_exit() {
    status=$?
    trap - EXIT INT TERM
    stop_server
    if [ "$status" -ne 0 ]; then
        echo "seeding failed (exit $status); the server log follows" >&2
        cat "$VINGA_SEED_LOG" >&2
    fi
    exit "$status"
}

# 128 + the signal number, which is the status a shell reports for a
# process a signal ended, so an interrupted seeding is nonzero and says
# which signal did it.
on_interrupt() {
    trap - EXIT INT TERM
    stop_server
    echo "seeding was interrupted" >&2
    exit 130
}

on_terminate() {
    trap - EXIT INT TERM
    stop_server
    echo "seeding was terminated" >&2
    exit 143
}
