# The server a seeding script writes through, started and stopped by it.
#
# `samtal-server config` writes through the configuration API, so seeding
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
# The server needs the environment a server needs: SAMTAL_AUTH_SECRET and
# SAMTAL_API_SECRET. It needs no provider credential, because with an
# empty domain half there is no provider to build.

# The port to reach the server on, overridable so that a test run and a
# CI container can each pick a free one. Exported rather than only read,
# so that the server binds it, the CLI resolves it (an environment
# override beats the mounted file) and the poll below waits on it: three
# readers, one value, no drift between the file's port and this one.
SAMTAL_SEED_PORT="${SAMTAL_SERVER__PORT:-8003}"
export SAMTAL_SERVER__PORT="$SAMTAL_SEED_PORT"

# And where the CLI sends the writes, set rather than left to be
# resolved. The CLI gives an inherited SAMTAL_API_URL precedence over
# the port, which is right for an operator and wrong here: an ambient
# one, left over in a shell or set in a CI job for another deployment,
# would take the seeding writes and the bearer token with them while
# the server started below stayed empty. The seeding writes to the
# server the seeding started, and to nothing else.
export SAMTAL_API_URL="http://127.0.0.1:${SAMTAL_SEED_PORT}/api"
SAMTAL_SEED_LOG="${TMPDIR:-/tmp}/samtal-seed-server.log"
SAMTAL_SEED_PID=""

server_ready() {
    # Not curl: the image carries a Python interpreter and deliberately
    # not much else, which is what the container healthcheck already
    # relies on.
    python - "$SAMTAL_SEED_PORT" <<'PY' 2>/dev/null
import sys
import urllib.request

urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/healthz", timeout=2).read()
PY
}

start_server() {
    samtal-server > "$SAMTAL_SEED_LOG" 2>&1 &
    SAMTAL_SEED_PID=$!
    # Bounded, and it gives up rather than hanging: a server that exited
    # on a configuration it will not accept is the failure this has to
    # report, and it reports it by letting the exit trap print the log.
    i=0
    while [ "$i" -lt 120 ]; do
        if ! kill -0 "$SAMTAL_SEED_PID" 2>/dev/null; then
            echo "the seeding server exited before it was ready" >&2
            return 1
        fi
        if server_ready; then
            return 0
        fi
        sleep 0.5
        i=$((i + 1))
    done
    echo "the seeding server never became ready on 127.0.0.1:$SAMTAL_SEED_PORT" >&2
    return 1
}

stop_server() {
    [ -n "$SAMTAL_SEED_PID" ] || return 0
    # SIGTERM, which is what runs the drain, rather than a kill that
    # would leave the database mid-write.
    kill "$SAMTAL_SEED_PID" 2>/dev/null || true
    wait "$SAMTAL_SEED_PID" 2>/dev/null || true
    SAMTAL_SEED_PID=""
}

on_exit() {
    status=$?
    stop_server
    if [ "$status" -ne 0 ]; then
        echo "seeding failed (exit $status); the server log follows" >&2
        cat "$SAMTAL_SEED_LOG" >&2
    fi
    exit "$status"
}
