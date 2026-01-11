# Scripts

Utilities to operate on Shiye data without digging into other docs.

**Related docs**
- Quick start and migration overview: [README.md](../README.md)
- Chunking and metadata details: [CHUNKING_GUIDE.md](../CHUNKING_GUIDE.md)

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

- **FAISS index**: Uses correct parameter order (`add(ids, vectors)`) with automatic persistence
- **Chat documents**: Passes message list to MessageChunker instead of concatenated string (avoids per-character chunking)
- **Chunk strategy**: Normalizes to `header-aware`, `sentence-window`, `fixed-token`, or `per-message` instead of raw class names
- **Embedding metadata**: Sets `embedding_id` for all chunks and updates vector index metadata
- **Chunk metadata**: Adds proper `heading_path`, `page_number`, and `parent_doc_seq` columns
- **Soft deletion**: Old chunks are marked as deleted (not removed) for rollback safety

### Key Fixes

1. **FAISS Update**: Corrected from `add(embeddings, ids)` to `add(ids, embeddings)` (note: `add()` automatically persists to disk)
2. **FAISS Cleanup**: Old chunk embeddings are removed **after** new vectors are written successfully; FAISS failures leave old chunks active and roll back the document strategy/version.
3. **Chat Chunking**: Fixed from passing string `chunker.chunk(content)` to list `chunker.chunk(messages)` for MessageChunker
4. **Strategy Names**: Changed from raw class names like `HeaderAwareChunker` to normalized names like `header-aware`
5. **Embedding IDs**: Sets `embedding_id` to the new chunk IDs and writes vector index metadata when FAISS is available; migration aborts if embeddings cannot be generated.
6. **Role/Timestamp Preservation**: Chat message roles and timestamps are preserved when present; missing values fall back to document timestamps.
7. **Token Limits**: Enforces the embedder's max token length before embedding so oversized chunks are split safely.

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

# Force migration even if strategy/version look current
python scripts/migrate_v08.py --force --dry-run --verbose
```

### Options

- `--verbose, -v`: Show detailed progress for each document
- `--dry-run, -n`: Preview changes without modifying the database
- `--doc-type TYPE`: Only migrate documents of specific type (chat, note, web_page, paper)
- `--doc-id ID`: Only migrate a specific document by ID
- `--force`: Migrate matching documents even if they already have normalized strategy/version

### What Gets Migrated

Documents are migrated if they meet either condition:
- `chunk_version` is NULL
- `chunk_version < 1`
- `--force` overrides the above selection and migrates everything selected by `--doc-id`/`--doc-type` (or all docs).

The script will:
1. Retrieve original content from existing chunks
2. Re-chunk using the appropriate v0.8 chunker for the document type
3. Soft-delete old chunks (set `deleted = 1`)
4. Insert new chunks with proper metadata
5. Generate embeddings (required) and store them in FAISS when the index is available
6. Set `chunk_strategy` and `chunk_version = 1` on the document

### Safety

- Old chunks are soft-deleted (not removed) so you can rollback if needed
- Requires the embedding model to be available; run `scripts/backup_restore.py backup` before migrating
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
