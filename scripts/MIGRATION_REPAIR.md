# Migration Repair Guide

Use these steps if searches miss obvious matches (e.g., CJK exact matches) or if migration needs to be rerun end-to-end.

## 1) Rebuild FTS without Porter Stemming (better CJK)

```bash
python - <<'PY'
import sqlite3, os
db = os.path.expanduser("~/.shiye/shiye.db")
conn = sqlite3.connect(db)
cur = conn.cursor()
cur.executescript("""
DROP TRIGGER IF EXISTS chunks_fts_insert;
DROP TRIGGER IF EXISTS chunks_fts_delete;
DROP TRIGGER IF EXISTS chunks_fts_update;
DROP TABLE IF EXISTS chunks_fts;
CREATE VIRTUAL TABLE chunks_fts USING fts5(
    chunk_id UNINDEXED,
    text,
    doc_type UNINDEXED,
    tokenize='unicode61'
);
INSERT INTO chunks_fts(chunk_id, text, doc_type)
SELECT c.id, c.text, d.doc_type
FROM chunks c JOIN documents d ON c.document_id = d.id
WHERE c.deleted = 0;
CREATE TRIGGER chunks_fts_insert AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(chunk_id, text, doc_type)
    SELECT NEW.id, NEW.text, d.doc_type FROM documents d WHERE d.id = NEW.document_id;
END;
CREATE TRIGGER chunks_fts_delete AFTER UPDATE OF deleted ON chunks
WHEN NEW.deleted = 1 BEGIN
    DELETE FROM chunks_fts WHERE chunk_id = NEW.id;
END;
CREATE TRIGGER chunks_fts_update AFTER UPDATE OF text ON chunks
WHEN NEW.deleted = 0 BEGIN
    DELETE FROM chunks_fts WHERE chunk_id = NEW.id;
    INSERT INTO chunks_fts(chunk_id, text, doc_type)
    SELECT NEW.id, NEW.text, d.doc_type FROM documents d WHERE d.id = NEW.document_id;
END;
""")
conn.commit()
print("FTS rebuilt with unicode tokenizer")
PY
```

## 2) Re-run Migration (optionally forced)

- Preview all docs (forced):  
  `python scripts/migrate_v08.py --force --dry-run --verbose`

- Re-migrate specific doc or type:  
  `python scripts/migrate_v08.py --doc-id 32 --verbose`  
  `python scripts/migrate_v08.py --doc-type note --force --verbose`

`--force` overrides version/strategy checks so every selected doc is re-chunked and re-embedded.

## 3) Verify

- Exact match queries should now find CJK terms (e.g., `十年`).
- Check `chunks` vs `chunks_fts` counts for a known doc to confirm FTS sync.
