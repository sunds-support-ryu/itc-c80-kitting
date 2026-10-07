from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import unittest
import numpy as np
from ir_cut_check import Check, analyze_frame


class IRTests(unittest.TestCase):
    def setUp(self):
        self.bw = np.full((90, 160, 3), 100, dtype=np.uint8)
        self.color = np.zeros((90, 160, 3), dtype=np.uint8)
        self.color[:, :, 0] = 220

    def test_algorithm_matches_bw_and_color(self):
        self.assertEqual(analyze_frame(self.bw)[0], 'BW')
        self.assertEqual(analyze_frame(self.color)[0], 'COLOR')

    def test_must_confirm_bw_before_restored_color(self):
        check = Check()
        self.assertEqual(check.feed(self.color, 1), 'COVER')
        check.start(1)
        for moment in (1, 1.5, 2): check.feed(self.color, moment)
        self.assertEqual(check.stage, 'BW')
        for moment in (2.5, 3, 3.5): check.feed(self.bw, moment)
        self.assertEqual(check.stage, 'COLOR')
        for moment in (4, 4.5, 5): check.feed(self.color, moment)
        self.assertEqual(check.stage, 'PASS')

    def test_noise_and_missing_frame_do_not_pass(self):
        check = Check()
        check.start(0)
        check.feed(self.bw, 0)
        check.feed(self.color, .5)
        check.feed(self.bw, 1)
        check.feed(None, 1.5)
        self.assertEqual(check.count, 0)
        self.assertEqual(check.feed(None, 91), 'TIMEOUT')
