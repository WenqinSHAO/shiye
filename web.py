from typing import List
from pathlib import Path
import secrets

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from datetime import UTC, datetime

from datatypes import Message, ensure_utc
from handlers import handle_add
from orchestrator import Orchestrator
from workspace import MemoryWorkspace
import rss

app = FastAPI(title="Shiye Web")

workspace = MemoryWorkspace()
orchestrator = Orchestrator(workspace)
BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"
IMAGES_DIR = ASSETS_DIR / "img"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")


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


def note_dict(note: dict) -> dict:
    if not note:
        return {}
    return {
        "id": note.get("id"),
        "title": note.get("title"),
        "content": note.get("content", ""),
        "created_at": note.get("created_at"),
        "updated_at": note.get("updated_at"),
        "images": note.get("images") or [],
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
        <script>
            window.MathJax = {
                tex: {
                    inlineMath: [['$', '$'], ['\\\\(', '\\\\)']],
                    displayMath: [['$$', '$$'], ['\\\\[', '\\\\]']]
                },
                options: { skipHtmlTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code'] }
            };
        </script>
        <script id="mathjax-script" defer src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
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
            body.note-mode main { grid-template-columns: 1fr; }
            body.note-mode section.chat, body.note-mode #history-wrapper { display: none; }
            #note-shell { display: none; height: 100%; min-height: 0; grid-template-columns: 260px 1.6fr 1.2fr; gap: 12px; }
            body.note-mode #note-shell { display: grid; }
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
            form { padding: 12px 16px; background: var(--panel); border-top: 1px solid var(--border); display: grid; gap: 8px; position: static; border-radius: 12px; box-shadow: 0 2px 8px rgba(15, 23, 42, 0.05); }
            .input-wrap { position: relative; border: 1px solid var(--border); border-radius: 12px; overflow: hidden; background: #fff; }
            textarea { width: 100%; min-height: 110px; resize: vertical; border: none; background: transparent; color: var(--ink); padding: 14px 14px 48px 14px; box-sizing: border-box; display: block; }
            .input-footer { position: absolute; left: 0; right: 0; bottom: 0; display: flex; align-items: center; gap: 10px; padding: 8px 12px; border-top: 1px solid var(--border); background: linear-gradient(180deg, rgba(255,255,255,0.94), #f7f9ff); }
            .input-footer span { font-size: 12px; color: var(--subtle); }
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
            .note-panel { background: var(--panel); border: 1px solid var(--border); border-radius: 12px; box-shadow: 0 2px 8px rgba(15, 23, 42, 0.05); display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
            .note-list-header, .note-editor-header, .note-preview-header { padding: 12px; border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; gap: 8px; }
            .note-subtle { font-size: 12px; color: var(--subtle); }
            .note-pill { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; padding: 4px 8px; border-radius: 999px; background: var(--accent-soft); color: var(--ink); }
            .note-pill.subtle { background: #f0f4ff; color: var(--subtle); }
            .note-list-body { padding: 10px; overflow-y: auto; flex: 1; background: #f9fbff; }
            .note-item { padding: 10px 12px; border-radius: 10px; border: 1px solid transparent; cursor: pointer; margin-bottom: 8px; transition: border-color 0.12s ease, background 0.12s ease; }
            .note-item:hover { border-color: var(--border); background: #fff; }
            .note-item.active { border-color: var(--accent); background: var(--accent-soft); }
            .note-item .title { font-weight: 600; margin-bottom: 4px; white-space: nowrap; overflow: hidden; }
            .note-item .meta { font-size: 12px; color: var(--subtle); }
            .note-editor-body { flex: 1; display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
            #note-input { width: 100%; height: 100%; border: none; outline: none; padding: 16px; padding-bottom: 140px; font-family: "JetBrains Mono", "SFMono-Regular", Consolas, monospace; font-size: 14px; resize: none; background: #fdfdff; color: var(--ink); border-bottom-left-radius: 12px; border-bottom-right-radius: 12px; box-sizing: border-box; overflow: auto; scroll-padding-bottom: 140px; }
            #note-preview { padding: 16px; overflow-y: auto; flex: 1; background: linear-gradient(180deg, #fff, #f7f9ff); }
            #note-preview h1, #note-preview h2, #note-preview h3 { margin-top: 0; }
            #note-preview img { max-width: 100%; border-radius: 10px; border: 1px solid var(--border); }
            .note-title { font-weight: 600; color: var(--ink); }
            .note-actions { display: inline-flex; gap: 8px; align-items: center; }
            .note-exit { display: none; }
            body.note-mode .note-exit { display: inline-flex; }
            .history-body { position: relative; }
            .history-body.collapsed { max-height: 140px; overflow: hidden; }
            .history-body.collapsed::after { content: ""; position: absolute; left: 0; right: 0; bottom: 0; height: 32px; background: linear-gradient(180deg, transparent, #fff); }
            .history-expand { margin-top: 6px; font-size: 11px; padding: 4px 6px; border-radius: 6px; border: 1px solid var(--border); background: var(--panel); cursor: pointer; }
            .command-strip { display: flex; gap: 6px; align-items: center; font-size: 12px; color: var(--subtle); }
            .command-pill { padding: 4px 8px; border-radius: 999px; border: 1px solid var(--border); background: #f3f6fb; color: var(--ink); font-weight: 600; }
        </style>
    </head>
    <body>
        <header>
            <div style="display:flex;flex-direction:column;gap:4px;">
                <div><strong>Shiye</strong> — Your personal knowledge base</div>
                <div class="command-strip">
                    <span class="command-pill">/note</span>
                    <span class="command-pill">/add</span>
                    <span class="command-pill">/rss</span>
                    <span class="command-pill">/summarize</span>
                    <span class="command-pill">/clear (UI only)</span>
                </div>
            </div>
            <div class="row">
                <label style="display:flex;align-items:center;gap:6px;color:#4b5563;font-size:12px;">
                    <input type="checkbox" id="debugToggle" onclick="toggleDebug()" />
                    debug
                </label>
                <button id="noteBackBtn" type="button" class="ghost note-exit" onclick="exitNoteMode()">Back to chat</button>
                <button id="historyBtn" type="button" class="ghost" onclick="toggleHistory()">History</button>
            </div>
        </header>
        <main>
            <section class="chat">
                <div id="log"></div>
                <form onsubmit="event.preventDefault(); sendChat();">
                    <div class="input-wrap">
                        <textarea id="input" placeholder="Type a message... (slash commands supported)"></textarea>
                        <div class="input-footer">
                            <span id="sendStatus">Ctrl+Enter to send</span>
                            <div style="flex:1;"></div>
                            <button id="sendBtn" type="submit">Send</button>
                        </div>
                    </div>
                </form>
            </section>
            <section id="note-shell" aria-label="note mode">
                <div class="note-panel note-list-panel">
                    <div class="note-list-header">
                        <div>
                            <div style="font-weight:700;">Notebook</div>
                            <div id="noteStatus" class="note-subtle">Capture Markdown notes with previews.</div>
                        </div>
                        <div class="note-actions">
                            <button type="button" class="ghost" onclick="refreshNotes()">Refresh</button>
                            <button type="button" class="secondary" onclick="newNote()">New</button>
                        </div>
                    </div>
                    <div class="note-list-body" id="note-list"></div>
                </div>
                <div class="note-panel note-editor-panel">
                    <div class="note-editor-header">
                        <div>
                            <div class="note-pill">Editor</div>
                            <div id="note-updated" class="note-subtle"></div>
                        </div>
                        <div class="note-actions">
                            <span id="note-dirty" class="note-pill subtle" style="display:none;">Unsaved</span>
                            <button id="saveNoteBtn" type="button" class="ghost" onclick="saveActiveNote()">Save</button>
                        </div>
                    </div>
                    <div class="note-editor-body">
                        <textarea id="note-input" placeholder="Markdown note... Paste images to attach."></textarea>
                    </div>
                </div>
                <div class="note-panel note-preview-panel">
                    <div class="note-preview-header">
                        <div class="note-pill">Preview</div>
                        <div id="note-title-display" class="note-title"></div>
                    </div>
                    <div id="note-preview"></div>
                </div>
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
            const sendBtn = document.getElementById('sendBtn');
            const sendStatus = document.getElementById('sendStatus');
            const debugToggle = document.getElementById('debugToggle');
            const historyBtn = document.getElementById('historyBtn');
            const historyList = document.getElementById('history-list');
            const historyWrapper = document.getElementById('history-wrapper');
            const historyPanel = document.getElementById('history-panel');
            const historyResize = document.getElementById('history-resize');
            const noteShell = document.getElementById('note-shell');
            const noteList = document.getElementById('note-list');
            const noteInput = document.getElementById('note-input');
            const notePreview = document.getElementById('note-preview');
            const noteTitleDisplay = document.getElementById('note-title-display');
            const noteUpdated = document.getElementById('note-updated');
            const noteDirty = document.getElementById('note-dirty');
            const noteStatus = document.getElementById('noteStatus');
            const mathScript = document.getElementById('mathjax-script');
            let mathQueue = [];
            let historyOpen = false;
            let isResizing = false;
            let noteMode = false;
            let activeNoteId = null;
            let noteChanged = false;
            let noteCache = [];
            let notesLoadedOnce = false;
            let sending = false;

            function deriveNoteTitle(text) {
                if (!text) return "Untitled note";
                const lines = text.split("\\n");
                for (const line of lines) {
                    const trimmed = line.trim().replace(/^#+\\s*/, "");
                    if (trimmed) return trimmed.slice(0, 160);
                }
                return "Untitled note";
            }

            function typesetMath(el) {
                if (!el) return;
                if (typeof MathJax !== 'undefined' && MathJax.typesetPromise) {
                    MathJax.typesetPromise([el]).catch((e) => console.warn("mathjax render failed", e));
                } else {
                    mathQueue.push(el);
                }
            }

            function flushMathQueue() {
                if (!mathQueue.length) return;
                if (typeof MathJax !== 'undefined' && MathJax.typesetPromise) {
                    const unique = Array.from(new Set(mathQueue));
                    mathQueue = [];
                    MathJax.typesetPromise(unique).catch((e) => console.warn("mathjax queued render failed", e));
                }
            }

            function formatNoteListTitle(title) {
                const full = title || "Untitled note";
                const maxLen = 42;
                if (full.length <= maxLen) return full;
                return full.slice(0, maxLen - 6).trimEnd() + " [...]";
            }

            function markNoteDirty(on) {
                noteChanged = on;
                if (noteDirty) noteDirty.style.display = on ? "inline-flex" : "none";
            }

            function setSending(on) {
                sending = on;
                if (sendBtn) {
                    sendBtn.disabled = on;
                    sendBtn.textContent = on ? "Waiting..." : "Send";
                }
                if (sendStatus) {
                    if (on) {
                        sendStatus.textContent = "Waiting for response...";
                        sendStatus.style.color = "#dc2626";
                    } else {
                        sendStatus.textContent = "Ctrl+Enter to send";
                        sendStatus.style.color = "var(--subtle)";
                    }
                }
            }

            function renderNotePreview() {
                if (!notePreview) return;
                const text = noteInput ? noteInput.value : "";
                notePreview.innerHTML = text ? marked.parse(text) : '<div class="note-subtle">Nothing to preview yet.</div>';
                if (noteTitleDisplay) noteTitleDisplay.textContent = deriveNoteTitle(text);
                typesetMath(notePreview);
            }

            async function refreshNotes(autoSelect = true) {
                try {
                    const res = await fetch("/api/notes");
                    const data = await res.json();
                    noteCache = data.notes || [];
                    notesLoadedOnce = true;
                    renderNotesList(noteCache);
                    if (noteStatus) {
                        noteStatus.textContent = noteCache.length ? `${noteCache.length} stored note(s)` : "No notes yet — start with a new one.";
                    }
                    if (autoSelect) {
                        if (activeNoteId && noteCache.some(n => n.id === activeNoteId)) return;
                        if (noteCache.length) {
                            await loadNote(noteCache[0].id);
                        } else {
                            newNote();
                        }
                    }
                } catch (e) {
                    console.warn("Failed to load notes", e);
                }
            }

            function renderNotesList(notes) {
                if (!noteList) return;
                noteList.innerHTML = "";
                if (!notes || !notes.length) {
                    const empty = document.createElement("div");
                    empty.className = "note-subtle";
                    empty.textContent = "No notes yet.";
                    noteList.appendChild(empty);
                    return;
                }
                notes.forEach(n => {
                    const item = document.createElement("div");
                    item.className = "note-item" + (n.id === activeNoteId ? " active" : "");
                    const title = document.createElement("div");
                    title.className = "title";
                    const display = formatNoteListTitle(n.title || "Untitled note");
                    title.textContent = display;
                    title.title = n.title || "Untitled note";
                    const meta = document.createElement("div");
                    meta.className = "meta";
                    const updated = n.updated_at ? new Date(n.updated_at).toLocaleString() : "";
                    meta.textContent = updated ? `Updated ${updated}` : "Draft";
                    item.appendChild(title);
                    item.appendChild(meta);
                    item.onclick = () => selectNote(n.id);
                    noteList.appendChild(item);
                });
            }

            async function loadNote(noteId) {
                if (!noteId) return;
                try {
                    const res = await fetch(`/api/notes/${noteId}`);
                    if (!res.ok) return;
                    const data = await res.json();
                    const note = data.note;
                    activeNoteId = note.id;
                    if (noteInput) noteInput.value = note.content || "";
                    markNoteDirty(false);
                    renderNotePreview();
                    if (noteUpdated) {
                        const ts = note.updated_at ? new Date(note.updated_at).toLocaleString() : "";
                        noteUpdated.textContent = ts ? `Last saved ${ts}` : "";
                    }
                    renderNotesList(noteCache);
                } catch (e) {
                    console.warn("Failed to load note", e);
                }
            }

            async function saveActiveNote() {
                if (!noteInput) return null;
                const content = noteInput.value || "";
                if (!content.trim() && !activeNoteId) {
                    markNoteDirty(false);
                    return null;
                }
                const payload = { content, title: deriveNoteTitle(content) };
                const url = activeNoteId ? `/api/notes/${activeNoteId}` : "/api/notes";
                const method = activeNoteId ? "PUT" : "POST";
                try {
                    const res = await fetch(url, {
                        method,
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify(payload),
                    });
                    if (!res.ok) throw new Error(`save failed (${res.status})`);
                    const data = await res.json();
                    const note = data.note;
                    activeNoteId = note.id;
                    markNoteDirty(false);
                    if (noteUpdated) {
                        const ts = note.updated_at ? new Date(note.updated_at).toLocaleString() : "";
                        noteUpdated.textContent = ts ? `Last saved ${ts}` : "";
                    }
                    await refreshNotes(false);
                    return note;
                } catch (e) {
                    console.warn("Failed to save note", e);
                    if (noteStatus) noteStatus.textContent = "Save failed — try again.";
                    return null;
                }
            }

            async function maybeSaveActiveNote() {
                if (noteChanged) {
                    return await saveActiveNote();
                }
                return null;
            }

            async function selectNote(noteId) {
                if (noteId === activeNoteId) return;
                await maybeSaveActiveNote();
                await loadNote(noteId);
            }

            function newNote() {
                activeNoteId = null;
                if (noteInput) {
                    noteInput.value = "";
                    noteInput.focus();
                }
                markNoteDirty(false);
                renderNotePreview();
                if (noteUpdated) noteUpdated.textContent = "Draft note — not saved yet.";
                renderNotesList(noteCache);
            }

            async function enterNoteMode() {
                noteMode = true;
                document.body.classList.add("note-mode");
                document.body.classList.remove("show-history");
                historyOpen = false;
                if (historyBtn) {
                    historyBtn.textContent = "History";
                    historyBtn.disabled = true;
                }
                if (!notesLoadedOnce) {
                    await refreshNotes();
                } else {
                    await refreshNotes(false);
                    if (activeNoteId) {
                        await loadNote(activeNoteId);
                    } else if (noteCache.length) {
                        await loadNote(noteCache[0].id);
                    }
                }
                if (noteInput) noteInput.focus();
                flushMathQueue();
            }

            async function exitNoteMode() {
                await maybeSaveActiveNote();
                noteMode = false;
                document.body.classList.remove("note-mode");
                if (historyBtn) {
                    historyBtn.disabled = false;
                }
            }

            async function uploadImage(file) {
                const form = new FormData();
                form.append("file", file, file.name || "pasted-image");
                const res = await fetch("/api/note_assets", { method: "POST", body: form });
                if (!res.ok) return null;
                const data = await res.json();
                return data.path;
            }

            async function handleNotePaste(event) {
                if (!event.clipboardData) return;
                const items = event.clipboardData.items || [];
                for (const item of items) {
                    if (item.kind === "file") {
                        const file = item.getAsFile();
                        if (file && file.type && file.type.startsWith("image/")) {
                            event.preventDefault();
                            const path = await uploadImage(file);
                            if (path) {
                                insertAtCursor(noteInput, `![pasted image](${path})\\n`);
                                renderNotePreview();
                                markNoteDirty(true);
                            }
                            return;
                        }
                    }
                }
            }

            function insertAtCursor(textarea, text) {
                if (!textarea) return;
                const start = textarea.selectionStart || 0;
                const end = textarea.selectionEnd || 0;
                const before = textarea.value.substring(0, start);
                const after = textarea.value.substring(end);
                textarea.value = before + text + after;
                const pos = start + text.length;
                textarea.selectionStart = textarea.selectionEnd = pos;
            }

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
                if (sending) return;
                const text = inputEl.value.trim();
                if (!text) return;
                if (text === '/note') {
                    inputEl.value = '';
                    await enterNoteMode();
                    return;
                }
                if (text === '/clear') {
                    if (logEl) logEl.innerHTML = '';
                    inputEl.value = '';
                    setSending(false);
                    return;
                }
                renderMessage('user', text, null, new Date().toISOString());
                inputEl.value = '';
                setSending(true);
                try {
                    const res = await fetch('/api/chat', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ text, debug: debugToggle.checked })
                    });
                    const data = await res.json();
                    (data.messages || []).forEach(m => renderMessage(m.role, m.content, m.chunk_id, m.created_at, m.debug, m.metadata));
                } catch (e) {
                    console.warn('chat failed', e);
                } finally {
                    setSending(false);
                    if (inputEl) inputEl.focus();
                }
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
                        body.className = 'history-body';
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
                        const shouldCollapse = (m.content || '').length > 360 || (body.textContent || '').length > 360;
                        if (shouldCollapse) {
                            body.classList.add('collapsed');
                            const toggle = document.createElement('button');
                            toggle.className = 'history-expand';
                            toggle.textContent = 'Expand';
                            toggle.onclick = () => {
                                const collapsed = body.classList.toggle('collapsed');
                                toggle.textContent = collapsed ? 'Expand' : 'Collapse';
                            };
                            item.appendChild(toggle);
                        }
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
                if (noteMode) return;
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
            document.body.classList.remove('show-history');

            if (noteInput) {
                noteInput.addEventListener('input', () => {
                    markNoteDirty(true);
                    renderNotePreview();
                });
                noteInput.addEventListener('paste', handleNotePaste);
                noteInput.addEventListener('keydown', (e) => {
                    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                        e.preventDefault();
                        saveActiveNote();
                    }
                });
            }
            if (mathScript) {
                mathScript.addEventListener('load', flushMathQueue);
            }
            renderNotePreview();
            flushMathQueue();
            setSending(false);

            document.addEventListener('keydown', (e) => {
                if (noteMode) return;
                if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                    if (sending) {
                        e.preventDefault();
                        return;
                    }
                    e.preventDefault();
                    sendChat();
                }
            });
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


@app.get("/api/notes")
def api_list_notes(limit: int = 50) -> dict:
    notes = workspace.list_notes(limit=limit)
    return {"notes": [note_dict(n) for n in notes]}


@app.get("/api/notes/{note_id}")
def api_get_note(note_id: int) -> dict:
    note = workspace.get_note(note_id)
    if not note:
        raise HTTPException(status_code=404, detail="note not found")
    return {"note": note_dict(note)}


@app.post("/api/notes")
def api_create_note(payload=Body(...)) -> dict:
    content = (payload or {}).get("content", "")
    title = (payload or {}).get("title")
    note = workspace.save_note(content or "", title=title)
    if not note:
        raise HTTPException(status_code=500, detail="failed to save note")
    return {"note": note_dict(note)}


@app.put("/api/notes/{note_id}")
def api_update_note(note_id: int, payload=Body(...)) -> dict:
    content = (payload or {}).get("content", "")
    title = (payload or {}).get("title")
    note = workspace.save_note(content or "", title=title, note_id=note_id)
    if not note:
        raise HTTPException(status_code=404, detail="note not found")
    return {"note": note_dict(note)}


@app.post("/api/note_assets")
async def upload_note_asset(file: UploadFile = File(...)) -> dict:
    if not file:
        raise HTTPException(status_code=400, detail="missing file")
    suffix = Path(file.filename or "").suffix or ".bin"
    safe_suffix = suffix if suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp"} else ".bin"
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty file")
    fname = f"{int(datetime.now(UTC).timestamp() * 1000)}-{secrets.token_hex(4)}{safe_suffix}"
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    out_path = IMAGES_DIR / fname
    out_path.write_bytes(data)
    rel_path = f"/assets/img/{fname}"
    return {"path": rel_path}


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
