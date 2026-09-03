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
# One document, which is what examples/presets/ holds too. This one is
# not a preset because a preset is a starting point somebody adapts, and
# this is a specific deployment's measured settings: a CPU quota, a
# language ladder, a Swedish voice, and the deliberate absence of a
# default agent. What it shares with a preset is the shape, and the
# shape is the point: the import orders the writes, so there is no
# creation order to get right, and the document is refused whole if
# anything in it will not resolve rather than leaving half a deployment
# behind.
#
# Run it against a running server. `vinga-server config` writes through
# the configuration API, so the natural place is inside the container
# that serves it, where the token variable and the loopback address are
# already in the environment:
#
#   docker exec -i <container> sh < config.deploy.example.sh
#
# From outside, name the API and carry the token yourself. It is the
# value of the variable server.api.secret_env names, VINGA_API_SECRET by
# default, and it is the same one the server was started with:
#
#   VINGA_API_URL=https://vinga.example/api \
#   VINGA_API_SECRET=... sh config.deploy.example.sh
#
# The token grants everything the API can do, so the client refuses a
# plain http:// connection to a host that is not a loopback address:
# reach it over TLS, through a tunnel that terminates TLS, or on
# loopback from inside the container.
#
# This writes the document to the store and stops there, which is the
# whole of what an import does. Installing it builds its engines: the
# ASR weights below are a download of minutes on a first run, and a
# deployment chooses when to spend that rather than having a seeding
# script choose. So the last step is yours:
#
#   vinga-server config apply
#
# which reaches the running server with no restart and no conversation
# dropped; the device binding at the foot applies at that board's next
# check-in without being asked at all.
set -eu

vinga-server config import -f - <<'YAML'
providers:
  llm:
    claude:
      type: anthropic
      model: claude-sonnet-5
      # The environment variable holding the key, never the key.
      api_key_env: ANTHROPIC_API_KEY

  asr:
    whisper:
      type: faster_whisper
      # Weights download into /data at first start, so the first boot
      # takes minutes and later ones seconds.
      model: small
      device: cpu
      compute_type: int8
      # The engine sizes its thread pool from the host's core count and
      # ignores container CPU quotas: set this to the quota, and keep the
      # two moving together, because drift between them is exactly the
      # throttling this key exists to prevent. A non-zero value overrides
      # OMP_NUM_THREADS for this engine; the variable still steers the
      # process's other native libraries.
      cpu_threads: 3
      # Strip non-speech inside the ASR call before decoding. Cuts both
      # latency and hallucinations on silence-padded utterances.
      vad_filter: true
      # Feeding each window's text into the next is the documented cause
      # of repetition loops; false is the standard mitigation.
      condition_on_previous_text: false
      # A short fallback ladder for failed decodes. The engine's six-step
      # default can retry one bad utterance six times over, and a voice
      # UI feels that worst case.
      temperature: [0.0, 0.2]
      # Detect the language once per session and reuse the first
      # confident answer, so later turns skip the whole detection pass.
      # Below the floor the guess is distrusted: the utterance decodes as
      # the fallback language instead, and nothing is locked. Keep the
      # fallback a language the agent's voice speaks: whatever is
      # decoded, that one voice renders it.
      language_detect: once
      language_confidence_floor: 0.6
      language_fallback: sv

  tts:
    piper:
      type: piper
      # Voice name from the Piper voice collection, downloaded into /data
      # at first start.
      voice: sv_SE-nst-medium

  vad:
    silero:
      type: silero
      # Speech probability threshold, default 0.5. In a noisy room this
      # is the companion knob to the server's barge-in gates: raise it
      # toward 0.6 or 0.7 so less noise counts as speech at all, instead
      # of leaning on the gates to catch it later.
      # threshold: 0.5
      #
      # The silence that ends an utterance, and the biggest remaining
      # deployment-decided latency knob: it sits directly in front of
      # every reply. 500 to 600 is a meaningful cut from the 700 default;
      # the trade is clipping slow speakers mid-sentence. Caution: the
      # shorter this gets, the more sentences endpoint at a mid-sentence
      # pause. The barge-in merge stitches a chopped sentence back
      # together, but only by restarting the reply, so do not shorten
      # this further in a deployment that interrupts too much already.
      # trailing_silence_ms: 600

agent_defaults:
  llm: claude
  asr: whisper
  vad: silero

agents:
  assistant:
    # State the reply language explicitly: models otherwise pick one by
    # their training bias.
    prompt: >
      You are a helpful voice assistant. Keep replies short, plain, and
      speakable: one or two sentences, no lists, no markdown. Always
      reply in the language the user spoke.
    tts: piper

# Devices are bound by the MAC address they send as Device-Id. There is
# deliberately no default_agent here: leaving it unset is what turns the
# device bindings into an allowlist, so an unknown device resolves to no
# agent, is issued no token, and is refused at the handshake. With the
# OTA endpoint guarded only by its unguessable path, a publicly exposed
# deployment wants that refusal. Omitting the key leaves the setting
# alone, which is what importing does with every section a document does
# not name.
devices:
  "aa:bb:cc:dd:ee:ff":
    - assistant
YAML
