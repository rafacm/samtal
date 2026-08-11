#!/bin/sh
# A domain half that names local engines, used to prove the slim image
# refuses it rather than failing later in some obscure way. Seeding
# succeeds on any image: nothing is imported until a provider is built,
# which is what the boot after this does.
set -eu

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
