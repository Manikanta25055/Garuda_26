"""
Tests for the async secondary-cascade architecture in Garuda_web.py.

The bounded-queue cascade lives in the production web server, not in
``garuda_cascade.py`` (which remains a serial single-thread pipeline used by
the offline INT8 evaluation script). These tests therefore exercise the real
implementation:

  1. Secondary queue size is exactly 2
  2. Enqueue is non-blocking — a full queue drops the frame instead of waiting
  3. The secondary worker runs in a daemon thread and consumes the queue
  4. Drops are intentional and recorded as a distinct metric
  5. Primary/secondary/drop/complete counters are independent and thread-safe
  6. The VDevice scheduling limitation is documented on the worker

Note: the secondary worker's inference body is presently a placeholder (see
``patent_claim_alignment/AR933_Code_vs_Claims_Technical_Note_for_Agent.md``).
These tests cover the queue, thread and drop semantics that are real; they
deliberately do not assert that a second network runs.

All Hailo/GStreamer hardware is mocked by conftest.py, so the tests run on any
machine without the NPU.
"""

import inspect
import queue
import threading
import time

import numpy as np
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'basic_pipelines'))
import Garuda_web as gw


def _wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


# ══════════════════════════════════════════════════════════════════════════════
# 1. Secondary queue size is exactly 2
# ══════════════════════════════════════════════════════════════════════════════

class TestSecondaryQueueSize:
    def test_constant_is_2(self):
        assert gw.SECONDARY_QUEUE_SIZE == 2

    def test_module_queue_uses_the_constant(self):
        assert gw._secondary_queue.maxsize == gw.SECONDARY_QUEUE_SIZE

    def test_queue_accepts_exactly_2_items(self):
        q = queue.Queue(maxsize=gw.SECONDARY_QUEUE_SIZE)
        q.put_nowait("a")
        q.put_nowait("b")
        with pytest.raises(queue.Full):
            q.put_nowait("c")


# ══════════════════════════════════════════════════════════════════════════════
# 2. Enqueue never blocks the primary GStreamer callback
# ══════════════════════════════════════════════════════════════════════════════

class TestPrimaryNonBlocking:
    def test_callback_enqueues_without_blocking(self):
        """The primary callback must use put_nowait and swallow queue.Full."""
        source = inspect.getsource(gw.app_callback)
        assert "_secondary_queue.put_nowait" in source, \
            "primary callback must not block on the secondary queue"
        assert "except _queue_mod.Full" in source, \
            "primary callback must handle a full secondary queue"
        assert "record_secondary_drop" in source, \
            "a dropped frame must be recorded as a metric"
        assert ".put(" not in source.split("_secondary_queue")[1][:80], \
            "primary callback must never use a blocking put"

    def test_drop_path_is_instant(self):
        """Dropping a secondary frame must cost far less than one frame period."""
        q = queue.Queue(maxsize=gw.SECONDARY_QUEUE_SIZE)
        q.put_nowait("a")
        q.put_nowait("b")

        t0 = time.monotonic()
        try:
            q.put_nowait("c")
        except queue.Full:
            pass
        elapsed = time.monotonic() - t0
        assert elapsed < 0.001, f"put_nowait took {elapsed * 1000:.2f}ms — should be instant"


# ══════════════════════════════════════════════════════════════════════════════
# 3. Secondary worker runs in a daemon thread and drains the queue
# ══════════════════════════════════════════════════════════════════════════════

class TestSecondaryDaemonThread:
    def test_thread_is_daemon_and_running(self):
        assert gw._secondary_thread.daemon is True, \
            "secondary worker must not keep the process alive"
        assert gw._secondary_thread.is_alive()
        assert gw._secondary_thread.name == "secondary_cascade"

    def test_worker_consumes_an_enqueued_frame(self):
        before = gw._cascade_metrics.snapshot()["secondary_completed"]
        frame = np.zeros((48, 64, 3), dtype=np.uint8)
        gw._secondary_queue.put((frame, {"label": "person", "confidence": 0.9}))

        assert _wait_for(
            lambda: gw._cascade_metrics.snapshot()["secondary_completed"] > before
        ), "secondary worker did not consume the queued frame"
        assert gw._secondary_queue.empty()

    def test_worker_survives_a_malformed_item(self):
        """A bad queue item must not kill the daemon thread."""
        before = gw._cascade_metrics.snapshot()["secondary_completed"]
        gw._secondary_queue.put((None, "not-a-dict"))

        assert _wait_for(
            lambda: gw._cascade_metrics.snapshot()["secondary_completed"] > before
        ), "worker did not record completion for a malformed item"
        assert gw._secondary_thread.is_alive(), "worker thread died on a malformed item"


# ══════════════════════════════════════════════════════════════════════════════
# 4. Drops are intentional and separately accounted
# ══════════════════════════════════════════════════════════════════════════════

class TestIntentionalDrops:
    def test_full_queue_records_a_drop_not_an_enqueue(self):
        metrics = gw._WebCascadeMetrics()
        q = queue.Queue(maxsize=gw.SECONDARY_QUEUE_SIZE)
        q.put_nowait("a")
        q.put_nowait("b")

        # Same shape as the enqueue site in app_callback.
        try:
            q.put_nowait("c")
            metrics.record_secondary_enqueue()
        except queue.Full:
            metrics.record_secondary_drop()

        snap = metrics.snapshot()
        assert snap["secondary_dropped"] == 1
        assert snap["secondary_enqueued"] == 0


# ══════════════════════════════════════════════════════════════════════════════
# 5. Counters are independent and thread-safe
# ══════════════════════════════════════════════════════════════════════════════

class TestCascadeMetrics:
    def test_initial_counters_zero(self):
        snap = gw._WebCascadeMetrics().snapshot()
        assert snap == {
            "primary_frames": 0,
            "secondary_enqueued": 0,
            "secondary_dropped": 0,
            "secondary_completed": 0,
        }

    def test_all_counters_independent(self):
        m = gw._WebCascadeMetrics()
        m.record_primary()
        m.record_secondary_enqueue()
        m.record_secondary_drop()
        m.record_secondary_complete()
        assert m.snapshot() == {
            "primary_frames": 1,
            "secondary_enqueued": 1,
            "secondary_dropped": 1,
            "secondary_completed": 1,
        }

    def test_thread_safety(self):
        m = gw._WebCascadeMetrics()

        def worker():
            for _ in range(100):
                m.record_primary()
                m.record_secondary_enqueue()
                m.record_secondary_drop()
                m.record_secondary_complete()

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert m.snapshot() == {
            "primary_frames": 400,
            "secondary_enqueued": 400,
            "secondary_dropped": 400,
            "secondary_completed": 400,
        }

    def test_primary_frames_recorded_by_the_callback(self):
        assert "record_primary" in inspect.getsource(gw.app_callback)


# ══════════════════════════════════════════════════════════════════════════════
# 6. VDevice scheduling limitation is documented where it bites
# ══════════════════════════════════════════════════════════════════════════════

class TestVDeviceDocumentation:
    def test_secondary_worker_docstring_mentions_vdevice(self):
        doc = gw._secondary_worker_loop.__doc__ or ""
        assert "VDevice" in doc, \
            "the worker must document why secondary inference needs its own device session"
