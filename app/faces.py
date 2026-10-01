"""Face detection + cropping via OpenCV YuNet (ONNX, no heavy deps)."""
from __future__ import annotations

from pathlib import Path

import cv2

MODEL = Path(__file__).resolve().parent / "models" / "face_detection_yunet_2023mar.onnx"
_detector = None


def _get_detector(w: int, h: int):
    global _detector
    if _detector is None:
        _detector = cv2.FaceDetectorYN_create(str(MODEL), "", (w, h), 0.6)
    _detector.setInputSize((w, h))
    return _detector


def detect_and_crop(path: str, out_dir, image_id: str) -> list[dict]:
    """Detect faces, save each as {image_id}_face{i}.jpg, return metadata."""
    img = cv2.imread(path)
    if img is None:
        return []
    ih, iw = img.shape[:2]
    _, results = _get_detector(iw, ih).detect(img)
    out = []
    if results is None:
        return out
    for i, row in enumerate(results):
        x, y, w, h, score = int(row[0]), int(row[1]), int(row[2]), int(row[3]), float(row[-1])
        px, py = int(w * 0.35), int(h * 0.35)
        x1, y1 = max(0, x - px), max(0, y - py)
        x2, y2 = min(iw, x + w + px), min(ih, y + h + py)
        face_id = f"{image_id}_face{i}"
        cv2.imwrite(str(out_dir / f"{face_id}.jpg"), img[y1:y2, x1:x2])
        out.append({
            "id": face_id,
            "url": f"/api/image/{face_id}",
            "score": round(score, 3),
            "box": {"x": x, "y": y, "w": w, "h": h},
        })
    return out
