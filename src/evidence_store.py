'Classify, name and save evidence without overwriting images.'
import os
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

CATEGORIES = {"B": 'Appearance', "E1": 'RTSP unavailable', "E2": 'IR-CUT defect', "Z": 'Other'}


def safe_component(value):
    value = str(value or "UNKNOWN")
    return "".join("_" if ch in '<>:"/\\|?*' or ord(ch) < 32 else ch
                   for ch in value).rstrip(" .")[:64] or "UNKNOWN"


def organize_records(base_dir):
    'Migrate the legacy CSV and retain any conflicting file under a different name.'
    folder = os.path.join(base_dir, "records")
    os.makedirs(folder, exist_ok=True)
    for filename in ("camera_inspection.csv", "step1_evidence.csv"):
        source = os.path.join(base_dir, filename)
        destination = os.path.join(folder, filename)
        if not os.path.isfile(source):
            continue
        if os.path.exists(destination):
            destination = os.path.join(folder, f"{filename[:-4]}_legacy_{datetime.now():%Y%m%d_%H%M%S_%f}.csv")
        os.rename(source, destination)


def evidence_path(directory, category, camera, extension, now=None):
    if category not in CATEGORIES:
        raise ValueError(f"Unknown evidence category: {category}")
    now = now or datetime.now()
    folder = os.path.join(directory, now.strftime("%Y-%m-%d"), category)
    os.makedirs(folder, exist_ok=True)
    mac = safe_component(str(camera.mac).replace(":", "-"))
    name = f"{category}-{safe_component(camera.sn)}_{mac}_{now:%Y%m%d_%H%M%S_%f}"
    path = os.path.join(folder, f"{name}.{extension}")
    suffix = 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{name}_{suffix:02d}.{extension}")
        suffix += 1
    return path


def save_image(directory, category, camera, image, extension="png"):
    while True:
        path = evidence_path(directory, category, camera, extension)
        try:
            target = open(path, "xb")
        except FileExistsError:
            continue
        try:
            with target:
                image.save(target, format="JPEG" if extension == "jpg" else "PNG",
                           **({"quality": 95} if extension == "jpg" else {}))
        except Exception:
            os.unlink(path)
            raise
        return path


def connection_failure_image(camera, status):
    'Clearly mark connection-failure evidence as a diagram, not a camera image.'
    image = Image.new("RGB", (1280, 720), "#17212B")
    draw = ImageDraw.Draw(image)
    font_path = "meiryo.ttc"
    try:
        title_font = ImageFont.truetype(font_path, 36)
        font = ImageFont.truetype(font_path, 26)
    except OSError:
        title_font = font = ImageFont.load_default()
    draw.text((48, 40), 'E1 - RTSP unavailable (no image)', fill="#FF7777", font=title_font)
    lines = ['Connection-failure record / not a captured camera image', f"SN: {camera.sn}",
             f"MAC: {camera.mac}", f"IP: {camera.ip}",
             f'Timestamp: {datetime.now():%Y-%m-%d %H:%M:%S}', f'Status: {status}']
    for i, line in enumerate(lines):
        draw.text((48, 130 + i * 64), line[:80], fill="white", font=font)
    return image
