# PicToID (picfinder)

Upload a photo → extract metadata → reverse-search it across every major engine,
and collect the pages where it appears — social-media profiles included.
No API keys required for the core flow.

## Run locally

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open http://127.0.0.1:8000

## Run publicly (use from anywhere)

```bash
PICFINDER_KEY=your-secret uvicorn app.main:app --host 0.0.0.0 --port 8000
cloudflared tunnel --url http://127.0.0.1:8000   # gives a https://*.trycloudflare.com URL
```

Then open `https://<tunnel-url>/?key=your-secret`.

- `PICFINDER_KEY` — optional access key; API returns 401 without it (`/api/image/*` stays public so engines can fetch images).
- When the app is reached over a public URL, TinEye/Yandex/Bing fetch the image **directly from the app** — no temp host needed.
- Quick-tunnel URLs change on restart; use a named Cloudflare tunnel (free account) for a permanent address, or deploy the app to a host (Render / Railway / a VPS) and skip the tunnel.

### Docker

```bash
docker build -t pictoid .
docker run -p 8000:8000 -e PICFINDER_KEY=your-secret pictoid
```

Works out of the box on Render, Railway, Fly.io, or any VPS — give the app a
public URL and every engine works without the publish toggle.

## How it works

1. **Upload** → stored in `uploads/`, original filename kept for filename-based searches.
2. **Face detection** — OpenCV YuNet (`FaceDetectorYN`, ONNX model vendored in
   `app/models/`) detects faces during analysis; each face is auto-cropped and can
   be reverse-searched individually — searching the cropped face is what makes
   Lens/Yandex return person results.
3. **Background job** (`POST /api/search` → poll `GET /api/search/{job_id}`) runs:
   - **Google Lens** — server-side upload, returns a live results URL.
   - **Link collection** — scrapes the Lens results page for external URLs
     (with `r.jina.ai` reader fallback); GitHub repo matches added too.
   - **URL-based engines** — TinEye, Yandex, Bing Visual Search, SauceNAO.
     If the app is public they fetch the image from it; otherwise enable
     "publish" to push to uguu.se / tmpfiles.org / catbox.moe.
   - **GitHub** — code-search deep link for the original filename, plus
     API collection when `GITHUB_TOKEN` is set.

## Env vars

| Var | Purpose |
|---|---|
| `PICFINDER_KEY` | Access key for all API endpoints except `/api/image/*` |
| `GITHUB_TOKEN` | Enables GitHub code-search collection |
| `JINA_API_KEY` | Higher rate limits for the reader fallback |
| `OLLAMA_HOST` / `OLLAMA_MODEL` | Local AI image description (default `llama3.2-vision`) |

## API

| Endpoint | Description |
|---|---|
| `POST /api/upload` | multipart upload → `{id, url}` |
| `GET /api/image/{id}` | serve the stored image (public by design) |
| `GET /api/analyze/{id}` | EXIF, GPS, dimensions, dominant colors |
| `GET /api/describe/{id}` | Ollama-generated description |
| `POST /api/search` | `{image_id, publish}` → `{job_id}` (runs in background) |
| `GET /api/search/{job_id}` | job status, steps, engines, collected links |
| `POST /api/links/{id}` | synchronous variant |
| `DELETE /api/image/{id}` | remove the uploaded file |

## GitHub repos that extend this project

- [opencv/opencv_zoo](https://github.com/opencv/opencv_zoo) — source of the YuNet face-detection model used in `app/models/` (MIT licensed). DNN models for better detectors live here.
- [deepinsight/insightface](https://github.com/deepinsight/insightface) — state-of-the-art face embeddings; use for matching faces against **your own** image sets (e.g. "same person in these two photos?").
- [serengil/deepface](https://github.com/serengil/deepface) — face verification + attribute analysis (age/emotion) with a simpler API; alternative local-matcher wiring.
- [ageitgey/face_recognition](https://github.com/ageitgey/face_recognition) — dlib-based face encodings; simplest API for compare-two-faces features.
- [exiftool](https://github.com/exiftool/exiftool) — deeper metadata extraction than Pillow (XMP, maker notes, thumbnails); drop-in upgrade for `exif_utils.py`.
- [sherlock](https://github.com/sherlock-project/sherlock) — once results surface a username, hunt it across 400+ social networks.
- [osintframework/osintframework](https://github.com/lockfale/osint-framework) — map of other search engines worth adding as engines.
- [cloudflared](https://github.com/cloudflare/cloudflared) — the tunnel binary used for public access.
- [Jina AI Reader](https://github.com/jina-ai/reader) — the `r.jina.ai` fallback used for results scraping.
- [okhttp](https://github.com/square/okhttp) — HTTP client used by the companion Android build.

## Notes

- Aggregates **public** reverse-image search — there is no face-recognition
  identity matcher or contact harvester.
- Thin results usually mean the photo isn't publicly indexed.
