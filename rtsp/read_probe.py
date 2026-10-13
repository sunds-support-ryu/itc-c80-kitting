"""Isolated read-only RTSP probe. Credentials arrive over stdin, never argv."""
import json
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import time
from urllib.parse import quote

os.environ['OPENCV_FFMPEG_LOGLEVEL'] = '-8'
os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = 'rtsp_transport;tcp'
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import cv2
from ir_cut_check import analyze_frame


def main():
    settings = json.load(sys.stdin)
    cv2.setLogLevel(0)
    url = 'rtsp://' + quote(settings['user'], safe='') + ':' + quote(settings['password'], safe='') + '@' + settings['ip'] + '/sub'
    start = time.perf_counter()
    capture = cv2.VideoCapture(url, cv2.CAP_FFMPEG,
        [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 10000, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000])
    result = {'opened': capture.isOpened(), 'open_ms': round((time.perf_counter()-start)*1000,3),
              'nominal_stream_fps':capture.get(cv2.CAP_PROP_FPS),'frames': []}
    try:
        for index in range(settings.get('frames', 90)):
            if not capture.isOpened():
                break
            ok, frame = capture.read()
            if not ok:
                break
            stamp = time.perf_counter()
            state, difference, saturation = analyze_frame(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            result['frames'].append({'index':index,'elapsed_ms':round((stamp-start)*1000,3),
                'utc':datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
                'stream_position_ms':capture.get(cv2.CAP_PROP_POS_MSEC),
                'width':frame.shape[1],'height':frame.shape[0],'image_state':state,
                'rgb_difference':round(difference,3),'saturation':round(saturation,3)})
        if result['frames']:
            result['first_frame_ms'] = result['frames'][0]['elapsed_ms']
            span = (result['frames'][-1]['elapsed_ms']-result['frames'][0]['elapsed_ms'])/1000
            result['observed_delivery_fps'] = round((len(result['frames'])-1)/span,3) if span>0 else None
            steady=result['frames'][-30:]
            span=(steady[-1]['elapsed_ms']-steady[0]['elapsed_ms'])/1000
            result['steady_delivery_fps']=round((len(steady)-1)/span,3) if span>0 else None
        print(json.dumps(result), flush=True)
    finally:
        capture.release()


if __name__ == '__main__':
    main()
