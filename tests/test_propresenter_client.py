"""Tests for the ProPresenter client."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from seeker.config import ProPresenterConfig
from seeker.propresenter_client import ProPresenterClient, ProPresenterToolHandler


class TestProPresenterClient:
    def test_base_url(self, pp_config):
        client = ProPresenterClient(pp_config, MagicMock())
        assert client.base_url == "http://127.0.0.1:50001"

    def test_base_url_custom_port(self):
        config = ProPresenterConfig(host="10.0.0.5", port=1025)
        client = ProPresenterClient(config, MagicMock())
        assert client.base_url == "http://10.0.0.5:1025"


class TestProPresenterToolHandler:
    @pytest.mark.asyncio
    async def test_handles_known_function(self):
        mock_client = MagicMock()
        mock_client.config = ProPresenterConfig(use_sequential_trigger=True)
        mock_client.trigger_next = AsyncMock(return_value=True)

        handler = ProPresenterToolHandler(mock_client)
        result = await handler.handle("trigger_presentation_slide", {"next_slide_index": 3})

        assert result["result"] == "success"
        assert result["slide_index"] == 3
        mock_client.trigger_next.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rejects_unknown_function(self):
        handler = ProPresenterToolHandler(MagicMock())
        result = await handler.handle("unknown_func", {})
        assert "error" in result
