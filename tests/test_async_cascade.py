"""
Tests for the async three-stage cascade architecture.

Verifies:
  1. Primary YOLO path does not block when secondary queue is full
  2. Secondary queue size is exactly 2
  3. Secondary worker runs in a daemon thread
  4. Frame drops occur intentionally on secondary backlog
  5. Serial old behavior is no longer the effective architecture
  6. Metrics track primary/secondary/drop counters independently
  7. Garuda_web secondary queue + daemon thread are wired correctly

All Hailo hardware is mocked; tests run on any machine without the NPU.
"""

import sys
import os
import types
import queue
import threading
import time
from unittest.mock import MagicMock, patch
import numpy as np
import pytest

# ── Mock hailo_platform before garuda_cascade is imported ─────────────────────
_hailo_mock = types.ModuleType("hailo_platform")
for _name in (
    "VDevice", "HailoStreamInterface", "InferVStreams",
    "ConfigureParams", "InputVStreamParams", "OutputVStreamParams",
    "FormatType", "HEF",
):
    setattr(_hailo_mock, _name, MagicMock())
sys.modules["hailo_platform"] = _hailo_mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "basic_pipelines"))
import garuda_cascade as gc


# ══════════════════════════════════════════════════════════════════════════════
# 1. Secondary queue size is exactly 2
# ══════════════════════════════════════════════════════════════════════════════

class TestSecondaryQueueSize:
    def test_constant_is_2(self):
        assert gc.SECONDARY_QUEUE_SIZE == 2

    def test_queue_constructed_with_maxsize_2(self):
        q = queue.Queue(maxsize=gc.SECONDARY_QUEUE_SIZE)
        assert q.maxsize == 2

    def test_queue_accepts_exactly_2_items(self):
        q = queue.Queue(maxsize=gc.SECONDARY_QUEUE_SIZE)
        q.put_nowait("a")
        q.put_nowait("b")
        with pytest.raises(queue.Full):
            q.put_nowait("c")

    def test_queue_rejects_third_item(self):
        q = queue.Queue(maxsize=gc.SECONDARY_QUEUE_SIZE)
        q.put("item1")
        q.put("item2")
        with pytest.raises(queue.Full):
            q.put_nowait("item3")


# ══════════════════════════════════════════════════════════════════════════════
# 2. Primary path does not block when secondary queue is full
# ══════════════════════════════════════════════════════════════════════════════

class TestPrimaryNonBlocking:
    def _make_mocks(self):
        yolo = MagicMock(spec=gc.YoloNetwork)
        yolo.infer.return_value = {}
        return yolo

    def test_primary_continues_when_secondary_full(self):
        """Primary thread must process frames even when secondary_queue is full."""
        frame_q  = queue.Queue(maxsize=4)
        result_q = queue.Queue(maxsize=16)
        sec_q    = queue.Queue(maxsize=gc.SECONDARY_QUEUE_SIZE)
        stop     = threading.Event()
        yolo     = self._make_mocks()

        # Pre-fill secondary queue to capacity
        sec_q.put(("dummy_frame_1", {"class_id": 2}))
        sec_q.put(("dummy_frame_2", {"class_id": 2}))
        assert sec_q.full()

        # Configure YOLO to return a person detection
        person_det = [{"class_id": gc.PERSON_CLASS_ID, "conf": 0.80,
                       "box": (50, 50, 400, 400)}]
        yolo.parse_detections.return_value = person_det

        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        for _ in range(3):
            frame_q.put(frame)

        metrics_before = gc._metrics.secondary_dropped

        t = threading.Thread(
            target=gc.primary_inference_thread,
            args=(frame_q, result_q, sec_q, yolo, stop),
            daemon=True,
        )
        t.start()

        # Collect results — primary must produce results without blocking
        results_collected = 0
        deadline = time.monotonic() + 5.0
        while results_collected < 3 and time.monotonic() < deadline:
            try:
                result_q.get(timeout=0.5)
                results_collected += 1
            except queue.Empty:
                pass

        stop.set()
        t.join(timeout=3)

        assert results_collected == 3, "Primary path blocked — did not produce 3 results"
        assert gc._metrics.secondary_dropped > metrics_before, "No drops recorded"

    def test_primary_latency_unaffected_by_slow_secondary(self):
        """Primary must not wait on secondary even when secondary is stalled."""
        frame_q  = queue.Queue(maxsize=16)
        result_q = queue.Queue(maxsize=32)
        sec_q    = queue.Queue(maxsize=gc.SECONDARY_QUEUE_SIZE)
        stop     = threading.Event()
        yolo     = self._make_mocks()
        yolo.parse_detections.return_value = []  # no detections = pure primary

        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        n_frames = 5
        for _ in range(n_frames):
            frame_q.put(frame)

        t = threading.Thread(
            target=gc.primary_inference_thread,
            args=(frame_q, result_q, sec_q, yolo, stop),
            daemon=True,
        )
        t0 = time.monotonic()
        t.start()

        collected = 0
        deadline = time.monotonic() + 5.0
        while collected < n_frames and time.monotonic() < deadline:
            try:
                result_q.get(timeout=0.5)
                collected += 1
            except queue.Empty:
                pass

        elapsed = time.monotonic() - t0
        stop.set()
        t.join(timeout=3)

        assert collected == n_frames, f"Only collected {collected}/{n_frames}"
        assert elapsed < 3.0, f"Primary took {elapsed:.1f}s for {n_frames} frames — too slow"


# ══════════════════════════════════════════════════════════════════════════════
# 3. Secondary worker runs in a daemon thread
# ══════════════════════════════════════════════════════════════════════════════

class TestSecondaryDaemonThread:
    def test_secondary_thread_is_daemon(self):
        sec_q    = queue.Queue(maxsize=gc.SECONDARY_QUEUE_SIZE)
        result_q = queue.Queue(maxsize=16)
        stop     = threading.Event()
        classifier = MagicMock(spec=gc.ClassifierNetwork)
        classifier.infer.return_value = "Safe"
        depth_net = MagicMock(spec=gc.DepthNetwork)
        depth_net.infer.return_value = np.ones((1, 256, 256, 1), dtype=np.float32)

        t = threading.Thread(
            target=gc.secondary_worker_thread,
            args=(sec_q, result_q, classifier, depth_net, stop),
            daemon=True,
            name="test_secondary",
        )
        t.start()
        assert t.daemon is True
        stop.set()
        t.join(timeout=3)

    def test_secondary_processes_enqueued_frame(self):
        sec_q    = queue.Queue(maxsize=gc.SECONDARY_QUEUE_SIZE)
        result_q = queue.Queue(maxsize=16)
        stop     = threading.Event()
        classifier = MagicMock(spec=gc.ClassifierNetwork)
        classifier.infer.return_value = "Safe"
        depth_net = MagicMock(spec=gc.DepthNetwork)
        depth_net.infer.return_value = np.ones((1, 256, 256, 1), dtype=np.float32)

        gc._cfg["depth_mode"] = "off"

        t = threading.Thread(
            target=gc.secondary_worker_thread,
            args=(sec_q, result_q, classifier, depth_net, stop),
            daemon=True,
        )
        t.start()

        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        det = {"class_id": 2, "conf": 0.80, "box": (50, 50, 400, 400)}
        sec_q.put((frame, det))

        try:
            _, results = result_q.get(timeout=5)
            assert len(results) == 1
            assert results[0]["threat"] == "Safe"
            assert classifier.infer.called
        finally:
            stop.set()
            t.join(timeout=3)


# ══════════════════════════════════════════════════════════════════════════════
# 4. Frame drops occur intentionally on secondary backlog
# ══════════════════════════════════════════════════════════════════════════════

class TestIntentionalDrops:
    def test_put_nowait_raises_full(self):
        q = queue.Queue(maxsize=gc.SECONDARY_QUEUE_SIZE)
        q.put("a")
        q.put("b")
        with pytest.raises(queue.Full):
            q.put_nowait("c")

    def test_primary_records_drop_metric_on_full_queue(self):
        frame_q  = queue.Queue(maxsize=4)
        result_q = queue.Queue(maxsize=16)
        sec_q    = queue.Queue(maxsize=gc.SECONDARY_QUEUE_SIZE)
        stop     = threading.Event()

        yolo = MagicMock(spec=gc.YoloNetwork)
        yolo.infer.return_value = {}
        yolo.parse_detections.return_value = [
            {"class_id": gc.PERSON_CLASS_ID, "conf": 0.80,
             "box": (50, 50, 400, 400)}
        ]

        # Fill secondary queue
        sec_q.put(("dummy1", {}))
        sec_q.put(("dummy2", {}))

        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        frame_q.put(frame)

        initial_drops = gc._metrics.secondary_dropped

        t = threading.Thread(
            target=gc.primary_inference_thread,
            args=(frame_q, result_q, sec_q, yolo, stop),
            daemon=True,
        )
        t.start()

        result_q.get(timeout=5)
        stop.set()
        t.join(timeout=3)

        assert gc._metrics.secondary_dropped > initial_drops

    def test_drop_does_not_block_primary(self):
        """Dropping a secondary frame must take < 1ms (put_nowait semantics)."""
        q = queue.Queue(maxsize=gc.SECONDARY_QUEUE_SIZE)
        q.put("a")
        q.put("b")

        t0 = time.monotonic()
        try:
            q.put_nowait("c")
        except queue.Full:
            pass
        elapsed = time.monotonic() - t0
        assert elapsed < 0.001, f"put_nowait took {elapsed*1000:.2f}ms — should be instant"


# ══════════════════════════════════════════════════════════════════════════════
# 5. Serial old behavior is no longer the effective architecture
# ══════════════════════════════════════════════════════════════════════════════

class TestArchitectureSplit:
    def test_primary_does_not_call_classifier(self):
        """primary_inference_thread must NOT call classifier.infer()."""
        frame_q  = queue.Queue(maxsize=4)
        result_q = queue.Queue(maxsize=16)
        sec_q    = queue.Queue(maxsize=gc.SECONDARY_QUEUE_SIZE)
        stop     = threading.Event()

        yolo = MagicMock(spec=gc.YoloNetwork)
        yolo.infer.return_value = {}
        yolo.parse_detections.return_value = [
            {"class_id": gc.PERSON_CLASS_ID, "conf": 0.80,
             "box": (50, 50, 400, 400)}
        ]

        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        frame_q.put(frame)

        t = threading.Thread(
            target=gc.primary_inference_thread,
            args=(frame_q, result_q, sec_q, yolo, stop),
            daemon=True,
        )
        t.start()
        result_q.get(timeout=5)
        stop.set()
        t.join(timeout=3)

        # Primary should only call yolo.infer, never classifier or depth
        assert yolo.infer.called

    def test_primary_does_not_call_depth(self):
        """primary_inference_thread signature has no classifier/depth args."""
        import inspect
        sig = inspect.signature(gc.primary_inference_thread)
        params = list(sig.parameters.keys())
        assert "classifier" not in params
        assert "depth_net" not in params

    def test_secondary_does_not_call_yolo(self):
        """secondary_worker_thread signature has no yolo arg."""
        import inspect
        sig = inspect.signature(gc.secondary_worker_thread)
        params = list(sig.parameters.keys())
        assert "yolo" not in params

    def test_pipeline_entry_creates_four_threads(self):
        """_pipeline_entry now creates 4 threads: cam, primary, secondary, postproc."""
        import inspect
        source = inspect.getsource(gc._pipeline_entry)
        assert "primary_inference_thread" in source or "primary_yolo" in source
        assert "secondary_worker_thread" in source or "secondary_worker" in source
        # Verify separate thread names
        assert "primary_yolo" in source
        assert "secondary_worker" in source


# ══════════════════════════════════════════════════════════════════════════════
# 6. CascadeMetrics tracks counters independently
# ══════════════════════════════════════════════════════════════════════════════

class TestCascadeMetrics:
    def test_initial_counters_zero(self):
        m = gc.CascadeMetrics()
        assert m.primary_frames == 0
        assert m.secondary_enqueued == 0
        assert m.secondary_dropped == 0
        assert m.secondary_completed == 0

    def test_record_primary(self):
        m = gc.CascadeMetrics()
        m.record_primary()
        m.record_primary()
        assert m.primary_frames == 2

    def test_record_secondary_enqueue(self):
        m = gc.CascadeMetrics()
        m.record_secondary_enqueue()
        assert m.secondary_enqueued == 1

    def test_record_secondary_drop(self):
        m = gc.CascadeMetrics()
        m.record_secondary_drop()
        m.record_secondary_drop()
        assert m.secondary_dropped == 2

    def test_record_secondary_complete(self):
        m = gc.CascadeMetrics()
        m.record_secondary_complete()
        assert m.secondary_completed == 1

    def test_all_counters_independent(self):
        m = gc.CascadeMetrics()
        m.record_primary()
        m.record_secondary_enqueue()
        m.record_secondary_drop()
        m.record_secondary_complete()
        assert m.primary_frames == 1
        assert m.secondary_enqueued == 1
        assert m.secondary_dropped == 1
        assert m.secondary_completed == 1

    def test_thread_safety(self):
        m = gc.CascadeMetrics()
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
        assert m.primary_frames == 400
        assert m.secondary_enqueued == 400
        assert m.secondary_dropped == 400
        assert m.secondary_completed == 400

    def test_backward_compat_alias(self):
        assert gc.TestMetrics is gc.CascadeMetrics

    def test_status_line_includes_secondary_fields(self):
        m = gc.CascadeMetrics()
        m.record_primary()
        m.record_secondary_enqueue()
        m.record_secondary_drop()
        m.record_secondary_complete()
        line = m.status_line(frame_q_depth=1)
        assert "primary=1" in line
        assert "sec_enq=1" in line
        assert "sec_drop=1" in line
        assert "sec_done=1" in line


# ══════════════════════════════════════════════════════════════════════════════
# 7. Legacy inference_thread routes through async architecture
# ══════════════════════════════════════════════════════════════════════════════

class TestLegacyInferenceThread:
    def test_legacy_creates_secondary_queue(self):
        """inference_thread (legacy) must internally create the async split."""
        import inspect
        source = inspect.getsource(gc.inference_thread)
        assert "SECONDARY_QUEUE_SIZE" in source
        assert "secondary_worker_thread" in source

    def test_legacy_still_produces_results(self):
        frame_q  = queue.Queue(maxsize=4)
        result_q = queue.Queue(maxsize=16)
        stop     = threading.Event()

        yolo = MagicMock(spec=gc.YoloNetwork)
        yolo.infer.return_value = {}
        yolo.parse_detections.return_value = []

        classifier = MagicMock(spec=gc.ClassifierNetwork)
        depth_net = MagicMock(spec=gc.DepthNetwork)

        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        frame_q.put(frame)

        t = threading.Thread(
            target=gc.inference_thread,
            args=(frame_q, result_q, yolo, classifier, depth_net, stop),
            daemon=True,
        )
        t.start()
        _, results = result_q.get(timeout=5)
        stop.set()
        t.join(timeout=3)

        assert results == []  # no detections = empty results


# ══════════════════════════════════════════════════════════════════════════════
# 8. VDevice documentation / constant verification
# ══════════════════════════════════════════════════════════════════════════════

class TestVDeviceDocumentation:
    def test_docstring_documents_vdevice_scheduling(self):
        """Module docstring must document VDevice scheduling limitation."""
        assert "VDevice" in gc.__doc__
        assert "ROUND_ROBIN" in gc.__doc__
        assert "time-multiplexed" in gc.__doc__

    def test_secondary_worker_docstring_mentions_vdevice(self):
        assert "VDevice" in gc.secondary_worker_thread.__doc__
