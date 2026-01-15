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
- **Entry points**:
  - `Orchestrator.summarize_lifelong(...)` handles cadence checks, delta selection, LLM calls, and persistence.
  - `MemoryWorkspace.save_lifelong_summary(...)` stores summaries as documents with facet/topic tags.
  - `LocalStore.list_lifelong_summaries(...)` and `get_latest_lifelong_summary(...)` power `/list` and delta lookup.
  - Web UI commands: `/list` to list summaries and `/sum` to trigger manual summarization.

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
- Add a bootstrap command (CLI or `/sum bootstrap`) to:
  - Load raw documents by type (chat, note, rss, web, paper).
  - Run profile/topic/timeline passes with batching and cached prefixes.
  - Persist summaries per facet/topic with provenance metadata.

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
