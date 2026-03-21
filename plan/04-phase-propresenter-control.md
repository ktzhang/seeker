# Phase 4: ProPresenter 7 Network Control

> **Goal:** Implement the local execution layer that translates Gemini tool-call responses into HTTP REST commands to advance slides in ProPresenter 7.

---

## 1. Scope

| In Scope | Out of Scope |
|----------|-------------|
| ProPresenter REST API client | Audio capture (Phase 1) |
| Slide trigger execution (sequential and absolute) | Gemini WebSocket (Phase 2) |
| Active presentation state polling | Prompt engineering (Phase 3) |
| Connection health checks | Full orchestration (Phase 5) |
| API quirk mitigation | |

---

## 2. ProPresenter 7 Network API Overview

ProPresenter 7.9+ exposes two network interfaces:

| Interface | Protocol | Use Case |
|-----------|----------|----------|
| **REST API** | HTTP (GET/POST/PUT) | Stateless command execution — ideal for slide triggers |
| **WebSocket API** | `ws://host:port/remote` | Persistent state subscriptions — useful for monitoring |

For slide advancement, the **REST API** is preferred due to lower overhead and simpler implementation.

### 2.1 Enabling the API

1. Open ProPresenter → **Preferences** → **Network**
2. Enable **"Enable Network"**
3. Note the displayed **IP address** and **port** (typically `50001` or `1025`)

---

## 3. REST API Endpoints

### 3.1 Slide Trigger Endpoints

| Intent | Method | Endpoint | Behavior |
|--------|--------|----------|----------|
| **Sequential advance** | `GET` | `/v1/trigger/next` | Advances to the next cue in the active playlist |
| **Active presentation advance** | `GET` | `/v1/presentation/active/next/trigger` | Advances only within the active presentation (safest) |
| **Absolute index trigger** | `GET` | `/v1/presentation/{uuid}/{index}/trigger` | Jumps to a specific slide by UUID and index |

### 3.2 State Query Endpoints

| Intent | Method | Endpoint | Returns |
|--------|--------|----------|---------|
| **Active presentation info** | `GET` | `/v1/presentation/active` | UUID, name, slide count, current index |
| **Current slide index** | `GET` | `/v1/status/slide` | Currently displayed slide index |
| **All presentations** | `GET` | `/v1/presentations` | List of all loaded presentations |

### 3.3 Primary Trigger Strategy

For normal sequential sermon delivery, use:

```
GET http://{host}:{port}/v1/presentation/active/next/trigger
```

This is safer than `/v1/trigger/next` because it scopes the command to the **active presentation document** only, avoiding accidental triggers on media items or other playlist elements.

### 3.4 Absolute Index Trigger (Recovery)

If the speaker skips ahead or the sequential state drifts, use the absolute trigger:

```
GET http://{host}:{port}/v1/presentation/{uuid}/{index}/trigger
```

> [!WARNING]
> **Known UI Quirk:** The absolute index trigger correctly outputs the slide to physical screens, but may **not auto-scroll** the ProPresenter operator's control monitor. The operator may need to manually scroll to see the active slide. For this reason, sequential triggers are preferred for normal operation.

---

## 4. Module Design

### 4.1 Module: `propresenter_client.py`

```
propresenter_client.py
├── ProPresenterConfig (dataclass)
│   ├── host: str = "127.0.0.1"
│   ├── port: int = 50001
│   ├── protocol: str = "http"
│   ├── timeout_s: float = 2.0
│   └── use_sequential_trigger: bool = True
│
├── ProPresenterClient (class)
│   ├── __init__(config, http_session)
│   ├── base_url -> str                          # Property: http://{host}:{port}
│   ├── trigger_next() -> bool                   # Sequential advance
│   ├── trigger_index(uuid, index) -> bool       # Absolute index advance
│   ├── get_active_presentation() -> PresentationInfo
│   ├── get_current_slide() -> int
│   ├── health_check() -> bool                   # Verify API connectivity
│   └── _request(method, path) -> dict | None
│
├── PresentationInfo (dataclass)
│   ├── uuid: str
│   ├── name: str
│   ├── slide_count: int
│   └── current_index: int
│
└── ProPresenterToolHandler (class)
    # Implements ToolHandler protocol from Phase 2
    ├── __init__(client: ProPresenterClient)
    └── handle(name, args) -> dict
        # Dispatches tool calls to ProPresenter client
```

### 4.2 Tool Handler Integration

The `ProPresenterToolHandler` bridges Gemini tool calls to ProPresenter commands:

```python
class ProPresenterToolHandler:
    async def handle(self, name: str, args: dict) -> dict:
        if name != "trigger_presentation_slide":
            return {"error": f"Unknown function: {name}"}

        slide_index = args["next_slide_index"]

        if self.client.config.use_sequential_trigger:
            success = await self.client.trigger_next()
        else:
            presentation = await self.client.get_active_presentation()
            success = await self.client.trigger_index(
                presentation.uuid, slide_index
            )

        return {
            "result": "success" if success else "error",
            "slide_index": slide_index
        }
```

---

## 5. Current Slide State Tracking

### 5.1 Drift Detection

The daemon should periodically poll the current slide index to detect **drift** between the expected slide (per Gemini's tool calls) and the actual active slide in ProPresenter.

Drift can occur when:
- The human operator manually advances or reverses slides
- A ProPresenter trigger fails silently
- Network latency causes command reordering

```python
class SlideStateTracker:
    def __init__(self, client: ProPresenterClient):
        self.expected_index: int = 0
        self.client = client

    async def verify_and_correct(self, triggered_index: int):
        actual = await self.client.get_current_slide()
        if actual != triggered_index:
            log.warning(
                f"Drift detected: expected={triggered_index}, actual={actual}"
            )
            # Option A: Correct by absolute trigger
            # Option B: Log and let human operator handle
```

> [!NOTE]
> **v1 Design Decision:** In the initial version, drift is logged but not auto-corrected. The human operator retains override authority. Auto-correction can be added as a configurable option in v2.

---

## 6. Configuration

```yaml
# config.yaml — ProPresenter section
propresenter:
  host: "127.0.0.1"
  port: 50001
  protocol: "http"
  timeout_s: 2.0
  use_sequential_trigger: true
  health_check_interval_s: 30
  drift_check_enabled: false
```

---

## 7. Error Handling

| Failure Mode | Detection | Recovery |
|-------------|-----------|----------|
| ProPresenter not running | `ConnectionRefusedError` on health check | Log error; operator must start ProPresenter |
| API not enabled | `ConnectionRefusedError` on port | Log error with setup instructions |
| HTTP timeout | `asyncio.TimeoutError` after 2s | Log warning, return error in tool response |
| 404 on trigger endpoint | HTTP 404 status | Likely wrong endpoint version; log API mismatch |
| 500 from ProPresenter | HTTP 500 status | Internal PP error; log and retry once |
| Network unreachable | `OSError` | Log error; halt triggers, notify operator |

---

## 8. Testing Strategy

### 8.1 Unit Tests

- **`test_url_construction`** — Verify endpoint URLs for all trigger types
- **`test_tool_handler_dispatch`** — Mock HTTP client, verify correct endpoint called for tool calls
- **`test_health_check`** — Mock successful and failed health checks
- **`test_drift_detection`** — Simulate expected vs. actual index mismatch

### 8.2 Integration Tests

- **`test_against_propresenter`** — With ProPresenter running:
  - Health check returns success
  - `trigger_next()` advances the active slide
  - `get_active_presentation()` returns valid UUID and slide count
  - `get_current_slide()` matches the visually active slide

### 8.3 Manual Verification

- [ ] Load a test presentation in ProPresenter (5+ slides)
- [ ] Run the ProPresenter client standalone
- [ ] Execute sequential triggers and observe screen output
- [ ] Execute absolute index trigger and verify correct slide
- [ ] Confirm operator UI auto-scrolls (sequential) or doesn't (absolute)

---

## 9. Deliverables

- [ ] `seeker/propresenter_client.py` — ProPresenter REST API client
- [ ] `tests/test_propresenter_client.py` — Unit + integration tests
- [ ] `seeker/config.py` — Configuration loader (ProPresenter section)

---

## 10. Dependencies

| Dependency | Direction | Detail |
|-----------|-----------|--------|
| Phase 2 (Gemini) | **Upstream** | Receives tool calls via `ToolHandler` protocol |
| Phase 3 (Prompt) | **Implicit** | Slide indices in tool calls match manuscript indices |
| Phase 5 (Orchestration) | **Lifecycle** | Client lifecycle managed by orchestrator; health checks integrated |
