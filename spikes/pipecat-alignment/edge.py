"""Wiring one device connection onto pipecat's websocket transport.

Adapter code, counted in gate 2: an adoption needs these parameters
and these two processors for every connection, whatever else the
pipeline holds. What is *not* here is the route itself, the app, and
the lifecycle around it, because samtal-server already owns those.

The parameters that are not defaults each encode something about the
device rather than a preference:

- `audio_out_10ms_chunks = 6` makes the transport hand the serializer
  exactly one 60 ms xiaozhi frame per write. The default of 4 would
  hand it 40 ms, and since `serialize` may return only one payload per
  call the serializer would have to hold a growing remainder and the
  wire cadence would stop matching the frame cadence.
- `audio_out_end_silence_secs = 0` because xiaozhi ends a reply with a
  `tts stop` message, not with trailing silence.
- `allowed_origins = []` because devices send no Origin header.
"""

import uuid

from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from control import XiaozhiControl
from serializer import (
    DEVICE_SAMPLE_RATE,
    FRAME_MS,
    OUTPUT_SAMPLE_RATE,
    XiaozhiFrameSerializer,
)


def device_edge(websocket, *, paced: bool = True):
    """The transport, the control processor and the session id for one
    device connection."""
    session_id = uuid.uuid4().hex[:8]
    serializer = XiaozhiFrameSerializer(session_id, paced=paced)
    params = FastAPIWebsocketParams(
        audio_in_enabled=True,
        audio_in_sample_rate=DEVICE_SAMPLE_RATE,
        audio_out_enabled=True,
        audio_out_sample_rate=OUTPUT_SAMPLE_RATE,
        audio_out_10ms_chunks=FRAME_MS // 10,
        audio_out_end_silence_secs=0,
        add_wav_header=False,
        serializer=serializer,
        allowed_origins=[],
    )
    transport = FastAPIWebsocketTransport(websocket=websocket, params=params)
    return transport, XiaozhiControl(session_id), session_id
