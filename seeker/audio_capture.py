"""Phase 1 — Hardware audio capture and async queue management."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pyaudio

from seeker.config import AudioConfig

log = logging.getLogger(__name__)


@dataclass
class DeviceInfo:
    """Metadata about an available audio input device."""

    index: int
    name: str
    max_input_channels: int
    default_sample_rate: float


class AudioCapture:
    """Captures audio from a hardware device and feeds an asyncio queue.

    The underlying ``pyaudio.Stream.read()`` call is blocking, so we run the
    capture loop inside ``asyncio.to_thread()`` to avoid stalling the event
    loop.
    """

    def __init__(self, config: AudioConfig, queue: asyncio.Queue[bytes]) -> None:
        self.config = config
        self.queue = queue
        self._pa: pyaudio.PyAudio | None = None
        self._stream: pyaudio.Stream | None = None
        self._running = False

    # ------------------------------------------------------------------
    # Device enumeration
    # ------------------------------------------------------------------

    @staticmethod
    def list_devices() -> list[DeviceInfo]:
        """Return a list of available audio input devices."""
        import pyaudio

        pa = pyaudio.PyAudio()
        devices: list[DeviceInfo] = []
        try:
            for i in range(pa.get_device_count()):
                info = pa.get_device_info_by_index(i)
                if info["maxInputChannels"] > 0:
                    devices.append(
                        DeviceInfo(
                            index=i,
                            name=info["name"],
                            max_input_channels=info["maxInputChannels"],
                            default_sample_rate=info["defaultSampleRate"],
                        )
                    )
        finally:
            pa.terminate()
        return devices

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Open the PyAudio stream."""
        import pyaudio

        self._pa = pyaudio.PyAudio()

        device_index = self._resolve_device_index()

        self._stream = self._pa.open(
            format=pyaudio.paInt16,
            channels=self.config.channels,
            rate=self.config.sample_rate,
            input=True,
            input_device_index=device_index,
            frames_per_buffer=self.config.chunk_frames,
        )
        self._running = True
        log.info(
            "Audio capture started — device=%s, rate=%d, chunk=%d frames",
            device_index,
            self.config.sample_rate,
            self.config.chunk_frames,
        )

    def stop(self) -> None:
        """Close the PyAudio stream and release resources."""
        self._running = False
        if self._stream is not None:
            self._stream.stop_stream()
            self._stream.close()
            self._stream = None
        if self._pa is not None:
            self._pa.terminate()
            self._pa = None
        log.info("Audio capture stopped.")

    # ------------------------------------------------------------------
    # Capture loop (blocking — run via asyncio.to_thread)
    # ------------------------------------------------------------------

    def _capture_loop(self) -> None:
        """Blocking loop: read audio frames and push to the queue."""
        assert self._stream is not None
        while self._running:
            try:
                data = self._stream.read(self.config.chunk_frames, exception_on_overflow=False)
            except OSError:
                log.exception("Audio read error — device may have disconnected")
                break

            try:
                self.queue.put_nowait(data)
            except asyncio.QueueFull:
                # Backpressure: drop oldest chunk to make room
                try:
                    self.queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    self.queue.put_nowait(data)
                except asyncio.QueueFull:
                    pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_device_index(self) -> int | None:
        """Determine which device index to use from config."""
        if self.config.device_index is not None:
            return self.config.device_index

        if self.config.device_name and self.config.device_name != "default":
            for device in self.list_devices():
                if self.config.device_name.lower() in device.name.lower():
                    log.info("Matched audio device: %s (index %d)", device.name, device.index)
                    return device.index
            log.warning("Device '%s' not found — using system default", self.config.device_name)

        return None  # PyAudio uses system default


async def create_audio_task(config: AudioConfig, queue: asyncio.Queue[bytes]) -> None:
    """Async wrapper that runs the blocking capture loop in a thread executor."""
    capture = AudioCapture(config, queue)
    capture.start()
    try:
        await asyncio.to_thread(capture._capture_loop)
    finally:
        capture.stop()
