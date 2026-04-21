import os
import httpx


async def get_embedding(text: str, model: str | None = None) -> list[float]:
    url = os.environ.get("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
    embed_model = model or os.environ.get("EMBED_MODEL", "mxbai-embed-large")
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{url}/api/embed",
            json={"model": embed_model, "input": text, "truncate": True},
        )
        r.raise_for_status()
        return r.json()["embeddings"][0]
