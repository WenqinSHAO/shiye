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

---

## Phase 3: Topic Catalog & Unified Change Detection

### Core data structures (`topic_catalog.py`)

#### TopicEntry
```python
@dataclass
class TopicEntry:
    name: str                         # Human-readable topic name (used as key)
    summary: str                      # Brief description of the topic
    status: str = "active"            # "active" or "archived"
    created_at: datetime              # When topic was first created
    updated_at: datetime              # Last modification time
    last_activity_at: Optional[datetime]  # Last document assignment time
    embedding: Optional[np.ndarray]   # Cached embedding vector (optional)
    document_id: Optional[int]        # ID of backing lifelong_summary document
    tags: Dict[str, Any]              # Additional metadata
```

#### TopicAssignment
```python
@dataclass
class TopicAssignment:
    topic_name: str                   # Which topic the document was assigned to
    document_id: int                  # The assigned document
    assigned_at: datetime             # When assignment was made
    rationale: str                    # Human-readable explanation
    similarity_score: float           # Embedding similarity score
    scores: Dict[str, float]          # All candidate scores for transparency
    decision_method: str              # "embedding", "llm", or "manual"
```

#### TopicChangeResult (unified)
```python
@dataclass
class TopicChangeResult:
    decision: str                     # "reuse", "create", "merge", "split", "rename"
    topic_name: str                   # Primary topic name
    rationale: str                    # Explanation of decision
    similarity_scores: Dict[str, float]  # All similarity scores
    top_candidates: List[Tuple[str, float]]  # Top-k candidates
    merge_from: Optional[str]         # Source topic for merge
    split_into: Optional[str]         # New topic for split
    rename_from: Optional[str]        # Old name for rename
```

### Topic persistence (document-based, Option B)

Topics are stored as `doc_type='lifelong_summary'` documents with:
- `facet='topics'` in tags
- `key=<topic_name>` in tags
- Topic metadata embedded in JSON payload
- References to assigned documents

This enables:
- Full-text search over topic summaries
- Tag-based filtering (`facet=topics`)
- Versioned history of topic evolution

### TopicCatalog

Manager class for topic operations:
- `list_topics(status)`: List all topics, optionally filtered by status
- `get_topic(name)`: Get a specific topic by name
- `save_topic(topic, references, assignments)`: Save/update a topic
- `get_topic_embeddings()`: Compute embeddings for all active topics
- `archive_topic(name)`: Mark a topic as archived

### TopicChangeDetector (unified)

Unified pipeline for all topic change operations. Handles:
- **REUSE**: Assign content to an existing topic (high similarity)
- **CREATE**: Create a new topic for novel content (low similarity)
- **MERGE**: Combine multiple topics when content bridges them
- **SPLIT**: Separate a subtopic from an existing topic
- **RENAME**: Update topic name when content redefines it

Pipeline:
1. **Embedding prefilter**: Compute similarity between content and all topic embeddings
2. **Decision logic**:
   - High similarity (≥ 0.6): Reuse existing topic
   - Low similarity (< 0.3): Create new topic
   - Ambiguous: Use LLM judge with unified prompt for all operations
3. **Output**: `TopicChangeResult` with decision, rationale, and operation-specific fields

Additional methods:
- `merge_topics(source, target, rationale)`: Explicit topic merge
- `split_topic(source, new_name, content, rationale)`: Explicit topic split

### Entry points (orchestrator.py)

- `Orchestrator.list_topics(status)`: List topics in catalog
- `Orchestrator.assign_to_topic(content, document_id, use_llm)`: Main entry point for all topic operations
- `Orchestrator.process_new_documents_for_topics(since, limit)`: Batch processing

Internal handlers for each operation:
- `_handle_topic_create()`: Create new topic
- `_handle_topic_reuse()`: Assign to existing topic
- `_handle_topic_merge()`: Merge topics (archives source)
- `_handle_topic_split()`: Split into new topic
- `_handle_topic_rename()`: Rename topic (archives old)

### Prompts (prompts.py)

- `topic_summary_instruction()`: Generate brief topic overviews
- `topic_assignment_instruction(candidates)`: Legacy prompt (backward compatibility)
- `topic_change_instruction(candidates, candidates_with_scores)`: Unified prompt for all operations

### Test coverage

Test file: `tests/test_topic_catalog.py` (Phase 3 coverage, 21 tests)
- `TestTopicEntry`: Payload conversion and document parsing
- `TestTopicAssignment`: Score tracking and serialization
- `TestTopicCatalog`: Save/get/list/archive operations
- `TestTopicChangeDetector`: Unified detection, merge, split operations
- `TestOrchestratorTopics`: Integration with orchestrator
- `TestPhase3Prompts`: Prompt functions including unified prompt
