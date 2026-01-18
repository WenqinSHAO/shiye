"""Reference resolver subsystem for lifelong summaries (Phase 4).

Provides resolution of chunk and document references embedded in summary payloads,
supporting the UI reference peek feature (Phase 5) and debugging workflows.

Key capabilities:
- Resolve chunk/document references by ID and version
- Generate preview cards (title, date, snippet) for UI display
- Support fallback to parent documents when chunks are missing
- Handle references embedded in summary JSON payloads
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from storage import LocalStore


@dataclass
class ReferencePreview:
    """Preview card for a resolved reference.
    
    Contains enough information to render a reference chip or preview panel.
    """
    # Core identifiers
    reference_type: str  # 'chunk', 'document', 'summary'
    chunk_id: Optional[int] = None
    document_id: Optional[int] = None
    
    # Display metadata
    title: Optional[str] = None
    doc_type: Optional[str] = None
    source: Optional[str] = None  # URL, file path, etc.
    date: Optional[datetime] = None  # event_at or created_at
    
    # Content preview
    snippet: str = ""  # First ~200 chars of content
    full_text: Optional[str] = None  # Full chunk text when requested
    char_start: int = 0
    char_end: Optional[int] = None  # None means unset/end of string
    
    # Context info
    heading_path: Optional[str] = None  # Section context
    page_number: Optional[int] = None
    seq: Optional[int] = None  # Chunk sequence in document
    
    # Status
    resolved: bool = True
    fallback_used: bool = False  # True if fell back to parent doc
    error: Optional[str] = None


@dataclass
class ResolvedReference:
    """Full reference resolution result.
    
    Contains the preview plus additional debug info.
    """
    preview: ReferencePreview
    
    # Additional debug info
    resolution_method: str = "direct"  # 'direct', 'fallback', 'search'
    resolution_time_ms: float = 0.0
    raw_reference: Dict[str, Any] = field(default_factory=dict)


class ReferenceResolver:
    """Resolves chunk and document references to displayable previews.
    
    Supports Phase 4 reference subsystem requirements:
    - Resolution by chunk ID or document ID
    - Preview card generation for UI
    - Fallback to parent documents when chunks are deleted/missing
    """
    
    def __init__(self, store: Optional["LocalStore"] = None):
        """Initialize resolver with storage backend.
        
        Args:
            store: LocalStore instance for database access
        """
        self.store = store
        self._snippet_length = 200
    
    def resolve_chunk(
        self,
        chunk_id: int,
        include_full_text: bool = False,
    ) -> ResolvedReference:
        """Resolve a chunk reference by ID.
        
        Args:
            chunk_id: The chunk ID to resolve
            include_full_text: Whether to include full chunk text
            
        Returns:
            ResolvedReference with preview and debug info
        """
        import time
        start = time.time()
        
        if not self.store:
            return self._make_error_result(
                {"chunk_id": chunk_id},
                "Store not available",
            )
        
        try:
            try:
                chunk = self.store.get_chunk(chunk_id)
            except ValueError:
                chunk = None
            
            if not chunk:
                # Try fallback to parent document
                return self._fallback_to_parent(chunk_id, include_full_text, start)
            
            # Get parent document info
            doc_info = self._get_document_info(chunk.document_id)
            
            # Build preview
            preview = ReferencePreview(
                reference_type="chunk",
                chunk_id=chunk_id,
                document_id=chunk.document_id,
                title=doc_info.get("title"),
                doc_type=doc_info.get("doc_type"),
                source=doc_info.get("uri") or doc_info.get("source"),
                date=chunk.reference_time or chunk.created_at,
                snippet=self._make_snippet(chunk.text),
                full_text=chunk.text if include_full_text else None,
                char_start=chunk.char_start,
                char_end=chunk.char_end,
                heading_path=chunk.heading_path,
                page_number=chunk.page_number,
                seq=getattr(chunk, "seq", None),
                resolved=True,
            )
            
            elapsed = (time.time() - start) * 1000
            return ResolvedReference(
                preview=preview,
                resolution_method="direct",
                resolution_time_ms=elapsed,
                raw_reference={"chunk_id": chunk_id},
            )
        except Exception as e:
            return self._make_error_result(
                {"chunk_id": chunk_id},
                str(e),
            )
    
    def resolve_document(
        self,
        document_id: int,
        include_chunks: bool = False,
        max_chunks: int = 5,
    ) -> ResolvedReference:
        """Resolve a document reference by ID.
        
        Args:
            document_id: The document ID to resolve
            include_chunks: Whether to include child chunk previews
            max_chunks: Maximum number of chunks to include
            
        Returns:
            ResolvedReference with preview and debug info
        """
        import time
        start = time.time()
        
        if not self.store:
            return self._make_error_result(
                {"document_id": document_id},
                "Store not available",
            )
        
        try:
            doc = self.store.get_document_with_chunks(document_id)
            if not doc:
                return self._make_error_result(
                    {"document_id": document_id},
                    "Document not found",
                )
            
            # Build preview
            content = doc.get("content") or ""
            preview = ReferencePreview(
                reference_type="document",
                document_id=document_id,
                title=doc.get("title"),
                doc_type=doc.get("doc_type"),
                source=doc.get("uri") or doc.get("source"),
                date=self._parse_date(doc.get("event_at")) or self._parse_date(doc.get("created_at")),
                snippet=self._make_snippet(content),
                full_text=content if include_chunks else None,
                resolved=True,
            )
            
            elapsed = (time.time() - start) * 1000
            return ResolvedReference(
                preview=preview,
                resolution_method="direct",
                resolution_time_ms=elapsed,
                raw_reference={"document_id": document_id},
            )
        except Exception as e:
            return self._make_error_result(
                {"document_id": document_id},
                str(e),
            )
    
    def resolve_reference(
        self,
        reference: Dict[str, Any],
        include_full_text: bool = False,
    ) -> ResolvedReference:
        """Resolve a reference from a summary payload.
        
        Handles both chunk_id and document_id references.
        
        Args:
            reference: Reference dict from summary payload
            include_full_text: Whether to include full text
            
        Returns:
            ResolvedReference with preview
        """
        chunk_id = reference.get("chunk_id")
        document_id = reference.get("document_id")
        
        if chunk_id is not None:
            try:
                chunk_id = int(chunk_id)
                return self.resolve_chunk(chunk_id, include_full_text)
            except (ValueError, TypeError):
                pass
        
        if document_id is not None:
            try:
                document_id = int(document_id)
                return self.resolve_document(document_id, include_chunks=include_full_text)
            except (ValueError, TypeError):
                pass
        
        return self._make_error_result(
            reference,
            "Invalid reference: missing chunk_id or document_id",
        )
    
    def resolve_references(
        self,
        references: List[Dict[str, Any]],
        include_full_text: bool = False,
    ) -> List[ResolvedReference]:
        """Resolve multiple references in batch.
        
        Args:
            references: List of reference dicts from summary payload
            include_full_text: Whether to include full text
            
        Returns:
            List of ResolvedReference objects
        """
        return [
            self.resolve_reference(ref, include_full_text)
            for ref in references
        ]
    
    def resolve_summary_references(
        self,
        summary_payload: Dict[str, Any],
        include_full_text: bool = False,
    ) -> List[ResolvedReference]:
        """Extract and resolve all references from a summary payload.
        
        Args:
            summary_payload: Full summary JSON payload
            include_full_text: Whether to include full text
            
        Returns:
            List of ResolvedReference objects
        """
        references = summary_payload.get("references") or []
        return self.resolve_references(references, include_full_text)
    
    def get_chunk_context(
        self,
        chunk_id: int,
        window_size: int = 1,
    ) -> Dict[str, Any]:
        """Get chunk with surrounding context for display.
        
        Args:
            chunk_id: The chunk ID
            window_size: Number of neighbor chunks to include
            
        Returns:
            Dict with chunk info and neighbors
        """
        if not self.store:
            return {"error": "Store not available"}
        
        try:
            chunk = self.store.get_chunk(chunk_id)
            if not chunk:
                return {"error": "Chunk not found", "chunk_id": chunk_id}
            
            # Get document info
            doc_info = self._get_document_info(chunk.document_id)
            
            # Get chunk window (neighbors)
            chunk_window = self._build_chunk_window(chunk, window_size)
            
            return {
                "chunk_id": chunk_id,
                "document_id": chunk.document_id,
                "text": chunk.text,
                "chunk_window": chunk_window,
                "heading_path": chunk.heading_path,
                "page_number": chunk.page_number,
                "char_start": chunk.char_start,
                "char_end": chunk.char_end,
                "document": doc_info,
            }
        except Exception as e:
            return {"error": str(e), "chunk_id": chunk_id}
    
    def _fallback_to_parent(
        self,
        chunk_id: int,
        include_full_text: bool,
        start_time: float,
    ) -> ResolvedReference:
        """Try to resolve by finding parent document when chunk is missing."""
        import time
        
        # Chunk not found - try to get any info about it
        # This could happen if chunk was soft-deleted but document exists
        
        error_preview = ReferencePreview(
            reference_type="chunk",
            chunk_id=chunk_id,
            resolved=False,
            fallback_used=True,
            error="Chunk not found",
        )
        
        elapsed = (time.time() - start_time) * 1000
        return ResolvedReference(
            preview=error_preview,
            resolution_method="fallback",
            resolution_time_ms=elapsed,
            raw_reference={"chunk_id": chunk_id},
        )
    
    def _get_document_info(self, document_id: int) -> Dict[str, Any]:
        """Get document metadata without full content."""
        if not self.store:
            return {}
        
        try:
            doc = self.store.get_document_with_chunks(document_id)
            return {
                "id": doc.get("id"),
                "title": doc.get("title"),
                "doc_type": doc.get("doc_type"),
                "uri": doc.get("uri"),
                "source": doc.get("source"),
                "created_at": doc.get("created_at"),
                "event_at": doc.get("event_at"),
            }
        except Exception:
            return {}
    
    def _build_chunk_window(self, chunk, window_size: int) -> Optional[str]:
        """Build context window from neighboring chunks."""
        if not self.store:
            return None
        
        # Use existing chunk_window if available
        if hasattr(chunk, "chunk_window") and chunk.chunk_window:
            return chunk.chunk_window
        
        try:
            from context_assembly import build_chunk_window
            return build_chunk_window(self.store, chunk.id, window_size=window_size)
        except Exception:
            return None
    
    def _make_snippet(self, text: str) -> str:
        """Create a preview snippet from text."""
        if not text:
            return ""
        text = text.strip()
        if len(text) <= self._snippet_length:
            return text
        return text[:self._snippet_length].rsplit(" ", 1)[0] + "..."
    
    def _parse_date(self, date_str: Optional[str]) -> Optional[datetime]:
        """Parse ISO date string to datetime."""
        if not date_str:
            return None
        try:
            from datatypes import ensure_utc
            return ensure_utc(datetime.fromisoformat(date_str))
        except Exception:
            return None
    
    def _make_error_result(
        self,
        raw_reference: Dict[str, Any],
        error: str,
    ) -> ResolvedReference:
        """Create an error result."""
        return ResolvedReference(
            preview=ReferencePreview(
                reference_type="unknown",
                resolved=False,
                error=error,
            ),
            resolution_method="error",
            raw_reference=raw_reference,
        )


def preview_to_dict(preview: ReferencePreview) -> Dict[str, Any]:
    """Convert ReferencePreview to JSON-serializable dict."""
    return {
        "reference_type": preview.reference_type,
        "chunk_id": preview.chunk_id,
        "document_id": preview.document_id,
        "title": preview.title,
        "doc_type": preview.doc_type,
        "source": preview.source,
        "date": preview.date.isoformat() if preview.date else None,
        "snippet": preview.snippet,
        "full_text": preview.full_text,
        "char_start": preview.char_start,
        "char_end": preview.char_end,
        "heading_path": preview.heading_path,
        "page_number": preview.page_number,
        "seq": preview.seq,
        "resolved": preview.resolved,
        "fallback_used": preview.fallback_used,
        "error": preview.error,
    }


def resolved_to_dict(resolved: ResolvedReference) -> Dict[str, Any]:
    """Convert ResolvedReference to JSON-serializable dict."""
    return {
        "preview": preview_to_dict(resolved.preview),
        "resolution_method": resolved.resolution_method,
        "resolution_time_ms": resolved.resolution_time_ms,
        "raw_reference": resolved.raw_reference,
    }
