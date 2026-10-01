from __future__ import annotations

import asyncio
import ipaddress
import mimetypes
import os
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import describe, engines, exif_utils, hosting

BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
MAX_BYTES = 15 * 1024 * 1024

# image_id -> original filename (also persisted as uploads/{id}.name)
FILES: dict[str, str] = {}

app = FastAPI(title="picfinder")

APP_KEY = os.environ.get("PICFINDER_KEY")


@app.middleware("http")
async def key_gate(request: Request, call_next):
    # /api/image must stay public — search engines fetch it without a key
    path = request.url.path
    if APP_KEY and path.startswith("/api/") and not path.startswith("/api/image/"):
        key = request.query_params.get("key") or request.headers.get("x-picfinder-key")
        if key != APP_KEY:
            return JSONResponse({"detail": "invalid or missing key"}, status_code=401)
    return await call_next(request)


def _find_image(image_id: str) -> Path:
    if not image_id or "/" in image_id or "\\" in image_id or ".." in image_id:
        raise HTTPException(400, "invalid image id")
    matches = [p for p in UPLOAD_DIR.glob(f"{image_id}.*") if p.suffix != ".name"]
    if not matches:
        raise HTTPException(404, "image not found")
    return matches[0]


def _filename_of(image_id: str) -> str | None:
    if image_id in FILES:
        return FILES[image_id]
    sidecar = UPLOAD_DIR / f"{image_id}.name"
    if sidecar.exists():
        try:
            return sidecar.read_text(encoding="utf-8").strip() or None
        except Exception:
            pass
    return None


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    ext = Path(file.filename or "img").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(415, f"unsupported file type: {ext}")
    data = await file.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "file too large (15 MB max)")
    image_id = uuid.uuid4().hex
    (UPLOAD_DIR / f"{image_id}{ext}").write_bytes(data)
    if file.filename:
        FILES[image_id] = file.filename
        try:
            (UPLOAD_DIR / f"{image_id}.name").write_text(file.filename, encoding="utf-8")
        except Exception:
            pass
    return {"id": image_id, "filename": file.filename, "url": f"/api/image/{image_id}"}


@app.get("/api/image/{image_id}")
def get_image(image_id: str):
    path = _find_image(image_id)
    media = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    return FileResponse(path, media_type=media)


@app.get("/api/analyze/{image_id}")
def analyze(image_id: str):
    path = _find_image(image_id)
    try:
        meta = exif_utils.extract_metadata(str(path))
        meta["dominant_colors"] = exif_utils.dominant_colors(str(path))
        return meta
    except Exception as e:
        raise HTTPException(400, f"could not analyze image: {e}")


@app.get("/api/describe/{image_id}")
async def describe_image(image_id: str):
    path = _find_image(image_id)
    return await describe.describe_image(str(path))


class LinksRequest(BaseModel):
    publish: bool = False          # upload to temporary public host
    base_url: str | None = None    # public base URL if self-hosted (e.g. https://x.ngrok.app)


@app.post("/api/links/{image_id}")
async def links(image_id: str, req: LinksRequest):
    path = _find_image(image_id)
    out: dict = {"engines": [], "warnings": []}

    # Google Lens works without a public URL via direct upload
    lens_url = await engines.lens_upload_results_url(str(path))
    if lens_url:
        out["engines"].append({
            "id": "google_lens", "name": "Google Lens", "url": lens_url,
            "note": "Image uploaded directly — results page ready.",
        })
        out["found"] = await engines.collect_links(lens_url)
    else:
        out["warnings"].append("Google Lens direct upload failed; enable publish to retry via URL.")

    public_url = None
    if req.publish:
        try:
            public_url, host = await hosting.publish(str(path))
            out["public_url"] = public_url
            out["warnings"].append(f"Image published to {host}.")
        except Exception as e:
            out["warnings"].append(f"Temporary publish failed: {e}")
    elif req.base_url:
        public_url = f"{req.base_url.rstrip('/')}/api/image/{image_id}"
    else:
        out["warnings"].append(
            "TinEye/Yandex/Bing need a public image URL. Enable 'publish' "
            "or run the app behind a public URL."
        )

    if public_url:
        by_id = {e["id"] for e in out["engines"]}
        for eng in engines.url_based_engines(public_url):
            if eng["id"] not in by_id:
                out["engines"].append(eng)

    gh = engines.github_engine(_filename_of(image_id))
    if gh:
        out["engines"].append(gh)

    return out


# ---------- background search jobs ----------

JOBS: dict[str, dict] = {}


def _is_public_base(base: str) -> bool:
    """True if the request base URL is reachable from the internet (not localhost/LAN)."""
    host = base.split("://", 1)[-1].split(":", 1)[0].rstrip("/")
    if host in ("localhost", "::1") or host.endswith((".local", ".internal")):
        return False
    try:
        return not ipaddress.ip_address(host).is_private
    except ValueError:
        return True  # a real hostname — assume public


class SearchRequest(BaseModel):
    image_id: str
    publish: bool = False


@app.post("/api/search")
async def start_search(req: SearchRequest, request: Request):
    path = _find_image(req.image_id)
    job_id = uuid.uuid4().hex
    JOBS[job_id] = {"status": "running", "engines": [], "found": [], "warnings": [], "steps": []}
    base = str(request.base_url).rstrip("/")
    asyncio.create_task(_run_search(job_id, str(path), req.image_id, req.publish, base))
    return {"job_id": job_id}


@app.get("/api/search/{job_id}")
def get_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return job


async def _run_search(job_id: str, path: str, image_id: str, publish: bool, base: str):
    job = JOBS[job_id]

    def step(msg: str):
        job["steps"].append(msg)

    try:
        step("uploading to Google Lens…")
        lens_url = await engines.lens_upload_results_url(path)
        if lens_url:
            job["engines"].append({
                "id": "google_lens", "name": "Google Lens", "url": lens_url,
                "note": "Image uploaded directly — results page ready.",
            })
            step("collecting result links…")
            job["found"] = await engines.collect_links(lens_url)
            step(f"collected {len(job['found'])} links")
        else:
            job["warnings"].append("Google Lens direct upload failed.")

        public_url = None
        if _is_public_base(base):
            public_url = f"{base}/api/image/{image_id}"
            step("app is public — engines will fetch the image from here")
        elif publish:
            step("publishing image to temporary host…")
            try:
                public_url, host = await hosting.publish(path)
                job["public_url"] = public_url
                job["warnings"].append(f"Image published to {host}.")
            except Exception as e:
                job["warnings"].append(f"Temporary publish failed: {e}")
        else:
            job["warnings"].append(
                "TinEye/Yandex/Bing need a public image URL — access the app "
                "via its public URL, or enable publish."
            )

        if public_url:
            step("building engine links…")
            by_id = {e["id"] for e in job["engines"]}
            for eng in engines.url_based_engines(public_url):
                if eng["id"] not in by_id:
                    job["engines"].append(eng)

        # GitHub: deep link always; API collection when GITHUB_TOKEN is set
        filename = _filename_of(image_id)
        gh = engines.github_engine(filename)
        if gh:
            job["engines"].append(gh)
            step("searching GitHub repos…")
            gh_found = await engines.github_code_search(filename)
            job["found"].extend(gh_found)
            if not os.environ.get("GITHUB_TOKEN"):
                job["warnings"].append(
                    "GitHub auto-collection needs GITHUB_TOKEN — deep link added instead."
                )
            elif gh_found:
                step(f"GitHub: {len(gh_found)} repo matches")

        job["status"] = "done"
    except Exception as e:
        job["status"] = "error"
        job["warnings"].append(f"Search failed: {e}")


@app.delete("/api/image/{image_id}")
def delete_image(image_id: str):
    path = _find_image(image_id)
    path.unlink()
    return {"deleted": image_id}


app.mount("/", StaticFiles(directory=BASE_DIR / "static", html=True), name="static")
