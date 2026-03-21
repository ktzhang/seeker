"""Shared test fixtures for the Seeker test suite."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from seeker.config import (
    AudioConfig,
    GeminiConfig,
    OperatorConfig,
    ProPresenterConfig,
    SeekerConfig,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def audio_config() -> AudioConfig:
    return AudioConfig(device_name="test", sample_rate=16000, chunk_duration_ms=32)


@pytest.fixture
def gemini_config() -> GeminiConfig:
    return GeminiConfig(api_key="test-key")


@pytest.fixture
def pp_config() -> ProPresenterConfig:
    return ProPresenterConfig(host="127.0.0.1", port=50001)


@pytest.fixture
def seeker_config(audio_config, gemini_config, pp_config) -> SeekerConfig:
    return SeekerConfig(
        audio=audio_config,
        gemini=gemini_config,
        propresenter=pp_config,
    )


@pytest.fixture
def audio_queue() -> asyncio.Queue[bytes]:
    return asyncio.Queue(maxsize=100)


@pytest.fixture
def sample_manuscript_text() -> str:
    return (
        "Welcome to our service today.\n\n"
        "If you look at Acts chapter 2, you see a devoted community.\n\n"
        "What does devotion look like in a modern context?"
    )
