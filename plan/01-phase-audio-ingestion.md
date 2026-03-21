# Phase 1: Audio Ingestion Pipeline

> **Goal:** Capture live audio from the soundboard, resample and format it to Gemini's exact specifications, and deliver it to an async queue ready for WebSocket transmission.

---

## 1. Scope

| In Scope | Out of Scope |
|----------|-------------|
| Hardware audio capture via PyAudio/sounddevice | Gemini API connection (Phase 2) |
| Resampling to 16 kHz mono 16-bit PCM | ProPresenter integration (Phase 4) |
| Backpressure-aware async queue | UI / operator controls (Phase 5) |
| Device enumeration and selection | |
| Audio preprocessing (gain normalization) | |

---

## 2. Audio Format Requirements

The Gemini Multimodal Live API mandates the following audio format. Non-conforming audio will be rejected or misinterpreted by the model.

| Parameter | Required Value | Notes |
|-----------|---------------|-------|
| Bit Depth | 16-bit (signed int) | Sufficient dynamic range for speech |
| Sample Rate | 16,000 Hz | Native Gemini processing frequency — avoids server-side resampling |
| Encoding | Raw PCM (little-endian) | Uncompressed; no WAV headers, no codecs |
| Channels | 1 (Mono) | Stereo provides no benefit for semantic tracking |

---

## 3. Hardware Setup Guidelines

### 3.1 Signal Routing

```
┌──────────────┐     Aux/Matrix Bus     ┌──────────────────┐     USB/Thunderbolt     ┌──────────────┐
│  Mixing Desk │ ─────────────────────▶ │  Audio Interface  │ ─────────────────────▶ │  Host Machine │
│  (FOH / Mon) │   Post-fader or        │  (e.g. Focusrite) │                        │  (Python)     │
└──────────────┘   dedicated aux        └──────────────────┘                        └──────────────┘
```

### 3.2 Signal Processing Rules

> [!IMPORTANT]
> The audio feed to the daemon must be **unprocessed**. The following must be **disabled** on the aux send:

- Automatic Gain Control (AGC)
- Dynamic compression / limiting
- Aggressive noise reduction / noise gates
- Reverb / delay effects

**Why:** These DSP effects introduce artifacts that degrade Gemini's acoustic tokenization. The model needs the raw vocal signal with natural dynamics.

### 3.3 Gain Staging

- Set the preamp gain so that normal speaking level sits at **-18 dBFS to -12 dBFS**
- Ensure at least **6 dB of headroom** for dynamic vocal delivery (shouting, emphasis)
- The aux send level should be unity (0 dB) relative to the preamp

---

## 4. Software Architecture

### 4.1 Module: `audio_capture.py`

```
audio_capture.py
├── AudioConfig (dataclass)
│   ├── sample_rate: int = 16000
│   ├── channels: int = 1
│   ├── chunk_duration_ms: int = 32   # 20–40ms optimal range
│   ├── format: int = paInt16
│   └── device_index: Optional[int]
│
├── AudioCapture (class)
│   ├── __init__(config, queue)
│   ├── list_devices() -> list[DeviceInfo]
│   ├── start() -> None            # Opens PyAudio stream
│   ├── stop() -> None             # Closes stream, releases resources
│   └── _capture_loop() -> None    # Blocking read → queue.put()
│
└── create_audio_task(config, queue) -> Coroutine
    # Wraps _capture_loop in asyncio.to_thread()
```

### 4.2 Chunk Size Calculation

The chunk size in frames is derived from the desired duration:

```
chunk_frames = sample_rate × (chunk_duration_ms / 1000)
             = 16000 × 0.032
             = 512 frames
```

Each frame is 2 bytes (16-bit), so each chunk is **1,024 bytes** of raw PCM data.

### 4.3 Async Queue Design

```python
# Bounded queue enforces backpressure
audio_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=100)
```

| Queue Parameter | Value | Rationale |
|----------------|-------|-----------|
| `maxsize` | 100 | ~3.2 seconds of audio buffer at 32ms chunks |
| Overflow policy | Drop oldest | Prevents memory exhaustion during network degradation |
| Consumer | WebSocket Egress Task (Phase 2) | |

> [!NOTE]
> When the queue is full, the capture loop should log a warning and discard the oldest chunk. This ensures the system always streams the most recent audio, even during temporary network issues.

### 4.4 Threading Model

```
┌─────────────────────────────────────────────────────────────┐
│  asyncio Event Loop (main thread)                           │
│                                                             │
│  ┌───────────────────────┐    ┌──────────────────────────┐  │
│  │  Audio Capture Task   │    │  Other Tasks (Phase 2+)  │  │
│  │  (asyncio.to_thread)  │───▶│  (consume from queue)    │  │
│  │                       │    │                           │  │
│  │  PyAudio.read() is    │    │                           │  │
│  │  blocking — runs in   │    │                           │  │
│  │  thread executor      │    │                           │  │
│  └───────────────────────┘    └──────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## 5. Configuration

```yaml
# config.yaml — Audio section
audio:
  device_name: "Focusrite USB"   # Substring match against available devices
  device_index: null              # Override: use explicit index if set
  sample_rate: 16000
  channels: 1
  chunk_duration_ms: 32
  queue_max_size: 100
  input_gain_db: 0.0              # Software gain adjustment (pre-queue)
```

---

## 6. Error Handling

| Failure Mode | Detection | Recovery |
|-------------|-----------|----------|
| Audio device disconnected | `IOError` / `OSError` on `stream.read()` | Log error, retry device open with exponential backoff (max 5s) |
| Device not found at startup | Device enumeration returns no match | Log available devices, exit with clear error message |
| Buffer overflow (CPU starvation) | `stream.read()` returns short read or raises overflow | Log warning, continue — partial chunks are acceptable |
| Queue full (network backpressure) | `queue.put_nowait()` raises `QueueFull` | Drop oldest via `queue.get_nowait()` + re-put |

---

## 7. Testing Strategy

### 7.1 Unit Tests

- **`test_audio_config`** — Validate chunk size calculation and parameter constraints
- **`test_queue_overflow`** — Verify oldest-chunk-drop behavior when queue is full
- **`test_device_enumeration`** — Mock PyAudio to test device listing and selection

### 7.2 Integration Tests

- **`test_capture_to_queue`** — Capture 5 seconds from a test device (or loopback) and verify:
  - Queue receives expected number of chunks (±1)
  - Each chunk is exactly `chunk_frames × 2` bytes
  - Audio data is valid PCM (not silence, not clipped)

### 7.3 Manual Verification

- [ ] Connect physical audio interface, speak into microphone
- [ ] Run capture module standalone, write queue contents to `.raw` file
- [ ] Play back raw file with `ffplay -f s16le -ar 16000 -ac 1 output.raw`
- [ ] Confirm audio quality is clean and level is appropriate

---

## 8. Deliverables

- [ ] `seeker/audio_capture.py` — Audio capture module
- [ ] `seeker/config.py` — Configuration loader (audio section)
- [ ] `tests/test_audio_capture.py` — Unit tests
- [ ] Documentation: hardware setup guide for operators

---

## 9. Dependencies on Other Phases

| Dependency | Direction | Detail |
|-----------|-----------|--------|
| Phase 2 (Gemini WebSocket) | **Downstream consumer** | Consumes from `audio_queue` |
| Phase 5 (Orchestration) | **Lifecycle mgmt** | Start/stop controlled by orchestrator |
