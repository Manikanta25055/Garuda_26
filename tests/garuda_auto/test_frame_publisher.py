import numpy as np

from basic_pipelines.garuda_auto.frame_publisher import FramePublisher


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _frame():
    """A frame whose channels differ, so a channel swap is detectable."""
    f = np.zeros((8, 8, 3), dtype=np.uint8)
    f[:, :, 0] = 10    # R
    f[:, :, 1] = 20    # G
    f[:, :, 2] = 30    # B
    return f


def test_first_frame_is_always_due():
    assert FramePublisher(fps=15.0, clock=FakeClock()).due() is True


def test_a_second_frame_in_the_same_instant_is_not_due():
    p = FramePublisher(fps=15.0, clock=FakeClock())
    assert p.due() is True
    assert p.due() is False


def test_due_again_once_the_interval_has_passed():
    clock = FakeClock()
    p = FramePublisher(fps=15.0, clock=clock)
    assert p.due() is True
    clock.t = 1 / 15 - 0.001
    assert p.due() is False
    clock.t = 1 / 15
    assert p.due() is True


def test_sixty_calls_over_one_second_yield_fifteen_publishes():
    """The whole point: pipeline rate in, browser rate out."""
    clock = FakeClock()
    p = FramePublisher(fps=15.0, clock=clock)
    published = 0
    for i in range(60):
        clock.t = i / 60
        if p.due():
            published += 1
    assert published == 15


def test_encode_does_not_modify_the_frame_it_was_given():
    """The frame is shared with the WebRTC track and the clip writer."""
    frame = _frame()
    before = frame.copy()
    FramePublisher.encode(frame)
    assert np.array_equal(frame, before)


def test_encode_returns_bgr():
    bgr, _ = FramePublisher.encode(_frame())
    assert bgr[0, 0, 0] == 30    # B where R was
    assert bgr[0, 0, 1] == 20
    assert bgr[0, 0, 2] == 10


def test_encode_returns_jpeg_bytes():
    _, jpeg = FramePublisher.encode(_frame())
    assert isinstance(jpeg, bytes)
    assert jpeg.startswith(b"\xff\xd8")    # SOI marker


def test_a_stall_does_not_produce_a_burst_of_catch_up_frames():
    """A pipeline that froze must not then publish every frame it owes at once.

    The deadline advances by whole intervals to keep the rate honest, so without
    a resynchronisation a five-second stall would leave seventy-five deadlines in
    the past and fire them back to back at a pipeline already in trouble.
    """
    clock = FakeClock()
    p = FramePublisher(fps=15.0, clock=clock)
    assert p.due() is True

    clock.t = 5.0
    assert p.due() is True      # one frame
    assert p.due() is False     # and not the other seventy-four

    clock.t = 5.0 + 1 / 15
    assert p.due() is True      # back on cadence
