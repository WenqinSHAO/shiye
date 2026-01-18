# Lifelong Summarization Implementation (v0.9)

This document consolidates the **current code-level behavior** for lifelong summarization.
It is intentionally implementation-focused; planning and roadmap details live in
`LIFELONG_SUMMARIZATION_DESIGN.md`.

## Core data structures

### LifelongSummary (`lifelong_summary.py`)
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

**Normalized payload structure:**
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

### SummaryRequest (`summary_planner.py`)
```python
@dataclass
class SummaryRequest:
    facet: str                    # "profile", "topics", or "timeline"
    key: Optional[str]            # Sub-identifier within facet
    is_delta: bool                # True for incremental, False for snapshot
    batch_label: str              # Human-readable label (e.g., "profile:2024-01-01")
    since: Optional[datetime]     # Start of time window
```

## Entry points
- `Orchestrator.summarize_lifelong(...)`: cadence checks, delta selection, LLM calls, persistence.
- `Orchestrator.bootstrap_lifelong(...)`: batch bootstrap passes over raw documents.
- `MemoryWorkspace.save_lifelong_summary(...)`: stores summaries as documents with facet/key tags.
- `LocalStore.list_lifelong_summaries(...)` and `get_latest_lifelong_summary(...)`: powers `/list` and delta lookup.
- Web UI commands:
  - `/list` to list summaries
  - `/sum` to trigger manual summarization
  - `/sum bootstrap` for bootstrapping

## Summarize flow (delta)
1. Load latest summary for `(facet, key)`.
2. Check cadence unless manual trigger.
3. Gather recent messages since last summary.
4. Build payload scaffold with references.
5. Call LLM summarizer if configured.
6. Merge payload deltas, render markdown, and save document.

## Bootstrap workflow
1. `SummaryPlanner.plan_bootstrap()` creates requests grouped by time windows.
2. For each request, fetch raw documents and format once per time window.
3. Build document-first prompt and run LLM summarizer (if available).
4. Merge payload deltas, render markdown, and save summary documents.

## Batch caching strategy (token economy)
Bootstrap runs cache document formatting per time window:
```python
batch_cache: dict[
    tuple[datetime, datetime],  # (batch_start, batch_end)
    tuple[str, str, list[dict], int]  # (prefix, recent_text, references, doc_count)
]
```
This avoids re-fetching document content when multiple facets share a window.

## Prompt facet customization
The `lifelong_summary_instruction(facet, is_delta)` function customizes prompts per facet:
- `profile`: interests/objectives focus
- `topics`: topic-level updates
- `timeline`: chronological changes

Delta mode prepends: "Summarize only deltas/new changes since previous_summary"
Snapshot mode prepends: "Produce a full snapshot when previous_summary is empty"

## Reference handling
- **Bootstrap** references use `document_id` because bootstrap operates on raw content.
- **Regular summarization** references may include `chunk_id` from recent messages.
- `merge_references()` de-duplicates by JSON content.

## Test coverage
Test file: `tests/test_lifelong_summary.py` (Phase 1–2 coverage)
- `TestLifelongSummary`: payload normalization + rendering
- `TestSummaryPlanner`: bootstrap planning + delta planning
- `TestPrompts`: facet-specific prompt generation
- `TestOrchestratorLifelongSummary`: summarize + bootstrap flows, batch caching
- `TestWorkspaceLifelongSummary`: save/list/get summaries
