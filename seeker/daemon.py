"""Phase 5 — Main daemon orchestrator and lifecycle manager."""

from __future__ import annotations

import asyncio
import enum
import logging
import time
from typing import Any

import aiohttp
from aiohttp import web

from seeker.audio_capture import create_audio_task
from seeker.config import SeekerConfig
from seeker.gemini_session import GeminiSession
from seeker.manuscript_parser import Manuscript, load_manuscript
from seeker.prompt_builder import SLIDE_TOOL_DECLARATION, build_system_prompt, load_prompt_template
from seeker.propresenter_client import ProPresenterClient, ProPresenterToolHandler

log = logging.getLogger(__name__)


class DaemonState(enum.Enum):
    INITIALIZING = "initializing"
    DORMANT = "dormant"
    ACTIVATING = "activating"
    STREAMING = "streaming"
    RECONNECTING = "reconnecting"
    ERROR = "error"
    KILLED = "killed"


class SeekerDaemon:
    """Top-level orchestrator that wires all subsystems together."""

    def __init__(self, config: SeekerConfig) -> None:
        self.config = config
        self.state = DaemonState.INITIALIZING

        # Subsystem handles (created on activation)
        self._audio_queue: asyncio.Queue[bytes] | None = None
        self._gemini_session: GeminiSession | None = None
        self._http_session: aiohttp.ClientSession | None = None
        self._pp_client: ProPresenterClient | None = None
        self._task_group_tasks: list[asyncio.Task[Any]] = []

        # Metrics
        self.current_slide_index = 0
        self.total_slides = 0
        self.session_start: float | None = None
        self.trigger_latencies: list[float] = []
        self.error_count = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, manuscript_path: str | None = None) -> None:
        """Entry-point: set up operator server, then wait for activation."""
        log.info("Seeker daemon starting...")
        self.state = DaemonState.DORMANT

        if manuscript_path:
            await self.activate(manuscript_path)

    async def activate(self, manuscript_path: str) -> None:
        """Transition from DORMANT → STREAMING."""
        self.state = DaemonState.ACTIVATING
        log.info("Activating with manuscript: %s", manuscript_path)

        # Load manuscript and prompt
        manuscript = load_manuscript(manuscript_path)
        self.total_slides = len(manuscript.blocks)
        template = load_prompt_template(self.config.prompt.template)
        system_prompt = build_system_prompt(template, manuscript)

        # Create subsystems
        self._audio_queue = asyncio.Queue(maxsize=self.config.audio.queue_max_size)
        self._http_session = aiohttp.ClientSession()
        self._pp_client = ProPresenterClient(self.config.propresenter, self._http_session)
        tool_handler = ProPresenterToolHandler(self._pp_client)
        self._gemini_session = GeminiSession(
            self.config.gemini, self._audio_queue, tool_handler
        )

        # Connect to Gemini
        await self._gemini_session.connect()
        await self._gemini_session.send_setup(
            system_prompt, [SLIDE_TOOL_DECLARATION]
        )

        self.session_start = time.monotonic()
        self.state = DaemonState.STREAMING
        log.info("Streaming — tracking %d slides.", self.total_slides)

        # Run concurrent tasks
        async with asyncio.TaskGroup() as tg:
            tg.create_task(create_audio_task(self.config.audio, self._audio_queue))
            tg.create_task(self._gemini_session.stream_audio())
            tg.create_task(self._gemini_session.receive_messages())

    async def deactivate(self) -> None:
        """Gracefully stop streaming and return to DORMANT."""
        log.info("Deactivating...")
        if self._gemini_session:
            await self._gemini_session.disconnect()
        if self._http_session:
            await self._http_session.close()
        self.state = DaemonState.DORMANT
        log.info("Returned to dormant state.")

    async def kill(self) -> None:
        """Emergency stop — sever Gemini connection immediately."""
        log.warning("KILL SWITCH activated.")
        if self._gemini_session:
            await self._gemini_session.disconnect()
        if self._http_session:
            await self._http_session.close()
        self.state = DaemonState.KILLED
        log.warning("Daemon killed. Manual control restored.")

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        duration = 0.0
        if self.session_start:
            duration = time.monotonic() - self.session_start

        avg_latency = (
            sum(self.trigger_latencies) / len(self.trigger_latencies)
            if self.trigger_latencies
            else 0.0
        )
        last_latency = self.trigger_latencies[-1] if self.trigger_latencies else 0.0

        return {
            "state": self.state.value,
            "current_slide_index": self.current_slide_index,
            "total_slides": self.total_slides,
            "session_duration_s": round(duration, 1),
            "gemini_connected": self._gemini_session is not None and self._gemini_session._running,
            "propresenter_connected": self._pp_client is not None,
            "audio_queue_depth": self._audio_queue.qsize() if self._audio_queue else 0,
            "last_trigger_latency_ms": round(last_latency, 1),
            "avg_trigger_latency_ms": round(avg_latency, 1),
            "errors_count": self.error_count,
        }


# ------------------------------------------------------------------
# Operator HTTP server
# ------------------------------------------------------------------


class OperatorServer:
    """Lightweight HTTP API for operator controls (kill-switch, status, etc.)."""

    def __init__(self, daemon: SeekerDaemon, config: SeekerConfig) -> None:
        self.daemon = daemon
        self.config = config
        self._app = web.Application()
        self._app.router.add_get("/api/status", self._handle_status)
        self._app.router.add_get("/api/health", self._handle_health)
        self._app.router.add_post("/api/activate", self._handle_activate)
        self._app.router.add_post("/api/deactivate", self._handle_deactivate)
        self._app.router.add_post("/api/kill", self._handle_kill)

    async def start(self) -> None:
        runner = web.AppRunner(self._app)
        await runner.setup()
        site = web.TCPSite(
            runner,
            self.config.operator.http_host,
            self.config.operator.http_port,
        )
        await site.start()
        log.info(
            "Operator server listening on %s:%d",
            self.config.operator.http_host,
            self.config.operator.http_port,
        )

    # -- Route handlers --

    async def _handle_status(self, _request: web.Request) -> web.Response:
        return web.json_response(self.daemon.get_status())

    async def _handle_health(self, _request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def _handle_activate(self, request: web.Request) -> web.Response:
        body = await request.json() if request.content_length else {}
        manuscript = body.get("manuscript", self.daemon.config.prompt.manuscript)
        if not manuscript:
            return web.json_response({"error": "No manuscript specified"}, status=400)
        asyncio.create_task(self.daemon.activate(manuscript))
        return web.json_response({"status": "activating"})

    async def _handle_deactivate(self, _request: web.Request) -> web.Response:
        await self.daemon.deactivate()
        return web.json_response({"status": "dormant"})

    async def _handle_kill(self, _request: web.Request) -> web.Response:
        await self.daemon.kill()
        return web.json_response({"status": "killed"})
