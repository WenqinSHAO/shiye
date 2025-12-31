from datetime import datetime
from typing import List

from fastapi import Body, FastAPI
from fastapi.responses import HTMLResponse
from datetime import UTC, datetime

from datatypes import Message
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
        "created_at": m.created_at.isoformat(),
        "reference_time": m.reference_time.isoformat() if m.reference_time else None,
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
            body { font-family: system-ui, sans-serif; margin: 0; background: #0f172a; color: #e2e8f0; }
            header { padding: 12px 16px; background: #1e293b; display: flex; justify-content: space-between; align-items: center; }
            #log { padding: 16px; height: 70vh; overflow-y: auto; background: #0b1220; }
            .msg { margin-bottom: 12px; position: relative; }
            .role { font-size: 12px; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.04em; }
            .bubble { padding: 10px 12px; border-radius: 8px; background: #111827; border: 1px solid #1f2937; }
            .me .bubble { background: #1d4ed8; border-color: #1e40af; color: #e2e8f0; }
            .msg .actions { position: absolute; right: 4px; top: 4px; display: none; gap: 4px; }
            .msg:hover .actions { display: inline-flex; }
            .actions button { font-size: 11px; padding: 4px 6px; border-radius: 6px; border: 1px solid #334155; background: #1f2937; color: #e2e8f0; cursor: pointer; }
            form { padding: 12px 16px; background: #111827; display: grid; gap: 8px; }
            textarea { width: 100%; min-height: 80px; resize: vertical; border-radius: 8px; border: 1px solid #1f2937; background: #0f172a; color: #e2e8f0; padding: 8px; }
            button { border: none; border-radius: 8px; padding: 10px 14px; cursor: pointer; font-weight: 600; color: #0b1220; background: #38bdf8; }
            button.secondary { background: #22c55e; }
            button.ghost { background: #1f2937; color: #e2e8f0; border: 1px solid #334155; }
            .row { display: flex; gap: 8px; align-items: center; }
        </style>
    </head>
    <body>
        <header>
            <div><strong>Shiye</strong> — Web UI</div>
            <div class="row">
                <label style="display:flex;align-items:center;gap:6px;color:#cbd5e1;font-size:12px;">
                    <input type="checkbox" id="debugToggle" onclick="toggleDebug()" />
                    debug
                </label>
                <button class="ghost" onclick="runRss()">Run RSS</button>
                <button class="ghost" onclick="loadTrace()">LLM Trace</button>
            </div>
        </header>
        <div id="log"></div>
        <div id="trace" style="padding:8px 16px; font-size:12px; color:#94a3b8; display:none;"></div>
        <form onsubmit="event.preventDefault(); sendChat();">
            <textarea id="input" placeholder="Type a message... (/add to archive, /rss to summarize feeds)"></textarea>
            <div class="row">
                <button type="submit">Send</button>
                <button type="button" class="secondary" onclick="sendAdd()">Add (/add)</button>
                <button type="button" class="ghost" onclick="loadHistory()">List history</button>
            </div>
        </form>
        <script>
            const logEl = document.getElementById('log');
            const inputEl = document.getElementById('input');
            const debugToggle = document.getElementById('debugToggle');
            const traceEl = document.getElementById('trace');

            function renderMessage(role, content, chunkId, createdAt, debug) {
                const wrap = document.createElement('div');
                wrap.className = 'msg ' + (role === 'user' ? 'me' : '');
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
                if (chunkId) {
                    const del = document.createElement('button');
                    del.textContent = '✕';
                    del.title = 'Delete';
                    del.onclick = () => deleteMessage(chunkId, wrap);
                    actions.appendChild(del);
                }
                const copyBtn = document.createElement('button');
                copyBtn.textContent = 'Copy';
                copyBtn.onclick = () => copyMessage(content);
                actions.appendChild(copyBtn);
                if (debug) {
                    const dbg = document.createElement('pre');
                    dbg.style.fontSize = '11px';
                    dbg.style.background = '#0f172a';
                    dbg.style.border = '1px solid #1f2937';
                    dbg.style.padding = '6px';
                    dbg.style.marginTop = '6px';
                    dbg.textContent = JSON.stringify(debug, null, 2);
                    bubble.appendChild(dbg);
                }
                wrap.appendChild(actions);
                wrap.appendChild(roleEl);
                wrap.appendChild(bubble);
                logEl.appendChild(wrap);
                logEl.scrollTop = logEl.scrollHeight;
            }

            async function loadTrace() {
                const res = await fetch('/api/llm_trace');
                const data = await res.json();
                document.getElementById('trace').textContent = JSON.stringify(data.trace || {}, null, 2);
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
                renderMessage('you', text, null, new Date().toISOString());
                inputEl.value = '';
                const res = await fetch('/api/chat', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ text })
                });
                const data = await res.json();
                (data.messages || []).forEach(m => renderMessage(m.role, m.content, m.chunk_id, m.created_at));
            }

            async function sendAdd() {
                const text = inputEl.value.trim();
                if (!text) return;
                inputEl.value = '';
                const res = await fetch('/api/add', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ text })
                });
                const data = await res.json();
                (data.logs || []).forEach(line => renderMessage('system', line, null, new Date().toISOString()));
            }

            async function runRss() {
                const res = await fetch('/api/rss', { method: 'POST' });
                const data = await res.json();
                (data.messages || []).forEach(m => renderMessage(m.role, m.content, m.chunk_id, m.created_at));
            }

            async function loadHistory() {
                const res = await fetch('/api/messages?limit=50');
                const data = await res.json();
                logEl.innerHTML = '';
                (data.messages || []).forEach(m => renderMessage(m.role, m.content, m.chunk_id, m.created_at));
            }

            async function loadTrace() {
                const res = await fetch('/api/llm_trace');
                const data = await res.json();
                document.getElementById('trace').textContent = JSON.stringify(data.trace || {}, null, 2);
            }

            function toggleDebug() {
                traceEl.style.display = debugToggle.checked ? 'block' : 'none';
            }
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
