"""Encode a camera frame for the browser, at a rate the browser can use.

The GStreamer callback runs at pipeline rate, and a JPEG encode at that rate is
the most expensive thing in it. No browser can use 60fps MJPEG, so the encode is
gated to a fixed interval.

The clip writer shares that gate. cv2.VideoWriter is constructed at a hardcoded
15fps, and writing to it faster than that is what makes saved clips play back in
slow motion.

Nothing is drawn on the frame here. The debug readout that used to be drawn at
this point also reached the WebRTC track and every saved evidence clip, because
all three read the same array.
"""
import time

import cv2

PUBLISH_FPS = 15.0


# A frame that arrives a nanosecond before its deadline is the frame we wanted.
# Without this, a 60Hz callback lands exactly on a 15Hz boundary only up to
# floating-point rounding, and every few boundaries loses by one ulp -- which
# showed up as 13 published frames a second instead of 15.
_EPSILON_S = 1e-9


class FramePublisher:
    def __init__(self, fps=PUBLISH_FPS, clock=time.monotonic):
        self._interval = 1.0 / fps
        self._clock = clock
        self._next = None

    def due(self):
        """True at most `fps` times a second, and always on the first call."""
        now = self._clock()
        if self._next is None:
            self._next = now + self._interval
            return True
        if now + _EPSILON_S < self._next:
            return False
        # Advance by a whole interval rather than to `now`, or the rate drifts
        # slower than asked for. If the callback stalled and we are already past
        # the following deadline, resynchronise instead of firing a burst of
        # catch-up publishes at a pipeline that is already struggling.
        self._next += self._interval
        if self._next <= now:
            self._next = now + self._interval
        return True

    @staticmethod
    def encode(frame_rgb, quality=75):
        """Return (bgr_frame, jpeg_bytes). The frame passed in is never modified."""
        bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
        ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not ok:
            raise ValueError("JPEG encode failed")
        return bgr, buf.tobytes()
