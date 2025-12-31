from typing import List

from fastapi import Body, FastAPI
from fastapi.responses import HTMLResponse
from datetime import UTC, datetime

from datatypes import Message, ensure_utc
from handlers import handle_add
from orchestrator import Orchestrator
from workspace import MemoryWorkspace
import rss

app = FastAPI(title="Shiye Web")

workspace = MemoryWorkspace()
orchestrator = Orchestrator(workspace)


def msg_to_dict(m: Message) -> dict:
    return {
        "content": m.content,
        "role": m.role.value,
        "created_at": ensure_utc(m.created_at).isoformat(),
        "reference_time": ensure_utc(m.reference_time).isoformat() if m.reference_time else None,
        "metadata": m.metadata,
        "chunk_id": m.metadata.get("chunk_id"),
    }

def make_system_msg(content: str) -> dict:
    return {
        "content": content,
        "role": "system",
        "created_at": datetime.now(UTC).isoformat(),
        "reference_time": None,
        "metadata": {},
        "chunk_id": None,
    }


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    html = """
    <!doctype html>
    <html>
    <head>
        <meta charset="utf-8" />
        <title>Shiye</title>
        <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
        <style>
            :root {
                --bg: #f6f8fb;
                --panel: #ffffff;
                --ink: #1f2933;
                --subtle: #62738a;
                --border: #d7deea;
                --accent: #5b8def;
                --accent-soft: #e8efff;
                --assistant: #e8f5e9;
                --assistant-border: #cfead4;
                --user: #e7f1ff;
                --user-border: #c8dcff;
                --system: #fff7e6;
                --system-border: #f4e3b5;
                --action-bg: #edf1f7;
                --action-border: #cfd7e2;
            }
            body { font-family: "Inter", "Segoe UI", system-ui, -apple-system, sans-serif; margin: 0; background: var(--bg); color: var(--ink); min-height: 100vh; overflow: hidden; }
            header { padding: 12px 16px; background: var(--panel); border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; position: sticky; top: 0; z-index: 10; }
            main { display: grid; grid-template-columns: 1fr; gap: 12px; padding: 12px 16px; height: calc(100vh - 64px); box-sizing: border-box; overflow: hidden; }
            body.show-history main { grid-template-columns: 2fr auto; }
            main section.chat { display: grid; grid-template-rows: 1fr auto; min-height: 0; height: 100%; min-width: 0; overflow: hidden; }
            #log { padding: 16px; height: 100%; min-height: 0; overflow-y: auto; background: linear-gradient(180deg, #fafdff 0%, var(--bg) 100%); border-radius: 12px; border: 1px solid var(--border); box-sizing: border-box; }
            .msg { margin-bottom: 12px; position: relative; padding-right: 90px; }
            .role { font-size: 12px; color: var(--subtle); text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 4px; }
            .bubble { padding: 12px 14px; border-radius: 12px; background: var(--panel); border: 1px solid var(--border); box-shadow: 0 2px 8px rgba(15, 23, 42, 0.05); }
            .role-user .bubble { background: var(--user); border-color: var(--user-border); }
            .role-assistant .bubble { background: var(--assistant); border-color: var(--assistant-border); }
            .role-system .bubble { background: var(--system); border-color: var(--system-border); }
            .msg .actions { position: absolute; right: 8px; top: 12px; display: inline-flex; gap: 6px; opacity: 0; transition: opacity 0.15s ease; }
            .msg:hover .actions { opacity: 1; }
            .actions button { font-size: 11px; padding: 4px 6px; border-radius: 6px; border: 1px solid var(--action-border); background: var(--action-bg); color: var(--ink); cursor: pointer; }
            .actions button:disabled { opacity: 0.4; cursor: not-allowed; }
            form { padding: 12px 16px; background: var(--panel); border-top: 1px solid var(--border); display: grid; gap: 8px; position: static; }
            textarea { width: 100%; min-height: 90px; resize: vertical; border-radius: 10px; border: 1px solid var(--border); background: #fff; color: var(--ink); padding: 10px; }
            button { border: none; border-radius: 10px; padding: 10px 14px; cursor: pointer; font-weight: 600; color: #fff; background: var(--accent); box-shadow: 0 2px 6px rgba(91, 141, 239, 0.35); }
            button.secondary { background: #5fc49e; box-shadow: 0 2px 6px rgba(95, 196, 158, 0.35); }
            button.ghost { background: var(--panel); color: var(--ink); border: 1px solid var(--border); box-shadow: none; }
            .row { display: flex; gap: 8px; align-items: center; }
            .debug-only { display: none; }
            body.debug-mode .debug-only { display: block; }
            details.debug-block { margin-top: 8px; border: 1px dashed var(--border); border-radius: 8px; background: #f9fbff; }
            details.debug-block summary { padding: 8px 10px; cursor: pointer; color: var(--subtle); font-size: 12px; }
            details.debug-block pre { margin: 0; padding: 8px 10px 10px; font-size: 12px; background: transparent; color: #0f172a; overflow-x: auto; }
            #history-wrapper { display: none; height: 100%; min-width: 240px; max-width: 520px; position: relative; min-height: 0; }
            body.show-history #history-wrapper { display: flex; }
            #history-panel { width: 100%; height: 100%; background: var(--panel); border: 1px solid var(--border); border-radius: 12px; box-shadow: 0 2px 8px rgba(15, 23, 42, 0.05); display: flex; flex-direction: column; overflow: hidden; min-height: 0; }
            #history-resize { width: 6px; cursor: col-resize; position: absolute; left: -4px; top: 0; bottom: 0; }
            #history-header { padding: 12px; border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; }
            #history-list { flex: 1; min-height: 0; overflow-y: auto; padding: 12px; background: #f9fbff; }
            #history-list .item { position: relative; padding: 8px 10px; border-radius: 8px; border: 1px solid var(--border); background: #fff; margin-bottom: 8px; }
            #history-list .item.role-user { background: var(--user); border-color: var(--user-border); }
            #history-list .item.role-assistant { background: var(--assistant); border-color: var(--assistant-border); }
            #history-list .item.role-system { background: var(--system); border-color: var(--system-border); }
            #history-list .item .role { margin: 0 0 4px 0; }
            #history-list .item .actions { position: absolute; right: 6px; top: 6px; display: inline-flex; gap: 4px; opacity: 0; transition: opacity 0.15s ease; }
            #history-list .item:hover .actions { opacity: 1; }
        </style>
    </head>
    <body>
        <header>
            <div><strong>Shiye</strong> — Web UI</div>
            <div class="row">
                <label style="display:flex;align-items:center;gap:6px;color:#4b5563;font-size:12px;">
                    <input type="checkbox" id="debugToggle" onclick="toggleDebug()" />
                    debug
                </label>
                <button id="historyBtn" type="button" class="ghost" onclick="toggleHistory()">History</button>
            </div>
        </header>
        <main>
            <section class="chat">
                <div id="log"></div>
                <form onsubmit="event.preventDefault(); sendChat();">
                    <textarea id="input" placeholder="Type a message... (slash commands supported)"></textarea>
                    <div class="row" style="justify-content:flex-end;">
                        <span style="flex:1;color:var(--subtle);font-size:12px;">Ctrl+Enter to send</span>
                        <button type="submit">Send</button>
                    </div>
                </form>
            </section>
            <aside id="history-wrapper">
                <div id="history-resize"></div>
                <div id="history-panel">
                    <div id="history-header">
                        <div style="font-weight:600;">History</div>
                        <button class="ghost" type="button" onclick="loadHistory()">Refresh</button>
                    </div>
                    <div id="history-list"></div>
                </div>
            </aside>
        </main>
        <script>
            const logEl = document.getElementById('log');
            const inputEl = document.getElementById('input');
            const debugToggle = document.getElementById('debugToggle');
            const historyBtn = document.getElementById('historyBtn');
            const historyList = document.getElementById('history-list');
            const historyWrapper = document.getElementById('history-wrapper');
            const historyPanel = document.getElementById('history-panel');
            const historyResize = document.getElementById('history-resize');
            let historyOpen = false;
            let isResizing = false;

            function renderMessage(role, content, chunkId, createdAt, debug, metadata) {
                const wrap = document.createElement('div');
                wrap.className = 'msg role-' + (role || 'system');
                if (chunkId) wrap.dataset.chunkId = chunkId;
                const roleEl = document.createElement('div');
                roleEl.className = 'role';
                const ts = createdAt ? new Date(createdAt).toLocaleString() : new Date().toLocaleString();
                roleEl.textContent = role + " • " + ts;
                const bubble = document.createElement('div');
                bubble.className = 'bubble';
                bubble.innerHTML = marked.parse(content || '');
                const actions = document.createElement('div');
                actions.className = 'actions';
                const del = document.createElement('button');
                del.textContent = '✕';
                del.title = chunkId ? 'Delete' : 'Delete unavailable';
                del.disabled = !chunkId;
                del.onclick = () => chunkId && deleteMessage(chunkId, wrap);
                actions.appendChild(del);
                const copyBtn = document.createElement('button');
                copyBtn.textContent = 'Copy';
                copyBtn.onclick = () => copyMessage(content);
                actions.appendChild(copyBtn);
                if (role !== 'user') {
                    const sections = [];
                    if (metadata && Object.keys(metadata).length) {
                        sections.push({ label: metadata.source ? `Trace: ${metadata.source}` : 'Trace: metadata', payload: metadata });
                    }
                    if (debug) {
                        const label = debug.kind ? `Trace: ${debug.kind}` : 'Trace: debug';
                        sections.push({ label, payload: debug });
                    }
                    sections.forEach(sec => {
                        const details = document.createElement('details');
                        details.className = 'debug-block';
                        details.classList.add('debug-only');
                        const summary = document.createElement('summary');
                        summary.textContent = sec.label;
                        const pre = document.createElement('pre');
                        pre.textContent = JSON.stringify(sec.payload, null, 2);
                        details.appendChild(summary);
                        details.appendChild(pre);
                        bubble.appendChild(details);
                    });
                }
                wrap.appendChild(actions);
                wrap.appendChild(roleEl);
                wrap.appendChild(bubble);
                logEl.appendChild(wrap);
                logEl.scrollTop = logEl.scrollHeight;
            }

            async function copyMessage(text) {
                try {
                    await navigator.clipboard.writeText(text || '');
                } catch (e) {
                    console.warn('copy failed', e);
                }
            }

            async function deleteMessage(chunkId, el) {
                const res = await fetch(`/api/messages/${chunkId}`, { method: 'DELETE' });
                if (res.ok && el) {
                    el.remove();
                }
            }

            async function sendChat() {
                const text = inputEl.value.trim();
                if (!text) return;
                renderMessage('user', text, null, new Date().toISOString());
                inputEl.value = '';
                const res = await fetch('/api/chat', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ text, debug: debugToggle.checked })
                });
                const data = await res.json();
                (data.messages || []).forEach(m => renderMessage(m.role, m.content, m.chunk_id, m.created_at, m.debug, m.metadata));
            }

            async function loadHistory() {
                const res = await fetch('/api/messages?limit=50');
                const data = await res.json();
                if (historyList) {
                    historyList.innerHTML = '';
                    const messages = data.messages || [];
                    if (!messages.length) {
                        const empty = document.createElement('div');
                        empty.className = 'item';
                        empty.textContent = 'No history yet.';
                        historyList.appendChild(empty);
                        return;
                    }
                    messages.forEach(m => {
                        const item = document.createElement('div');
                        item.className = 'item role-' + (m.role || 'system');
                        const ts = m.created_at ? new Date(m.created_at).toLocaleString() : '';
                        const roleEl = document.createElement('div');
                        roleEl.className = 'role';
                        roleEl.textContent = `${m.role} • ${ts}`;
                        const body = document.createElement('div');
                        body.innerHTML = marked.parse(m.content || '');
                        const actions = document.createElement('div');
                        actions.className = 'actions';
                        const del = document.createElement('button');
                        del.textContent = '✕';
                        del.title = m.chunk_id ? 'Delete' : 'Delete unavailable';
                        del.disabled = !m.chunk_id;
                        del.onclick = () => m.chunk_id && deleteMessage(m.chunk_id, item);
                        const copyBtn = document.createElement('button');
                        copyBtn.textContent = 'Copy';
                        copyBtn.onclick = () => copyMessage(m.content);
                        actions.appendChild(del);
                        actions.appendChild(copyBtn);
                        item.appendChild(actions);
                        item.appendChild(roleEl);
                        item.appendChild(body);
                        historyList.appendChild(item);
                    });
                } else {
                    logEl.innerHTML = '';
                    (data.messages || []).forEach(m => renderMessage(m.role, m.content, m.chunk_id, m.created_at, m.debug, m.metadata));
                }
            }

            function toggleDebug() {
                const on = debugToggle.checked;
                document.body.classList.toggle('debug-mode', on);
            }

            function toggleHistory() {
                historyOpen = !historyOpen;
                document.body.classList.toggle('show-history', historyOpen);
                if (historyOpen) {
                    loadHistory();
                    if (historyBtn) historyBtn.textContent = 'Hide history';
                } else {
                    if (historyBtn) historyBtn.textContent = 'History';
                }
            }

            function startResize(e) {
                isResizing = true;
                document.body.classList.add('resizing');
                e.preventDefault();
            }

            function onResize(e) {
                if (!isResizing || !historyWrapper) return;
                const min = 240;
                const max = 520;
                const rect = historyWrapper.getBoundingClientRect();
                const newWidth = Math.min(Math.max(rect.right - e.clientX, min), max);
                historyWrapper.style.width = `${newWidth}px`;
            }

            function stopResize() {
                isResizing = false;
                document.body.classList.remove('resizing');
            }

            if (historyResize) {
                historyResize.addEventListener('mousedown', startResize);
                window.addEventListener('mousemove', onResize);
                window.addEventListener('mouseup', stopResize);
            }

            document.addEventListener('keydown', (e) => {
                if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                    e.preventDefault();
                    sendChat();
                }
            });
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


@app.get("/api/messages")
def get_messages(limit: int = 50) -> dict:
    msgs: List[Message] = workspace.list_recent(limit)
    def _ts(msg: Message) -> float:
        dt = msg.created_at
        if dt.tzinfo:
            return dt.timestamp()
        return dt.replace(tzinfo=UTC).timestamp()
    msgs = sorted(msgs, key=_ts)
    return {"messages": [msg_to_dict(m) for m in msgs]}


@app.delete("/api/messages/{chunk_id}")
def delete_message(chunk_id: int) -> dict:
    ok = workspace.delete_chunk(chunk_id)
    return {"deleted": ok}


@app.post("/api/chat")
def chat(payload=Body(...)) -> dict:
    text = (payload or {}).get("text", "")
    debug = bool((payload or {}).get("debug"))
    if not text:
        return {"messages": []}
    if text.strip().startswith("/add"):
        logs = handle_add(text.strip().removeprefix("/add").strip(), workspace, orchestrator, debug=debug)
        return {"messages": [make_system_msg(log["text"]) | {"debug": log.get("debug")} for log in logs]}
    reply = orchestrator.timelinereply(text)
    if isinstance(reply, list):
        messages = [msg_to_dict(m) for m in reply]
    else:
        messages = [{
            "content": str(reply),
            "role": "assistant",
            "created_at": datetime.now(UTC).isoformat(),
            "reference_time": None,
            "metadata": {},
            "chunk_id": None,
        }]
    if debug:
        for m in messages:
            m["debug"] = orchestrator.last_llm_trace
    return {"messages": messages}


@app.post("/api/add")
def add(payload=Body(...)) -> dict:
    text = (payload or {}).get("text", "")
    debug = bool((payload or {}).get("debug"))
    if not text:
        return {"logs": ["[add] missing text"]}
    logs = handle_add(text, workspace, orchestrator, debug=debug)
    return {"logs": [log["text"] for log in logs]}


@app.post("/api/rss")
def run_rss() -> dict:
    feeds = rss.load_feed_urls()
    if not feeds:
        return {"messages": [make_system_msg("[rss] no feeds configured (rss_feeds.txt)")] }
    try:
        items = rss.fetch_all(feeds, per_feed_limit=3, total_limit=20)
    except Exception as e:
        return {"messages": [make_system_msg(f"[rss] fetch failed: {e}")] }
    if not items:
        return {"messages": [make_system_msg("[rss] no items found.")]}
    keywords = ["AI infra", "LLM", "AI coding", "Agent", "Agentic AI", "machine learning", "attention", "memory"]
    summary = orchestrator.summarize_rss(items, keywords=keywords)
    messages = [msg_to_dict(m) for m in summary]
    return {"messages": messages}


@app.get("/api/llm_trace")
def llm_trace() -> dict:
    return {"trace": orchestrator.last_llm_trace}
