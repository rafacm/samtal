"""The audio half of a session capture: the recording's own decode path.

A capture records what happened, which is not the same material the
conversation ran on. The microphone track has to hold every frame that
arrived, including the ones the session's guards dropped, because those
are precisely the frames that explain a misfire; and the reply track
has to hold what the speaker played, which is the Opus that actually
went to the device rather than the PCM that was synthesized. Neither
can be taken off the pipeline's codecs: pushing guarded frames through
the pipeline's decoder would change what the conversation hears, and
the reply never passes through a decoder at all on its way out.

So recording needs three codec objects of its own, and this is the
module that owns them. Built only when a capture opens, so a server
that is not recording pays for none of it, and closed with the capture
it was built around.

The rule every path here follows: a frame the capture cannot read is
not a reason to stop capturing. Nothing raises out of this module.
"""

import asyncio

import av

from vinga_server.audio.opus import OpusDecoder
from vinga_server.audio.resample import Resampler
from vinga_server.capture import CAPTURE_RATE, SessionCapture
from vinga_server.device.boundary import PIPELINE_SAMPLE_RATE
from vinga_server.protocol import framing


class CaptureAudio:
    """One open capture's two audio tracks, fed frame by frame.

    Named for the audio because the other half of a capture, the
    decision track, is written by `events.CaptureTap` from the session's
    event emissions. This side never sees an event; that side never sees
    a codec.

    It is handed a `SessionCapture` and closes it, so the session holds
    one field for the whole of recording's audio rather than a capture
    and three codecs beside it.
    """

    def __init__(
        self, capture: SessionCapture, protocol_version: int, reply_sample_rate: int
    ) -> None:
        self._capture = capture
        # What the microphone track unwraps against. Fixed for the
        # session: the hello settles it before anything opens a capture.
        # The reply track needs none, because a reply is recorded from
        # the packet rather than from the frame it was wrapped in.
        self._protocol_version = protocol_version
        self._decoder = OpusDecoder(sample_rate=PIPELINE_SAMPLE_RATE)
        self._reply_decoder = OpusDecoder(sample_rate=reply_sample_rate)
        self._resampler = Resampler(reply_sample_rate, CAPTURE_RATE)

    def microphone(self, data: bytes) -> None:
        """Decode a mic frame for the capture, whatever the session then
        does with it.

        Its own decoder, not the pipeline's: this one sees every frame
        while the pipeline's sees only the frames that got past the
        guards, and pushing the guarded frames through the pipeline
        decoder would change what the conversation hears."""
        try:
            frame = framing.unwrap(self._protocol_version, data)
            if frame.payload_type != framing.PAYLOAD_OPUS:
                return
            pcm = self._decoder.decode(frame.payload)
        except (framing.FramingError, av.FFmpegError):
            # Already counted as dropped by the caller, and a frame the
            # capture cannot read is not a reason to stop capturing.
            return
        self._capture.microphone(pcm, asyncio.get_running_loop().time())

    def reply(self, packet: bytes) -> None:
        """Record a frame as it is paced out, which is what the speaker
        played rather than what was synthesized. Decoded back from the
        Opus that actually went to the device, and resampled to the
        capture rate so one sample index means one instant in both
        channels."""
        try:
            pcm = self._reply_decoder.decode(packet)
        except av.FFmpegError:
            return
        self._capture.reply(self._resampler.process(pcm), asyncio.get_running_loop().time())

    def close(self) -> None:
        """Finish the recording this was built around. Called after the
        session's last event has been emitted, so the WAV header is
        patched with a length covering everything."""
        self._capture.close()
