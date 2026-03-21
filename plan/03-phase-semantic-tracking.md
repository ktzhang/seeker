# Phase 3: Semantic Tracking & Prompt Engineering

> **Goal:** Structure the sermon manuscript for AI consumption, engineer the system prompt for robust semantic tracking, and define the tool-call schema that bridges Gemini inference to local slide control.

---

## 1. Scope

| In Scope | Out of Scope |
|----------|-------------|
| Manuscript preprocessing and structuring | Audio capture (Phase 1) |
| System prompt design and behavioral constraints | WebSocket transport (Phase 2) |
| Tool-call function declaration schema | ProPresenter HTTP calls (Phase 4) |
| Handling speaker deviations (paraphrasing, tangents) | Concurrency / fault tolerance (Phase 5) |
| Manuscript import formats (plain text, DOCX, etc.) | |

---

## 2. The Core Problem: Semantic vs. Lexical Matching

Traditional speech-to-slide systems rely on **keyword spotting** — listening for exact words from the manuscript. This fails immediately when a speaker:

- **Paraphrases:** "It requires intentional sacrifice" → "It means giving up something valuable"
- **Ad-libs:** Inserts personal stories, jokes, or extended explanations not in the manuscript
- **Skips sections:** Jumps ahead in the manuscript, leaving entire slide blocks unspoken
- **Repeats themes:** Uses the same phrases in different parts of the sermon

The Gemini native-audio model solves this through **semantic equivalence** — understanding *meaning* rather than matching *words*. But we must engineer the prompt to leverage this capability precisely.

---

## 3. Manuscript Structuring

### 3.1 Input Formats

The daemon must accept sermon manuscripts in common formats:

| Input Format | Processing |
|-------------|-----------|
| **Plain text** (`.txt`) | Split by operator-defined delimiters (blank lines, `---`, etc.) |
| **Markdown** (`.md`) | Split by headings (`##`) or horizontal rules |
| **DOCX** (`.docx`) | Extract text via `python-docx`, split by paragraph styles |
| **Structured JSON/YAML** | Direct mapping — no preprocessing needed |

### 3.2 Internal Representation

Regardless of input format, all manuscripts are normalized to a structured XML format for injection into the system prompt:

```xml
<presentation_manuscript>
  <slide_block index="0">
    <expected_content>
      Welcome to our service today. We are beginning a new series
      looking at the historical context of the early church.
    </expected_content>
  </slide_block>
  <slide_block index="1">
    <expected_content>
      If you look at the book of Acts, chapter 2, you see a community
      that was entirely devoted to teaching, fellowship, and prayer.
    </expected_content>
  </slide_block>
  <slide_block index="2">
    <expected_content>
      But what does devotion actually look like in a modern context?
      It requires intentional sacrifice.
    </expected_content>
  </slide_block>
  <!-- ... additional slide blocks ... -->
</presentation_manuscript>
```

### 3.3 Module: `manuscript_parser.py`

```
manuscript_parser.py
├── SlideBlock (dataclass)
│   ├── index: int
│   └── content: str
│
├── Manuscript (dataclass)
│   ├── title: str
│   ├── blocks: list[SlideBlock]
│   └── to_xml() -> str              # Serializes to XML for prompt injection
│
├── parse_plain_text(text, delimiter) -> Manuscript
├── parse_markdown(text) -> Manuscript
├── parse_docx(file_path) -> Manuscript
└── parse_structured(data: dict) -> Manuscript
```

---

## 4. System Prompt Architecture

The system prompt is the most critical component of the entire system. It transforms Gemini from a general-purpose conversational AI into a **silent, analytical tracking engine**.

### 4.1 Prompt Template

```
You are a silent semantic tracking engine for a live presentation. Your ONLY purpose is to listen to live audio and determine when the speaker has completed the content of one slide block and is transitioning to the next.

## RULES — follow these absolutely:

1. SEMANTIC MATCHING: Do NOT listen for exact words. The speaker WILL paraphrase, reword, and improvise. You must evaluate semantic MEANING. If the speaker conveys the same idea as a slide block using different words, that block is complete.

2. SEQUENTIAL TRACKING: You maintain an internal pointer starting at slide_block index="0". You may ONLY evaluate the audio against the NEXT sequential slide block. Never skip ahead. Never go backward.

3. TANGENT HANDLING: The speaker will frequently go on tangents — personal stories, jokes, extended explanations not in the manuscript. During a tangent, do NOTHING. Wait patiently. Do not call any functions. Resume tracking only when the speaker returns to content that semantically matches the next expected slide block.

4. TRIGGER CONDITION: Call the `trigger_presentation_slide` function ONLY when you are confident that the speaker has COMPLETED the semantic content of the current slide block AND is transitioning to the next topic. Do not trigger on partial matches.

5. SILENCE: Never generate text responses. Never generate audio. Your ONLY output is the `trigger_presentation_slide` function call. No explanations, no commentary, no status updates.

6. ONE CALL AT A TIME: After calling `trigger_presentation_slide`, wait for the tool response before evaluating the next slide block. Never queue multiple calls.

7. COMPLETION: After the final slide block has been spoken, do nothing further. Do not call any more functions.

## MANUSCRIPT:

{manuscript_xml}
```

### 4.2 Behavioral Constraint Breakdown

| Constraint | Purpose | Failure Mode if Missing |
|-----------|---------|------------------------|
| Semantic matching | Handle paraphrasing and natural speech variation | Missed transitions when speaker doesn't quote exact text |
| Sequential tracking | Prevent out-of-order triggers | Model triggers slide 5 when speaker repeats a phrase from slide 1 |
| Tangent handling | Prevent false triggers during ad-libs | Model triggers next slide during an unrelated story |
| Trigger condition | Prevent premature transitions | Model triggers slide change mid-sentence |
| Silence | Focus model compute on analysis, not response generation | Wastes tokens generating unnecessary text |
| One call at a time | Prevent race conditions | Multiple rapid slide changes create jarring UX |
| Completion | Clean shutdown | Model hallucinates additional function calls after sermon ends |

---

## 5. Tool Declaration Schema

The function declaration provided in the `BidiGenerateContentSetup` payload:

```json
{
  "functionDeclarations": [
    {
      "name": "trigger_presentation_slide",
      "description": "Advance the live presentation to the next slide. Call this function ONLY when the speaker has semantically completed the content of the current slide block and is moving to the next topic. The next_slide_index is the 0-based index of the target slide from the manuscript.",
      "parameters": {
        "type": "OBJECT",
        "properties": {
          "next_slide_index": {
            "type": "INTEGER",
            "description": "The 0-based index of the next slide to display, corresponding to the slide_block index in the manuscript."
          }
        },
        "required": ["next_slide_index"]
      }
    }
  ]
}
```

> [!NOTE]
> The `description` field on the function declaration is critical. It reinforces the behavioral constraints from the system prompt and provides the model with additional context about *when* to call the function.

---

## 6. Prompt Engineering Considerations

### 6.1 Temperature Setting

**Temperature: 0.0** — We want deterministic, consistent behavior. Creative variation in slide timing is undesirable.

### 6.2 Manuscript Size Limits

| Manuscript Length | Token Cost (~) | Fits in Context? |
|-----------------|---------------|-----------------|
| 1,000 words | ~1,500 tokens | ✅ Easily |
| 5,000 words | ~7,500 tokens | ✅ Easily |
| 20,000 words | ~30,000 tokens | ✅ Within budget |
| 50,000 words | ~75,000 tokens | ⚠️ May compete with audio context |

For most sermons (2,000–8,000 words), the manuscript consumes < 10% of the context window budget.

### 6.3 Prompt Iteration Strategy

The system prompt will need iterative refinement based on real-world testing. Key areas to tune:

1. **Trigger sensitivity** — How much content must the speaker complete before triggering? Too early = premature transitions. Too late = awkward pauses.
2. **Tangent timeout** — How long should the model wait during a tangent before it considers the speaker may have skipped ahead?
3. **Semantic threshold** — How loosely should paraphrasing be matched? "Sacrifice" ≈ "giving up" but does "commitment" also match?

### 6.4 Prompt Versioning

Store prompt templates as versioned files to enable A/B testing:

```
prompts/
├── v1.0_baseline.txt
├── v1.1_stricter_tangent.txt
├── v1.2_relaxed_semantic.txt
└── active.txt → v1.1_stricter_tangent.txt  (symlink)
```

---

## 7. Module: `prompt_builder.py`

```
prompt_builder.py
├── PromptConfig (dataclass)
│   ├── template_path: str
│   └── manuscript: Manuscript
│
├── build_system_prompt(config) -> str
│   # Loads template, injects manuscript XML
│
├── build_setup_payload(prompt, tools, gemini_config) -> dict
│   # Constructs the full BidiGenerateContentSetup JSON
│
└── load_prompt_template(path) -> str
```

---

## 8. Testing Strategy

### 8.1 Unit Tests

- **`test_manuscript_parsing`** — Parse sample manuscripts from each format, verify correct slide block extraction
- **`test_xml_serialization`** — Verify generated XML is well-formed and matches expected schema
- **`test_prompt_construction`** — Verify template loading and manuscript injection
- **`test_setup_payload`** — Validate complete setup JSON against API spec

### 8.2 Semantic Accuracy Tests

These tests require the Gemini API and are closer to integration/acceptance tests:

| Test Scenario | Input Audio | Expected Behavior |
|--------------|------------|-------------------|
| **Verbatim reading** | Speaker reads manuscript word-for-word | Tool calls match sequential slide indices |
| **Heavy paraphrasing** | Speaker conveys same ideas with different words | Tool calls still match correct indices |
| **Tangent inserted** | Speaker adds 2-minute unrelated story | No tool calls during tangent; resumes correctly |
| **Section skipped** | Speaker skips slide blocks 3–5 | Model waits on block 3 until content arrives (see design note below) |
| **Repeated concept** | Speaker re-uses a phrase from an earlier slide | No spurious backward trigger |

> [!IMPORTANT]
> **Design Decision — Skip Detection:** If the speaker skips ahead, the model (per current prompt rules) will wait on the next sequential block. For v1, this is the safest behavior. In v2, we may add a "skip detection" directive that allows the model to recognize when the speaker has jumped ahead and trigger accordingly.

---

## 9. Deliverables

- [ ] `seeker/manuscript_parser.py` — Manuscript parser (multi-format)
- [ ] `seeker/prompt_builder.py` — Prompt template engine
- [ ] `prompts/v1.0_baseline.txt` — Initial system prompt template
- [ ] `tests/test_manuscript_parser.py` — Parser unit tests
- [ ] `tests/test_prompt_builder.py` — Prompt construction tests
- [ ] Test audio clips for semantic accuracy validation

---

## 10. Dependencies

| Dependency | Direction | Detail |
|-----------|-----------|--------|
| Phase 2 (Gemini) | **Consumer** | System prompt injected into setup payload |
| Phase 4 (ProPresenter) | **Implicit** | Tool call schema maps to ProPresenter slide indices |
