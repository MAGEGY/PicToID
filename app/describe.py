"""Image description via a local Ollama vision model (optional, no API key needed)."""
from __future__ import annotations

import base64
import os

import httpx

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2-vision")

PROMPT = (
    "Describe this image in 2-3 sentences for reverse-image-search purposes: "
    "main subject, setting, notable objects/text/watermarks. Be factual, no speculation about identity."
)


async def describe_image(path: str) -> dict:
    """Return {'description': ...} or {'error': ...}."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            tags = await client.get(f"{OLLAMA_HOST}/api/tags")
        tags.raise_for_status()
        models = [m["name"] for m in tags.json().get("models", [])]
        model = next(
            (m for m in models if m.split(":")[0] == OLLAMA_MODEL.split(":")[0] or m == OLLAMA_MODEL),
            models[0] if models else None,
        )
        if model is None:
            return {"error": "Ollama is running but no models are installed. Try `ollama pull llama3.2-vision`."}

        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{OLLAMA_HOST}/api/generate",
                json={"model": model, "prompt": PROMPT, "images": [b64], "stream": False},
            )
        resp.raise_for_status()
        return {"description": resp.json().get("response", "").strip(), "model": model}
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return {
            "error": "Ollama not reachable. Install ollama.com and `ollama pull llama3.2-vision`, "
            "or set OLLAMA_HOST / OLLAMA_MODEL."
        }
    except Exception as e:
        return {"error": f"Description failed: {e}"}
