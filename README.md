# Minimum personal assistant: Shiye (师爷)

## Current status

### Dev run

Start the web app:

```bash
python main.py  # or: uvicorn web:app --reload --port 8000
```

What it does:

- multi-round chat with DS backend
- browser UI with history + note mode (terminal UI deprecated)
- add stuff to the context, sorta in context learning
- summarize the context
- enhanced timeline processing
- paste-friendly inputs with Markdown rendering (math + images)

Storage/embeddings (v0):

- Local SQLite + FAISS live under `~/.shiye` by default (override with `SHIYE_DATA_DIR`).
- Default embedding model: `sentence-transformers/all-MiniLM-L6-v2` (override with `SHIYE_EMBED_MODEL`); download once while online.
- Requires `faiss-cpu` and `sentence-transformers` from `requirements.txt`.

Testing:

- Activate your env (e.g., `source ~/.virtualenvs/dspytest/bin/activate`) and run `python -m pytest -q`.
- Tests use temp data dirs; the real files appear after you run the app or write to the store (default `~/.shiye/shiye.db` and `~/.shiye/shiye.faiss`).

Web UI:

- Visit `http://localhost:8000` after starting the server.
- Commands: `/note` (3-panel note mode), `/add` (URLs/notes), `/rss`, `/summarize`, `/clear` (clears on-screen log only).
- Chat, run `/rss`, toggle `/note` for the markdown note taker, `/add` URLs/notes, `/clear` to clear the on-screen log (does not wipe storage).

## Data model (working)

### Timestamps
- `created_at` (chunks/documents/messages): when the chunk was stored; normalized to UTC ISO (`YYYY-MM-DDTHH:MM:SS.ssssss+00:00`). If the source lacks tzinfo, it is forced to UTC.
- `event_at` (optional): when the content actually happened (user-provided or inferred). Stored as UTC ISO; absent when not provided.
- `ingested_at` (documents): when the document landed in the store (UTC ISO).
- UI note: the web log shows `created_at`; note editor shows `updated_at` derived from `event_at` or `created_at`.

Extraction/assignment:
- User input: timestamped at receipt (`created_at=now`).
- DSPy replies or fallbacks: timestamped at generation (`created_at=now`).
- `/add` fetches: fetched content gets `created_at/ingested_at=now`; `event_at` unset unless provided.
- Notes: `created_at` on first save; `event_at` reused as “last changed” and mirrored into tags as `last_changed`.

### Document types in use
- `chat` (default chat log document).
- `note` (Markdown notes created via `/note`; images referenced in tags).
- `web_page` (fetched URL content via `/add fetch ...`).
- `paper` (arXiv metadata extraction via `/add fetch`).
- `rss_daily_summary` (daily RSS brief).
- Future/other: generic `system`/`user_note` chunks live under `chat` unless explicitly stored as a document.

RSS brief (micro-app):

- Configure feeds in `rss_feeds.txt` (one URL per line). Defaults include Google Research, OpenAI, DeepMind, Microsoft Research.
- Run `/rss` in the app to fetch latest items, cap per feed, and generate a concise digest with references. The daily digest is stored as `doc_type=rss_daily_summary` in local storage.

Planning/roadmap: see `TODO.md` for the working plan and open questions.

## End Goal

Functions-wise, what's most important to me, for my personal uses:

- a personal off-brain data bank storing raw original date, and strucutrized ones easier for LLM investigation
  - web, wechat readings
  - acamdeic papers, pdfs
  - books, epub, mobi
  - mails
  - etc.
- a mostly chat based assistant inferace that
  - makes add-hoc tools/mini app from test instructions, tool execution via LLM API or scripts
  - maintains the personal off-brain data banks, knows what are my current focus topics, what out stall archives
  - updates the off-brain data banks with meaningful exchanges with the assistant
  - is sessionless facing the user, yet user may still explicit ask to start afresh or telling the focus topic as of now
  - make good use of the off-brain data banks in interactions, yet may also makes searches or call other tools to help along with the quest
  - may use different LLM backend eventual
  - proactively suggestion actions at fitting moment

## achitectural considerations

TBD, main components, what to build first
