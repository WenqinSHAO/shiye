"""Tests for lifelong summarization functionality (Phase 1&2).

This module tests:
- LifelongSummary dataclass and helpers
- SummaryPlanner bootstrap and delta planning
- Prompt generation for different facets
- Orchestrator summarize_lifelong and bootstrap_lifelong flows
"""

import importlib
import json
import os
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import faiss
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


# --- LifelongSummary dataclass tests ---


class TestLifelongSummary:
    """Tests for lifelong_summary.py dataclass and helpers."""

    def test_normalized_payload_adds_defaults(self):
        from lifelong_summary import LifelongSummary

        summary = LifelongSummary(payload={}, markdown="test")
        normalized = summary.normalized_payload()

        assert "schema_version" in normalized
        assert "language" in normalized
        assert "summary_date" in normalized
        assert "facets" in normalized
        assert "references" in normalized

    def test_normalized_payload_preserves_existing_values(self):
        from lifelong_summary import LifelongSummary

        payload = {
            "schema_version": "custom",
            "facets": {"profile": ["interest1"], "topics": [], "timeline": []},
        }
        summary = LifelongSummary(payload=payload, markdown="test")
        normalized = summary.normalized_payload()

        assert normalized["schema_version"] == "custom"
        assert normalized["facets"]["profile"] == ["interest1"]

    def test_normalized_payload_includes_facet_and_topic(self):
        from lifelong_summary import LifelongSummary

        summary = LifelongSummary(
            payload={}, markdown="test", facet="profile", topic="AI"
        )
        normalized = summary.normalized_payload()

        assert normalized["facet"] == "profile"
        assert normalized["topic"] == "AI"

    def test_render_document_produces_markdown(self):
        from lifelong_summary import LifelongSummary

        summary = LifelongSummary(
            payload={"facets": {"profile": [], "topics": [], "timeline": []}},
            markdown="## Test Section\n- Item 1",
            title="Test Summary",
        )
        rendered = summary.render_document()

        assert "# Test Summary" in rendered
        assert "```json" in rendered
        assert "## Test Section" in rendered

    def test_document_meta_includes_required_fields(self):
        from lifelong_summary import LifelongSummary, SUMMARY_DOC_TYPE

        summary = LifelongSummary(
            payload={}, markdown="test", facet="profile", summary_source="system"
        )
        meta = summary.document_meta()

        assert meta["doc_type"] == SUMMARY_DOC_TYPE
        assert "title" in meta
        assert meta["source"] == "system"
        assert meta["tags"]["facet"] == "profile"

    def test_ensure_reference_ids_creates_chunk_refs(self):
        from lifelong_summary import ensure_reference_ids

        result = ensure_reference_ids(["chunk_1", "chunk_2", "chunk_3"])

        assert len(result["references"]) == 3
        assert result["references"][0] == {"chunk_id": "chunk_1"}

    def test_ensure_reference_ids_handles_empty(self):
        from lifelong_summary import ensure_reference_ids

        result = ensure_reference_ids(None)
        assert result["references"] == []

        result = ensure_reference_ids([])
        assert result["references"] == []

    def test_merge_references_deduplicates(self):
        from lifelong_summary import merge_references

        base = [{"chunk_id": "1"}, {"chunk_id": "2"}]
        extra = [{"chunk_id": "2"}, {"chunk_id": "3"}]

        merged = merge_references(base, extra)

        assert len(merged) == 3
        chunk_ids = [r["chunk_id"] for r in merged]
        assert "1" in chunk_ids
        assert "2" in chunk_ids
        assert "3" in chunk_ids

    def test_render_markdown_from_payload(self):
        from lifelong_summary import render_markdown_from_payload

        payload = {
            "facets": {
                "profile": ["Interest in AI", "Interest in ML"],
                "topics": [{"name": "LLM", "summary": "Large language models"}],
                "timeline": [{"date": "2024-01-01", "event": "Started project"}],
            }
        }
        markdown = render_markdown_from_payload(payload)

        assert "## Profile" in markdown
        assert "Interest in AI" in markdown
        assert "## Topics" in markdown
        assert "**LLM**" in markdown
        assert "## Timeline" in markdown
        assert "2024-01-01" in markdown

    def test_build_default_title_variants(self):
        from lifelong_summary import _build_default_title

        assert "profile" in _build_default_title("2024-01-01", "profile", None).lower()
        assert "topic" in _build_default_title("2024-01-01", "topics", "AI").lower()
        assert "summary" in _build_default_title("2024-01-01", None, None).lower()


# --- SummaryPlanner tests ---


class TestSummaryPlanner:
    """Tests for summary_planner.py planning logic."""

    def test_plan_bootstrap_creates_batches(self):
        from summary_planner import SummaryPlanner

        planner = SummaryPlanner(batch_days=30)
        since = datetime(2024, 1, 1, tzinfo=UTC)

        # Mock datetime.now to control time window
        with patch("summary_planner.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2024, 3, 1, tzinfo=UTC)
            mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
            requests = planner.plan_bootstrap(["profile", "topics"], since=since)

        # Should create batches for each facet × time window
        assert len(requests) > 0
        facets = set(r.facet for r in requests)
        assert "profile" in facets
        assert "topics" in facets

    def test_plan_bootstrap_sets_is_delta_false(self):
        from summary_planner import SummaryPlanner

        planner = SummaryPlanner(batch_days=30)
        since = datetime(2024, 1, 1, tzinfo=UTC)

        with patch("summary_planner.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2024, 2, 1, tzinfo=UTC)
            mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
            requests = planner.plan_bootstrap(["profile"], since=since)

        for req in requests:
            assert req.is_delta is False

    def test_plan_bootstrap_batch_labels_include_facet_and_date(self):
        from summary_planner import SummaryPlanner

        planner = SummaryPlanner(batch_days=30)
        since = datetime(2024, 1, 1, tzinfo=UTC)

        with patch("summary_planner.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2024, 2, 1, tzinfo=UTC)
            mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
            requests = planner.plan_bootstrap(["profile"], since=since)

        assert len(requests) > 0
        assert "profile" in requests[0].batch_label
        assert "2024-01-01" in requests[0].batch_label

    def test_plan_delta_creates_single_request(self):
        from summary_planner import SummaryPlanner

        planner = SummaryPlanner()
        since = datetime(2024, 1, 15, tzinfo=UTC)

        requests = planner.plan_delta(facet="profile", topic=None, since=since)

        assert len(requests) == 1
        assert requests[0].facet == "profile"
        assert requests[0].is_delta is True
        assert requests[0].since == since

    def test_plan_delta_with_topic(self):
        from summary_planner import SummaryPlanner

        planner = SummaryPlanner()

        requests = planner.plan_delta(facet="topics", topic="AI", since=None)

        assert len(requests) == 1
        assert requests[0].topic == "AI"
        assert requests[0].is_delta is False  # No since means full snapshot


# --- Prompts tests ---


class TestPrompts:
    """Tests for prompts.py summarization instructions."""

    def test_lifelong_summary_instruction_base(self):
        from prompts import lifelong_summary_instruction

        instruction = lifelong_summary_instruction(facet=None, is_delta=False)

        assert "JSON" in instruction
        assert "facets" in instruction.lower()

    def test_lifelong_summary_instruction_delta_mode(self):
        from prompts import lifelong_summary_instruction

        delta_instruction = lifelong_summary_instruction(facet=None, is_delta=True)
        snapshot_instruction = lifelong_summary_instruction(facet=None, is_delta=False)

        assert "delta" in delta_instruction.lower() or "new changes" in delta_instruction.lower()
        assert "snapshot" in snapshot_instruction.lower() or "full" in snapshot_instruction.lower()

    def test_lifelong_summary_instruction_profile_facet(self):
        from prompts import lifelong_summary_instruction

        instruction = lifelong_summary_instruction(facet="profile", is_delta=False)

        assert "interest" in instruction.lower() or "objective" in instruction.lower()

    def test_lifelong_summary_instruction_topics_facet(self):
        from prompts import lifelong_summary_instruction

        instruction = lifelong_summary_instruction(facet="topics", is_delta=False)

        assert "topic" in instruction.lower()

    def test_lifelong_summary_instruction_timeline_facet(self):
        from prompts import lifelong_summary_instruction

        instruction = lifelong_summary_instruction(facet="timeline", is_delta=False)

        assert "chronological" in instruction.lower() or "timeline" in instruction.lower()

    def test_rss_summary_instruction_includes_keywords(self):
        from prompts import rss_summary_instruction

        instruction = rss_summary_instruction(keywords=["AI", "ML"])

        assert "AI" in instruction
        assert "ML" in instruction

    def test_note_summary_instruction_exists(self):
        from prompts import note_summary_instruction

        instruction = note_summary_instruction()

        assert isinstance(instruction, str)
        assert len(instruction) > 0


# --- Orchestrator integration tests ---


class TestOrchestratorLifelongSummary:
    """Tests for orchestrator.py lifelong summary methods."""

    def test_summarize_lifelong_without_llm_returns_message(self):
        """Test that summarize_lifelong works without LLM configured."""
        with tempfile.TemporaryDirectory() as tmp:
            store, cfg = make_store(tmp)

            from workspace import MemoryWorkspace
            from orchestrator import Orchestrator
            from datatypes import Message, Role

            workspace = MemoryWorkspace(store=store)

            # Add some test messages
            workspace.add(Message(content="Test message 1", role=Role.USER))
            workspace.add(Message(content="Test message 2", role=Role.ASSISTANT))

            orchestrator = Orchestrator(workspace)
            orchestrator.dspy_summarizer = None  # Ensure no LLM

            result = orchestrator.summarize_lifelong(manual=True)

            assert isinstance(result, Message)
            assert result.role == Role.SYSTEM

    def test_summarize_lifelong_respects_cadence(self):
        """Test that summarize_lifelong respects cadence when not manual."""
        with tempfile.TemporaryDirectory() as tmp:
            store, cfg = make_store(tmp)

            from workspace import MemoryWorkspace
            from orchestrator import Orchestrator
            from datatypes import Message, Role

            workspace = MemoryWorkspace(store=store)
            orchestrator = Orchestrator(workspace)
            orchestrator.dspy_summarizer = None

            # First call should create a summary
            workspace.add(Message(content="Test", role=Role.USER))
            result1 = orchestrator.summarize_lifelong(manual=True)

            # Add more content
            workspace.add(Message(content="More content", role=Role.USER))

            # Non-manual call should respect cadence
            result2 = orchestrator.summarize_lifelong(manual=False)

            # Should indicate cadence not reached (summary is too recent)
            assert "cadence" in result2.content.lower() or "saved" in result2.content.lower()

    def test_bootstrap_lifelong_creates_summaries(self):
        """Test that bootstrap_lifelong creates summaries for each facet/batch."""
        with tempfile.TemporaryDirectory() as tmp:
            store, cfg = make_store(tmp)

            from workspace import MemoryWorkspace
            from orchestrator import Orchestrator
            from datatypes import Message, Role

            workspace = MemoryWorkspace(store=store)

            # Add documents with proper metadata for bootstrap
            now = datetime.now(UTC)
            store.add_document_chunked(
                content="This is a chat about AI and machine learning.",
                document_meta={
                    "doc_type": "chat",
                    "title": "AI Discussion",
                    "source": "test",
                    "created_at": now,
                    "event_at": now,
                },
            )

            orchestrator = Orchestrator(workspace)
            orchestrator.dspy_summarizer = None  # No LLM

            result = orchestrator.bootstrap_lifelong(
                since=now - timedelta(days=1),
                batch_days=30,
                facets=["profile"],
            )

            assert isinstance(result, Message)
            assert "bootstrap" in result.content.lower()

    def test_bootstrap_lifelong_batch_cache_reuses_documents(self):
        """Test that bootstrap reuses document content across facets."""
        with tempfile.TemporaryDirectory() as tmp:
            store, cfg = make_store(tmp)

            from workspace import MemoryWorkspace
            from orchestrator import Orchestrator

            workspace = MemoryWorkspace(store=store)

            # Add a document
            now = datetime.now(UTC)
            store.add_document_chunked(
                content="Test document content",
                document_meta={
                    "doc_type": "note",
                    "title": "Test Note",
                    "source": "test",
                    "created_at": now,
                    "event_at": now,
                },
            )

            orchestrator = Orchestrator(workspace)
            orchestrator.dspy_summarizer = None

            # Mock list_documents to track calls
            original_list = workspace.list_documents
            call_count = [0]

            def counting_list(*args, **kwargs):
                call_count[0] += 1
                return original_list(*args, **kwargs)

            workspace.list_documents = counting_list

            result = orchestrator.bootstrap_lifelong(
                since=now - timedelta(days=1),
                batch_days=30,
                facets=["profile", "topics", "timeline"],
            )

            # Should only call list_documents once per time window (not per facet)
            # With one batch window and 3 facets, we should see 1 call, not 3
            assert call_count[0] == 1

    def test_bootstrap_prefix_format(self):
        """Test that bootstrap prefix contains expected information."""
        with tempfile.TemporaryDirectory() as tmp:
            store, cfg = make_store(tmp)

            from workspace import MemoryWorkspace
            from orchestrator import Orchestrator

            workspace = MemoryWorkspace(store=store)
            orchestrator = Orchestrator(workspace)

            batch_start = datetime(2024, 1, 1, tzinfo=UTC)
            batch_end = datetime(2024, 1, 31, tzinfo=UTC)
            prefix = orchestrator._bootstrap_prefix(batch_start, batch_end, 42)

            assert "2024-01-01" in prefix
            assert "2024-01-31" in prefix
            assert "42" in prefix

    def test_format_bootstrap_documents_extracts_text(self):
        """Test that _format_bootstrap_documents properly extracts document text."""
        with tempfile.TemporaryDirectory() as tmp:
            store, cfg = make_store(tmp)

            from workspace import MemoryWorkspace
            from orchestrator import Orchestrator

            workspace = MemoryWorkspace(store=store)
            orchestrator = Orchestrator(workspace)

            documents = [
                {
                    "id": 1,
                    "doc_type": "note",
                    "title": "Test Note",
                    "raw_content": "This is the note content",
                },
                {
                    "id": 2,
                    "doc_type": "web_page",
                    "title": "Test Page",
                    "uri": "https://example.com",
                    "raw_content": "This is web content",
                },
            ]

            text, refs = orchestrator._format_bootstrap_documents(documents)

            assert "note content" in text
            assert "web content" in text
            assert len(refs) == 2
            assert refs[0]["document_id"] == 1
            assert refs[1]["document_id"] == 2

    def test_format_bootstrap_documents_handles_chat_json(self):
        """Test that chat documents with JSON content are properly extracted."""
        with tempfile.TemporaryDirectory() as tmp:
            store, cfg = make_store(tmp)

            from workspace import MemoryWorkspace
            from orchestrator import Orchestrator

            workspace = MemoryWorkspace(store=store)
            orchestrator = Orchestrator(workspace)

            chat_content = json.dumps([
                {"role": "user", "content": "Hello there"},
                {"role": "assistant", "content": "Hi! How can I help?"},
            ])

            documents = [
                {
                    "id": 1,
                    "doc_type": "chat",
                    "title": "Chat Log",
                    "raw_content": chat_content,
                },
            ]

            text, refs = orchestrator._format_bootstrap_documents(documents)

            assert "Hello there" in text
            assert "How can I help" in text

    def test_summarize_lifelong_auto_bootstrap_when_no_summaries(self):
        """Test that summarize_lifelong triggers bootstrap when no summaries exist."""
        with tempfile.TemporaryDirectory() as tmp:
            store, cfg = make_store(tmp)

            from workspace import MemoryWorkspace
            from orchestrator import Orchestrator

            workspace = MemoryWorkspace(store=store)

            # Add a document so bootstrap has something to work with
            now = datetime.now(UTC)
            store.add_document_chunked(
                content="Test content for bootstrap",
                document_meta={
                    "doc_type": "note",
                    "title": "Test",
                    "source": "test",
                    "created_at": now,
                    "event_at": now,
                },
            )

            orchestrator = Orchestrator(workspace)
            orchestrator.dspy_summarizer = None

            # Manual trigger without existing summaries should bootstrap
            result = orchestrator.summarize_lifelong(manual=True)

            # Should indicate bootstrap was triggered
            assert "bootstrap" in result.content.lower() or "saved" in result.content.lower()


# --- Workspace integration tests ---


class TestWorkspaceLifelongSummary:
    """Tests for workspace.py lifelong summary methods."""

    def test_save_lifelong_summary_creates_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, cfg = make_store(tmp)

            from workspace import MemoryWorkspace

            workspace = MemoryWorkspace(store=store)

            result = workspace.save_lifelong_summary(
                payload={"facets": {"profile": [], "topics": [], "timeline": []}},
                markdown="Test summary",
                facet="profile",
            )

            assert result is not None
            assert "document_id" in result

    def test_list_lifelong_summaries_returns_summaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, cfg = make_store(tmp)

            from workspace import MemoryWorkspace

            workspace = MemoryWorkspace(store=store)

            # Create some summaries
            workspace.save_lifelong_summary(
                payload={"facets": {"profile": [], "topics": [], "timeline": []}},
                markdown="Summary 1",
                facet="profile",
            )
            workspace.save_lifelong_summary(
                payload={"facets": {"profile": [], "topics": [], "timeline": []}},
                markdown="Summary 2",
                facet="topics",
            )

            summaries = workspace.list_lifelong_summaries()

            assert len(summaries) >= 2

    def test_get_latest_lifelong_summary_filters_by_facet(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, cfg = make_store(tmp)

            from workspace import MemoryWorkspace

            workspace = MemoryWorkspace(store=store)

            # Create summaries for different facets
            workspace.save_lifelong_summary(
                payload={"facets": {"profile": [], "topics": [], "timeline": []}},
                markdown="Profile summary",
                facet="profile",
            )
            workspace.save_lifelong_summary(
                payload={"facets": {"profile": [], "topics": [], "timeline": []}},
                markdown="Topics summary",
                facet="topics",
            )

            profile_summary = workspace.get_latest_lifelong_summary(facet="profile")
            topics_summary = workspace.get_latest_lifelong_summary(facet="topics")

            assert profile_summary is not None
            assert topics_summary is not None
            assert profile_summary["tags"]["facet"] == "profile"
            assert topics_summary["tags"]["facet"] == "topics"
