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

## Current implementation
See **LIFELONG_SUMMARIZATION_IMPLEMENTATION.md** for the current code-level behavior, entry points,
request planning, prompt structure, and test coverage.

---

## Roadmap (Next Steps)

### Phase 1–2 (Complete)
- Prompt registry consolidated in `prompts.py`.
- Summary planner for bootstrap + delta planning.
- Bootstrap pipeline using raw documents and batch caching.
- KV-cache-friendly prompt ordering for bootstrap.

### Phase 3: Topic catalog + novelty detection

#### Phase 3 goals
- Establish a durable topic catalog that survives weekly deltas.
- Assign new material to topics (or create/merge topics) with a transparent rationale.
- Enable adaptive topic tracking without exposing fine-grained controls to UI commands.

#### Proposed data model
- **topics** table:
  - `id` (PK), `name`, `summary`, `status` (active/archived), `created_at`, `updated_at`,
    `last_activity_at`, `embedding_id` (optional), `tags` (json).
- **topic_aliases** table (optional):
  - `topic_id`, `alias`, `created_at`.
- **topic_assignments** table:
  - `id`, `topic_id`, `document_id`/`chunk_id`, `assigned_at`, `rationale`, `scores` (json).
- **topic_events** table (optional):
  - `id`, `topic_id`, `event_at`, `event`, `references` (json).

#### Novelty detection design space
1. **Embedding similarity gate**
   - Compute embeddings for new material.
   - Compare to topic embeddings (centroids or recent exemplar summaries).
   - Reuse topic if max similarity ≥ threshold; else propose a new topic.
   - Pros: deterministic, cheaper; Cons: hard thresholds, drift handling.
2. **LLM classifier/judge**
   - Provide current topic catalog + new material.
   - Ask model to select topic, create new, or merge.
   - Pros: flexible semantics, easy to encode rationale; Cons: cost and stability.
3. **Hybrid pipeline (recommended)**
   - Embedding prefilter to shortlist candidates (top-k topics).
   - LLM judge for final decision + rationale.
   - Store similarity scores and decision trace in `topic_assignments`.

#### Proposed pipeline (adaptive, non-UI)
1. **Candidate extraction** from new documents/chunks (reuse existing bootstrap document formatting).
2. **Similarity scan** against topic catalog embeddings.
3. **Decision stage**:
   - If high-confidence match: reuse topic.
   - If ambiguous: LLM judge between top-k topics or create new.
   - If clear novelty: create new topic and seed summary.
4. **Persistence**:
   - Update topic summary and last_activity_at.
   - Write topic_assignment with decision metadata and references.
   - Write topic_event entries for changes (optional but recommended for timeline).

#### Required design questions
- **Topic identity**: should topic IDs be immutable and human-readable, or opaque IDs with display names?
- **Embedding source**: store embeddings per topic summary, per event, or aggregate centroid?
- **Thresholds**: what similarity threshold signals reuse vs novelty? Do we allow dynamic thresholds?
- **Topic merge/split**: when two topics converge, how do we merge histories without losing continuity?
- **Rationale storage**: where should LLM explanations live (topic_assignments vs document tags)?
- **Consistency policy**: should topic names be stable over time, or auto-rename based on summaries?
- **Reference granularity**: do assignments prefer document IDs, chunk IDs, or hybrid?

### Phase 4: Reference subsystem
- Introduce a reference resolver that:
  - Resolves chunk/document references by ID and version.
  - Generates preview cards (title, date, snippet) for UI reuse.
  - Supports “missing chunk” fallback to parent documents.

### Phase 5: UI reference peek
- Add summary reference chips that open the existing search-hit preview panel.
- Provide a compact “View source” button for document-level references.
