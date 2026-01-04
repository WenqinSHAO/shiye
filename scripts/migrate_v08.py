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
            SELECT id, text, role, seq, created_at, event_at, embedding_id
            FROM chunks
            WHERE document_id = ? AND deleted = 0
            ORDER BY seq ASC
            """,
            (doc_id,)
        )
        return cur.fetchall()


def get_document_content(store: LocalStore, doc_id: int, doc_type: str) -> Optional[Union[str, List[str]]]:
    """Retrieve document content for re-chunking.
    
    For chat documents, returns a list of message texts.
    For other documents, returns the concatenated text.
    
    Args:
        store: LocalStore instance
        doc_id: Document ID to retrieve
        doc_type: Document type ('chat', 'note', 'web_page', etc.)
        
    Returns:
        Document content as string or list of strings for chat
    """
    chunks_meta = get_document_chunks_metadata(store, doc_id)
    
    if not chunks_meta:
        return None
    
    # For chat documents, return list of message texts
    if doc_type == 'chat':
        return [row['text'] for row in chunks_meta]
    
    # For other documents, concatenate with newlines
    return '\n'.join(row['text'] for row in chunks_meta)


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
        # Get old chunks with metadata
        old_chunks = get_document_chunks_metadata(store, doc_id)
        stats['old_chunk_count'] = len(old_chunks)
        
        if verbose:
            print(f"  Migrating doc {doc_id} ({doc_type}), {stats['old_chunk_count']} old chunks")
        
        if not old_chunks:
            stats['error'] = 'No content found'
            return stats
        
        # Get document content
        content = get_document_content(store, doc_id, doc_type)
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
        if store.embedder and store._faiss_index:
            try:
                embeddings = store.embedder.embed(chunk_texts)
                if verbose:
                    print(f"  Generated embeddings: {embeddings.shape}")
            except Exception as e:
                print(f"  [WARN] Embedding failed: {e}")
        
        now_iso = datetime.now(UTC).isoformat()
        
        # Normalize chunk strategy
        chunk_strategy = normalize_chunk_strategy(type(chunker).__name__)
        
        with store._connect() as conn:
            cur = conn.cursor()
            
            # Remove old chunk embeddings from FAISS index before soft-deleting
            old_embedding_ids = [row['embedding_id'] for row in old_chunks if row['embedding_id'] is not None]
            if old_embedding_ids and store._faiss_index:
                try:
                    import numpy as np
                    ids_to_remove = np.array(old_embedding_ids, dtype='int64')
                    store._faiss_index.index.remove_ids(ids_to_remove)
                    store._faiss_index.persist()
                    if verbose:
                        print(f"  Removed {len(old_embedding_ids)} old embeddings from FAISS")
                except Exception as e:
                    if verbose:
                        print(f"  [WARN] Failed to remove old embeddings from FAISS: {e}")
            
            # Mark old chunks as deleted (soft delete)
            cur.execute(
                "UPDATE chunks SET deleted = 1 WHERE document_id = ? AND deleted = 0",
                (doc_id,)
            )
            
            # Update document with chunk strategy
            cur.execute(
                """
                UPDATE documents 
                SET chunk_strategy = ?, chunk_version = 1
                WHERE id = ?
                """,
                (chunk_strategy, doc_id)
            )
            
            # Get document metadata for tags
            cur.execute("SELECT tags FROM documents WHERE id = ?", (doc_id,))
            doc_row = cur.fetchone()
            tags = {}
            if doc_row and doc_row['tags']:
                try:
                    tags = json.loads(doc_row['tags'])
                except:
                    pass
            
            # Map old chunks to new chunks to preserve metadata
            # For chat docs, order is preserved (1:1 mapping by seq)
            # For other docs, we use the first old chunk's metadata for all new chunks
            chunk_ids = []
            for i, chunk in enumerate(chunks):
                # Determine which old chunk to use for metadata
                if doc_type == 'chat' and i < len(old_chunks):
                    # Chat: 1:1 mapping by sequence
                    old_chunk = old_chunks[i]
                elif len(old_chunks) > 0:
                    # Non-chat: use first chunk's metadata (or default to USER role)
                    old_chunk = old_chunks[0]
                else:
                    old_chunk = None
                
                # Preserve role from original chunk
                if old_chunk and old_chunk['role']:
                    role = old_chunk['role']
                else:
                    # Default to USER for non-chat, SYSTEM for chat if no role found
                    role = Role.USER.value if doc_type != 'chat' else Role.SYSTEM.value
                
                # Preserve timestamps from original chunk
                if old_chunk and old_chunk['created_at']:
                    created_at = old_chunk['created_at']
                else:
                    created_at = now_iso
                
                # Preserve event_at, including None values
                if old_chunk:
                    event_at = old_chunk['event_at']  # May be None, and that's OK
                else:
                    event_at = None
                
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
                        created_at, event_at, json.dumps(tags), None,
                        chunk.char_start, chunk.char_end, MODEL_NAME,
                        chunk.heading_path, chunk.page_number, chunk.seq
                    ),
                )
                chunk_ids.append(cur.lastrowid)
            
            # Add embeddings to FAISS in batch with correct parameter order
            if embeddings is not None and store._faiss_index:
                # CORRECT: ids first, then vectors
                store._faiss_index.add(chunk_ids, embeddings)
                
                # Update embedding_id for all chunks
                for chunk_id in chunk_ids:
                    cur.execute(
                        "UPDATE chunks SET embedding_id = ? WHERE id = ?",
                        (chunk_id, chunk_id)
                    )
                
                # Write index metadata
                store._write_index_meta(cur, now_iso)
                
                if verbose:
                    print(f"  Added {len(chunk_ids)} embeddings to FAISS")
        
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
                """
                SELECT id, doc_type, title FROM documents 
                WHERE doc_type = ? AND (chunk_version IS NULL OR chunk_version < 1)
                ORDER BY id
                """,
                (args.doc_type,)
            )
        else:
            # Migrate all documents without v0.8 chunking
            cur.execute(
                """
                SELECT id, doc_type, title FROM documents 
                WHERE chunk_version IS NULL OR chunk_version < 1
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
