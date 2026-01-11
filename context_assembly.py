"""Context assembly with neighbor expansion for enhanced retrieval.

This module provides utilities for expanding chunks with their neighbors
to maintain coherent context while keeping the index efficient.
"""

from typing import List, Optional, Dict, Any
from dataclasses import dataclass
import json


@dataclass
class ExpandedChunk:
    """A chunk expanded with neighbor context."""
    chunk_id: int
    core_text: str  # The original retrieved chunk
    expanded_text: str  # Full text with neighbors
    char_start: int
    char_end: int
    document_id: int
    seq: int
    neighbor_seq_before: List[int]  # Sequence numbers of chunks added before
    neighbor_seq_after: List[int]   # Sequence numbers of chunks added after
    heading_path: Optional[str] = None
    page_number: Optional[int] = None


def expand_chunks_with_neighbors(
    store,
    chunk_ids: List[int],
    neighbor_range: int = 1,
    max_expansion_chars: int = 2000
) -> List[ExpandedChunk]:
    """Expand retrieved chunks with their neighbors for better context.
    
    Args:
        store: LocalStore instance
        chunk_ids: List of chunk IDs to expand
        neighbor_range: Number of chunks to fetch before/after (default 1)
        max_expansion_chars: Maximum total characters after expansion
        
    Returns:
        List of ExpandedChunk objects with neighbor context
    """
    if not chunk_ids:
        return []
    
    expanded = []
    
    for chunk_id in chunk_ids:
        try:
            # Get the core chunk
            with store._connect() as conn:
                cur = conn.cursor()
                
                # Fetch core chunk
                cur.execute("""
                    SELECT c.id, c.document_id, c.text, c.seq, c.char_start, c.char_end,
                           c.heading_path, c.page_number
                    FROM chunks c
                    WHERE c.id = ? AND c.deleted = 0
                """, (chunk_id,))
                
                row = cur.fetchone()
                if not row:
                    continue
                
                doc_id = row['document_id']
                core_seq = row['seq']
                core_text = row['text']
                
                # Fetch neighbor chunks from same document
                cur.execute("""
                    SELECT c.id, c.text, c.seq, c.char_start, c.char_end
                    FROM chunks c
                    WHERE c.document_id = ?
                      AND c.deleted = 0
                      AND c.seq >= ?
                      AND c.seq <= ?
                    ORDER BY c.seq
                """, (doc_id, core_seq - neighbor_range, core_seq + neighbor_range))
                
                neighbors = cur.fetchall()
                
                # Build expanded text, always anchoring on the core chunk
                texts_before = []
                texts_after = []
                seq_before = []
                seq_after = []
                core_text = core_text or ""
                total_chars = len(core_text)
                
                for neighbor in neighbors:
                    neighbor_text = neighbor['text'] or ""
                    neighbor_seq = neighbor['seq']
                    
                    if neighbor_seq < core_seq:
                        if total_chars + len(neighbor_text) > max_expansion_chars:
                            continue
                        texts_before.append(neighbor_text)
                        seq_before.append(neighbor_seq)
                        total_chars += len(neighbor_text)
                    elif neighbor_seq > core_seq:
                        if total_chars + len(neighbor_text) > max_expansion_chars:
                            continue
                        texts_after.append(neighbor_text)
                        seq_after.append(neighbor_seq)
                        total_chars += len(neighbor_text)
                    else:
                        # Core chunk: already accounted for in total_chars
                        continue
                
                expanded_text = ' '.join(texts_before + [core_text] + texts_after)
                
                expanded.append(ExpandedChunk(
                    chunk_id=chunk_id,
                    core_text=core_text,
                    expanded_text=expanded_text,
                    char_start=row['char_start'],
                    char_end=row['char_end'],
                    document_id=doc_id,
                    seq=core_seq,
                    neighbor_seq_before=seq_before,
                    neighbor_seq_after=seq_after,
                    heading_path=row['heading_path'],
                    page_number=row['page_number']
                ))
                
        except Exception as e:
            print(f"[warn] Failed to expand chunk {chunk_id}: {e}")
            continue
    
    return expanded


def build_chunk_window(
    store,
    chunk_id: int,
    window_size: int = 1
) -> Optional[str]:
    """Build a chunk_window string for display/navigation.
    
    This creates a compact representation of the chunk's context
    for UI display, showing a snippet of surrounding text.
    
    Args:
        store: LocalStore instance
        chunk_id: Chunk ID to build window for
        window_size: Number of chunks before/after to include (default 1)
        
    Returns:
        Chunk window string or None if not available
    """
    try:
        with store._connect() as conn:
            cur = conn.cursor()
            
            # Get chunk info
            cur.execute("""
                SELECT c.document_id, c.seq
                FROM chunks c
                WHERE c.id = ? AND c.deleted = 0
            """, (chunk_id,))
            
            row = cur.fetchone()
            if not row:
                return None
            
            doc_id = row['document_id']
            seq = row['seq']
            
            # Get surrounding chunks
            cur.execute("""
                SELECT c.text, c.seq
                FROM chunks c
                WHERE c.document_id = ?
                  AND c.deleted = 0
                  AND c.seq >= ?
                  AND c.seq <= ?
                ORDER BY c.seq
            """, (doc_id, max(0, seq - window_size), seq + window_size))
            
            chunks = cur.fetchall()
            
            # Build window with ellipsis for truncated parts
            parts = []
            for chunk in chunks:
                text = chunk['text']
                # Truncate long chunks for window display
                if len(text) > 200:
                    if chunk['seq'] < seq:
                        # Before: show end
                        text = "..." + text[-150:]
                    elif chunk['seq'] > seq:
                        # After: show start
                        text = text[:150] + "..."
                    else:
                        # Current: show both ends
                        text = text[:100] + " ... " + text[-100:]
                
                parts.append(text)
            
            return ' '.join(parts)
            
    except Exception as e:
        print(f"[warn] Failed to build chunk window for {chunk_id}: {e}")
        return None


def get_chunk_provenance(store, chunk_id: int) -> Optional[Dict[str, Any]]:
    """Get full provenance chain for a chunk: chunk -> context -> document.
    
    Returns metadata useful for UI navigation from chunk back to source.
    
    Args:
        store: LocalStore instance
        chunk_id: Chunk ID
        
    Returns:
        Dict with provenance info or None
    """
    try:
        with store._connect() as conn:
            cur = conn.cursor()
            
            cur.execute("""
                SELECT 
                    c.id as chunk_id,
                    c.text as chunk_text,
                    c.seq,
                    c.char_start,
                    c.char_end,
                    c.heading_path,
                    c.page_number,
                    d.id as doc_id,
                    d.doc_type,
                    d.title,
                    d.source,
                    d.uri,
                    d.created_at as doc_created_at
                FROM chunks c
                JOIN documents d ON c.document_id = d.id
                WHERE c.id = ? AND c.deleted = 0
            """, (chunk_id,))
            
            row = cur.fetchone()
            if not row:
                return None
            
            return {
                'chunk_id': row['chunk_id'],
                'chunk_text': row['chunk_text'][:200] + '...' if len(row['chunk_text']) > 200 else row['chunk_text'],
                'seq': row['seq'],
                'char_start': row['char_start'],
                'char_end': row['char_end'],
                'heading_path': row['heading_path'],
                'page_number': row['page_number'],
                'document': {
                    'id': row['doc_id'],
                    'type': row['doc_type'],
                    'title': row['title'],
                    'source': row['source'],
                    'uri': row['uri'],
                    'created_at': row['doc_created_at']
                }
            }
            
    except Exception as e:
        print(f"[warn] Failed to get provenance for chunk {chunk_id}: {e}")
        return None


def format_chunk_location(provenance: Dict[str, Any]) -> str:
    """Format chunk location info for display.
    
    Args:
        provenance: Provenance dict from get_chunk_provenance
        
    Returns:
        Human-readable location string
    """
    parts = []
    
    if provenance.get('heading_path'):
        parts.append(provenance['heading_path'])
    
    if provenance.get('page_number'):
        parts.append(f"Page {provenance['page_number']}")
    
    if provenance.get('seq') is not None:
        parts.append(f"Chunk {provenance['seq']}")
    
    doc = provenance.get('document', {})
    if doc.get('title'):
        parts.append(f"in {doc['title']}")
    
    return ' • '.join(parts) if parts else 'Unknown location'
