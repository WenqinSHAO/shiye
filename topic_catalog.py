"""Topic catalog and novelty detection for lifelong summarization (Phase 3).

This module implements:
- TopicEntry: Data structure for topic catalog entries
- TopicAssignment: Data structure for tracking document-topic assignments
- TopicCatalog: Manager for topic persistence and novelty detection
- NoveltyDetector: Hybrid pipeline for assigning content to topics
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np


@dataclass
class TopicEntry:
    """A topic in the catalog.
    
    Topics are stored as lifelong_summary documents with facet='topics' and key=<name>.
    The summary payload contains the topic overview and recent activity.
    """
    name: str
    summary: str
    status: str = "active"  # active, archived
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_activity_at: Optional[datetime] = None
    embedding: Optional[np.ndarray] = None
    document_id: Optional[int] = None  # ID of the lifelong_summary document
    tags: Dict[str, Any] = field(default_factory=dict)
    
    def to_payload(self) -> Dict[str, Any]:
        """Convert to JSON payload for storage."""
        return {
            "topic_name": self.name,
            "summary": self.summary,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "last_activity_at": self.last_activity_at.isoformat() if self.last_activity_at else None,
            "tags": self.tags,
        }
    
    @classmethod
    def from_document(cls, doc: dict) -> "TopicEntry":
        """Reconstruct from a lifelong_summary document."""
        raw_tags = doc.get("tags") or {}
        # Parse tags if stored as JSON string
        if isinstance(raw_tags, str):
            try:
                tags = json.loads(raw_tags)
            except (json.JSONDecodeError, ValueError):
                tags = {}
        else:
            tags = raw_tags
        
        raw_content = doc.get("raw_content") or ""
        
        # Parse JSON payload from raw_content
        payload = {}
        if raw_content:
            try:
                # raw_content may contain markdown with embedded JSON
                if "```json" in raw_content:
                    start = raw_content.find("```json") + 7
                    end = raw_content.find("```", start)
                    if end > start:
                        payload = json.loads(raw_content[start:end])
                else:
                    payload = json.loads(raw_content)
            except (json.JSONDecodeError, ValueError):
                pass
        
        def parse_dt(val: Optional[str]) -> Optional[datetime]:
            if not val:
                return None
            try:
                return datetime.fromisoformat(val.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                return None
        
        return cls(
            name=tags.get("key") or payload.get("topic_name") or "Unknown",
            summary=payload.get("summary") or "",
            status=payload.get("status") or tags.get("status") or "active",
            created_at=parse_dt(doc.get("created_at")) or datetime.now(UTC),
            updated_at=parse_dt(doc.get("event_at")) or datetime.now(UTC),
            last_activity_at=parse_dt(payload.get("last_activity_at")),
            document_id=doc.get("id"),
            tags=payload.get("tags") or {},
        )


@dataclass
class TopicAssignment:
    """Record of a document being assigned to a topic.
    
    Assignments track the rationale and similarity scores for transparency.
    """
    topic_name: str
    document_id: int
    assigned_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    rationale: str = ""
    similarity_score: float = 0.0
    scores: Dict[str, float] = field(default_factory=dict)
    decision_method: str = "embedding"  # embedding, llm, manual
    
    def to_payload(self) -> Dict[str, Any]:
        """Convert to JSON payload for embedding in summary."""
        return {
            "topic_name": self.topic_name,
            "document_id": self.document_id,
            "assigned_at": self.assigned_at.isoformat() if self.assigned_at else None,
            "rationale": self.rationale,
            "similarity_score": self.similarity_score,
            "scores": self.scores,
            "decision_method": self.decision_method,
        }


@dataclass
class NoveltyResult:
    """Result of novelty detection for a piece of content."""
    decision: str  # "reuse", "create", "merge"
    topic_name: str
    rationale: str
    similarity_scores: Dict[str, float] = field(default_factory=dict)
    top_candidates: List[Tuple[str, float]] = field(default_factory=list)
    merge_into: Optional[str] = None  # For merge decisions


class TopicCatalog:
    """Manager for topic catalog persistence and retrieval.
    
    Uses document-based persistence (Option B from design):
    - Topics are stored as lifelong_summary documents with facet='topics'
    - Topic assignments are embedded in the topic summary payload
    """
    
    def __init__(self, store, embedder=None):
        """Initialize topic catalog.
        
        Args:
            store: LocalStore instance for persistence
            embedder: Optional embedding provider for similarity computation
        """
        self.store = store
        self.embedder = embedder
        self._cache: Dict[str, TopicEntry] = {}
        self._cache_valid = False
    
    def list_topics(self, status: Optional[str] = None) -> List[TopicEntry]:
        """List all topics, optionally filtered by status."""
        if not self.store:
            return []
        
        summaries = self.store.list_lifelong_summaries(limit=100, facet="topics")
        topics = []
        seen_names = set()
        
        for summary in summaries:
            tags = summary.get("tags") or {}
            if tags.get("facet") != "topics":
                continue
            
            topic_name = tags.get("key")
            if not topic_name or topic_name in seen_names:
                continue
            seen_names.add(topic_name)
            
            # Fetch full document for parsing
            doc = self.store.get_document(summary.get("id"))
            if not doc:
                continue
            
            topic = TopicEntry.from_document(doc)
            if status and topic.status != status:
                continue
            
            topics.append(topic)
        
        return topics
    
    def get_topic(self, name: str) -> Optional[TopicEntry]:
        """Get a topic by name."""
        if not self.store:
            return None
        
        latest = self.store.get_latest_lifelong_summary(facet="topics", key=name)
        if not latest:
            return None
        
        doc = self.store.get_document(latest.get("id"))
        if not doc:
            return None
        
        return TopicEntry.from_document(doc)
    
    def save_topic(
        self,
        topic: TopicEntry,
        references: Optional[List[Dict[str, Any]]] = None,
        assignments: Optional[List[TopicAssignment]] = None,
    ) -> Optional[dict]:
        """Save or update a topic.
        
        Args:
            topic: TopicEntry to save
            references: Optional document references
            assignments: Optional list of new assignments to record
            
        Returns:
            Dict with document_id if successful
        """
        if not self.store:
            return None
        
        now = datetime.now(UTC)
        topic.updated_at = now
        
        payload = {
            "schema_version": "v0.9",
            "language": "zh",
            "summary_date": now.date().isoformat(),
            "facet": "topics",
            "key": topic.name,
            **topic.to_payload(),
            "references": references or [],
            "prompt_version": "v0.9",
        }
        
        # Include recent assignments in payload
        if assignments:
            payload["recent_assignments"] = [a.to_payload() for a in assignments]
        
        # Render markdown
        markdown_lines = [
            f"## Topic: {topic.name}",
            "",
            topic.summary or "No summary available.",
            "",
            f"**Status**: {topic.status}",
        ]
        if topic.last_activity_at:
            markdown_lines.append(f"**Last Activity**: {topic.last_activity_at.date().isoformat()}")
        
        markdown = "\n".join(markdown_lines)
        
        # Import workspace helpers
        from lifelong_summary import build_lifelong_summary
        
        summary = build_lifelong_summary(
            payload=payload,
            markdown=markdown,
            summary_date=now,
            facet="topics",
            key=topic.name,
            tags={
                "facet": "topics",
                "key": topic.name,
                "status": topic.status,
            },
        )
        
        content = summary.render_document()
        document_meta = summary.document_meta()
        
        result = self.store.add_document_chunked(content=content, document_meta=document_meta)
        if result:
            topic.document_id = result.get("document_id")
            self._cache[topic.name] = topic
        
        return result
    
    def get_topic_embeddings(self) -> Dict[str, np.ndarray]:
        """Get embeddings for all active topics.
        
        Embeddings are computed from the topic summary text.
        Returns dict mapping topic name to embedding vector.
        """
        if not self.embedder:
            return {}
        
        topics = self.list_topics(status="active")
        if not topics:
            return {}
        
        texts = [t.summary or t.name for t in topics]
        try:
            embeddings = self.embedder.embed(texts)
            return {t.name: embeddings[i] for i, t in enumerate(topics)}
        except Exception as e:
            print(f"[warn] Failed to compute topic embeddings: {e}")
            return {}
    
    def archive_topic(self, name: str) -> bool:
        """Archive a topic (mark as inactive)."""
        topic = self.get_topic(name)
        if not topic:
            return False
        
        topic.status = "archived"
        result = self.save_topic(topic)
        return result is not None


class NoveltyDetector:
    """Hybrid pipeline for assigning content to topics.
    
    Uses embedding similarity as a prefilter, then optionally
    invokes LLM for ambiguous cases (when configured).
    """
    
    def __init__(
        self,
        catalog: TopicCatalog,
        embedder=None,
        llm_judge=None,
        similarity_threshold: float = 0.6,
        top_k: int = 3,
    ):
        """Initialize novelty detector.
        
        Args:
            catalog: TopicCatalog for topic management
            embedder: Embedding provider for similarity
            llm_judge: Optional DSPy predictor for LLM-based decisions
            similarity_threshold: Threshold for confident matching
            top_k: Number of top candidates to consider
        """
        self.catalog = catalog
        self.embedder = embedder or catalog.embedder
        self.llm_judge = llm_judge
        self.similarity_threshold = similarity_threshold
        self.top_k = top_k
    
    def detect(
        self,
        content: str,
        document_id: Optional[int] = None,
        use_llm: bool = True,
    ) -> NoveltyResult:
        """Detect whether content belongs to existing topic or is novel.
        
        Args:
            content: Text content to analyze
            document_id: Optional document ID for tracking
            use_llm: Whether to use LLM judge for ambiguous cases
            
        Returns:
            NoveltyResult with decision and rationale
        """
        # Get topic embeddings
        topic_embeddings = self.catalog.get_topic_embeddings()
        
        # If no topics exist, this is definitely novel
        if not topic_embeddings:
            return NoveltyResult(
                decision="create",
                topic_name=self._suggest_topic_name(content),
                rationale="No existing topics in catalog; creating first topic.",
                similarity_scores={},
                top_candidates=[],
            )
        
        # Compute content embedding
        if not self.embedder:
            # Without embedder, default to creating new topic
            return NoveltyResult(
                decision="create",
                topic_name=self._suggest_topic_name(content),
                rationale="No embedder available for similarity comparison.",
                similarity_scores={},
                top_candidates=[],
            )
        
        try:
            content_embedding = self.embedder.embed([content])[0]
        except Exception as e:
            return NoveltyResult(
                decision="create",
                topic_name=self._suggest_topic_name(content),
                rationale=f"Embedding failed: {e}",
                similarity_scores={},
                top_candidates=[],
            )
        
        # Compute similarities
        similarities = {}
        for name, topic_emb in topic_embeddings.items():
            # Cosine similarity (embeddings are normalized)
            sim = float(np.dot(content_embedding, topic_emb))
            similarities[name] = sim
        
        # Sort by similarity
        sorted_topics = sorted(similarities.items(), key=lambda x: x[1], reverse=True)
        top_candidates = sorted_topics[:self.top_k]
        
        best_topic, best_score = top_candidates[0] if top_candidates else ("", 0.0)
        
        # Decision logic
        if best_score >= self.similarity_threshold:
            # High confidence match
            return NoveltyResult(
                decision="reuse",
                topic_name=best_topic,
                rationale=f"High similarity ({best_score:.3f}) to existing topic '{best_topic}'.",
                similarity_scores=similarities,
                top_candidates=top_candidates,
            )
        
        # Ambiguous case - use LLM if available
        if use_llm and self.llm_judge and top_candidates:
            return self._llm_decision(content, top_candidates, similarities)
        
        # Default to creating new topic for low similarity
        if best_score < 0.3:
            return NoveltyResult(
                decision="create",
                topic_name=self._suggest_topic_name(content),
                rationale=f"Low similarity ({best_score:.3f}) to all topics; creating new topic.",
                similarity_scores=similarities,
                top_candidates=top_candidates,
            )
        
        # Moderate similarity without LLM - default to reuse
        return NoveltyResult(
            decision="reuse",
            topic_name=best_topic,
            rationale=f"Moderate similarity ({best_score:.3f}) to '{best_topic}'; reusing without LLM confirmation.",
            similarity_scores=similarities,
            top_candidates=top_candidates,
        )
    
    def _llm_decision(
        self,
        content: str,
        top_candidates: List[Tuple[str, float]],
        all_scores: Dict[str, float],
    ) -> NoveltyResult:
        """Use LLM to make decision for ambiguous cases."""
        try:
            # Build prompt context
            candidates_text = "\n".join(
                f"- {name} (similarity: {score:.3f})"
                for name, score in top_candidates
            )
            
            instruction = (
                "Given the following content and candidate topics, decide:\n"
                "1. REUSE an existing topic (if content fits)\n"
                "2. CREATE a new topic (if content is novel)\n"
                "3. MERGE into a topic (if content bridges topics)\n\n"
                f"Candidate topics:\n{candidates_text}\n\n"
                "Return JSON with: decision ('reuse'/'create'/'merge'), "
                "topic_name, rationale, merge_into (if merge)."
            )
            
            # Call LLM judge
            from datatypes import Message, Role
            result = self.llm_judge(
                instruction=instruction,
                recent_messages=content[:2000],  # Limit content length
                previous_summary="",
            )
            
            # Parse LLM response
            try:
                response = json.loads(result.payload_json or "{}")
                return NoveltyResult(
                    decision=response.get("decision", "create"),
                    topic_name=response.get("topic_name", self._suggest_topic_name(content)),
                    rationale=response.get("rationale", "LLM decision"),
                    similarity_scores=all_scores,
                    top_candidates=top_candidates,
                    merge_into=response.get("merge_into"),
                )
            except (json.JSONDecodeError, AttributeError):
                # Fallback if LLM response is malformed
                pass
                
        except Exception as e:
            print(f"[warn] LLM decision failed: {e}")
        
        # Fallback to highest similarity
        best_topic, best_score = top_candidates[0]
        return NoveltyResult(
            decision="reuse",
            topic_name=best_topic,
            rationale=f"LLM unavailable; defaulting to highest similarity ({best_score:.3f}).",
            similarity_scores=all_scores,
            top_candidates=top_candidates,
        )
    
    def _suggest_topic_name(self, content: str) -> str:
        """Generate a suggested topic name from content."""
        # Simple heuristic: use first meaningful line or keywords
        lines = content.strip().split("\n")
        for line in lines:
            line = line.strip().lstrip("#").strip()
            if line and len(line) > 3:
                # Truncate to reasonable length
                return line[:60].strip()
        
        # Fallback
        return f"Topic_{datetime.now(UTC).strftime('%Y%m%d_%H%M')}"
