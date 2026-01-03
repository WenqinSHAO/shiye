# Scripts

Utilities to operate on Shiye data without digging into other docs.

## backup_restore.py

Back up and restore the primary storage files (`shiye.db`, `shiye.faiss`).

Defaults:
- Data dir: `SHIYE_DATA_DIR` or `~/.shiye`
- Backup dest: `<data_dir>/backups/backup-YYYYMMDD-HHMMSS/`

Usage:
```bash
# Backup to default location
python scripts/backup_restore.py backup

# Backup to a custom folder
python scripts/backup_restore.py --data-dir /path/to/data backup --dest /tmp/shiye-backup

# Restore from a backup folder
python scripts/backup_restore.py --data-dir /path/to/data restore /tmp/shiye-backup
```

## migrate_v08.py

Migration script for v0.7 to v0.8 chunking enhancements. This script re-chunks existing documents with the new v0.8 chunkers and properly updates:

- **FAISS index**: Uses correct parameter order (`add(ids, vectors)`) and calls `persist()` to save changes
- **Chat documents**: Passes message list to MessageChunker instead of concatenated string (avoids per-character chunking)
- **Chunk strategy**: Normalizes to `header-aware`, `sentence-window`, `fixed-token`, or `per-message` instead of raw class names
- **Embedding metadata**: Sets `embedding_id` for all chunks and updates vector index metadata
- **Chunk metadata**: Adds proper `heading_path`, `page_number`, and `parent_doc_seq` columns
- **Soft deletion**: Old chunks are marked as deleted (not removed) for rollback safety

### Key Fixes

1. **FAISS Update**: Corrected from `store._faiss_index.add(np.array(embeddings), np.array(chunk_ids))` to proper `store._faiss_index.add(chunk_ids, embeddings)` with persist
2. **Chat Chunking**: Fixed from passing string `chunker.chunk(content)` to list `chunker.chunk(messages)` for MessageChunker
3. **Strategy Names**: Changed from raw class names like `HeaderAwareChunker` to normalized names like `header-aware`
4. **Embedding IDs**: Now properly sets `embedding_id` column and writes index metadata after migration

### Usage

```bash
# Dry run to preview changes (recommended first)
python scripts/migrate_v08.py --dry-run --verbose

# Migrate all documents
python scripts/migrate_v08.py --verbose

# Migrate only a specific document type
python scripts/migrate_v08.py --doc-type chat --verbose

# Migrate a specific document by ID
python scripts/migrate_v08.py --doc-id 42 --verbose
```

### Options

- `--verbose, -v`: Show detailed progress for each document
- `--dry-run, -n`: Preview changes without modifying the database
- `--doc-type TYPE`: Only migrate documents of specific type (chat, note, web_page, paper)
- `--doc-id ID`: Only migrate a specific document by ID

### What Gets Migrated

Documents are migrated if they meet either condition:
- `chunk_version` is NULL
- `chunk_version < 1`

The script will:
1. Retrieve original content from existing chunks
2. Re-chunk using the appropriate v0.8 chunker for the document type
3. Soft-delete old chunks (set `deleted = 1`)
4. Insert new chunks with proper metadata
5. Generate and store embeddings in FAISS index (if embedder available)
6. Set `chunk_strategy` and `chunk_version = 1` on the document

### Safety

- Old chunks are soft-deleted (not removed) so you can rollback if needed
- Supports dry-run mode to preview changes
- Per-document error handling - failures don't stop the entire migration
- Detailed statistics and error reporting

### Example Output

```
============================================================
Document Migration: v0.7 → v0.8
============================================================
✓ Initialized embedder: sentence-transformers/all-MiniLM-L6-v2
✓ Connected to database: ~/.shiye/shiye.db

Found 5 document(s) to migrate

[1] Chat: Meeting Notes (chat)
  Migrating doc 1 (chat), 25 old chunks
  Generated embeddings: (25, 384)
  Added 25 embeddings to FAISS and persisted
  ✓ Migrated successfully: 25 → 25 chunks

============================================================
Migration Summary
============================================================
Documents processed: 5
  ✓ Successful: 5
  ✗ Failed: 0

Chunks:
  Old chunks (soft-deleted): 87
  New chunks created: 87

✓ Migration complete!
```
