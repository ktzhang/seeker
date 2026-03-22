"""Phase 4 — ProPresenter 7 REST API client."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

from seeker.config import ProPresenterConfig

log = logging.getLogger(__name__)


@dataclass
class PresentationInfo:
    """Metadata about the currently active ProPresenter presentation."""

    uuid: str
    name: str
    slide_count: int
    current_index: int


class ProPresenterClient:
    """Async HTTP client for ProPresenter 7's REST API."""

    def __init__(self, config: ProPresenterConfig, session: aiohttp.ClientSession) -> None:
        self.config = config
        self._session = session

    @property
    def base_url(self) -> str:
        return self.config.base_url

    # ------------------------------------------------------------------
    # Slide triggers
    # ------------------------------------------------------------------

    async def trigger_next(self) -> bool:
        """Advance the active presentation by one slide (sequential)."""
        return await self._get_ok("/v1/presentation/active/next/trigger")

    async def trigger_index(self, uuid: str, index: int) -> bool:
        """Jump to a specific slide by presentation UUID and index."""
        return await self._get_ok(f"/v1/presentation/{uuid}/{index}/trigger")

    # ------------------------------------------------------------------
    # State queries
    # ------------------------------------------------------------------

    async def get_active_presentation(self) -> PresentationInfo | None:
        """Return metadata about the currently active presentation."""
        data = await self._get_json("/v1/presentation/active")
        if data is None:
            return None
        return PresentationInfo(
            uuid=data.get("id", {}).get("uuid", ""),
            name=data.get("id", {}).get("name", ""),
            slide_count=len(data.get("groups", [{}])[0].get("slides", [])),
            current_index=data.get("presentation_index", 0),
        )

    async def get_current_slide_index(self) -> int | None:
        """Return the index of the currently displayed slide."""
        data = await self._get_json("/v1/status/slide")
        if data is None:
            return None
        return data.get("current", {}).get("index")

    async def health_check(self) -> bool:
        """Return True if the ProPresenter API is reachable."""
        return await self._get_ok("/version")

    # ------------------------------------------------------------------
    # Internal HTTP helpers
    # ------------------------------------------------------------------

    async def _get_ok(self, path: str) -> bool:
        """Issue a GET request and return True if response is 2xx."""
        url = f"{self.base_url}{path}"
        try:
            async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=self.config.timeout_s)) as resp:
                ok = resp.ok
                if not ok:
                    log.warning("ProPresenter %s returned %d", path, resp.status)
                return ok
        except (aiohttp.ClientError, TimeoutError, OSError) as exc:
            log.error("ProPresenter request failed (%s): %s", path, exc)
            return False

    async def _get_json(self, path: str) -> dict[str, Any] | None:
        """Issue a GET request and return JSON body, or None on failure."""
        url = f"{self.base_url}{path}"
        try:
            async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=self.config.timeout_s)) as resp:
                if resp.ok:
                    return await resp.json()
                log.warning("ProPresenter %s returned %d", path, resp.status)
                return None
        except (aiohttp.ClientError, TimeoutError, OSError) as exc:
            log.error("ProPresenter request failed (%s): %s", path, exc)
            return None


class ProPresenterToolHandler:
    """Bridges Gemini tool calls to ProPresenter REST commands.

    Implements the ``ToolHandler`` protocol expected by ``GeminiSession``.
    """

    def __init__(self, client: ProPresenterClient) -> None:
        self.client = client

    async def handle(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name != "trigger_presentation_slide":
            log.warning("Unknown tool call: %s", name)
            return {"error": f"Unknown function: {name}"}

        slide_index = args.get("next_slide_index", -1)
        log.info("Triggering slide advance → index %d", slide_index)

        if self.client.config.use_sequential_trigger:
            success = await self.client.trigger_next()
        else:
            pres = await self.client.get_active_presentation()
            if pres is None:
                return {"error": "No active presentation"}
            success = await self.client.trigger_index(pres.uuid, slide_index)

        return {
            "result": "success" if success else "error",
            "slide_index": slide_index,
        }
