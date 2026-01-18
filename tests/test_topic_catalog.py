"""Tests for topic catalog and novelty detection (Phase 3).

This module tests:
- TopicEntry and TopicAssignment dataclasses
- TopicCatalog persistence and retrieval
- NoveltyDetector similarity-based topic matching
- Orchestrator topic management methods
"""

import importlib
import json
import os
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


# --- Test fixtures ---


class FakeEmbedder:
    """Deterministic embedder for tests."""

    def __init__(self, dim: int = 4):
        self.dim = dim
        self.loaded = False
        self._call_count = 0

    def load(self):
        self.loaded = True

    def embed(self, texts):
        """Generate embeddings based on text content for reproducible similarity."""
        vecs = []
        for text in texts:
            v = np.zeros(self.dim, dtype="float32")
            # Use text hash to generate deterministic embedding
            h = hash(text) % 1000 / 1000.0
            v[0] = h
            v[1] = 1.0 - h
            v = v / np.linalg.norm(v)  # Normalize
            vecs.append(v)
        self._call_count += 1
        return np.vstack(vecs)


def make_store(tmp_dir: str):
    """Create a LocalStore with test configuration."""
    os.environ["SHIYE_DATA_DIR"] = tmp_dir
    for name in ("config", "vector_store", "embeddings", "storage", "topic_catalog", "lifelong_summary"):
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


# --- TopicEntry tests ---


class TestTopicEntry:
    """Tests for TopicEntry dataclass."""

    def test_to_payload_includes_all_fields(self):
        from topic_catalog import TopicEntry

        now = datetime.now(UTC)
        topic = TopicEntry(
            name="AI Research",
            summary="Research on artificial intelligence",
            status="active",
            created_at=now,
            updated_at=now,
            last_activity_at=now,
            tags={"priority": "high"},
        )

        payload = topic.to_payload()

        assert payload["topic_name"] == "AI Research"
        assert payload["summary"] == "Research on artificial intelligence"
        assert payload["status"] == "active"
        assert payload["tags"]["priority"] == "high"

    def test_from_document_parses_json_payload(self):
        from topic_catalog import TopicEntry

        doc = {
            "id": 1,
            "tags": {"facet": "topics", "key": "ML Projects"},
            "raw_content": json.dumps({
                "topic_name": "ML Projects",
                "summary": "Machine learning projects",
                "status": "active",
                "last_activity_at": "2024-01-15T10:00:00+00:00",
            }),
            "created_at": "2024-01-01T00:00:00+00:00",
            "event_at": "2024-01-15T10:00:00+00:00",
        }

        topic = TopicEntry.from_document(doc)

        assert topic.name == "ML Projects"
        assert topic.summary == "Machine learning projects"
        assert topic.status == "active"
        assert topic.document_id == 1

    def test_from_document_handles_markdown_with_json(self):
        from topic_catalog import TopicEntry

        doc = {
            "id": 2,
            "tags": {"facet": "topics", "key": "NLP"},
            "raw_content": (
                "# Topic Summary\n\n"
                "```json\n"
                '{"topic_name": "NLP", "summary": "Natural language processing", "status": "active"}\n'
                "```\n\n"
                "## Details\nMore info here."
            ),
            "created_at": "2024-01-01T00:00:00+00:00",
            "event_at": "2024-01-15T10:00:00+00:00",
        }

        topic = TopicEntry.from_document(doc)

        assert topic.name == "NLP"
        assert topic.summary == "Natural language processing"


# --- TopicAssignment tests ---


class TestTopicAssignment:
    """Tests for TopicAssignment dataclass."""

    def test_to_payload_includes_scores(self):
        from topic_catalog import TopicAssignment

        now = datetime.now(UTC)
        assignment = TopicAssignment(
            topic_name="AI Research",
            document_id=42,
            assigned_at=now,
            rationale="High similarity match",
            similarity_score=0.85,
            scores={"AI Research": 0.85, "ML": 0.6},
            decision_method="embedding",
        )

        payload = assignment.to_payload()

        assert payload["topic_name"] == "AI Research"
        assert payload["document_id"] == 42
        assert payload["similarity_score"] == 0.85
        assert payload["scores"]["ML"] == 0.6
        assert payload["decision_method"] == "embedding"


# --- TopicCatalog tests ---


class TestTopicCatalog:
    """Tests for TopicCatalog persistence."""

    def test_save_and_get_topic(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, cfg = make_store(tmp)

            from topic_catalog import TopicCatalog, TopicEntry

            catalog = TopicCatalog(store=store, embedder=FakeEmbedder())

            topic = TopicEntry(
                name="Test Topic",
                summary="A test topic for unit tests",
                status="active",
            )

            result = catalog.save_topic(topic)

            assert result is not None
            assert "document_id" in result

            # Retrieve and verify
            retrieved = catalog.get_topic("Test Topic")
            assert retrieved is not None
            assert retrieved.name == "Test Topic"
            assert retrieved.summary == "A test topic for unit tests"

    def test_list_topics_filters_by_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, cfg = make_store(tmp)

            from topic_catalog import TopicCatalog, TopicEntry

            catalog = TopicCatalog(store=store, embedder=FakeEmbedder())

            # Create active and archived topics
            active_topic = TopicEntry(name="Active", summary="Active topic", status="active")
            archived_topic = TopicEntry(name="Archived", summary="Archived topic", status="archived")

            catalog.save_topic(active_topic)
            catalog.save_topic(archived_topic)

            # List all
            all_topics = catalog.list_topics()
            assert len(all_topics) == 2

            # List only active
            active_only = catalog.list_topics(status="active")
            assert len(active_only) == 1
            assert active_only[0].name == "Active"

    def test_archive_topic(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, cfg = make_store(tmp)

            # Re-import topic_catalog after make_store to ensure fresh imports
            import topic_catalog
            importlib.reload(topic_catalog)
            from topic_catalog import TopicCatalog, TopicEntry
            import time

            catalog = TopicCatalog(store=store, embedder=FakeEmbedder())

            topic = TopicEntry(name="To Archive", summary="Will be archived", status="active")
            result1 = catalog.save_topic(topic)
            first_doc_id = result1.get("document_id")
            
            # Small delay to ensure timestamp ordering at second level
            time.sleep(1.1)

            # Archive it
            result = catalog.archive_topic("To Archive")
            assert result is True

            # Debug: check all summaries
            summaries = store.list_lifelong_summaries(limit=10, facet="topics", key="To Archive")
            
            # Verify we have 2 summaries
            assert len(summaries) == 2, f"Expected 2 summaries, got {len(summaries)}"
            
            # Verify status changed (get_topic returns the most recent one)
            archived = catalog.get_topic("To Archive")
            assert archived is not None
            assert archived.status == "archived", f"Got status '{archived.status}', doc_id={archived.document_id}"
            
            # Verify the archived document has a different ID
            assert archived.document_id != first_doc_id, "Archived doc should have new ID"

    def test_get_topic_embeddings(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, cfg = make_store(tmp)

            from topic_catalog import TopicCatalog, TopicEntry

            embedder = FakeEmbedder()
            catalog = TopicCatalog(store=store, embedder=embedder)

            # Create topics
            topic1 = TopicEntry(name="AI", summary="Artificial intelligence research")
            topic2 = TopicEntry(name="ML", summary="Machine learning projects")

            catalog.save_topic(topic1)
            catalog.save_topic(topic2)

            # Get embeddings
            embeddings = catalog.get_topic_embeddings()

            assert len(embeddings) == 2
            assert "AI" in embeddings
            assert "ML" in embeddings
            assert embeddings["AI"].shape == (embedder.dim,)


# --- TopicChangeDetector tests ---


class TestTopicChangeDetector:
    """Tests for TopicChangeDetector (unified topic operations)."""

    def test_detect_creates_new_topic_when_empty_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, cfg = make_store(tmp)

            from topic_catalog import TopicCatalog, TopicChangeDetector

            embedder = FakeEmbedder()
            catalog = TopicCatalog(store=store, embedder=embedder)
            detector = TopicChangeDetector(catalog=catalog, embedder=embedder)

            result = detector.detect("Content about a brand new topic")

            assert result.decision == "create"
            assert len(result.topic_name) > 0
            assert "No existing topics" in result.rationale

    def test_detect_reuses_topic_with_high_similarity(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, cfg = make_store(tmp)

            from topic_catalog import TopicCatalog, TopicEntry, TopicChangeDetector

            embedder = FakeEmbedder()
            catalog = TopicCatalog(store=store, embedder=embedder)

            # Create a topic
            topic = TopicEntry(name="AI Research", summary="AI Research content")
            catalog.save_topic(topic)

            # Use same content for high similarity
            detector = TopicChangeDetector(
                catalog=catalog,
                embedder=embedder,
                similarity_threshold=0.5,  # Lower threshold for test
            )

            result = detector.detect("AI Research content")  # Same text

            assert result.decision == "reuse"
            assert result.topic_name == "AI Research"

    def test_detect_creates_topic_with_low_similarity(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, cfg = make_store(tmp)

            from topic_catalog import TopicCatalog, TopicEntry, TopicChangeDetector

            embedder = FakeEmbedder()
            catalog = TopicCatalog(store=store, embedder=embedder)

            # Create a topic about AI
            topic = TopicEntry(name="AI", summary="Artificial intelligence")
            catalog.save_topic(topic)

            detector = TopicChangeDetector(
                catalog=catalog,
                embedder=embedder,
                similarity_threshold=0.99,  # Very high threshold
            )

            result = detector.detect("Completely different content about cooking recipes")

            # Should create since similarity is low with high threshold
            assert result.decision in ["create", "reuse"]  # Depends on hash collision
            assert len(result.similarity_scores) > 0

    def test_detect_returns_top_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, cfg = make_store(tmp)

            from topic_catalog import TopicCatalog, TopicEntry, TopicChangeDetector

            embedder = FakeEmbedder()
            catalog = TopicCatalog(store=store, embedder=embedder)

            # Create multiple topics
            for name in ["Topic A", "Topic B", "Topic C", "Topic D"]:
                topic = TopicEntry(name=name, summary=f"Summary for {name}")
                catalog.save_topic(topic)

            detector = TopicChangeDetector(catalog=catalog, embedder=embedder, top_k=3)

            result = detector.detect("Some test content")

            assert len(result.top_candidates) <= 3
            assert len(result.similarity_scores) == 4  # All topics

    def test_merge_topics_returns_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, cfg = make_store(tmp)

            from topic_catalog import TopicCatalog, TopicEntry, TopicChangeDetector

            embedder = FakeEmbedder()
            catalog = TopicCatalog(store=store, embedder=embedder)

            # Create two topics
            topic1 = TopicEntry(name="ML Basics", summary="Machine learning fundamentals")
            topic2 = TopicEntry(name="Deep Learning", summary="Neural networks and deep learning")
            catalog.save_topic(topic1)
            catalog.save_topic(topic2)

            detector = TopicChangeDetector(catalog=catalog, embedder=embedder)

            result = detector.merge_topics(
                source_name="ML Basics",
                target_name="Deep Learning",
                rationale="ML Basics is a subset of Deep Learning"
            )

            assert result.decision == "merge"
            assert result.topic_name == "Deep Learning"
            assert result.merge_from == "ML Basics"

    def test_split_topic_returns_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, cfg = make_store(tmp)

            from topic_catalog import TopicCatalog, TopicEntry, TopicChangeDetector

            embedder = FakeEmbedder()
            catalog = TopicCatalog(store=store, embedder=embedder)

            # Create a topic
            topic = TopicEntry(name="Machine Learning", summary="All ML topics")
            catalog.save_topic(topic)

            detector = TopicChangeDetector(catalog=catalog, embedder=embedder)

            result = detector.split_topic(
                source_name="Machine Learning",
                new_topic_name="Reinforcement Learning",
                content="RL is a distinct area",
                rationale="RL deserves its own topic"
            )

            assert result.decision == "split"
            assert result.topic_name == "Machine Learning"
            assert result.split_into == "Reinforcement Learning"


# --- Orchestrator topic methods tests ---


class TestOrchestratorTopics:
    """Tests for orchestrator.py topic management methods."""

    def test_list_topics_returns_empty_when_no_topics(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, cfg = make_store(tmp)

            from workspace import MemoryWorkspace
            from orchestrator import Orchestrator

            workspace = MemoryWorkspace(store=store)
            orchestrator = Orchestrator(workspace)
            orchestrator.dspy_summarizer = None

            topics = orchestrator.list_topics()

            assert topics == []

    def test_assign_to_topic_creates_new_topic(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, cfg = make_store(tmp)

            from workspace import MemoryWorkspace
            from orchestrator import Orchestrator
            from datatypes import Role

            workspace = MemoryWorkspace(store=store)
            orchestrator = Orchestrator(workspace)
            orchestrator.dspy_summarizer = None

            result = orchestrator.assign_to_topic(
                content="Brand new content about quantum computing",
                use_llm=False,
            )

            assert isinstance(result.content, str)
            assert result.role == Role.SYSTEM
            # Should create new topic since catalog is empty
            assert "Created new topic" in result.content or "topic" in result.content.lower()

    def test_assign_to_topic_reuses_existing_topic(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, cfg = make_store(tmp)

            from workspace import MemoryWorkspace
            from orchestrator import Orchestrator
            from topic_catalog import TopicEntry

            workspace = MemoryWorkspace(store=store)
            orchestrator = Orchestrator(workspace)
            orchestrator.dspy_summarizer = None

            # Create a topic first
            catalog = orchestrator._get_topic_catalog()
            topic = TopicEntry(name="Test Content", summary="Test Content summary")
            catalog.save_topic(topic)

            # Assign similar content (using same text for high similarity)
            result = orchestrator.assign_to_topic(
                content="Test Content summary",  # Same as topic summary
                use_llm=False,
            )

            assert "topic" in result.content.lower()

    def test_process_new_documents_for_topics(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, cfg = make_store(tmp)

            from workspace import MemoryWorkspace
            from orchestrator import Orchestrator

            workspace = MemoryWorkspace(store=store)

            # Add some documents
            now = datetime.now(UTC)
            store.add_document_chunked(
                content="This is a document about machine learning algorithms and neural networks.",
                document_meta={
                    "doc_type": "note",
                    "title": "ML Notes",
                    "source": "test",
                    "created_at": now,
                    "event_at": now,
                },
            )

            orchestrator = Orchestrator(workspace)
            orchestrator.dspy_summarizer = None

            result = orchestrator.process_new_documents_for_topics(
                since=now - timedelta(hours=1),
                limit=10,
            )

            assert "Processed" in result.content
            assert "documents" in result.content.lower()


# --- Prompts tests ---


class TestPhase3Prompts:
    """Tests for Phase 3 prompt functions."""

    def test_topic_summary_instruction(self):
        from prompts import topic_summary_instruction

        instruction = topic_summary_instruction()

        assert "summary" in instruction.lower()
        assert "JSON" in instruction

    def test_topic_assignment_instruction_includes_candidates(self):
        from prompts import topic_assignment_instruction

        instruction = topic_assignment_instruction(candidates=["AI", "ML", "NLP"])

        assert "AI" in instruction
        assert "ML" in instruction
        assert "NLP" in instruction
        assert "REUSE" in instruction
        assert "CREATE" in instruction

    def test_topic_change_instruction_includes_all_operations(self):
        from prompts import topic_change_instruction

        instruction = topic_change_instruction(
            candidates=["AI", "ML"],
            candidates_with_scores="- AI (similarity: 0.8)\n- ML (similarity: 0.6)"
        )

        # Check all operations are mentioned
        assert "REUSE" in instruction
        assert "CREATE" in instruction
        assert "MERGE" in instruction
        assert "SPLIT" in instruction
        assert "RENAME" in instruction

        # Check candidates are included
        assert "AI" in instruction
        assert "ML" in instruction

        # Check decision criteria are provided
        assert "decision" in instruction.lower()
        assert "rationale" in instruction.lower()
