"""The camera pipeline: the GStreamer detection app and its per-run callback state.

Moved out of Garuda_web.py (2026-10). Class bodies are unchanged except that
names belonging to Garuda_web are read through `core`: the live module, handed
over once by bind(). The base classes come straight from hailo_rpi_common,
because a class statement needs them at import, before bind() has run.
Garuda_web imports these classes back under the same names.
"""
import os
import sys
import threading
import time
import traceback

import setproctitle

from hailo_rpi_common import QUEUE, GStreamerApp, app_callback_class

core = None

# No published frame for this long and the pipeline is torn down and started
# again (_run_pipeline in Garuda_web). A stalled element or a camera read that
# never returns raised nothing, so the loop never quit: the live view and every
# detection stayed dead until someone restarted the service.
STALL_RESTART_S = 30.0
STALL_CHECK_S = 5


def bind(module):
    """Called by Garuda_web with itself, before anything here runs."""
    global core
    if core is not None and core is not module:
        # A second copy of Garuda_web (imported under another name) would
        # silently take over the state every method here reads.
        raise RuntimeError(f"{__name__} is already bound to {core.__name__}; "
                           f"refusing a second copy, {module.__name__}")
    core = module


class user_app_callback_class(app_callback_class):
    def __init__(self):
        super().__init__()
        self.person_detected = False
        self.danger_labels = list(core.STATE.config.danger_labels)
        # Override with a threading-safe lock-based store
        # (base class uses multiprocessing.Queue which breaks across threads)
        self._frame = None
        self._flock = threading.Lock()

    def set_frame(self, frame):
        with self._flock:
            self._frame = frame

    def get_frame(self):
        with self._flock:
            return self._frame


class GStreamerDetectionApp(GStreamerApp):
    def __init__(self, args, user_data):
        # Force frame capture for MJPEG stream; suppress display
        args.use_frame = True
        args.show_fps = False
        super().__init__(args, user_data)
        self.batch_size = 1
        self.network_width = 640
        self.network_height = 640
        self.network_format = "RGB"
        nms_score_threshold = 0.25
        nms_iou_threshold = 0.45

        new_postprocess_path = os.path.join(self.current_path, '../resources/libyolo_hailortpp_post.so')
        if os.path.exists(new_postprocess_path):
            self.default_postprocess_so = new_postprocess_path
        else:
            self.default_postprocess_so = os.path.join(self.postprocess_dir, 'libyolo_hailortpp_post.so')

        if args.hef_path is not None:
            self.hef_path = args.hef_path
        elif args.network == "yolov8s":
            self.hef_path = os.path.join(self.current_path, '../resources/yolov8s_h8l.hef')
        elif args.network == "yolov6n":
            self.hef_path = os.path.join(self.current_path, '../resources/yolov6n.hef')
        elif args.network == "yolox_s_leaky":
            self.hef_path = os.path.join(self.current_path, '../resources/yolox_s_leaky_h8l_mz.hef')
        else:
            raise ValueError("Invalid network type")

        if args.labels_json:
            self.labels_config = f' config-path={args.labels_json} '
            if not os.path.exists(new_postprocess_path):
                print("New postprocess .so file is missing. Required for custom labels.")
                sys.exit(1)
        else:
            self.labels_config = ''

        self.app_callback = core.app_callback
        # When using libyolo_hailortpp_post.so with a custom labels config, the HEF
        # runs NMS internally (HailortPP mode). Adding output-format-type=FLOAT32
        # conflicts with that and silently drops all detections (Knife, Hammer, etc.).
        # Only set FLOAT32 for standard models that rely on hailofilter for NMS.
        if args.labels_json:
            self.thresholds_str = (
                f"nms-score-threshold={nms_score_threshold} "
                f"nms-iou-threshold={nms_iou_threshold}"
            )
        else:
            self.thresholds_str = (
                f"nms-score-threshold={nms_score_threshold} "
                f"nms-iou-threshold={nms_iou_threshold} "
                f"output-format-type=HAILO_FORMAT_TYPE_FLOAT32"
            )
        setproctitle.setproctitle("Garuda Web App")
        # Use fakesink — no display needed, frames captured via MJPEG callback
        self.video_sink = "fakesink"
        self.create_pipeline()

    def run(self):
        """Override base run() to skip cv2 display subprocess (web mode uses MJPEG)."""
        from hailo_rpi_common import disable_qos
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self.bus_call, self.loop)

        identity = self.pipeline.get_by_name("identity_callback")
        if identity:
            identity_pad = identity.get_static_pad("src")
            identity_pad.add_probe(core.Gst.PadProbeType.BUFFER, self.app_callback, self.user_data)

        disable_qos(self.pipeline)
        self.pipeline.set_state(core.Gst.State.PLAYING)

        camera_stop, camera_thread = threading.Event(), None
        appsrc = self.pipeline.get_by_name("app_source")
        if appsrc is not None:
            camera_thread = threading.Thread(target=self._feed_pi_camera,
                                             args=(appsrc, camera_stop), daemon=True)
            camera_thread.start()

        if self.options_menu.dump_dot:
            core.GLib.timeout_add_seconds(3, self.dump_dot_file)

        self._started_at = time.time()
        core.GLib.timeout_add_seconds(STALL_CHECK_S, self._check_stall)

        try:
            self.loop.run()
        except Exception:
            pass

        self.user_data.running = False
        # The camera must be closed before the next pipeline opens it again.
        camera_stop.set()
        if camera_thread is not None:
            camera_thread.join(timeout=8)
        self.pipeline.set_state(core.Gst.State.NULL)

    def _stalled(self, now=None):
        """True when this run has gone STALL_RESTART_S without publishing a frame."""
        now = time.time() if now is None else now
        last = max(self._started_at, core.STATE.camera.frame_ts)
        return now - last > STALL_RESTART_S

    def _check_stall(self):
        if not self._stalled():
            return True                      # GLib: keep the timer
        core.log_system_update(
            f"[CAMERA] No frame for {int(STALL_RESTART_S)} s: restarting the camera pipeline.")
        self.loop.quit()
        return False

    def _feed_pi_camera(self, appsrc, stop):
        """Capture from the Pi camera with picamera2 and push frames into appsrc.

        Until 2026-10 the pipeline used libcamerasrc. That GStreamer plugin
        (libcamera 0.5.2) aborts the whole process on an internal assertion
        (a request completes while its queue is empty) when the Pi is busy,
        taking the web server down with it: seven times on 2026-10-01 alone.
        picamera2 talks to the same camera without that plugin, and a failure
        here is an ordinary Python exception: the pipeline loop is asked to
        quit and _run_pipeline starts a fresh one.
        """
        picam2 = None
        try:
            from picamera2 import Picamera2
            # appsrc has to announce what it sends; the capsfilter after it
            # only checks, it does not describe the buffers.
            appsrc.set_property("caps", core.Gst.Caps.from_string(
                f"video/x-raw, format={self.network_format}, width={core.PI_CAMERA_SIZE[0]}, "
                f"height={core.PI_CAMERA_SIZE[1]}, framerate={core.PI_CAMERA_FPS}/1, pixel-aspect-ratio=1/1"))
            picam2 = Picamera2()
            # libcamera names formats back to front: "BGR888" is R,G,B in memory,
            # which is what the pipeline's video/x-raw format=RGB expects.
            picam2.configure(picam2.create_video_configuration(
                main={"size": core.PI_CAMERA_SIZE, "format": "BGR888"},
                controls={"FrameRate": core.PI_CAMERA_FPS}, buffer_count=4, queue=False))
            picam2.start()
            while not stop.is_set():
                frame = picam2.capture_array("main")
                ret = appsrc.emit("push-buffer", core.Gst.Buffer.new_wrapped(frame.tobytes()))
                if ret not in (core.Gst.FlowReturn.OK, core.Gst.FlowReturn.FLUSHING):
                    raise RuntimeError(f"appsrc refused a frame: {ret.value_nick}")
        except Exception as exc:
            if not stop.is_set():
                core.log_system_update(f"[CAMERA] Pi camera feed failed: {type(exc).__name__}: {exc}")
                traceback.print_exc()
                core.GLib.idle_add(self.loop.quit)
        finally:
            if picam2 is not None:
                for close in (picam2.stop, picam2.close):
                    try:
                        close()
                    except Exception:
                        pass

    def _whole_buffer_cropper(self):
        """TAPPAS's whole-frame cropper, or None when this install lacks it."""
        path = os.path.join(self.postprocess_dir or "", "cropping_algorithms", "libwhole_buffer.so")
        return path if os.path.exists(path) else None

    def get_pipeline_string(self):
        """The camera at its own size for the picture, a 640x640 copy for the network.

        The whole stream used to be scaled to 640x640 before the tee, so the
        live view, snapshots and saved clips were a 16:9 picture squeezed into
        a square. Now hailocropper hands the network a letterboxed 640x640
        copy and hailoaggregator puts its detections back on the full frame
        (boxes are fractions of the frame, so they still line up). Without
        TAPPAS's whole-buffer cropper it falls back to the old square stream.
        """
        if self.source_type == "rpi":
            # Frames arrive from _feed_pi_camera (picamera2) already RGB at
            # this size. Leaky: a late frame is dropped, never queued.
            source_element = (
                "appsrc name=app_source is-live=true do-timestamp=true format=time "
                "max-buffers=2 leaky-type=downstream ! "
                f"video/x-raw, format={self.network_format}, width={core.PI_CAMERA_SIZE[0]}, "
                f"height={core.PI_CAMERA_SIZE[1]}, framerate={core.PI_CAMERA_FPS}/1 ! "
            )
        elif self.source_type == "usb":
            source_element = (
                f"v4l2src device={self.video_source} name=src_0 ! "
                "video/x-raw, width=640, height=480, framerate=30/1 ! "
                + QUEUE("queue_src_convert")
                + "videoconvert n-threads=2 name=src_convert qos=false ! "
                f"video/x-raw, format={self.network_format} ! "
            )
        else:
            source_element = (
                f"filesrc location={self.video_source} name=src_0 ! "
                + QUEUE("queue_dec264")
                + "qtdemux ! h264parse ! avdec_h264 max-threads=2 ! "
                "video/x-raw, format=I420 ! "
                + QUEUE("queue_src_convert")
                + "videoconvert n-threads=2 name=src_convert qos=false ! "
                f"video/x-raw, format={self.network_format} ! "
            )

        network = (
            f"hailonet hef-path={self.hef_path} batch-size={self.batch_size} "
            f"{self.thresholds_str} force-writable=true ! "
            + QUEUE("queue_hailofilter")
            + f"hailofilter so-path={self.default_postprocess_so} {self.labels_config} qos=false ! "
        )
        # Nothing downstream of the probe is used: the frames the app serves
        # are taken at identity_callback.
        tail = (
            QUEUE("queue_user_callback")
            + "identity name=identity_callback ! "
            "fakesink name=hailo_display sync=false "
        )

        cropper = self._whole_buffer_cropper()
        if cropper:
            return (
                source_element
                + QUEUE("queue_crop")
                + f"hailocropper name=crop so-path={cropper} function-name=create_crops "
                "use-letterbox=true resize-method=inter-area internal-offset=true "
                "hailoaggregator name=agg "
                "crop. ! " + QUEUE("bypass_queue", max_size_buffers=20) + "agg.sink_0 "
                "crop. ! " + QUEUE("queue_hailonet") + network
                + QUEUE("queue_agg") + "agg.sink_1 "
                "agg. ! " + tail
            )

        # Fallback: the old square stream (the picture is squeezed).
        square = (
            QUEUE("queue_src_scale")
            + "videoscale n-threads=2 ! "
            f"video/x-raw, format={self.network_format}, width={self.network_width}, "
            f"height={self.network_height}, pixel-aspect-ratio=1/1 ! "
        )
        return (
            "hailomuxer name=hmux "
            + source_element + square
            + "tee name=t ! "
            + QUEUE("bypass_queue", max_size_buffers=20)
            + "hmux.sink_0 "
            + "t. ! "
            + QUEUE("queue_hailonet")
            + network
            + QUEUE("queue_hmuc")
            + "hmux.sink_1 "
            + "hmux. ! "
            + tail
        )
