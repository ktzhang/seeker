"""Phase 3 — Prompt template builder for Gemini system instructions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from seeker.config import GeminiConfig
from seeker.manuscript_parser import Manuscript

# Default tool declaration for slide triggering
SLIDE_TOOL_DECLARATION: dict[str, Any] = {
    "name": "trigger_presentation_slide",
    "description": (
        "Advance the live presentation to the next slide. Call this function "
        "ONLY when the speaker has semantically completed the content of the "
        "current slide block and is moving to the next topic. The next_slide_index "
        "is the 0-based index of the target slide from the manuscript."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "next_slide_index": {
                "type": "INTEGER",
                "description": (
                    "The 0-based index of the next slide to display, "
                    "corresponding to the slide_block index in the manuscript."
                ),
            }
        },
        "required": ["next_slide_index"],
    },
}


def load_prompt_template(path: str | Path) -> str:
    """Load a prompt template from disk."""
    return Path(path).read_text(encoding="utf-8")


def build_system_prompt(template: str, manuscript: Manuscript) -> str:
    """Inject the manuscript XML into the prompt template.

    The template should contain a ``{manuscript_xml}`` placeholder.
    """
    return template.replace("{manuscript_xml}", manuscript.to_xml())


def build_setup_payload(
    system_prompt: str,
    tools: list[dict[str, Any]],
    config: GeminiConfig,
    resumption_handle: str | None = None,
) -> dict[str, Any]:
    """Construct the full ``BidiGenerateContentSetup`` JSON payload."""
    return {
        "setup": {
            "model": config.model,
            "generationConfig": {
                "responseModalities": ["TEXT"],
                "temperature": 0.0,
            },
            "systemInstruction": {
                "parts": [{"text": system_prompt}],
            },
            "tools": [{"functionDeclarations": tools}],
            "realtimeInputConfig": {
                "mediaResolution": "MEDIA_RESOLUTION_LOW",
            },
            "contextWindowCompression": {
                "slidingWindow": {
                    "targetTokens": config.target_tokens,
                },
            },
            "sessionResumption": {
                "handle": resumption_handle,
            },
        }
    }
