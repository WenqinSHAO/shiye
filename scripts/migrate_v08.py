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

from chunking import get_chunker_for_doctype, MessageChunker
from config import DB_PATH, DATA_DIR, MODEL_NAME
from datatypes import Role
from embeddings import EmbeddingProvider
from storage import LocalStore
from vector_store import FaissIndex

MIGRATABLE_WHERE_CLAUSE = """
chunk_version IS NULL
    OR chunk_version < 1
    OR chunk_strategy IS NULL
    OR chunk_strategy NOT IN ('header-aware', 'sentence-window', 'fixed-token', 'per-message', 'single-chunk')
"""


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
            # Build raw_content from existing chunks so we persist a source copy
            raw_content = json.dumps([
                {
                    "content": row['text'],
                    "role": row['role'],
                    "created_at": row['created_at'],
                    "event_at": row['event_at'],
                    "tags": json.loads(row['tags']) if row['tags'] else doc_tags,
                    "focus_hint": row['focus_hint'],
                }
                for row in chunks_meta
            ])
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
        'error': None
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
        
        if not old_chunks:
            stats['error'] = 'No content found'
            return stats
        
        if content is None:
            stats['error'] = 'No content found'
            return stats
        
        # Get appropriate chunker
        chunker = get_chunker_for_doctype(doc_type)
        
        # For chat documents, content is already a list; for others it's a string
        if doc_type == 'chat':
            if not isinstance(content, list):
                stats['error'] = 'Chat document content should be a list'
                return stats
            chunks = chunker.chunk(content)
        else:
            if isinstance(content, list):
                content = '\n'.join(content)
            chunks = chunker.chunk(content)
        
        if not chunks:
            stats['error'] = 'Chunker returned no chunks'
            return stats
        
        stats['new_chunk_count'] = len(chunks)
        
        if dry_run:
            if verbose:
                print(f"  [DRY RUN] Would create {len(chunks)} new chunks")
            stats['success'] = True
            return stats
        
        # Embed all chunks
        chunk_texts = [c.text for c in chunks]
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
            for i, chunk in enumerate(chunks):
                metadata_source: dict = {}
                if doc_type == 'chat':
                    if i < len(message_meta):
                        metadata_source = message_meta[i] or {}
                    elif message_meta:
                        metadata_source = message_meta[-1] or {}
                if not metadata_source and old_chunks:
                    source_row = old_chunks[i] if i < len(old_chunks) else old_chunks[0]
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
                        chunk.heading_path, chunk.page_number, chunk.seq
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
        
        if args.doc_id:
            # Migrate specific document
            cur.execute(
                "SELECT id, doc_type, title FROM documents WHERE id = ?",
                (args.doc_id,)
            )
        elif args.doc_type:
            # Migrate documents of specific type
            cur.execute(
                f"""
                SELECT id, doc_type, title FROM documents 
                WHERE doc_type = ? AND ({MIGRATABLE_WHERE_CLAUSE})
                ORDER BY id
                """,
                (args.doc_type,)
            )
        else:
            # Migrate all documents without v0.8 chunking
            cur.execute(
                f"""
                SELECT id, doc_type, title FROM documents 
                WHERE {MIGRATABLE_WHERE_CLAUSE}
                ORDER BY id
                """
            )
        
        documents = cur.fetchall()
    
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
    
    success_count = sum(1 for r in results if r['success'])
    failed_count = len(results) - success_count
    
    total_old_chunks = sum(r['old_chunk_count'] for r in results)
    total_new_chunks = sum(r['new_chunk_count'] for r in results if r['success'])
    
    print(f"Documents processed: {len(results)}")
    print(f"  ✓ Successful: {success_count}")
    print(f"  ✗ Failed: {failed_count}")
    print(f"\nChunks:")
    print(f"  Old chunks (soft-deleted): {total_old_chunks}")
    print(f"  New chunks created: {total_new_chunks}")
    
    if failed_count > 0:
        print("\nFailed documents:")
        for r in results:
            if not r['success']:
                print(f"  Doc {r['doc_id']}: {r['error']}")
    
    if args.dry_run:
        print("\n[DRY RUN - No changes were made]")
    else:
        print("\n✓ Migration complete!")
    
    return 1 if failed_count > 0 else 0


if __name__ == '__main__':
    sys.exit(main())
