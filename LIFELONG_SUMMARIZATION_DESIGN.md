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
  - (Follow-up) track prompt versions and attach them to summary metadata.

### Phase 2: Bootstrap pipeline
- ✅ Add a bootstrap command (`/sum bootstrap`) to:
  - Load raw documents by type (chat, note, rss, web, paper).
  - Run profile/topic/timeline passes with batching and cached prefixes.
  - Persist summaries per facet/topic with provenance metadata.

---

## Phase 1&2 Code Review Findings (v0.9.1)

### Summary
Phase 1&2 are largely implemented but have several gaps related to reference granularity,
test coverage, and documentation. This section documents issues found during code review.

### Issues Found

#### Issue 1: Bootstrap references use document_id instead of chunk_id
- **Location**: `orchestrator.py` → `_format_bootstrap_documents()`
- **Design expectation**: "references should use chunk IDs as the preferred reference unit"
- **Actual behavior**: Bootstrap populates `{"document_id": doc_id}` references
- **Impact**: Low - document-level references are acceptable for bootstrap since it
  operates on raw_content, not chunked text. Design doc also notes document-level
  is "usually enough for papers/web/GitHub issues/PRs."
- **Status**: ✅ Acceptable (design doc allows document-level for certain types)

#### Issue 2: Missing test coverage for lifelong summary functionality
- **Location**: `tests/` directory
- **Design expectation**: Critical functionality should have test coverage
- **Actual behavior**: No tests for `summarize_lifelong()`, `bootstrap_lifelong()`,
  `SummaryPlanner`, `LifelongSummary`, or `prompts.py` summarization functions
- **Impact**: High - regressions may go undetected
- **Status**: 🔧 Fix required - add comprehensive tests

#### Issue 3: Prefix caching documentation gap
- **Location**: Design doc mentions "cached prefixes" but implementation details unclear
- **Design expectation**: "Bootstrap batches reuse cached prefixes per time window to
  reduce repeated prompt setup across facets"
- **Actual behavior**: `batch_cache` in `bootstrap_lifelong()` caches entire document
  content per time window, not just prefix. The prefix is a simple static header.
- **Impact**: Medium - documentation should clarify what "prefix reuse" actually does
- **Status**: 🔧 Update documentation to clarify actual caching strategy

#### Issue 4: SummaryRequest lacks prompt_version tracking
- **Location**: `summary_planner.py` → `SummaryRequest` dataclass
- **Design expectation**: "(Follow-up) track prompt versions and attach them to summary metadata"
- **Actual behavior**: `SummaryRequest` doesn't include prompt_version; it's added later
  in `orchestrator.py` during payload construction
- **Impact**: Low - prompt version is correctly attached to final payload
- **Status**: ✅ Acceptable - prompt_version properly tracked in payload

#### Issue 5: Documentation doesn't reflect current data structures
- **Location**: LIFELONG_SUMMARIZATION_DESIGN.md
- **Design expectation**: Documentation should match implementation
- **Actual behavior**: Missing documentation of:
  - Actual `LifelongSummary` dataclass structure
  - `SummaryRequest` and `SummaryPlanner` API
  - Exact workflow of `/sum bootstrap` command
  - How batch caching works in practice
- **Impact**: Medium - makes maintenance harder
- **Status**: 🔧 Update documentation

### Fix Plan

1. **Add test coverage** (Issue 2):
   - Add `tests/test_lifelong_summary.py` with tests for:
     - `LifelongSummary` dataclass methods
     - `SummaryPlanner.plan_bootstrap()` and `plan_delta()`
     - `prompts.lifelong_summary_instruction()` variations
     - `orchestrator.summarize_lifelong()` basic flow
     - `orchestrator.bootstrap_lifelong()` with mocked LLM

2. **Update documentation** (Issues 3, 5):
   - Add "Current Implementation Details" section documenting:
     - Data structures (`LifelongSummary`, `SummaryRequest`)
     - Bootstrap workflow and batch caching strategy
     - How prefix reuse saves tokens (shared document content across facets)

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
