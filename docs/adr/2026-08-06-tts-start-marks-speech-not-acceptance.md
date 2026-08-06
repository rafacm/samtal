# `tts start` marks speech, not acceptance of the turn

**Status:** Accepted (2026-08-06)

## Context

`tts start` went out in `_reply` as soon as transcription finished,
before the LLM ran. It was doing two jobs at once: telling the device a
reply is about to be spoken, and acknowledging that the turn had been
accepted.

A field session made the difference matter. A post-handover generation
took 19.04 s against a session median of 1.18 s, and for all of it the
board displayed 说话中… and played nothing. The same timeline carried a
`device aborted (no reason)`, which
[#55](https://github.com/rafacm/samtal/issues/55) suspected was not the
firmware giving up but the user pressing the conversation button at a
device that claimed to be talking, since a reasonless abort is what
`HandleToggleChatEvent` sends when the device believes it is speaking.

The issue recorded this as a real design question rather than an
obvious fix, because `tts start` also gates how an auto-mode device
re-arms its listening: it waits for the matching `tts stop`. Moving the
message risked stranding a device in a way that looks fine in the
server log and is silent on the hardware, so it was decided on the
board rather than by reasoning.

## What the board showed

A Waveshare ESP32-S3-Touch-LCD-1.54 on firmware 2.4.0, against a server
whose LLM stalled 20 s before its first token, with the firmware's own
state machine read over serial.

Before, the state changed at transcription, and the button press landed
in it:

```
listening -> speaking          at the transcript, nothing playing
Application: Abort speaking    7.1 s later, the button
speaking  -> listening
```

The server logged `device aborted (no reason)` at that same instant, so
the inference in #55 is confirmed: the reasonless abort is a user
interrupting a device that was not speaking.

After, with the message moved, the same 20 s stall passed with the
board still in `listening`, and it entered `speaking` only when audio
began:

```
connecting -> listening
listening  -> speaking         20.1 s later, at the first sentence
speaking   -> listening        on tts stop, re-armed by itself
```

## Decision

`tts start` means "audio is about to play", and nothing else. It is
sent when the first sentence of a reply is about to be spoken, at most
once per reply.

A reply that speaks nothing at all (nothing transcribed, or a provider
that failed before any audio) still sends the pair, `start` immediately
before `stop`. The device leaves its speaking state on `tts stop` and
in auto mode that is what re-arms its listening, so a `stop` it was
never told to expect is the one way this change could strand a device.

## Consequences

- A device is idle, not "speaking", while the model is thinking. What
  it displays now matches what it is doing.
- A conversation-button press during a slow generation is a fresh
  utterance rather than an abort of speech that has not started.
- Nothing acknowledges the turn between the transcript and the first
  sentence. The device already has its `stt` message with the
  transcript, which is what tells the user they were heard.
- One window stays open, one stage later: a TTS provider slow to its
  first byte holds the device in its speaking state for that wait,
  which for a host that drops traffic is the synthesis `timeout_s`.
  Measured time to first byte is 129 ms to 884 ms on the providers in
  use, against the 19 s this decision is about, so the shape is the
  same and the scale is not. Closing it means holding `sentence_start`
  back until the first chunk arrives, which reverses a decision #37
  made deliberately: the announcement belongs to the sentence about to
  be spoken, and whether its audio will arrive is not known then. That
  is another device-visible reordering, and belongs on the board
  rather than in reasoning, so it is left open and written down.

- The observability half of this is what makes a stall diagnosable at
  all: the `llm_round` event added alongside it carries `duration_ms`,
  `first_token_ms` and `turns`, which is what separates a slow vendor
  from a growing payload. See
  [JSON log events are the observability surface](2026-08-04-json-logs-are-the-observability-surface.md).
