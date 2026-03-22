"""Phase 6 — File-based audio ingestion (MP3/WAV/etc) via ffmpeg."""

from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path

from seeker.config import AudioConfig

log = logging.getLogger(__name__)


class FileAudioCapture:
    """Decodes an audio file using ffmpeg and feeds an asyncio queue.

    Simulates real-time ingestion by sleeping between chunks.
    """

    def __init__(self, config: AudioConfig, queue: asyncio.Queue[bytes]) -> None:
        self.config = config
        self.queue = queue
        self._process: subprocess.Popen | None = None
        self._running = False

    async def start(self) -> None:
        """Launch ffmpeg and start the ingestion loop."""
        if not self.config.audio_file:
            log.error("No audio file specified for FileAudioCapture")
            return

        file_path = Path(self.config.audio_file)
        if not file_path.exists():
            log.error("Audio file not found: %s", file_path)
            return

        # ffmpeg command to decode to raw s16le PCM at the configured sample rate and channels
        cmd = [
            "ffmpeg",
            "-i", str(file_path),
            "-f", "s16le",
            "-acodec", "pcm_s16le",
            "-ar", str(self.config.sample_rate),
            "-ac", str(self.config.channels),
            "-vn",
            "-loglevel", "quiet",
            "pipe:1"
        ]

        log.info("Starting file ingestion: %s", file_path)
        self._process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self._running = True
        self._loop = asyncio.get_running_loop()

        # Run the ingestion loop in a separate thread to avoid blocking
        await asyncio.to_thread(self._ingestion_loop)

        # Check for errors in stderr if process exited
        if self._process:
            stdout, stderr = self._process.communicate()
            exit_code = self._process.wait()
            if exit_code != 0:
                log.error("ffmpeg exited with code %d. stderr: %s", exit_code, stderr.decode(errors='replace'))
            elif stderr:
                log.debug("ffmpeg stderr: %s", stderr.decode(errors='replace'))

    def stop(self) -> None:
        """Stop the ingestion and kill the ffmpeg process."""
        self._running = False
        if self._process:
            self._process.terminate()
            self._process = None
        log.info("File ingestion stopped.")

    def _ingestion_loop(self) -> None:
        """Reads PCM data from ffmpeg stdout and pushes to queue with real-time pacing."""
        if not self._process or not self._process.stdout:
            return

        chunk_size = self.config.chunk_bytes
        # Wait duration to simulate real-time (chunk_duration_ms / 1000)
        pacing_s = self.config.chunk_duration_ms / 1000.0
        chunks_sent = 0

        while self._running:
            data = self._process.stdout.read(chunk_size)
            if not data:
                log.info("End of audio file reached.")
                break

            # Push to queue (using thread-safe call)
            try:
                self._loop.call_soon_threadsafe(self.queue.put_nowait, data)
                chunks_sent += 1
                if chunks_sent % 500 == 0:
                    log.info("Sent %d audio chunks from file.", chunks_sent)
            except Exception as e:
                log.error("Failed to push audio chunk to queue: %s", e)

            # Simulate real-time pacing
            import time
            time.sleep(pacing_s)

        self._running = False


async def create_file_audio_task(config: AudioConfig, queue: asyncio.Queue[bytes]) -> None:
    """Async wrapper for FileAudioCapture."""
    capture = FileAudioCapture(config, queue)
    try:
        await capture.start()
    finally:
        capture.stop()
