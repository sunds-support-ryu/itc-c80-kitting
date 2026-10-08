"""Day/night detection adapted from the original test_IR-cut.py; sample: samples/ir_cut_sample.py."""
import cv2
import numpy as np


def analyze_frame(rgb_frame):
    small = cv2.resize(rgb_frame, (160, 90), interpolation=cv2.INTER_AREA)
    image = small.astype(np.float32)
    r, g, b = cv2.split(image)
    difference = float((np.mean(np.abs(r-g)) + np.mean(np.abs(g-b)) + np.mean(np.abs(b-r))) / 3)
    saturation = float(np.mean(cv2.cvtColor(small, cv2.COLOR_RGB2HSV)[:, :, 1]))
    return ('BW' if difference < 4.0 and saturation < 8.0 else 'COLOR'), difference, saturation


class Check:
    def __init__(self):
        self.stage = 'COVER'
        self.count = 0
        self.last_check = None
        self.started = 0
        self.metrics = ''

    def start(self, now):
        self.stage, self.count, self.started, self.last_check = 'BW', 0, now, None

    def feed(self, frame, now):
        if self.stage not in ('BW', 'COLOR'):
            return self.stage
        if now - self.started > 90:
            self.stage = 'TIMEOUT'
            return self.stage
        if frame is None:
            self.count = 0
            return self.stage
        if self.last_check is not None and now - self.last_check < .5:
            return self.stage
        self.last_check = now
        state, diff, saturation = analyze_frame(frame)
        self.count = self.count + 1 if state == self.stage else 0
        self.metrics = f'{state} - RGB difference {diff:.1f} - saturation {saturation:.1f} · {self.count}/3'
        if self.count >= 3:
            if self.stage == 'BW':
                self.stage, self.count, self.started, self.last_check = 'COLOR', 0, now, None
            else:
                self.stage = 'PASS'
        return self.stage
