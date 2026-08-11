#!/bin/sh
# The domain half of the deployment profile in config.deploy.example.yaml:
# the providers it runs on, its agent, and its device binding, with the
# values validated by latency measurements from a live deployment and the
# reasoning behind each one.
#
# A script rather than a block of commented commands, because a
# configuration nobody can run is worse than none: this one is run
# verbatim by tests/integration/test_config_examples.py against a real
# server, so its values are checked rather than merely written down.
#
# Run it against a running server. `samtal-server config` writes through
# the configuration API, so the natural place is inside the container
# that serves it, where the token variable and the loopback address are
# already in the environment:
#
#   docker exec -i <container> sh < config.deploy.example.sh
#
# From outside, name the API and carry the token yourself. It is the
# value of the variable server.api.secret_env names, SAMTAL_API_SECRET by
# default, and it is the same one the server was started with:
#
#   SAMTAL_API_URL=https://samtal.example/api \
#   SAMTAL_API_SECRET=... sh config.deploy.example.sh
#
# The token grants everything the API can do, so the client refuses a
# plain http:// connection to anything but this machine: reach it over
# TLS, through a tunnel that terminates TLS, or on loopback from inside
# the container.
#
# Each write applies at the next server start, so restart the server when
# this finishes. The order matters: a write whose references do not
# resolve is refused, so the providers come before the agent defaults,
# and the agent before the device bound to it.
set -eu

samtal-server config set provider llm claude -f - <<'YAML'
type: anthropic
model: claude-sonnet-5
# The environment variable holding the key, never the key.
api_key_env: ANTHROPIC_API_KEY
YAML

samtal-server config set provider asr whisper -f - <<'YAML'
type: faster_whisper
# Weights download into /data at first start, so the first boot takes
# minutes and later ones seconds.
model: small
device: cpu
compute_type: int8
# The engine sizes its thread pool from the host's core count and ignores
# container CPU quotas: set this to the quota, and keep the two moving
# together, because drift between them is exactly the throttling this key
# exists to prevent. A non-zero value overrides OMP_NUM_THREADS for this
# engine; the variable still steers the process's other native libraries.
cpu_threads: 3
# Strip non-speech inside the ASR call before decoding. Cuts both latency
# and hallucinations on silence-padded utterances.
vad_filter: true
# Feeding each window's text into the next is the documented cause of
# repetition loops; false is the standard mitigation.
condition_on_previous_text: false
# A short fallback ladder for failed decodes. The engine's six-step
# default can retry one bad utterance six times over, and a voice UI
# feels that worst case.
temperature: [0.0, 0.2]
# Detect the language once per session and reuse the first confident
# answer, so later turns skip the whole detection pass. Below the floor
# the guess is distrusted: the utterance decodes as the fallback language
# instead, and nothing is locked. Keep the fallback a language the
# agent's voice speaks: whatever is decoded, that one voice renders it.
language_detect: once
language_confidence_floor: 0.6
language_fallback: sv
YAML

samtal-server config set provider tts piper -f - <<'YAML'
type: piper
# Voice name from the Piper voice collection, downloaded into /data at
# first start.
voice: sv_SE-nst-medium
YAML

samtal-server config set provider vad silero -f - <<'YAML'
type: silero
# Speech probability threshold, default 0.5. In a noisy room this is the
# companion knob to the server's barge-in gates: raise it toward 0.6 or
# 0.7 so less noise counts as speech at all, instead of leaning on the
# gates to catch it later.
# threshold: 0.5
#
# The silence that ends an utterance, and the biggest remaining
# deployment-decided latency knob: it sits directly in front of every
# reply. 500 to 600 is a meaningful cut from the 700 default; the trade
# is clipping slow speakers mid-sentence. Caution: the shorter this gets,
# the more sentences endpoint at a mid-sentence pause. The barge-in merge
# stitches a chopped sentence back together, but only by restarting the
# reply, so do not shorten this further in a deployment that interrupts
# too much already.
# trailing_silence_ms: 600
YAML

samtal-server config set agent-defaults -f - <<'YAML'
llm: claude
asr: whisper
vad: silero
YAML

samtal-server config set agent assistant -f - <<'YAML'
# State the reply language explicitly: models otherwise pick one by their
# training bias.
prompt: >
  You are a helpful voice assistant. Keep replies short, plain, and
  speakable: one or two sentences, no lists, no markdown. Always reply in
  the language the user spoke.
tts: piper
YAML

# Devices are bound by the MAC address they send as Device-Id. There is
# deliberately no default agent: leaving it unset is what turns the device
# bindings into an allowlist, so an unknown device resolves to no agent,
# is issued no token, and is refused at the handshake. With the OTA
# endpoint guarded only by its unguessable path, a publicly exposed
# deployment wants that refusal.
samtal-server config bind-device aa:bb:cc:dd:ee:ff assistant
