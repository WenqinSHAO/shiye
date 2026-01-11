# Web UI Debug Mode Guide

## Quick Start

The debug mode in the web UI provides a visual, interactive way to understand how the enhanced retrieval pipeline works.

**Related docs**
- Pipeline details and score definitions: [DEBUG_RETRIEVAL_GUIDE.md](DEBUG_RETRIEVAL_GUIDE.md)
- Retrieval overview: [Retrieval.md](Retrieval.md)

### Enabling Debug Mode

1. **Enable the debug toggle** in the web UI header (checkbox labeled "Debug")
2. **Use the `/find` command** as normal:

```
/find kubernetes networking
```

or with filters:

```
/find type:note after:2024-01-01 kubernetes
```

The debug panel will automatically appear below your search results when debug mode is enabled.
See `DEBUG_RETRIEVAL_GUIDE.md` for the full pipeline breakdown and score component reference.

## What You'll See

### Search Results Section
The normal search results display first, showing:
- Document type badge
- Relevance score
- Timestamp
- Document title
- Text preview
- Source link (if available)

### Debug Info Panel
Below the results, you'll see a collapsible debug panel (click the header to toggle).

#### 1. Query & Filters
```
Query: kubernetes networking
Filters: {'doc_type': 'note', 'after': '2024-01-01'}
```

Shows exactly what was searched and what filters were applied.

#### 2. Retrieval Pipeline Statistics
```
Dense (FAISS): 500 results → 23 after filtering
Sparse (FTS5): 18 results
RRF Fusion: 35 unique candidates
Reranked: Yes (top 50)
Post-processors: RecencyBooster, TypeBooster, ExactMatchBooster, Deduplicator
Final: 5 results
```

This shows:
- **Dense retrieval**: How many semantic matches were found, and how many passed metadata filters
- **Sparse retrieval**: Keyword-based matches
- **RRF Fusion**: Unique candidates after combining both retrievers
- **Reranking**: Whether cross-encoder reranking was applied
- **Post-processors**: Which scoring adjustments were made
- **Final**: Number of results returned

#### 3. Top Candidates Score Evolution

For each of the top 5 results, you'll see:

```
#1 - Chunk 142 (doc 45, note)
Final Score: 1.7454
Score Evolution:
  dense: 0.7892
  sparse: 0.6891
  fused: 0.0298
  rerank: 0.9234
  recency_boost: 1.05
  type_boost: 1.2
  exact_match_boost: 1.5
  final: 1.7454
Preview: Kubernetes container orchestration allows you to deploy...
```

This shows:
- **Chunk ID and document info**: Which piece of content this is
- **Final score**: The score after all processing
- **Score evolution**: How the score changed at each pipeline stage
  - `dense`: FAISS semantic similarity (0-1)
  - `sparse`: FTS5 BM25 keyword relevance (0-1)
  - `fused`: RRF combined score (typically 0.01-0.05)
  - `rerank`: Cross-encoder score (0-1, if enabled)
  - `recency_boost`: Multiplier based on content age (1.0-1.2)
  - `type_boost`: Multiplier based on document type (0.9-1.2)
  - `exact_match_boost`: Multiplier if exact query phrase found (1.5)
  - `final`: Score after all adjustments
- **Text preview**: First 80 characters of the content

## Example Scenarios

### Scenario 1: Why did this result rank #1?

**Debug enabled** + **Query**: `/find machine learning optimization`

**Result #1 shows**:
```
dense: 0.7234
sparse: 0.4123
fused: 0.0287
rerank: 0.9456
exact_match_boost: 1.5
final: 1.4184
```

**Analysis**: 
- Moderate semantic similarity (dense: 0.72)
- Lower keyword match (sparse: 0.41)
- Reranking significantly boosted it (0.03 → 0.95)
- Contains exact phrase "machine learning optimization" (+50% boost)
- **Conclusion**: Reranking correctly identified high relevance, and exact match confirmed it

### Scenario 2: Why didn't sparse retrieval find anything?

**Debug enabled** + **Query**: `/find understanding attention mechanisms`

**Debug shows**:
```
Dense (FAISS): 200 results → 45 after filtering
Sparse (FTS5): 0 results
```

**Analysis**:
- Query uses conceptual terms ("understanding", "mechanisms")
- Sparse retrieval needs exact keyword matches
- Dense retrieval handled the semantic query well
- **Solution**: If you want keyword matches, use simpler terms: `/find attention mechanism`

### Scenario 3: Why was this result filtered out?

**Debug enabled** + **Query**: `/find type:note after:2024-12-01 kubernetes`

**Debug shows**:
```
Dense (FAISS): 500 results → 2 after filtering
```

**Analysis**:
- FAISS found 500 semantically similar chunks
- Only 2 matched filters (type=note AND after 2024-12-01)
- Most content is either not notes or older than Dec 1, 2024
- **Solution**: Relax filters: `/find type:note kubernetes` or `/find after:2024-12-01 kubernetes`

### Scenario 4: Understanding fusion behavior

**Debug enabled** + **Query**: `/find container deployment`

**Top results show**:
```
#1: dense: 0.85, sparse: 0.71, fused: 0.0325
#2: dense: 0.91, sparse: 0.00, fused: 0.0163
#3: dense: 0.67, sparse: 0.82, fused: 0.0298
```

**Analysis**:
- #1 appears in both retrievers (high fusion score)
- #2 has highest semantic match but wasn't found by keyword search
- #3 strong keyword match but lower semantic similarity
- Fusion balances both signals
- **Insight**: RRF promotes diversity - results appearing in multiple retrievers rank higher

## Tips for Using Debug Mode

1. **Start without filters**: Enable debug, then run `/find query` first to see what's available, then add filters

2. **Compare scores**: Look at how scores change through the pipeline to understand what's affecting ranking

3. **Check both retrievers**: If one retriever finds nothing, consider adjusting your query
   - No sparse results? Use more specific keywords
   - No dense results? Check if content has been ingested with embeddings

4. **Understand reranking impact**: Large score changes after reranking indicate the cross-encoder is correcting initial ranking

5. **Monitor filters**: If "after filtering" is much lower than initial results, your filters might be too restrictive

6. **Toggle on/off**: Turn debug off for normal searches, on when you need to troubleshoot

7. **Use for tuning**: Adjust environment variables based on what you observe:
   ```bash
   export SHIYE_RECENCY_DECAY_DAYS=60  # Extend recency boost
   export SHIYE_RERANK_TOP_K=100       # Rerank more candidates
   ```

## Troubleshooting

**Problem**: Debug panel doesn't appear
- **Solution**: Make sure the debug checkbox in the header is checked, then run `/find query`

**Problem**: All scores are very low
- **Solution**: Normal! Fusion scores are typically 0.01-0.05. Look at relative rankings, not absolute values.

**Problem**: No results at all
- **Solution**: 
  1. Check if content exists: remove all filters
  2. Try different query terms
  3. Verify embeddings are generated (check startup logs)

**Problem**: Results look wrong even with good scores
- **Solution**: Enable reranking if not already: check config for `SHIYE_RERANKER=flashrank`

## Comparison: Web UI vs Terminal Debug

| Feature | Web UI (Debug Toggle + `/find`) | Terminal (`SHIYE_DEBUG_RETRIEVAL=true`) |
|---------|----------------------------------|----------------------------------------|
| Visibility | Visual, formatted in browser | Text logs in terminal |
| Score evolution | Top 5-10 candidates | Top 5 per stage |
| Interactivity | Collapsible panel | Static logs |
| Best for | Quick debugging, demos | Development, detailed analysis |
| Toggle | Per-session with checkbox | All queries when enabled |

Use web UI debug for quick insights and user-facing explanations. Use terminal debug for deep troubleshooting and development.
