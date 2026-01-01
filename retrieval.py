"""Enhanced retrieval interfaces and implementations."""

from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Dict, Any, List, Optional, Protocol
from pathlib import Path
import inspect

from config import DATA_DIR, MODEL_NAME


@dataclass
class SearchRequest:
    """Search query with filters and options."""
    query: str
    filters: Dict[str, Any] = field(default_factory=dict)
    top_k: int = 20
    enable_rerank: bool = True
    enable_time_boost: bool = True
    enable_exact_boost: bool = True
    debug: bool = False  # Collect debug info for web UI


@dataclass
class Candidate:
    """Intermediate retrieval candidate."""
    chunk_id: int
    score: float
    channel: str  # 'dense', 'sparse', 'exact', 'fused', 'rerank'
    doc_id: Optional[int] = None
    doc_type: Optional[str] = None
    timestamp: Optional[datetime] = None
    text_preview: Optional[str] = None
    score_history: Dict[str, float] = field(default_factory=dict)  # Track scores per stage


@dataclass
class SearchHit:
    """Final search result with full context and provenance."""
    chunk_id: int
    doc_id: int
    doc_type: str
    doc_title: Optional[str]
    doc_source: Optional[str]  # URL, file path, etc.
    
    # Text and position
    text: str
    char_start: int
    char_end: int
    chunk_window: Optional[str]  # surrounding context for display
    
    # Timestamps
    created_at: datetime
    event_at: Optional[datetime]
    ingested_at: Optional[datetime]
    
    # Retrieval provenance
    scores: Dict[str, float]  # {'dense': 0.8, 'sparse': 0.6, 'fused': 0.85, 'rerank': 0.92, 'final': 0.95}
    rank: int
    
    # Metadata
    tags: List[str]
    focus_hint: Optional[str]


@dataclass
class SearchDebugInfo:
    """Debug information for retrieval pipeline."""
    query: str = ""
    filters: Dict[str, Any] = field(default_factory=dict)
    dense_query: str = ""
    dense_results_count: int = 0
    dense_filtered_count: int = 0
    sparse_query: str = ""
    sparse_results_count: int = 0
    exact_query: str = ""
    exact_results_count: int = 0
    fused_count: int = 0
    reranked: bool = False
    rerank_count: int = 0
    post_processors: List[str] = field(default_factory=list)
    final_count: int = 0
    top_candidates: List[Dict[str, Any]] = field(default_factory=list)  # Detailed info for top candidates
    queries: Dict[str, Any] = field(default_factory=dict)
    stages: Dict[str, Any] = field(default_factory=dict)
    score_keys: List[str] = field(default_factory=list)
    candidates: List[Dict[str, Any]] = field(default_factory=list)


class Reranker(Protocol):
    """Protocol for pluggable rerankers."""
    def rerank(self, query: str, candidates: List[Candidate], store) -> List[Candidate]:
        """Rerank candidates based on query relevance."""
        ...


class PostProcessor(Protocol):
    """Protocol for result post-processing."""
    def process(self, request: SearchRequest, candidates: List[Candidate]) -> List[Candidate]:
        """Process and potentially reorder candidates."""
        ...


class FlashRankReranker:
    """Lightweight CPU-friendly reranker using FlashRank.
    
    Install: pip install flashrank
    """
    
    def __init__(self, model: str = 'ms-marco-MiniLM-L-12-v2', cache_dir: Optional[Path] = None):
        try:
            from flashrank import Ranker
            self.ranker = Ranker(model_name=model, cache_dir=str(cache_dir or DATA_DIR / 'models'))
        except ImportError:
            raise RuntimeError("FlashRank not installed: pip install flashrank")
    
    def rerank(self, query: str, candidates: List[Candidate], store) -> List[Candidate]:
        """Rerank top candidates with cross-encoder scoring."""
        if not candidates:
            return candidates
        
        # Fetch full text for top N candidates (reranking is expensive)
        from config import SHIYE_RERANK_TOP_K
        top_n = min(SHIYE_RERANK_TOP_K, len(candidates))
        passages = []
        for c in candidates[:top_n]:
            try:
                chunk = store.get_chunk(c.chunk_id)
                passages.append({
                    "id": c.chunk_id,
                    "text": chunk.text
                })
            except Exception as e:
                print(f"[warn] Failed to fetch chunk {c.chunk_id} for reranking: {e}")
                continue
        
        if not passages:
            return candidates
        
        # Run reranker
        try:
            sig = inspect.signature(self.ranker.rerank)
            param_names = list(sig.parameters.keys())
            reranked = None
            if len(param_names) == 2:
                # Newer FlashRank API: rerank(passages)
                try:
                    reranked = self.ranker.rerank(passages)
                except TypeError:
                    reranked = self.ranker.rerank(query, passages)
            else:
                # Older API: rerank(query, passages) or keyworded
                try:
                    reranked = self.ranker.rerank(query=query, documents=passages)
                except TypeError:
                    try:
                        reranked = self.ranker.rerank(query, passages)
                    except TypeError:
                        reranked = self.ranker.rerank(passages)
        except Exception as e:
            print(f"[warn] Reranking failed: {e}")
            return candidates
        
        if not reranked:
            return candidates
        
        # Update candidates with rerank scores
        result = []
        reranked_ids = set()
        for item in reranked:
            # Find original candidate
            orig = next((c for c in candidates if c.chunk_id == item['id']), None)
            if orig:
                orig.score = item['score']
                orig.channel = 'rerank'
                orig.score_history['rerank'] = item['score']
                result.append(orig)
                reranked_ids.add(item['id'])
        
        # Append remaining candidates (not reranked) at the end
        result.extend([c for c in candidates[top_n:] if c.chunk_id not in reranked_ids])
        
        return result


class RecencyBooster:
    """Boost recently created/occurred content."""
    
    def __init__(self, decay_days: int = 30, boost_factor: float = 0.2):
        self.decay_days = decay_days
        self.boost_factor = boost_factor
    
    def process(self, request: SearchRequest, candidates: List[Candidate]) -> List[Candidate]:
        if not request.enable_time_boost:
            return candidates
        
        now = datetime.now(UTC)
        for c in candidates:
            if c.timestamp:
                age_days = (now - c.timestamp).days
                # Linear decay: 1.0 at age=0, 0.0 at age=decay_days
                recency_factor = max(0, 1.0 - age_days / self.decay_days)
                old_score = c.score
                c.score *= (1.0 + self.boost_factor * recency_factor)
                c.score_history['recency_boost'] = c.score / old_score if old_score > 0 else 1.0
        
        return sorted(candidates, key=lambda x: x.score, reverse=True)


class TypeBooster:
    """Prefer certain document types."""
    
    DEFAULT_BOOSTS = {
        'note': 1.2,
        'web_page': 1.1,
        'chat': 1.0,
        'paper': 1.15,
        'rss_daily_summary': 0.9
    }
    
    def __init__(self, boosts: Optional[Dict[str, float]] = None):
        self.boosts = boosts or self.DEFAULT_BOOSTS
    
    def process(self, request: SearchRequest, candidates: List[Candidate]) -> List[Candidate]:
        for c in candidates:
            if c.doc_type in self.boosts:
                old_score = c.score
                c.score *= self.boosts[c.doc_type]
                c.score_history['type_boost'] = self.boosts[c.doc_type]
        return sorted(candidates, key=lambda x: x.score, reverse=True)


class ExactMatchBooster:
    """Boost candidates with exact phrase matches."""
    
    def __init__(self, boost_factor: float = 1.5):
        self.boost_factor = boost_factor
    
    def process(self, request: SearchRequest, candidates: List[Candidate]) -> List[Candidate]:
        if not request.enable_exact_boost:
            return candidates
        
        query_lower = request.query.lower()
        for c in candidates:
            if c.text_preview and query_lower in c.text_preview.lower():
                c.score *= self.boost_factor
                c.score_history['exact_match_boost'] = self.boost_factor
        
        return sorted(candidates, key=lambda x: x.score, reverse=True)


class Deduplicator:
    """Remove duplicate chunks, keep best per document."""
    
    def __init__(self, mode: str = 'by_doc'):
        self.mode = mode  # 'by_doc', 'by_text'
    
    def process(self, request: SearchRequest, candidates: List[Candidate]) -> List[Candidate]:
        if self.mode == 'by_doc':
            # Keep only best chunk per document
            seen_docs = set()
            result = []
            for c in candidates:
                if c.doc_id not in seen_docs:
                    seen_docs.add(c.doc_id)
                    result.append(c)
            return result
        elif self.mode == 'by_text':
            # Remove near-duplicate text (simple hash-based)
            seen_hashes = set()
            result = []
            for c in candidates:
                text_hash = hash(c.text_preview[:100]) if c.text_preview else 0
                if text_hash not in seen_hashes:
                    seen_hashes.add(text_hash)
                    result.append(c)
            return result
        return candidates


class ContextPacker:
    """Pack search hits into token-budget-aware context for LLM."""
    
    def __init__(self, max_tokens: int = 8000):
        self.max_tokens = max_tokens
        # Use simple approximation: 1 token ~= 4 chars for English
        self.chars_per_token = 4
    
    def pack(self, hits: List[SearchHit], query: str) -> Dict[str, Any]:
        """Pack hits into context bundle with citation metadata."""
        context_items = []
        total_tokens = 0
        
        for rank, hit in enumerate(hits, start=1):
            # Estimate tokens
            text_tokens = len(hit.text) // self.chars_per_token
            
            if total_tokens + text_tokens > self.max_tokens:
                break
            
            context_items.append({
                'citation_id': rank,
                'chunk_id': hit.chunk_id,
                'doc_id': hit.doc_id,
                'doc_type': hit.doc_type,
                'source': hit.doc_source,
                'title': hit.doc_title,
                'text': hit.text,
                'event_at': hit.event_at.isoformat() if hit.event_at else None,
                'created_at': hit.created_at.isoformat(),
                'relevance_score': hit.scores.get('final', 0.0)
            })
            
            total_tokens += text_tokens
        
        return {
            'query': query,
            'context_items': context_items,
            'total_items': len(context_items),
            'estimated_tokens': total_tokens
        }
