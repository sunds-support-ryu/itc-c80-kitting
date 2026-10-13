"""RTSP capture and frame queues, independent of CGI transport.

The existing inspection engine supplies live configuration/callbacks through
context so settings and test injection retain their prior behavior.
"""
import queue
import threading
import time
from urllib.parse import quote
from PIL import Image

class StreamReader:

    def __init__(self, camera, context):
        self._context = context
        self.camera = camera
        self.frames = queue.Queue(maxsize=1)
        self.frame_lock = threading.Lock()
        self.latest_frame = None
        self.latest_frame_time = 0
        self.frame_rate = self._context['exterior'].FrameRate()
        self.last_status = 'RTSP connecting...'
        self.url = f'rtsp://{quote(self._context['USERNAME'], safe='')}:{quote(self._context['PASSWORD'], safe='')}@{camera.ip}:{self._context['RTSP_PORT']}{self._context['RTSP_PATH']}'
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def snapshot(self):
        """Take a frozen original-resolution RGB frame before display resizing."""
        with self.frame_lock:
            frame = None if self.latest_frame is None or time.monotonic() - self.latest_frame_time > 5 else self.latest_frame.copy()
        return None if frame is None else Image.fromarray(frame)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.frame_rate.reset()

    def send_status(self, status):
        self.last_status = status
        if 'NG' in status or 'Error' in status:
            self.frame_rate.reset()
            with self.frame_lock:
                self.latest_frame = None
        self._context['send_debug'](f'RTSP {self.camera.ip}: {status}')
        self._context['event_queue'].put(('stream_status', self.camera.sn, status))

    def create_capture(self):
        self._context['send_debug'](f'RTSP connection rtsp://{self.camera.ip}:{self._context['RTSP_PORT']}{self._context['RTSP_PATH']}')
        cap = self._context['cv2'].VideoCapture(self.url, self._context['cv2'].CAP_FFMPEG, [self._context['cv2'].CAP_PROP_OPEN_TIMEOUT_MSEC, 5000, self._context['cv2'].CAP_PROP_READ_TIMEOUT_MSEC, 5000])
        try:
            cap.set(self._context['cv2'].CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        return cap

    def run(self):
        self.send_status('RTSP connecting...')
        while not self.stop_event.is_set():
            try:
                cap = self.create_capture()
            except Exception as error:
                self._context['send_log'](f'[RTSP Error] {self.camera.ip} / {type(error).__name__}: {self._context['redact'](str(error))}')
                self.send_status('RTSP NG / reconnecting...')
                self.stop_event.wait(self._context['RTSP_RETRY_INTERVAL'])
                continue
            if not cap.isOpened():
                self.send_status('RTSP NG / reconnecting...')
                cap.release()
                time.sleep(self._context['RTSP_RETRY_INTERVAL'])
                continue
            first_frame = True
            while not self.stop_event.is_set():
                ret, frame = cap.read()
                if not ret:
                    self.send_status('Video NG / reconnecting...')
                    break
                if first_frame:
                    self.send_status('Checking video')
                    self._context['send_debug'](f'Video size {frame.shape[1]}x{frame.shape[0]}')
                    first_frame = False
                frame = self._context['cv2'].cvtColor(frame, self._context['cv2'].COLOR_BGR2RGB)
                self.frame_rate.record()
                with self.frame_lock:
                    self.latest_frame = frame
                    self.latest_frame_time = time.monotonic()
                height, width = frame.shape[:2]
                scale = min(self._context['VIDEO_WIDTH'] / width, self._context['VIDEO_HEIGHT'] / height)
                new_width = int(width * scale)
                new_height = int(height * scale)
                frame = self._context['cv2'].resize(frame, (new_width, new_height))
                try:
                    self.frames.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self.frames.put_nowait(frame)
                except queue.Full:
                    pass
            cap.release()
            if not self.stop_event.is_set():
                time.sleep(self._context['RTSP_RETRY_INTERVAL'])
