"""Tests for reference resolver functionality (Phase 4).

This module tests:
- ReferenceResolver chunk resolution
- ReferenceResolver document resolution
- Reference fallback behavior
- Preview card generation
- Summary reference resolution
"""

import importlib
import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


# --- Test fixtures ---


class FakeEmbedder:
    """Deterministic embedder for tests."""

    def __init__(self, dim: int = 4):
        self.dim = dim
        self.loaded = False

    def load(self):
        self.loaded = True

    def embed(self, texts):
        vecs = []
        for i, _ in enumerate(texts):
            v = np.zeros(self.dim, dtype="float32")
            v[min(i, self.dim - 1)] = 1.0
            vecs.append(v)
        return np.vstack(vecs)


def make_store(tmp_dir: str):
    """Create a LocalStore with test configuration."""
    os.environ["SHIYE_DATA_DIR"] = tmp_dir
    for name in ("config", "vector_store", "embeddings", "storage"):
        if name in sys.modules:
            importlib.reload(sys.modules[name])
        else:
            importlib.import_module(name)
    import config as cfg
    import storage

    store = storage.LocalStore(
        db_path=cfg.DB_PATH,
        data_dir=cfg.DATA_DIR,
        embedder=FakeEmbedder(),
    )
    return store, cfg


# --- ReferencePreview tests ---


class TestReferencePreview:
    """Tests for ReferencePreview dataclass."""

    def test_preview_defaults(self):
        from reference_resolver import ReferencePreview

        preview = ReferencePreview(reference_type="chunk")
        assert preview.reference_type == "chunk"
        assert preview.resolved is True
        assert preview.snippet == ""
        assert preview.fallback_used is False

    def test_preview_with_full_data(self):
        from reference_resolver import ReferencePreview

        now = datetime.now(UTC)
        preview = ReferencePreview(
            reference_type="chunk",
            chunk_id=123,
            document_id=456,
            title="Test Document",
            doc_type="note",
            source="file://test.md",
            date=now,
            snippet="This is a test snippet...",
            char_start=0,
            char_end=100,
            heading_path="Section 1 > Subsection A",
            seq=0,
            resolved=True,
        )
        assert preview.chunk_id == 123
        assert preview.document_id == 456
        assert preview.title == "Test Document"
        assert preview.heading_path == "Section 1 > Subsection A"


# --- ReferenceResolver tests ---


class TestReferenceResolver:
    """Tests for ReferenceResolver class."""

    def test_resolver_without_store(self):
        from reference_resolver import ReferenceResolver

        resolver = ReferenceResolver(store=None)
        result = resolver.resolve_chunk(1)
        assert not result.preview.resolved
        assert "Store not available" in result.preview.error

    def test_resolve_chunk_not_found(self):
        """Test resolving a chunk that doesn't exist."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store, _ = make_store(tmp_dir)
            from reference_resolver import ReferenceResolver

            resolver = ReferenceResolver(store=store)
            result = resolver.resolve_chunk(9999)  # Non-existent chunk
            assert not result.preview.resolved
            assert result.preview.fallback_used or "not found" in (result.preview.error or "").lower()

    def test_resolve_chunk_success(self):
        """Test successfully resolving a chunk."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store, _ = make_store(tmp_dir)
            
            # Add a document with content
            from datatypes import Message, Role
            msg = Message(
                content="This is test content for the resolver.",
                role=Role.USER,
                created_at=datetime.now(UTC),
            )
            ids = store.add_messages([msg], document_meta={"doc_type": "chat", "title": "Test Doc"})
            assert ids, "Message should be added successfully"
            
            chunk_id = ids[0]
            
            from reference_resolver import ReferenceResolver
            resolver = ReferenceResolver(store=store)
            result = resolver.resolve_chunk(chunk_id)
            
            assert result.preview.resolved
            assert result.preview.chunk_id == chunk_id
            assert result.preview.snippet
            assert result.resolution_method == "direct"

    def test_resolve_document_success(self):
        """Test successfully resolving a document."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store, _ = make_store(tmp_dir)
            
            # Add a document
            result = store.add_document_chunked(
                content="This is a test document for resolution.",
                document_meta={
                    "doc_type": "note",
                    "title": "Test Note",
                    "source": "test",
                }
            )
            doc_id = result.get("document_id")
            assert doc_id
            
            from reference_resolver import ReferenceResolver
            resolver = ReferenceResolver(store=store)
            resolved = resolver.resolve_document(doc_id)
            
            assert resolved.preview.resolved
            assert resolved.preview.document_id == doc_id
            assert resolved.preview.title == "Test Note"

    def test_resolve_reference_by_chunk_id(self):
        """Test resolve_reference with chunk_id."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store, _ = make_store(tmp_dir)
            
            from datatypes import Message, Role
            msg = Message(content="Chunk reference test", role=Role.USER)
            ids = store.add_messages([msg])
            chunk_id = ids[0]
            
            from reference_resolver import ReferenceResolver
            resolver = ReferenceResolver(store=store)
            result = resolver.resolve_reference({"chunk_id": chunk_id})
            
            assert result.preview.resolved
            assert result.preview.chunk_id == chunk_id

    def test_resolve_reference_by_document_id(self):
        """Test resolve_reference with document_id."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store, _ = make_store(tmp_dir)
            
            result = store.add_document_chunked(
                content="Document reference test",
                document_meta={"doc_type": "note", "title": "Doc Test"}
            )
            doc_id = result.get("document_id")
            
            from reference_resolver import ReferenceResolver
            resolver = ReferenceResolver(store=store)
            resolved = resolver.resolve_reference({"document_id": doc_id})
            
            assert resolved.preview.resolved
            assert resolved.preview.document_id == doc_id

    def test_resolve_multiple_references(self):
        """Test resolving multiple references at once."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store, _ = make_store(tmp_dir)
            
            from datatypes import Message, Role
            msg1 = Message(content="First message", role=Role.USER)
            msg2 = Message(content="Second message", role=Role.USER)
            ids1 = store.add_messages([msg1])
            ids2 = store.add_messages([msg2])
            
            from reference_resolver import ReferenceResolver
            resolver = ReferenceResolver(store=store)
            references = [
                {"chunk_id": ids1[0]},
                {"chunk_id": ids2[0]},
            ]
            results = resolver.resolve_references(references)
            
            assert len(results) == 2
            assert all(r.preview.resolved for r in results)

    def test_resolve_summary_references(self):
        """Test extracting and resolving references from a summary payload."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store, _ = make_store(tmp_dir)
            
            from datatypes import Message, Role
            msg = Message(content="Summary reference test", role=Role.USER)
            ids = store.add_messages([msg])
            
            payload = {
                "facet": "profile",
                "references": [
                    {"chunk_id": ids[0]},
                ],
            }
            
            from reference_resolver import ReferenceResolver
            resolver = ReferenceResolver(store=store)
            results = resolver.resolve_summary_references(payload)
            
            assert len(results) == 1
            assert results[0].preview.resolved


# --- Helper function tests ---


class TestReferenceHelpers:
    """Tests for helper functions."""

    def test_preview_to_dict(self):
        from reference_resolver import ReferencePreview, preview_to_dict

        now = datetime.now(UTC)
        preview = ReferencePreview(
            reference_type="chunk",
            chunk_id=123,
            title="Test",
            date=now,
            snippet="Test snippet",
            resolved=True,
        )
        result = preview_to_dict(preview)
        
        assert isinstance(result, dict)
        assert result["reference_type"] == "chunk"
        assert result["chunk_id"] == 123
        assert result["title"] == "Test"
        assert result["date"] == now.isoformat()
        assert result["resolved"] is True

    def test_resolved_to_dict(self):
        from reference_resolver import ReferencePreview, ResolvedReference, resolved_to_dict

        preview = ReferencePreview(
            reference_type="document",
            document_id=456,
            resolved=True,
        )
        resolved = ResolvedReference(
            preview=preview,
            resolution_method="direct",
            resolution_time_ms=5.5,
            raw_reference={"document_id": 456},
        )
        result = resolved_to_dict(resolved)
        
        assert isinstance(result, dict)
        assert result["resolution_method"] == "direct"
        assert result["resolution_time_ms"] == 5.5
        assert result["preview"]["document_id"] == 456


# --- Integration tests ---


class TestReferenceResolverIntegration:
    """Integration tests for reference resolver with workspace."""

    def test_get_chunk_context(self):
        """Test getting chunk with surrounding context."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store, _ = make_store(tmp_dir)
            
            # Create a multi-chunk document
            content = """# Section 1
            
This is the first section with some content.

# Section 2

This is the second section with more content.

# Section 3

This is the third section with even more content.
"""
            result = store.add_document_chunked(
                content=content,
                document_meta={"doc_type": "note", "title": "Multi-section doc"}
            )
            chunk_ids = result.get("chunk_ids", [])
            
            if chunk_ids:
                from reference_resolver import ReferenceResolver
                resolver = ReferenceResolver(store=store)
                context = resolver.get_chunk_context(chunk_ids[0], window_size=1)
                
                assert "chunk_id" in context
                assert "document_id" in context
                assert "text" in context

    def test_snippet_truncation(self):
        """Test that long text is properly truncated to snippet."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store, _ = make_store(tmp_dir)
            
            # Create a document with long content
            long_content = "A" * 500  # 500 character content
            result = store.add_document_chunked(
                content=long_content,
                document_meta={"doc_type": "note", "title": "Long doc"}
            )
            chunk_ids = result.get("chunk_ids", [])
            
            if chunk_ids:
                from reference_resolver import ReferenceResolver
                resolver = ReferenceResolver(store=store)
                resolved = resolver.resolve_chunk(chunk_ids[0])
                
                # Snippet should be truncated
                assert len(resolved.preview.snippet) <= 203  # 200 + "..."
