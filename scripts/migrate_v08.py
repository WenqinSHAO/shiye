#!/usr/bin/env python3
"""
Migration script for v0.7 to v0.8 chunking enhancements.

This script re-chunks existing documents with the new v0.8 chunkers and updates:
- Chunks with proper metadata (heading_path, page_number, parent_doc_seq)
- Documents with chunk_strategy and chunk_version
- FAISS index with proper embeddings
- embedding_id for all chunks

Usage:
    python scripts/migrate_v08.py [--verbose] [--dry-run] [--doc-type TYPE]
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import List, Optional, Union

import numpy as np

# Add parent directory to path to import modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from chunking import (
    Chunk,
    count_tokens,
    get_chunker_for_doctype,
    MessageChunker,
    _get_tokenizer,
)
from config import DB_PATH, DATA_DIR, MODEL_NAME
from datatypes import Role
from embeddings import EmbeddingProvider
from storage import LocalStore
from vector_store import FaissIndex

EXPECTED_STRATEGY_CASE = """
CASE doc_type
    WHEN 'chat' THEN 'per-message'
    WHEN 'note' THEN 'header-aware'
    WHEN 'web_page' THEN 'header-aware'
    WHEN 'paper' THEN 'sentence-window'
    WHEN 'rss_daily_summary' THEN 'fixed-token'
    ELSE NULL
END
"""

MIGRATABLE_WHERE_CLAUSE = f"""
chunk_version IS NULL
    OR chunk_version < 1
    OR chunk_strategy IS NULL
    OR chunk_strategy NOT IN ('header-aware', 'sentence-window', 'fixed-token', 'per-message', 'single-chunk')
    OR (
        chunk_strategy IS NOT NULL
        AND {EXPECTED_STRATEGY_CASE} IS NOT NULL
        AND chunk_strategy != {EXPECTED_STRATEGY_CASE}
    )
"""

NORMALIZED_STRATEGIES = {'header-aware', 'sentence-window', 'fixed-token', 'per-message', 'single-chunk'}


def get_embedding_max_tokens(embedder) -> Optional[int]:
    """Infer the maximum token length supported by the embedder/model."""
    max_tokens = None
    
    # Prefer embedder model hints
    model = getattr(embedder, "model", None) if embedder else None
    if model:
        try:
            if hasattr(model, "get_max_seq_length"):
                max_tokens = model.get_max_seq_length()
        except Exception:
            max_tokens = None
        if max_tokens is None:
            max_tokens = getattr(model, "max_seq_length", None)
    
    # Fallback to tokenizer config from chunking module
    if not max_tokens:
        try:
            from chunking import _get_tokenizer
            
            tok = _get_tokenizer()
            if tok not in (None, "fallback"):
                max_tokens = getattr(tok, "model_max_length", None) or getattr(tok, "max_len_single_sentence", None)
                # Ignore absurdly large sentinel values
                if max_tokens and max_tokens > 100000:
                    max_tokens = None
        except Exception:
            max_tokens = None
    
    try:
        max_tokens = int(max_tokens) if max_tokens else None
    except Exception:
        max_tokens = None
    
    # Sensible default for MiniLM-class models
    if not max_tokens or max_tokens <= 0:
        max_tokens = 512
    
    return max_tokens


def enforce_embedding_token_limit(chunks: List[Chunk], max_tokens: int) -> List[tuple[Chunk, int]]:
    """Ensure chunks do not exceed the embedder's max token length.
    
    Returns a list of (chunk, parent_seq) where parent_seq tracks the original
    chunk sequence before any splitting.
    """
    if not chunks or not max_tokens or max_tokens <= 0:
        return [(c, c.seq) for c in chunks]
    
    limited_chunks: List[tuple[Chunk, int]] = []
    seq = 0

    def _split_chunk_preserving_text(chunk: Chunk) -> List[Chunk]:
        tok = _get_tokenizer()
        text = chunk.text
        
        def char_split():
            approx_chars = max_tokens * 4
            pieces = []
            start = 0
            while start < len(text):
                end = min(len(text), start + approx_chars)
                piece = text[start:end]
                pieces.append(Chunk(
                    text=piece,
                    char_start=chunk.char_start + start,
                    char_end=chunk.char_start + end,
                    seq=0,
                    heading_path=chunk.heading_path,
                    page_number=chunk.page_number,
                    token_count=count_tokens(piece),
                ))
                start = end
            return pieces
        
        # Fallback: simple char-based slicing when tokenizer unavailable
        if tok == "fallback" or tok is None:
            return char_split()
        
        # Use tokenizer offsets to slice without altering text (important for CJK)
        try:
            encoded = tok(
                text,
                add_special_tokens=False,
                return_offsets_mapping=True,
            )
            offsets = encoded.get("offset_mapping")
            if not offsets:
                return char_split()
        except Exception:
            return char_split()
        
        pieces: List[Chunk] = []
        start_idx = 0
        while start_idx < len(offsets):
            end_idx = min(start_idx + max_tokens, len(offsets))
            start_char = offsets[start_idx][0]
            end_char = offsets[end_idx - 1][1]
            piece_text = text[start_char:end_char]
            pieces.append(Chunk(
                text=piece_text,
                char_start=chunk.char_start + start_char,
                char_end=chunk.char_start + end_char,
                seq=0,
                heading_path=chunk.heading_path,
                page_number=chunk.page_number,
                token_count=end_idx - start_idx,
            ))
            start_idx = end_idx
        return pieces
    
    for chunk in chunks:
        parent_seq = chunk.seq
        token_count = chunk.token_count or count_tokens(chunk.text)
        
        if token_count and token_count > max_tokens:
            sub_chunks = _split_chunk_preserving_text(chunk)
            if not sub_chunks:
                sub_chunks = [Chunk(text=chunk.text, char_start=chunk.char_start, char_end=chunk.char_end, seq=0, heading_path=chunk.heading_path, page_number=chunk.page_number, token_count=count_tokens(chunk.text))]
            
            for sub in sub_chunks:
                adjusted = Chunk(
                    text=sub.text,
                    char_start=sub.char_start,
                    char_end=sub.char_end,
                    seq=seq,
                    heading_path=chunk.heading_path,
                    page_number=chunk.page_number,
                    token_count=sub.token_count,
                )
                limited_chunks.append((adjusted, parent_seq))
                seq += 1
        else:
            adjusted = Chunk(
                text=chunk.text,
                char_start=chunk.char_start,
                char_end=chunk.char_end,
                seq=seq,
                heading_path=chunk.heading_path,
                page_number=chunk.page_number,
                token_count=token_count,
            )
            limited_chunks.append((adjusted, parent_seq))
            seq += 1
    
    return limited_chunks


def normalize_chunk_strategy(chunker_class_name: str) -> str:
    """Normalize chunk strategy name to match storage.py conventions.
    
    Args:
        chunker_class_name: Class name like 'HeaderAwareChunker', 'MessageChunker', etc.
        
    Returns:
        Normalized strategy name: 'header-aware', 'sentence-window', 'fixed-token', or 'per-message'
    """
    strategy = chunker_class_name.replace('Chunker', '').lower()
    if 'headeraware' in strategy:
        return 'header-aware'
    elif 'sentencewindow' in strategy:
        return 'sentence-window'
    elif 'fixedtoken' in strategy:
        return 'fixed-token'
    elif 'message' in strategy:
        return 'per-message'
    return strategy


def expected_strategy_for_doc_type(doc_type: Optional[str]) -> Optional[str]:
    """Return normalized strategy expected for a given doc_type."""
    try:
        chunker = get_chunker_for_doctype(doc_type or 'web_page')
    except Exception:
        return None
    return normalize_chunk_strategy(type(chunker).__name__)


def should_migrate_doc(doc_row) -> bool:
    """Determine if a document row should be migrated."""
    strategy = doc_row['chunk_strategy']
    version = doc_row['chunk_version']
    
    if strategy is None or version is None or version < 1:
        return True
    if strategy not in NORMALIZED_STRATEGIES:
        return True
    
    expected = expected_strategy_for_doc_type(doc_row['doc_type'])
    if expected and strategy != expected:
        return True
    return False


def get_document_chunks_metadata(store: LocalStore, doc_id: int):
    """Retrieve chunks with full metadata for migration.
    
    Args:
        store: LocalStore instance
        doc_id: Document ID to retrieve
        
    Returns:
        List of chunk rows with all metadata (text, role, created_at, event_at, embedding_id, etc.)
    """
    with store._connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, text, role, seq, created_at, event_at, embedding_id, tags, focus_hint, heading_path, page_number, parent_doc_seq, char_start, char_end
            FROM chunks
            WHERE document_id = ? AND deleted = 0
            ORDER BY seq ASC
            """,
            (doc_id,)
        )
        return cur.fetchall()


def _parse_chat_raw_content(raw_content: Optional[str]):
    """Parse stored raw_content for chat documents."""
    if not raw_content:
        return None, None
    try:
        data = json.loads(raw_content)
    except Exception:
        return None, None
    
    if not isinstance(data, list):
        return None, None
    
    messages = []
    metadata = []
    for item in data:
        if isinstance(item, dict):
            messages.append(item.get('content', '') or '')
            msg_tags = item.get('tags') or item.get('metadata') or {}
            if isinstance(msg_tags, str):
                try:
                    msg_tags = json.loads(msg_tags)
                except Exception:
                    msg_tags = {}
            metadata.append({
                'role': item.get('role'),
                'created_at': item.get('created_at'),
                'event_at': item.get('event_at') or item.get('reference_time'),
                'tags': msg_tags if isinstance(msg_tags, dict) else {},
                'focus_hint': item.get('focus_hint') or (msg_tags.get('focus_hint') if isinstance(msg_tags, dict) else None)
            })
        else:
            messages.append(str(item))
            metadata.append({})
    
    return messages, metadata


def _chunk_row_to_meta(row, doc_tags: dict) -> dict:
    """Normalize chunk row metadata for migration."""
    tags = {}
    raw_tags = row['tags'] if 'tags' in row.keys() else None
    if raw_tags:
        try:
            tags = json.loads(raw_tags)
        except Exception:
            tags = {}
    if not tags:
        tags = doc_tags
    return {
        'role': row['role'],
        'created_at': row['created_at'],
        'event_at': row['event_at'],
        'tags': tags,
        'focus_hint': row['focus_hint']
    }


def get_document_sources(
    store: LocalStore, doc_id: int, doc_type: str
) -> tuple[Optional[Union[str, List[str]]], List[dict], Optional[str], Optional[dict], List[dict]]:
    """Load document content, metadata, and raw content for migration."""
    with store._connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, doc_type, title, chunk_strategy, chunk_version, created_at, event_at, ingested_at, raw_content, tags
            FROM documents
            WHERE id = ?
            """,
            (doc_id,)
        )
        doc_row = cur.fetchone()
    
    doc_tags = {}
    if doc_row and doc_row['tags']:
        try:
            doc_tags = json.loads(doc_row['tags'])
        except Exception:
            doc_tags = {}
    
    chunks_meta = get_document_chunks_metadata(store, doc_id)
    raw_content = doc_row['raw_content'] if doc_row else None
    message_meta: List[dict] = []
    content: Optional[Union[str, List[str]]] = None
    
    if doc_type == 'chat':
        messages_from_raw, meta_from_raw = _parse_chat_raw_content(raw_content) if raw_content else (None, None)
        if messages_from_raw:
            content = messages_from_raw
            message_meta = meta_from_raw or []
        elif chunks_meta:
            content = [row['text'] for row in chunks_meta]
            message_meta = [_chunk_row_to_meta(row, doc_tags) for row in chunks_meta]
            def _safe_chunk_tags(value: Optional[str]) -> dict:
                if not value:
                    return doc_tags or {}
                try:
                    return json.loads(value)
                except Exception:
                    return doc_tags or {}
            # Build raw_content from existing chunks so we persist a source copy
            raw_content = json.dumps([
                {
                    "content": row['text'],
                    "role": row['role'],
                    "created_at": row['created_at'],
                    "event_at": row['event_at'],
                    "tags": _safe_chunk_tags(row['tags']),
                    "focus_hint": row['focus_hint'],
                }
                for row in chunks_meta
            ])
        else:
            # Empty chat document (e.g., default placeholder) - return empty list
            content = []
            message_meta = []
    else:
        if raw_content:
            content = raw_content
        elif chunks_meta:
            content = '\n'.join(row['text'] for row in chunks_meta)
            raw_content = content
    
    return content, message_meta, raw_content, doc_row, chunks_meta


def get_document_content(store: LocalStore, doc_id: int, doc_type: str) -> Optional[Union[str, List[str]]]:
    """Retrieve document content for re-chunking (compat wrapper for tests)."""
    content, _, _, _, _ = get_document_sources(store, doc_id, doc_type)
    return content


def migrate_document(
    store: LocalStore,
    doc_id: int,
    doc_type: str,
    verbose: bool = False,
    dry_run: bool = False
) -> dict:
    """Migrate a single document to v0.8 chunking format.
    
    Args:
        store: LocalStore instance
        doc_id: Document ID to migrate
        doc_type: Document type
        verbose: Print detailed progress
        dry_run: Don't actually modify the database
        
    Returns:
        Dict with migration statistics
    """
    stats = {
        'doc_id': doc_id,
        'success': False,
        'old_chunk_count': 0,
        'new_chunk_count': 0,
        'error': None,
        'skipped': False,
    }
    
    try:
        content, message_meta, raw_source, doc_row, old_chunks = get_document_sources(store, doc_id, doc_type)
        original_chunk_strategy = doc_row['chunk_strategy'] if doc_row else None
        original_chunk_version = doc_row['chunk_version'] if doc_row else None
        doc_created_at = doc_row['created_at'] if doc_row else None
        doc_event_at = doc_row['event_at'] if doc_row else None
        doc_ingested_at = doc_row['ingested_at'] if doc_row else None
        doc_tags = {}
        if doc_row and doc_row['tags']:
            try:
                doc_tags = json.loads(doc_row['tags'])
            except Exception:
                doc_tags = {}
        
        stats['old_chunk_count'] = len(old_chunks)
        
        if verbose:
            print(f"  Migrating doc {doc_id} ({doc_type}), {stats['old_chunk_count']} old chunks")
        
        content_missing = content is None or (isinstance(content, str) and not content.strip()) or (isinstance(content, list) and len(content) == 0)
        if not old_chunks and content_missing:
            # Even for empty documents, fix the strategy if it's wrong
            expected_strategy = expected_strategy_for_doc_type(doc_type)
            if expected_strategy and original_chunk_strategy != expected_strategy:
                if not dry_run:
                    with store._connect() as conn:
                        cur = conn.cursor()
                        cur.execute(
                            "UPDATE documents SET chunk_strategy = ?, chunk_version = 1 WHERE id = ?",
                            (expected_strategy, doc_id)
                        )
                    if verbose:
                        print(f"  Fixed strategy from '{original_chunk_strategy}' to '{expected_strategy}'")
            
            stats['success'] = True
            stats['skipped'] = True
            stats['error'] = 'No content found - skipped (strategy corrected if needed)'
            if verbose:
                print("  [SKIP] No chunks or raw content found; skipping")
            return stats
        
        # Get appropriate chunker
        chunker = get_chunker_for_doctype(doc_type)
        
        # For chat documents, content is already a list; for others it's a string
        if doc_type == 'chat':
            if not isinstance(content, list):
                stats['error'] = f'Chat document content should be a list, got {type(content).__name__}'
                return stats
            # Empty chat documents should be skipped
            if len(content) == 0 and not old_chunks:
                stats['success'] = True
                stats['skipped'] = True
                stats['error'] = 'Empty chat document - skipped'
                if verbose:
                    print("  [SKIP] Empty chat document; skipping")
                return stats
            base_chunks = chunker.chunk(content)
        else:
            if isinstance(content, list):
                content = '\n'.join(content)
            base_chunks = chunker.chunk(content)
        
        if not base_chunks:
            stats['error'] = 'Chunker returned no chunks'
            return stats
        
        # Ensure chunks respect embedder max token length
        embed_max_tokens = get_embedding_max_tokens(store.embedder)
        chunks_with_parent = enforce_embedding_token_limit(base_chunks, embed_max_tokens)
        if not chunks_with_parent:
            stats['error'] = 'Chunker returned no chunks after enforcing token limit'
            return stats
        
        stats['new_chunk_count'] = len(chunks_with_parent)
        
        if dry_run:
            if verbose:
                print(f"  [DRY RUN] Would create {len(chunks_with_parent)} new chunks")
            stats['success'] = True
            return stats
        
        # Embed all chunks
        chunk_texts = [c.text for c, _ in chunks_with_parent]
        embeddings = None
        if store.embedder:
            try:
                embeddings = store.embedder.embed(chunk_texts)
                if verbose:
                    print(f"  Generated embeddings: {embeddings.shape}")
            except Exception as e:
                if verbose:
                    print(f"  [ERROR] Embedding failed: {e}")
                stats['error'] = f'Embedding failed: {e}'
                return stats
        
        # Always abort migration if embeddings are not available
        # This prevents migrating documents without dense search capability
        if embeddings is None:
            stats['error'] = 'Embeddings required but not available - configure embedder to enable migration'
            if verbose:
                print(f"  [ERROR] Cannot migrate without embeddings")
                print(f"  [ERROR] Please ensure embedder is properly configured")
            return stats
        
        now_iso = datetime.now(UTC).isoformat()
        
        # Normalize chunk strategy
        chunk_strategy = normalize_chunk_strategy(type(chunker).__name__)
        raw_to_store = raw_source
        if raw_to_store is None:
            if doc_type == 'chat' and isinstance(content, list):
                raw_to_store = json.dumps([{"content": c} for c in content])
            elif isinstance(content, str):
                raw_to_store = content
        
        # Get old embedding IDs that need to be removed from FAISS
        # Also store old chunk IDs for selective soft-delete
        old_chunk_ids = [row['id'] for row in old_chunks]
        old_embedding_ids = [row['embedding_id'] for row in old_chunks if row['embedding_id'] is not None]
        index_requires_update = store._faiss_index is not None
        
        with store._connect() as conn:
            cur = conn.cursor()

            # Insert new chunks first (before deleting old ones)
            # This ensures we can roll back if anything fails
            chunk_ids = []
            for i, (chunk, parent_seq) in enumerate(chunks_with_parent):
                # Ensure sequence numbers are strictly increasing for storage
                chunk.seq = i
                metadata_source: dict = {}
                if doc_type == 'chat':
                    if parent_seq < len(message_meta):
                        metadata_source = message_meta[parent_seq] or {}
                    elif message_meta:
                        metadata_source = message_meta[-1] or {}
                if not metadata_source and old_chunks:
                    source_row = old_chunks[parent_seq] if parent_seq < len(old_chunks) else old_chunks[0]
                    metadata_source = _chunk_row_to_meta(source_row, doc_tags)
                
                tags = metadata_source.get('tags') if metadata_source else doc_tags
                focus_hint = metadata_source.get('focus_hint') if metadata_source else None
                # Preserve role from original chunk
                role = metadata_source.get('role') if metadata_source else None
                if not role:
                    role = Role.USER.value if doc_type != 'chat' else Role.SYSTEM.value
                
                created_at = metadata_source.get('created_at') if metadata_source else None
                if not created_at:
                    created_at = doc_created_at or doc_ingested_at or now_iso
                
                # Preserve event_at, including None values
                event_at = metadata_source.get('event_at') if metadata_source else None
                if event_at is None:
                    event_at = doc_event_at
                
                cur.execute(
                    """
                    INSERT INTO chunks (document_id, seq, text, role, token_count, embedding_id,
                                       created_at, event_at, tags, focus_hint, char_start, char_end,
                                       embedding_model, heading_path, page_number, parent_doc_seq)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        doc_id, chunk.seq, chunk.text, role, chunk.token_count,
                        None,  # embedding_id filled after chunk ID is known
                        created_at, event_at, json.dumps(tags) if tags else None, focus_hint,
                        chunk.char_start, chunk.char_end, MODEL_NAME,
                        chunk.heading_path, chunk.page_number, parent_seq
                    ),
                )
                chunk_ids.append(cur.lastrowid)
            
            # Update embedding_id for all chunks (but don't add to FAISS yet)
            if embeddings is not None:
                for chunk_id in chunk_ids:
                    cur.execute(
                        "UPDATE chunks SET embedding_id = ? WHERE id = ?",
                        (chunk_id, chunk_id)
                    )
        
        faiss_add_succeeded = not index_requires_update
        faiss_error = None
        
        # Only after successful DB commit: add new embeddings to FAISS
        # This ensures if commit fails, FAISS won't have orphaned vectors
        if embeddings is not None and store._faiss_index:
            try:
                store._faiss_index.add(chunk_ids, embeddings)
                faiss_add_succeeded = True
                if verbose:
                    print(f"  Added {len(chunk_ids)} embeddings to FAISS")
            except Exception as e:
                faiss_error = str(e)
                if verbose:
                    print(f"  [WARN] Failed to add embeddings to FAISS after commit: {e}")
                    print(f"  [WARN] Document {doc_id}, chunk IDs {chunk_ids[:5]}{'...' if len(chunk_ids) > 5 else ''}")
                    print(f"  [WARN] Database updated but FAISS may be out of sync")
        
        # Only remove old embeddings if new ones were added successfully
        if faiss_add_succeeded and old_embedding_ids and store._faiss_index:
            try:
                ids_to_remove = np.array(old_embedding_ids, dtype='int64')
                store._faiss_index.index.remove_ids(ids_to_remove)
                store._faiss_index.persist()
                if verbose:
                    print(f"  Removed {len(old_embedding_ids)} old embeddings from FAISS")
            except Exception as e:
                if verbose:
                    print(f"  [WARN] Failed to remove old embeddings from FAISS: {e}")
        
        # Update index metadata only when FAISS is successfully updated
        if faiss_add_succeeded and store._faiss_index:
            with store._connect() as conn:
                cur = conn.cursor()
                store._write_index_meta(cur, now_iso)
        
        # If FAISS add failed, attempt to restore DB state so old chunks remain active
        if index_requires_update and not faiss_add_succeeded:
            with store._connect() as conn:
                cur = conn.cursor()
                if chunk_ids:
                    placeholders_new = ','.join(['?'] * len(chunk_ids))
                    cur.execute(f"UPDATE chunks SET deleted = 1 WHERE id IN ({placeholders_new})", chunk_ids)
                cur.execute(
                    "UPDATE documents SET chunk_strategy = ?, chunk_version = ? WHERE id = ?",
                    (original_chunk_strategy, original_chunk_version, doc_id)
                )
            stats['error'] = faiss_error or 'FAISS update failed'
            return stats
        
        # Only after index sync succeeds (or is not required) do we swap active chunks
        with store._connect() as conn:
            cur = conn.cursor()
            if old_chunk_ids:
                placeholders_old = ','.join(['?'] * len(old_chunk_ids))
                cur.execute(f"UPDATE chunks SET deleted = 1 WHERE id IN ({placeholders_old})", old_chunk_ids)
            cur.execute(
                """
                UPDATE documents 
                SET chunk_strategy = ?, chunk_version = 1, raw_content = COALESCE(raw_content, ?)
                WHERE id = ?
                """,
                (chunk_strategy, raw_to_store, doc_id)
            )
        
        stats['success'] = True
        if verbose:
            print(f"  ✓ Migrated successfully: {stats['old_chunk_count']} → {stats['new_chunk_count']} chunks")
        
    except Exception as e:
        stats['error'] = str(e)
        if verbose:
            print(f"  ✗ Migration failed: {e}")
            import traceback
            traceback.print_exc()
    
    return stats


def main():
    parser = argparse.ArgumentParser(description='Migrate documents to v0.8 chunking format')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    parser.add_argument('--dry-run', '-n', action='store_true', help='Dry run (no changes)')
    parser.add_argument('--doc-type', type=str, help='Only migrate documents of this type')
    parser.add_argument('--doc-id', type=int, help='Only migrate this specific document ID')
    parser.add_argument('--force', action='store_true', help='Force migration even if chunk_strategy/version look up-to-date')
    args = parser.parse_args()
    
    print("=" * 60)
    print("Document Migration: v0.7 → v0.8")
    print("=" * 60)
    
    if args.dry_run:
        print("[DRY RUN MODE - No changes will be made]")
    
    # Initialize store with embedder
    try:
        embedder = EmbeddingProvider()
        print(f"✓ Initialized embedder: {MODEL_NAME}")
    except Exception as e:
        print(f"✗ Failed to initialize embedder: {e}")
        embedder = None
    
    store = LocalStore(embedder=embedder)
    print(f"✓ Connected to database: {DB_PATH}")
    
    # Get documents to migrate
    with store._connect() as conn:
        cur = conn.cursor()
        base_query = "SELECT id, doc_type, title, chunk_strategy, chunk_version FROM documents"
        
        if args.doc_id:
            # Migrate specific document
            cur.execute(
                f"{base_query} WHERE id = ?",
                (args.doc_id,)
            )
            documents = cur.fetchall()
        elif args.doc_type:
            cur.execute(f"{base_query} WHERE doc_type = ? ORDER BY id", (args.doc_type,))
            docs = cur.fetchall()
            documents = docs if args.force else [row for row in docs if should_migrate_doc(row)]
        else:
            cur.execute(f"{base_query} ORDER BY id")
            docs = cur.fetchall()
            documents = docs if args.force else [row for row in docs if should_migrate_doc(row)]
    
    if not documents:
        print("\n✓ No documents need migration")
        return 0
    
    print(f"\nFound {len(documents)} document(s) to migrate")
    
    # Migrate each document
    results = []
    for doc in documents:
        doc_id = doc['id']
        doc_type = doc['doc_type'] or 'web_page'
        title = doc['title'] or f"Doc #{doc_id}"
        
        if args.verbose:
            print(f"\n[{doc_id}] {title[:50]} ({doc_type})")
        
        stats = migrate_document(store, doc_id, doc_type, args.verbose, args.dry_run)
        results.append(stats)
    
    # Print summary
    print("\n" + "=" * 60)
    print("Migration Summary")
    print("=" * 60)
    
    success_count = sum(1 for r in results if r['success'] and not r.get('skipped'))
    skipped_count = sum(1 for r in results if r.get('skipped'))
    failed_count = len(results) - success_count - skipped_count
    
    total_old_chunks = sum(r['old_chunk_count'] for r in results)
    total_new_chunks = sum(r['new_chunk_count'] for r in results if r['success'])
    
    print(f"Documents processed: {len(results)}")
    print(f"  ✓ Successful: {success_count}")
    print(f"  ◦ Skipped (no content): {skipped_count}")
    print(f"  ✗ Failed: {failed_count}")
    print(f"\nChunks:")
    print(f"  Old chunks (soft-deleted): {total_old_chunks}")
    print(f"  New chunks created: {total_new_chunks}")
    
    if failed_count > 0:
        print("\nFailed documents:")
        for r in results:
            if not r['success']:
                print(f"  Doc {r['doc_id']}: {r['error']}")
    
    if skipped_count > 0 and failed_count == 0:
        print("\nSkipped documents (no chunks/raw_content):")
        for r in results:
            if r.get('skipped'):
                print(f"  Doc {r['doc_id']}")
    
    if args.dry_run:
        print("\n[DRY RUN - No changes were made]")
    else:
        print("\n✓ Migration complete!")
    
    return 1 if failed_count > 0 else 0


if __name__ == '__main__':
    sys.exit(main())
