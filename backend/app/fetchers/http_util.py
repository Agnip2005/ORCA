"""
Small shared HTTP helper for the open-data clients.

Every data source in this project is a public, key-less API, so the only
realistic failures are transient (timeouts, 429/5xx). We retry those ONCE
after a short pause and then raise -- we never substitute fake data.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}
USER_AGENT = "SIH-Marine-Intelligence/1.0 (open-data research project)"


def make_client(timeout: float = 30.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True)


async def get_json(client: httpx.AsyncClient, url: str, params: dict | None = None, *, what: str) -> dict:
    """GET + JSON with one retry on transient failures. `what` names the source in errors."""
    last_exc: Exception | None = None
    for attempt in (1, 2):
        try:
            resp = await client.get(url, params=params)
            if resp.status_code in RETRY_STATUS and attempt == 1:
                logger.warning("%s returned %s, retrying once", what, resp.status_code)
                await asyncio.sleep(1.5)
                continue
            resp.raise_for_status()
            return resp.json()
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            last_exc = exc
            if attempt == 1:
                logger.warning("%s network error (%s), retrying once", what, exc)
                await asyncio.sleep(1.5)
                continue
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:300].replace("\n", " ")
            raise RuntimeError(f"{what} failed: HTTP {exc.response.status_code} - {body}") from exc
    raise RuntimeError(f"{what} failed after retry: {last_exc!r}") from last_exc
