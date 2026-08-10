"""Conversation runtimes: what a conversation is made of.

Everything behind the device-facing boundary, which is everything that
would not exist if the backend were a telephone call to a human:
endpointing, the barge-in gate ladder, the filler, ASR, the LLM tool
loop, sentence splitting, speech synthesis and its lookahead,
conversation history, and agent handover.

One runtime lives here today, the bespoke pipeline. It is the first
runtime behind the boundary by construction rather than by wrapping,
and a second one arrives beside it rather than inside it
([ADR](../../../docs/adr/2026-08-10-normalize-the-hardware-edge.md)).
"""
