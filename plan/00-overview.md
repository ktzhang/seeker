# Seeker: Automated Sermon Slide Synchronization

## Project Overview

**Seeker** is a Python-based daemon that listens to a live pastor's audio feed, uses the Gemini Multimodal Live API to semantically track position within a sermon manuscript, and automatically advances ProPresenter 7 slides at the right moment — all in under one second of latency.

---

## Architecture at a Glance

```
┌──────────────┐    PCM Audio     ┌──────────────────┐    WSS / Base64     ┌─────────────────────┐
│  Soundboard  │ ──────────────▶  │  Python Daemon    │ ──────────────────▶ │  Gemini Live API    │
│  (Aux Send)  │                  │  (asyncio)        │ ◀────────────────── │  (native-audio)     │
└──────────────┘                  │                   │    Tool Calls       └─────────────────────┘
                                  │  ┌─────────────┐  │
                                  │  │ Audio Queue  │  │
                                  │  └─────────────┘  │
                                  │                   │
                                  │  ┌─────────────┐  │    HTTP REST
                                  │  │ Orchestrator │──│──────────────────▶ ┌─────────────────────┐
                                  │  └─────────────┘  │                     │  ProPresenter 7     │
                                  └──────────────────┘                     │  (Network API)      │
                                                                           └─────────────────────┘
```

---

## Phased Implementation Plan

The project is broken into **5 phases**, each self-contained and independently testable. Phases build on each other sequentially.

| Phase | Document | Deliverable | Est. Effort |
|-------|----------|-------------|-------------|
| **1** | [Audio Ingestion Pipeline](./01-phase-audio-ingestion.md) | Capture, resample, and queue hardware audio | 2–3 days |
| **2** | [Gemini WebSocket Integration](./02-phase-gemini-websocket.md) | Establish and maintain the Gemini Live API session | 3–4 days |
| **3** | [Semantic Tracking & Prompt Engineering](./03-phase-semantic-tracking.md) | Manuscript structuring, prompt design, and tool-call schema | 2–3 days |
| **4** | [ProPresenter Network Control](./04-phase-propresenter-control.md) | HTTP REST slide triggers and state management | 1–2 days |
| **5** | [Reliability, Orchestration & Operator UX](./05-phase-reliability-orchestration.md) | Concurrency, fault tolerance, kill-switch, and lifecycle management | 3–4 days |

**Total estimated effort: 11–16 days**

---

## Technology Stack

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| Language | Python 3.12+ | Mature async ecosystem, strong audio/ML library support |
| Async Runtime | `asyncio` | Native coroutine support for concurrent I/O tasks |
| Audio Capture | `PyAudio` / `sounddevice` | Cross-platform hardware audio capture |
| WebSocket Client | `websockets` | Lightweight, asyncio-native WSS client |
| HTTP Client | `aiohttp` | Non-blocking HTTP for ProPresenter REST calls |
| Configuration | YAML / `.env` | Operator-friendly configuration files |
| Operator Control | Local HTTP server (`aiohttp`) | Kill-switch and status endpoints for Stream Deck / Companion |

---

## Key Design Principles

1. **Semantic over Lexical** — Track *meaning*, not exact words. Speakers paraphrase, ad-lib, and tangent.
2. **Sub-second Latency** — The complete pipeline (capture → inference → slide trigger) must complete in < 1 second.
3. **Graceful Degradation** — Network drops, API timeouts, and model confusion must never crash the presentation.
4. **Human Override Always Wins** — A physical kill-switch and manual ProPresenter controls remain available at all times.
5. **Zero Audience Impact** — Failures are silent; the operator can seamlessly take over manual control.

---

## Prerequisites

- Python 3.12+
- Google Cloud project with Gemini API access (`gemini-2.5-flash-native-audio`)
- ProPresenter 7.9+ with Network API enabled
- Audio interface providing a dedicated aux/matrix send from the soundboard
- (Optional) Elgato Stream Deck + Bitfocus Companion for hardware operator controls
