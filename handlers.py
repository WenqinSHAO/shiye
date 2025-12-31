from typing import List, Optional

from datatypes import Message, Role
from fetcher import extract_urls, fetch_url_content


def _store_note_and_refs(workspace, note_text: str, urls: List[str], refs_only: bool = False) -> None:
    metadata = {"urls": urls, "note_type": "user_note"}
    if refs_only:
        metadata["archive_mode"] = "refs_only"
    msg = Message(content=note_text, role=Role.USER, metadata=metadata)
    workspace.add(msg)


def handle_add(arg: str, workspace, orchestrator, debug: bool = False) -> List[dict]:
    """Handle /add logic for both CLI and web."""
    tokens = arg.split()
    mode: Optional[str] = None
    if tokens and tokens[0].lower() in ("fetch", "refs"):
        mode = tokens[0].lower()
        arg = " ".join(tokens[1:])
    urls = extract_urls(arg)
    note_text = arg.strip()
    logs: List[dict] = []

    if urls:
        if len(urls) > 1 and mode != "fetch":
            _store_note_and_refs(workspace, note_text, urls, refs_only=True)
            logs.append({"text": "[add] multiple URLs detected; saved note + references. Re-run with '/add fetch ...' to fetch contents.", "debug": None})
            return logs

        if mode == "refs":
            _store_note_and_refs(workspace, note_text, urls, refs_only=True)
            logs.append({"text": "[add] saved note + references (no fetch).", "debug": None})
            return logs

        # fetch content for URLs
        fetched = 0
        note_msg = Message(content=note_text, role=Role.USER, metadata={"urls": urls, "note_type": "user_note"})
        workspace.add(note_msg)
        for url in urls:
            title, content, method = fetch_url_content(url)
            if not content:
                logs.append({"text": f"[add] fetch failed for {url}; saved note only.", "debug": None})
                continue
            msg = Message(
                content=content,
                role=Role.SYSTEM,
                metadata={"url": url, "title": title, "source": "url_fetch", "extraction": method},
            )
            workspace.add_with_document(
                [msg],
                document_meta={
                    "doc_type": "web_page" if method != "arxiv_meta" else "paper",
                    "title": title,
                    "source": "url",
                    "uri": url,
                    "tags": {"url": url, "note_present": bool(note_text), "extraction": method},
                },
            )
            fetched += 1
            if debug:
                logs.append({"text": f"[add] fetched {url} [{method}] ({title})", "debug": {"url": url, "title": title, "method": method}})
        logs.append({"text": f"[ok] saved note and fetched {fetched}/{len(urls)} URL(s).", "debug": None})
        return logs

    # no URLs: regular add via timechunker
    orchestrator.timechunker(text=arg, role_hint="user")
    logs.append({"text": "[ok] added to memory", "debug": None})
    return logs
