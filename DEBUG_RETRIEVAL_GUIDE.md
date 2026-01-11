# Debug Retrieval Guide

A concise guide to the retrieval debug tooling in Shiye. Use this when you need to see what each retriever did, how filters were applied, and how every score contributes to the final ranking.

## Turn on Debug
- **CLI / tests**: `export SHIYE_DEBUG_RETRIEVAL=true` then run `python main.py` or `python test_debug_retrieval.py`.
- **Web UI**: check the `debug` toggle in the header before running `/find …`.
- With `SHIYE_DEBUG_RETRIEVAL=true`, console tracing remains available; the web toggle renders the UI panel even without console logs.

## What the UI Shows
1) **Queries & Filters** — raw query plus per-retriever variants (dense, sparse/BM25, exact) with the filters applied.
2) **Pipeline Stats** — dense retrieved/after-filter counts, sparse and exact counts, fusion unique candidates, rerank coverage, post-processors applied, final count.
3) **Score Chips on Every Result** — per-result badges for each score component (dense, sparse, exact, fused, rerank, recency_boost, type_boost, exact_match_boost, final).
4) **Candidate Breakdown Panel** — top results with a stage-by-stage score list and text preview.
*Search hits render a chunk window (±1 neighbor) so previews show a bit of surrounding context rather than isolated fragments.*

## Score Components Cheat Sheet
- `dense`: FAISS semantic similarity (0–1).
- `sparse`: BM25 keyword score (normalized 0–1).
- `exact`: exact-phrase retrieval channel (mirrors the sparse query; stays 0 if not used).
- `fused`: RRF fusion score (small positive values).
- `rerank`: cross-encoder score (0–1).
- `recency_boost`: multiplier applied for freshness.
- `type_boost`: multiplier applied per document type.
- `exact_match_boost`: multiplier when the query string appears verbatim.
- `final`: score after all adjustments.

## Example (debug on)
```
Queries
- Raw: kubernetes container
- Dense: kubernetes container • Filters: {'doc_type': 'note'}
- Sparse (BM25): kubernetes container • Filters: {'doc_type': 'note'}
- Exact match: kubernetes container • Filters: {'doc_type': 'note'}

Pipeline
- Dense (FAISS): 500 results → 23 after filters
- Sparse (BM25): 18 results
- Exact match: 0 results
- RRF Fusion: 35 unique candidates
- Reranked: Yes (top 50)
- Post-processors: RecencyBooster, TypeBooster, ExactMatchBooster, Deduplicator
- Final: 5 results

Result #1 chips
dense 0.8523 | sparse 0.7234 | fused 0.0325 | rerank 0.9156 | recency_boost 1.05 | type_boost 1.2 | exact_match_boost 1.5 | final 1.7454
```
Score chips appear inline with each result and again in the debug panel, alongside the preview text, so you can see exactly which stage lifted or dropped a candidate.

## Troubleshooting
- **Dense retrieval empty**: check that embeddings are built and the FAISS index/model loaded; confirm content was ingested.
- **Sparse retrieval empty**: FTS5 may be missing or the query is too fuzzy—try simpler keywords or remove filters.
- **Huge drop after filtering**: filters (type/time/tags) are too strict; relax them or adjust `time_field`.
- **Rerank errors**: reranker dependencies missing; install `flashrank` or disable rerank.
- **Scores look tiny**: fusion scores are intentionally small; compare relative order, not absolute values.

Tune via env vars as needed:
`SHIYE_RERANK_TOP_K`, `SHIYE_SEARCH_TOP_K`, `SHIYE_RRF_K`, `SHIYE_RECENCY_DECAY_DAYS`.
