# Lifelong Summarization Design (v0.9 draft)

## Goals
- Provide a low-cost, incremental summary that guides search/query optimization.
- Keep summaries as first-class documents (new doc type), searchable like other content.
- Maintain multiple dated summary versions indefinitely for longitudinal reasoning.
- Support extensibility (new topics on the fly) with forward-only defaults.

## Facets (simplified set)
1. **User Profile**: interests + objectives (long/short-term) in a unified facet.
2. **Topics**: general abstraction for thematic groupings/projects, extensible at runtime.
3. **Timeline**: meta-facet derived from changes in profile and topic evolution.

## Representation
- **Hybrid format** per summary document:
  - **JSON** payload for tools + LLM API usage.
  - **Human-readable Markdown** for user-facing views.
- **Default language**: Chinese, with concise phrasing and character-efficient formatting.
- Include **references** in the JSON payload to support search, using chunk IDs as the
  preferred reference unit.
- Record a **prompt_version** in the JSON payload to trace which summarization prompt
  generated the summary.

## Summary cadence and triggers
- **Weekly fixed schedule** as the default cadence.
- **Manual trigger** supported:
  - On manual trigger, compute activity since the last summary and only summarize
    if new material exists.

## Versioning & retention
- Store **dated summary versions** as full documents.
- **Retention**: keep all versions forever.
- Weekly summaries should focus on **events within the week**, referencing prior
  summaries only when needed for context.

## Timeline behavior
- Timeline is a **second-order summary** derived from other facets.
- Any facet that evolves over time is eligible to contribute to the timeline.
- Start with a **global timeline**; per-topic timelines are deferred.

## Topic lifecycle (initial policy)
- Topics are **created on demand** and summarize **forward only** by default.
- Retrospective summaries require explicit user request plus search.
- Forgetting/sunsetting is out of scope for v0.9 (to be designed later).

## Authority & conflict handling
- **Raw history is authoritative** when conflicts occur.

## Deferred topics
- Forgetting mechanics and retrieval-use details.
- Privacy/retention policies (e.g., tokenization of sensitive data).
- Cost vs. quality trade-offs beyond the initial baseline.

## Open questions
- Should topics allow **subtopics** or remain flat in v0.9? **Decision**: flat under `facet=topics` for v0.9.
- Should weekly summaries be full snapshots or minimal deltas? **Decision**: only the first summary
  per facet/topic is a snapshot; subsequent ones are minimal deltas.
- What is the minimal “event” definition for timeline entries? **Guidance**: let the LLM pick pivotal
  moments for the given time range (prompt-driven), rather than hard-coding an event schema.

## Implementation notes (current code)
- **Source of truth**: JSON payload is authoritative; Markdown is rendered from JSON for UI display.
- **Facet semantics**:
  - `facet=profile` summarizes global user interests/objectives.
  - `facet=topics` uses optional `topic=<name>` to scope a summary to a specific topic.
  - `facet=timeline` captures second-order changes derived from profile/topic updates.
- **Delta vs snapshot**:
  - If a previous summary exists, summarize only new material since the latest summary.
  - If no summary exists, produce a full snapshot from available history.
- **Bootstrap considerations**:
  - Initial run should surface overall profile and seed topic list from recent exchanges.
  - Topic creation/merging heuristics remain a follow-on design task (not yet implemented).
  - Bootstrap batches reuse cached prefixes per time window to reduce repeated prompt setup across facets.
- **Entry points**:
  - `Orchestrator.summarize_lifelong(...)` handles cadence checks, delta selection, LLM calls, and persistence.
  - `Orchestrator.bootstrap_lifelong(...)` runs batch bootstrap passes over raw documents using `SummaryPlanner`.
  - `MemoryWorkspace.save_lifelong_summary(...)` stores summaries as documents with facet/topic tags.
  - `LocalStore.list_lifelong_summaries(...)` and `get_latest_lifelong_summary(...)` power `/list` and delta lookup.
  - Web UI commands: `/list` to list summaries, `/sum` to trigger manual summarization, and `/sum bootstrap` for bootstrapping. If `/sum` runs before any summaries exist, it automatically falls back to bootstrap using the earliest document date.
  - Bootstrap pulls raw document content from `chat`, `note`, `rss_daily_summary`, `web_page`, and `paper` types and records document-level references.

## Planning notes (next iterations)
### Bootstrap strategy for large histories
- Bootstrap summaries should be derived from **raw document content**, not chunked text, so
  chunking strategy changes do not alter the initial profile/topics/timeline view.
- Suggested staged bootstrap:
  1. **Profile pass**: summarize overall user interests/objectives from recent chat + notes.
  2. **Topic mining pass**: extract candidate topics from raw documents; cluster and dedupe.
  3. **Topic detail pass**: for each topic, summarize recent activity in batches (by time or
     document type) to keep LLM context manageable.
  4. **Timeline pass**: derive a global timeline from profile/topic deltas.
- Each pass can be multiple LLM calls; merge by facet type with deterministic rules
  (e.g., union + dedupe for topics, chronological merge for timeline).
- **Token economy**: reuse common history context across passes where possible (e.g., cached
  summaries or prompt prefixes), and batch by time windows to improve prefix cache hits.

### Facet-specific summarization prompts
- Use different instructions for profile, topics, and timeline to bias the model correctly.
- Profile should emphasize stable interests/objectives; topics should focus on projects and
  evolving themes; timeline should be strictly chronological deltas.

### Centralized prompt registry
- Centralize prompts in a single module (e.g., `prompts.py`) with functions keyed by task
  and facet to avoid drift across call sites.
- Allow future versions to be versioned/config-driven without touching orchestration logic.
- Consider migrating other prompts into the same registry and reviewing prompt-asset best
  practices (versioning, tests, configuration, and traceability).

### Topic consistency vs novelty
- Maintain a rolling topic catalog (name + short description + last updated timestamp).
- When new material arrives, score it against existing topics:
  - Reuse the topic if semantic similarity exceeds a threshold.
  - Create a new topic if no topic matches sufficiently or if the material is orthogonal.
- Periodically merge or rename topics when overlap grows; keep a stable topic ID for continuity.
- Use an LLM as a judge for topic reuse/creation decisions and store the **rationale**
  with references to supporting documents/chunks so changes are explainable.

### Reference granularity & sourcing
- References should avoid repeating raw content; instead, point users to the original
  document/chunk so they can navigate the source as needed.
- References should use raw documents where possible:
  - **Document-level** is usually enough for papers/web/GitHub issues/PRs.
  - **Chunk-level** is often required for RSS and long notes with mixed themes.
- Build a dedicated referencing subsystem that:
  - Chooses document vs chunk granularity deterministically.
  - Emits `references` entries with chunk/document IDs and optional spans.
  - Can be reused in note citations and assistant responses.
- **Chunk versioning**:
  - Prefer soft-deleting old chunks during migrations so references remain resolvable.
  - If a referenced chunk is removed, surface a “no longer available” state with a
    best-effort link to the parent document.

### UI reference peek
- Summary UI should surface references as clickable chips/links.
- Reuse the existing search-hit card UI to preview the referenced chunk/document in-place.

## Implementation plan (next steps)
### Phase 1: Prompt & data plumbing
- ✅ Extend `prompts.py` to cover other summarization prompts (RSS, note, etc.).
- ✅ Add a `SummaryPlanner` helper (new module) that:
  - Determines snapshot vs delta per facet/topic.
  - Builds LLM request batches for bootstrap passes.
  - Groups requests by time window for LLM KV cache optimization.
  - (Follow-up) track prompt versions and attach them to summary metadata.

### Phase 2: Bootstrap pipeline
- ✅ Add a bootstrap command (`/sum bootstrap`) to:
  - Load raw documents by type (chat, note, rss, web, paper).
  - Run profile/topic/timeline passes with batching and cached prefixes.
  - Persist summaries per facet/topic with provenance metadata.
- ✅ Optimize LLM API calls for KV cache hits:
  - Document-first prompt structure: `[documents] + [instruction]`
  - Batch processing ordered by time window, then facet
  - All facets for same time window processed together

---

## Phase 1&2 Code Review Findings

### Summary
Phase 1&2 implementation reviewed and improved. Issues addressed include test coverage,
documentation, LLM API optimization for KV cache utilization, and unified (facet, key) data model.

### Issues Found and Resolutions

#### Issue 1: Bootstrap references use document_id instead of chunk_id
- **Status**: ✅ Acceptable - document-level references are appropriate for bootstrap since it
  operates on raw_content, not chunked text. Design doc notes "document-level is usually enough."

#### Issue 2: Missing test coverage
- **Status**: ✅ Fixed - Added `tests/test_lifelong_summary.py` with tests covering
  `LifelongSummary`, `SummaryPlanner`, prompts, and orchestrator flows.

#### Issue 3: Suboptimal batch ordering for LLM KV cache
- **Status**: ✅ Fixed - `SummaryPlanner.plan_bootstrap()` now groups requests by time window
  first, then by facet, maximizing prefix reuse across facet passes.

#### Issue 4: Instruction-first prompt structure misses KV cache
- **Status**: ✅ Fixed - Implemented document-first prompt structure in `bootstrap_lifelong()`.
  Documents appear before facet-specific instructions so LLM can cache the shared prefix.

#### Issue 5: Inconsistent facet/topic data structure
- **Status**: ✅ Fixed - Renamed `topic` to `key` to create a unified (facet, key) addressing model.
  Now any facet type can have sub-identifiers: profile.interests, topics.AI, timeline.profile:interests.

---

## Current Implementation Details

This section documents the actual implementation of Phase 1&2 functionality.

### Data Structures

#### Unified (facet, key) Addressing Model

The `(facet, key)` pair uniquely identifies a summary within a time scope:
- **facet**: Top-level category ("profile", "topics", "timeline")
- **key**: Sub-identifier within the facet

Examples:
```
facet: profile
  ├── key: interests        → User interests summary
  ├── key: objectives       → User objectives summary
  └── key: preferences      → User preferences summary

facet: topics
  ├── key: AI               → AI topic summary
  └── key: distributed_sys  → Distributed systems topic summary

facet: timeline
  ├── key: profile:interests → Timeline of interests changes
  └── key: topics:AI         → Timeline of AI topic changes
```

#### LifelongSummary (lifelong_summary.py)

```python
@dataclass
class LifelongSummary:
    payload: Dict[str, Any]      # JSON payload for tools/LLM
    markdown: str                 # Human-readable markdown
    summary_date: datetime        # When summary was created
    title: Optional[str]          # Optional title override
    summary_source: str           # "system" or "user"
    facet: Optional[str]          # "profile", "topics", or "timeline"
    key: Optional[str]            # Sub-identifier within facet
    uri: Optional[str]            # Optional URI reference
    tags: Optional[Dict]          # Additional metadata tags
```

**Normalized Payload Structure:**
```json
{
  "schema_version": "v0.9",
  "language": "zh",
  "summary_date": "2024-01-15",
  "facet": "profile",
  "key": "interests",
  "facets": {
    "profile": ["interest 1", "interest 2"],
    "topics": [{"name": "AI", "summary": "..."}],
    "timeline": [{"date": "2024-01-01", "event": "..."}]
  },
  "references": [{"document_id": 123}],
  "prompt_version": "v0.9",
  "trigger": "bootstrap"
}
```

#### SummaryRequest (summary_planner.py)

```python
@dataclass
class SummaryRequest:
    facet: str                    # "profile", "topics", or "timeline"
    key: Optional[str]            # Sub-identifier within facet
    is_delta: bool                # True for incremental, False for snapshot
    batch_label: str              # Human-readable label (e.g., "profile:2024-01-01")
    since: Optional[datetime]     # Start of time window
```

### Bootstrap Workflow

The `/sum bootstrap since:YYYY-MM-DD` command triggers the following workflow:

```
1. SummaryPlanner.plan_bootstrap() → List[SummaryRequest]
   - Creates batches for each (facet × time_window) combination
   - Time windows are batch_days apart (default: 30 days)
   - All bootstrap requests have is_delta=False

2. For each SummaryRequest:
   a. Check batch_cache for (batch_start, batch_end) key
   b. If cache miss:
      - workspace.list_documents(doc_types, since, until)
      - _format_bootstrap_documents(docs) → (text, references)
      - _bootstrap_prefix(start, end, count) → prefix
      - Store in batch_cache
   c. Build initial payload with facet/references/bootstrap metadata
   d. If LLM available: call dspy_summarizer with prefix + text
   e. Merge LLM response into payload
   f. workspace.save_lifelong_summary(payload, markdown, ...)

3. Return completion message with saved/skipped counts
```

### Batch Caching Strategy (Token Economy)

The batch cache implements token savings for bootstrap operations:

**What's Cached:**
```python
batch_cache: dict[
    tuple[datetime, datetime],  # (batch_start, batch_end)
    tuple[str, str, list[dict], int]  # (prefix, recent_text, references, doc_count)
]
```

**How It Saves Tokens:**

When bootstrapping multiple facets (profile, topics, timeline) for the same time window:
- Document content is fetched and formatted **once** per time window
- The same `recent_text` is reused for all facets in that window
- Only the LLM instruction differs between facets

**Example with 3 facets, 2 time windows:**
```
Without caching: 6 document fetches, 6 text formatting operations
With caching:    2 document fetches, 2 text formatting operations
                 (3x reduction in I/O and processing)
```

**LLM API Prefix Caching:**

While the code-level `batch_cache` saves processing overhead, the actual LLM API
prefix caching depends on the provider (e.g., Anthropic's prompt caching, DeepSeek's
context caching). The implementation prepends a static header to each request:

```
Bootstrap batch context.
Window: 2024-01-01 to 2024-01-31.
Document count: 42.

[document content follows...]
```

When the LLM provider supports prefix caching, the repeated document content
across facet requests may be served from the provider's cache, further reducing
token costs.

### Prompt Facet Customization

The `lifelong_summary_instruction(facet, is_delta)` function returns customized
prompts for each facet:

| Facet | Focus | Key Phrases |
|-------|-------|-------------|
| `profile` | User interests/objectives | "Focus on user interests/objectives; ignore topic details" |
| `topics` | Thematic updates | "Focus on topic-level updates and emerging themes" |
| `timeline` | Chronological changes | "Focus on chronological changes derived from profile/topics" |

Delta mode prepends: "Summarize only deltas/new changes since previous_summary"
Snapshot mode prepends: "Produce a full snapshot when previous_summary is empty"

### Reference Handling

**During Bootstrap:**
- References use `document_id` (not `chunk_id`) since bootstrap operates on raw content
- This is consistent with design doc guidance: "Document-level is usually enough for
  papers/web/GitHub issues/PRs"

**During Regular Summarization:**
- References can include `chunk_id` from recent messages
- The `merge_references()` function deduplicates references by JSON content

### Test Coverage

Test file: `tests/test_lifelong_summary.py` (33 tests)

| Test Class | Coverage Area |
|------------|---------------|
| `TestLifelongSummary` | Dataclass methods, payload normalization, markdown rendering |
| `TestSummaryPlanner` | Bootstrap planning, batch creation, delta planning |
| `TestPrompts` | Facet-specific instructions, delta vs snapshot modes |
| `TestOrchestratorLifelongSummary` | summarize_lifelong, bootstrap_lifelong, batch caching |
| `TestWorkspaceLifelongSummary` | save/list/get lifelong summaries |

---

## Future Improvements (Analysis for Post-v0.9)

This section documents potential improvements for future phases based on code review feedback.

### Improvement 1: Unified (facet, key) Addressing Model ✅ (Implemented)

**Problem:** The `topic` field was semantically overloaded—only useful for `facet="topics"` but profile
could have sub-facets (interests, objectives) that couldn't be expressed.

**Solution implemented:**
- Renamed `topic` to `key` throughout the codebase
- `(facet, key)` pair now uniquely identifies any summary
- All facet types can have sub-identifiers: `profile.interests`, `topics.AI`, `timeline.profile:interests`

**Data model:**
```
facet: profile
  ├── key: interests      → payload: {...}
  ├── key: objectives     → payload: {...}
  └── key: preferences    → payload: {...}

facet: topics
  ├── key: AI             → payload: {...}
  └── key: distributed_systems → payload: {...}

facet: timeline
  ├── key: "profile:interests" → payload: {...}
  └── key: "topics:AI"         → payload: {...}
```

**Backward compatibility:** Storage layer supports both `key` (new) and `topic` (legacy) tag names.

### Improvement 2: LLM API KV Cache Optimization ✅ (Implemented)

**Problem:** Original instruction-first prompts prevented LLM API KV cache hits.

**Solution implemented:**
1. `SummaryPlanner.plan_bootstrap()` groups requests by time window, then facet
2. Document-first prompt structure in `bootstrap_lifelong()`:
   ```
   [document_content] + [facet_instruction]
   ```
3. All facets for the same time window process sequentially

**Result:** Document prefix is reused across facet passes for each time window.

**Note on cost:** Most providers bill per token regardless of KV cache—benefit is latency,
not cost. DeepSeek offers prompt caching with billing discounts.

---

### Phase 3: Topic catalog + novelty
- Create a `topics` table (id, name, summary, last_updated, status).
- Add a topic assignment step that uses LLM judgment + embeddings,
  and stores rationale + references used for decisions.

### Phase 4: Reference subsystem
- Introduce a reference resolver that:
  - Resolves chunk/document references by ID and version.
  - Generates preview cards (title, date, snippet) for UI reuse.
  - Supports “missing chunk” fallback to parent documents.

### Phase 5: UI reference peek
- Add summary reference chips that open the existing search-hit preview panel.
- Provide a compact “View source” button for document-level references.
