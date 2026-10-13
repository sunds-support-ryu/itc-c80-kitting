"""Read-only day/night demo derived from the original test_IR-cut.py."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import argparse
import getpass
import os
import time
from urllib.parse import quote
import cv2
from ir_cut_check import analyze_frame


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ip', required=True)
    parser.add_argument('--user', default='admin')
    args = parser.parse_args()
    password = os.environ.get('ITC_CAMERA_PASSWORD') or getpass.getpass('Camera password: ')
    capture = cv2.VideoCapture(f'rtsp://{quote(args.user, safe="")}:{quote(password, safe="")}@{args.ip}/sub', cv2.CAP_FFMPEG)
    try:
        while capture.isOpened():
            ok, frame = capture.read()
            if not ok: break
            state, diff, sat = analyze_frame(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            print(f'{state} RGB difference={diff:.2f} saturation={sat:.2f}', flush=True)
            time.sleep(.5)
    finally:
        capture.release()


if __name__ == '__main__':
    main()
