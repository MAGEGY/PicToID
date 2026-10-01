"""Reverse image search engines: server-side uploads and URL deep links."""
from __future__ import annotations

import asyncio
import os
import re
import time
from urllib.parse import quote, unquote, urlparse

import httpx
from PIL import Image

LENS_UPLOAD = "https://lens.google.com/v3/upload?stcs={ts}"
LENS_UPLOAD_BY_URL = "https://lens.google.com/uploadbyurl?url={url}"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


async def lens_upload_results_url(path: str) -> str | None:
    """Upload the image to Google Lens directly; return the results-page URL."""
    try:
        with Image.open(path) as im:
            dims = f"{im.width},{im.height}"
        filename = path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
        async with httpx.AsyncClient(
            timeout=60, follow_redirects=False,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            with open(path, "rb") as f:
                resp = await client.post(
                    LENS_UPLOAD.format(ts=int(time.time())),
                    data={"processed_image_dimensions": dims},
                    files={"encoded_image": (filename, f)},
                )
        if resp.status_code in (301, 302, 303, 307, 308):
            return resp.headers.get("location")
        m = re.search(r'https://lens\.google\.com/v3/results\?[^"\'<> ]+', resp.text)
        return m.group(0) if m else None
    except Exception:
        return None


SKIP_DOMAINS = (
    "google.", "gstatic.", "googleapis.", "schema.org", "w3.org", "ggpht.",
    "googleusercontent.", "gmail.", "doubleclick.", "googlesyndication", "google-analytics",
)

JINA_API_KEY = os.environ.get("JINA_API_KEY")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")

SOCIAL_DOMAINS = (
    "facebook.", "instagram.", "twitter.", "x.com", "linkedin.", "tiktok.",
    "reddit.", "pinterest.", "vk.com", "vk.", "tumblr.", "youtube.",
    "github.", "threads.net", "mastodon", "bsky.app", "snapchat.",
    "t.me", "telegram.", "discord.", "flickr.", "medium.",
)


def _basename(path: str) -> str:
    return path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]


async def pimeyes_results_url(path: str) -> str | None:
    """Upload to PimEyes' public endpoint; return results URL or None."""
    try:
        async with httpx.AsyncClient(
            timeout=90, follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            with open(path, "rb") as f:
                r = await client.post(
                    "https://pimeyes.com/api/upload/exec",
                    files={"image[]": (_basename(path), f)},
                )
        data = r.json()
        h = data.get("searchHash") or data.get("search_hash")
        if h:
            return f"https://pimeyes.com/en/results/{h}"
        if isinstance(data.get("results"), dict) and data["results"].get("searchHash"):
            return f"https://pimeyes.com/en/results/{data['results']['searchHash']}"
        return None
    except Exception:
        return None


async def clarify_found(found: list[dict], max_fetch: int = 8) -> tuple[list[dict], str]:
    """Tag each collected link as social/web and pull its page title.
    Returns (entries, verdict) where verdict ∈ identified | partial | not_found."""
    sem = asyncio.Semaphore(4)

    async def one(item: dict) -> dict:
        host = item.get("host", "")
        kind = "social" if any(d in host for d in SOCIAL_DOMAINS) else "web"
        title = ""
        try:
            async with sem:
                page = await _fetch_page(item["url"])
            m = re.search(r"<title[^>]*>([^<]{3,180})", page, re.I) \
                or re.search(r"^#\s+(.{3,160})", page, re.M)
            if m:
                title = re.sub(r"\s+", " ", m.group(1)).strip()
        except Exception:
            pass
        return {**item, "kind": kind, "title": title}

    entries = list(await asyncio.gather(*[one(i) for i in found[:max_fetch]]))
    entries += [dict(i, kind=("social" if any(d in i.get("host", "") for d in SOCIAL_DOMAINS) else "web"))
                for i in found[max_fetch:]]
    social = sum(1 for e in entries if e["kind"] == "social")
    verdict = "identified" if social else ("partial" if entries else "not_found")
    return entries, verdict


def github_engine(filename: str | None) -> dict | None:
    """Deep link to GitHub code search for repos containing the same filename."""
    if not filename:
        return None
    q = quote(f'"{filename}"')
    return {
        "id": "github_code",
        "name": "GitHub Code Search",
        "url": f"https://github.com/search?q={q}&type=code",
        "note": f"Repos containing a file named {filename} — profile pics get committed to repos.",
    }


async def github_code_search(filename: str) -> list[dict]:
    """Collect matching GitHub file links via the code search API (needs GITHUB_TOKEN)."""
    if not GITHUB_TOKEN or not filename:
        return []
    try:
        async with httpx.AsyncClient(
            timeout=30,
            headers={
                "Authorization": f"Bearer {GITHUB_TOKEN}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "picfinder",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        ) as client:
            r = await client.get(
                "https://api.github.com/search/code",
                params={"q": f'"{filename}" in:path', "per_page": "20"},
            )
        if r.status_code != 200:
            return []
        return [
            {"host": "github.com", "url": item["html_url"]}
            for item in r.json().get("items", [])
        ]
    except Exception:
        return []


async def _fetch_page(url: str) -> str:
    """Fetch a page; fall back to the Jina AI reader if direct fetch fails/thin."""
    async with httpx.AsyncClient(
        timeout=60, follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        try:
            r = await client.get(url)
            if r.status_code == 200 and len(r.text) > 1000:
                return r.text
        except Exception:
            pass
        try:
            headers = {"User-Agent": USER_AGENT}
            if JINA_API_KEY:
                headers["Authorization"] = f"Bearer {JINA_API_KEY}"
            r = await client.get(f"https://r.jina.ai/{url}", headers=headers, timeout=120)
            if r.status_code == 200:
                return r.text
        except Exception:
            pass
    return ""


async def collect_links(results_url: str) -> list[dict]:
    """Best-effort extraction of external result links from a Lens results page."""
    html = await _fetch_page(results_url)
    if not html:
        return []

    urls: dict[str, None] = {}
    for m in re.finditer(r'href="/url\?q=(https?://[^"&]+)', html):
        urls[unquote(m.group(1))] = None
    for m in re.finditer(r"https?://[A-Za-z0-9.\-]+\.[A-Za-z]{2,}[^\s\"'<>\\]*", html):
        urls[m.group(0).rstrip(")]};,")] = None

    return [
        {"host": urlparse(u).netloc, "url": u}
        for u in urls
        if not any(s in u for s in SKIP_DOMAINS)
    ][:40]


def url_based_engines(public_url: str) -> list[dict]:
    u = quote(public_url, safe="")
    return [
        {
            "id": "google_lens",
            "name": "Google Lens",
            "url": LENS_UPLOAD_BY_URL.format(url=u),
            "note": "Best overall coverage; surfaces social profiles where the image appears.",
        },
        {
            "id": "tineye",
            "name": "TinEye",
            "url": f"https://tineye.com/search?url={u}",
            "note": "Oldest pure reverse-image index; good for exact/modified matches.",
        },
        {
            "id": "yandex",
            "name": "Yandex Images",
            "url": f"https://yandex.com/images/search?rpt=imageview&url={u}",
            "note": "Historically strongest at face-similarity matches.",
        },
        {
            "id": "bing",
            "name": "Bing Visual Search",
            "url": f"https://www.bing.com/images/searchbyimage?imgurl={u}&cbir=sbi",
            "note": "Microsoft's visual search; sometimes finds pages others miss.",
        },
        {
            "id": "saucenao",
            "name": "SauceNAO",
            "url": f"https://saucenao.com/search.php?url={u}",
            "note": "Specialized in artwork/illustration sources.",
        },
        {
            "id": "google_images",
            "name": "Google Images",
            "url": f"https://www.google.com/searchbyimage?image_url={u}",
            "note": "Legacy endpoint; usually redirects to Lens.",
        },
    ]
