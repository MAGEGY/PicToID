"""EXIF / metadata extraction and basic image statistics."""
from __future__ import annotations

import io
from fractions import Fraction

from PIL import Image, ExifTags


def _to_serializable(value):
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="replace")
        except Exception:
            return repr(value)
    if isinstance(value, Fraction):
        return float(value)
    if isinstance(value, tuple):
        return [_to_serializable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _to_serializable(v) for k, v in value.items()}
    try:
        float(value) if not isinstance(value, (str, int, float, bool, type(None))) else None
    except Exception:
        return str(value)
    return value


def _dms_to_decimal(dms, ref: str) -> float | None:
    try:
        d, m, s = (float(v) for v in dms)
        dec = d + m / 60.0 + s / 3600.0
        if ref in ("S", "W"):
            dec = -dec
        return round(dec, 6)
    except Exception:
        return None


def extract_metadata(path: str) -> dict:
    img = Image.open(path)
    meta: dict = {
        "format": img.format,
        "mode": img.mode,
        "width": img.width,
        "height": img.height,
        "megapixels": round(img.width * img.height / 1_000_000, 2),
    }

    # Non-EXIF info (e.g. PNG text chunks)
    info = {k: _to_serializable(v) for k, v in img.info.items() if k != "exif"}
    if info:
        meta["embedded_info"] = info

    exif = img.getexif()
    fields = {}
    for tag_id, value in exif.items():
        name = ExifTags.TAGS.get(tag_id, f"Tag_{tag_id}")
        fields[name] = _to_serializable(value)

    # Sub-IFDs (Exif / GPS)
    for ifd_enum, label in ((ExifTags.IFD.Exif, None), (ExifTags.IFD.Interop, None)):
        try:
            for tag_id, value in exif.get_ifd(ifd_enum).items():
                name = ExifTags.TAGS.get(tag_id, f"Tag_{tag_id}")
                fields[name] = _to_serializable(value)
        except Exception:
            pass

    gps = {}
    try:
        gps_ifd = exif.get_ifd(ExifTags.IFD.GPSInfo)
        gps_tags = {v: k for k, v in ExifTags.GPSTAGS.items()}
        raw = {ExifTags.GPSTAGS.get(k, str(k)): v for k, v in gps_ifd.items()}
        lat = _dms_to_decimal(raw.get("GPSLatitude"), raw.get("GPSLatitudeRef", "N"))
        lon = _dms_to_decimal(raw.get("GPSLongitude"), raw.get("GPSLongitudeRef", "E"))
        if lat is not None and lon is not None:
            gps = {
                "latitude": lat,
                "longitude": lon,
                "maps_url": f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=15/{lat}/{lon}",
            }
        if "GPSAltitude" in raw:
            gps["altitude_m"] = round(float(raw["GPSAltitude"]), 1)
        if "GPSDateStamp" in raw:
            gps["date_stamp"] = str(raw["GPSDateStamp"])
    except Exception:
        pass

    meta["exif"] = fields
    meta["gps"] = gps
    meta["has_exif"] = bool(fields)
    return meta


def dominant_colors(path: str, n: int = 5) -> list[str]:
    img = Image.open(path).convert("RGB")
    img.thumbnail((160, 160))
    q = img.quantize(colors=n, method=Image.Quantize.MEDIANCUT)
    palette = q.getpalette()[: n * 3]
    colors = []
    for i in range(0, len(palette), 3):
        colors.append("#{:02x}{:02x}{:02x}".format(*palette[i : i + 3]))
    return colors
