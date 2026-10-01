"""Temporary public hosting so URL-based engines can fetch the image.

Tries, in order: uguu.se (~3h), tmpfiles.org (~60min), catbox.moe (permanent).
"""
from __future__ import annotations

import httpx

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _basename(path: str) -> str:
    return path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]


async def upload_uguu(path: str) -> str:
    async with httpx.AsyncClient(timeout=60, headers={"User-Agent": UA}) as client:
        with open(path, "rb") as f:
            resp = await client.post(
                "https://uguu.se/upload.php?output=json",
                files={"files[]": (_basename(path), f)},
            )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(str(data)[:200])
    return data["files"][0]["url"]


async def upload_tmpfiles(path: str) -> str:
    async with httpx.AsyncClient(timeout=60, headers={"User-Agent": UA}) as client:
        with open(path, "rb") as f:
            resp = await client.post(
                "https://tmpfiles.org/api/v1/upload",
                files={"file": (_basename(path), f)},
            )
    resp.raise_for_status()
    data = resp.json()
    url = data["data"]["url"]
    # tmpfiles.org/xx/name.jpg serves HTML; /dl/ serves the raw file
    return url.replace("tmpfiles.org/", "tmpfiles.org/dl/", 1)


async def upload_catbox(path: str) -> str:
    async with httpx.AsyncClient(timeout=60, headers={"User-Agent": UA}) as client:
        with open(path, "rb") as f:
            resp = await client.post(
                "https://catbox.moe/user/api.php",
                data={"reqtype": "fileupload"},
                files={"fileToUpload": (_basename(path), f)},
            )
    resp.raise_for_status()
    url = resp.text.strip()
    if not url.startswith("https://"):
        raise RuntimeError(f"Unexpected catbox response: {url[:200]}")
    return url


_HOSTS = (
    ("uguu.se (expires ~3h)", upload_uguu),
    ("tmpfiles.org (expires ~60min)", upload_tmpfiles),
    ("catbox.moe (permanent)", upload_catbox),
)


async def publish(path: str) -> tuple[str, str]:
    """Upload to a public host. Returns (url, host_description)."""
    errors = []
    for name, fn in _HOSTS:
        try:
            return await fn(path), name
        except Exception as e:
            errors.append(f"{name}: {e}")
    raise RuntimeError(" | ".join(errors))
