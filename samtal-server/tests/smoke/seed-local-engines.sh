#!/bin/sh
# A domain half that names local engines, used to prove the slim image
# refuses it rather than failing later in some obscure way. Seeding
# succeeds on any image: nothing is imported until a provider is built,
# and the server this writes through starts on an empty domain half, so
# it builds none. The boot after this is what does.
set -eu

# The server this writes through, started here and stopped on the way
# out: `samtal-server config` is a client of the configuration API now,
# so seeding a database means having a server to write to. serve.sh says
# how, and its exit trap prints the server's log if anything here fails.
. "$(dirname "$0")/serve.sh"
trap on_exit EXIT
trap on_interrupt INT
trap on_terminate TERM
start_server

samtal-server config set provider llm mock -f - <<'YAML'
type: mock
YAML

samtal-server config set provider asr whisper -f - <<'YAML'
type: faster_whisper
model: small
YAML

samtal-server config set provider tts mock -f - <<'YAML'
type: mock
YAML

samtal-server config set provider vad silero -f - <<'YAML'
type: silero
YAML

samtal-server config set agent-defaults -f - <<'YAML'
llm: mock
asr: whisper
tts: mock
vad: silero
YAML

samtal-server config set agent assistant -f - <<'YAML'
prompt: A local-engine configuration.
YAML

samtal-server config set-default-agent assistant

stop_server
